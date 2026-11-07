"""NCAAW slate scraper — Barttorvik ratings + ESPN schedule + The Odds API.

Daily pipeline step 1 (mirrors mlb-sharp's mlb_data_scraper.py):
    python ncaaw_data_scraper.py --date 2026-11-04

Writes ncaaw_data/<date>/:
    teams.json   — Torvik NCAAW adjusted efficiencies + four factors (live)
    slate.json   — ESPN D-I games for the ET date (incl. neutral-site flag)
    odds.json    — multi-book ML/spread/total odds (basketball_wncaab)

Odds API quota is the #1 operational concern (inherited from mlb-sharp):
key rotation via ODDS_API_KEY, ODDS_API_KEY_2.._8; a proactive Discord
warning fires when the LAST key runs low. NCAAW burn is tiny — one odds
call per run covers the whole slate (~30/month).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from ncaaw_model import canon_name, canon_match

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ESPN_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball"
ODDS_API = "https://api.the-odds-api.com/v4"
ODDS_SPORT = "basketball_wncaab"
TORVIK_URL = "https://barttorvik.com/ncaaw/trank.php"

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# Bookmaker policy — carried over from mlb-sharp (FanDuel stays excluded:
# its marketing-driven trap lines won "best price" at -27.7% ROI there).
# Widened 2026-11-05 for NCAAW line-shopping width: betrivers (verified live
# for wncaab), espnbet (theScore Bet's US successor — theScore itself is not
# carried by The Odds API), ballybet + hardrockbet (soft books, free width).
# williamhill_us is Caesars' canonical API key (kept alongside "caesars").
# NOTE: stay ≤10 total books — The Odds API bills per 10-book block.
TARGET_BOOKS = ("draftkings", "betmgm", "caesars", "williamhill_us",
                "betrivers", "espnbet", "ballybet", "hardrockbet")
SHARP_ANCHORS = ("pinnacle",)
ALL_BOOKS = TARGET_BOOKS + SHARP_ANCHORS
FULL_MARKETS = ("h2h", "spreads", "totals")

DATA_ROOT = Path(__file__).resolve().parent / "ncaaw_data"

# ESPN women's basketball conference group ids -> CONF_STRENGTH keys
# (fetched from sports.core.api.espn.com groups/50/children, season 2027)
ESPN_CONF = {
    "1": "AE", "2": "ACC", "3": "A10", "4": "Big East", "5": "Big Sky",
    "6": "Big South", "7": "Big Ten", "8": "Big 12", "9": "Big West",
    "10": "CAA", "11": "CUSA", "12": "Ivy", "13": "MAAC", "14": "MAC",
    "16": "MEAC", "18": "MVC", "19": "NEC", "20": "OVC", "21": "Pac-12",
    "22": "Patriot", "23": "SEC", "24": "SC", "25": "Southland",
    "26": "SWAC", "27": "Sun Belt", "29": "WCC", "30": "WAC",
    "44": "MWC", "45": "Horz", "46": "ASUN", "47": "Summit", "62": "Amer",
}

_ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Helpers (ported from mlb_data_scraper.py)
# ---------------------------------------------------------------------------

def _redact(text: str) -> str:
    """Strip secrets (API keys) out of any string before it is logged/persisted."""
    return re.sub(r"(apiKey=)[^&\s]+", r"\1***", str(text))


def _et_date(iso: str) -> str:
    """Calendar date (YYYY-MM-DD) of a UTC ISO timestamp in US/Eastern —
    night games after 8pm ET roll past 00:00Z and must not file a day late."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_ET).date().isoformat()
    except (ValueError, AttributeError, TypeError):
        return (iso or "")[:10]


_LAST_QUOTA_HDR: dict = {}
_ODDS_QUOTA_BY_KEY: dict = {}   # key_tail -> {"remaining": int, "used": int}


