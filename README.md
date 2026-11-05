# NCAAW Sharp

Automated, market-anchored women's college basketball betting pipeline —
the **v13 Torvik model** (spread MAE 9.66 / total MAE 10.92 / 73% SU,
grid-searched on 319 games) fused with the **mlb-sharp** operational
architecture (strict gates, key rotation, CLV tracking, dead-man's-switch).

## What it does, daily

1. **Scrapes** live Barttorvik NCAAW adjusted efficiencies + four factors,
   the ESPN D-I slate (incl. neutral-site flags), and multi-book odds
   (The Odds API, `basketball_wncaab`).
2. **Projects** every game's spread + total with the v13 model, then
   **anchors to the market**: the model may move the sharp line at most
   ±1.5 pts (spread) / ±2.5 pts (total). Probabilities come from a normal
   error model (σ ≈ 12.1 / 13.7), widened early-season until both teams
   have ~10 games.
3. **Fires cards** only past strict gates: min edge 1.8% (ML) / 2.5%
   (spread, total), max reasonable edge 6% (bigger = stale line → reject),
   price band −180..+180, confidence ≥6/10, two-book price confirmation,
   caps of 5 cards / 6u per slate and 1 card per game.
4. **Posts** to Discord, logs every bet to `ncaaw_data/bet_log.csv`,
   settles from ESPN finals, captures closing lines for CLV, rebuilds the
   GitHub Pages site.

**Most days are NO PLAYS. That is by design, not a bug.** The edge comes
from small, evidence-backed disagreements with the market plus line
shopping — not from predicting games from scratch.

## Run locally

```bash
pip install -r requirements.txt
export ODDS_API_KEY=...            # optional backups: ODDS_API_KEY_2.._8
python ncaaw_data_scraper.py --date 2026-11-04
python ncaaw_grader.py --date 2026-11-04   # re-renders with zero API calls
python bet_tracker.py --append 2026-11-04
python bet_tracker.py --settle
python build_site.py
```

## Hard rules (inherited from mlb-sharp)

- **Never loosen the gates** to manufacture volume. Tightening is fine.
- **Don't rewrite history**: `bet_log.csv` records what was actually bet;
  model changes apply going forward only.
- Validate with **Brier / log-loss on all graded games + CLV**, never
  small-sample ROI.
- `python -m py_compile` touched files before deploy. Never commit API keys.

See `HANDOFF.md` for the full operational brief.
