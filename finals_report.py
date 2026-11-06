"""Finals report — join each slate's model predictions with actual results.

    python finals_report.py            # process every past date missing finals.json
    python finals_report.py --date D   # (re)process one date

Writes ncaaw_data/<date>/finals.json: per-game model-vs-actual with errors,
plus a day summary (spread/total MAE, straight-up %, fired-card record).
Runs in the daily workflow right after settlement, so the dashboard's FINALS
tab fills in the morning after games complete. This is also the eyeball-level
companion to the weekly retune: systematic patterns here (e.g. totals always
low) show up before the tune formally absorbs them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ncaaw_data_scraper import DATA_ROOT, ESPN_API, _get

_ET = ZoneInfo("America/New_York")


def _finals_for_date(d: str) -> dict[str, tuple[int, int]]:
    data = _get(f"{ESPN_API}/scoreboard", params={
        "dates": d.replace("-", ""), "limit": 500, "groups": 50})
    out = {}
    for ev in data.get("events", []):
        if not ((ev.get("status") or {}).get("type") or {}).get("completed"):
            continue
        s = {c["homeAway"]: int(float(c.get("score") or 0))
             for c in ev["competitions"][0]["competitors"]}
        if "home" in s and "away" in s:
            out[str(ev["id"])] = (s["home"], s["away"])
    return out


def build_report(d: str) -> dict | None:
    day = DATA_ROOT / d
    gp = day / "grades.json"
    if not gp.exists():
        return None
    grades = json.loads(gp.read_text())
    finals = _finals_for_date(d)
    rows = []
    s_err = t_err = su = n = 0
    for g in grades.get("games") or []:
        fin = finals.get(str(g.get("espn_event_id")))
        m = g.get("model")
        if not fin or not m:
            continue
        act_margin = fin[0] - fin[1]
        act_total = fin[0] + fin[1]
        sp_e = m["margin_home"] - act_margin
        to_e = m["total"] - act_total
        side_right = (m["margin_home"] > 0) == (act_margin > 0) and act_margin != 0
        rows.append({
            "matchup": g.get("matchup"),
            "away_short": g.get("away_short"), "home_short": g.get("home_short"),
            "model_away": m["away_score"], "model_home": m["home_score"],
            "act_away": fin[1], "act_home": fin[0],
            "model_margin_home": m["margin_home"], "act_margin_home": act_margin,
            "model_total": m["total"], "act_total": act_total,
            "spread_err": round(sp_e, 1), "total_err": round(to_e, 1),
            "side_right": side_right,
            "had_card": bool(g.get("cards")),
        })
        s_err += abs(sp_e)
        t_err += abs(to_e)
        su += 1 if side_right else 0
        n += 1
    if not n:
        return None
    # fired-card record for the day, from bet_log
    import csv
    fired = {"w": 0, "l": 0, "p": 0, "units": 0.0}
    log = DATA_ROOT / "bet_log.csv"
    if log.exists():
        with log.open(newline="") as f:
            for r in csv.DictReader(f):
                if r.get("date") != d or r.get("status") not in ("win", "loss", "push"):
                    continue
                fired["w" if r["status"] == "win" else
                      "l" if r["status"] == "loss" else "p"] += 1
                try:
                    fired["units"] += float(r.get("units_pl") or 0)
                except ValueError:
                    pass
    report = {
        "date": d,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "summary": {
            "n": n,
            "spread_mae": round(s_err / n, 2),
            "total_mae": round(t_err / n, 2),
            "su_pct": round(su / n * 100, 1),
            "fired": {**fired, "units": round(fired["units"], 2)},
        },
        "games": sorted(rows, key=lambda r: abs(r["spread_err"])),
    }
    (day / "finals.json").write_text(json.dumps(report, indent=2))
    print(f"[finals] {d}: {n} games · spread MAE {report['summary']['spread_mae']} "
          f"· total MAE {report['summary']['total_mae']} · SU {report['summary']['su_pct']}%")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    args = ap.parse_args()
    if args.date:
        build_report(args.date)
        return 0
    from datetime import timedelta
    today = datetime.now(_ET).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    done = 0
    for day in sorted(p for p in DATA_ROOT.iterdir() if p.is_dir()):
        if day.name >= today.isoformat() or day.name == "history":
            continue
        # yesterday always reprocesses (late West Coast finals land overnight);
        # older dates only fill in if missing
        if (day / "grades.json").exists() and (
                day.name == yesterday or not (day / "finals.json").exists()):
            if build_report(day.name):
                done += 1
    print(f"[finals] processed {done} date(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
