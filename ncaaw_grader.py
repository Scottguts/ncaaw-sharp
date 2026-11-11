"""NCAAW grader — v13 Torvik model projections anchored to the sharp market,
with mlb-sharp's gate discipline.

    python ncaaw_grader.py --date 2026-11-04

Reads ncaaw_data/<date>/{teams,slate,odds}.json (run ncaaw_data_scraper.py
first), writes grades.json + cards.md. Re-running the grader costs ZERO API
requests.

CORE PHILOSOPHY (inherited from mlb-sharp — do not violate):
  * The edge comes from small, evidence-backed disagreements with the market
    plus line shopping — NOT from predicting games from scratch. The model
    spread/total is CAPPED to a small deviation from the market line before
    any probability is computed.
  * Strict gates (MIN_EDGE, MAX_REASONABLE_EDGE, price band, confidence,
    per-slate caps). Most days produce few or zero cards. A quiet day is a
    correct day. NEVER loosen gates to manufacture volume.
  * No market line for a game -> no bet (nothing to anchor against).
  * Validation is Brier/log-loss on ALL graded games + CLV, never
    small-sample ROI.

NCAAW-specific guard: Torvik ratings are noisy in November. The error sigma
is widened until both teams have ~10 games played (early_season_sigma), which
naturally shrinks edges and suppresses volume early — by design.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from ncaaw_model import (
    TRAINED_WEIGHTS, SPREAD_SIGMA, TOTAL_SIGMA,
    predict, norm_cdf, early_season_sigma, canon_name, canon_match,
    sigma_for_margin, sigma_for_total,
)
from ncaaw_data_scraper import (
    DATA_ROOT, TARGET_BOOKS, SHARP_ANCHORS, _et_date,
)

# ===========================================================================
# Money philosophy / gates (carried from mlb_grader.py, re-keyed for hoops)
# ===========================================================================

MIN_EDGE = {
    "moneyline": 0.018,   # 1.8% — matches MLB ML gate
    "spread":    0.025,   # 2.5% — point-handicap markets need more cushion
    "total":     0.025,
}
MAX_REASONABLE_EDGE = 0.06   # bigger "edges" = stale line or calibration bug -> reject
MIN_TEAM_WIN_PROB   = 0.45   # never ML a team we rate <45% to win outright

MIN_AMERICAN_PRICE = -180
MAX_AMERICAN_PRICE = +180

# Market anchoring caps — the model may only move the market line this far.
# 1.5 pts on a ~12.1-sigma spread ~= 5pp of win prob (MLB caps at 3.5pp).
# TOTAL shift tightened 2.5 -> 1.5 on 2026-11-08: week-one audit showed the
# model runs ~6 low on the marquee games that get early lines (books were
# near-perfect on them), which pegged the shift and auto-fired Unders on
# every lined total. Tighter cap = totals only fire on genuine disagreement.
MAX_SPREAD_SHIFT = 1.5   # points of margin
MAX_TOTAL_SHIFT  = 1.5   # points of total


def season_total_bias(data_root: Path = DATA_ROOT, days: int = 10,
                      min_games: int = 30, clamp: float = 6.0) -> float:
    """Self-correcting totals bias: mean (actual - model) total over recent
    finals reports. Positive = the season is scoring more than the model
    thinks -> raise projections before anchoring. Clamped, and zero until a
    real sample exists. This closes the loop the week-one audit exposed
    without touching any betting gate."""
    diffs: list[float] = []
    reports = sorted((p / "finals.json" for p in data_root.iterdir()
                      if p.is_dir() and (p / "finals.json").exists()),
                     key=lambda p: p.parent.name, reverse=True)[:days]
    for rp in reports:
        try:
            for r in json.loads(rp.read_text()).get("games") or []:
                diffs.append(r["act_total"] - r["model_total"])
        except (ValueError, KeyError):
            continue
    if len(diffs) < min_games:
        return 0.0
    return max(-clamp, min(clamp, sum(diffs) / len(diffs)))

# Best price must be confirmed by a DIFFERENT book within this many cents
# (and at the same line, for spread/total) — stale-line protection.
BOOK_CONFIRMATION_TOLERANCE_CENTS = 8

MIN_CONFIDENCE = 6

# Early-season discipline: full confidence only once BOTH teams have this
# many EFFECTIVE games; below it sigma widens (ncaaw_model.early_season_sigma).
# A prior-season rating counts as PRIOR_GAMES_CREDIT games of sample — Torvik's
# adjOE/adjDE already carry preseason priors, and we blend last season's
# tempo/four-factors in (see blend_with_prior), so a 0-game November team is
# NOT a zero-information team. Validated 2026-11-05 vs 76 actual finals:
# factors-only blend cut total MAE 16.6 -> 14.1 with spreads unchanged.
FULL_CONFIDENCE_GAMES = 10
MIN_GAMES_TO_BET = 2         # effective games below this -> no bet
PRIOR_GAMES_CREDIT = 5
PRIOR_BLEND_K = 8            # current-season games to mostly trust current stats
# blend ONLY the raw per-game stats; adjOE/adjDE/tRank stay live (already
# prior-informed by Torvik — blending them made spreads slightly worse)
PRIOR_BLEND_KEYS = ("tempo", "eFG", "eFGD", "tor", "torD",
                    "orb", "drb", "ftr", "ftrD")


def blend_with_prior(cur: dict, pri: dict | None) -> dict:
    """Shrink a team's raw season stats toward last season's by games played,
    and grant the prior-games credit used by the betting gate + sigma."""
    out = dict(cur)
    if not pri:
        out["games_eff"] = cur.get("games", 0)
        return out
    g = cur.get("games", 0)
    w = g / (g + PRIOR_BLEND_K)
    for k in PRIOR_BLEND_KEYS:
        out[k] = w * cur[k] + (1 - w) * pri[k]
    out["games_eff"] = g + PRIOR_GAMES_CREDIT
    return out


def load_prior_teams(season: int) -> dict:
    p = DATA_ROOT / "history" / f"teams_{season - 1}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()).get("teams") or {}
    except ValueError:
        return {}

# (edge_floor, confidence_floor, units, risk_label) — 1u = 1% bankroll
UNIT_LADDER = [
    (0.050, 8, 1.5, "Strong"),
    (0.035, 7, 1.0, "Standard"),
    (0.018, 6, 0.5, "Lean"),
]

MAX_CARDS_PER_SLATE = 5
MAX_UNITS_PER_SLATE = 6.0
MAX_CARDS_PER_GAME  = 1
MAX_UNITS_PER_GAME  = 1.5

ENABLED_MARKETS = {"moneyline": True, "spread": True, "total": True}

# Shadow (paper) tier — evidence accumulation, NOT real bets. Anything that
# clears HALF the edge gate at conf >=4 is paper-logged at a flat 0.5u so we
# learn which markets/buckets actually win ~5-10x faster than live volume
# alone. Price band, win-prob floor, book confirmation and the stale-line
# (MAX_REASONABLE_EDGE) reject still apply. Mirrors mlb-sharp's paper props.
SHADOW_MIN_CONFIDENCE = 4
SHADOW_UNITS = 0.5


def gate_tier(market: str, edge: float, conf: int) -> str | None:
    """'fire' = real bet, 'shadow' = paper-only, None = discard."""
    if edge > MAX_REASONABLE_EDGE:
        return None
    if edge >= MIN_EDGE[market] and conf >= MIN_CONFIDENCE:
        return "fire"
    if edge >= MIN_EDGE[market] / 2 and conf >= SHADOW_MIN_CONFIDENCE:
        return "shadow"
    return None


# ===========================================================================
# Injury feed — ncaaw_data/injuries.json (manual; update when news breaks)
# ===========================================================================
# Format (team names in any ESPN/Torvik spelling):
#   { "UConn": [ {"player": "Star Guard", "ppg": 18.5, "status": "OUT",
#                 "replacement": 0.45, "starter": true} ] }
# status: OUT (full weight) / GTD (half) / PROB (quarter).
# replacement: fraction of the player's production the bench recovers
# (default 0.45). Deltas mirror the v11 app's getInjuryAdj: each net lost
# ppg costs ~1.43 adjOE points; each starter out costs ~0.8 adjDE.
# NCAAW books are SLOW on availability news — this is the highest-alpha
# input in the system. Clear entries once the market has priced them.

_STATUS_MULT = {"OUT": 1.0, "GTD": 0.5, "PROB": 0.25}


def load_injuries(data_root: Path = None) -> dict:
    path = (data_root or DATA_ROOT) / "injuries.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except ValueError:
        print("[grader] WARNING: injuries.json is malformed — ignoring")
        return {}
    return {canon_name(team): entries for team, entries in raw.items()
            if isinstance(entries, list) and not team.startswith("_")
            and all(isinstance(e, dict) for e in entries)}


def injury_adj_for(team_canon: str, injuries: dict) -> tuple[dict, list[str]]:
    """(predict() delta dict, human-readable notes) for a team."""
    entries = injuries.get(team_canon) or []
    lost_ppg = 0.0
    starters_out = 0
    notes = []
    for e in entries:
        mult = _STATUS_MULT.get(str(e.get("status", "")).upper())
        if mult is None:
            continue
        repl = float(e.get("replacement", 0.45))
        lost = float(e.get("ppg", 0.0)) * (1 - repl) * mult
        lost_ppg += lost
        if e.get("starter") and mult >= 1.0:
            starters_out += 1
        notes.append(f"{e.get('player', '?')} {str(e.get('status', '')).upper()} "
                     f"(~{lost:.1f} ppg net)")
    if not notes:
        return {}, []
    return {"adjOE": -(lost_ppg * 1.43), "adjDE": starters_out * 0.8}, notes


# ===========================================================================
# Dataclasses
# ===========================================================================

@dataclass
class BetCard:
    bet_label: str
    market: str                 # moneyline / spread / total
    side: str                   # home / away / over / under
    book: str
    price_american: int
    line: float | None
    fair_prob: float
    fair_american: int
    edge: float
    confidence: int
    unit_size: float
    risk: str
    reasoning: list[str]
    pass_triggers: list[str]
    # game identity for tracking/settlement
    espn_event_id: str = ""
    matchup: str = ""
    start: str = ""


# ===========================================================================
# Odds helpers
# ===========================================================================

def _load(p: Path):
    return json.loads(p.read_text())


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def american_to_prob(odds: int) -> float:
    return (-odds) / ((-odds) + 100) if odds < 0 else 100 / (odds + 100)


def prob_to_american(p: float) -> int:
    if p <= 0 or p >= 1:
        return 0
    return int(round(-100 * p / (1 - p))) if p >= 0.5 else int(round(100 * (1 - p) / p))


def devig_two_way(price_a: int, price_b: int) -> tuple[float, float]:
    pa, pb = american_to_prob(price_a), american_to_prob(price_b)
    s = pa + pb
    return pa / s, pb / s


def edge_pct(your_prob: float, offered_american: int) -> float:
    """Edge as a fraction. 0.045 == 4.5%."""
    dec = (offered_american / 100 + 1) if offered_american > 0 else (100 / -offered_american + 1)
    return your_prob * dec - 1


def _markets_for_book(odds_event: dict, book_key: str) -> dict[str, list]:
    for b in odds_event.get("bookmakers", []):
        if b.get("key") == book_key:
            return {m.get("key"): m.get("outcomes", []) for m in b.get("markets", [])}
    return {}


def _team_outcome(outcomes: list, team_name: str) -> dict | None:
    for o in outcomes:
        if canon_match(o.get("name") or "", team_name):
            return o
    return None


def market_anchor_lines(odds_event: dict, home_name: str) -> dict:
    """Extract the market anchor: Pinnacle spread/total (preferred) or the
    median across target books. Returns home-perspective handicap and total,
    plus Pinnacle devigged home ML prob when available."""
    spreads: list[float] = []
    totals: list[float] = []
    pinn_spread = pinn_total = None
    manual_spread = manual_total = None
    pinn_fair_home = None
    for b in odds_event.get("bookmakers", []):
        key = b.get("key")
        mk = {m.get("key"): m.get("outcomes", []) for m in b.get("markets", [])}
        h = _team_outcome(mk.get("spreads", []), home_name)
        if h and h.get("point") is not None:
            if key in SHARP_ANCHORS:
                pinn_spread = float(h["point"])
            elif key in TARGET_BOOKS:
                spreads.append(float(h["point"]))
            elif key == "manual":
                manual_spread = float(h["point"])
        tot = next((o for o in mk.get("totals", []) if o.get("name") == "Over"), None)
        if tot and tot.get("point") is not None:
            if key in SHARP_ANCHORS:
                pinn_total = float(tot["point"])
            elif key in TARGET_BOOKS:
                totals.append(float(tot["point"]))
            elif key == "manual":
                manual_total = float(tot["point"])
        if key in SHARP_ANCHORS:
            h2h = mk.get("h2h", [])
            ho = _team_outcome(h2h, home_name)
            ao = next((o for o in h2h if o is not ho), None)
            if ho and ao and ho.get("price") is not None and ao.get("price") is not None:
                pinn_fair_home, _ = devig_two_way(int(ho["price"]), int(ao["price"]))
    spread_home = (pinn_spread if pinn_spread is not None
                   else median(spreads) if spreads
                   else manual_spread)
    total = (pinn_total if pinn_total is not None
             else median(totals) if totals
             else manual_total)
    return {
        "spread_home": spread_home,
        "total": total,
        "spread_src": ("pinnacle" if pinn_spread is not None
                       else "consensus" if spreads
                       else "manual" if manual_spread is not None else None),
        "total_src": ("pinnacle" if pinn_total is not None
                      else "consensus" if totals
                      else "manual" if manual_total is not None else None),
        "pinnacle_fair_home_ml": pinn_fair_home,
    }


def best_price(odds_event: dict, market_key: str, pick) -> tuple[int, float | None, str] | None:
    """Best (price, line, book) across TARGET_BOOKS with two-book confirmation.

    pick: for h2h/spreads a team name; for totals 'Over'/'Under'.
    For spreads/totals the confirming book must offer the SAME line within
    BOOK_CONFIRMATION_TOLERANCE_CENTS. Returns None when unconfirmed — a
    lone outlier price is treated as stale, not as value.
    """
    quotes: list[tuple[int, float | None, str]] = []
    for book in TARGET_BOOKS + ("manual",):
        mk = _markets_for_book(odds_event, book)
        outs = mk.get(market_key, [])
        o = (_team_outcome(outs, pick) if market_key in ("h2h", "spreads")
             else next((x for x in outs if x.get("name") == pick), None))
        if o and o.get("price") is not None:
            quotes.append((int(o["price"]), o.get("point"), book))
    if not quotes:
        return None
    quotes.sort(key=lambda q: -q[0])   # higher american = better for bettor
    best = quotes[0]
    if best[2] == "manual":
        return best   # user-submitted line = user-confirmed
    for q in quotes[1:]:
        same_line = (market_key == "h2h") or (q[1] == best[1])
        if same_line and abs(q[0] - best[0]) <= BOOK_CONFIRMATION_TOLERANCE_CENTS:
            return best
    return None   # single-book outlier -> unconfirmed


# ===========================================================================
# Confidence + sizing (recalibrated from the MLB bet-log analysis)
# ===========================================================================

def confidence_from(point_disagreement: float, edge: float) -> int:
    """Both axes must contribute: a real model-vs-market point gap AND a
    priced edge. Single-axis ceiling is 6 (= MIN_CONFIDENCE, i.e. Lean at
    best). Point thresholds sized to NCAAW spread sigma (~12)."""
    gap = abs(point_disagreement)
    gap_score = 3 if gap >= 6 else 2 if gap >= 4 else 1 if gap >= 2 else 0
    edge_score = 3 if edge >= 0.045 else 2 if edge >= 0.025 else 1 if edge >= 0.018 else 0
    if gap_score == 0 or edge_score == 0:
        return int(_clip(4 + max(gap_score, edge_score), 1, 6))
    return int(_clip(4 + gap_score + edge_score, 1, 10))


def unit_size_from(edge: float, conf: int) -> tuple[float, str]:
    for edge_floor, conf_floor, units, label in UNIT_LADDER:
        if edge >= edge_floor and conf >= conf_floor:
            return units, label
    return 0.0, "Pass"


def _pass_triggers() -> list[str]:
    return [
        "Key player ruled out after card is posted",
        "Line moves through our fair value before tip",
        "Confirmed lineup/coaching news that invalidates the Torvik profile",
    ]


# ===========================================================================
# Per-game grading
# ===========================================================================

def grade_game(game: dict, teams: dict, odds_event: dict | None,
               injuries: dict | None = None, total_bias: float = 0.0) -> dict:
    """Grade one game: model projection, market anchor, candidate cards."""
    hname = game["home"]["display_name"]
    aname = game["away"]["display_name"]
    # clean (mascot-free) names for odds lookups — canon_match trims mascots
    # from the Odds-API side only, so the reference side must be clean
    hloc = game["home"].get("location") or hname
    aloc = game["away"].get("location") or aname
    matchup = f"{aname} @ {hname}"
    rec: dict = {
        "espn_event_id": game["espn_event_id"],
        "matchup": matchup,
        "home_short": game["home"].get("location") or hname,
        "away_short": game["away"].get("location") or aname,
        "start": game["start"],
        "neutral_site": game["neutral_site"],
        "status": "graded",
        "cards": [],
        "shadow": [],
        "pass_reasons": [],
    }

    th = teams.get(game["home"]["canon"])
    ta = teams.get(game["away"]["canon"])
    if not th or not ta:
        rec["status"] = "skipped"
        rec["pass_reasons"].append("missing Torvik data (non-D-I opponent?)")
        return rec

    # ESPN record/conf are fresher than Torvik's cached record field
    home = {**th, "record": game["home"].get("record") or th["record"],
            "conf": game["home"].get("conf") or ""}
    away = {**ta, "record": game["away"].get("record") or ta["record"],
            "conf": game["away"].get("conf") or ""}

    inj = injuries or {}
    home_inj, home_inj_notes = injury_adj_for(game["home"]["canon"], inj)
    away_inj, away_inj_notes = injury_adj_for(game["away"]["canon"], inj)
    inj_notes = ([f"{hname}: {n}" for n in home_inj_notes]
                 + [f"{aname}: {n}" for n in away_inj_notes])
    if inj_notes:
        rec["injury_notes"] = inj_notes

    proj = predict(away, home, TRAINED_WEIGHTS, neutral_site=game["neutral_site"],
                   away_inj=away_inj or None, home_inj=home_inj or None)
    model_margin_home = -proj["spread"]           # jsx spread is away-minus-home
    rec["model"] = {**proj, "margin_home": round(model_margin_home, 1),
                    "games_home": th["games"], "games_away": ta["games"],
                    "rank_home": th.get("tRank"), "rank_away": ta.get("tRank"),
                    # season 3P% (shrunk at use-site) for live luck baselines
                    "p3_home": th.get("p3"), "p3_away": ta.get("p3")}

    if odds_event is None:
        rec["pass_reasons"].append("no market line (nothing to anchor against)")
        return rec

    anchor = market_anchor_lines(odds_event, hloc)
    rec["market"] = anchor
    gh_eff = th.get("games_eff", th["games"])
    ga_eff = ta.get("games_eff", ta["games"])
    if min(gh_eff, ga_eff) < MIN_GAMES_TO_BET:
        rec["pass_reasons"].append(
            f"insufficient sample (<{MIN_GAMES_TO_BET} effective games on one side)")
        return rec

    # heteroscedastic base: sigma scales with projected margin / scoring level
    sigma_s = early_season_sigma(sigma_for_margin(model_margin_home),
                                 gh_eff, ga_eff, FULL_CONFIDENCE_GAMES)
    sigma_t = early_season_sigma(sigma_for_total(proj["total"] + total_bias),
                                 gh_eff, ga_eff, FULL_CONFIDENCE_GAMES)
    rec["sigma"] = {"spread": round(sigma_s, 2), "total": round(sigma_t, 2)}

    cards: list[BetCard] = []
    shadow: list[BetCard] = []

    def _place(tier: str, card: BetCard) -> None:
        if inj_notes:
            card.reasoning = card.reasoning + [f"Injury adj: {n}" for n in inj_notes[:2]]
        (cards if tier == "fire" else shadow).append(card)

    # ── moneyline + spread (need a market spread anchor) ──
    if anchor["spread_home"] is not None:
        market_margin = -anchor["spread_home"]    # home -6.5 -> market expects +6.5
        shift = _clip(model_margin_home - market_margin,
                      -MAX_SPREAD_SHIFT, MAX_SPREAD_SHIFT)
        fair_margin = market_margin + shift
        rec["anchored"] = {"margin_home": round(fair_margin, 2),
                           "spread_shift": round(shift, 2)}
        disagreement = model_margin_home - market_margin

        if ENABLED_MARKETS["moneyline"]:
            p_home = norm_cdf(fair_margin / sigma_s)
            # Pinnacle devigged ML, when quoted, overrides the spread-derived
            # prior (same preference order as mlb-sharp)
            if anchor["pinnacle_fair_home_ml"] is not None:
                p_home = _clip(anchor["pinnacle_fair_home_ml"]
                               + _clip(p_home - anchor["pinnacle_fair_home_ml"],
                                       -0.035, 0.035), 0.05, 0.95)
            for side, team, lookup, p in (("home", hname, hloc, p_home),
                                          ("away", aname, aloc, 1 - p_home)):
                if p < MIN_TEAM_WIN_PROB:
                    continue
                bp = best_price(odds_event, "h2h", lookup)
                if not bp:
                    continue
                price, _, book = bp
                if not (MIN_AMERICAN_PRICE <= price <= MAX_AMERICAN_PRICE):
                    continue
                e = edge_pct(p, price)
                conf = confidence_from(disagreement, e)
                tier = gate_tier("moneyline", e, conf)
                if tier == "fire":
                    units, risk = unit_size_from(e, conf)
                    if units == 0:
                        tier = "shadow"
                if tier == "shadow":
                    units, risk = SHADOW_UNITS, "Shadow"
                if tier is None:
                    continue
                _place(tier, BetCard(
                    bet_label=f"{team} ML", market="moneyline", side=side,
                    book=book, price_american=price, line=None,
                    fair_prob=round(p, 4), fair_american=prob_to_american(p),
                    edge=round(e, 4), confidence=conf, unit_size=units, risk=risk,
                    reasoning=[
                        f"Model margin {model_margin_home:+.1f} vs market {market_margin:+.1f} (home)",
                        f"Anchored fair margin {fair_margin:+.1f}, sigma {sigma_s:.1f}",
                    ],
                    pass_triggers=_pass_triggers(),
                    espn_event_id=game["espn_event_id"], matchup=matchup,
                    start=game["start"],
                ))

        if ENABLED_MARKETS["spread"]:
            for side, team, lookup in (("home", hname, hloc), ("away", aname, aloc)):
                bp = best_price(odds_event, "spreads", lookup)
                if not bp:
                    continue
                price, line, book = bp
                if line is None or not (MIN_AMERICAN_PRICE <= price <= MAX_AMERICAN_PRICE):
                    continue
                # home covers when margin_home + line_home > 0
                line_home = float(line) if side == "home" else -float(line)
                p_home_cover = norm_cdf((fair_margin + line_home) / sigma_s)
                p = p_home_cover if side == "home" else 1 - p_home_cover
                e = edge_pct(p, price)
                conf = confidence_from(disagreement, e)
                tier = gate_tier("spread", e, conf)
                if tier == "fire":
                    units, risk = unit_size_from(e, conf)
                    if units == 0:
                        tier = "shadow"
                if tier == "shadow":
                    units, risk = SHADOW_UNITS, "Shadow"
                if tier is None:
                    continue
                _place(tier, BetCard(
                    bet_label=f"{team} {float(line):+g}", market="spread", side=side,
                    book=book, price_american=price, line=float(line),
                    fair_prob=round(p, 4), fair_american=prob_to_american(p),
                    edge=round(e, 4), confidence=conf, unit_size=units, risk=risk,
                    reasoning=[
                        f"Model margin {model_margin_home:+.1f} vs market {market_margin:+.1f} (home)",
                        f"Anchored fair margin {fair_margin:+.1f} vs line {float(line):+g} ({side})",
                    ],
                    pass_triggers=_pass_triggers(),
                    espn_event_id=game["espn_event_id"], matchup=matchup,
                    start=game["start"],
                ))
    else:
        rec["pass_reasons"].append("no market spread quoted by target/sharp books")

    # ── total ──
    if ENABLED_MARKETS["total"] and anchor["total"] is not None:
        adj_total = proj["total"] + total_bias   # in-season environment corrector
        shift_t = _clip(adj_total - anchor["total"],
                        -MAX_TOTAL_SHIFT, MAX_TOTAL_SHIFT)
        fair_total = anchor["total"] + shift_t
        rec.setdefault("anchored", {})["total"] = round(fair_total, 2)
        rec["anchored"]["total_shift"] = round(shift_t, 2)
        if total_bias:
            rec["anchored"]["season_bias_applied"] = round(total_bias, 2)
        disagreement_t = adj_total - anchor["total"]
        for side in ("Over", "Under"):
            bp = best_price(odds_event, "totals", side)
            if not bp:
                continue
            price, line, book = bp
            if line is None or not (MIN_AMERICAN_PRICE <= price <= MAX_AMERICAN_PRICE):
                continue
            p_over = norm_cdf((fair_total - float(line)) / sigma_t)
            p = p_over if side == "Over" else 1 - p_over
            e = edge_pct(p, price)
            conf = confidence_from(disagreement_t, e)
            tier = gate_tier("total", e, conf)
            if tier == "fire":
                units, risk = unit_size_from(e, conf)
                if units == 0:
                    tier = "shadow"
            if tier == "shadow":
                units, risk = SHADOW_UNITS, "Shadow"
            if tier is None:
                continue
            _place(tier, BetCard(
                bet_label=f"{side} {float(line):g}", market="total",
                side=side.lower(), book=book, price_american=price,
                line=float(line),
                fair_prob=round(p, 4), fair_american=prob_to_american(p),
                edge=round(e, 4), confidence=conf, unit_size=units, risk=risk,
                reasoning=[
                    f"Model total {proj['total']:.1f} vs market {anchor['total']:g}",
                    f"Anchored fair total {fair_total:.1f} vs line {float(line):g}",
                ],
                pass_triggers=_pass_triggers(),
                espn_event_id=game["espn_event_id"], matchup=matchup,
                start=game["start"],
            ))

    rec["cards"] = cards
    rec["shadow"] = shadow
    if not cards and not rec["pass_reasons"]:
        rec["pass_reasons"].append("no edge cleared the gates")
    return rec


# ===========================================================================
# Slate selection + outputs
# ===========================================================================

def select_cards(graded: list[dict]) -> list[BetCard]:
    """Apply slate caps: best card per game, then top cards by (edge, conf)
    until MAX_CARDS_PER_SLATE / MAX_UNITS_PER_SLATE."""
    per_game: list[BetCard] = []
    for rec in graded:
        cards = sorted(rec.get("cards") or [],
                       key=lambda c: (-c.edge, -c.confidence))
        picked = 0
        units = 0.0
        for c in cards:
            if picked >= MAX_CARDS_PER_GAME or units + c.unit_size > MAX_UNITS_PER_GAME:
                continue
            per_game.append(c)
            picked += 1
            units += c.unit_size
    per_game.sort(key=lambda c: (-c.edge, -c.confidence))
    final: list[BetCard] = []
    total_units = 0.0
    for c in per_game:
        if len(final) >= MAX_CARDS_PER_SLATE:
            break
        if total_units + c.unit_size > MAX_UNITS_PER_SLATE:
            continue
        final.append(c)
        total_units += c.unit_size
    return final


def _fmt_price(p: int) -> str:
    return f"+{p}" if p > 0 else str(p)


def _display_market(rec: dict) -> str:
    m = rec.get("market") or {}
    bits = []
    if m.get("spread_home") is not None:
        bits.append(f"home {m['spread_home']:+g} ({m.get('spread_src')})")
    if m.get("total") is not None:
        bits.append(f"total {m['total']:g} ({m.get('total_src')})")
    return " · ".join(bits) if bits else "no line"


def render_cards_md(target_date: str, graded: list[dict],
                    fired: list[BetCard], odds_available: bool) -> str:
    lines = [f"# NCAAW Betting Card — {target_date}", ""]
    if not odds_available:
        lines += ["## ⚠️ DATA OUTAGE", "",
                  "Odds unavailable this run — NOT a disciplined no-play. "
                  "Check odds.json / API quota.", ""]
    if fired:
        total_u = sum(c.unit_size for c in fired)
        lines += [f"**{len(fired)} play(s) · {total_u:g}u total**", ""]
        for c in fired:
            lines += [
                f"## {c.bet_label} ({_fmt_price(c.price_american)} {c.book})",
                "",
                f"- **Game:** {c.matchup}",
                f"- **Market:** {c.market} · **Fair:** {c.fair_prob:.1%} "
                f"({_fmt_price(c.fair_american)}) · **Edge:** {c.edge:.1%}",
                f"- **Confidence:** {c.confidence}/10 · **Size:** {c.unit_size}u ({c.risk})",
                "- **Why:** " + "; ".join(c.reasoning),
                "- **Pass if:** " + "; ".join(c.pass_triggers),
                "",
            ]
    else:
        lines += ["## NO PLAYS", "",
                  "Nothing cleared the gates today. A quiet day is a correct day.", ""]
    lines += ["---", "", "### Full slate", ""]
    for rec in graded:
        model = rec.get("model")
        proj = (f"model {model['away_score']:.0f}-{model['home_score']:.0f} "
                f"(margin {model['margin_home']:+.1f} home, total {model['total']:.1f})"
                if model else "no model")
        lines.append(f"- **{rec['matchup']}** — {proj} · market: {_display_market(rec)}"
                     + (f" · PASS: {rec['pass_reasons'][0]}" if rec.get("pass_reasons") else ""))
    lines += ["", f"_Generated {datetime.now(timezone.utc).isoformat()[:19]}Z — "
              f"gates: minEdge {MIN_EDGE['moneyline']:.1%}/{MIN_EDGE['spread']:.1%}, "
              f"maxEdge {MAX_REASONABLE_EDGE:.0%}, conf ≥{MIN_CONFIDENCE}, "
              f"caps {MAX_CARDS_PER_SLATE} cards/{MAX_UNITS_PER_SLATE:g}u_", ""]
    return "\n".join(lines)


def load_manual_lines(target_date: str, data_root: Path = DATA_ROOT) -> list[dict]:
    """User-submitted late lines (via the dashboard's LATE LINE button ->
    GitHub issue -> line-submit.yml -> ncaaw_data/manual_lines.json)."""
    p = data_root / "manual_lines.json"
    if not p.exists():
        return []
    try:
        rows = json.loads(p.read_text())
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("date") == target_date]


def _synth_manual_event(game: dict, line: dict) -> dict:
    """Build a one-book odds event from a manually submitted line. Prices
    default to -110; the 'manual' book is exempt from two-book confirmation
    (the user IS the confirmation) but every other gate still applies."""
    hname = game["home"]["display_name"]
    aname = game["away"]["display_name"]
    markets = []
    sp = line.get("spread_home")
    if sp is not None:
        price = int(line.get("spread_price") or -110)
        markets.append({"key": "spreads", "outcomes": [
            {"name": hname, "price": price, "point": float(sp)},
            {"name": aname, "price": price, "point": -float(sp)}]})
    tot = line.get("total")
    if tot is not None:
        price = int(line.get("total_price") or -110)
        markets.append({"key": "totals", "outcomes": [
            {"name": "Over", "price": price, "point": float(tot)},
            {"name": "Under", "price": price, "point": float(tot)}]})
    if line.get("ml_home") is not None and line.get("ml_away") is not None:
        markets.append({"key": "h2h", "outcomes": [
            {"name": hname, "price": int(line["ml_home"])},
            {"name": aname, "price": int(line["ml_away"])}]})
    return {"id": f"manual-{game['espn_event_id']}",
            "commence_time": game["start"],
            "home_team": hname, "away_team": aname,
            "bookmakers": [{"key": "manual", "markets": markets}]}


def run(target_date: str, data_root: Path = DATA_ROOT) -> dict:
    day = data_root / target_date
    slate = _load(day / "slate.json")
    teams_doc = _load(day / "teams.json")
    season = int(teams_doc.get("season") or 0)
    prior = load_prior_teams(season) if season else {}
    teams = {k: blend_with_prior(v, prior.get(k))
             for k, v in teams_doc["teams"].items()}
    if prior:
        print(f"[grader] prior-blend active (season {season - 1} baseline, "
              f"K={PRIOR_BLEND_K}, +{PRIOR_GAMES_CREDIT} games credit)")
    odds = _load(day / "odds.json") if (day / "odds.json").exists() else {"available": False, "games": []}

    odds_by_id = {g.get("id"): g for g in odds.get("games") or []}

    # merge user-submitted late lines for games the board doesn't cover
    manual = load_manual_lines(target_date, data_root)
    n_manual = 0
    for g in slate["games"]:
        if g.get("odds_event_id") and g["odds_event_id"] in odds_by_id:
            continue
        for line in manual:
            if (canon_match(line.get("away") or "", g["away"].get("location") or "")
                    and canon_match(line.get("home") or "", g["home"].get("location") or "")):
                ev = _synth_manual_event(g, line)
                odds_by_id[ev["id"]] = ev
                g["odds_event_id"] = ev["id"]
                n_manual += 1
                break
    if n_manual:
        print(f"[grader] merged {n_manual} manually submitted line(s)")
    injuries = load_injuries()
    if injuries:
        print(f"[grader] injuries.json active for {len(injuries)} team(s)")
    tbias = season_total_bias(data_root)
    if tbias:
        print(f"[grader] season total-bias corrector: {tbias:+.2f} "
              f"(recent actual-vs-model totals)")
    # slate.json stored odds_event_id at scrape time
    graded = []
    for g in slate["games"]:
        ev = odds_by_id.get(g.get("odds_event_id"))
        graded.append(grade_game(g, teams, ev, injuries, total_bias=tbias))

    fired = select_cards(graded)
    # shadow = paper evidence, uncapped (fired cards excluded to avoid overlap).
    # Full-gate cards squeezed out by the slate caps are paper-logged too.
    fired_keys = {(c.espn_event_id, c.market, c.side) for c in fired}
    shadow_all = [c for rec in graded for c in rec.get("shadow") or []
                  if (c.espn_event_id, c.market, c.side) not in fired_keys]
    for rec in graded:
        for c in rec.get("cards") or []:
            if (c.espn_event_id, c.market, c.side) not in fired_keys:
                c.unit_size, c.risk = SHADOW_UNITS, "Shadow(cap-cut)"
                shadow_all.append(c)

    # Model-vs-market bias instrument: signed deltas across every game with a
    # line. A persistent mean (e.g. totals always -5) = recalibrate; noise
    # around zero = healthy. This is the "do we need to tune" gauge.
    s_deltas = [rec["model"]["margin_home"] - (-rec["market"]["spread_home"])
                for rec in graded
                if rec.get("model") and (rec.get("market") or {}).get("spread_home") is not None]
    t_deltas = [rec["model"]["total"] - rec["market"]["total"]
                for rec in graded
                if rec.get("model") and (rec.get("market") or {}).get("total") is not None]
    market_bias = {
        "n_spread_lines": len(s_deltas),
        "n_total_lines": len(t_deltas),
        "mean_spread_delta": round(sum(s_deltas) / len(s_deltas), 2) if s_deltas else None,
        "mean_total_delta": round(sum(t_deltas) / len(t_deltas), 2) if t_deltas else None,
    }
    if s_deltas or t_deltas:
        print(f"[grader] model-vs-market: spread Δ {market_bias['mean_spread_delta']} "
              f"({len(s_deltas)} lines) · total Δ {market_bias['mean_total_delta']} "
              f"({len(t_deltas)} lines)")

    out = {
        "date": target_date,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "market_bias": market_bias,
        "odds_available": bool(odds.get("available")),
        "n_games": len(graded),
        "n_cards": len(fired),
        "total_units": round(sum(c.unit_size for c in fired), 2),
        "cards": [asdict(c) for c in fired],
        "shadow_cards": [asdict(c) for c in shadow_all],
        "games": [
            {**rec,
             "cards": [asdict(c) for c in rec.get("cards") or []],
             "shadow": [asdict(c) for c in rec.get("shadow") or []]}
            for rec in graded
        ],
    }
    (day / "grades.json").write_text(json.dumps(out, indent=2, default=str))
    (day / "cards.md").write_text(
        render_cards_md(target_date, graded, fired, bool(odds.get("available"))))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = ap.parse_args()
    out = run(args.date)
    print(f"[grader] {out['n_games']} games graded, {out['n_cards']} cards "
          f"({out['total_units']}u), odds_available={out['odds_available']}")
    for c in out["cards"]:
        print(f"  -> {c['bet_label']} {c['price_american']:+d} @{c['book']} "
              f"edge {c['edge']:.1%} conf {c['confidence']}/10 {c['unit_size']}u")
    return 0


if __name__ == "__main__":
    sys.exit(main())
