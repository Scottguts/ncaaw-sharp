"""Autonomous live signals — scan in-play lines on the board the hourly
sweep already fetched (zero extra Odds-API quota) and push Discord
advisories when a variance-adjusted edge clears the live band.

ADVISORY ONLY: never touches bet_log.csv. Signals are deduped per game/
market/side in ncaaw_data/live_signals.json. Cadence = the sweep cadence
(hourly), so a signal can be minutes stale — the alert says to re-check the
current number in the app before acting.

Math mirrors the Live tab: banked points stand; the remaining-game rate
blends pregame expectation with LUCK-ADJUSTED observed form (shooting luck
vs 3P 31% / 2P 46% / FT 70% baselines) weighted by minutes played. Hot
shooting never projects forward.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from ncaaw_data_scraper import DATA_ROOT, ESPN_API, TARGET_BOOKS, _get
from ncaaw_model import canon_match, norm_cdf
from ncaaw_grader import edge_pct, _markets_for_book, _team_outcome

MIN_EDGE_LIVE = 0.035
MAX_EDGE_LIVE = 0.12
MIN_FRACTION = 0.15      # skip the final stretch (projection unreliable)
MAX_FRACTION = 0.85      # skip the opening minutes (nothing learned yet)
SIGNALS_LOG = DATA_ROOT / "live_signals.json"


def _minutes_left(status: dict) -> float:
    period = int(status.get("period") or 0)
    clock = str(status.get("displayClock") or "0:00")
    try:
        mins, secs = clock.replace(".", ":").split(":")[:2]
        cm = int(mins) + int(secs) / 60.0
    except (ValueError, AttributeError):
        cm = 0.0
    if period <= 0:
        return 40.0
    if period <= 4:
        return (4 - period) * 10.0 + cm
    return cm


def _ma(s: str) -> tuple[int, int]:
    try:
        a, b = str(s or "0-0").split("-")[:2]
        return int(a), int(b)
    except ValueError:
        return 0, 0


def _luck(event_id: str) -> dict:
    """{'home': pts_above_sustainable, 'away': ...} from the live box."""
    try:
        d = _get(f"{ESPN_API}/summary", params={"event": event_id})
    except Exception:
        return {}
    out = {}
    for t in (d.get("boxscore") or {}).get("teams") or []:
        st = {s.get("name"): s.get("displayValue") for s in t.get("statistics") or []}
        fgm, fga = _ma(st.get("fieldGoalsMade-fieldGoalsAttempted"))
        tm, ta = _ma(st.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
        ftm, fta = _ma(st.get("freeThrowsMade-freeThrowsAttempted"))
        two_m, two_a = fgm - tm, fga - ta
        l3 = (tm - 0.31 * ta) * 3
        l2 = (two_m - 0.46 * two_a) * 2
        lft = ftm - 0.70 * fta
        out[t.get("homeAway")] = {
            "luck": l3 + l2 + lft, "l3": l3, "l2": l2, "lft": lft,
            "shots": f"3P {tm}/{ta}, 2P {two_m}/{two_a}, FT {ftm}/{fta}",
        }
    return out


def _load_signals() -> dict:
    try:
        return json.loads(SIGNALS_LOG.read_text())
    except (OSError, ValueError):
        return {}


def scan(target_date: str) -> int:
    """Scan today's odds.json for live events, project, alert. Returns the
    number of NEW signals pushed."""
    day = DATA_ROOT / target_date
    try:
        odds = json.loads((day / "odds.json").read_text())
        grades = json.loads((day / "grades.json").read_text())
    except (OSError, ValueError):
        return 0
    models = {str(g["espn_event_id"]): g for g in grades.get("games") or []
              if g.get("model")}
    if not models:
        return 0

    # live state for today's games (one free ESPN call)
    live = {}
    data = _get(f"{ESPN_API}/scoreboard", params={
        "dates": target_date.replace("-", ""), "limit": 500, "groups": 50})
    for ev in data.get("events", []):
        st = ev.get("status") or {}
        if ((st.get("type") or {}).get("state")) != "in":
            continue
        comp = ev["competitions"][0]
        s = {c["homeAway"]: c for c in comp.get("competitors", [])}
        live[str(ev["id"])] = {
            "hs": int(float(s["home"].get("score") or 0)),
            "as": int(float(s["away"].get("score") or 0)),
            "hname": (s["home"].get("team") or {}).get("location") or "",
            "aname": (s["away"].get("team") or {}).get("location") or "",
            "clock": f"Q{st.get('period')} {st.get('displayClock')}",
            "min_left": _minutes_left(st),
        }
    if not live:
        print("[live] no games in progress")
        return 0

    seen = _load_signals()
    new_signals = []
    for og in odds.get("games") or []:
        # match odds event to a live ESPN game
        eid = None
        for k, lv in live.items():
            if (canon_match(og.get("home_team") or "", lv["hname"])
                    and canon_match(og.get("away_team") or "", lv["aname"])):
                eid = k
                break
        if not eid or eid not in models:
            continue
        lv = live[eid]
        g = models[eid]
        m = g["model"]
        sig = g.get("sigma") or {}
        f = max(0.0, min(1.0, lv["min_left"] / 40.0))
        if not (MIN_FRACTION <= f <= MAX_FRACTION):
            continue
        played = 40 * (1 - f)
        lk = _luck(eid)
        luck_h = (lk.get("home") or {}).get("luck", 0.0)
        luck_a = (lk.get("away") or {}).get("luck", 0.0)
        margin = lv["hs"] - lv["as"]
        total = lv["hs"] + lv["as"]
        w = played / (played + 60)
        sust_m = (margin - (luck_h - luck_a)) / played * 40
        sust_t = (total - luck_h - luck_a) / played * 40
        em = margin + f * (m["margin_home"] * (1 - w) + sust_m * w)
        et = total + f * (m["total"] * (1 - w) + sust_t * w)
        ss = (sig.get("spread") or 12.0) * math.sqrt(max(f, 0.05))
        st_ = (sig.get("total") or 16.0) * math.sqrt(max(f, 0.05))

        # live quotes from target books on this event
        cands = []
        for book in TARGET_BOOKS:
            mk = _markets_for_book(og, book)
            h = _team_outcome(mk.get("spreads") or [], lv["hname"])
            if h and h.get("point") is not None and h.get("price") is not None:
                line = float(h["point"])
                p_home = norm_cdf((em + line) / ss)
                cands.append(("spread", f"{lv['hname']} {line:+g}", p_home,
                              int(h["price"]), book))
                a = next((x for x in mk.get("spreads") or [] if x is not h), None)
                if a and a.get("price") is not None:
                    cands.append(("spread", f"{lv['aname']} {-line:+g}", 1 - p_home,
                                  int(a["price"]), book))
            over = next((x for x in mk.get("totals") or []
                         if x.get("name") == "Over"), None)
            under = next((x for x in mk.get("totals") or []
                          if x.get("name") == "Under"), None)
            if over and over.get("point") is not None and over.get("price") is not None:
                line = float(over["point"])
                p_over = norm_cdf((et - line) / st_)
                cands.append(("total", f"Over {line:g}", p_over, int(over["price"]), book))
                if under and under.get("price") is not None:
                    cands.append(("total", f"Under {line:g}", 1 - p_over,
                                  int(under["price"]), book))
        best = None
        for mkt, label, p, price, book in cands:
            e = edge_pct(p, price)
            if MIN_EDGE_LIVE <= e <= MAX_EDGE_LIVE and (best is None or e > best[5]):
                best = (mkt, label, p, price, book, e)
        if not best:
            continue
        mkt, label, p, price, book, e = best
        key = f"{eid}:{mkt}:{label.split()[0]}"
        prev = seen.get(key)
        if prev and e < prev.get("edge", 0) + 0.02:
            continue   # already alerted at similar or better edge
        seen[key] = {"edge": e, "at": datetime.now(timezone.utc).isoformat()}
        luck_note = ""
        for side_key, nm in (("home", lv["hname"]), ("away", lv["aname"])):
            l = (lk.get(side_key) or {})
            if abs(l.get("luck", 0)) > 8:
                tag = "🔥 hot" if l["luck"] > 0 else "🧊 cold"
                luck_note += (f"\n{tag}: {nm} {l['luck']:+.1f} luck pts "
                              f"(3P {l.get('l3', 0):+.1f}, 2P {l.get('l2', 0):+.1f}, "
                              f"FT {l.get('lft', 0):+.1f}) [{l.get('shots')}] — expect reversion")
        new_signals.append({
            "name": f"{lv['aname']} {lv['as']} — {lv['hs']} {lv['hname']} · {lv['clock']}",
            "label": label, "price": price, "book": book,
            "fair": p, "edge": e, "proj": f"margin {em:+.1f} / total {et:.1f}",
            "luck_note": luck_note,
        })

    if new_signals:
        hook = os.environ.get("DISCORD_WEBHOOK_URL")
        if hook:
            fields = [{
                "name": f"🎥 {s['label']} {s['price']:+d} ({s['book']}) — edge {s['edge']:.1%}",
                "value": (f"{s['name']}\nfair {s['fair']:.1%} · projected finish {s['proj']}"
                          f"{s['luck_note']}\n⚠️ hourly scan — re-check the current "
                          f"number in the app before betting"),
                "inline": False,
            } for s in new_signals[:5]]
            try:
                requests.post(hook, json={"embeds": [{
                    "title": f"🎥 LIVE SIGNAL{'S' if len(new_signals) > 1 else ''} (advisory)",
                    "url": "https://scottguts.github.io/ncaaw-sharp/#live",
                    "color": 0x9B59B6, "fields": fields,
                    "footer": {"text": "variance-adjusted · never auto-bet · separate from pregame cards"},
                }]}, timeout=20)
            except Exception as ex:
                print(f"[live] discord failed: {ex}")
        SIGNALS_LOG.write_text(json.dumps(seen, indent=2))
        print(f"[live] pushed {len(new_signals)} live signal(s)")
    else:
        print(f"[live] {len(live)} live game(s), no signal cleared the band")
    return len(new_signals)
