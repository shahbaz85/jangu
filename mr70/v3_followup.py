"""MR-70 V3 follow-up (pre-registered in MR70_V3_FOLLOWUP.md).

One hypothesis: does V3 have an edge large enough to beat costs once the target
and stop are large relative to fees?

Fresh symbols only -- BTC, AVAX, XRP, ADA. V3 was *selected* on BNB/ETH/SOL/DOGE,
so those are burnt and are not touched here.

Geometry is fixed at TP 1.5 ATR / SL 3.0 ATR / 32-bar time stop. Nothing is
optimised, nothing is tuned toward the result.

The gate is absolute, not relative: for every signal the actual cost of a win and
of a loss is expressed in ATR units and turned into the hit rate that trade would
need to break even. V3 must clear that bar with the Wilson 95% lower bound, not
just the point estimate.

Usage:
  python mr70/v3_followup.py --synthetic   # falsification: V3 must NOT pass
  python mr70/v3_followup.py               # real data, 4 fresh symbols
"""
import argparse
import math
import pathlib
import sys
from dataclasses import replace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np

from data import fetch_ohlcv, load_csv, save_csv, synthetic
from mr70.config import MR70Config
from mr70.edge_gate import cache_path, evaluate, resolve
from mr70.indicators import build_features
from mr70.signals import v0_random, v3_vwap_climax

FOLLOWUP_SYMBOLS = ["BTC/USDT:USDT", "AVAX/USDT:USDT", "XRP/USDT:USDT", "ADA/USDT:USDT"]
MIN_POOLED = 300


def followup_config(geometry: str = "new") -> MR70Config:
    """Main spec, with only what the addendum changes.

    geometry="new"  -> the addendum's TP 1.5 / SL 3.0 ATR, 32-bar stop.
    geometry="old"  -> the main spec's TP 0.75 / SL 2.0 ATR, 16-bar stop, run on
                       the FRESH symbols. Requested explicitly to separate two
                       confounded explanations for the follow-up's reversal:
                       a geometry effect, or selection noise in the original
                       +4.14 pp that simply failed to replicate out of sample.
                       Only the symbols differ from the original measurement.
    """
    geo = {"new": (1.5, 3.0, 32), "old": (0.75, 2.0, 16)}[geometry]
    return replace(
        MR70Config(),
        symbols=FOLLOWUP_SYMBOLS,
        tp_atr=geo[0],
        sl_atr=geo[1],
        max_bars=geo[2],
        slippage=0.0002,                                   # BTC
        slippage_high=0.0004,                              # AVAX, XRP, ADA
        high_slip_symbols=("AVAX/USDT:USDT", "XRP/USDT:USDT", "ADA/USDT:USDT"),
    )


def break_even(entry: float, atr_i: float, cfg, symbol: str):
    """Per-signal break-even hit rate, with costs expressed in ATR units.

    cw = maker entry + maker TP       (a win pays maker twice)
    cl = maker entry + taker SL + slip (a loss exits at market)
    be = (SL + cl) / (TP + SL + cl - cw)
    """
    scale = entry / atr_i                                  # fraction-of-price -> ATR units
    cw = (2 * cfg.maker_fee) * scale
    cl = (cfg.maker_fee + cfg.taker_fee + cfg.slip_for(symbol)) * scale
    return (cfg.sl_atr + cl) / (cfg.tp_atr + cfg.sl_atr + cl - cw), cw, cl


