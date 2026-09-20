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

Usage:
  python shared/diagnostics.py               # real data (uses the cached CSVs)
  python shared/diagnostics.py --synthetic   # 6.3, plus the same tables on random walks
"""
import argparse
import copy
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import fetch_ohlcv, load_csv, save_csv, synthetic          # noqa: E402
from shared import strategy_b                                        # noqa: E402
from shared.config import ABConfig                                   # noqa: E402
from shared.features import build_features                           # noqa: E402
from shared.gate import evaluate, random_control, wilson             # noqa: E402

GENEROUS_BARS = 500          # section 6.2 asks for a horizon long enough to resolve
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
    if not res:
        return f"  {label:<{width}} no trades"
    n = len(res)
    w = sum(o["win"] for o in res)
    lo, hi = wilson(w, n)
    return (f"  {label:<{width}} n={n:<5} hit={w / n:>6.1%}  "
            f"CI [{lo:>5.1%}, {hi:>5.1%}]  time-stopped={sum(o['status'] == 'time' for o in res) / n:>5.1%}")


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
                   o["exit_idx"] - o["idx"], o["status"] == "time")
            loose_rows.append(row)
            if o["idx"] in wide:
                rejected_rows.append(row)

        for name, kw in (("full cascade", {}), ("no 30m sweep", {"require_sweep": False}),
                         ("no 1H zone", {"require_zone": False}),
                         ("break only", {"require_zone": False, "require_sweep": False})):
            a = strategy_b.cascade(f, cfg, **kw)
            ablations[name] += resolved(evaluate(f, a, cfg, symbol,
                                                 cfg.entry_valid_bars_b, cfg.max_bars_b))

        cs = random_control(f, cfg, strategy_b.regime_mask(f, cfg),
                            strategy_b.stop_for, seed_offset=i)
        control += resolved(evaluate(f, cs, cfg_loose, symbol,
                                     cfg.entry_valid_bars_b, GENEROUS_BARS))
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
    print("\n" + hit_line("pooled cascade", [{"win": r[1], "status": "time" if r[3] else "x"}
                                             for r in loose_rows]))
    print(hit_line("regime-matched control", control))
    print("\n  Both lines use the same lifted cap and horizon, so the gap between them")
    print("  is geometry plus edge; on synthetic data it is geometry alone.")

    print("\n" + "=" * 78)
    print(f"6.4  the stop_too_wide rejects, had they traded ({tag})")
    print("=" * 78)
    bucket_table(rejected_rows, "  rejected signals only")
    print("\n" + hit_line("pooled rejects", [{"win": r[1], "status": "time" if r[3] else "x"}
                                             for r in rejected_rows]))

    print("\n" + "=" * 78)
    print(f"6.5  per-ablation hit rates ({tag}, config as shipped)")
    print("=" * 78)
    for name, outs in ablations.items():
        print(hit_line(name, outs))
    print("\n  Reporting only. The spec forbids using an ablation to select anything.")

    if args.synthetic:
        drift_report(cfg)


if __name__ == "__main__":
    main()
