"""Cheap feasibility probe: how many Strategy B signals actually exist?

The gate needs >= 150 pooled signals. Our own SMC engine managed only 17 across 8
symbols in 730 days, so it was worth measuring this before building the rest of
Strategy B's apparatus. Ablation counts are printed too, but the spec forbids using
them to select anything -- they are here to show which level does the filtering.

Usage:
  python shared/count_b.py              # real data, cached CSVs reused
  python shared/count_b.py --synthetic  # plumbing check
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd

from data import fetch_ohlcv, load_csv, save_csv, synthetic
from shared.config import ABConfig
from shared.features import build_features
from shared.strategy_b import cascade


def cache_path(symbol: str) -> str:
    return f"{symbol.split('/')[0]}_15m.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--refetch", action="store_true")
    args = ap.parse_args()
    cfg = ABConfig()

    rows, totals = [], {"full": 0, "no_sweep": 0, "no_zone": 0, "long": 0, "short": 0}
    for i, symbol in enumerate(cfg.symbols):
        if args.synthetic:
            df, label = synthetic(days=cfg.days, seed=300 + i), f"SYNTH:{symbol.split('/')[0]}"
        else:
            path, df = cache_path(symbol), None
            if not args.refetch:
                try:
                    df = load_csv(path)
                except FileNotFoundError:
                    pass
            if df is None:
                print(f"  fetching {symbol} ({cfg.days}d)...", flush=True)
                df = fetch_ohlcv(symbol, "15m", cfg.days, cfg.exchange_id)
                save_csv(df, path)
            label = symbol
        f = build_features(df, cfg)
        full = cascade(f, cfg)
        longs = sum(1 for s in full if s["dir"] == 1)
        rows.append({
            "symbol": label, "bars": len(f), "full": len(full),
            "long": longs, "short": len(full) - longs,
            "no_sweep": len(cascade(f, cfg, require_sweep=False)),
            "no_zone": len(cascade(f, cfg, require_zone=False)),
        })
        totals["full"] += len(full)
        totals["long"] += longs
        totals["short"] += len(full) - longs
        totals["no_sweep"] += rows[-1]["no_sweep"]
        totals["no_zone"] += rows[-1]["no_zone"]
        print(f"  {label}: {len(full)} signals ({longs}L/{len(full)-longs}S)", flush=True)

    print(f"\n{'='*70}\nSTRATEGY B SIGNAL COUNT"
          f"{'  (SYNTHETIC)' if args.synthetic else '  (REAL DATA)'}\n{'='*70}")
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"\npooled full cascade: {totals['full']}  "
          f"({totals['long']} long / {totals['short']} short)")
    print(f"gate minimum:        {cfg.gate_min_signals}")
    need = cfg.gate_min_signals
    if totals["full"] >= need:
        print(f"-> FEASIBLE: {totals['full']} >= {need}, worth building the gate")
    else:
        print(f"-> INSUFFICIENT SAMPLE: {totals['full']} < {need}; the gate cannot "
              f"be evaluated and per the spec we stop here")
    print(f"\nablations (reporting only, never used to select):")
    print(f"  drop the 30m sweep requirement: {totals['no_sweep']}")
    print(f"  drop the 1H zone requirement:   {totals['no_zone']}")


if __name__ == "__main__":
    main()
