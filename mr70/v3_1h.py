"""Stage 0 and Stage 0b of V3_1H_SPEC.md (+ addendum): can this test be run at all?

Counts V3 signals and filled trades on native 1H data, measures the placebo
baseline, and compares the projected sample against what the hypothesis needs.
Nothing here scores V3 against its requirement -- that is Stage 1, and the spec
forbids running it unless Stage 0 clears.

Stage 0b (addendum, Change 1) powers the test against the *smallest edge worth
trading* rather than against break-even. Powering for break-even asks whether a
strategy that earns nothing can be told apart from the placebo, and a strategy
sitting exactly at break-even clears Stage 1's lower-bound condition only about
2.5% of the time at any sample size. The addendum's target is Stage 2's own
+0.08R criterion, which is a strictly harder thing for V3 to achieve -- it just
happens to need fewer trades to detect, because the effect is larger.

Neither stage touches a V3 outcome, so the V3 arm stays unseen.

The 15m V3 code and its results are untouched; this adds files only.

Usage:
  python mr70/v3_1h.py                # real data
  python mr70/v3_1h.py --synthetic    # plumbing check
"""
import argparse
import pathlib
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from math import sqrt
from statistics import NormalDist

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import fetch_ohlcv, load_csv, save_csv, synthetic      # noqa: E402
from mr70.config import MR70Config                               # noqa: E402
from mr70.edge_gate import evaluate                              # noqa: E402
from mr70.indicators import build_features                       # noqa: E402
from mr70.signals import v3_vwap_climax                          # noqa: E402
from shared.diagnostics import trades_needed                     # noqa: E402

SHIFTS = (-800, -400, 400, 800)      # 1H bars, per the spec


