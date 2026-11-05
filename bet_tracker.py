"""Bet tracker — append fired cards to bet_log.csv, settle via ESPN finals,
rebuild record.md.

    python bet_tracker.py --append 2026-11-04     # log today's cards as pending
    python bet_tracker.py --settle                # settle all pending bets
    python bet_tracker.py --record                # rebuild record.md

HARD RULE (from mlb-sharp): bet_log.csv is the record of what was ACTUALLY
bet. Model changes apply going forward — re-grading past dates must never
alter settled rows. Writes are atomic (tmp + rename).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ncaaw_data_scraper import DATA_ROOT, _get, ESPN_API, _et_date

BET_LOG = DATA_ROOT / "bet_log.csv"
# Paper tier: half-gate "shadow" bets at a flat 0.5u — evidence accumulation
# only, never real money, never capped, never posted to Discord.
SHADOW_LOG = DATA_ROOT / "shadow_log.csv"

CSV_FIELDS = [
    "bet_id", "recommended_at", "date", "espn_event_id", "matchup", "tipoff",
    "market", "side", "line", "book", "price", "fair_prob", "fair_american",
    "edge_pred", "confidence", "units_risked",
    "status", "units_pl", "final_home", "final_away", "settled_at",
    "actual_result",
    "closing_price", "closing_fair_prob", "clv_pct", "beat_close",
    "notes",
]


# ---------------------------------------------------------------------------
# CSV plumbing (atomic writes, corrupt-row-tolerant ids — from mlb-sharp)
# ---------------------------------------------------------------------------

def _ensure_log(path: Path = BET_LOG) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()
    return path


def _read_log(path: Path = BET_LOG) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _write_log(rows: list[dict], path: Path = BET_LOG) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in CSV_FIELDS})
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _next_id(rows: list[dict]) -> int:
    mx = 0
    for r in rows:
        try:
            mx = max(mx, int(r.get("bet_id") or 0))
        except (ValueError, TypeError):
            continue   # tolerate a corrupt row rather than dying
    return mx + 1


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _payout_units(price: int, units: float) -> float:
    """Profit (not stake) in units for a WIN at american `price`."""
    return units * (price / 100.0) if price > 0 else units * (100.0 / -price)


# ---------------------------------------------------------------------------
# Append pending
# ---------------------------------------------------------------------------

# Slate caps shared with the grader — enforced ACROSS runs here (the grader
# caps one run; daily + line-watch re-runs must not stack past the day cap).
MAX_CARDS_PER_SLATE = 5
MAX_UNITS_PER_SLATE = 6.0


def append_cards(cards: list[dict], target_date: str) -> int:
    """Append specific cards as pending, respecting the day's remaining caps.
    Cards already in the log (same event/market/side) are skipped."""
    _ensure_log()
    rows = _read_log()
    seen = {(str(r["espn_event_id"]), r["market"], r["side"]) for r in rows}
    today_rows = [r for r in rows if r.get("date") == target_date
                  and r.get("status") in ("pending", "win", "loss", "push")]
    slots = MAX_CARDS_PER_SLATE - len(today_rows)
    units = MAX_UNITS_PER_SLATE - sum(_f(r.get("units_risked"), 0.0) or 0.0
                                      for r in today_rows)
    added = 0
    for c in sorted(cards, key=lambda c: -(c.get("edge") or 0)):
        key = (str(c["espn_event_id"]), c["market"], c["side"])
        if key in seen:
            continue
        if slots <= 0 or (c.get("unit_size") or 0) > units:
            print(f"[tracker] {c.get('bet_label')} skipped — day caps reached")
            continue
        rows.append({
            "bet_id": _next_id(rows) + added,
            "recommended_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "date": target_date,
            "espn_event_id": c["espn_event_id"],
            "matchup": c["matchup"],
            "tipoff": c["start"],
            "market": c["market"], "side": c["side"],
            "line": "" if c.get("line") is None else c["line"],
            "book": c["book"], "price": c["price_american"],
            "fair_prob": c["fair_prob"], "fair_american": c["fair_american"],
            "edge_pred": c["edge"], "confidence": c["confidence"],
            "units_risked": c["unit_size"],
            "status": "pending", "notes": c.get("risk", ""),
        })
        seen.add(key)
        added += 1
        slots -= 1
        units -= c.get("unit_size") or 0
    if added:
        _write_log(rows)
    print(f"[tracker] appended {added} pending bet(s) for {target_date}")
    return added


def append_pending(target_date: str) -> int:
    """Append all of grades.json's fired cards (cap-aware across runs)."""
    grades = DATA_ROOT / target_date / "grades.json"
    if not grades.exists():
        print(f"[tracker] no grades.json for {target_date}")
        return 0
    cards = json.loads(grades.read_text()).get("cards") or []
    return append_cards(cards, target_date)