def _get(url: str, params: dict | None = None, retries: int = 3, timeout: int = 30) -> Any:
    """GET JSON with retry. 4xx raises immediately (quota/bad market won't
    change on retry); 5xx/network back off and retry."""
    headers = {"User-Agent": USER_AGENT}
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            r.raise_for_status()
            _rem = r.headers.get("x-requests-remaining")
            if _rem is not None:   # Odds API responses only
                _LAST_QUOTA_HDR["remaining"] = _rem
                _LAST_QUOTA_HDR["used"] = r.headers.get("x-requests-used")
            return r.json()
        except requests.exceptions.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status is not None and 400 <= status < 500:
                raise
            if i == retries - 1:
                raise
            time.sleep(1.5 ** i)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 ** i)
    return {}


# ---------------------------------------------------------------------------
# Odds API key rotation / failover + proactive quota alert (from mlb-sharp)
# ---------------------------------------------------------------------------
_ODDS_DEAD_KEYS: set[str] = set()


def _odds_keys() -> list[str]:
    """Ordered Odds API keys: ODDS_API_KEY (comma-separable) then _2.._8."""
    keys: list[str] = []
    for k in (os.environ.get("ODDS_API_KEY") or "").split(","):
        k = k.strip()
        if k and k not in keys:
            keys.append(k)
    for i in range(2, 9):
        k = (os.environ.get(f"ODDS_API_KEY_{i}") or "").strip()
        if k and k not in keys:
            keys.append(k)
    return keys


def _get_odds_json(url: str, params: dict, keys: list[str] | None = None) -> Any:
    """GET against The Odds API, rotating keys on 401/429 (quota exhausted —
    those responses are NOT charged, so probing a drained primary is free)."""
    keys = keys or _odds_keys()
    if not keys:
        raise RuntimeError("no Odds API key configured (set ODDS_API_KEY)")
    live = [k for k in keys if k not in _ODDS_DEAD_KEYS] or keys
    last_exc: Exception | None = None
    for idx, key in enumerate(live):
        try:
            data = _get(url, params={**params, "apiKey": key}, retries=2)
            rem = _LAST_QUOTA_HDR.get("remaining")
            if rem is not None:
                try:
                    _ODDS_QUOTA_BY_KEY[key[-4:]] = {
                        "remaining": int(rem),
                        "used": int(_LAST_QUOTA_HDR.get("used") or 0),
                    }
                except (ValueError, TypeError):
                    pass
            return data
        except requests.exceptions.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status in (401, 429):
                _ODDS_DEAD_KEYS.add(key)
                last_exc = e
                if idx < len(live) - 1:
                    continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("no Odds API key configured")


ODDS_QUOTA_ALERT_THRESHOLD = int(os.environ.get("ODDS_QUOTA_ALERT_THRESHOLD") or 150)


def check_and_alert_odds_quota(threshold: int = ODDS_QUOTA_ALERT_THRESHOLD) -> dict | None:
    """Discord heads-up when the LAST configured key is low — the real
    'about to go dark' signal. Zero extra requests (reads cached headers)."""
    keys = _odds_keys()
    if not keys or not _ODDS_QUOTA_BY_KEY:
        return None
    pos_by_tail = {k[-4:]: i + 1 for i, k in enumerate(keys)}
    observed = sorted(
        (pos_by_tail.get(tail, 0), tail, rec.get("remaining"))
        for tail, rec in _ODDS_QUOTA_BY_KEY.items() if tail in pos_by_tail
    )
    if not observed:
        return None
    active_pos, active_tail, active_rem = observed[-1]
    n = len(keys)
    is_last_key = (active_pos == n)
    state = {"active_position": active_pos, "n_keys": n,
             "active_tail": active_tail, "remaining": active_rem,
             "is_last_key": is_last_key}
    if is_last_key and active_rem is not None and active_rem < threshold:
        url = os.environ.get("DISCORD_WEBHOOK_URL")
        if url:
            msg = (f"⚠️ **Odds API quota low (NCAAW)** — your LAST key (#{active_pos} of {n}, "
                   f"…{active_tail}) has only **{active_rem}** requests left this month.\n"
                   f"Add another key (`ODDS_API_KEY_{n+1}`) before the slate goes dark. "
                   f"Heads-up, not an outage — bets are still firing.")
            try:
                requests.post(url, json={"content": msg}, timeout=15)
                print(f"[quota] LOW: last key at {active_rem} — alerted Discord")
            except Exception as e:
                print(f"[quota] discord alert failed: {e}")
    else:
        print(f"[quota] active key #{active_pos}/{n} (…{active_tail}): "
              f"{active_rem} left" + ("" if is_last_key else " — backup key(s) in reserve"))
    return state


