"""Static dashboard — docs/ for GitHub Pages, styled after the NCAAW/MLB
React model apps (dark JetBrains-Mono terminal aesthetic).

Three tabs (client-side, no framework):
  PREDICTIONS — every game: matchup card, pitching-style stat grid (model
                score / win% / total / fair spread vs book), EDGE glow when
                a bet fired, date navigation.
  BEST BETS   — fired cards with star sizing + live status (pending/win/loss).
  BET HISTORY — P&L summary tiles (net units, ROI, W-L-P, CLV) + every bet
                ever logged, newest first.

Rebuilt + pushed by every daily-bets / line-watch / closing-snapshot run;
index.html auto-refreshes every 5 minutes. Deterministic output.
"""

from __future__ import annotations

import csv
import html
import json
import math
import sys
from pathlib import Path

from ncaaw_data_scraper import DATA_ROOT
from ncaaw_model import SPREAD_SIGMA


def _win_prob_home(margin_home: float) -> float:
    return 0.5 * (1.0 + math.erf(margin_home / SPREAD_SIGMA / math.sqrt(2.0)))

DOCS = Path(__file__).resolve().parent / "docs"

C = {  # palette lifted from the React app
    "bg": "#0a0f1a", "surface": "#111827", "hover": "#1a2236",
    "border": "#1e293b", "text": "#e2e8f0", "mut": "#8899aa", "dim": "#556677",
    "accent": "#f97316", "green": "#22c55e", "red": "#ef4444",
    "blue": "#3b82f6", "yellow": "#eab308",
}

STYLE = f"""
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&display=swap');
*{{box-sizing:border-box}}
body{{font-family:'JetBrains Mono','SF Mono',monospace;background:{C['bg']};color:{C['text']};
margin:0;font-size:13px;min-height:100vh}}
a{{color:{C['blue']};text-decoration:none}}
.header{{background:linear-gradient(135deg,{C['surface']} 0%,#0d1525 100%);
border-bottom:1px solid {C['border']};padding:16px 20px;display:flex;align-items:center;
justify-content:space-between;flex-wrap:wrap;gap:8px}}
.title{{font-size:18px;font-weight:800;letter-spacing:-0.02em;
background:linear-gradient(135deg,{C['accent']} 0%,#fbbf24 100%);
-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.subtitle{{font-size:11px;color:{C['mut']};margin-top:2px}}
.badge{{display:inline-flex;align-items:center;padding:2px 8px;border-radius:6px;
font-size:11px;font-weight:700}}
.b-green{{color:{C['green']};background:{C['green']}18;border:1px solid {C['green']}25}}
.b-red{{color:{C['red']};background:{C['red']}18;border:1px solid {C['red']}25}}
.b-grey{{color:{C['mut']};background:{C['mut']}18;border:1px solid {C['mut']}25}}
.b-yellow{{color:{C['yellow']};background:{C['yellow']}18;border:1px solid {C['yellow']}25}}
.b-accent{{color:{C['accent']};background:{C['accent']}18;border:1px solid {C['accent']}25}}
.tabs{{display:flex;gap:2px;padding:8px 20px;background:{C['surface']};
border-bottom:1px solid {C['border']};position:sticky;top:0;z-index:5}}
.tab{{padding:8px 16px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;
border:none;background:transparent;color:{C['mut']};font-family:inherit}}
.tab.active{{background:{C['accent']};color:#fff}}
.wrap{{padding:20px;max-width:1100px;margin:0 auto}}
.card{{background:{C['surface']};border:1px solid {C['border']};border-radius:10px;
padding:16px;margin-bottom:12px}}
.card.edge{{border:1px solid {C['accent']}40;box-shadow:0 0 20px {C['accent']}22}}
.matchup{{font-size:15px;font-weight:800;letter-spacing:-0.01em}}
.muted{{font-size:11px;color:{C['mut']}}} .dim{{font-size:10px;color:{C['dim']}}}
.grid4{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0}}
.stat{{padding:8px 10px;background:{C['bg']};border-radius:6px;text-align:center}}
.stat .l{{font-size:10px;color:{C['mut']};margin-bottom:3px}}
.stat .v{{font-size:15px;font-weight:800}}
.stat .s{{font-size:10px;color:{C['dim']}}}
.play{{flex:1;min-width:200px;padding:10px 12px;border-radius:8px;margin-top:8px}}
.play-g{{background:{C['green']}12;border:1px solid {C['green']}30}}
.play-b{{background:{C['blue']}12;border:1px solid {C['blue']}30}}
.play-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}}
.stars{{font-size:14px;font-weight:800;color:{C['yellow']}}}
.datenav{{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}}
.navbtn{{padding:8px 16px;border-radius:6px;border:1px solid {C['border']};color:{C['mut']};
background:transparent;font-size:14px;font-weight:700}}
.datetitle{{font-size:16px;font-weight:700}}
.pnl{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:16px}}
.pnl .card{{text-align:center;padding:12px;margin:0}}
.pnl .l{{font-size:10px;color:{C['mut']}}} .pnl .v{{font-size:20px;font-weight:800}}
.betrow{{display:flex;justify-content:space-between;align-items:center;padding:12px;
background:{C['surface']};border:1px solid {C['border']};border-radius:10px;margin-bottom:8px}}
.g{{color:{C['green']}}} .r{{color:{C['red']}}} .y{{color:{C['yellow']}}} .bl{{color:{C['blue']}}}
.empty{{text-align:center;color:{C['mut']};padding:40px}}
.search{{width:100%;background:{C['bg']};border:1px solid {C['border']};border-radius:8px;
padding:10px 14px;color:{C['text']};font-family:inherit;font-size:13px;outline:none;
margin-bottom:14px}}
.search:focus{{border-color:{C['accent']}66}}
.gamecard{{cursor:pointer}}
.chev{{color:{C['dim']};font-size:14px;margin-right:6px}}
.details{{margin-top:12px;border-top:1px solid {C['border']};padding-top:12px}}
.details p{{font-size:12px;color:{C['mut']};line-height:1.55;margin:.4rem 0}}
.details b{{color:{C['text']}}}
.statrow td{{font-variant-numeric:tabular-nums}}
.better{{color:{C['green']};font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{padding:8px 6px;text-align:left;font-size:10px;font-weight:700;color:{C['mut']};
text-transform:uppercase;border-bottom:2px solid {C['border']}}}
td{{padding:6px;border-bottom:1px solid {C['border']}30}}
@media(max-width:700px){{.grid4{{grid-template-columns:repeat(2,1fr)}}
.pnl{{grid-template-columns:repeat(2,1fr)}}}}
"""

