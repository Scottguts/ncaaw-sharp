"""NCAAW prediction model — exact Python port of the v13 trained JSX model
(ncaaw_ml_v11.jsx, grid-searched on 319 games: spread MAE 9.66, total MAE
10.92, SU 73.0%) fused with the mlb-sharp market-anchored philosophy.

The model projects a spread and total from Barttorvik NCAAW adjusted
efficiencies + four factors. The GRADER (ncaaw_grader.py) then anchors those
projections to the devigged market line and only fires on small, capped
disagreements — the edge comes from disciplined disagreement + line shopping,
NOT from predicting games from scratch.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# v13 trained weights — ported verbatim from the JSX (do not hand-tune;
# re-train on graded games instead)
# ---------------------------------------------------------------------------
TRAINED_WEIGHTS = {
    # spread
    "effWeight": 0.55,
    "tRankWeight": 0.08,
    "winPctWeight": 0.18,
    "ffWeight": 0.65,
    "confAdjust": 0.75,
    "hca": -0.60,              # NOTE: subtracted in rawSpread (away-minus-home), so negative = home edge
    "tempoSens": 0.02,
    "spreadCompressBig": 1.05,   # >20pt raw spreads amplified slightly
    "spreadCompressMid": 1.00,   # 8-20pt neutral
    "spreadCompressSmall": 0.90, # <8pt compressed
    # four factors (spread)
    "eFGW": 0.45,
    "torW": 0.10,
    "orbW": 0.06,
    "ftrW": 0.18,
    # total model
    "totalOwnW": 0.48,
    "totalTorW": 0.25,
    "totalHCA": 0.50,
    "totalTempoBoost": 0.10,
    "totalBiasBase": 5.00,
    "totalBiasOEScale": 0.10,
    "totalBias": 0.0,
    "totalTOMismatch": 0.03,
    "totalLowTempoPen": 3.00,
    "totalVarAmp": 1.20,
    "totalHighCap": 148,
    "totalHighDamp": 0.40,
    "totalLowCap": 100,
    "totalLowDamp": 0.20,
    # v14 features from the 15.4k-game residual autopsy (2026-11-08):
    # power homes outperform flat HCA by ~0.8; home teams cover extra in
    # non-conference buy games; 35+ point favorites stretch further.
    # Defaults are inert — the cross-fold tuner fits them.
    "hcaPowerBonus": 0.0,
    "hcaNonConBonus": 0.0,
    "spreadCompressHuge": 1.05,
}

# Power conferences get the extra home-court bonus (autopsy: -0.80 signed
# residual on 3,667 power home games with the flat HCA)
POWER_CONFS = {"SEC", "Big Ten", "Big 12", "ACC", "Big East"}

# League mean total used by the variance amplifier (v13 constant)
MEAN_TOTAL = 138.0

# Cross-validated error (v13, 319 games). sigma = MAE * sqrt(pi/2) under a
# normal error assumption. These feed the cover-probability layer; the grader
# widens them early-season when Torvik samples are thin.
SPREAD_MAE = 9.66
TOTAL_MAE = 10.92
SPREAD_SIGMA = SPREAD_MAE * math.sqrt(math.pi / 2)   # ~12.1
TOTAL_SIGMA = TOTAL_MAE * math.sqrt(math.pi / 2)     # ~13.7

# Tuned weights override: train_model.py writes weights.json after a
# cross-season-validated tune. When present it replaces the v13 constants
# (which remain the fallback — delete weights.json to roll back).
_WEIGHTS_FILE = Path(__file__).resolve().parent / "weights.json"
if _WEIGHTS_FILE.exists():
    try:
        _tuned = json.loads(_WEIGHTS_FILE.read_text())
        TRAINED_WEIGHTS = {**TRAINED_WEIGHTS, **(_tuned.get("weights") or {})}
        SPREAD_SIGMA = float(_tuned.get("spread_sigma") or SPREAD_SIGMA)
        TOTAL_SIGMA = float(_tuned.get("total_sigma") or TOTAL_SIGMA)
    except (ValueError, OSError):
        pass   # malformed weights.json -> keep v13

# Conference strength multipliers (from the JSX; Torvik names on the left)
CONF_STRENGTH = {
    "SEC": 1.05, "Big Ten": 1.03, "Big 12": 1.02, "ACC": 1.0, "Big East": 0.98,
    "Amer": 0.95, "AAC": 0.95, "MWC": 0.93, "WCC": 0.92, "Pac-12": 0.91,
    "A10": 0.90, "A-10": 0.90, "MVC": 0.89, "CAA": 0.88, "Horz": 0.87,
    "Horizon": 0.87, "ASUN": 0.87, "MAAC": 0.86, "Sun Belt": 0.86, "SB": 0.86,
    "SC": 0.86, "SoCon": 0.86, "CUSA": 0.85, "C-USA": 0.85, "Patriot": 0.85,
    "Pat": 0.85, "OVC": 0.84, "WAC": 0.84, "Ivy": 0.84, "Big Sky": 0.83,
    "BSky": 0.83, "Summit": 0.83, "Sum": 0.83, "Southland": 0.82, "Slnd": 0.82,
    "Big South": 0.82, "BSth": 0.82, "Big West": 0.82, "BW": 0.82,
    "America East": 0.81, "AE": 0.81, "NEC": 0.80, "SWAC": 0.78, "MEAC": 0.76,
    "MAC": 0.87, "Ind.": 0.80,
}


def win_pct_from_record(record: str) -> float:
    """'25-0' -> 1.0; malformed -> 0.5 (same as the JSX parseWinPct)."""
    try:
        w, l = (int(x) for x in record.split("-")[:2])
        return w / (w + l) if (w + l) > 0 else 0.5
    except (ValueError, AttributeError):
        return 0.5


def predict(away: dict, home: dict, weights: dict | None = None,
            neutral_site: bool = False,
            away_inj: dict | None = None, home_inj: dict | None = None) -> dict:
    """Project (away_score, home_score, spread, total) for one game.

    Team dicts need: adjOE, adjDE, tempo, eFG, eFGD, tor, torD, orb, drb,
    ftr, ftrD, tRank, record, conf. `spread` is away-minus-home (positive =
    away favored), matching the JSX convention.

    neutral_site zeroes both the spread HCA and the total HCA.
    Injury dicts are {adjOE: float, adjDE: float} deltas (optional).
    """
    w = weights or TRAINED_WEIGHTS

    a = dict(away)
    h = dict(home)
    if away_inj:
        a["adjOE"] = a["adjOE"] + away_inj.get("adjOE", 0.0)
        a["adjDE"] = a["adjDE"] + away_inj.get("adjDE", 0.0)
    if home_inj:
        h["adjOE"] = h["adjOE"] + home_inj.get("adjOE", 0.0)
        h["adjDE"] = h["adjDE"] + home_inj.get("adjDE", 0.0)

    # ── possession estimate ──
    avg_tempo = (a["tempo"] + h["tempo"]) / 2
    tempo_delta = ((a["tempo"] - 70) + (h["tempo"] - 70)) * w["tempoSens"]
    proj_poss = avg_tempo + tempo_delta

    # ── per-team scoring (total model) ──
    away_to_adv = (a["tor"] - h["torD"]) * w["totalTorW"]
    home_to_adv = (h["tor"] - a["torD"]) * w["totalTorW"]
    own_w = w["totalOwnW"]
    away_base_eff = a["adjOE"] * own_w + h["adjDE"] * (1 - own_w) + away_to_adv
    home_base_eff = h["adjOE"] * own_w + a["adjDE"] * (1 - own_w) + home_to_adv

    total_hca = 0.0 if neutral_site else w["totalHCA"]
    away_pts_raw = (away_base_eff / 100) * proj_poss
    home_pts_raw = (home_base_eff / 100) * proj_poss + total_hca

    tempo_boost = (proj_poss - 70) * w["totalTempoBoost"]
    avg_oe = (a["adjOE"] + h["adjOE"]) / 2
    dynamic_bias = w["totalBiasBase"] + (avg_oe - 105) * w["totalBiasOEScale"]
    to_mismatch = (max(0.0, a["tor"] - 20) * max(0.0, h["torD"] - 22)
                   + max(0.0, h["tor"] - 20) * max(0.0, a["torD"] - 22)) * w["totalTOMismatch"]
    low_tempo_pen = (68 - proj_poss) * w["totalLowTempoPen"] if proj_poss < 68 else 0.0

    raw_total = (away_pts_raw + home_pts_raw + tempo_boost + dynamic_bias
                 + w["totalBias"] - to_mismatch - low_tempo_pen)

    proj_total = MEAN_TOTAL + (raw_total - MEAN_TOTAL) * w["totalVarAmp"]
    if proj_total > w["totalHighCap"]:
        proj_total = w["totalHighCap"] + (proj_total - w["totalHighCap"]) * w["totalHighDamp"]
    if proj_total < w["totalLowCap"]:
        proj_total = w["totalLowCap"] - (w["totalLowCap"] - proj_total) * w["totalLowDamp"]

    # ── spread projection ──
    eff_diff = (a["adjOE"] - a["adjDE"]) - (h["adjOE"] - h["adjDE"])
    raw_rank_diff = h.get("tRank", 180) - a.get("tRank", 180)
    rank_diff = math.copysign(math.log1p(abs(raw_rank_diff)), raw_rank_diff) * 3 if raw_rank_diff else 0.0
    wp_diff = win_pct_from_record(a.get("record", "")) - win_pct_from_record(h.get("record", ""))
    a_conf = CONF_STRENGTH.get(a.get("conf", ""), 1.0)
    h_conf = CONF_STRENGTH.get(h.get("conf", ""), 1.0)
    ff_diff = (((a["eFG"] - h["eFGD"]) - (h["eFG"] - a["eFGD"])) * w["eFGW"]
               + ((h["torD"] - a["tor"]) - (a["torD"] - h["tor"])) * w["torW"]
               + ((a["orb"] - (100 - h["drb"])) - (h["orb"] - (100 - a["drb"]))) * w["orbW"]
               + ((a["ftr"] - h["ftrD"]) - (h["ftr"] - a["ftrD"])) * w["ftrW"])
    hca = 0.0 if neutral_site else w["hca"]
    if not neutral_site:
        if h.get("conf") in POWER_CONFS:
            hca += w.get("hcaPowerBonus", 0.0)
        if h.get("conf") and a.get("conf") and h["conf"] != a["conf"]:
            hca += w.get("hcaNonConBonus", 0.0)
    raw_spread = (eff_diff * w["effWeight"]
                  + rank_diff * w["tRankWeight"]
                  + wp_diff * w["winPctWeight"] * 30
                  + ff_diff * w["ffWeight"]
                  + (a_conf - h_conf) * w["confAdjust"] * 10
                  - hca)

    abs_raw = abs(raw_spread)
    compress = (w.get("spreadCompressHuge", w["spreadCompressBig"]) if abs_raw > 35
                else w["spreadCompressBig"] if abs_raw > 20
                else w["spreadCompressMid"] if abs_raw > 8
                else w["spreadCompressSmall"])
    proj_spread = raw_spread * compress

    away_score = round((proj_total + proj_spread) / 2, 1)
    home_score = round((proj_total - proj_spread) / 2, 1)
    return {
        "away_score": away_score,
        "home_score": home_score,
        "spread": round(proj_spread, 1),     # away minus home
        "total": round(proj_total, 1),
        "proj_poss": round(proj_poss, 1),
    }


# ---------------------------------------------------------------------------
# Probability layer — margins to probabilities via a normal error model
# ---------------------------------------------------------------------------

def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def margin_to_ml_prob(fair_margin_home: float, sigma: float = SPREAD_SIGMA) -> float:
    """P(home wins outright) given the fair home margin (home minus away)."""
    return norm_cdf(fair_margin_home / sigma)


def cover_prob(fair_margin_home: float, book_spread_home: float,
               sigma: float = SPREAD_SIGMA) -> float:
    """P(home covers book_spread_home). book_spread_home is the home handicap
    (e.g. -6.5 means home favored by 6.5): home covers when
    actual_margin + book_spread_home > 0."""
    return norm_cdf((fair_margin_home + book_spread_home) / sigma)


def total_over_prob(fair_total: float, book_total: float,
                    sigma: float = TOTAL_SIGMA) -> float:
    """P(game total goes OVER book_total)."""
    return norm_cdf((fair_total - book_total) / sigma)


def early_season_sigma(base_sigma: float, games_a: int, games_b: int,
                       full_at: int = 10) -> float:
    """Widen sigma while Torvik samples are thin. At 0 games played the error
    doubles; it tapers linearly to the CV-validated sigma by `full_at` games
    for BOTH teams. This naturally shrinks edges (fewer bets) in November —
    a quiet early season is a correct early season."""
    g = min(games_a, games_b)
    if g >= full_at:
        return base_sigma
    return base_sigma * (2.0 - (g / full_at))


# ---------------------------------------------------------------------------
# Team-name normalization — Torvik <-> ESPN <-> The Odds API
# ---------------------------------------------------------------------------
# Torvik uses "Connecticut", "North Carolina St.", "Tennessee Martin";
# ESPN/Odds API use "UConn Huskies" (displayName) / "UConn" (location).
# We canonicalize everything to a lowercase key and keep a manual alias map
# for the stragglers. Unmatched teams fall back to difflib fuzzy match in the
# scraper (with a cutoff, so a bad match can't silently poison a card).

_SUFFIX_RE = re.compile(
    r"\s+(huskies|bruins|gamecocks|longhorns|tigers|wolverines|blue devils|"
    r"cardinals|golden gophers|terrapins|commodores|sooners|trojans|horned frogs|"
    r"hawkeyes|lady vols|spartans|wolfpack|buckeyes|wildcats|rebels|tar heels|"
    r"mountaineers|ducks|cornhuskers|red raiders|crimson tide|bears|cowgirls|"
    r"lady bulldogs|bulldogs|fighting irish|cyclones|fighting illini|illini|"
    r"aggies|bearcats|badgers|yellow jackets|boilermakers|nittany lions|"
    r"demon deacons|bluejays|knights|mustangs|blue demons|red storm|friars|"
    r"musketeers|scarlet knights|hurricanes|panthers|eagles|seminoles|shockers|"
    r"blazers|pirates|green wave|roadrunners|owls|golden hurricane|hilltoppers|"
    r"lady toppers|cardinal|gaels|waves|dons|lions|toreros|broncos|zags|"
    r"gauchos|anteaters|highlanders|49ers|mocs|paladins|terriers|catamounts|"
    r"wolf pack|lobos|aztecs|rams|falcons|rainbow wahine|utes|buffaloes|"
    r"sun devils|beavers|cougars|vandals|bengals|bobcats|grizzlies|lumberjacks|"
    r"thunderbirds|jackrabbits|coyotes|bison|mavericks|leathernecks|salukis|"
    r"sycamores|redbirds|braves|purple aces|racers|colonels|govs|governors|"
    r"skyhawks|gators|seahawks|dolphins|ospreys|hatters|flames|jaguars|"
    r"trailblazers|islanders|cardinals|vaqueros|dukes|spiders|minutewomen|"
    r"explorers|billikens|flyers|bonnies|rams|colonials|patriots|revolutionaries)$",
    re.IGNORECASE)

# Torvik name -> canonical (ESPN location). Extend as mismatches surface in
# ncaaw_data/<date>/unmatched_teams.json.
TORVIK_ALIASES = {
    "connecticut": "uconn",
    "north carolina st.": "nc state",
    "southern california": "usc",
    "mississippi": "ole miss",
    "tennessee martin": "ut martin",
    "louisiana monroe": "ul monroe",
    "texas a&m corpus chris": "texas a&m-corpus christi",
    "texas a&m corpus christi": "texas a&m-corpus christi",
    "st. john's": "st. john's (ny)",
    "saint mary's": "saint mary's (ca)",
    "fort wayne": "purdue fort wayne",
    "massachusetts": "umass",
    "miami fl": "miami",
    "miami oh": "miami (oh)",
    "central florida": "ucf",
    "florida international": "florida intl",
    "texas christian": "tcu",
    "virginia commonwealth": "vcu",
    "nevada las vegas": "unlv",
    "brigham young": "byu",
    "southern methodist": "smu",
    "louisiana state": "lsu",
    # ESPN location -> Torvik, surfaced by unmatched_teams.json 2026-11-04
    "app state": "appalachian st.",
    "california baptist": "cal baptist",
    "long island university": "liu",
    "lsu new orleans": "new orleans",
    "queens university": "queens",
    "south carolina upstate": "usc upstate",
}


def canon_match(mascot_name: str, clean_name: str) -> bool:
    """Robust team-name equality across Torvik/ESPN/Odds-API spellings.

    ASYMMETRIC by design: `mascot_name` is the Odds-API-style name that may
    carry a mascot ('Cal Baptist Lancers'); `clean_name` is the ESPN
    location / Torvik name ('California Baptist'). Exact canon match first;
    on miss, retry with 1-2 trailing words dropped from mascot_name only,
    re-canoning so aliases apply to the trimmed form. Trimming only one side
    prevents 'Texas Tech' -> 'Texas' false positives while catching mascots
    missing from the suffix list — the list never needs to be complete again."""
    ca, cb = canon_name(mascot_name), canon_name(clean_name)
    if ca == cb:
        return bool(ca)
    words = (mascot_name or "").split()
    for k in (1, 2):
        if len(words) > k and canon_name(" ".join(words[:-k])) == cb:
            return True
    return False


def canon_name(name: str) -> str:
    """Canonical lowercase team key: strips mascots, periods, punctuation."""
    n = (name or "").strip().lower().replace("-", " ")
    n = _SUFFIX_RE.sub("", n).strip()
    n = TORVIK_ALIASES.get(n, n)
    n = n.replace("state", "st").replace("st.", "st")
    n = re.sub(r"[^a-z0-9& ]", "", n)
    n = re.sub(r"\s+", " ", n)
    return n
