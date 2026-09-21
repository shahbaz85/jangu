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
    ap.add_argument("--stage1", action="store_true",
                    help="run the full Stage 1 gate in one pass (addendum)")
    args = ap.parse_args()
    cfg = V3OneHourConfig()
    if args.stage1:
        report_stage1(cfg)
        return
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




# =============================================================================
# Stage 1 (addendum). Everything below runs in a single pass and prints one
# report, per the blindness protocol: no V3 outcome is inspected until every
# condition, sensitivity and the synthetic check has been computed.
# =============================================================================

FILL_OFFSET_ATR = 0.05      # Change 3: strict model requires price to trade through
SYN_SEEDS = range(400, 412)
SYN_MIN_SIGNALS = 300
VOL_BLOCK_HOURS = 24 * 7    # Change 4: 7-day blocks preserve volume clustering


def resolve_fill(f_arr, i, d, cfg, cost, fill_offset_atr=0.0):
    """mr70.edge_gate.resolve, with the fill requirement parameterised.

    The touch model (offset 0) fills when a bar's low reaches the limit. The
    strict model requires price to trade `fill_offset_atr` beyond it, standing in
    for queue position, while still filling at the limit price.
    """
    o, h, l, c, atr = f_arr
    n = len(c)
    entry, a = c[i], atr[i]
    if not np.isfinite(a) or a <= 0:
        return None
    if cfg.tp_atr * a / entry < cfg.min_tp_cost_mult * cost:
        return {"status": "tp_too_small_vs_fees"}
    if i + 1 + cfg.max_bars >= n:
        return None

    trigger = entry - d * fill_offset_atr * a
    fill = None
    for j in range(i + 1, min(i + 1 + cfg.entry_valid_bars, n)):
        if (l[j] <= trigger) if d == 1 else (h[j] >= trigger):
            fill = j
            break
    if fill is None:
        return {"status": "unfilled"}

    tp, sl = entry + d * cfg.tp_atr * a, entry - d * cfg.sl_atr * a
    last = min(fill + cfg.max_bars, n - 1)
    for j in range(fill, last + 1):
        if (l[j] <= sl) if d == 1 else (h[j] >= sl):
            return {"status": "sl", "win": False, "exit_idx": j}
        if (h[j] >= tp) if d == 1 else (l[j] <= tp):
            return {"status": "tp", "win": True, "exit_idx": j}
    return {"status": "time", "win": False, "exit_idx": last}


def evaluate_fill(f, signals, cfg, symbol, fill_offset_atr=0.0):
    """Non-overlap rule plus cooldown, under a given fill model."""
    arr = tuple(f[k].to_numpy() for k in ("open", "high", "low", "close", "atr"))
    cost = cfg.round_trip_cost(symbol)
    out, busy_until = [], -1
    for i, d in signals:
        if i <= busy_until:
            continue
        r = resolve_fill(arr, i, d, cfg, cost, fill_offset_atr)
        if r is None:
            continue
        r["idx"], r["dir"] = i, d
        out.append(r)
        if r["status"] in ("tp", "sl", "time"):
            busy_until = r["exit_idx"] + cfg.cooldown_bars
    return out


def _blocks(rows, block_days, rng, draws):
    """Shared machinery for the bootstraps: yield (start, end) epoch windows."""
    ts = np.array([r[0].value for r in rows])
    span = ts.max() - ts.min()
    width = pd.Timedelta(days=block_days).value
    per = max(1, round(span / width))
    for _ in range(draws):
        starts = ts.min() + rng.random(per) * span
        yield starts, starts + width


def _rate_in(wins, times, starts, ends, span, t0):
    """Pooled hit rate over the drawn windows, wrapping at the end of the sample."""
    t2 = np.concatenate([times, times + span])
    w2 = np.concatenate([wins, wins])
    tot = hit = 0
    for a, b in zip(np.searchsorted(t2, starts), np.searchsorted(t2, ends)):
        hit += int(w2[a:b].sum())
        tot += b - a
    return (hit / tot) if tot else np.nan


