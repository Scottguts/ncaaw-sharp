"""Tune the model on historic seasons + recalibrate error sigmas (v2).

    python train_model.py --auto        # full protocol; writes weights.json on improvement
    python train_model.py --eval-only   # report current weights vs all seasons

v2 protocol (2026-11-05):
  * Trains on every games_<season>.json in ncaaw_data/history/ (2024-2026 +
    the CURRENT season once it has >=300 Torvik-covered games — this is how
    the model absorbs the new season's scoring environment instead of
    guessing from a two-line sample).
  * Recency-weighted loss: older seasons count less (0.7x oldest -> 1.0x most
    recent completed; current season 1.3x).
  * Cross-fold validation: each completed season held out once, tuned on the
    rest. The tune is ADOPTED only if the fold-mean improves the current
    deployed weights on BOTH heads, with no fold regressing >1.5% on spread.
  * On adoption: refit on all seasons, sigmas from residuals on the most
    recent season (x1.05 live-noise inflation), write weights.json.

Honesty note: Torvik's women's site serves season-END ratings only, so
same-season backtests carry rating lookahead. Cross-season folds are the
defense; treat absolute MAE as optimistic, relative comparisons as valid.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from ncaaw_model import predict

HISTORY = Path(__file__).resolve().parent / "ncaaw_data" / "history"
WEIGHTS_OUT = Path(__file__).resolve().parent / "weights.json"

# v13 constants are the ultimate fallback baseline (ncaaw_model.TRAINED_WEIGHTS
# already reflects weights.json when present, so read the raw fallback here).
from ncaaw_model import TRAINED_WEIGHTS as _ACTIVE_WEIGHTS

MIN_CURRENT_SEASON_GAMES = 300
CURRENT_SEASON_WEIGHT = 1.3

# key -> (lo, hi). Only these are tuned; structural caps stay fixed.
BOUNDS = {
    "effWeight": (0.2, 1.0), "tRankWeight": (0.0, 0.5), "winPctWeight": (0.0, 0.5),
    "ffWeight": (0.1, 1.5), "confAdjust": (0.0, 2.0), "hca": (-3.0, 5.0),
    "tempoSens": (0.0, 0.3),
    "spreadCompressBig": (0.8, 1.8), "spreadCompressMid": (0.8, 1.2),
    "spreadCompressSmall": (0.7, 1.1),
    "eFGW": (0.0, 1.0), "torW": (0.0, 0.6), "orbW": (0.0, 0.4), "ftrW": (0.0, 0.6),
    "totalOwnW": (0.3, 0.7), "totalTorW": (0.0, 0.6), "totalHCA": (0.0, 3.0),
    "totalTempoBoost": (0.0, 1.0), "totalBiasBase": (-10.0, 15.0),
    "totalBiasOEScale": (0.0, 0.5), "totalTOMismatch": (0.0, 0.15),
    "totalLowTempoPen": (0.0, 5.0), "totalVarAmp": (0.8, 2.2),
}


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

def available_seasons() -> list[int]:
    return sorted(int(p.stem.split("_")[1]) for p in HISTORY.glob("games_*.json"))


def load_season(season: int) -> list[dict]:
    games = json.loads((HISTORY / f"games_{season}.json").read_text())
    teams = json.loads((HISTORY / f"teams_{season}.json").read_text())["teams"]
    games.sort(key=lambda g: g["date"])
    wl: dict[str, list[int]] = {}   # in-season records, as-of BEFORE each game
    rows = []
    for g in games:
        h, a = g["home"], g["away"]
        th, ta = teams.get(h), teams.get(a)
        if th and ta:
            hw, hl = wl.get(h, [0, 0])
            aw, al = wl.get(a, [0, 0])
            rows.append({
                "season": season,
                "neutral": g["neutral_site"],
                "home": {**th, "record": f"{hw}-{hl}", "conf": g.get("home_conf") or ""},
                "away": {**ta, "record": f"{aw}-{al}", "conf": g.get("away_conf") or ""},
                "margin_home": g["home_score"] - g["away_score"],
                "total": g["home_score"] + g["away_score"],
            })
        hwon = g["home_score"] > g["away_score"]
        wl.setdefault(h, [0, 0])[0 if hwon else 1] += 1
        wl.setdefault(a, [0, 0])[1 if hwon else 0] += 1
    return rows


def season_weights(seasons: list[int], current: int | None) -> dict[int, float]:
    completed = [s for s in seasons if s != current]
    w = {}
    n = len(completed)
    for i, s in enumerate(sorted(completed)):
        w[s] = 0.7 + 0.3 * (i / max(1, n - 1)) if n > 1 else 1.0
    if current in seasons:
        w[current] = CURRENT_SEASON_WEIGHT
    return w


# ---------------------------------------------------------------------------
# Evaluation + tuning
# ---------------------------------------------------------------------------

def evaluate(weights: dict, rows: list[dict], row_w: dict[int, float] | None = None) -> dict:
    s_err = t_err = wsum = 0.0
    su = n = 0
    for r in rows:
        w = (row_w or {}).get(r["season"], 1.0)
        p = predict(r["away"], r["home"], weights, neutral_site=r["neutral"])
        pm = -p["spread"]
        s_err += w * abs(pm - r["margin_home"])
        t_err += w * abs(p["total"] - r["total"])
        wsum += w
        if (pm > 0) == (r["margin_home"] > 0) and r["margin_home"] != 0:
            su += 1
        n += 1
    return {"n": n, "spread_mae": s_err / wsum, "total_mae": t_err / wsum,
            "su_pct": su / n * 100}


def _obj(m: dict) -> float:
    return m["spread_mae"] + m["total_mae"]


def tune(rows: list[dict], start: dict, row_w: dict[int, float] | None = None,
         passes: int = 7, step0: float = 0.30, label: str = "") -> dict:
    w = dict(start)
    best = evaluate(w, rows, row_w)
    best_obj = _obj(best)
    step = step0
    stale = 0
    for p in range(passes):
        improved = False
        for key, (lo, hi) in BOUNDS.items():
            base = w[key]
            span = hi - lo
            for delta in (step * span, -step * span):
                cand = max(lo, min(hi, base + delta))
                if cand == base:
                    continue
                w[key] = cand
                m = evaluate(w, rows, row_w)
                if _obj(m) < best_obj - 1e-6:
                    best_obj, best, base = _obj(m), m, cand
                    improved = True
                else:
                    w[key] = base
        print(f"[tune{label}] pass {p + 1}: spread {best['spread_mae']:.3f} / "
              f"total {best['total_mae']:.3f} / SU {best['su_pct']:.1f}% (step {step:.3f})")
        step *= 0.55
        stale = 0 if improved else stale + 1
        if stale >= 2:
            break
    return w


def residual_sigmas(weights: dict, rows: list[dict]) -> tuple[float, float]:
    ss = tt = 0.0
    n = 0
    for r in rows:
        p = predict(r["away"], r["home"], weights, neutral_site=r["neutral"])
        ss += ((-p["spread"]) - r["margin_home"]) ** 2
        tt += (p["total"] - r["total"]) ** 2
        n += 1
    return math.sqrt(ss / n), math.sqrt(tt / n)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

def auto(current_season: int | None = None) -> int:
    seasons = available_seasons()
    data = {s: load_season(s) for s in seasons}
    # current season only joins once it has a real sample
    if current_season in data and len(data[current_season]) < MIN_CURRENT_SEASON_GAMES:
        print(f"[auto] season {current_season}: only {len(data[current_season])} "
              f"covered games (<{MIN_CURRENT_SEASON_GAMES}) — excluded this week")
        data.pop(current_season)
        seasons = [s for s in seasons if s != current_season]
    sw = season_weights(seasons, current_season)
    print(f"[auto] seasons: " + ", ".join(
        f"{s} ({len(data[s])} games, w={sw[s]:.2f})" for s in seasons))

    baseline = dict(_ACTIVE_WEIGHTS)
    folds = [s for s in seasons if s != current_season]
    results = []
    for hold in folds:
        train_rows = [r for s in seasons if s != hold for r in data[s]]
        hold_rows = data[hold]
        base_m = evaluate(baseline, hold_rows)
        print(f"\n[fold hold={hold}] baseline: spread {base_m['spread_mae']:.3f} "
              f"/ total {base_m['total_mae']:.3f} / SU {base_m['su_pct']:.1f}%")
        tuned = tune(train_rows, baseline, sw, label=f" hold={hold}")
        tuned_m = evaluate(tuned, hold_rows)
        print(f"[fold hold={hold}] tuned:    spread {tuned_m['spread_mae']:.3f} "
              f"/ total {tuned_m['total_mae']:.3f} / SU {tuned_m['su_pct']:.1f}%")
        results.append((hold, base_m, tuned_m))

    mean_bs = sum(r[1]["spread_mae"] for r in results) / len(results)
    mean_ts = sum(r[2]["spread_mae"] for r in results) / len(results)
    mean_bt = sum(r[1]["total_mae"] for r in results) / len(results)
    mean_tt = sum(r[2]["total_mae"] for r in results) / len(results)
    worst_reg = max((r[2]["spread_mae"] / r[1]["spread_mae"] - 1) for r in results)
    print(f"\n[auto] fold means — spread {mean_bs:.3f}->{mean_ts:.3f}, "
          f"total {mean_bt:.3f}->{mean_tt:.3f}, worst spread regression "
          f"{worst_reg * 100:+.1f}%")
    if not (mean_ts < mean_bs and mean_tt < mean_bt and worst_reg < 0.015):
        print("[auto] REJECTED — folds did not beat the deployed weights; "
              "weights.json unchanged")
        return 1

    print("[auto] accepted — refitting on all seasons for deployment")
    all_rows = [r for s in seasons for r in data[s]]
    final = tune(all_rows, tune(all_rows, baseline, sw, passes=5, label=" refit"),
                 sw, passes=3, step0=0.05, label=" polish")
    sig_season = max(folds)
    s_sig, t_sig = residual_sigmas(final, data[sig_season])
    s_sig *= 1.05
    t_sig *= 1.05
    out = {
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "train_seasons": seasons,
        "weights": {**baseline, **final},
        "spread_sigma": round(s_sig, 3),
        "total_sigma": round(t_sig, 3),
        "metrics": {
            "folds": [{"holdout": h, "baseline": b, "tuned": t}
                      for h, b, t in results],
        },
    }
    WEIGHTS_OUT.write_text(json.dumps(out, indent=2))
    print(f"[auto] wrote weights.json — sigmas spread {s_sig:.2f} / total {t_sig:.2f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="full cross-fold protocol")
    ap.add_argument("--eval-only", action="store_true")
    ap.add_argument("--current-season", type=int, default=2027)
    args = ap.parse_args()
    if args.eval_only:
        for s in available_seasons():
            rows = load_season(s)
            m = evaluate(_ACTIVE_WEIGHTS, rows)
            print(f"[eval] {s}: n={m['n']} spread {m['spread_mae']:.3f} / "
                  f"total {m['total_mae']:.3f} / SU {m['su_pct']:.1f}%")
        return 0
    return auto(args.current_season)


if __name__ == "__main__":
    sys.exit(main())
