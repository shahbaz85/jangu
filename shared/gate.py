"""Cost-inclusive edge gate shared by Strategy A and Strategy B.

The gate asks one question: does the signal reach TP1 before the stop often enough
to pay for itself? Per signal, the real cost of a win and of a loss is expressed as
a fraction of that signal's own stop distance, which turns into the hit rate that
trade needs to break even:

    be = (1 + cl) / (RR + 1 + cl - cw)          RR = 1 at TP1

Deliberate choices, all of which make the gate stricter rather than looser:

- Signals are thinned by the same one-position-per-symbol rule the backtest will
  trade under. Overlapping signals are near-duplicate events; counting them all
  would leave the hit rate about right but shrink the interval dishonestly.
- Time-stop exits count as losses, and a same-bar stop-and-target counts as the
  stop.
- The 95% Wilson interval is two-sided (a 97.5% one-sided bound), because two
  hypotheses are being tested in this round.

It also reports runner conversion -- P(reach 2R | reached TP1) -- because the gate
alone cannot see the scaled exit. Both strategies bank half at 1R and move the stop
to breakeven, so a win is worth +0.5R unless the runner delivers. A gate pass with
poor runner conversion is not a profitable strategy.
"""
import math

import numpy as np
import pandas as pd


def wilson(x: int, n: int, z: float = 1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = x / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z / d * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return centre - half, centre + half


def required_hit_rate(entry: float, stop: float, cfg, symbol: str, rr: float = 1.0):
    """Break-even hit rate for this signal, costs expressed in units of its own risk."""
    risk = abs(entry - stop)
    scale = entry / risk                       # fraction-of-price -> fraction-of-risk
    cw = (2 * cfg.maker_fee) * scale
    cl = (cfg.maker_fee + cfg.taker_fee + cfg.slip_for(symbol)) * scale
    return (1 + cl) / (rr + 1 + cl - cw), cw, cl


def resolve(arr, sig, cfg, symbol: str, entry_valid_bars: int, max_bars: int):
    """Walk one signal forward. Win = TP1 (1R) before the stop, within the time stop."""
    o, h, l, c, atr = arr
    n = len(c)
    i, d = sig["idx"], sig["dir"]
    entry, stop = sig["entry"], sig["stop"]
    risk = abs(entry - stop)
    a = atr[i]
    if not (np.isfinite(a) and a > 0 and risk > 0):
        return None
    if risk > cfg.max_stop_atr * a:
        return {"status": "stop_too_wide"}
    if risk / entry < cfg.min_stop_cost_mult * cfg.round_trip_cost(symbol):
        return {"status": "stop_too_tight_vs_fees"}
    if i + 1 + max_bars >= n:
        return None

    fill = None
    for j in range(i + 1, min(i + 1 + entry_valid_bars, n)):
        if (l[j] <= entry) if d == 1 else (h[j] >= entry):
            fill = j
            break
    if fill is None:
        return {"status": "unfilled"}

    tp1 = entry + d * risk
    tp2 = entry + d * 2 * risk
    last = min(fill + max_bars, n - 1)
    hit_tp1_at = None
    for j in range(fill, last + 1):
        hit_sl = (l[j] <= stop) if d == 1 else (h[j] >= stop)
        hit_1 = (h[j] >= tp1) if d == 1 else (l[j] <= tp1)
        if hit_tp1_at is None:
            if hit_sl:                                   # stop wins same-bar ties
                return {"status": "sl", "win": False, "exit_idx": j, "tp2": False}
            if hit_1:
                hit_tp1_at = j
                continue
        else:                                            # after TP1: does the runner reach 2R?
            if (h[j] >= tp2) if d == 1 else (l[j] <= tp2):
                return {"status": "tp1", "win": True, "exit_idx": j, "tp2": True}
            if (l[j] <= entry) if d == 1 else (h[j] >= entry):   # breakeven stop
                return {"status": "tp1", "win": True, "exit_idx": j, "tp2": False}
    if hit_tp1_at is not None:
        return {"status": "tp1", "win": True, "exit_idx": last, "tp2": False}
    return {"status": "time", "win": False, "exit_idx": last, "tp2": False}


def evaluate(f, sigs, cfg, symbol: str, entry_valid_bars: int, max_bars: int):
    """Resolve signals under a one-position-at-a-time rule."""
    arr = tuple(f[k].to_numpy() for k in ("open", "high", "low", "close", "atr"))
    out, busy_until = [], -1
    for s in sorted(sigs, key=lambda x: x["idx"]):
        if s["idx"] <= busy_until:
            continue
        r = resolve(arr, s, cfg, symbol, entry_valid_bars, max_bars)
        if r is None:
            continue
        r.update(idx=s["idx"], dir=s["dir"], entry=s["entry"], stop=s["stop"])
        if r["status"] in ("tp1", "sl", "time"):
            be, cw, cl = required_hit_rate(s["entry"], s["stop"], cfg, symbol)
            r.update(required=be, cw=cw, cl=cl)
            busy_until = r["exit_idx"]
        out.append(r)
    return out


def random_control(f, cfg, regime, stop_fn, seed_offset: int = 0):
    """Random direction on bars the strategy's own regime filter would allow, using
    the strategy's own stop rule, so the control shares the geometry."""
    rng = np.random.default_rng(cfg.seed + seed_offset)
    eligible = np.flatnonzero(regime[1] | regime[-1])
    sigs = []
    for i in eligible:
        allowed = [d for d in (1, -1) if regime[d][i]]
        d = int(rng.choice(allowed))
        stop = stop_fn(f, int(i), d, cfg)
        entry = float(f["close"].to_numpy()[i])
        if not np.isfinite(stop) or (entry - stop) * d <= 0:
            continue
        sigs.append({"idx": int(i), "dir": d, "entry": entry, "stop": float(stop)})
    return sigs


def summarise(outcomes):
    traded = [r for r in outcomes if r["status"] in ("tp1", "sl", "time")]
    wins = [r for r in traded if r["win"]]
    return {
        "signals": len(outcomes),
        "trades": len(traded),
        "wins": len(wins),
        "hit": len(wins) / len(traded) if traded else float("nan"),
        "required": float(np.mean([r["required"] for r in traded])) if traded else float("nan"),
        "runner_q": (sum(1 for r in wins if r["tp2"]) / len(wins)) if wins else float("nan"),
        "time_stops": sum(1 for r in traded if r["status"] == "time"),
        "unfilled": sum(1 for r in outcomes if r["status"] == "unfilled"),
        "stop_wide": sum(1 for r in outcomes if r["status"] == "stop_too_wide"),
        "stop_tight": sum(1 for r in outcomes if r["status"] == "stop_too_tight_vs_fees"),
    }


def report(name, per_symbol, pooled, ctrl, cfg):
    print(f"\n{'='*86}\n{name} EDGE GATE\n{'='*86}")
    print(f"{'symbol':<18}{'sig':>6}{'trades':>8}{'hit':>8}{'required':>10}{'margin':>9}"
          f"{'runner q':>10}{'unfill':>8}{'wide':>6}{'tight':>7}")
    beat = 0
    for sym, s in per_symbol.items():
        if not s["trades"]:
            print(f"{sym:<18}{s['signals']:>6}{0:>8}{'-':>8}{'-':>10}{'-':>9}{'-':>10}"
                  f"{s['unfilled']:>8}{s['stop_wide']:>6}{s['stop_tight']:>7}")
            continue
        margin = (s["hit"] - s["required"]) * 100
        beat += s["hit"] > s["required"]
        print(f"{sym:<18}{s['signals']:>6}{s['trades']:>8}{s['hit']:>8.1%}{s['required']:>10.1%}"
              f"{margin:>+8.2f}pp{s['runner_q']:>10.1%}{s['unfilled']:>8}"
              f"{s['stop_wide']:>6}{s['stop_tight']:>7}")

    n, x = pooled["trades"], pooled["wins"]
    if n == 0:
        print("\nno trades -> INSUFFICIENT SAMPLE")
        return "INSUFFICIENT SAMPLE"
    hit = x / n
    req = pooled["required"]
    lo, hi = wilson(x, n, cfg.wilson_z)
    print(f"\npooled: {n} trades, hit {hit:.2%}, Wilson 95% [{lo:.2%}, {hi:.2%}], "
          f"required {req:.2%}")
    print(f"  margin on the Wilson lower bound: {(lo - req) * 100:+.2f} pp   <-- the gate")
    print(f"  runner conversion P(2R | TP1):    {pooled['runner_q']:.1%}")
    if ctrl:
        print(f"  random control in the same regime: {ctrl['hit']:.2%} "
              f"({ctrl['trades']} trades), signal - random = {(hit - ctrl['hit']) * 100:+.2f} pp")
    q = pooled["runner_q"]
    if np.isfinite(q):
        be_actual = 1 / (1.5 + q)
        print(f"  with that conversion, the SCALED exit needs {be_actual:.1%} to break even "
              f"(gate only asks {req:.1%})")

    c1 = n >= cfg.gate_min_signals
    c2 = lo > req
    c3 = beat >= cfg.gate_symbol_majority
    print(f"\n  [{'x' if c1 else ' '}] pooled trades >= {cfg.gate_min_signals}   ({n})")
    print(f"  [{'x' if c2 else ' '}] Wilson lower bound > required ({lo:.2%} vs {req:.2%})")
    print(f"  [{'x' if c3 else ' '}] beats required on >= {cfg.gate_symbol_majority} of 4  ({beat}/4)")
    verdict = "INSUFFICIENT SAMPLE" if not c1 else ("PASS" if (c2 and c3) else "FAIL")
    print(f"\n  VERDICT: {verdict}")
    return verdict


def pool(per_symbol_outcomes):
    allo = [r for rows in per_symbol_outcomes for r in rows]
    return summarise(allo)