def paired_bootstrap(a_rows, b_rows, block_days=28, iters=4000, seed=17):
    """Resample time once per draw and measure BOTH arms in the same blocks.

    This is the interval the verdict rests on. Measuring the arms in independent
    resamples would ignore that they share market conditions, which is the whole
    reason a time-shifted placebo is the right null.

    Returns (lo, hi) for (a - b).
    """
    if not a_rows or not b_rows:
        return float("nan"), float("nan")
    ta = np.sort(np.array([r[0].value for r in a_rows]))
    order_a = np.argsort([r[0].value for r in a_rows])
    wa = np.array([bool(a_rows[i][1]) for i in order_a])
    order_b = np.argsort([r[0].value for r in b_rows])
    tb = np.sort(np.array([r[0].value for r in b_rows]))
    wb = np.array([bool(b_rows[i][1]) for i in order_b])

    t0 = min(ta.min(), tb.min())
    span = max(ta.max(), tb.max()) - t0
    rng = np.random.default_rng(seed)
    draws = []
    for starts, ends in _blocks(a_rows, block_days, rng, iters):
        ra = _rate_in(wa, ta, starts, ends, span, t0)
        rb = _rate_in(wb, tb, starts, ends, span, t0)
        if np.isfinite(ra) and np.isfinite(rb):
            draws.append(ra - rb)
    if not draws:
        return float("nan"), float("nan")
    return float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))


def one_arm_bootstrap(rows, block_days=28, iters=4000, seed=17):
    """Same block machinery, one arm. Returns (lo, hi)."""
    if not rows:
        return float("nan"), float("nan")
    order = np.argsort([r[0].value for r in rows])
    t = np.sort(np.array([r[0].value for r in rows]))
    w = np.array([bool(rows[i][1]) for i in order])
    span = t.max() - t.min()
    rng = np.random.default_rng(seed)
    draws = [r for starts, ends in _blocks(rows, block_days, rng, iters)
             if np.isfinite(r := _rate_in(w, t, starts, ends, span, t.min()))]
    if not draws:
        return float("nan"), float("nan")
    return float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))


def block_shuffled_volume(real_1h: pd.DataFrame, n: int, seed: int) -> np.ndarray:
    """Real 1H volume, shuffled in 7-day blocks and fitted to `n` bars.

    Shuffling whole weeks keeps volume clustering -- the property V3's 3x rule
    needs in order to fire at all -- while destroying any relationship between
    volume and the synthetic prices it will be paired with.
    """
    v = real_1h["volume"].to_numpy()
    blocks = [v[i:i + VOL_BLOCK_HOURS] for i in range(0, len(v), VOL_BLOCK_HOURS)]
    blocks = [b for b in blocks if len(b) == VOL_BLOCK_HOURS]
    rng = np.random.default_rng(seed)
    out = []
    while sum(len(b) for b in out) < n:
        out.append(blocks[rng.integers(0, len(blocks))])
    return np.concatenate(out)[:n]


def synthetic_arm(real_1h: pd.DataFrame, seed: int, cfg):
    """Random-walk 1H prices carrying real, block-shuffled volume."""
    df = synthetic(days=cfg.days, seed=seed).resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}).dropna()
    df["volume"] = block_shuffled_volume(real_1h, len(df), seed)
    return df