def append_shadow(target_date: str) -> int:
    """Paper-log shadow cards (no caps, flat 0.5u, dedup by event/market/side)."""
    grades = DATA_ROOT / target_date / "grades.json"
    if not grades.exists():
        return 0
    cards = json.loads(grades.read_text()).get("shadow_cards") or []
    if not cards:
        return 0
    _ensure_log(SHADOW_LOG)
    rows = _read_log(SHADOW_LOG)
    seen = {(str(r["espn_event_id"]), r["market"], r["side"]) for r in rows}
    added = 0
    for c in cards:
        key = (str(c["espn_event_id"]), c["market"], c["side"])
        if key in seen:
            continue
        rows.append({
            "bet_id": _next_id(rows) + added,
            "recommended_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "date": target_date,
            "espn_event_id": c["espn_event_id"],
            "matchup": c["matchup"], "tipoff": c["start"],
            "market": c["market"], "side": c["side"],
            "line": "" if c.get("line") is None else c["line"],
            "book": c["book"], "price": c["price_american"],
            "fair_prob": c["fair_prob"], "fair_american": c["fair_american"],
            "edge_pred": c["edge"], "confidence": c["confidence"],
            "units_risked": c["unit_size"],
            "status": "pending", "notes": c.get("risk", "Shadow"),
        })
        seen.add(key)
        added += 1
    if added:
        _write_log(rows, SHADOW_LOG)
    print(f"[tracker] paper-logged {added} shadow bet(s) for {target_date}")
    return added


# ---------------------------------------------------------------------------
# Settlement via ESPN finals
# ---------------------------------------------------------------------------

def _finals_for_date(d: str) -> dict[str, tuple[int, int]]:
    """event_id -> (home_score, away_score) for completed games on ET date d."""
    data = _get(f"{ESPN_API}/scoreboard", params={
        "dates": d.replace("-", ""), "limit": 500, "groups": 50})
    out: dict[str, tuple[int, int]] = {}
    for ev in data.get("events", []):
        st = ((ev.get("status") or {}).get("type") or {})
        if not st.get("completed"):
            continue
        comp = (ev.get("competitions") or [{}])[0]
        hs = as_ = None
        for c in comp.get("competitors", []):
            if c.get("homeAway") == "home":
                hs = _f(c.get("score"))
            elif c.get("homeAway") == "away":
                as_ = _f(c.get("score"))
        if hs is not None and as_ is not None:
            out[str(ev.get("id"))] = (int(hs), int(as_))
    return out


def _settle_one(row: dict, final_home: int, final_away: int) -> tuple[str, float, str]:
    """Returns (status, units_pl, actual_result)."""
    units = _f(row.get("units_risked"), 0.0) or 0.0
    price = int(_f(row.get("price"), 0) or 0)
    market, side = row.get("market"), row.get("side")
    line = _f(row.get("line"))
    margin_home = final_home - final_away
    total = final_home + final_away

    if market == "moneyline":
        won = margin_home > 0 if side == "home" else margin_home < 0
        if margin_home == 0:   # cannot happen in hoops, but be safe
            return "push", 0.0, "tie"
        result = f"{'home' if margin_home > 0 else 'away'} won by {abs(margin_home)}"
    elif market == "spread":
        if line is None:
            return "error", 0.0, "missing line"
        cover = (margin_home + line) if side == "home" else (-margin_home + line)
        if cover == 0:
            return "push", 0.0, f"push at {line:+g}"
        won = cover > 0
        result = f"margin {margin_home:+d} vs {line:+g} ({side})"
    elif market == "total":
        if line is None:
            return "error", 0.0, "missing line"
        if total == line:
            return "push", 0.0, f"push at {line:g}"
        won = total > line if side == "over" else total < line
        result = f"total {total} vs {line:g} ({side})"
    else:
        return "error", 0.0, f"unknown market {market}"

    if won:
        return "win", round(_payout_units(price, units), 4), result
    return "loss", round(-units, 4), result


def settle_pending(log: Path = BET_LOG, label: str = "real") -> int:
    rows = _read_log(log)
    pending = [r for r in rows if r.get("status") == "pending"]
    if not pending:
        print(f"[tracker] nothing pending ({label})")
        return 0
    finals_by_date: dict[str, dict] = {}
    settled = 0
    for r in pending:
        d = r.get("date") or _et_date(r.get("tipoff") or "")
        if d not in finals_by_date:
            try:
                finals_by_date[d] = _finals_for_date(d)
            except Exception as e:
                print(f"[tracker] finals fetch failed for {d}: {e}")
                finals_by_date[d] = {}
        fin = finals_by_date[d].get(str(r.get("espn_event_id")))
        if not fin:
            continue
        status, pl, result = _settle_one(r, fin[0], fin[1])
        r.update({
            "status": status, "units_pl": pl,
            "final_home": fin[0], "final_away": fin[1],
            "settled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "actual_result": result,
        })
        # CLV: vig-to-vig at the same market/side (fixes mlb-sharp open item D)
        cp = _f(r.get("closing_price"))
        if cp is not None and r.get("price"):
            bet_p, close_p = int(_f(r["price"])), int(cp)
            def _ip(p):
                return (-p) / ((-p) + 100) if p < 0 else 100 / (p + 100)
            clv = (_ip(close_p) - _ip(bet_p))
            r["clv_pct"] = round(clv * 100, 3)
            r["beat_close"] = "yes" if clv > 0 else ("no" if clv < 0 else "even")
        settled += 1
    if settled:
        _write_log(rows, log)
    print(f"[tracker] settled {settled} {label} bet(s)")
    return settled