TABS_JS = """
function showTab(id){
  document.querySelectorAll('.pane').forEach(p=>p.style.display='none');
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('pane-'+id).style.display='block';
  document.getElementById('tab-'+id).classList.add('active');
  if(history.replaceState)history.replaceState(null,'','#'+id);
}
function toggleGame(id){
  const d=document.getElementById('det-'+id);
  const c=document.getElementById('chev-'+id);
  const open=d.style.display!=='none';
  d.style.display=open?'none':'block';
  if(c)c.textContent=open?'▸':'▾';
}
function searchGames(q){
  q=q.trim().toLowerCase();
  document.querySelectorAll('.gamecard').forEach(el=>{
    el.style.display=(!q||el.dataset.search.includes(q))?'':'none';
  });
  document.querySelectorAll('.secthead').forEach(el=>{el.style.display=q?'none':'';});
}
window.addEventListener('DOMContentLoaded',()=>{
  const h=(location.hash||'#predictions').slice(1);
  showTab(['predictions','bets','history'].includes(h)?h:'predictions');
});
"""


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _price(p) -> str:
    try:
        p = int(float(p))
    except (TypeError, ValueError):
        return "—"
    return f"+{p}" if p > 0 else str(p)


def _stars(units) -> str:
    try:
        u = float(units)
    except (TypeError, ValueError):
        return ""
    return "★★★" if u >= 1.5 else "★★" if u >= 1.0 else "★" if u > 0 else ""


def _read_bets() -> list[dict]:
    log = DATA_ROOT / "bet_log.csv"
    if not log.exists():
        return []
    with log.open(newline="") as f:
        return list(csv.DictReader(f))


