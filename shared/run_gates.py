"""Steps 2 and 3: run both strategies through the cost-inclusive edge gate.

Per the spec, only a strategy that PASSES its gate proceeds to a backtest. Both
are judged independently, with a 95% two-sided Wilson bound to account for testing
two hypotheses in one round.

Usage:
  python shared/run_gates.py --synthetic   # falsification: both must FAIL
  python shared/run_gates.py               # real data
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import fetch_ohlcv, load_csv, save_csv, synthetic
from shared import strategy_a, strategy_b
from shared.config import ABConfig
from shared.features import build_features
from shared.gate import evaluate, pool, random_control, report, summarise


def cache_path(symbol: str) -> str:
    return f"{symbol.split('/')[0]}_15m.csv"


def load(symbol, cfg, use_synthetic, i, refetch):
    if use_synthetic:
        return synthetic(days=cfg.days, seed=400 + i), f"SYNTH:{symbol.split('/')[0]}"
    path, df = cache_path(symbol), None
    if not refetch:
        try:
            df = load_csv(path)
        except FileNotFoundError:
            pass
    if df is None:
        print(f"  fetching {symbol} ({cfg.days}d)...", flush=True)
        df = fetch_ohlcv(symbol, "15m", cfg.days, cfg.exchange_id)
        save_csv(df, path)
    return df, symbol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--refetch", action="store_true")
    args = ap.parse_args()
    cfg = ABConfig()

    per = {"A": {}, "A2": {}, "B": {}}
    raw = {"A": [], "A2": [], "B": []}
    ctrl_raw = {"A": [], "B": []}

    for i, symbol in enumerate(cfg.symbols):
        df, label = load(symbol, cfg, args.synthetic, i, args.refetch)
        f = build_features(df, cfg)

        specs = {
            "A":  (strategy_a.signals(f, cfg, "A"),  cfg.entry_valid_bars_a, cfg.max_bars_a),
            "A2": (strategy_a.signals(f, cfg, "A2"), cfg.entry_valid_bars_a, cfg.max_bars_a),
            "B":  (strategy_b.cascade(f, cfg),       cfg.entry_valid_bars_b, cfg.max_bars_b),
        }
        for name, (sigs, ev, mb) in specs.items():
            outcomes = evaluate(f, sigs, cfg, symbol, ev, mb)
            per[name][label] = summarise(outcomes)
            raw[name].append(outcomes)

        for name, mod, ev, mb in (("A", strategy_a, cfg.entry_valid_bars_a, cfg.max_bars_a),
                                  ("B", strategy_b, cfg.entry_valid_bars_b, cfg.max_bars_b)):
            reg = mod.regime_mask(f, cfg) if name == "B" else mod.regime_mask(f, cfg, "A")
            cs = random_control(f, cfg, reg, mod.stop_for, seed_offset=i)
            ctrl_raw[name].append(evaluate(f, cs, cfg, symbol, ev, mb))

        print(f"  {label}: A={len(specs['A'][0])} A2={len(specs['A2'][0])} "
              f"B={len(specs['B'][0])} signals", flush=True)

    tag = "SYNTHETIC" if args.synthetic else "REAL DATA"
    verdicts = {}
    for name in ("A", "B"):
        verdicts[name] = report(f"STRATEGY {name} ({tag})", per[name], pool(raw[name]),
                                pool(ctrl_raw[name]), cfg)

    a2 = pool(raw["A2"])
    print(f"\nVariant A2 (reporting only, never selected): {a2['trades']} trades, "
          f"hit {a2['hit']:.1%} vs required {a2['required']:.1%}"
          if a2["trades"] else "\nVariant A2: no trades")

    if args.synthetic:
        print("\n--- falsification check ---")
        bad = [k for k, v in verdicts.items() if v == "PASS"]
        if bad:
            print(f"FAILED: {bad} passed on random-walk data. There is a bug.")
            sys.exit(1)
        print(f"ok  neither strategy passes on synthetic data ({verdicts})")


if __name__ == "__main__":
    main()