# ---------------------------------------------------------------------------
# record.md
# ---------------------------------------------------------------------------

def build_record() -> str:
    rows = _read_log()
    settled = [r for r in rows if r.get("status") in ("win", "loss", "push")]
    pending = [r for r in rows if r.get("status") == "pending"]
    w = sum(1 for r in settled if r["status"] == "win")
    l = sum(1 for r in settled if r["status"] == "loss")
    p = sum(1 for r in settled if r["status"] == "push")
    pl = sum(_f(r.get("units_pl"), 0.0) or 0.0 for r in settled)
    risked = sum(_f(r.get("units_risked"), 0.0) or 0.0 for r in settled)
    clvs = [_f(r.get("clv_pct")) for r in settled if _f(r.get("clv_pct")) is not None]
    lines = [
        "# NCAAW Sharp — Record", "",
        f"_Updated {datetime.now(timezone.utc).isoformat()[:19]}Z_", "",
        f"**{w}-{l}-{p}** · **{pl:+.2f}u** on {risked:g}u risked "
        f"({(pl / risked * 100) if risked else 0:+.1f}% ROI) · {len(pending)} pending", "",
    ]
    if clvs:
        beat = sum(1 for c in clvs if c > 0)
        lines += [f"CLV: avg {sum(clvs) / len(clvs):+.2f}pp · beat close "
                  f"{beat}/{len(clvs)} ({beat / len(clvs) * 100:.0f}%)", ""]
    # per-market table
    lines += ["| market | W-L-P | units |", "|---|---|---|"]
    for mkt in ("moneyline", "spread", "total"):
        ms = [r for r in settled if r.get("market") == mkt]
        if not ms:
            continue
        mw = sum(1 for r in ms if r["status"] == "win")
        ml_ = sum(1 for r in ms if r["status"] == "loss")
        mp = sum(1 for r in ms if r["status"] == "push")
        mpl = sum(_f(r.get("units_pl"), 0.0) or 0.0 for r in ms)
        lines.append(f"| {mkt} | {mw}-{ml_}-{mp} | {mpl:+.2f}u |")
    # shadow (paper) section — evidence tier, never real money
    srows = _read_log(SHADOW_LOG)
    ssettled = [r for r in srows if r.get("status") in ("win", "loss", "push")]
    if srows:
        sw = sum(1 for r in ssettled if r["status"] == "win")
        sl = sum(1 for r in ssettled if r["status"] == "loss")
        sp = sum(1 for r in ssettled if r["status"] == "push")
        spl = sum(_f(r.get("units_pl"), 0.0) or 0.0 for r in ssettled)
        lines += ["", f"## Shadow (paper) tier — half-gate evidence, 0.5u flat",
                  "", f"**{sw}-{sl}-{sp}** · {spl:+.2f}u paper · "
                  f"{sum(1 for r in srows if r.get('status') == 'pending')} pending", ""]
        lines += ["| market | W-L-P | paper units |", "|---|---|---|"]
        for mkt in ("moneyline", "spread", "total"):
            ms = [r for r in ssettled if r.get("market") == mkt]
            if not ms:
                continue
            mw = sum(1 for r in ms if r["status"] == "win")
            ml_ = sum(1 for r in ms if r["status"] == "loss")
            mp = sum(1 for r in ms if r["status"] == "push")
            mpl = sum(_f(r.get("units_pl"), 0.0) or 0.0 for r in ms)
            lines.append(f"| {mkt} | {mw}-{ml_}-{mp} | {mpl:+.2f}u |")
    lines += ["", "_Validation is Brier/log-loss on all graded games + CLV, "
              "never small-sample ROI._", ""]
    md = "\n".join(lines)
    (DATA_ROOT / "record.md").write_text(md)
    print(f"[tracker] record: {w}-{l}-{p} {pl:+.2f}u, {len(pending)} pending")
    return md


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--append", metavar="DATE")
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()
    if args.append:
        append_pending(args.append)
        append_shadow(args.append)
    if args.settle:
        settle_pending(BET_LOG, "real")
        settle_pending(SHADOW_LOG, "shadow")
    if args.record or args.settle or args.append:
        build_record()
    if not (args.append or args.settle or args.record):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
