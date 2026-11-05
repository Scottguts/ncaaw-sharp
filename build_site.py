"""Static dashboard — docs/ for GitHub Pages (or open docs/index.html locally).

index.html IS the live board for the most recent slate: fired best bets up
top (with settled results), then every game's predicted score / spread /
total next to the market lines, with the pass reason when no bet fired.
Auto-refreshes every 5 minutes; daily-bets and line-watch rebuild + push it
whenever lines drop, so this is the "app" view of the system.

Deterministic by construction (sorted globs, sort_keys) — zero-diff rebuilds.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from ncaaw_data_scraper import DATA_ROOT

DOCS = Path(__file__).resolve().parent / "docs"

STYLE = """
:root{--bg:#0f1419;--panel:#161b22;--line:#30363d;--fg:#e6e6e6;--mut:#8b949e;
--green:#3fb950;--red:#f85149;--gold:#d29922;--blue:#58a6ff}
body{font-family:-apple-system,Segoe UI,sans-serif;max-width:980px;margin:1.2rem auto;
padding:0 .8rem;background:var(--bg);color:var(--fg);line-height:1.45}
a{color:var(--blue);text-decoration:none} a:hover{text-decoration:underline}
h1{margin:.2rem 0 .4rem;font-size:1.5rem} h2{font-size:1.1rem;border-bottom:1px solid var(--line);padding-bottom:.25rem}
table{border-collapse:collapse;width:100%;font-size:.92rem}
td,th{border:1px solid var(--line);padding:.3rem .5rem;text-align:left;vertical-align:top}
th{background:var(--panel);position:sticky;top:0}
.card{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--gold);
border-radius:8px;padding:.7rem .9rem;margin:.6rem 0}
.card.win{border-left-color:var(--green)} .card.loss{border-left-color:var(--red)}
.win{color:var(--green)}.loss{color:var(--red)}.push,.gold{color:var(--gold)}
.muted{color:var(--mut);font-size:.88rem} .big{font-size:1.05rem;font-weight:600}
.pill{display:inline-block;padding:0 .5rem;border-radius:99px;border:1px solid var(--line);
font-size:.8rem;margin-left:.4rem}
pre{white-space:pre-wrap;background:var(--panel);padding:.8rem;border-radius:8px;font-size:.88rem}
@media(max-width:640px){td,th{padding:.25rem .35rem;font-size:.82rem}}
"""


def _page(title: str, body: str, refresh: bool = False) -> str:
    meta = "<meta http-equiv='refresh' content='300'>" if refresh else ""
    return (f"<!doctype html><html><head><meta charset='utf-8'>{meta}"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
            f"<body>{body}</body></html>")


def _price(p) -> str:
    try:
        p = int(p)
    except (TypeError, ValueError):
        return "—"
    return f"+{p}" if p > 0 else str(p)


def _bet_status_index() -> dict:
    """(event_id, market, side) -> bet row, from bet_log.csv."""
    import csv
    log = DATA_ROOT / "bet_log.csv"
    if not log.exists():
        return {}
    with log.open(newline="") as f:
        return {(str(r["espn_event_id"]), r["market"], r["side"]): r
                for r in csv.DictReader(f)}


def _cards_section(grades: dict, bets: dict) -> list[str]:
    cards = grades.get("cards") or []
    out = []
    if not grades.get("odds_available"):
        out.append("<div class='card loss'><b>DATA OUTAGE</b> — odds unavailable "
                   "on the last run. Not a disciplined no-play.</div>")
    if not cards:
        out.append("<div class='card'><b>NO PLAYS (yet)</b> — nothing has cleared "
                   "the gates. Line-watch re-checks as books post lines.</div>")
        return out
    for c in cards:
        bet = bets.get((str(c["espn_event_id"]), c["market"], c["side"])) or {}
        status = bet.get("status", "pending")
        badge = {"win": "✅ WIN", "loss": "❌ LOSS", "push": "➖ PUSH"}.get(
            status, "🕐 PENDING")
        pl = bet.get("units_pl")
        pl_txt = f" ({float(pl):+.2f}u)" if pl not in (None, "") and status != "pending" else ""
        out.append(
            f"<div class='card {status}'><span class='big'>{html.escape(c['bet_label'])}"
            f"</span> {_price(c['price_american'])} ({html.escape(c['book'])})"
            f"<span class='pill {status}'>{badge}{pl_txt}</span><br>"
            f"{html.escape(c['matchup'])}<br>"
            f"<span class='muted'>fair {c['fair_prob']:.1%} ({_price(c['fair_american'])}) · "
            f"edge {c['edge']:.1%} · conf {c['confidence']}/10 · "
            f"{c['unit_size']}u {html.escape(c['risk'])}</span></div>")
    return out


def _slate_table(grades: dict) -> list[str]:
    out = ["<table><tr><th>game</th><th>tip (ET)</th><th>model score</th>"
           "<th>model spread</th><th>model total</th><th>market</th><th>status</th></tr>"]
    carded = {(g_c["espn_event_id"]) for g_c in (grades.get("cards") or [])}
    for g in sorted(grades.get("games") or [], key=lambda x: (x.get("start") or "",
                                                              x.get("matchup") or "")):
        m = g.get("model")
        mk = g.get("market") or {}
        tip = (g.get("start") or "")[11:16]
        if m:
            score = f"{m['away_score']:.0f}–{m['home_score']:.0f}"
            spread = f"home {m['margin_home']:+.1f}"
            total = f"{m['total']:.1f}"
        else:
            score = spread = total = "—"
        mkt_bits = []
        if mk.get("spread_home") is not None:
            mkt_bits.append(f"{mk['spread_home']:+g}")
        if mk.get("total") is not None:
            mkt_bits.append(f"O/U {mk['total']:g}")
        if g["espn_event_id"] in carded:
            status = "<b class='gold'>BET FIRED</b>"
        elif not mkt_bits:
            status = "<span class='muted'>no line yet</span>"
        else:
            status = f"<span class='muted'>{html.escape((g.get('pass_reasons') or ['pass'])[0])}</span>"
        out.append(f"<tr><td>{html.escape(g.get('matchup') or '')}</td><td>{tip}</td>"
                   f"<td>{score}</td><td>{spread}</td><td>{total}</td>"
                   f"<td>{' · '.join(mkt_bits) or '—'}</td><td>{status}</td></tr>")
    out.append("</table>")
    return out


def build() -> int:
    DOCS.mkdir(exist_ok=True)
    days = sorted(
        (p for p in DATA_ROOT.iterdir()
         if p.is_dir() and (p / "grades.json").exists()),
        key=lambda p: p.name, reverse=True)
    bets = _bet_status_index()

    rows_html = []
    for i, day in enumerate(days):
        grades = json.loads((day / "grades.json").read_text())
        body = ["<p><a href='index.html'>← today</a> · <a href='record.html'>record</a></p>",
                f"<h1>🏀 NCAAW — {day.name}</h1>",
                f"<p class='muted'>updated {html.escape(grades.get('generated_at') or '')} · "
                f"{grades.get('n_games', 0)} games · auto-refreshes</p>",
                "<h2>Best bets</h2>", *_cards_section(grades, bets),
                "<h2>Full board — model vs market</h2>", *_slate_table(grades)]
        page = _page(f"NCAAW {day.name}", "".join(body), refresh=(i == 0))
        (DOCS / f"{day.name}.html").write_text(page)
        if i == 0:
            (DOCS / "index.html").write_text(page.replace(
                "<p><a href='index.html'>← today</a>",
                "<p><a href='archive.html'>archive</a>"))
        (DOCS / f"{day.name}.json").write_text(json.dumps(grades, indent=2,
                                                          sort_keys=True, default=str))
        n_cards = len(grades.get("cards") or [])
        rows_html.append(
            f"<tr><td><a href='{day.name}.html'>{day.name}</a></td>"
            f"<td>{grades.get('n_games', 0)}</td><td>{n_cards}</td>"
            f"<td>{grades.get('total_units', 0):g}u</td></tr>")

    record_md = DATA_ROOT / "record.md"
    rec_html = (f"<pre>{html.escape(record_md.read_text())}</pre>"
                if record_md.exists() else "<p class='muted'>no record yet</p>")
    (DOCS / "record.html").write_text(_page("Record", (
        "<p><a href='index.html'>← today</a></p><h1>Record</h1>" + rec_html)))
    (DOCS / "archive.html").write_text(_page("Archive", (
        "<p><a href='index.html'>← today</a> · <a href='record.html'>record</a></p>"
        "<h1>Slate archive</h1>" + rec_html +
        "<table><tr><th>date</th><th>games</th><th>cards</th><th>units</th></tr>"
        + "".join(rows_html) + "</table>")))
    print(f"[site] built {len(days)} slate page(s) + dashboard")
    return 0


if __name__ == "__main__":
    sys.exit(build())