@dataclass
class V3OneHourConfig(MR70Config):
    """V3 unchanged; only the window lengths are converted, by time.

    A 96-bar window on 15m is 24 hours, so on 1H it is 24 bars. The time stop
    follows the spec's diffusion argument: 1H ATR is roughly twice the 15m ATR,
    a trade needs about four times as many 15m bars to cover it, and 64 x 15m is
    16 x 1H.
    """
    base_tf: str = "1h"
    htf_1h: str = "1h"
    htf_4h: str = "4h"
    days: int = 1460                 # 4 years
    vwap_std_len: int = 24
    vol_avg_len: int = 24
    max_bars: int = 16
    cooldown_bars: int = 2
    slippage: float = 0.0002         # BTC / ETH
    slippage_high: float = 0.0004    # everything else
    high_slip_symbols: tuple = ()    # set in __post_init__ to "all but BTC/ETH"
    symbols: list = field(default_factory=lambda: [
        "BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT", "SOL/USDT:USDT",
        "XRP/USDT:USDT", "DOGE/USDT:USDT", "ADA/USDT:USDT", "AVAX/USDT:USDT",
        "LINK/USDT:USDT", "LTC/USDT:USDT", "DOT/USDT:USDT", "TRX/USDT:USDT"])

    # the four symbols the 15m parameters were chosen on, reported separately
    tuned_on: tuple = ("BNB/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "DOGE/USDT:USDT")

    def __post_init__(self):
        self.high_slip_symbols = tuple(
            s for s in self.symbols if s not in ("BTC/USDT:USDT", "ETH/USDT:USDT"))


def required_rate(f, i: int, cfg, symbol: str) -> float:
    """Cost-inclusive break-even for one signal: (SL + cl) / (TP + SL + cl - cw).

    Distances are in ATR units and costs are converted to the same units at this
    bar, so the ratio is dimensionless. A win pays the maker fee twice; a loss
    pays maker in, taker plus slippage out.
    """
    a, px = f["atr"].to_numpy()[i], f["close"].to_numpy()[i]
    if not (np.isfinite(a) and a > 0 and px > 0):
        return float("nan")
    unit = px / a                                     # price fraction -> ATR units
    cw = (2 * cfg.maker_fee) * unit
    cl = (cfg.maker_fee + cfg.taker_fee + cfg.slip_for(symbol)) * unit
    return (cfg.sl_atr + cl) / (cfg.tp_atr + cfg.sl_atr + cl - cw)


def min_tradeable_rate(f, i: int, cfg, symbol: str, edge_r: float = 0.08) -> float:
    """Hit rate at which this signal's expected return equals `edge_r` per trade.

    In R units, with R the stop distance: a win pays (TP - cw)/SL and a loss
    costs (SL + cl)/SL. Setting p(TP - cw)/SL - (1-p)(SL + cl)/SL = edge_r and
    solving gives

        p* = (edge_r*SL + SL + cl) / (TP - cw + SL + cl)

    which reduces to the plain break-even when edge_r is 0.
    """
    a, px = f["atr"].to_numpy()[i], f["close"].to_numpy()[i]
    if not (np.isfinite(a) and a > 0 and px > 0):
        return float("nan")
    unit = px / a
    cw = (2 * cfg.maker_fee) * unit
    cl = (cfg.maker_fee + cfg.taker_fee + cfg.slip_for(symbol)) * unit
    return (edge_r * cfg.sl_atr + cfg.sl_atr + cl) / (cfg.tp_atr - cw + cfg.sl_atr + cl)


def two_arm_n(p0: float, p1: float, ratio: float, power: float = 0.80,
              alpha: float = 0.05) -> float:
    """Trades needed in the smaller arm when the other arm is `ratio` times larger.

    `trades_needed()` in shared/diagnostics.py assumes equal arms. The placebo
    here runs four shifts and carries roughly 2.4x the V3 arm, and ignoring that
    overstates the requirement -- which is what produced Stage 0's misleadingly
    harsh verdict.
    """
    nd = NormalDist()
    za, zb = nd.inv_cdf(1 - alpha / 2), nd.inv_cdf(power)
    d = abs(p1 - p0)
    if d == 0 or ratio <= 0:
        return float("inf")
    return (za + zb) ** 2 * (p1 * (1 - p1) + p0 * (1 - p0) / ratio) / d ** 2


def shifted_signals(signals, n_bars: int, shift: int):
    """The time-shifted placebo: same direction, same construction, no alignment.

    V3's geometry is defined entirely in ATR units at the entry bar -- entry at
    that bar's close, TP and SL a fixed number of ATR away -- so moving the bar
    index carries the construction with it and nothing needs rescaling.

    Shifts wrap around the end of the sample rather than being dropped. Dropping
    would discard late-sample signals preferentially and leave the placebo
    sampling an earlier stretch of history than the signal arm.
    """
    return [((i + shift) % n_bars, d) for i, d in signals]


def load(symbol, cfg, use_synthetic, i):
    if use_synthetic:
        df = synthetic(days=cfg.days // 4, seed=900 + i)      # 15m bars -> resample below
        return df.resample("1h", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}).dropna()
    path = f"{symbol.split('/')[0]}_1h.csv"
    try:
        return load_csv(path)
    except FileNotFoundError:
        print(f"  fetching {symbol} 1h ({cfg.days}d)...", flush=True)
        df = fetch_ohlcv(symbol, "1h", cfg.days, cfg.exchange_id)
        save_csv(df, path)
        return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()
    cfg = V3OneHourConfig()
    tag = "SYNTHETIC" if args.synthetic else "REAL DATA"

    per, be_all, ps_all = {}, [], []
    pooled = {"signals": 0, "trades": 0, "wins": 0, "unfilled": 0, "years": 0.0}
    placebo = {"trades": 0, "wins": 0}

    for i, symbol in enumerate(cfg.symbols):
        df = load(symbol, cfg, args.synthetic, i)
        f = build_features(df, cfg)
        sigs = v3_vwap_climax(f, cfg)
        outs, _ = evaluate(f, sigs, cfg, symbol)
        traded = [r for r in outs if r["status"] in ("tp", "sl", "time")]
        years = len(df) / (365 * 24)

        for r in traded:
            b = required_rate(f, r["idx"], cfg, symbol)
            if np.isfinite(b):
                be_all.append(b)
            ps = min_tradeable_rate(f, r["idx"], cfg, symbol)
            if np.isfinite(ps):
                ps_all.append(ps)

        for sh in SHIFTS:
            p_outs, _ = evaluate(f, shifted_signals(sigs, len(f), sh), cfg, symbol)
            p_traded = [r for r in p_outs if r["status"] in ("tp", "sl", "time")]
            placebo["trades"] += len(p_traded)
            placebo["wins"] += sum(r["win"] for r in p_traded)

        per[symbol] = {"signals": len(sigs), "trades": len(traded), "years": years,
                       "unfilled": sum(1 for r in outs if r["status"] == "unfilled"),
                       "per_year": len(traded) / years if years else float("nan")}
        pooled["signals"] += len(sigs)
        pooled["trades"] += len(traded)
        pooled["wins"] += sum(r["win"] for r in traded)
        pooled["unfilled"] += per[symbol]["unfilled"]
        pooled["years"] += years
        print(f"  {symbol.split('/')[0]:<5} {len(sigs):>5} signals  "
              f"{len(traded):>5} trades  {per[symbol]['per_year']:>6.1f}/yr", flush=True)

    print("\n" + "=" * 78)
    print(f"STAGE 0 -- sample-size check ({tag})")
    print("=" * 78)
    print(f"  {'symbol':<8} {'years':>6} {'signals':>8} {'unfilled':>9} {'trades':>7} {'per yr':>8}")
    for s, v in per.items():
        print(f"  {s.split('/')[0]:<8} {v['years']:>6.1f} {v['signals']:>8,} "
              f"{v['unfilled']:>9,} {v['trades']:>7,} {v['per_year']:>8.1f}")
    print(f"  {'POOLED':<8} {pooled['years']:>6.1f} {pooled['signals']:>8,} "
          f"{pooled['unfilled']:>9,} {pooled['trades']:>7,} "
          f"{pooled['trades'] / pooled['years']:>8.1f}")

    tuned = sum(per[s]["trades"] for s in cfg.tuned_on if s in per)
    print(f"\n  of which parameters were chosen on (BNB/ETH/SOL/DOGE): {tuned:,} trades")
    print(f"  the other eight symbols: {pooled['trades'] - tuned:,} trades")

    p1 = float(np.mean(be_all)) if be_all else float("nan")
    p0 = placebo["wins"] / placebo["trades"] if placebo["trades"] else float("nan")
    print(f"\n  placebo baseline (time-shifted, {len(SHIFTS)} shifts): "
          f"{p0:.2%} on {placebo['trades']:,} trades")
    print(f"  pooled cost-inclusive break-even: {p1:.2%}")

    if not (np.isfinite(p0) and np.isfinite(p1)) or p1 <= p0:
        print("\n  VERDICT: cannot compute a required sample -- the break-even rate is not")
        print("  above the placebo baseline, so there is no effect size to power for.")
        return

    one, two = trades_needed(p0, p1)
    need = max(one, two)
    print(f"  effect the hypothesis needs: +{100 * (p1 - p0):.1f} pp over placebo")
    print(f"\n  trades needed at 80% power: {one:,.0f} one-sample "
          f"(hit rate vs its required rate)")
    print(f"                              {two:,.0f} per arm "
          f"(paired difference vs placebo)")
    print(f"  Stage 1 requires BOTH conditions, so the binding figure is {need:,.0f}.")

    print(f"\n  available: {pooled['trades']:,}")
    if pooled["trades"] < need:
        print(f"\n  VERDICT: UNDERPOWERED -- do not run the gate.")
        print(f"  {pooled['trades']:,} trades against {need:,.0f} needed "
              f"({pooled['trades'] / need:.0%} of the requirement).")
        print(f"  Reaching it would take about {need / (pooled['trades'] / pooled['years']):,.0f} "
              f"symbol-years against the {pooled['years']:.0f} available.")
        print("  Per the spec, adding symbols, extending history or loosening the signal")
        print("  is a new pre-registration decision for the owner, not a fix to make here.")
    else:
        print(f"\n  VERDICT: sample is sufficient -- Stage 1 may run.")

    # ---- Stage 0b: power against the smallest edge worth trading -------------
    p_star = float(np.mean(ps_all)) if ps_all else float("nan")
    print("\n" + "=" * 78)
    print(f"STAGE 0b -- power against the smallest tradeable edge ({tag})")
    print("=" * 78)
    print(f"  pooled break-even (earns nothing)        {p1:>7.2%}")
    print(f"  pooled p* (+0.08R per trade)             {p_star:>7.2%}")
    print(f"  placebo baseline                         {p0:>7.2%}")
    if not np.isfinite(p_star) or p_star <= p1:
        print("\n  VERDICT: cannot size -- p* is not above break-even.")
        return

    one = ((NormalDist().inv_cdf(0.975) * sqrt(p1 * (1 - p1))
            + NormalDist().inv_cdf(0.80) * sqrt(p_star * (1 - p_star))) ** 2
           / (p_star - p1) ** 2)
    ratio = placebo["trades"] / pooled["trades"] if pooled["trades"] else float("nan")
    two_arm = two_arm_n(p0, p_star, ratio)
    need_b = max(one, two_arm)
    print(f"\n  1. one-sample, H0 = break-even, true = p*  (+{100 * (p_star - p1):.1f} pp)"
          f"   -> {one:>7,.0f} trades")
    print(f"  2. two-arm vs placebo (+{100 * (p_star - p0):.1f} pp), "
          f"placebo arm {ratio:.2f}x   -> {two_arm:>7,.0f} trades")
    print(f"\n  binding requirement {need_b:,.0f}   available {pooled['trades']:,}")
    if pooled["trades"] >= need_b:
        print(f"\n  VERDICT: STAGE 0b SATISFIED -- Stage 1 may run.")
    else:
        print(f"\n  VERDICT: UNDERPOWERED even against the tradeable edge -- stop.")
    print("\n  Note: p* is what Stage 2 requires to call V3 tradeable. Stage 1 only")
    print("  asks whether V3 clears break-even, which is a lower bar. V3 hit 72.80%")
    print("  on 15m; p* here is far above that, so a Stage 1 pass would still leave")
    print("  Stage 2 a long way off.")


if __name__ == "__main__":
    main()
