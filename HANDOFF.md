# NCAAW Sharp — Handoff / Onboarding Brief

Paste-and-go context for a new Claude (or Claude Code) session. Source of
truth = the code + the GitHub Actions workflows.

## 0. Lineage

This system is the fusion of two parents:

- **mlb-sharp** (github.com/Scottguts/mlb-sharp) — the operational
  architecture: scraper → grader → gated cards → Discord → bet log / CLV →
  GitHub Pages, Odds-API key rotation + quota monitor, heartbeat watchdog,
  "NO PLAYS is a correct day" philosophy.
- **ncaaw_ml_v11.jsx** (the 2025-26 React app) — the prediction model:
  Barttorvik adjusted efficiencies + four factors → projected spread/total,
  with the v13 grid-searched weights (115k+ combos on 319 games; spread MAE
  9.66, total MAE 10.92, 73.0% SU). The Python port in `ncaaw_model.py` is
  **bit-exact** vs the JSX math (verified in node).

## 1. The money philosophy / key constants (ncaaw_grader.py)

```
MIN_EDGE      = moneyline 1.8% · spread 2.5% · total 2.5%
MAX_REASONABLE_EDGE = 6%      # bigger "edges" = stale/calibration bug → rejected
MIN_TEAM_WIN_PROB   = 0.45    # never ML a team we rate <45%
PRICE BAND          = -180 .. +180
MIN_CONFIDENCE      = 6/10    # gap-score + edge-score, both axes must contribute
BOOK_CONFIRMATION_TOLERANCE_CENTS = 8   # best price needs a 2nd book (same line)
MAX_SPREAD_SHIFT = 1.5 pts    # model may move the market line at most this far
MAX_TOTAL_SHIFT  = 2.5 pts
Caps: 5 cards / 6.0u per slate · 1 card & ≤1.5u per game
UNIT_LADDER: 5%+/conf8 → 1.5u · 3.5%+/conf7 → 1.0u · 1.8%+/conf6 → 0.5u
```

NCAAW-specific: **early-season discipline** — error sigma is doubled at 0
games and tapers to the CV value once both teams have 10 games
(`early_season_sigma`); under 2 games on either side → no bet at all.
November is deliberately quiet.

## 2. File inventory

