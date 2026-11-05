"""Closing-line snapshot — capture closing prices for pending bets near tip.

    python closing_snapshot.py

ONE odds board fetch per run (quota-safe, via key rotation). For each pending
bet whose game tips within the lookahead window, records closing_price (same
market/side, best at the bet's book, falling back to best target-book quote)
and the devigged closing fair prob. CLV is then computed vig-to-vig at
settlement (bet_tracker) — bet price vs closing price — which fixes the
devig/vig mismatch flagged as open item D in mlb-sharp.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta

from ncaaw_data_scraper import (
    fetch_odds, canon_name, canon_match, _et_date, TARGET_BOOKS,
)
from ncaaw_grader import (
    _markets_for_book, _team_outcome, devig_two_way,
)
from bet_tracker import _read_log, _write_log, _f

LOOKAHEAD_H = 2.0   # snapshot bets tipping within this many hours (or started)
LOOKBACK_H = 1.0


def _closing_quote(odds_event: dict, row: dict) -> tuple[int | None, float | None]:
    """(closing_price, closing_fair_prob) for the bet's market/side."""
    market, side = row.get("market"), row.get("side")
    matchup = row.get("matchup") or " @ "
    away_name, home_name = (matchup.split(" @ ") + [""])[:2]
    team = home_name if side == "home" else away_name
    mkey = {"moneyline": "h2h", "spread": "spreads", "total": "totals"}.get(market)
    if not mkey:
        return None, None
    books = [row.get("book")] + [b for b in TARGET_BOOKS if b != row.get("book")]
    line = _f(row.get("line"))
    price = None
    for book in books:
        outs = _markets_for_book(odds_event, book).get(mkey, [])
        if mkey == "totals":
            o = next((x for x in outs if (x.get("name") or "").lower() == side), None)
        else:
            o = _team_outcome(outs, team)
        if not o or o.get("price") is None:
            continue
        if mkey in ("spreads", "totals") and line is not None and _f(o.get("point")) != line:
            continue   # only a same-line close is comparable
        price = int(o["price"])
        break
    fair = None
    if mkey == "h2h":
        for book in ("pinnacle",) + TARGET_BOOKS:
            outs = _markets_for_book(odds_event, book).get("h2h", [])
            mine = _team_outcome(outs, team)
            other = next((x for x in outs if x is not mine), None)
            if mine and other and mine.get("price") is not None and other.get("price") is not None:
                fair, _ = devig_two_way(int(mine["price"]), int(other["price"]))
                break
    return price, fair


def run() -> int:
    rows = _read_log()
    now = datetime.now(timezone.utc)
    targets = []
    for r in rows:
        if r.get("status") != "pending" or r.get("closing_price"):
            continue
        try:
            tip = datetime.fromisoformat((r.get("tipoff") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if now - timedelta(hours=LOOKBACK_H) <= tip <= now + timedelta(hours=LOOKAHEAD_H):
            targets.append(r)
    if not targets:
        print("[closing] no pending bets near tip")
        return 0

    odds = fetch_odds()
    if not odds.get("available"):
        print(f"[closing] odds unavailable: {odds.get('errors')}")
        return 0

    events = odds.get("games") or []

    updated = 0
    for r in targets:
        matchup = r.get("matchup") or " @ "
        away_name, home_name = (matchup.split(" @ ") + [""])[:2]
        ev = next((og for og in events
                   if _et_date(og.get("commence_time") or "") == (r.get("date") or "")
                   and canon_match(og.get("away_team") or "", away_name)
                   and canon_match(og.get("home_team") or "", home_name)), None)
        if not ev:
            continue
        price, fair = _closing_quote(ev, r)
        if price is not None:
            r["closing_price"] = price
            updated += 1
        if fair is not None:
            r["closing_fair_prob"] = round(fair, 4)
    if updated:
        _write_log(rows)
    print(f"[closing] captured closing prices for {updated}/{len(targets)} bet(s)")
    return 0


if __name__ == "__main__":
    sys.exit(run())
