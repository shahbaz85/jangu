"""Pool backtest results across multiple symbols with the SAME, unmodified config.

The SMC premise is that this price-action pattern is universal, not asset-specific.
If a single symbol doesn't produce enough trades to judge, testing the identical
strict rules across several liquid perpetuals is a more honest way to grow the
sample size than loosening thresholds on one or two coins.

R (risk-normalized return per trade) is comparable across symbols, so pooling it is
valid -- the "equity"/drawdown columns are NOT (each symbol's backtest independently
assumes its own $10k starting capital), so this script deliberately doesn't pool those.

Works for either strategy: pass --strategy smc (default) or --strategy trend.

Usage:
  python pool_symbols.py --symbols BTC/USDT:USDT ETH/USDT:USDT BNB/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT ADA/USDT:USDT DOGE/USDT:USDT AVAX/USDT:USDT --days 730
  python pool_symbols.py --strategy trend --symbols BTC/USDT:USDT ETH/USDT:USDT --days 730
"""
import argparse
import json

import pandas as pd

from data import fetch_ohlcv, load_csv, save_csv


def cache_path(symbol):
    return f"{symbol.split('/')[0]}_15m.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--refetch", action="store_true", help="ignore cached CSVs and re-fetch")
    ap.add_argument("--strategy", choices=["smc", "trend"], default="smc")
    args = ap.parse_args()
    if args.strategy == "trend":
        from trend_config import TrendConfig as Config
        from trend_backtest import run_trend_backtest as run_backtest
    else:
        from config import Config
        from backtest import run_backtest
    cfg = Config()

    all_trades = []
    per_symbol = {}
    for symbol in args.symbols:
        path = cache_path(symbol)
        df = None
        if not args.refetch:
            try:
                df = load_csv(path)
                print(f"{symbol}: loaded cached {path} ({len(df)} bars)")
            except FileNotFoundError:
                pass
        if df is None:
            print(f"{symbol}: fetching {args.days} days...")
            df = fetch_ohlcv(symbol, "15m", args.days, cfg.exchange_id)
            save_csv(df, path)
        st, tr, signals, rejects = run_backtest(df, cfg, symbol, verbose=False)
        per_symbol[symbol] = {
            "trades": len(tr), "signals": len(signals),
            "reject_reasons": pd.Series([r["reason"] for r in rejects]).value_counts().to_dict() if rejects else {},
        }
        if len(tr):
            tr = tr.copy()
            tr["symbol"] = symbol
            all_trades.append(tr)
        print(f"{symbol}: {len(signals)} signals, {len(tr)} trades")

    print("\n--- per-symbol ---")
    print(json.dumps(per_symbol, indent=2, default=str))

    if all_trades:
        pooled = pd.concat(all_trades, ignore_index=True)
        R = pooled["R"]
        wins, losses = R[R > 0], R[R <= 0]
        pf = round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None
        print("\n--- POOLED (R is risk-normalized per trade, so pooling across symbols is valid) ---")
        print(f"total trades: {len(R)}")
        print(f"win rate: {(R > 0).mean():.1%}")
        print(f"expectancy: {R.mean():.3f}R")
        print(f"profit factor: {pf}")
        print(f"total R: {R.sum():.2f}")
        print("by symbol:")
        print(pooled.groupby("symbol")["R"].agg(["count", "mean", "sum"]).round(3).to_string())
    else:
        print("\nNo trades across any symbol.")


if __name__ == "__main__":
    main()