def _pnl(bets: list[dict]) -> dict:
    w = l = p = 0
    net = risked = 0.0
    clvs = []
    for b in bets:
        st = b.get("status")
        try:
            u = float(b.get("units_risked") or 0)
            pl = float(b.get("units_pl") or 0)
        except ValueError:
            u = pl = 0.0
        if st == "win":
            w += 1; net += pl; risked += u
        elif st == "loss":
            l += 1; net += pl; risked += u
        elif st == "push":
            p += 1
        try:
            clvs.append(float(b["clv_pct"]))
        except (KeyError, ValueError, TypeError):
            pass
    return {"w": w, "l": l, "p": p, "net": net, "risked": risked,
            "roi": (net / risked * 100) if risked else 0.0,
            "clv": (sum(clvs) / len(clvs)) if clvs else None,
            "pending": sum(1 for b in bets if b.get("status") == "pending")}


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------

def _team_stats_ctx(day: Path) -> dict:
    """espn_event_id -> {'home': torvik stats, 'away': torvik stats} for the
    advanced-metrics panel. Joined from the day's slate.json + teams.json."""
    try:
        slate = json.loads((day / "slate.json").read_text())
        teams = json.loads((day / "teams.json").read_text())["teams"]
    except (OSError, ValueError, KeyError):
        return {}
    ctx = {}
    for g in slate.get("games", []):
        ctx[g["espn_event_id"]] = {
            "home": teams.get(g["home"]["canon"]),
            "away": teams.get(g["away"]["canon"]),
        }
    return ctx


_STAT_ROWS = [
    ("T-Rank", "tRank", False), ("Record", "record", None),
    ("adjOE", "adjOE", True), ("adjDE", "adjDE", False),
    ("Tempo", "tempo", None), ("eFG%", "eFG", True), ("eFG% def", "eFGD", False),
    ("TOV%", "tor", False), ("TOV% forced", "torD", True),
    ("ORB%", "orb", True), ("DRB%", "drb", False),
    ("FT rate", "ftr", True), ("FT rate def", "ftrD", False),
]


