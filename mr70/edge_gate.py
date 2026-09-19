"""MR-70 step 3: does any variant actually beat a random entry on the same geometry?

A 70% win rate is meaningless on its own -- TP 0.75 ATR / SL 2.0 ATR hits TP first
about 71% of the time from a random entry, purely by geometry, with zero edge.
Break-even is 2.0/2.75 = 72.7% before costs. So the only question that matters is
whether a signal lifts the hit rate ABOVE the random baseline by enough to cover
break-even plus costs. The spec's gate: pooled lift >= +4 pp with the 95% CI lower
bound above zero.

Two deliberate choices, both of which make the gate stricter rather than looser:

- Signals are evaluated with the same non-overlap rule the backtest will trade
  under (one open position per symbol, then a cooldown). Overlapping signals are
  near-duplicate events; counting them all would leave the hit rate roughly right
  but shrink the confidence interval dishonestly, making a fluke easier to pass.
- A trade counts as a win ONLY if TP is hit before SL within the time stop.
  Time-stop exits count as not-wins, even though their P&L is usually small. The
  time-stop rate is reported separately so it stays visible.

Usage:
  python mr70/edge_gate.py --synthetic     # falsification run: everything must fail
  python mr70/edge_gate.py                 # real data, 4 symbols, 730 days
"""
import argparse
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from data import fetch_ohlcv, load_csv, save_csv, synthetic
from mr70.config import MR70Config
from mr70.indicators import build_features
from mr70.signals import VARIANTS


def cache_path(symbol: str) -> str:
    return f"{symbol.split('/')[0]}_15m.csv"


def resolve(f_arr, i: int, d: int, cfg, cost: float):
    """Walk one signal forward under the shared geometry.

    Returns None if the trade was never eligible (too small a TP vs fees, or not
    enough history left), otherwise a dict describing what happened.
    """
    o, h, l, c, atr = f_arr
    n = len(c)
    entry = c[i]
    a = atr[i]
    if not np.isfinite(a) or a <= 0:
        return None
    tp_dist = cfg.tp_atr * a
    if tp_dist / entry < cfg.min_tp_cost_mult * cost:
        return {"status": "tp_too_small_vs_fees"}
    if i + 1 + cfg.max_bars >= n:
        return None

    # limit order at the signal close, valid entry_valid_bars bars
    fill = None
    for j in range(i + 1, min(i + 1 + cfg.entry_valid_bars, n)):
        if (l[j] <= entry) if d == 1 else (h[j] >= entry):
            fill = j
            break
    if fill is None:
        return {"status": "unfilled"}

    tp = entry + d * tp_dist
    sl = entry - d * cfg.sl_atr * a
    last = min(fill + cfg.max_bars, n - 1)
    for j in range(fill, last + 1):
        hit_sl = (l[j] <= sl) if d == 1 else (h[j] >= sl)
        hit_tp = (h[j] >= tp) if d == 1 else (l[j] <= tp)
        if hit_sl:                      # SL wins ties within a bar (conservative)
            return {"status": "sl", "win": False, "exit_idx": j, "bars": j - fill}
        if hit_tp:
            return {"status": "tp", "win": True, "exit_idx": j, "bars": j - fill}
    return {"status": "time", "win": False, "exit_idx": last, "bars": last - fill}


def evaluate(f, signals, cfg, symbol: str):
    """Apply the non-overlap rule and resolve each surviving signal."""
    f_arr = tuple(f[k].to_numpy() for k in ("open", "high", "low", "close", "atr"))
    cost = cfg.round_trip_cost(symbol)
    out, busy_until = [], -1
    skipped_overlap = 0
    for i, d in signals:
        if i <= busy_until:
            skipped_overlap += 1
            continue
        r = resolve(f_arr, i, d, cfg, cost)
        if r is None:
            continue
        r["idx"], r["dir"] = i, d
        out.append(r)
        if r["status"] in ("tp", "sl", "time"):
            busy_until = r["exit_idx"] + cfg.cooldown_bars
    return out, skipped_overlap


def summarise(outcomes):
    traded = [r for r in outcomes if r["status"] in ("tp", "sl", "time")]
    wins = sum(1 for r in traded if r["win"])
    return {
        "signals": len(outcomes),
        "unfilled": sum(1 for r in outcomes if r["status"] == "unfilled"),
        "tp_too_small": sum(1 for r in outcomes if r["status"] == "tp_too_small_vs_fees"),
        "trades": len(traded),
        "wins": wins,
        "hit_rate": wins / len(traded) if traded else float("nan"),
        "time_stops": sum(1 for r in traded if r["status"] == "time"),
    }


