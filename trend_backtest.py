"""Backtest for the trend-following engine. Reuses simulate()/stats() from backtest.py
unchanged -- only the signal-generation logic (trend_engine.py) is new.

Usage:
  python trend_backtest.py --synthetic
  python trend_backtest.py --fetch BTC/USDT:USDT --days 730
  python trend_backtest.py --csv BTC_15m.csv
"""
import argparse
import json

import pandas as pd

from trend_config import TrendConfig
from trend_engine import TrendEngine
from backtest import simulate, stats


def run_trend_backtest(df, cfg, symbol="", news=None, verbose=True):
    eng = TrendEngine(cfg, news)
    signals, rejects, f = eng.run(df, symbol)
    tr, final_eq = simulate(f, signals, cfg)
    st = stats(tr, 10_000.0, final_eq)
    st["signals"] = len(signals)
    st["reject_reasons"] = pd.Series([r["reason"] for r in rejects]).value_counts().to_dict() if rejects else {}
    if verbose:
        print(json.dumps(st, indent=2, default=str))
    return st, tr, signals, rejects


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--fetch", help="ccxt symbol, e.g. BTC/USDT:USDT")
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out", default="trend_trades.csv")
    args = ap.parse_args()
    cfg = TrendConfig()
    from data import load_csv, fetch_ohlcv, synthetic, save_csv
    if args.csv:
        df, sym = load_csv(args.csv), args.csv
    elif args.fetch:
        df, sym = fetch_ohlcv(args.fetch, "15m", args.days, cfg.exchange_id), args.fetch
        save_csv(df, f"{args.fetch.split('/')[0]}_15m.csv")
    else:
        df, sym = synthetic(days=365), "SYNTH"
    st, tr, *_ = run_trend_backtest(df, cfg, sym)
    tr.to_csv(args.out, index=False)
    print(f"\n{len(tr)} trades written to {args.out}")
