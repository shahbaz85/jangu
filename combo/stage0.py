"""Stage 0 for the combined strategy (FINAL_REPORT.md section 6).

Sample-size check FIRST, as the spec requires: section 6 expects "well under 1
signal per coin per week, so it may be underpowered -- if so, stop". This script
counts, sizes, and stops. It does not score the variants.

**Do not run this until the LuxAlgo report is back.** Section 6 gates it on that
result, and running early would mean the combined test's outcome is known before
the thing it is supposed to build on.

Usage:
  python combo/stage0.py               # real data (only after the LuxAlgo result)
  python combo/stage0.py --synthetic   # plumbing check, safe any time
"""
import argparse
import pathlib
import sys
from math import sqrt
from statistics import NormalDist

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from combo.config import ComboConfig                              # noqa: E402
from combo import signals as sg                                   # noqa: E402
from data import fetch_ohlcv, load_csv, save_csv, synthetic        # noqa: E402


def load(symbol, cfg, use_synthetic, i):
    if use_synthetic:
        return synthetic(days=cfg.days, seed=1100 + i)
    path = f"{symbol.split('/')[0]}_15m.csv"
    try:
        return load_csv(path)
    except FileNotFoundError:
        print(f"  fetching {symbol} ({cfg.days}d)...", flush=True)
        df = fetch_ohlcv(symbol, "15m", cfg.days, cfg.exchange_id)
        save_csv(df, path)
        return df


def trades_needed(p0, p1, alpha, power):
    """Sample size at the given alpha. Section 6 sets alpha to 0.01 because three
    variants are tested and three shots at 5% pass on noise about one time in
    seven."""
    nd = NormalDist()
    za, zb = nd.inv_cdf(1 - alpha / 2), nd.inv_cdf(power)
    d = abs(p1 - p0)
    if d == 0:
        return float("inf")
    return (za * sqrt(p0 * (1 - p0)) + zb * sqrt(p1 * (1 - p1))) ** 2 / d ** 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()
    cfg = ComboConfig()
    tag = "SYNTHETIC" if args.synthetic else "REAL DATA"

    per = {v: {} for v in cfg.variants}
    per["BOS only"] = {}
    be_all, plac = {v: [] for v in cfg.variants}, {v: [0, 0] for v in cfg.variants}
    weeks = 0.0

    for i, symbol in enumerate(cfg.symbols):
        df = load(symbol, cfg, args.synthetic, i)
        weeks = len(df) * 15 / (60 * 24 * 7)
        bos, _, _ = sg.bos_events(df, cfg)
        per["BOS only"][symbol] = len(bos)
        for name in cfg.variants:
            s = sg.for_variant(df, cfg, name)
            res = [r for r in (sg.resolve(df, x, cfg) for x in s) if r]
            per[name][symbol] = len(res)
            be_all[name] += [cfg.break_even(symbol, x["entry"], x["atr"]) for x in s]
            for sh in cfg.placebo_shifts:
                pr = [r for r in (sg.resolve(df, x, cfg)
                                  for x in sg.shifted(df, s, cfg, sh)) if r]
                plac[name][0] += len(pr)
                plac[name][1] += sum(r["win"] for r in pr)
        print(f"  {symbol.split('/')[0]:<5} BOS {len(bos):>5}  " +
              "  ".join(f"{n} {per[n][symbol]:>4}" for n in cfg.variants), flush=True)

    print("\n" + "=" * 80)
    print(f"COMBINED STRATEGY -- STAGE 0, sample-size check ({tag})")
    print("=" * 80)
    print(f"  {len(cfg.symbols)} coins x {weeks:.0f} weeks = "
          f"{len(cfg.symbols) * weeks:.0f} coin-weeks\n")
    print(f"  {'variant':<10} {'trades':>8} {'per coin/week':>15} {'break-even':>12} "
          f"{'placebo':>9} {'needed':>9}  verdict")

    nd = NormalDist()
    for name in ["BOS only"] + list(cfg.variants):
        n = sum(per[name].values())
        rate = n / (len(cfg.symbols) * weeks) if weeks else float("nan")
        if name == "BOS only":
            print(f"  {name:<10} {n:>8,} {rate:>15.2f} "
                  f"{'-':>12} {'-':>9} {'-':>9}  reference only")
            continue
        be = float(np.nanmean(be_all[name])) if be_all[name] else float("nan")
        pt, pw = plac[name]
        p0 = pw / pt if pt else float("nan")
        need = trades_needed(be, be + 0.05, cfg.alpha, cfg.power) \
            if np.isfinite(be) else float("nan")
        ok = np.isfinite(need) and n >= need
        print(f"  {name:<10} {n:>8,} {rate:>15.2f} {be:>12.2%} {p0:>9.2%} "
              f"{need:>9,.0f}  {'enough' if ok else 'UNDERPOWERED'}")

    print(f"\n  'needed' is the trades required to show a +5 pp edge over break-even")
    print(f"  at {cfg.power:.0%} power and alpha {cfg.alpha} (99% intervals, because three")
    print(f"  variants are tested). +5 pp is the smallest edge worth trading here.")

    if any(np.isfinite(x) for x in be_all["BOS+S+V"]):
        print(f"\n  Note on the geometry: entry is at the next candle's open, so it is a")
        print(f"  market order paying taker plus slippage, and only the target exit can")
        print(f"  rest as a limit. That is dearer than MR-70's maker-limit entry and it")
        print(f"  shows up directly in the break-even column.")

    reused = [s.split('/')[0] for s in cfg.reused_symbols]
    print(f"\n  Universe note: {', '.join(reused)} were three of the four coins MR-70's")
    print(f"  parameters were chosen on. Fresh to this strategy, not to the project.")
    print(f"\n  Per section 6: if a variant is UNDERPOWERED, stop there. Do not add")
    print(f"  coins, extend history or loosen a rule -- that is a new pre-registration.")


if __name__ == "__main__":
    main()