def two_prop(x1, n1, x2, n2):
    """Signal vs random: difference in proportions with a 95% CI (percentage points)."""
    if n1 == 0 or n2 == 0:
        return float("nan"), float("nan"), float("nan")
    p1, p2 = x1 / n1, x2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    d = (p1 - p2) * 100
    return d, d - 1.96 * se * 100, d + 1.96 * se * 100


def run(cfg, use_synthetic: bool, refetch: bool = False):
    per = {}      # (variant, symbol) -> summary
    pooled = {v: {"wins": 0, "trades": 0} for v in VARIANTS}
    symbols = cfg.symbols

    for s_i, symbol in enumerate(symbols):
        if use_synthetic:
            df = synthetic(days=cfg.days, seed=100 + s_i)
            label = f"SYNTH:{symbol.split('/')[0]}"
        else:
            path = cache_path(symbol)
            df = None
            if not refetch:
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
        for vname, fn in VARIANTS.items():
            sig = fn(f, cfg)
            outcomes, overlap = evaluate(f, sig, cfg, symbol)
            su = summarise(outcomes)
            su["raw_signals"] = len(sig)
            su["skipped_overlap"] = overlap
            per[(vname, label)] = su
            pooled[vname]["wins"] += su["wins"]
            pooled[vname]["trades"] += su["trades"]
        print(f"  {label}: " + "  ".join(
            f"{v}={per[(v, label)]['trades']}tr/{per[(v, label)]['hit_rate']:.1%}"
            if per[(v, label)]["trades"] else f"{v}=0tr" for v in VARIANTS), flush=True)

    return per, pooled


def report(per, pooled, cfg, tag):
    print(f"\n{'='*78}\nEDGE GATE ({tag})\n{'='*78}")
    print(f"{'variant':<6}{'symbol':<18}{'raw':>7}{'trades':>8}{'hit':>8}{'unfill':>8}{'time':>7}")
    for (v, sym), su in per.items():
        hr = f"{su['hit_rate']:.1%}" if su["trades"] else "-"
        print(f"{v:<6}{sym:<18}{su['raw_signals']:>7}{su['trades']:>8}{hr:>8}"
              f"{su['unfilled']:>8}{su['time_stops']:>7}")

    base = pooled["V0"]
    bp = base["wins"] / base["trades"] if base["trades"] else float("nan")
    print(f"\nRandom baseline (V0), pooled: {base['trades']} trades, hit rate {bp:.2%}")
    print(f"Break-even for TP {cfg.tp_atr} / SL {cfg.sl_atr} ATR (before costs): "
          f"{cfg.sl_atr / (cfg.tp_atr + cfg.sl_atr):.2%}")

    print(f"\n{'variant':<8}{'trades':>8}{'hit':>9}{'lift pp':>10}{'95% CI':>20}{'verdict':>10}")
    verdicts = {}
    for v in VARIANTS:
        if v == "V0":
            continue
        p = pooled[v]
        if p["trades"] == 0:
            print(f"{v:<8}{0:>8}{'-':>9}{'-':>10}{'-':>20}{'NO SIGNALS':>10}")
            verdicts[v] = False
            continue
        hr = p["wins"] / p["trades"]
        d, lo, hi = two_prop(p["wins"], p["trades"], base["wins"], base["trades"])
        ok = (d >= cfg.gate_min_lift_pp) and (lo > 0)
        verdicts[v] = ok
        print(f"{v:<8}{p['trades']:>8}{hr:>8.1%}{d:>+10.2f}"
              f"{f'[{lo:+.2f}, {hi:+.2f}]':>20}{'PASS' if ok else 'fail':>10}")
    passed = [v for v, ok in verdicts.items() if ok]
    print(f"\nGate: lift >= +{cfg.gate_min_lift_pp:.0f} pp AND CI lower bound > 0")
    print(f"Passed: {passed if passed else 'none'}")
    return verdicts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true",
                    help="falsification run -- every variant MUST fail")
    ap.add_argument("--refetch", action="store_true")
    args = ap.parse_args()
    cfg = MR70Config()

    per, pooled = run(cfg, args.synthetic, args.refetch)
    verdicts = report(per, pooled, cfg, "SYNTHETIC random walk" if args.synthetic else "REAL DATA")

    if args.synthetic:
        bad = [v for v, ok in verdicts.items() if ok]
        print("\n--- falsification check ---")
        if bad:
            print(f"FAILED: {bad} passed on random-walk data. There is a bug.")
            sys.exit(1)
        print("ok  no variant beats random on synthetic data, as required")
        print("note: V3/V4 cannot fire on synthetic() (uniform volume, no 3x climaxes),")
        print("      so their falsification result here is vacuous, not evidence.")
