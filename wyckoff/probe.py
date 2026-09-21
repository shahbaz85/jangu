"""Feasibility probe for an SMC + Wyckoff combination. Counts only, no scoring.

The question this answers, before any strategy is written:

  1. How often does each framework fire, per timeframe?
  2. How often do they fire together? A conjunction can only be rarer than its
     rarest term, and SMC alone produced 17 signals in two years on 15m.
  3. **How much of Wyckoff is already SMC?** A spring is a sweep of a range low
     and an SOS is a displacement break of structure, so the two vocabularies
     may be describing one thing. If the overlap is near total, combining them
     adds rarity without adding information.

Nothing here scores outcomes, so there is nothing to tune toward. Read the
frequency against `shared/diagnostics.py` section 8.5: a design that cannot
supply the trades its effect size needs is not worth specifying.

Usage:
  python wyckoff/probe.py                # real data, cached CSVs
  python wyckoff/probe.py --synthetic    # random walks, for comparison
"""
import argparse
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import fetch_ohlcv, load_csv, save_csv, synthetic          # noqa: E402
from shared.indicators import (market_structure, prev_day_extremes,  # noqa: E402
                               resample, sweep_flags)
from wyckoff.config import WyckoffConfig                             # noqa: E402
from wyckoff.events import events                                    # noqa: E402

BARS_PER_YEAR = {"15min": 365 * 96, "1h": 365 * 24, "4h": 365 * 6}


def smc_signals(df, cfg):
    """The SMC leg in its own terms: a sweep, then a break of structure the same
    way within `smc_sweep_to_bos_bars`. Deliberately looser than the full engine,
    so the overlap below is not understated by an over-tight SMC definition."""
    pdl, pdh = prev_day_extremes(df)
    sw = sweep_flags(df, cfg.fractal_n, pdl, pdh)
    ev = market_structure(df, cfg.fractal_n)["event"].to_numpy()
    sw_lo, sw_hi = sw["sweep_low"].to_numpy(), sw["sweep_high"].to_numpy()
    out = np.zeros(len(df), np.int8)
    last = {1: -10**9, -1: -10**9}
    for i in range(len(df)):
        if sw_lo[i]:
            last[1] = i
        if sw_hi[i]:
            last[-1] = i
        d = int(np.sign(ev[i]))
        if d != 0 and i - last[d] <= cfg.smc_sweep_to_bos_bars:
            out[i] = d
    return out


def coincidence(a, b, window):
    """For each nonzero event in `a`, is there a same-direction event in `b`
    within `window` bars either side? Returns (n_a, n_b, n_a_matched)."""
    ia = np.flatnonzero(a)
    ib = np.flatnonzero(b)
    if len(ia) == 0 or len(ib) == 0:
        return len(ia), len(ib), 0
    matched = 0
    for i in ia:
        near = ib[(ib >= i - window) & (ib <= i + window)]
        if np.any(b[near] == a[i]):
            matched += 1
    return len(ia), len(ib), matched


def load(symbol, cfg, use_synthetic, i):
    if use_synthetic:
        return synthetic(days=cfg.days, seed=700 + i)
    path = f"{symbol.split('/')[0]}_15m.csv"
    try:
        return load_csv(path)
    except FileNotFoundError:
        print(f"  fetching {symbol} ({cfg.days}d)...", flush=True)
        df = fetch_ohlcv(symbol, "15m", cfg.days, cfg.exchange_id)
        save_csv(df, path)
        return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()
    cfg = WyckoffConfig()
    tag = "SYNTHETIC" if args.synthetic else "REAL DATA"
    years = cfg.days / 365 * len(cfg.symbols)

    totals = {tf: {"wy": 0, "smc": 0, "both": 0, "wy_in_smc": 0, "bars": 0}
              for tf in cfg.timeframes}

    for i, symbol in enumerate(cfg.symbols):
        base = load(symbol, cfg, args.synthetic, i)
        for tf in cfg.timeframes:
            df = base if tf == "15min" else resample(base, tf)
            ev = events(df, cfg)
            wy = ev["phase_dir"].to_numpy().astype(np.int8)
            smc = smc_signals(df, cfg)
            n_wy, n_smc, matched = coincidence(wy, smc, cfg.coincide_bars)
            t = totals[tf]
            t["wy"] += n_wy
            t["smc"] += n_smc
            t["wy_in_smc"] += matched
            t["both"] += matched
            t["bars"] += len(df)
        print(f"  {symbol.split('/')[0]} done", flush=True)

    print("\n" + "=" * 78)
    print(f"SMC + Wyckoff feasibility ({tag}, {cfg.days} days x {len(cfg.symbols)} symbols)")
    print("=" * 78)
    print(f"  {'tf':<6} {'bars':>8} {'Wyckoff':>9} {'SMC':>8} {'both':>7} "
          f"{'both/yr':>9} {'Wyckoff already SMC':>21}")
    for tf in cfg.timeframes:
        t = totals[tf]
        per_yr = t["both"] / years
        share = t["wy_in_smc"] / t["wy"] if t["wy"] else float("nan")
        print(f"  {tf:<6} {t['bars']:>8,} {t['wy']:>9,} {t['smc']:>8,} {t['both']:>7,} "
              f"{per_yr:>9.1f} {share:>20.1%}")

    print("\n  'both/yr' is combined signals per symbol-year, pooled across symbols.")
    print("  Against section 8.5 of shared/diagnostics.py: detecting a +5 pp edge needs")
    print("  ~780 trades. Divide that by 'both/yr' to get the symbol-years required.")
    print("\n  'Wyckoff already SMC' is the share of Wyckoff completions that coincide")
    print("  with a same-direction SMC signal. High means the frameworks are one")
    print("  framework in two vocabularies, and conjoining them buys rarity, not")
    print("  information.")

    print("\n  symbol-years needed to test the combination, by effect size:")
    for edge, n in ((0.05, 783), (0.08, 304), (0.10, 194)):
        row = "   ".join(
            f"{tf}: {n / (totals[tf]['both'] / years):,.0f}" if totals[tf]["both"] else f"{tf}: n/a"
            for tf in cfg.timeframes)
        print(f"    +{100 * edge:.0f} pp ({n:,} trades)   {row}")
    print(f"\n  For reference this dataset is {years:.0f} symbol-years.")


if __name__ == "__main__":
    main()
