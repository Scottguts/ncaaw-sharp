"""Discord notifications — betting card embeds + DATA OUTAGE alert.

    python notify.py --date 2026-11-04

Reads grades.json. Posts to DISCORD_WEBHOOK_URL. An odds outage posts a red
@here alert so a dark slate never masquerades as a disciplined no-play
(mlb-sharp rule).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

from ncaaw_data_scraper import DATA_ROOT

GREEN, RED, GREY = 0x2ECC71, 0xE74C3C, 0x95A5A6


def _fmt_price(p: int) -> str:
    return f"+{p}" if p > 0 else str(p)


def post_discord(payload: dict) -> bool:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("[notify] DISCORD_WEBHOOK_URL not set — skipping")
        return False
    r = requests.post(url, json=payload, timeout=20)
    ok = 200 <= r.status_code < 300
    print(f"[notify] discord {'ok' if ok else f'failed {r.status_code}'}")
    return ok


def build_payload(grades: dict) -> dict:
    date = grades["date"]
    cards = grades.get("cards") or []
    if not grades.get("odds_available"):
        return {
            "content": "@here 🚨 **NCAAW DATA OUTAGE** — odds unavailable, slate is dark. "
                       "This is NOT a no-play day. Check API quota / odds.json.",
            "embeds": [{
                "title": f"NCAAW {date} — DATA OUTAGE",
                "color": RED,
                "description": "Scraper could not fetch odds. "
                               "`gh secret set ODDS_API_KEY_N` with a fresh key, "
                               "then re-dispatch daily-bets.",
            }],
        }
    if not cards:
        return {
            "embeds": [{
                "title": f"🏀 NCAAW {date} — NO PLAYS",
                "color": GREY,
                "description": (f"{grades.get('n_games', 0)} games graded; nothing cleared "
                                "the gates. A quiet day is a correct day."),
            }],
        }
    fields = []
    for c in cards:
        fields.append({
            "name": f"{c['bet_label']}  {_fmt_price(c['price_american'])} ({c['book']})",
            "value": (f"{c['matchup']}\n"
                      f"fair {c['fair_prob']:.1%} ({_fmt_price(c['fair_american'])}) · "
                      f"edge **{c['edge']:.1%}** · conf {c['confidence']}/10 · "
                      f"**{c['unit_size']}u** {c['risk']}"),
            "inline": False,
        })
    total_u = sum(c["unit_size"] for c in cards)
    return {
        "embeds": [{
            "title": f"🏀 NCAAW {date} — {len(cards)} play(s), {total_u:g}u",
            "color": GREEN,
            "fields": fields,
            "footer": {"text": "v13 Torvik model · market-anchored · "
                               "gates: 1.8-2.5% min edge, 6% max, conf ≥6"},
        }],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    gp = DATA_ROOT / args.date / "grades.json"
    if not gp.exists():
        print(f"[notify] no grades.json for {args.date}")
        return 1
    grades = json.loads(gp.read_text())
    post_discord(build_payload(grades))
    return 0


if __name__ == "__main__":
    sys.exit(main())
