"""Diagnostics for STRATEGY_B_FINDINGS_v2 section 6.

Measurement only. Nothing here selects a parameter, changes a threshold, or
feeds back into the spec -- it answers questions the gate run left open. The
shipped config is never mutated; the loosened copies used below exist so that
rejected and truncated signals can be observed, not traded.

  6.1  real exit-reason split of the traded signals        (tp1 / sl / time)
  6.2  hit rate and bars-to-resolution by stop-width bucket, cap and horizon lifted
  6.3  synthetic generator drift and long/short split      (runs with --synthetic)
  6.4  what the stop_too_wide rejects would have done under a generous horizon
  6.5  per-ablation hit rates, not just signal counts
  3    zero-cost break-even per configuration, which no cost assumption can reach
  8.3  block-bootstrap intervals, which drop the independence assumption
  8.5  the trade count a given effect size actually needs

Usage:
  python shared/diagnostics.py               # real data (uses the cached CSVs)
  python shared/diagnostics.py --synthetic   # 6.3, plus the same tables on random walks
"""
import argparse
import copy
import pathlib
import sys

import numpy as np
import pandas as pd
from math import sqrt
from statistics import NormalDist

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import fetch_ohlcv, load_csv, save_csv, synthetic          # noqa: E402
from shared import strategy_b                                        # noqa: E402
from shared.config import ABConfig                                   # noqa: E402
from shared.features import build_features                           # noqa: E402
from shared.gate import evaluate, random_control, wilson             # noqa: E402

GENEROUS_BARS = 500          # section 6.2 asks for a horizon long enough to resolve
BLOCK_DAYS = 28              # bootstrap block: long enough to swallow a 4H-structure regime
BOOT_ITERS = 4000
BUCKETS = [(0.0, 2.5, "<= 2.5 ATR"), (2.5, 4.0, "2.5-4 ATR"),
           (4.0, 6.0, "4-6 ATR"), (6.0, np.inf, "> 6 ATR")]


def loosen(cfg, cap=False, fees=False):
    """A copy of the config with a rejection rule disabled, for observation only."""
    out = copy.deepcopy(cfg)
    if cap:
        out.max_stop_atr = float("inf")
    if fees:
        out.min_stop_cost_mult = 0.0
    return out


def load(symbol, cfg, use_synthetic, i):
    if use_synthetic:
        return synthetic(days=cfg.days, seed=400 + i), f"SYNTH:{symbol.split('/')[0]}"
    path = f"{symbol.split('/')[0]}_15m.csv"
    try:
        df = load_csv(path)
    except FileNotFoundError:
        print(f"  fetching {symbol} ({cfg.days}d)...", flush=True)
        df = fetch_ohlcv(symbol, "15m", cfg.days, cfg.exchange_id)
        save_csv(df, path)
    return df, symbol


def resolved(outcomes):
    return [o for o in outcomes if o["status"] in ("tp1", "sl", "time")]


def hit_line(label, res, width=26):
    """Widening the stop lowers the cost-to-risk ratio, so the break-even rate a set
    of trades must clear is not fixed -- it is reported alongside, per set."""
    if not res:
        return f"  {label:<{width}} no trades"
    n = len(res)
    w = sum(o["win"] for o in res)
    lo, hi = wilson(w, n)
    req = [o["required"] for o in res if "required" in o]
    tail = f"  required={np.mean(req):>5.1%}" if req else ""
    return (f"  {label:<{width}} n={n:<5} hit={w / n:>6.1%}  "
            f"CI [{lo:>5.1%}, {hi:>5.1%}]  "
            f"time-stopped={sum(o['status'] == 'time' for o in res) / n:>5.1%}{tail}")


