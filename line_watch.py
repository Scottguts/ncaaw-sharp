"""Line-drop watcher — catch value the moment books post NCAAW lines.

    python line_watch.py

Runs every ~2h during posting hours (line-watch.yml). Each run:
  1. Re-scrapes the slate + odds board (1 Odds-API request) and re-grades.
  2. Compares graded cards against bet_log.csv — anything already fired
     today stands (bets are never retracted by a re-run).
  3. NEW cards that fit the remaining slate caps (5 cards / 6u per day,
     counting already-fired bets) are posted to Discord as a LINE DROP
     alert and appended to the bet log.
  4. Rebuilds cards.md / grades.json / the site, so the dashboard always
     shows the freshest board.

Early lines are where the edge lives — model-vs-market gaps are widest
before the market converges, and betting them maximizes CLV.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import ncaaw_data_scraper as scraper
import ncaaw_grader as grader
from bet_tracker import _read_log, append_pending, append_shadow, build_record
from notify import post_discord, _fmt_price, SITE_URL
import build_site

_ET = ZoneInfo("America/New_York")


def _todays_fired(rows: list[dict], today: str) -> list[dict]:
    return [r for r in rows if r.get("date") == today
            and r.get("status") in ("pending", "win", "loss", "push")]


def run() -> int:
    today = datetime.now(_ET).date()
    today_iso = today.isoformat()

    # 0. quota saver: if nothing on today's slate is still pregame, there is
    # nothing left to bet — skip the odds call entirely (ESPN check is free)
    try:
        upcoming = [g for g in scraper.fetch_slate(today)
                    if (g.get("status") or "").upper().endswith("SCHEDULED")]
        if not upcoming:
            print("[watch] no pregame games left today — skipping odds call")
            return 0
        print(f"[watch] {len(upcoming)} pregame game(s) remain")
    except Exception as e:
        print(f"[watch] pregame check failed ({e}) — proceeding with full run")

    # 1. fresh scrape (slate + torvik-or-cache + 1 odds call) + grade
    scraper.run(today)
    out = grader.run(today_iso)
    if not out.get("odds_available"):
        print("[watch] odds unavailable — nothing to compare")
        return 0

    # 2. diff vs already-fired bets
    rows = _read_log()
    fired = _todays_fired(rows, today_iso)
    fired_keys = {(str(r["espn_event_id"]), r["market"], r["side"]) for r in fired}
    fired_units = sum(float(r.get("units_risked") or 0) for r in fired)
    slots_left = grader.MAX_CARDS_PER_SLATE - len(fired)
    units_left = grader.MAX_UNITS_PER_SLATE - fired_units

    now = datetime.now(timezone.utc)
    new_cards = []
    for c in out.get("cards") or []:
        key = (str(c["espn_event_id"]), c["market"], c["side"])
        if key in fired_keys:
            continue
        try:   # never fire on a game that already tipped
            tip = datetime.fromisoformat(c["start"].replace("Z", "+00:00"))
            if tip <= now:
                continue
        except (ValueError, AttributeError):
            pass
        if slots_left <= 0 or c["unit_size"] > units_left:
            print(f"[watch] {c['bet_label']} clears gates but slate caps are "
                  f"used up ({len(fired)} cards / {fired_units:g}u fired) — skipping")
            continue
        new_cards.append(c)
        slots_left -= 1
        units_left -= c["unit_size"]

    # 3. alert + log the new ones
    if new_cards:
        fields = [{
            "name": f"{c['bet_label']}  {_fmt_price(c['price_american'])} ({c['book']})",
            "value": (f"{c['matchup']}\n"
                      f"fair {c['fair_prob']:.1%} ({_fmt_price(c['fair_american'])}) · "
                      f"edge **{c['edge']:.1%}** · conf {c['confidence']}/10 · "
                      f"**{c['unit_size']}u** {c['risk']}"),
            "inline": False,
        } for c in new_cards]
        fields.append({"name": "📊 Live board",
                       "value": f"[{SITE_URL}]({SITE_URL})", "inline": False})
        post_discord({"embeds": [{
            "title": f"🚨 LINE DROP — {len(new_cards)} new best bet(s) · {today_iso}",
            "url": SITE_URL,
            "color": 0xF1C40F,
            "fields": fields,
            "footer": {"text": "Fired on line drop for max CLV · caps include "
                               "earlier bets today"},
        }]})
        append_pending(today_iso)
        build_record()
        print(f"[watch] fired {len(new_cards)} new card(s)")
    else:
        print(f"[watch] no new value ({len(out.get('cards') or [])} graded cards, "
              f"{len(fired)} already fired)")
    # paper tier accumulates silently on every sweep (no Discord, no caps)
    if append_shadow(today_iso):
        build_record()

    # 4. keep the dashboard fresh
    build_site.build()
    return 0


if __name__ == "__main__":
    sys.exit(run())
