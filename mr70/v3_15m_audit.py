"""Audit: does MR-70 V3's 15m edge survive a construction-matched null?

Not a new experiment. `PROJECT_CONCLUSIONS.md` records V3 on 15m as having a real
edge of about +3.4 pp over its random control (V0). The 1H test later used a
time-shifted placebo instead and found nothing, and the Strategy B diagnostics
showed that a control which does not match the strategy's entry and stop
construction measures geometry rather than edge -- there it flattered the cascade
by 7 pp on data with no edge in it.

So the question is whether V0 was a fair null for V3, or whether the +3.4 pp was
the same artifact. V3 itself is untouched: same signal, geometry, costs, symbols
and cached data as the original run. Only the null changes.

Shifts of +/-1,600 and +/-3,200 15m bars are about 17 and 33 days, matching the
1H test's shifts in calendar time.

Usage:
  python mr70/v3_15m_audit.py
"""
import pathlib
import sys

import numpy as np
from math import sqrt
from statistics import NormalDist

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import fetch_ohlcv, load_csv, save_csv                 # noqa: E402
from mr70.config import MR70Config                               # noqa: E402
from mr70.edge_gate import cache_path, evaluate                  # noqa: E402
from mr70.indicators import build_features                       # noqa: E402
from mr70.signals import v0_random, v3_vwap_climax               # noqa: E402
from mr70.v3_1h import (one_arm_bootstrap, paired_bootstrap,     # noqa: E402
                        required_rate, shifted_signals)

SHIFTS_15M = (-3200, -1600, 1600, 3200)


def load(symbol, cfg):
    path = cache_path(symbol)
    try:
        return load_csv(path)
    except FileNotFoundError:
        print(f"  fetching {symbol} ({cfg.days}d)...", flush=True)
        df = fetch_ohlcv(symbol, "15m", cfg.days, cfg.exchange_id)
        save_csv(df, path)
        return df


def rate(rows):
    return (sum(w for _, w in rows) / len(rows)) if rows else float("nan")


def main():
    cfg = MR70Config()
    v3, plac, v0, be = [], [], [], []

    for symbol in cfg.symbols:
        f = build_features(load(symbol, cfg), cfg)
        ct = f["close_time"]

        def rows_for(signals):
            outs, _ = evaluate(f, signals, cfg, symbol)
            return [(ct.iloc[r["idx"]], bool(r["win"])) for r in outs
                    if r["status"] in ("tp", "sl", "time")]

        sigs = v3_vwap_climax(f, cfg)
        outs, _ = evaluate(f, sigs, cfg, symbol)
        traded = [r for r in outs if r["status"] in ("tp", "sl", "time")]
        v3 += [(ct.iloc[r["idx"]], bool(r["win"])) for r in traded]
        be += [required_rate(f, r["idx"], cfg, symbol) for r in traded]

        for sh in SHIFTS_15M:
            plac += rows_for(shifted_signals(sigs, len(f), sh))
        v0 += rows_for(v0_random(f, cfg))
        print(f"  {symbol.split('/')[0]:<5} {len(sigs):>4} signals, "
              f"{len(traded):>4} trades", flush=True)

    h3, hp, h0 = rate(v3), rate(plac), rate(v0)
    be_m = float(np.nanmean(be))

    print("\n" + "=" * 78)
    print("V3 15m -- audit against the time-shifted placebo")
    print("=" * 78)
    print(f"  V3                         {h3:>8.2%}  on {len(v3):,} trades")
    print(f"  time-shifted placebo       {hp:>8.2%}  on {len(plac):,} trades")
    print(f"  V0 random control          {h0:>8.2%}  on {len(v0):,} trades")
    print(f"  pooled break-even          {be_m:>8.2%}")

    print(f"\n  V3 - V0        {100 * (h3 - h0):>+6.2f} pp   "
          f"<- the original claim, about +3.4 pp")
    print(f"  V3 - placebo   {100 * (h3 - hp):>+6.2f} pp   <- the same question, better null")

    print(f"\n  paired block bootstrap, V3 - placebo:")
    verdicts = []
    for bd in (7, 28, 90):
        lo, hi = paired_bootstrap(v3, plac, block_days=bd)
        above = lo > 0
        verdicts.append(above)
        print(f"    {bd:>3}-day blocks   [{lo:+.2%}, {hi:+.2%}]   "
              f"{'above zero' if above else 'spans zero'}")
    lo28, hi28 = paired_bootstrap(v3, plac, block_days=28)
    v3_lo = one_arm_bootstrap(v3)[0]

    print(f"\n  V3's own 95% lower bound {v3_lo:.2%} vs break-even {be_m:.2%}: "
          f"{'clears' if v3_lo > be_m else 'below'}")

    print("\n" + "-" * 78)
    # An interval spanning zero does not establish that the edge is absent -- it
    # establishes that this sample cannot resolve it. Reporting the power makes
    # the difference visible instead of hiding it behind a verdict word.
    nd = NormalDist()
    d = h3 - hp
    se = sqrt(h3 * (1 - h3) / len(v3) + hp * (1 - hp) / len(plac))
    power = 1 - nd.cdf(nd.inv_cdf(0.975) - d / se) if se > 0 else float("nan")
    need = ((nd.inv_cdf(0.975) + nd.inv_cdf(0.80)) ** 2
            * (h3 * (1 - h3) + hp * (1 - hp)) / d ** 2) if d else float("inf")

    if all(verdicts):
        print("  RESULT: the original claim STANDS. V3 beats a construction-matched")
        print("  null at every block length, so V0 was a fair null for this strategy.")
    elif not any(verdicts):
        print(f"  RESULT: UNSUPPORTED, not refuted. V3 - placebo is {100 * d:+.2f} pp with")
        print(f"  an interval spanning zero, so this sample cannot tell a real edge from")
        print(f"  none. Power to detect an effect of that size here is only {power:.0%};")
        print(f"  resolving it would need about {need:,.0f} trades per arm against "
              f"{len(v3):,} / {len(plac):,}.")
        print(f"  The interval contains zero AND the originally claimed figure, so it")
        print(f"  argues against neither.")
    else:
        print("  RESULT: mixed -- the interval's sign depends on block length, so the")
        print("  comparison is not robust either way.")

    shift = (h3 - h0) - d
    print(f"\n  What IS established: of the {100 * (h3 - h0):+.2f} pp V3 shows over V0,")
    print(f"  {100 * shift:.2f} pp ({shift / (h3 - h0):.0%}) disappears when the null is")
    print(f"  construction-matched. That much of the original figure was the null, not V3.")
    print(f"\n  What is not in doubt: V3 at {h3:.2%} sits {100 * (h3 - be_m):+.2f} pp below")
    print(f"  its own break-even of {be_m:.2%}, with a lower bound {100 * (v3_lo - be_m):.2f} pp")
    print(f"  below it. Whether or not an edge exists, it is not a tradeable one.")

    print(f"\n  For the record: V3 - V0 = {100 * (h3 - h0):+.2f} pp, "
          f"V3 - placebo = {100 * d:+.2f} pp "
          f"[{lo28:+.2%}, {hi28:+.2%}] at 28-day blocks.")


if __name__ == "__main__":
    main()