def _details_html(g: dict, stats: dict | None) -> str:
    """Advanced metrics table + generated reasoning for one game."""
    m = g.get("model") or {}
    mk = g.get("market") or {}
    ts = stats or {}
    th, ta = ts.get("home"), ts.get("away")
    away_nm = g.get("away_short") or "Away"
    home_nm = g.get("home_short") or "Home"
    parts = []

    # four-factor stats are raw this-season numbers — meaningless at 0 games
    # (Torvik fills placeholders like eFG 0.0 / def 200.0)
    _FACTOR_KEYS = {"eFG", "eFGD", "tor", "torD", "orb", "drb", "ftr", "ftrD"}
    a_played = bool(ta and ta.get("games"))
    h_played = bool(th and th.get("games"))

    def _fmt(v, key, played):
        if key in _FACTOR_KEYS and not played:
            return None
        if isinstance(v, float):
            return f"{v:.1f}"
        return v

    if th and ta:
        rows = []
        for label, key, higher_better in _STAT_ROWS:
            av, hv = _fmt(ta.get(key), key, a_played), _fmt(th.get(key), key, h_played)
            ac = hc = ""
            rav, rhv = ta.get(key), th.get(key)
            if (higher_better is not None and av is not None and hv is not None
                    and isinstance(rav, (int, float)) and isinstance(rhv, (int, float))):
                if (rav > rhv) == higher_better and rav != rhv:
                    ac = " class='better'"
                elif rav != rhv:
                    hc = " class='better'"
            rows.append(f"<tr class='statrow'><td{ac}>{esc(av if av is not None else '—')}</td>"
                        f"<td style='color:{C['mut']}'>{esc(label)}</td>"
                        f"<td{hc} style='text-align:right'>{esc(hv if hv is not None else '—')}</td></tr>")
        parts.append(
            f"<table style='max-width:420px'><tr><th>{esc(away_nm)}</th>"
            f"<th style='text-align:center'>TORVIK</th>"
            f"<th style='text-align:right'>{esc(home_nm)}</th></tr>"
            + "".join(rows) + "</table>")

    # ── generated reasoning ──
    paras = []
    if th and ta and m:
        net_a = ta["adjOE"] - ta["adjDE"]
        net_h = th["adjOE"] - th["adjDE"]
        edge_team = home_nm if net_h > net_a else away_nm
        paras.append(
            f"<b>Efficiency:</b> {esc(away_nm)} nets {net_a:+.1f} pts/100 poss "
            f"(O {ta['adjOE']:.1f} / D {ta['adjDE']:.1f}) vs {esc(home_nm)} "
            f"{net_h:+.1f} (O {th['adjOE']:.1f} / D {th['adjDE']:.1f}) — a "
            f"{abs(net_h - net_a):.1f}-point gap favoring {esc(edge_team)} before "
            f"venue. At ~{m.get('proj_poss', 70):.0f} projected possessions that "
            f"drives most of the {abs(m.get('margin_home', 0)):.1f}-point model margin.")
        if a_played and h_played:   # factor stats are placeholders at 0 games
            ff = []
            shoot = (ta["eFG"] - th["eFGD"]) - (th["eFG"] - ta["eFGD"])
            if abs(shoot) >= 3:
                ff.append(f"shooting leans {esc(away_nm if shoot > 0 else home_nm)} "
                          f"({abs(shoot):.1f} eFG pts of matchup edge)")
            to = (th["torD"] - ta["tor"]) - (ta["torD"] - th["tor"])
            if abs(to) >= 3:
                ff.append(f"turnover battle leans {esc(away_nm if to > 0 else home_nm)}")
            glass = (ta["orb"] - (100 - th["drb"])) - (th["orb"] - (100 - ta["drb"]))
            if abs(glass) >= 3:
                ff.append(f"offensive glass leans {esc(away_nm if glass > 0 else home_nm)}")
            if ff:
                paras.append("<b>Four factors:</b> " + "; ".join(ff) + ".")
        paras.append(
            f"<b>Pace:</b> {esc(away_nm)} plays at {ta['tempo']:.0f} poss/40, "
            f"{esc(home_nm)} at {th['tempo']:.0f} → ~{m.get('proj_poss', 0):.0f} "
            f"possessions, model total {m.get('total', 0):.1f}.")
    if g.get("injury_notes"):
        paras.append("<b>Injuries:</b> " + esc("; ".join(g["injury_notes"])) + ".")
    anch = g.get("anchored") or {}
    if mk.get("spread_home") is not None and m:
        dis = -m["margin_home"] - mk["spread_home"]
        paras.append(
            f"<b>Market:</b> book has home {mk['spread_home']:+g} "
            f"({esc(mk.get('spread_src') or '')}); model says {-m['margin_home']:+.1f} "
            f"(Δ {dis:+.1f}). Anchored fair margin {anch.get('margin_home', '—')} "
            f"(shift capped at ±1.5)"
            + (f"; fair total {anch.get('total')} vs book {mk.get('total'):g}."
               if mk.get("total") is not None and anch.get("total") is not None else "."))
    sig = g.get("sigma") or {}
    if sig:
        paras.append(f"<b>Uncertainty:</b> σ spread {sig.get('spread')} / total "
                     f"{sig.get('total')} (widened until both teams reach ~10 games; "
                     f"currently {m.get('games_away', '?')} and {m.get('games_home', '?')} played).")
    for c in g.get("cards") or []:
        paras.append(f"<b class='g'>FIRED:</b> {esc(c['bet_label'])} "
                     f"{_price(c['price_american'])} @ {esc(c['book'])} — edge "
                     f"{c['edge']:.1%}, conf {c['confidence']}/10, {c['unit_size']}u.")
    for c in (g.get("shadow") or [])[:3]:
        paras.append(f"<b class='y'>shadow:</b> {esc(c['bet_label'])} "
                     f"{_price(c['price_american'])} — edge {c['edge']:.1%} "
                     f"(paper tier, below live gates).")
    if g.get("pass_reasons") and not (g.get("cards")):
        paras.append(f"<b>Verdict:</b> PASS — {esc(g['pass_reasons'][0])}.")
    parts.append("".join(f"<p>{p}</p>" for p in paras))
    return "".join(parts)