def _save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=False, default=str))


# ---------------------------------------------------------------------------
# Barttorvik NCAAW ratings
# ---------------------------------------------------------------------------
# barttorvik.com fronts a trivial JS check: POST js_test_submitted=1 once to
# obtain a js_verified cookie, then JSON endpoints respond normally.
#
# trank.php?json=1 column map (verified against the 2026 season vs the v11 app
# database — adjDE/ftrD/drb matched to the decimal):
#   0 name · 1 adjOE · 2 adjDE · 3 barthag · 4 record · 5 wins · 6 games
#   7 eFG · 8 eFGD · 9 ftr · 10 ftrD · 11 tor · 12 torD · 13 orb · 14 drb
#   15 adjTempo · 30 year · 34 WAB

def torvik_season_for(d: date) -> int:
    """Torvik 'year' is the season END year: Nov 2026 games -> 2027."""
    return d.year + 1 if d.month >= 7 else d.year


def fetch_torvik(year: int) -> dict[str, dict]:
    """Live Torvik NCAAW table keyed by canonical team name."""
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    url = f"{TORVIK_URL}?json=1&year={year}"
    rows = None
    for attempt in range(3):
        r = s.get(url, timeout=30)
        if "js_test" in r.text[:500] or "Verifying" in r.text[:500]:
            s.post(url, data={"js_test_submitted": "1"}, timeout=30,
                   allow_redirects=True)
            r = s.get(url, timeout=30)
        try:
            rows = r.json()
            break
        except ValueError:
            print(f"[torvik] attempt {attempt + 1}: non-JSON response "
                  f"(HTTP {r.status_code}): {r.text[:120]!r}")
            time.sleep(2 * (attempt + 1))
    if rows is None:
        raise RuntimeError(f"Torvik unreachable (last HTTP {r.status_code}) — "
                           "likely blocking this IP")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Torvik returned no data")
    # tRank = barthag rank (1 = best), matching the app's tRank column
    ranked = sorted(rows, key=lambda x: -float(x[3]))
    rank_of = {row[0]: i + 1 for i, row in enumerate(ranked)}
    teams: dict[str, dict] = {}
    for row in rows:
        name = row[0]
        teams[canon_name(name)] = {
            "torvik_name": name,
            "tRank": rank_of[name],
            "barthag": float(row[3]),
            "record": str(row[4]),
            "games": int(row[6]),
            "adjOE": float(row[1]), "adjDE": float(row[2]),
            "eFG": float(row[7]), "eFGD": float(row[8]),
            "ftr": float(row[9]), "ftrD": float(row[10]),
            "tor": float(row[11]), "torD": float(row[12]),
            "orb": float(row[13]), "drb": float(row[14]),
            "tempo": float(row[15]),
        }
    return teams


# ---------------------------------------------------------------------------
# ESPN slate
# ---------------------------------------------------------------------------