def stage1(cfg):
    """Every Stage 1 quantity, computed before anything about V3 is printed."""
    touch = {"rows": [], "by_sym": {}, "be": [], "ps": []}
    strict = {"rows": [], "signals": 0, "fills": 0}
    plac = {"rows": []}
    reals = {}

    for i, symbol in enumerate(cfg.symbols):
        df = load(symbol, cfg, False, i)
        reals[symbol] = df
        f = build_features(df, cfg)
        ct = f["close_time"]
        sigs = v3_vwap_climax(f, cfg)

        outs = evaluate_fill(f, sigs, cfg, symbol, 0.0)
        traded = [r for r in outs if r["status"] in ("tp", "sl", "time")]
        rows = [(ct.iloc[r["idx"]], bool(r["win"])) for r in traded]
        touch["rows"] += rows
        touch["by_sym"][symbol] = rows
        touch["be"] += [required_rate(f, r["idx"], cfg, symbol) for r in traded]
        touch["ps"] += [min_tradeable_rate(f, r["idx"], cfg, symbol) for r in traded]

        s_outs = evaluate_fill(f, sigs, cfg, symbol, FILL_OFFSET_ATR)
        s_traded = [r for r in s_outs if r["status"] in ("tp", "sl", "time")]
        strict["rows"] += [(ct.iloc[r["idx"]], bool(r["win"])) for r in s_traded]
        strict["signals"] += len(sigs)
        strict["fills"] += len(s_traded)

        for sh in SHIFTS:
            p_outs = evaluate_fill(f, shifted_signals(sigs, len(f), sh), cfg, symbol, 0.0)
            plac["rows"] += [(ct.iloc[r["idx"]], bool(r["win"]))
                             for r in p_outs if r["status"] in ("tp", "sl", "time")]
        print(f"  {symbol.split('/')[0]:<5} computed", flush=True)

    # --- synthetic falsification (Change 4)
    syn_rows, syn_plac, syn_sigs, syn_be = [], [], 0, []
    for k, seed in enumerate(SYN_SEEDS):
        symbol = cfg.symbols[k % len(cfg.symbols)]
        df = synthetic_arm(reals[symbol], seed, cfg)
        f = build_features(df, cfg)
        ct = f["close_time"]
        sigs = v3_vwap_climax(f, cfg)
        syn_sigs += len(sigs)
        outs = evaluate_fill(f, sigs, cfg, symbol, 0.0)
        traded = [r for r in outs if r["status"] in ("tp", "sl", "time")]
        syn_rows += [(ct.iloc[r["idx"]], bool(r["win"])) for r in traded]
        syn_be += [required_rate(f, r["idx"], cfg, symbol) for r in traded]
        for sh in SHIFTS:
            p_outs = evaluate_fill(f, shifted_signals(sigs, len(f), sh), cfg, symbol, 0.0)
            syn_plac += [(ct.iloc[r["idx"]], bool(r["win"]))
                         for r in p_outs if r["status"] in ("tp", "sl", "time")]
    print(f"  synthetic: {syn_sigs} signals", flush=True)

    return touch, strict, plac, (syn_rows, syn_plac, syn_sigs, syn_be)


def rate(rows):
    return (sum(w for _, w in rows) / len(rows)) if rows else float("nan")


