"""Walk-forward test: pick parameters on a training window, judge them ONLY on the next unseen window.

If the out-of-sample (OOS) expectancy collapses compared with in-sample, the "edge" was curve-fitted.

  python walkforward.py --csv BTC_15m.csv --train-months 6 --test-months 2
"""
import argparse
import itertools
from dataclasses import replace

import numpy as np
import pandas as pd

from config import Config
from backtest import run_backtest

GRID = {
    "displacement_body_atr": [1.2, 1.5],
    "max_sl_atr": [1.5, 2.0],
    "entry_mode": ["fvg_mid", "ob_top"],
    "require_premium_discount": [True, False],
}
WARMUP_DAYS = 25   # history fed before each window so indicators/ATR percentile are warmed up


def window(df, start, end):
    return df[(df.index >= start - pd.Timedelta(days=WARMUP_DAYS)) & (df.index < end)]


def trades_in(tr, start):
    return tr[pd.to_datetime(tr["signal_time"]) >= start] if len(tr) else tr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--train-months", type=int, default=6)
    ap.add_argument("--test-months", type=int, default=2)
    ap.add_argument("--min-trades", type=int, default=15)
    args = ap.parse_args()

    from data import load_csv
    df = load_csv(args.csv)
    base = Config()
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    t0 = df.index[0] + pd.Timedelta(days=WARMUP_DAYS)
    oos_all, rows = [], []
    while True:
        tr_s, tr_e = t0, t0 + pd.DateOffset(months=args.train_months)
        te_e = tr_e + pd.DateOffset(months=args.test_months)
        if te_e > df.index[-1]:
            break
        best, best_score = None, -np.inf
        for p in combos:
            cfg = replace(base, **p)
            _, tr, *_ = run_backtest(window(df, tr_s, tr_e), cfg, verbose=False)
            tr = trades_in(tr, tr_s)
            if len(tr) < args.min_trades:
                continue
            score = tr["R"].mean()
            if score > best_score:
                best, best_score = p, score
        if best is None:
            print(f"{tr_s.date()} -> {tr_e.date()}: not enough trades for any parameter set")
        else:
            _, te, *_ = run_backtest(window(df, tr_e, te_e), replace(base, **best), verbose=False)
            te = trades_in(te, tr_e)
            oos_all.append(te)
            rows.append({"train": f"{tr_s.date()}..{tr_e.date()}", "IS_expectancy": round(best_score, 3),
                         "OOS_trades": len(te), "OOS_expectancy": round(te["R"].mean(), 3) if len(te) else None,
                         **best})
        t0 = t0 + pd.DateOffset(months=args.test_months)

    print(pd.DataFrame(rows).to_string(index=False))
    if oos_all:
        R = pd.concat(oos_all)["R"]
        print(f"\nOOS combined: {len(R)} trades, win rate {(R > 0).mean():.1%}, expectancy {R.mean():.3f}R, total {R.sum():.1f}R")


if __name__ == "__main__":
    main()