def fetch_slate(target: date) -> list[dict]:
    """All D-I games for the ET date. groups=50 = Division I."""
    data = _get(f"{ESPN_API}/scoreboard", params={
        "dates": target.strftime("%Y%m%d"), "limit": 500, "groups": 50,
    })
    games = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        sides: dict[str, dict] = {}
        for c in comp.get("competitors", []):
            team = c.get("team") or {}
            recs = c.get("records") or []
            sides[c.get("homeAway")] = {
                "espn_id": team.get("id"),
                "location": team.get("location"),
                "display_name": team.get("displayName"),
                "canon": canon_name(team.get("location") or team.get("displayName") or ""),
                "conf": ESPN_CONF.get(str(team.get("conferenceId") or ""), ""),
                "record": next((r.get("summary") for r in recs
                                if r.get("type") in (None, "total")), None)
                          or (recs[0].get("summary") if recs else None),
                "score": c.get("score"),
                "winner": c.get("winner"),
            }
        if "home" not in sides or "away" not in sides:
            continue
        status = ((ev.get("status") or {}).get("type") or {})
        games.append({
            "espn_event_id": ev.get("id"),
            "start": ev.get("date"),                      # UTC ISO
            "et_date": _et_date(ev.get("date") or ""),
            "name": ev.get("name"),
            "neutral_site": bool(comp.get("neutralSite")),
            "conference_game": bool(comp.get("conferenceCompetition")),
            "status": status.get("name"),
            "completed": bool(status.get("completed")),
            "home": sides["home"],
            "away": sides["away"],
        })
    return games


# ---------------------------------------------------------------------------
# Odds
# ---------------------------------------------------------------------------

def fetch_odds(api_key: str | None = None,
               markets: tuple[str, ...] = FULL_MARKETS,
               books: tuple[str, ...] = ALL_BOOKS) -> dict:
    """Multi-book NCAAW odds — ONE request covers the whole slate."""
    keys = [api_key] if api_key else _odds_keys()
    if not keys:
        return {"available": False, "reason": "ODDS_API_KEY not set", "games": []}
    payload: dict[str, Any] = {
        "available": True,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "books_used": list(books),
        "markets_requested": list(markets),
        "games": [],
        "errors": [],
    }
    try:
        data = _get_odds_json(f"{ODDS_API}/sports/{ODDS_SPORT}/odds", {
            "regions": "us,us2,eu",
            "markets": ",".join(markets),
            "oddsFormat": "american",
            "bookmakers": ",".join(books),
        }, keys=keys)
        payload["games"] = data or []
    except Exception as e:
        payload["available"] = False
        payload["errors"].append(_redact(f"odds call failed: {e}"))
    return payload


def match_odds_to_game(game: dict, odds_games: list[dict]) -> dict | None:
    """Attach the Odds API event to an ESPN game by canonical team names +
    same ET date. Odds API names include mascots ('UConn Huskies') which
    canon_name strips."""
    hn = game["home"]["location"] or game["home"]["display_name"] or ""
    an = game["away"]["location"] or game["away"]["display_name"] or ""
    et = game["et_date"]
    for og in odds_games:
        if _et_date(og.get("commence_time") or "") != et:
            continue
        oh, oa = og.get("home_team") or "", og.get("away_team") or ""
        if canon_match(oh, hn) and canon_match(oa, an):
            return {**og, "_flipped": False}
        if canon_match(oh, an) and canon_match(oa, hn):
            return {**og, "_flipped": True}
    return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _latest_cached_teams(data_root: Path, season: int) -> dict | None:
    """Most recent committed Torvik snapshot for this season — fallback when
    the live fetch is blocked (barttorvik 403s datacenter/GitHub-runner IPs).
    Checks the top-level latest_teams.json first (refreshed by ANY successful
    live fetch, e.g. a local run on the Mac), then per-day snapshots. Ratings
    move slowly — a 1-day-old snapshot with a warning beats a dead slate."""
    candidates = [data_root / "latest_teams.json"]
    candidates += sorted((p / "teams.json" for p in data_root.iterdir()
                          if p.is_dir() and (p / "teams.json").exists()),
                         key=lambda p: p.parent.name, reverse=True)
    for path in candidates:
        try:
            cached = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if cached.get("season") == season and cached.get("teams"):
            cached["stale_from"] = (cached.get("fetched_at") or "")[:10] or path.parent.name
            return cached
    return None


