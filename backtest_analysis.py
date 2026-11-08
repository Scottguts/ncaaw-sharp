"""Residual autopsy — where is the model SYSTEMATICALLY wrong?

    python backtest_analysis.py

Replays the deployed weights over every historical game and slices the
signed residuals by bucket. A bucket whose MEAN error is far from zero is a
missing feature (fixable); a bucket with big MAE but ~zero mean is just
variance (not fixable). Candidate features this report was built to judge:
venue/tier home-court, rest days, pace interaction, favorite-size bias.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from ncaaw_model import predict, TRAINED_WEIGHTS

HISTORY = Path(__file__).resolve().parent / "ncaaw_data" / "history"

POWER = {"SEC", "Big Ten", "Big 12", "ACC", "Big East"}
MID = {"Amer", "A10", "MWC", "WCC", "MVC", "CAA", "Ivy", "MAC", "Horz", "ASUN",
       "Sun Belt", "SC", "CUSA", "WAC", "Summit", "Big Sky", "Patriot"}


def tier(conf: str) -> str:
    if conf in POWER:
        return "power"
    if conf in MID:
        return "mid"
    return "low"


def load_rows(season: int):
    games = json.loads((HISTORY / f"games_{season}.json").read_text())
    teams = json.loads((HISTORY / f"teams_{season}.json").read_text())["teams"]
    games.sort(key=lambda g: g["date"])
    wl: dict[str, list[int]] = {}
    last_played: dict[str, str] = {}
    rows = []
    for g in games:
        h, a = g["home"], g["away"]
        th, ta = teams.get(h), teams.get(a)
        d = date.fromisoformat(g["date"])
        rest = {}
        for side, canon in (("home", h), ("away", a)):
            lp = last_played.get(canon)
            rest[side] = (d - date.fromisoformat(lp)).days if lp else 9
        if th and ta:
            hw, hl = wl.get(h, [0, 0])
            aw, al = wl.get(a, [0, 0])
            rows.append({
                "date": g["date"], "season": season,
                "neutral": g["neutral_site"],
                "home": {**th, "record": f"{hw}-{hl}", "conf": g.get("home_conf") or ""},
                "away": {**ta, "record": f"{aw}-{al}", "conf": g.get("away_conf") or ""},
                "home_conf": g.get("home_conf") or "", "away_conf": g.get("away_conf") or "",
                "rest_home": min(rest["home"], 9), "rest_away": min(rest["away"], 9),
                "margin_home": g["home_score"] - g["away_score"],
                "total": g["home_score"] + g["away_score"],
            })
        hwon = g["home_score"] > g["away_score"]
        wl.setdefault(h, [0, 0])[0 if hwon else 1] += 1
        wl.setdefault(a, [0, 0])[1 if hwon else 0] += 1
        last_played[h] = g["date"]
        last_played[a] = g["date"]
    return rows


def main() -> int:
    rows = []
    for s in (2024, 2025, 2026):
        if (HISTORY / f"games_{s}.json").exists():
            rows += load_rows(s)
    print(f"[backtest] {len(rows)} games, deployed weights\n")

    buckets_s = defaultdict(list)   # bucket -> signed spread errors (pred - actual, home margin)
    buckets_t = defaultdict(list)   # bucket -> signed total errors
    for r in rows:
        p = predict(r["away"], r["home"], TRAINED_WEIGHTS, neutral_site=r["neutral"])
        pm = -p["spread"]
        es = pm - r["margin_home"]
        et = p["total"] - r["total"]

        fav = abs(pm)
        fband = ("0-5" if fav < 5 else "5-10" if fav < 10 else "10-20" if fav < 20
                 else "20-30" if fav < 30 else "30+")
        # sign convention: error relative to the PREDICTED FAVORITE
        # (positive = model overrated the favorite)
        ef = es if pm > 0 else -es
        buckets_s[f"favsize {fband}"].append(ef)

        tband = ("<125" if p["total"] < 125 else "125-140" if p["total"] < 140
                 else "140-155" if p["total"] < 155 else "155+")
        buckets_t[f"projtotal {tband}"].append(et)

        if not r["neutral"]:
            buckets_s[f"HCA tier {tier(r['home_conf'])}"].append(es)
        else:
            buckets_s["HCA neutral"].append(es)

        conf_game = (r["home_conf"] and r["home_conf"] == r["away_conf"])
        buckets_s["conf game" if conf_game else "non-con"].append(es)

        radv = r["rest_home"] - r["rest_away"]
        rband = ("home-tired" if radv <= -2 else "even-rest" if radv < 2 else "away-tired")
        buckets_s[f"rest {rband}"].append(es)
        if r["rest_home"] <= 1:
            buckets_s["home b2b"].append(es)
        if r["rest_away"] <= 1:
            buckets_s["away b2b"].append(es)

        month = r["date"][5:7]
        buckets_s[f"month {month}"].append(es)

    def report(name, buckets):
        print(f"── {name} ──")
        print(f"{'bucket':22s} {'n':>6s} {'mean(signed)':>12s} {'MAE':>7s}")
        for k in sorted(buckets):
            v = buckets[k]
            mean = sum(v) / len(v)
            mae = sum(abs(x) for x in v) / len(v)
            flag = "  <-- SYSTEMATIC" if abs(mean) > 0.8 and len(v) > 300 else ""
            print(f"{k:22s} {len(v):6d} {mean:+12.2f} {mae:7.2f}{flag}")
        print()

    report("SPREAD residuals (pred home margin - actual; favsize rows are vs predicted favorite)", buckets_s)
    report("TOTAL residuals (pred - actual)", buckets_t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