def block_bootstrap(rows, block_days=BLOCK_DAYS, iters=BOOT_ITERS, seed=11):
    """A confidence interval that does not assume trades are independent.

    A Wilson interval treats every trade as its own observation. These are not:
    the four symbols are strongly correlated and the cascade keys off 4H
    structure, so signals arrive in clusters. Resampling contiguous stretches of
    calendar time keeps each cluster intact, which is what widens the interval
    to something honest.

    Blocks overlap and wrap around the end of the sample (a moving-block
    bootstrap). Cutting the sample into ~26 fixed blocks instead leaves the
    interval width itself carrying about 15% sampling error, which is too noisy
    to quote; drawing from every possible start removes that.

    rows: (timestamp, win) per trade, pooled across symbols.
    Returns (lo, hi, effective_blocks_drawn_per_resample).
    """
    if not rows:
        return float("nan"), float("nan"), 0
    order = np.argsort([r[0].value for r in rows])
    ts = np.array([rows[i][0].value for i in order])
    win = np.array([bool(rows[i][1]) for i in order])
    n = len(win)
    span = ts[-1] - ts[0]
    width = pd.Timedelta(days=block_days).value
    if span <= width:
        lo, hi = wilson(int(win.sum()), n)
        return lo, hi, 1

    # Wrapping the series makes every timestamp an equally likely block start,
    # so no part of the sample is systematically under-represented.
    ts2 = np.concatenate([ts, ts + span])
    win2 = np.concatenate([win, win])
    rng = np.random.default_rng(seed)
    per_resample = max(1, round(span / width))
    draws = []
    for _ in range(iters):
        starts = ts[0] + rng.random(per_resample) * span
        lo_i = np.searchsorted(ts2, starts)
        hi_i = np.searchsorted(ts2, starts + width)
        total = wins = 0
        for a, b in zip(lo_i, hi_i):
            wins += int(win2[a:b].sum())
            total += b - a
        if total:
            draws.append(wins / total)
    return (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5)),
            per_resample)


def clustering_factor(rows, shuffles=9, seed=23):
    """How much the independence assumption understates the uncertainty.

    The bootstrap above carries its own bias -- roughly +/-15% at these sample
    sizes, and it narrows slightly as trades get dense. Comparing a set against
    a Wilson interval would fold that bias into the answer. Comparing it against
    a label-shuffled copy of itself does not: the shuffle keeps the timestamps,
    the sample size and the overall hit rate, and destroys only the clustering,
    so the bias cancels and what is left is the clustering alone.

    Returns (width_actual, width_shuffled, ratio).
    """
    lo, hi, _ = block_bootstrap(rows)
    actual = hi - lo
    rng = np.random.default_rng(seed)
    wins = np.array([r[1] for r in rows])
    widths = []
    for _ in range(shuffles):
        sh = rng.permutation(wins)
        blo, bhi, _ = block_bootstrap([(r[0], bool(w)) for r, w in zip(rows, sh)])
        widths.append(bhi - blo)
    null = float(np.median(widths))
    return actual, null, (actual / null if null > 0 else float("nan"))


def zero_cost_breakeven(outs):
    """The hit rate this exit needs when fees and slippage are set to zero.

    A plain 1:1 target-and-stop breaks even at 50%. The spec does not trade that:
    it closes half the position at TP1, moves the stop to entry, and runs the
    remainder to 2R. Writing q for the share of winners whose runner reaches 2R,
    a win returns 0.5 + q and a loss returns 1, so break-even solves

        p(0.5 + q) = 1 - p     ->     p = 1 / (1.5 + q)

    which is 50% only when q happens to be 50%. This matters because a hit rate
    below its zero-cost line cannot be rescued by a better fee tier, a tighter
    spread or a kinder slippage model -- there is no edge there to recover.

    Returns (q, break_even) and (nan, nan) when nothing won.
    """
    wins = [o for o in outs if o["win"]]
    if not wins:
        return float("nan"), float("nan")
    q = sum(bool(o["tp2"]) for o in wins) / len(wins)
    return q, 1.0 / (1.5 + q)