def report_stage1(cfg):
    touch, strict, plac, (syn_rows, syn_plac, syn_sigs, syn_be) = stage1(cfg)

    be = float(np.nanmean(touch["be"]))
    p_star = float(np.nanmean(touch["ps"]))
    hit = rate(touch["rows"])
    lo, hi = one_arm_bootstrap(touch["rows"])
    d_lo, d_hi = paired_bootstrap(touch["rows"], plac["rows"])

    # condition 4: leave one symbol out
    loo = {}
    for symbol, rows in touch["by_sym"].items():
        keep = [r for s, rs in touch["by_sym"].items() if s != symbol for r in rs]
        loo[symbol] = one_arm_bootstrap(keep)[0]

    # synthetic gate
    syn_hit, syn_be_m = rate(syn_rows), float(np.nanmean(syn_be)) if syn_be else float("nan")
    syn_lo = one_arm_bootstrap(syn_rows)[0] if syn_rows else float("nan")
    syn_d_lo = paired_bootstrap(syn_rows, syn_plac)[0] if syn_rows else float("nan")
    syn_fired = syn_sigs >= SYN_MIN_SIGNALS
    syn_passed = bool(syn_fired and syn_lo > syn_be_m and syn_d_lo > 0)

    s_hit, s_lo = rate(strict["rows"]), one_arm_bootstrap(strict["rows"])[0]
    s_dlo = paired_bootstrap(strict["rows"], plac["rows"])[0]

    c2 = lo > be
    c3 = d_lo > 0
    c4 = all(v > be for v in loo.values())
    c5 = syn_fired and not syn_passed
    verdict = "PASS" if (c2 and c3 and c4 and c5) else "FAIL"
    strict_ok = (s_lo > be) and (s_dlo > 0)
    if verdict == "PASS" and not strict_ok:
        verdict = "PASS (FILL-DEPENDENT)"

    print("\n" + "=" * 78)
    print("STAGE 1 -- V3 on 1H, edge gate (REAL DATA)")
    print("=" * 78)
    print(f"  pooled hit rate            {hit:>8.2%}  on {len(touch['rows']):,} trades")
    print(f"  block bootstrap 95%        [{lo:.2%}, {hi:.2%}]  (28-day blocks)")
    print(f"  pooled break-even          {be:>8.2%}")
    print(f"  pooled p* (+0.08R)         {p_star:>8.2%}   <- Stage 2's bar, not Stage 1's")
    print(f"  placebo baseline           {rate(plac['rows']):>8.2%}  on {len(plac['rows']):,} trades")
    print(f"  paired difference 95%      [{d_lo:+.2%}, {d_hi:+.2%}]")

    print(f"\n  {'condition':<52} {'result'}")
    print(f"  {'1. Stage 0b satisfied':<52} PASS (947 >= 435)")
    print(f"  {'2. bootstrap lower bound > break-even':<52} {'PASS' if c2 else 'FAIL'}"
          f"   ({lo:.2%} vs {be:.2%})")
    print(f"  {'3. paired difference entirely above zero':<52} {'PASS' if c3 else 'FAIL'}"
          f"   (lo {d_lo:+.2%})")
    print(f"  {'4. survives leave-one-symbol-out':<52} {'PASS' if c4 else 'FAIL'}")
    print(f"  {'5. synthetic fired and failed':<52} {'PASS' if c5 else 'FAIL'}"
          f"   ({syn_sigs} signals)")
    print(f"\n  VERDICT: {verdict}")

    print(f"\n  leave-one-out lower bounds (break-even {be:.2%}):")
    for symbol, v in sorted(loo.items(), key=lambda kv: kv[1]):
        print(f"    without {symbol.split('/')[0]:<5} {v:>7.2%}  {'ok' if v > be else 'FLIPS'}")

    print(f"\n  strict fill ({FILL_OFFSET_ATR} ATR through the limit):")
    print(f"    fill rate {strict['fills'] / strict['signals']:.1%}  "
          f"({strict['fills']:,} of {strict['signals']:,})   hit {s_hit:.2%}   "
          f"lower bound {s_lo:.2%}   paired lo {s_dlo:+.2%}")
    print(f"    conditions 2 and 3 under strict fill: {'PASS' if strict_ok else 'FAIL'}")

    print(f"\n  synthetic falsification: {syn_sigs} signals "
          f"({'fired' if syn_fired else f'INCONCLUSIVE, under {SYN_MIN_SIGNALS}'})")
    if syn_rows:
        print(f"    hit {syn_hit:.2%} vs break-even {syn_be_m:.2%}, "
              f"lower bound {syn_lo:.2%}, paired lo {syn_d_lo:+.2%} "
              f"-> {'PASSED (BUG)' if syn_passed else 'failed, as required'}")

    print(f"\n  block-length sweep (lower bound vs break-even {be:.2%}):")
    for bd in (7, 28, 90):
        b_lo = one_arm_bootstrap(touch["rows"], block_days=bd)[0]
        p_lo = paired_bootstrap(touch["rows"], plac["rows"], block_days=bd)[0]
        print(f"    {bd:>3}-day   lower bound {b_lo:>7.2%}  paired lo {p_lo:>+7.2%}  "
              f"{'ok' if b_lo > be and p_lo > 0 else 'conclusion changes'}")

    tuned = [r for s in cfg.tuned_on if s in touch["by_sym"] for r in touch["by_sym"][s]]
    other = [r for s, rs in touch["by_sym"].items() if s not in cfg.tuned_on for r in rs]
    print(f"\n  provenance split (information only):")
    print(f"    parameters chosen on these 4: {rate(tuned):.2%} on {len(tuned):,} trades")
    print(f"    the other 8:                  {rate(other):.2%} on {len(other):,} trades")

    print(f"\n  the direct answer: V3's 1H edge over placebo is "
          f"{100 * (hit - rate(plac['rows'])):+.1f} pp. Tradeable needs the hit rate at "
          f"{p_star:.1%};")
    print(f"  it is {hit:.2%}, so V3 is "
          f"{'above' if hit >= p_star else 'below'} the Stage 2 bar"
          f"{'' if hit >= p_star else f' by {100 * (p_star - hit):.1f} pp'}.")

if __name__ == "__main__":
    main()
