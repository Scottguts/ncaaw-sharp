"""Tune the v13 model on historic seasons + recalibrate error sigmas.

    python train_model.py                 # tune, validate, write weights.json
    python train_model.py --eval-only     # just report metrics for current weights

Protocol (honest about Torvik rating lookahead — the women's site only serves
season-END ratings, so same-season backtests see future-informed ratings):
  1. Baseline: v13 weights on season 2025 (train) and 2026 (holdout).
  2. Coordinate-descent tune on 2025 only; accept ONLY if the 2026 holdout
     improves on BOTH spread and total MAE vs the v13 baseline.
  3. If accepted, re-run the identical tune on 2025+2026 combined for the
     deployment weights (standard refit-on-all after validation).
  4. Sigmas: residual std of the deployed weights on the 2026 holdout,
     inflated 5% because live (lookahead-free) ratings run slightly noisier.

In-season records are reconstructed from the game log (no lookahead on the
winPct feature). Rating lookahead remains on adjOE/adjDE/four-factors — the
same limitation the original 319-game jsx tune had, now mitigated by
cross-season validation.

Outputs weights.json — ncaaw_model auto-loads it when present (v13 constants
remain the fallback).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from ncaaw_model import TRAINED_WEIGHTS as V13_WEIGHTS, predict

HISTORY = Path(__file__).resolve().parent / "ncaaw_data" / "history"
WEIGHTS_OUT = Path(__file__).resolve().parent / "weights.json"

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

def load_season(season: int) -> list[dict]:
    games = json.loads((HISTORY / f"games_{season}.json").read_text())
    teams = json.loads((HISTORY / f"teams_{season}.json").read_text())["teams"]
    games.sort(key=lambda g: g["date"])
    # reconstruct in-season records (as-of BEFORE each game)
    wl: dict[str, list[int]] = {}
    rows = []
    for g in games:
        h, a = g["home"], g["away"]
        th, ta = teams.get(h), teams.get(a)
        if th and ta:
            hw, hl = wl.get(h, [0, 0])
            aw, al = wl.get(a, [0, 0])
            rows.append({
                "date": g["date"],
                "neutral": g["neutral_site"],
                "home": {**th, "record": f"{hw}-{hl}", "conf": g.get("home_conf") or ""},
                "away": {**ta, "record": f"{aw}-{al}", "conf": g.get("away_conf") or ""},
                "margin_home": g["home_score"] - g["away_score"],
                "total": g["home_score"] + g["away_score"],
            })
        # update records regardless of Torvik coverage
        hwon = g["home_score"] > g["away_score"]
        wl.setdefault(h, [0, 0])[0 if hwon else 1] += 1
        wl.setdefault(a, [0, 0])[1 if hwon else 0] += 1
    return rows


def evaluate(weights: dict, rows: list[dict]) -> dict:
    s_err = t_err = 0.0
    su = 0
    n = 0
    for r in rows:
        p = predict(r["away"], r["home"], weights, neutral_site=r["neutral"])
        pm = -p["spread"]              # predicted home margin
        s_err += abs(pm - r["margin_home"])
        t_err += abs(p["total"] - r["total"])
        if (pm > 0) == (r["margin_home"] > 0) and r["margin_home"] != 0:
            su += 1
        n += 1
    return {"n": n, "spread_mae": s_err / n, "total_mae": t_err / n,
            "su_pct": su / n * 100}


def _objective(m: dict) -> float:
    return m["spread_mae"] + m["total_mae"]


def tune(rows: list[dict], start: dict, passes: int = 5) -> dict:
    """Coordinate descent with shrinking multiplicative steps."""
    w = dict(start)
    best = evaluate(w, rows)
    best_obj = _objective(best)
    print(f"[tune] start: spread {best['spread_mae']:.3f} / total "
          f"{best['total_mae']:.3f} / SU {best['su_pct']:.1f}% (n={best['n']})")
    step = 0.25
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
                m = evaluate(w, rows)
                if _objective(m) < best_obj - 1e-6:
                    best_obj, best, base = _objective(m), m, cand
                    improved = True
                else:
                    w[key] = base
        print(f"[tune] pass {p + 1}: spread {best['spread_mae']:.3f} / total "
              f"{best['total_mae']:.3f} / SU {best['su_pct']:.1f}% (step {step:.3f})")
        step *= 0.5
        if not improved and p >= 2:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-only", action="store_true")
    ap.add_argument("--train-season", type=int, default=2025)
    ap.add_argument("--holdout-season", type=int, default=2026)
    args = ap.parse_args()

    train_rows = load_season(args.train_season)
    hold_rows = load_season(args.holdout_season)
    print(f"[data] train {args.train_season}: {len(train_rows)} games · "
          f"holdout {args.holdout_season}: {len(hold_rows)} games")

    base_train = evaluate(V13_WEIGHTS, train_rows)
    base_hold = evaluate(V13_WEIGHTS, hold_rows)
    print(f"[v13] train:   spread {base_train['spread_mae']:.3f} / total "
          f"{base_train['total_mae']:.3f} / SU {base_train['su_pct']:.1f}%")
    print(f"[v13] holdout: spread {base_hold['spread_mae']:.3f} / total "
          f"{base_hold['total_mae']:.3f} / SU {base_hold['su_pct']:.1f}%")
    if args.eval_only:
        return 0

    tuned = tune(train_rows, V13_WEIGHTS)
    tuned_hold = evaluate(tuned, hold_rows)
    print(f"[tuned] holdout: spread {tuned_hold['spread_mae']:.3f} / total "
          f"{tuned_hold['total_mae']:.3f} / SU {tuned_hold['su_pct']:.1f}%")

    if (tuned_hold["spread_mae"] >= base_hold["spread_mae"]
            or tuned_hold["total_mae"] >= base_hold["total_mae"]):
        print("[tune] REJECTED — holdout did not improve on both heads; "
              "keeping v13 weights (no weights.json written)")
        return 1

    print("[tune] holdout improved on both heads — refitting on all seasons "
          "for deployment")
    final = tune(train_rows + hold_rows, tuned, passes=3)
    s_sig, t_sig = residual_sigmas(final, hold_rows)
    s_sig *= 1.05   # live ratings (no lookahead) run slightly noisier
    t_sig *= 1.05

    out = {
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "train_seasons": [args.train_season, args.holdout_season],
        "weights": {**V13_WEIGHTS, **final},
        "spread_sigma": round(s_sig, 3),
        "total_sigma": round(t_sig, 3),
        "metrics": {
            "v13_holdout": base_hold,
            "tuned_holdout": tuned_hold,
        },
    }
    WEIGHTS_OUT.write_text(json.dumps(out, indent=2))
    print(f"[tune] wrote {WEIGHTS_OUT.name}: sigmas spread {s_sig:.2f} / "
          f"total {t_sig:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