def trades_needed(p0: float, p1: float, power: float = 0.80, alpha: float = 0.05):
    """Normal-approximation sample size for distinguishing p1 from p0.

    Returns (one_sample, two_sample_per_arm). The one-sample figure is the
    question "does this hit rate clear its break-even rate"; the two-sample
    figure is "does this hit rate beat a placebo arm", which costs roughly
    twice as much because both sides are estimated.
    """
    nd = NormalDist()
    za, zb = nd.inv_cdf(1 - alpha / 2), nd.inv_cdf(power)
    d = abs(p1 - p0)
    if d == 0:
        return float("inf"), float("inf")
    one = (za * sqrt(p0 * (1 - p0)) + zb * sqrt(p1 * (1 - p1))) ** 2 / d ** 2
    two = (za + zb) ** 2 * (p0 * (1 - p0) + p1 * (1 - p1)) / d ** 2
    return one, two


def bucket_table(rows, title):
    """rows: (width_in_atr, won, bars_to_exit, timed_out) for each resolved trade."""
    print(f"\n{title}")
    print(f"  {'stop width':<12} {'n':>5} {'hit':>7} {'timed out':>10} "
          f"{'median':>7} {'p75':>6} {'p90':>6}")
    for lo, hi, name in BUCKETS:
        sel = [r for r in rows if lo < r[0] <= hi] if lo else [r for r in rows if r[0] <= hi]
        if not sel:
            print(f"  {name:<12} {'-':>5}")
            continue
        bars = np.array([r[2] for r in sel])
        print(f"  {name:<12} {len(sel):>5} {np.mean([r[1] for r in sel]):>7.1%} "
              f"{np.mean([r[3] for r in sel]):>10.1%} "
              f"{np.median(bars):>7.0f} {np.percentile(bars, 75):>6.0f} "
              f"{np.percentile(bars, 90):>6.0f}")