def run(target: date, data_root: Path = DATA_ROOT) -> dict:
    out = data_root / target.isoformat()
    out.mkdir(parents=True, exist_ok=True)

    season = torvik_season_for(target)
    print(f"[torvik] fetching NCAAW season {season} …")
    try:
        if os.environ.get("TORVIK_SKIP_LIVE"):
            # CI runners are always 403-blocked — skip the doomed attempt
            # (saves ~10s + retry noise per run) and go straight to snapshot
            raise RuntimeError("TORVIK_SKIP_LIVE set (runner IPs are blocked)")
        teams = fetch_torvik(season)
        teams_doc = {
            "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "season": season,
            "teams": teams,
        }
        print(f"[torvik] {len(teams)} teams (live)")
        # Refresh the shared snapshot so CI (whose IPs Torvik blocks) can
        # always fall back to the newest successful fetch.
        _save_json(data_root / "latest_teams.json", teams_doc)
    except Exception as e:
        cached = _latest_cached_teams(data_root, season)
        if not cached:
            raise RuntimeError(f"Torvik fetch failed and no cached snapshot "
                               f"exists: {e}") from e
        teams = cached["teams"]
        teams_doc = cached
        print(f"[torvik] live fetch unavailable ({e}) — using cached "
              f"snapshot from {cached['stale_from']} ({len(teams)} teams)")
        # Discord warning ONLY when the snapshot is actually going stale
        # (>2 days old) — the fallback itself is normal on CI and warning
        # every hourly sweep was pure spam.
        try:
            stale_days = (target - date.fromisoformat(
                cached["stale_from"][:10])).days
        except (ValueError, TypeError):
            stale_days = 99
        hook = os.environ.get("DISCORD_WEBHOOK_URL")
        if hook and stale_days > 2:
            try:
                requests.post(hook, json={"content":
                    f"⚠️ **NCAAW ratings snapshot is {stale_days} days old** — "
                    f"double-click `refresh_teams.command` on the Mac to push "
                    f"fresh Torvik ratings (CI can't fetch them itself)."},
                    timeout=15)
            except Exception:
                pass
    _save_json(out / "teams.json", teams_doc)

    print(f"[espn] fetching slate for {target} …")
    slate = fetch_slate(target)
    print(f"[espn] {len(slate)} D-I games")

    print("[odds] fetching board …")
    odds = fetch_odds()
    n_books_games = len(odds.get("games") or [])
    print(f"[odds] available={odds.get('available')} events={n_books_games}")
    _save_json(out / "odds.json", odds)
    check_and_alert_odds_quota()

    # Attach odds + torvik coverage flags to each slate game
    unmatched: list[str] = []
    for g in slate:
        g["torvik_home"] = g["home"]["canon"] in teams
        g["torvik_away"] = g["away"]["canon"] in teams
        for side in ("home", "away"):
            if g[f"torvik_{side}"] is False:
                unmatched.append(f'{g[side]["display_name"]} -> {g[side]["canon"]}')
        m = match_odds_to_game(g, odds.get("games") or [])
        g["odds_event_id"] = m.get("id") if m else None
    _save_json(out / "slate.json", {
        "date": target.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "games": slate,
    })
    if unmatched:
        _save_json(out / "unmatched_teams.json", sorted(set(unmatched)))
        print(f"[warn] {len(set(unmatched))} teams missing from Torvik match "
              f"(see unmatched_teams.json) — usually non-D-I opponents")
    return {"teams": len(teams), "games": len(slate), "odds": odds.get("available")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today ET)")
    args = ap.parse_args()
    target = (date.fromisoformat(args.date) if args.date
              else datetime.now(_ET).date())
    summary = run(target)
    print(f"[done] {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
