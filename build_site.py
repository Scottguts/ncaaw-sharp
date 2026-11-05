"""Static GitHub Pages site — docs/ index + one page per slate date.

Deterministic by construction (sorted globs, sort_keys) so repeat builds are
zero-diff — the mlb-sharp docs-churn lesson, applied from day one.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from ncaaw_data_scraper import DATA_ROOT

DOCS = Path(__file__).resolve().parent / "docs"

STYLE = """
body{font-family:-apple-system,Segoe UI,sans-serif;max-width:860px;margin:2rem auto;
padding:0 1rem;background:#0f1419;color:#e6e6e6;line-height:1.5}
a{color:#58a6ff;text-decoration:none} a:hover{text-decoration:underline}
h1,h2{border-bottom:1px solid #30363d;padding-bottom:.3rem}
table{border-collapse:collapse;width:100%} td,th{border:1px solid #30363d;
padding:.35rem .6rem;text-align:left} th{background:#161b22}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;
padding:1rem;margin:1rem 0}
.win{color:#3fb950}.loss{color:#f85149}.push{color:#d29922}
.muted{color:#8b949e;font-size:.9rem}
pre{white-space:pre-wrap;background:#161b22;padding:1rem;border-radius:8px}
"""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
            f"<body>{body}</body></html>")


def build() -> int:
    DOCS.mkdir(exist_ok=True)
    days = sorted(
        (p for p in DATA_ROOT.iterdir()
         if p.is_dir() and (p / "grades.json").exists()),
        key=lambda p: p.name, reverse=True)

    # per-date pages + snapshot JSON
    rows_html = []
    for day in days:
        grades = json.loads((day / "grades.json").read_text())
        cards = grades.get("cards") or []
        body = [f"<p><a href='index.html'>← index</a></p>",
                f"<h1>NCAAW — {day.name}</h1>"]
        if not grades.get("odds_available"):
            body.append("<div class='card loss'><b>DATA OUTAGE</b> — odds unavailable this run.</div>")
        if cards:
            for c in cards:
                price = f"+{c['price_american']}" if c['price_american'] > 0 else str(c['price_american'])
                body.append(
                    f"<div class='card'><b>{html.escape(c['bet_label'])}</b> {price} "
                    f"({html.escape(c['book'])}) — {html.escape(c['matchup'])}<br>"
                    f"<span class='muted'>fair {c['fair_prob']:.1%} · edge {c['edge']:.1%} · "
                    f"conf {c['confidence']}/10 · {c['unit_size']}u {html.escape(c['risk'])}</span></div>")
        else:
            body.append("<div class='card'>NO PLAYS — nothing cleared the gates.</div>")
        body.append("<h2>Slate</h2><table><tr><th>game</th><th>model</th><th>market</th><th>note</th></tr>")
        for g in sorted(grades.get("games") or [], key=lambda x: x.get("matchup") or ""):
            m = g.get("model")
            model = (f"{m['away_score']:.0f}-{m['home_score']:.0f} "
                     f"(tot {m['total']:.0f})") if m else "—"
            mk = g.get("market") or {}
            mkt_bits = []
            if mk.get("spread_home") is not None:
                mkt_bits.append(f"home {mk['spread_home']:+g}")
            if mk.get("total") is not None:
                mkt_bits.append(f"O/U {mk['total']:g}")
            note = (g.get("pass_reasons") or [""])[0]
            body.append(f"<tr><td>{html.escape(g.get('matchup') or '')}</td>"
                        f"<td>{model}</td><td>{' · '.join(mkt_bits) or '—'}</td>"
                        f"<td class='muted'>{html.escape(note)}</td></tr>")
        body.append("</table>")
        (DOCS / f"{day.name}.html").write_text(_page(f"NCAAW {day.name}", "".join(body)))
        (DOCS / f"{day.name}.json").write_text(json.dumps(grades, indent=2,
                                                          sort_keys=True, default=str))
        rows_html.append(
            f"<tr><td><a href='{day.name}.html'>{day.name}</a></td>"
            f"<td>{grades.get('n_games', 0)}</td><td>{len(cards)}</td>"
            f"<td>{grades.get('total_units', 0):g}u</td></tr>")

    record_md = DATA_ROOT / "record.md"
    rec_html = (f"<pre>{html.escape(record_md.read_text())}</pre>"
                if record_md.exists() else "<p class='muted'>no record yet</p>")
    idx = ["<h1>NCAAW Sharp</h1>",
           "<p class='muted'>Market-anchored NCAAW betting cards — v13 Torvik model, "
           "mlb-sharp gates. Most days are NO PLAYS by design.</p>",
           rec_html,
           "<h2>Slates</h2><table><tr><th>date</th><th>games</th><th>cards</th><th>units</th></tr>",
           *rows_html, "</table>"]
    (DOCS / "index.html").write_text(_page("NCAAW Sharp", "".join(idx)))
    print(f"[site] built {len(days)} slate page(s)")
    return 0


if __name__ == "__main__":
    sys.exit(build())