| File | Role |
|---|---|
| `ncaaw_model.py` | Model: predict(), prob layer (normal error model), name canonicalization. Auto-loads tuned `weights.json` (v13 constants = fallback; delete weights.json to roll back). |
| `historic_scrape.py` | Full-season ESPN results + Torvik ratings → ncaaw_data/history/ (2 seasons, ~12k games). |
| `train_model.py` | Cross-season-validated coordinate-descent tune + sigma recalibration → weights.json. 2026-11-04 tune (10,304 games): holdout spread MAE 9.46→8.82, SU 79.7→80.2%; sigmas spread 11.73 / total 15.97 (v13's 13.7 total sigma was overconfident). |
| `line_watch.py` | Line-drop watcher: re-scrape + re-grade every 2h, fire ONLY new cards to Discord (yellow LINE DROP embed), day-level caps enforced across runs via bet_log. |
| `ncaaw_data_scraper.py` | Torvik ratings (JS-check cookie bypass), ESPN slate (neutral-site, conf ids), Odds API board (one call/run) with key rotation + quota alert. |
| `ncaaw_grader.py` | Market anchoring, cover probs, gates, card selection. Writes grades.json + cards.md. Zero API calls. |
| `bet_tracker.py` | bet_log.csv append/settle (ESPN finals), record.md. Atomic writes. |
| `closing_snapshot.py` | Closing prices near tip → CLV (vig-to-vig — fixes mlb-sharp open item D from day one). |
| `notify.py` | Discord embeds; red @here DATA OUTAGE alert. |
| `build_site.py` | Deterministic GitHub Pages site (docs/). |

Data: `ncaaw_data/<date>/{teams,slate,odds,grades}.json + cards.md`,
top-level `ncaaw_data/bet_log.csv`, `ncaaw_data/record.md`.

## 3. How it runs (GitHub Actions, season-gated Nov–Apr)

| Workflow | Schedule (UTC) | Purpose |
|---|---|---|
| daily-bets.yml | 14:47 / 15:32 / 16:17 / 17:17 | THE pipeline. `precheck` skips when cards.md already committed. |
| heartbeat.yml | 18:30 & 19:30 | Dead-man's-switch: no slate by early afternoon ET → Discord ping + auto-dispatch. |
| closing-snapshot.yml | hourly 17:00–04:00 | CLV capture; skips the odds call when nothing is pending. |
| line-watch.yml | every 2h, 11:10–23:10 | Catch newly-posted lines; fire best bets immediately (max CLV). ~210 req/mo — add ODDS_API_KEY_2 for headroom. |

**Dashboard:** `docs/index.html` is the live board (best bets w/ settled
results + model-vs-market for every game, auto-refresh 5 min). Rebuilt and
pushed by every daily-bets / line-watch run. Enable GitHub Pages on docs/
(repo must be public on a free plan) or open the file locally.

Manual full fire:
```bash
git rm ncaaw_data/$(date +%F)/cards.md && git commit -m "force rerun" && git push
gh workflow run daily-bets.yml --ref main
```

## 4. Odds-API quota

Same machinery as mlb-sharp: `ODDS_API_KEY` (+ `_2`.._8`) GitHub secrets,
auto-failover on 401/429, Discord warning when the LAST key dips below ~150.
NCAAW burn is tiny — ~1 call/daily run + closing snapshots ≈ **40–70
requests/month** (no per-event prop calls). It shares keys with mlb-sharp's
quota, but Nov–Apr barely overlaps the MLB season.

Sport key: `basketball_wncaab`. Books: draftkings/betmgm/caesars shopping,
pinnacle devig anchor. FanDuel stays excluded (mlb-sharp empirical analysis).

## 5. Data sources

- `barttorvik.com/ncaaw/trank.php?json=1&year=<season>` — season = end year
  (Nov 2026 → 2027). Fronted by a trivial JS check: POST
  `js_test_submitted=1` once → `js_verified` cookie. Column map verified
  against the 2026 season to the decimal (see fetch_torvik docstring).
  **GOTCHA: barttorvik 403-blocks GitHub runner (datacenter) IPs**, so CI
  always falls back to `ncaaw_data/latest_teams.json` — the snapshot pushed
  by the last successful live fetch. Keep it fresh by double-clicking
  `refresh_teams.command` on the Mac every day or two (ratings drift
  slowly; a stale snapshot triggers a Discord warning, not an outage).
- `site.api.espn.com/.../womens-college-basketball/scoreboard?dates=&groups=50`
  — slate, neutral-site flags, conference ids, finals for settlement.
- The Odds API `basketball_wncaab` — h2h/spreads/totals.

Unmatched teams land in `ncaaw_data/<date>/unmatched_teams.json` — almost
always non-D-I exhibition opponents (correctly skipped). A real D-I miss →
add an alias to `TORVIK_ALIASES` in ncaaw_model.py.

## 6. Hard rules

1. **Never loosen betting gates** to create plays. Tightening is fine.
2. **Don't rewrite history** — bet_log.csv is what was actually bet.
3. **Verify before deploy** — `python -m py_compile` touched files.
4. **Secrets** — never commit keys; errors are redacted (`apiKey=***`).
5. Commit on a branch / push only when asked.

## 7. Known open items / good next tasks

- Injuries: `predict()` accepts injury deltas but nothing feeds them yet
  (the JSX app had a manual injury UI). A manual
  `ncaaw_data/injuries.json` + grader hookup is the natural v2.
- Push handling: cover probs use a continuous normal (push mass at integer
  lines is folded in). Half-point lines dominate NCAAW so impact is small.
- Re-training: the in-app gradient trainer wasn't ported. Collect a season
  of grades.json, then re-fit weights offline (same loss: recency-weighted
  MAE + direction penalty).
- Totals model vs market: v13 was tuned to flat 2025-26 scoring; watch the
  anchored-total shift distribution the first month — if it pegs at ±2.5
  constantly, recalibrate `totalBiasBase`.
- Brier/log-loss validation script over all graded games (port of
  model_calibration.py).