def wilson(x: int, n: int, z: float = 1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = x / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z / d * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return centre - half, centre + half


def assess(f, signals, cfg, symbol: str):
    """Resolve signals under the follow-up geometry and collect the cost bar."""
    outcomes, _ = evaluate(f, signals, cfg, symbol)
    traded = [r for r in outcomes if r["status"] in ("tp", "sl", "time")]
    close = f["close"].to_numpy()
    atr = f["atr"].to_numpy()
    bes, cls = [], []
    for r in traded:
        be, _cw, cl = break_even(close[r["idx"]], atr[r["idx"]], cfg, symbol)
        bes.append(be)
        cls.append(cl)
    wins = sum(1 for r in traded if r["win"])
    return {
        "raw": len(signals),
        "trades": len(traded),
        "wins": wins,
        "hit": wins / len(traded) if traded else float("nan"),
        "required": float(np.mean(bes)) if bes else float("nan"),
        "mean_cl": float(np.mean(cls)) if cls else float("nan"),
        "time_stops": sum(1 for r in traded if r["status"] == "time"),
        "unfilled": sum(1 for r in outcomes if r["status"] == "unfilled"),
        "tp_too_small": sum(1 for r in outcomes if r["status"] == "tp_too_small_vs_fees"),
        "bes": bes,
    }


def run(cfg, use_synthetic: bool, refetch: bool):
    rows, pooled = {}, {"wins": 0, "trades": 0, "bes": [], "time_stops": 0}
    v0_pooled = {"wins": 0, "trades": 0, "time_stops": 0}

    for i, symbol in enumerate(cfg.symbols):
        if use_synthetic:
            df = synthetic(days=cfg.days, seed=200 + i)
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

        s = assess(f, v3_vwap_climax(f, cfg), cfg, symbol)
        rows[label] = s
        pooled["wins"] += s["wins"]
        pooled["trades"] += s["trades"]
        pooled["bes"] += s["bes"]
        pooled["time_stops"] += s["time_stops"]

        v0 = assess(f, v0_random(f, cfg), cfg, symbol)
        s["v0_hit"] = v0["hit"]
        v0_pooled["wins"] += v0["wins"]
        v0_pooled["trades"] += v0["trades"]
        v0_pooled["time_stops"] += v0["time_stops"]

        print(f"  {label}: V3 {s['trades']}tr hit {s['hit']:.1%} vs required "
              f"{s['required']:.1%} | V0 {v0['hit']:.1%}", flush=True)

    return rows, pooled, v0_pooled


def report(rows, pooled, v0_pooled, cfg, tag):
    print(f"\n{'='*82}\nV3 FOLLOW-UP GATE ({tag})  TP {cfg.tp_atr} / SL {cfg.sl_atr} ATR, "
          f"{cfg.max_bars}-bar stop\n{'='*82}")
    print(f"{'symbol':<18}{'raw':>6}{'trades':>8}{'hit':>8}{'required':>10}"
          f"{'margin':>9}{'time':>7}{'V0 hit':>8}")
    beat = 0
    for sym, s in rows.items():
        if s["trades"]:
            margin = (s["hit"] - s["required"]) * 100
            beat += s["hit"] > s["required"]
            print(f"{sym:<18}{s['raw']:>6}{s['trades']:>8}{s['hit']:>8.1%}"
                  f"{s['required']:>10.1%}{margin:>+8.2f}pp{s['time_stops']:>7}{s['v0_hit']:>8.1%}")
        else:
            print(f"{sym:<18}{s['raw']:>6}{0:>8}{'-':>8}{'-':>10}{'-':>9}{'-':>9}{'-':>8}")

    n, x = pooled["trades"], pooled["wins"]
    print(f"\npooled V3: {n} trades")
    if n == 0:
        print("no V3 trades at all -> INSUFFICIENT SAMPLE")
        return "INSUFFICIENT SAMPLE"

    hit = x / n
    req = float(np.mean(pooled["bes"]))
    lo, hi = wilson(x, n)
    v0_hit = v0_pooled["wins"] / v0_pooled["trades"] if v0_pooled["trades"] else float("nan")

    print(f"  hit rate          {hit:.2%}")
    print(f"  Wilson 95% CI     [{lo:.2%}, {hi:.2%}]")
    print(f"  required (cost)   {req:.2%}")
    print(f"  margin (point)    {(hit - req) * 100:+.2f} pp")
    print(f"  margin (Wilson lo){(lo - req) * 100:+.2f} pp   <-- this is what the gate tests")
    print(f"\n  for information: V0 random at this geometry {v0_hit:.2%} "
          f"({v0_pooled['trades']} trades), V3 - random = {(hit - v0_hit) * 100:+.2f} pp")
    print(f"  before-costs break-even {cfg.sl_atr / (cfg.tp_atr + cfg.sl_atr):.2%}")
    print(f"  time stops: V3 {pooled['time_stops']}/{n} ({pooled['time_stops']/n:.1%}), "
          f"V0 {v0_pooled['time_stops']}/{v0_pooled['trades']} "
          f"({v0_pooled['time_stops']/max(v0_pooled['trades'],1):.1%}) -- counted as losses")

    c1 = n >= MIN_POOLED
    c2 = lo > req
    c3 = beat >= 3
    print(f"\n  [{'x' if c1 else ' '}] pooled trades >= {MIN_POOLED}          ({n})")
    print(f"  [{'x' if c2 else ' '}] Wilson lower bound > required   ({lo:.2%} vs {req:.2%})")
    print(f"  [{'x' if c3 else ' '}] beats required on >= 3 of 4     ({beat}/4)")

    if not c1:
        verdict = "INSUFFICIENT SAMPLE"
    elif c2 and c3:
        verdict = "PASS"
    else:
        verdict = "FAIL"
    print(f"\n  VERDICT: {verdict}")
    return verdict


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--geometry", choices=["new", "old"], default="new",
                    help="'old' re-tests the main spec's 0.75/2.0 geometry on the fresh symbols")
    args = ap.parse_args()
    cfg = followup_config(args.geometry)

    rows, pooled, v0p = run(cfg, args.synthetic, args.refetch)
    tag = ("SYNTHETIC random walk" if args.synthetic
           else f"REAL DATA, fresh symbols, {args.geometry.upper()} geometry")
    verdict = report(rows, pooled, v0p, cfg, tag)

    if args.geometry == "old" and not args.synthetic:
        print("\n--- separating geometry effect from selection noise ---")
        print("  original measurement (old geometry, BNB/ETH/SOL/DOGE): V3 - random = +4.14 pp")
        print("  this run             (old geometry, fresh symbols)   : see 'V3 - random' above")
        print("  follow-up            (new geometry, fresh symbols)   : V3 - random = -2.43 pp")
        print("  Only the symbols differ between the first two, so a lift that replicates")
        print("  points at a real but geometry-bound effect; a lift near zero points at")
        print("  selection noise in the original best-of-four pick.")

    if args.synthetic:
        print("\n--- falsification check ---")
        if verdict == "PASS":
            print("FAILED: V3 passed on random-walk data. There is a bug.")
            sys.exit(1)
        print(f"ok  V3 does not pass on synthetic data (verdict: {verdict})")
        print("note: synthetic() has uniform volume and cannot produce a 3x climax, so")
        print("      V3 fires zero times here. This check is vacuous, not evidence.")
        print("      The meaningful control is the V0 random line on real data above.")