def drift_report(cfg):
    """6.3 -- is the falsification generator driftless, and is the cascade lopsided?"""
    print("\n" + "=" * 78)
    print("6.3  synthetic generator drift and cascade long/short split")
    print("=" * 78)
    print(f"  {'seed':>6} {'realised drift over the run':>28} {'4H-up bars':>11} "
          f"{'long':>6} {'short':>6}")
    tot_l = tot_s = 0
    for i, symbol in enumerate(cfg.symbols):
        df = synthetic(days=cfg.days, seed=400 + i)
        ret = np.diff(np.log(df["close"].to_numpy()))
        f = build_features(df, cfg)
        sigs = strategy_b.cascade(f, cfg)
        nl = sum(1 for s in sigs if s["dir"] == 1)
        ns = len(sigs) - nl
        tot_l, tot_s = tot_l + nl, tot_s + ns
        print(f"  {400 + i:>6} {100 * (np.exp(ret.sum()) - 1):>27.1f}% "
              f"{np.mean(f['trend_4h'].to_numpy() == 1):>11.1%} {nl:>6} {ns:>6}")
    print(f"\n  pooled: {tot_l} long / {tot_s} short "
          f"({tot_l / max(tot_l + tot_s, 1):.1%} long)")
    print("  The generator's drift term is a block-persistent regime, so any single")
    print("  seed shows large realised drift. Whether that drift reaches the cascade")
    print("  is answered by the regime-matched control below, not by this table.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()
    cfg = ABConfig()
    tag = "SYNTHETIC" if args.synthetic else "REAL DATA"

    shipped = []            # 6.1: outcomes under the config as pre-registered
    loose_rows = []         # 6.2: cap and horizon lifted
    rejected_rows = []      # 6.4: only the signals the cap threw away
    ablations = {"full cascade": [], "no 30m sweep": [], "no 1H zone": [],
                 "break only": []}
    control = []
    boot = {"pooled cascade (cap lifted)": [], "regime-matched control": [],
            "break only (as shipped)": []}      # (timestamp, win) for section 8.3

    cfg_loose = loosen(cfg, cap=True, fees=True)

    for i, symbol in enumerate(cfg.symbols):
        df, label = load(symbol, cfg, args.synthetic, i)
        f = build_features(df, cfg)
        atr = f["atr"].to_numpy()
        sigs = strategy_b.cascade(f, cfg)

        shipped += evaluate(f, sigs, cfg, symbol, cfg.entry_valid_bars_b, cfg.max_bars_b)

        wide = {s["idx"] for s in sigs
                if np.isfinite(atr[s["idx"]]) and atr[s["idx"]] > 0
                and abs(s["entry"] - s["stop"]) > cfg.max_stop_atr * atr[s["idx"]]}
        for o in resolved(evaluate(f, sigs, cfg_loose, symbol,
                                   cfg.entry_valid_bars_b, GENEROUS_BARS)):
            row = (abs(o["entry"] - o["stop"]) / atr[o["idx"]], bool(o["win"]),
                   o["exit_idx"] - o["idx"], o["status"] == "time", o["required"])
            loose_rows.append(row)
            boot["pooled cascade (cap lifted)"].append(
                (f["close_time"].iloc[o["idx"]], bool(o["win"])))
            if o["idx"] in wide:
                rejected_rows.append(row)

        for name, kw in (("full cascade", {}), ("no 30m sweep", {"require_sweep": False}),
                         ("no 1H zone", {"require_zone": False}),
                         ("break only", {"require_zone": False, "require_sweep": False})):
            a = strategy_b.cascade(f, cfg, **kw)
            outs = resolved(evaluate(f, a, cfg, symbol,
                                     cfg.entry_valid_bars_b, cfg.max_bars_b))
            ablations[name] += outs
            if name == "break only":
                boot["break only (as shipped)"] += [
                    (f["close_time"].iloc[o["idx"]], bool(o["win"])) for o in outs]

        cs = random_control(f, cfg, strategy_b.regime_mask(f, cfg),
                            strategy_b.stop_for, seed_offset=i)
        cs_out = resolved(evaluate(f, cs, cfg_loose, symbol,
                                   cfg.entry_valid_bars_b, GENEROUS_BARS))
        control += cs_out
        boot["regime-matched control"] += [
            (f["close_time"].iloc[o["idx"]], bool(o["win"])) for o in cs_out]
        print(f"  {label}: {len(sigs)} signals", flush=True)

    print("\n" + "=" * 78)
    print(f"6.1  exit-reason split of the traded signals ({tag}, config as shipped)")
    print("=" * 78)
    res = resolved(shipped)
    if res:
        for st, name in (("tp1", "TP1 (win)"), ("sl", "stop"), ("time", "time stop")):
            k = sum(o["status"] == st for o in res)
            print(f"  {name:<14} {k:>5}  ({k / len(res):>5.1%})")
        print(f"  {'total':<14} {len(res):>5}")
        print("\n  A small time-stop share kills the truncation hypothesis for real data:")
        print("  it would mean the 96-bar limit is not what held the hit rate down.")

    print("\n" + "=" * 78)
    print(f"6.2  stop width vs outcome ({tag}, cap lifted, {GENEROUS_BARS}-bar horizon)")
    print("=" * 78)
    bucket_table(loose_rows, "  cascade signals")
    print("\n" + hit_line("pooled cascade",
                             [{"win": r[1], "status": "time" if r[3] else "x",
                               "required": r[4]} for r in loose_rows]))
    print(hit_line("regime-matched control", control))
    print("\n  Both lines use the same lifted cap and horizon, so the gap between them")
    print("  is geometry plus edge; on synthetic data it is geometry alone.")

    print("\n" + "=" * 78)
    print(f"6.4  the stop_too_wide rejects, had they traded ({tag})")
    print("=" * 78)
    bucket_table(rejected_rows, "  rejected signals only")
    print("\n" + hit_line("pooled rejects",
                             [{"win": r[1], "status": "time" if r[3] else "x",
                               "required": r[4]} for r in rejected_rows]))

    print("\n" + "=" * 78)
    print(f"6.5  per-ablation hit rates ({tag}, config as shipped)")
    print("=" * 78)
    for name, outs in ablations.items():
        print(hit_line(name, outs))
    print("\n  Reporting only. The spec forbids using an ablation to select anything.")

    print("\n" + "=" * 78)
    print(f"3  zero-cost break-even per configuration ({tag})")
    print("=" * 78)
    print(f"  {'configuration':<16} {'n':>5} {'hit':>7} {'Wilson 95%':>16} "
          f"{'runner q':>9} {'zero-cost':>10} {'w/ costs':>9}  verdict")
    for name, outs in ablations.items():
        if not outs:
            continue
        n = len(outs)
        w = sum(o["win"] for o in outs)
        lo, hi = wilson(w, n)
        q, be = zero_cost_breakeven(outs)
        req = float(np.mean([o["required"] for o in outs]))
        if hi < be:
            verdict = "whole CI below zero-cost line"
        elif w / n < be:
            verdict = "point est. below, CI top above"
        else:
            verdict = "at or above zero-cost line"
        print(f"  {name:<16} {n:>5} {w / n:>7.1%} {f'[{lo:.1%}, {hi:.1%}]':>16} "
              f"{q:>9.1%} {be:>10.1%} {req:>9.1%}  {verdict}")
    print("\n  'zero-cost' is 1/(1.5+q) for this configuration's own runner conversion.")
    print("  A configuration below its own zero-cost line is not a marginal edge that")
    print("  fees destroyed; it has no edge to destroy, and no fee tier reaches it.")

    print("\n" + "=" * 78)
    print(f"8.3  block-bootstrap intervals ({tag}, {BLOCK_DAYS}-day blocks, "
          f"{BOOT_ITERS} resamples)")
    print("=" * 78)
    print(f"  {'set':<30} {'n':>5} {'hit':>7} {'Wilson 95%':>18} "
          f"{'bootstrap 95%':>18} {'vs shuffled':>12}")
    for name, rows in boot.items():
        if not rows:
            continue
        n = len(rows)
        w = sum(r[1] for r in rows)
        wl, wh = wilson(w, n)
        bl, bh, _ = block_bootstrap(rows)
        _, _, ratio = clustering_factor(rows)
        print(f"  {name:<30} {n:>5} {w / n:>7.1%} "
              f"{f'[{wl:.1%}, {wh:.1%}]':>18} {f'[{bl:.1%}, {bh:.1%}]':>18} "
              f"{ratio:>11.2f}x")
    print("\n  The last column is the clustering cost, measured against a label-shuffled")
    print("  copy of the same trades rather than against Wilson, so the bootstrap's own")
    print("  bias cancels. 1.00x means the trades carry no more information together")
    print("  than they would scattered at random, i.e. the independence assumption was")
    print("  harmless; above 1.00x, every Wilson interval elsewhere in this output is")
    print("  too narrow by about that factor and the conclusions drawn from them are")
    print("  correspondingly overconfident.")
    print("\n  Resolution: with ~26 blocks in 730 days this ratio carries about +/-25%")
    print("  noise. It separates 'roughly none' from 'substantial'; it cannot tell")
    print("  1.0x from 1.3x. Read it as an order of magnitude, not a correction factor.")

    print("\n" + "=" * 78)
    print("8.5  trades needed, by effect size (80% power, alpha 0.05)")
    print("=" * 78)
    print(f"  {'edge over a 50% baseline':<26} {'vs break-even':>14} {'vs a placebo arm':>18}")
    for d in (0.026, 0.03, 0.04, 0.05, 0.08, 0.10):
        one, two = trades_needed(0.50, 0.50 + d)
        print(f"  {f'+{100 * d:.1f} pp':<26} {one:>14,.0f} {f'{two:,.0f} per arm':>18}")
    print(f"\n  This run produced {len(loose_rows)} resolved trades from "
          f"{cfg.days} days x {len(cfg.symbols)} symbols.")
    print("  Read the table against that number before designing a follow-up: an edge")
    print("  that needs more trades than the data can ever supply is not a finding")
    print("  waiting to be confirmed, it is a question this design cannot ask.")

    if args.synthetic:
        drift_report(cfg)


if __name__ == "__main__":
    main()