def render_predictions(grades: dict, dates: list[str], this_date: str,
                       bet_idx: dict, stats_ctx: dict | None = None) -> str:
    i = dates.index(this_date)
    prev_link = (f"<a class='navbtn' href='{dates[i + 1]}.html'>‹</a>"
                 if i + 1 < len(dates) else "<span class='navbtn'>‹</span>")
    next_link = (f"<a class='navbtn' href='{dates[i - 1]}.html'>›</a>"
                 if i > 0 else "<span class='navbtn'>›</span>")
    out = [f"<div class='datenav'>{prev_link}"
           f"<div class='datetitle'>📅 {this_date}</div>{next_link}</div>",
           "<input class='search' type='search' placeholder='🔍 Search teams…' "
           "oninput='searchGames(this.value)'>"]
    if not grades.get("odds_available"):
        out.append("<div class='card' style='border-color:#ef444460'>"
                   "<span class='r'><b>DATA OUTAGE</b></span> — odds unavailable "
                   "on the last run.</div>")
    def _best_rank(g):
        m = g.get("model") or {}
        ranks = [r for r in (m.get("rank_home"), m.get("rank_away"))
                 if isinstance(r, int)]
        return min(ranks) if ranks else 999

    # Ranked matchups (top-25 on either side) float to the top by best rank;
    # the rest follow by tip time.
    games = sorted(grades.get("games") or [],
                   key=lambda x: (0 if _best_rank(x) <= 25 else 1,
                                  _best_rank(x) if _best_rank(x) <= 25 else 0,
                                  x.get("start") or "", x.get("matchup") or ""))
    if not games:
        out.append("<div class='card empty'>No D-I games on this date.</div>")
    carded = {c["espn_event_id"]: c for c in (grades.get("cards") or [])}

    def _ranked_matchup(g) -> str:
        m = g.get("model") or {}
        parts = (g.get("matchup") or " @ ").split(" @ ")
        if len(parts) != 2:
            return esc(g.get("matchup"))
        away, home = parts
        ra, rh = m.get("rank_away"), m.get("rank_home")
        if isinstance(ra, int) and ra <= 25:
            away = f"<span class='y'>#{ra}</span> {esc(away)}"
        else:
            away = esc(away)
        if isinstance(rh, int) and rh <= 25:
            home = f"<span class='y'>#{rh}</span> {esc(home)}"
        else:
            home = esc(home)
        return f"{away} <span class='dim'>@</span> {home}"

    ranked_header_done = others_header_done = False
    for g in games:
        if _best_rank(g) <= 25 and not ranked_header_done:
            out.append("<div class='muted secthead' style='margin:10px 0 6px'>"
                       "🏆 RANKED MATCHUPS</div>")
            ranked_header_done = True
        elif _best_rank(g) > 25 and ranked_header_done and not others_header_done:
            out.append("<div class='muted secthead' style='margin:18px 0 6px'>"
                       "— rest of the slate —</div>")
            others_header_done = True
        m = g.get("model")
        mk = g.get("market") or {}
        card = carded.get(g["espn_event_id"])
        bet = bet_idx.get((str(g["espn_event_id"]), card["market"], card["side"])) if card else None
        tip = (g.get("start") or "")[11:16]
        head = (f"<div style='display:flex;justify-content:space-between;align-items:flex-start'>"
                f"<div><div class='matchup'>{_ranked_matchup(g)}</div>"
                f"<div class='muted'>{tip} UTC"
                + (" · neutral site" if g.get("neutral_site") else "")
                + "</div></div>"
                + ("<span class='badge b-accent'>⚡ EDGE</span>" if card else "")
                + "</div>")
        if m:
            away_nm = g.get("away_short") or (g.get("matchup") or " @ ").split(" @ ")[0]
            home_nm = g.get("home_short") or (g.get("matchup") or " @ ").split(" @ ")[-1]
            home_wins = m["margin_home"] > 0
            p_home = _win_prob_home(m["margin_home"])
            away_cls = "" if home_wins else " g"
            home_cls = " g" if home_wins else ""
            fav_nm = home_nm if home_wins else away_nm
            model_line = f"{esc(fav_nm)} {-abs(m['margin_home']):.1f}"
            # total lean vs book (blue), spread disagreement vs book (gold)
            if mk.get("total") is not None:
                lean = m["total"] - mk["total"]
                total_sub = (f"Book {mk['total']:g} · <span class='bl'>lean "
                             f"{'OVER' if lean > 0 else 'UNDER'} {abs(lean):.1f}</span>")
            else:
                total_sub = "Book: no line yet"
            if mk.get("spread_home") is not None:
                dis = -m["margin_home"] - mk["spread_home"]
                dis_html = (f"<span class='y'>Δ {dis:+.1f} vs book {mk['spread_home']:+g}</span>"
                            if abs(dis) >= 2 else f"Book {mk['spread_home']:+g}")
            else:
                dis_html = "Book: no line yet"
            grid = f"""<div class='grid4'>
<div class='stat'><div class='l'>{esc(away_nm.upper())}</div><div class='v{away_cls}'>{m['away_score']:.0f}</div><div class='s'>win {1 - p_home:.0%}</div></div>
<div class='stat'><div class='l'>{esc(home_nm.upper())}</div><div class='v{home_cls}'>{m['home_score']:.0f}</div><div class='s'>win {p_home:.0%}</div></div>
<div class='stat'><div class='l'>PROJ TOTAL</div><div class='v bl'>{m['total']:.1f}</div><div class='s'>{total_sub}</div></div>
<div class='stat'><div class='l'>MODEL LINE</div><div class='v'>{esc(model_line)}</div><div class='s'>{dis_html}</div></div>
</div>"""
        else:
            grid = ("<div class='muted' style='margin:10px 0'>No Torvik data "
                    "(non-D-I opponent) — not graded.</div>")
        play = ""
        if card:
            status = (bet or {}).get("status", "pending")
            badge = {"win": "<span class='badge b-green'>WIN</span>",
                     "loss": "<span class='badge b-red'>LOSS</span>",
                     "push": "<span class='badge b-yellow'>PUSH</span>"}.get(
                status, "<span class='badge b-grey'>PENDING</span>")
            cls = "play-b" if card["market"] == "total" else "play-g"
            clr = "bl" if card["market"] == "total" else "g"
            play = (f"<div class='play {cls}'><div class='play-head'>"
                    f"<span class='{clr}' style='font-size:12px;font-weight:800'>"
                    f"{esc(card['bet_label'])} {_price(card['price_american'])} "
                    f"({esc(card['book'])})</span>"
                    f"<span class='stars'>{_stars(card['unit_size'])}</span></div>"
                    f"<div class='muted'>Edge +{card['edge']:.1%} · fair "
                    f"{card['fair_prob']:.1%} ({_price(card['fair_american'])}) · "
                    f"conf {card['confidence']}/10 · {card['unit_size']}u {badge}</div></div>")
        elif m and (g.get("pass_reasons")):
            play = f"<div class='dim'>PASS — {esc(g['pass_reasons'][0])}</div>"
        eid = esc(g["espn_event_id"])
        search_key = esc((g.get("matchup") or "").lower())
        details = _details_html(g, (stats_ctx or {}).get(g["espn_event_id"]))
        head = head.replace("<div class='matchup'>",
                            f"<div class='matchup'><span class='chev' id='chev-{eid}'>▸</span>", 1)
        out.append(
            f"<div class='card gamecard{' edge' if card else ''}' "
            f"data-search='{search_key}' onclick='toggleGame(\"{eid}\")'>"
            f"{head}{grid}{play}"
            f"<div class='details' id='det-{eid}' style='display:none'>{details}</div></div>")
    return "".join(out)


