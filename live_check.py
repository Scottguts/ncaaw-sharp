"""Live line checker — is an IN-GAME line a viable play?

    python live_check.py --away "Stetson" --home "Louisville" \
        --spread-home -12.5 --total 131.5 [--price -110]

Pulls the live score/clock from ESPN, projects the rest of the game from the
model's pregame rates (margin and total scale with the fraction of game
remaining; variance scales with sqrt of time left), and renders a verdict.

ADVISORY ONLY — completely separate from pregame picks: nothing here touches
bet_log.csv. Verdicts are appended to ncaaw_data/live_checks.json for review.
Live books hold more vig and shade heavily, so the viability bar is higher
than pregame (3.5% min edge) and the stale-line reject wider (12%).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ncaaw_data_scraper import DATA_ROOT, ESPN_API, _get
from ncaaw_model import canon_match, norm_cdf
from ncaaw_grader import edge_pct, prob_to_american

_ET = ZoneInfo("America/New_York")

REG_MINUTES = 40.0        # 4 x 10-minute quarters
MIN_EDGE_LIVE = 0.035     # live books shade hard — demand more than pregame
MAX_EDGE_LIVE = 0.12      # beyond this the submitted line is stale/typo'd
MIN_FRACTION = 0.10       # <4 game-minutes left: variance math unreliable -> pass


def _minutes_remaining(status: dict) -> float | None:
    """From ESPN status: period (1-4, OT=5+) + displayClock ('5:23')."""
    period = int(status.get("period") or 0)
    clock = str(status.get("displayClock") or "0:00")
    try:
        mins, secs = clock.replace(".", ":").split(":")[:2]
        clock_min = int(mins) + int(secs) / 60.0
    except (ValueError, AttributeError):
        clock_min = 0.0
    if period <= 0:
        return REG_MINUTES
    if period <= 4:
        return (4 - period) * 10.0 + clock_min
    return clock_min   # OT: only the current extra period remains


def find_live_game(away: str, home: str) -> dict | None:
    today = datetime.now(_ET).date().strftime("%Y%m%d")
    data = _get(f"{ESPN_API}/scoreboard", params={
        "dates": today, "limit": 500, "groups": 50})
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        h, a = sides.get("home"), sides.get("away")
        if not h or not a:
            continue
        hn = (h.get("team") or {}).get("location") or ""
        an = (a.get("team") or {}).get("location") or ""
        if canon_match(home, hn) and canon_match(away, an):
            return {
                "event_id": str(ev.get("id")),
                "name": ev.get("name"),
                "state": ((ev.get("status") or {}).get("type") or {}).get("state"),
                "status": ev.get("status") or {},
                "home_score": int(float(h.get("score") or 0)),
                "away_score": int(float(a.get("score") or 0)),
                "home_name": hn, "away_name": an,
            }
    return None


def check(away: str, home: str, spread_home: float | None, total: float | None,
          price: int = -110) -> dict:
    live = find_live_game(away, home)
    if not live:
        return {"ok": False, "verdict": f"no game found today for {away} @ {home}"}
    today = datetime.now(_ET).date().isoformat()
    grades_p = DATA_ROOT / today / "grades.json"
    model = None
    if grades_p.exists():
        for g in json.loads(grades_p.read_text()).get("games") or []:
            if g.get("espn_event_id") == live["event_id"]:
                model = g.get("model")
                sigma = g.get("sigma") or {}
                break
    if not model:
        return {"ok": False, "verdict": "game not in today's graded slate (no model)"}

    remaining = _minutes_remaining(live["status"])
    f = max(0.0, min(1.0, (remaining or 0) / REG_MINUTES))
    if live["state"] == "pre":
        f = 1.0
    cur_margin = live["home_score"] - live["away_score"]
    cur_total = live["home_score"] + live["away_score"]
    exp_margin = cur_margin + model["margin_home"] * f
    exp_total = cur_total + model["total"] * f
    sig_s = (sigma.get("spread") or 12.0) * math.sqrt(max(f, 0.05))
    sig_t = (sigma.get("total") or 16.0) * math.sqrt(max(f, 0.05))

    result = {
        "ok": True,
        "game": live["name"],
        "live": f"{live['away_name']} {live['away_score']} — "
                f"{live['home_score']} {live['home_name']} · "
                f"{remaining:.1f} min left" if live["state"] == "in"
                else f"state: {live['state']}",
        "fraction_left": round(f, 3),
        "projected_final_margin_home": round(exp_margin, 1),
        "projected_final_total": round(exp_total, 1),
        "checks": [],
    }
    if f < MIN_FRACTION and live["state"] == "in":
        result["verdict"] = "PASS — under 4 game-minutes left, projection unreliable"
        return result

    plays = []
    if spread_home is not None:
        p_home_cover = norm_cdf((exp_margin + spread_home) / sig_s)
        for side, p, label in (("home", p_home_cover, f"{live['home_name']} {spread_home:+g}"),
                               ("away", 1 - p_home_cover, f"{live['away_name']} {-spread_home:+g}")):
            e = edge_pct(p, price)
            plays.append({"market": "live spread", "label": label,
                          "fair": round(p, 3), "edge": round(e, 4)})
    if total is not None:
        p_over = norm_cdf((exp_total - total) / sig_t)
        for side, p in (("Over", p_over), ("Under", 1 - p_over)):
            e = edge_pct(p, price)
            plays.append({"market": "live total", "label": f"{side} {total:g}",
                          "fair": round(p, 3), "edge": round(e, 4)})
    viable = [p for p in plays if MIN_EDGE_LIVE <= p["edge"] <= MAX_EDGE_LIVE]
    rejected_stale = [p for p in plays if p["edge"] > MAX_EDGE_LIVE]
    result["checks"] = plays
    if viable:
        best = max(viable, key=lambda p: p["edge"])
        result["verdict"] = (f"VIABLE — {best['label']} ({price:+d}): fair "
                             f"{best['fair']:.1%}, edge {best['edge']:.1%} "
                             f"[live-advisory, not a pregame card]")
    elif rejected_stale:
        result["verdict"] = ("PASS — biggest 'edge' exceeds the 12% stale-line "
                             "reject; re-check the number you entered")
    else:
        result["verdict"] = "PASS — no side clears the 3.5% live edge bar"

    log_p = DATA_ROOT / "live_checks.json"
    try:
        log = json.loads(log_p.read_text()) if log_p.exists() else []
    except ValueError:
        log = []
    log.append({"at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                **{k: v for k, v in result.items() if k != "ok"}})
    log_p.write_text(json.dumps(log[-500:], indent=2))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--away", required=True)
    ap.add_argument("--home", required=True)
    ap.add_argument("--spread-home", type=float)
    ap.add_argument("--total", type=float)
    ap.add_argument("--price", type=int, default=-110)
    args = ap.parse_args()
    r = check(args.away, args.home, args.spread_home, args.total, args.price)
    print(json.dumps(r, indent=2))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
