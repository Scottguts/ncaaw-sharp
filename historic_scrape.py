"""Historic NCAAW data — full-season ESPN results + Torvik season ratings.

    python historic_scrape.py --seasons 2025 2026

Writes ncaaw_data/history/:
    games_<season>.json   — every completed D-I game (date, teams, scores, neutral)
    teams_<season>.json   — Torvik season-end ratings keyed by canonical name

Season = END year (2026 = the 2025-26 season). Torvik's women's endpoint
ignores the &date= as-of parameter (verified 2026-11-04: identical payload),
so season-END ratings are all that exist — the trainer must treat same-season
backtests as having rating lookahead (same caveat as the original jsx tune)
and validate across seasons instead.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from ncaaw_data_scraper import ESPN_API, ESPN_CONF, _get, _save_json, fetch_torvik
from ncaaw_model import canon_name

HISTORY = Path(__file__).resolve().parent / "ncaaw_data" / "history"

# Season windows (END-year keyed): first games ~Nov 4, title game ~first Sun of Apr
SEASON_WINDOW = {
    2025: (date(2024, 11, 4), date(2025, 4, 6)),
    2026: (date(2025, 11, 3), date(2026, 4, 5)),
    2027: (date(2026, 11, 3), date(2027, 4, 4)),
}


def scrape_season_games(season: int, pause: float = 0.25) -> list[dict]:
    start, end = SEASON_WINDOW[season]
    games: list[dict] = []
    d = start
    n_days = (end - start).days + 1
    done = 0
    while d <= end:
        try:
            data = _get(f"{ESPN_API}/scoreboard", params={
                "dates": d.strftime("%Y%m%d"), "limit": 500, "groups": 50})
        except Exception as e:
            print(f"[hist] {d}: fetch failed ({e}) — skipping day")
            d += timedelta(days=1)
            continue
        for ev in data.get("events", []):
            st = ((ev.get("status") or {}).get("type") or {})
            if not st.get("completed"):
                continue
            comp = (ev.get("competitions") or [{}])[0]
            sides = {}
            for c in comp.get("competitors", []):
                t = c.get("team") or {}
                sides[c.get("homeAway")] = {
                    "name": t.get("location") or t.get("displayName") or "",
                    "canon": canon_name(t.get("location") or t.get("displayName") or ""),
                    "score": int(float(c.get("score") or 0)),
                    "conf": ESPN_CONF.get(str(t.get("conferenceId") or ""), ""),
                }
            if "home" not in sides or "away" not in sides:
                continue
            games.append({
                "date": d.isoformat(),
                "season": season,
                "espn_event_id": ev.get("id"),
                "neutral_site": bool(comp.get("neutralSite")),
                "home": sides["home"]["canon"], "away": sides["away"]["canon"],
                "home_name": sides["home"]["name"], "away_name": sides["away"]["name"],
                "home_conf": sides["home"]["conf"], "away_conf": sides["away"]["conf"],
                "home_score": sides["home"]["score"], "away_score": sides["away"]["score"],
            })
        done += 1
        if done % 20 == 0:
            print(f"[hist] season {season}: {done}/{n_days} days, {len(games)} games")
        time.sleep(pause)
        d += timedelta(days=1)
    return games


def run(seasons: list[int]) -> None:
    HISTORY.mkdir(parents=True, exist_ok=True)
    for season in seasons:
        print(f"[hist] === season {season} ===")
        tpath = HISTORY / f"teams_{season}.json"
        if tpath.exists():
            print(f"[hist] teams_{season}.json exists — keeping")
        else:
            teams = fetch_torvik(season)
            _save_json(tpath, {"season": season, "teams": teams})
            print(f"[hist] torvik {season}: {len(teams)} teams")
        gpath = HISTORY / f"games_{season}.json"
        if gpath.exists():
            print(f"[hist] games_{season}.json exists — keeping")
            continue
        games = scrape_season_games(season)
        _save_json(gpath, games)
        print(f"[hist] season {season}: {len(games)} completed games saved")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=[2025, 2026])
    args = ap.parse_args()
    run(args.seasons)
    return 0


if __name__ == "__main__":
    sys.exit(main())