def render_best_bets(dates: list[str], bet_idx: dict) -> str:
    out = []
    shown = 0
    for d in dates[:14]:   # last two weeks of cards
        gp = DATA_ROOT / d / "grades.json"
        if not gp.exists():
            continue
        grades = json.loads(gp.read_text())
        cards = grades.get("cards") or []
        if not cards:
            continue
        out.append(f"<div class='muted' style='margin:14px 0 6px'>— {d} —</div>")
        for c in cards:
            bet = bet_idx.get((str(c["espn_event_id"]), c["market"], c["side"])) or {}
            status = bet.get("status", "pending")
            badge = {"win": "<span class='badge b-green'>✅ WIN</span>",
                     "loss": "<span class='badge b-red'>❌ LOSS</span>",
                     "push": "<span class='badge b-yellow'>➖ PUSH</span>"}.get(
                status, "<span class='badge b-grey'>🕐 PENDING</span>")
            pl = bet.get("units_pl")
            pl_txt = ""
            if pl not in (None, "") and status in ("win", "loss"):
                try:
                    pl_txt = f" <b class='{'g' if float(pl) >= 0 else 'r'}'>{float(pl):+.2f}u</b>"
                except ValueError:
                    pass
            out.append(
                f"<div class='card edge'><div class='play-head'>"
                f"<span class='matchup'>{esc(c['bet_label'])} "
                f"{_price(c['price_american'])} <span class='muted'>({esc(c['book'])})</span></span>"
                f"<span class='stars'>{_stars(c['unit_size'])}</span></div>"
                f"<div class='muted'>{esc(c['matchup'])}</div>"
                f"<div class='muted' style='margin-top:6px'>edge <b class='g'>+{c['edge']:.1%}</b> · "
                f"fair {c['fair_prob']:.1%} ({_price(c['fair_american'])}) · "
                f"conf {c['confidence']}/10 · <b>{c['unit_size']}u</b> {esc(c['risk'])} "
                f"· {badge}{pl_txt}</div>"
                f"<div class='dim' style='margin-top:4px'>{esc('; '.join(c.get('reasoning') or []))}</div></div>")
            shown += 1
    if not shown:
        out.append("<div class='card empty'>No bets fired yet. The gates are "
                   "strict by design — line-watch checks every 2h as books "
                   "post lines.</div>")
    return "".join(out)


def render_history(bets: list[dict]) -> str:
    pnl = _pnl(bets)
    net_cls = "g" if pnl["net"] >= 0 else "r"
    tiles = [
        ("NET P&L", f"{pnl['net']:+.2f}u", net_cls),
        ("ROI", f"{pnl['roi']:+.1f}%", net_cls),
        ("WINS", str(pnl["w"]), "g"),
        ("LOSSES", str(pnl["l"]), "r"),
        ("PUSH / PEND", f"{pnl['p']} / {pnl['pending']}", "y"),
    ]
    out = ["<div class='pnl'>" + "".join(
        f"<div class='card'><div class='l'>{t}</div><div class='v {c}'>{v}</div></div>"
        for t, v, c in tiles) + "</div>"]
    if pnl["clv"] is not None:
        out.append(f"<div class='muted' style='margin-bottom:12px'>avg CLV "
                   f"{pnl['clv']:+.2f}pp vs close</div>")
    if not bets:
        out.append("<div class='card empty'>No bets logged yet.</div>")
        return "".join(out)
    for b in sorted(bets, key=lambda r: -(int(r.get("bet_id") or 0))):
        status = b.get("status", "pending")
        badge = {"win": "b-green", "loss": "b-red", "push": "b-yellow"}.get(status, "b-grey")
        line = b.get("line")
        label = (f"{esc(b.get('matchup'))} — {esc(b.get('market'))} "
                 f"{esc(b.get('side'))}" + (f" {line}" if line not in (None, "") else ""))
        pl = b.get("units_pl")
        try:
            pl_txt = f"<b class='{'g' if float(pl) >= 0 else 'r'}'>{float(pl):+.2f}u</b>" \
                if pl not in (None, "") and status != "pending" else ""
        except ValueError:
            pl_txt = ""
        out.append(
            f"<div class='betrow'><div><div style='font-weight:700;font-size:13px'>{label}</div>"
            f"<div class='muted'>{esc(b.get('date'))} · {_price(b.get('price'))} @ "
            f"{esc(b.get('book'))} · {esc(b.get('units_risked'))}u · "
            f"edge {esc(b.get('edge_pred'))} · conf {esc(b.get('confidence'))}/10</div></div>"
            f"<div style='display:flex;gap:8px;align-items:center'>{pl_txt}"
            f"<span class='badge {badge}'>{status.upper()}</span></div></div>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

def _page(this_date: str, grades: dict, dates: list[str], bets: list[dict],
          bet_idx: dict, is_index: bool, stats_ctx: dict | None = None) -> str:
    pnl = _pnl(bets)
    n_cards = len(grades.get("cards") or [])
    pnl_badge = ""
    if pnl["risked"] > 0:
        cls = "b-green" if pnl["net"] >= 0 else "b-red"
        pnl_badge = (f"<span class='badge {cls}'>{pnl['net']:+.2f}u "
                     f"({pnl['roi']:+.1f}% ROI)</span>")
    refresh = "<meta http-equiv='refresh' content='300'>" if is_index else ""
    return f"""<!doctype html><html><head><meta charset='utf-8'>{refresh}
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>NCAAW SHARP — {esc(this_date)}</title><style>{STYLE}</style></head><body>
<div class='header'><div>
<div class='title'>🏀 NCAAW SHARP</div>
<div class='subtitle'>Tuned Torvik Model · Market-Anchored · ML + Spread + Totals ·
updated {esc((grades.get('generated_at') or '')[:16].replace('T', ' '))}Z</div></div>
<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap'>
<span class='badge b-green'>{n_cards} PLAY{'S' if n_cards != 1 else ''}</span>
{pnl_badge}
<a class='badge b-accent' target='_blank' rel='noopener'
href='https://github.com/Scottguts/ncaaw-sharp/issues/new?title=LINE%3A%20Away%20%40%20Home&body=spread_home%3D-6.5%0Atotal%3D141.5%0A%23%20optional%3A%20ml_home%3D-250%20ml_away%3D%2B200%20spread_price%3D-110%20date%3D{esc(this_date)}'>➕ LATE LINE</a></div></div>
<div class='tabs'>
<button class='tab' id='tab-predictions' onclick="showTab('predictions')">🎯 Predictions</button>
<button class='tab' id='tab-bets' onclick="showTab('bets')">⚡ Best Bets</button>
<button class='tab' id='tab-history' onclick="showTab('history')">💰 Bet History</button>
</div>
<div class='wrap'>
<div class='pane' id='pane-predictions'>{render_predictions(grades, dates, this_date, bet_idx, stats_ctx)}</div>
<div class='pane' id='pane-bets' style='display:none'>{render_best_bets(dates, bet_idx)}</div>
<div class='pane' id='pane-history' style='display:none'>{render_history(bets)}</div>
</div>
<div class='wrap dim' style='padding-top:0'>Gates: 1.8–2.5% min edge · 6% max (stale-line reject)
· conf ≥6/10 · 5 cards / 6u per day · early-season sigma widening. NO PLAYS is a correct day.</div>
<script>{TABS_JS}</script></body></html>"""


def build() -> int:
    DOCS.mkdir(exist_ok=True)
    days = sorted((p for p in DATA_ROOT.iterdir()
                   if p.is_dir() and (p / "grades.json").exists()),
                  key=lambda p: p.name, reverse=True)
    dates = [p.name for p in days]
    bets = _read_bets()
    bet_idx = {(str(b["espn_event_id"]), b["market"], b["side"]): b for b in bets}

    for i, day in enumerate(days):
        grades = json.loads((day / "grades.json").read_text())
        page = _page(day.name, grades, dates, bets, bet_idx, is_index=(i == 0),
                     stats_ctx=_team_stats_ctx(day))
        (DOCS / f"{day.name}.html").write_text(page)
        (DOCS / f"{day.name}.json").write_text(
            json.dumps(grades, indent=2, sort_keys=True, default=str))
        if i == 0:
            (DOCS / "index.html").write_text(page)
    # legacy redirects
    for name in ("record.html", "archive.html"):
        (DOCS / name).write_text(
            "<!doctype html><meta http-equiv='refresh' content='0;url=index.html#history'>")
    print(f"[site] built {len(days)} slate page(s) + app dashboard")
    return 0


if __name__ == "__main__":
    sys.exit(build())
