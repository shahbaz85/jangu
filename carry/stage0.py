"""Stage 0 of CARRY_SPEC.md: descriptive statistics and power. No variant results.

Stops and reports before Stage 1, as the spec directs. The benchmark B is not
needed here -- it is only used to judge C0 in Stage 1 -- so Stage 0 runs without
it, but Stage 1 will refuse to start until it is set.

Usage:
  python carry/stage0.py
"""
import pathlib
import sys

import numpy as np
import pandas as pd
from math import sqrt
from statistics import NormalDist

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from carry.config import CarryConfig                                   # noqa: E402
from carry.engine import per_8h                                        # noqa: E402
from carry.fetch import align_to_funding, candles, funding, interval_hours  # noqa: E402


def monthly_funding_returns(aligned, hours, cfg) -> pd.Series:
    """Funding received per month as a fraction of capital, before price effects.

    This is a descriptive series built from funding alone -- it is not a variant
    result, which Stage 0 is forbidden to produce. It exists to estimate how
    variable a monthly carry return is, which is what sets the detectable effect.
    """
    # to_period() drops the timezone and warns about it; UTC is already the only
    # zone in play, so drop it deliberately rather than letting pandas complain
    idx = aligned.index.tz_convert("UTC").tz_localize(None)
    s = pd.Series(aligned["rate"].to_numpy(), index=idx)
    return s.groupby(s.index.to_period("M")).sum() / cfg.capital_multiple()


def block_bootstrap_se(monthly: pd.Series, cfg, iters=4000) -> float:
    """Standard error of the mean monthly return, resampling 3-month blocks."""
    v = monthly.to_numpy()
    k = cfg.block_months
    if len(v) < 2 * k:
        return float("nan")
    starts = np.arange(len(v) - k + 1)
    rng = np.random.default_rng(cfg.seed)
    n_blocks = max(1, len(v) // k)
    means = []
    for _ in range(iters):
        pick = rng.choice(starts, n_blocks)
        means.append(np.concatenate([v[s:s + k] for s in pick]).mean())
    return float(np.std(means, ddof=1))


def main():
    cfg = CarryConfig()
    nd = NormalDist()
    rows, monthly_all, excluded = [], {}, []

    for sym in cfg.symbols:
        try:
            fund = funding(sym, cfg)
            spot = candles(sym, cfg, "spot")
            perp = candles(sym, cfg, "perp")
        except Exception as e:                       # noqa: BLE001
            excluded.append((sym, f"fetch failed: {type(e).__name__}: {e}"))
            continue
        if fund.empty or spot.empty or perp.empty:
            excluded.append((sym, "empty series"))
            continue

        hours = interval_hours(fund)
        span_years = (fund.index[-1] - fund.index[0]).days / 365.25
        gaps = hours / float(pd.Series(hours).round().mode().iloc[0])
        bad_gaps = int((gaps > 2.5).sum())
        years_present = set(fund.index.year)

        aligned = align_to_funding(fund, spot, perp)
        aligned_years = ((aligned.index[-1] - aligned.index[0]).days / 365.25
                         if not aligned.empty else 0.0)
        r8 = per_8h(aligned["rate"].to_numpy(), hours.reindex(aligned.index).to_numpy())
        per_day = 24.0 / float(pd.Series(hours).round().mode().iloc[0])
        mean_daily = float(np.mean(aligned["rate"].to_numpy())) * per_day
        rt = cfg.round_trip_cost(sym)
        be_days = rt / mean_daily if mean_daily > 0 else float("inf")

        px = perp["close"]
        rise_1d = float((px / px.shift(24) - 1).max())
        rise_7d = float((px / px.shift(24 * 7) - 1).max())

        # Only funding payments that have BOTH a spot and a perp price can be
        # simulated, so the aligned span is the one that matters: five years of
        # funding with two years of candles is not four usable years.
        ok = (min(span_years, aligned_years) >= cfg.min_years
              and cfg.must_include_year in years_present)
        if not ok:
            excluded.append((sym, f"funding {span_years:.1f}y, usable {aligned_years:.1f}y, 2022 "
                                  f"{'present' if cfg.must_include_year in years_present else 'MISSING'}"))
        else:
            monthly_all[sym] = monthly_funding_returns(aligned, hours, cfg)

        rows.append({
            "sym": sym, "start": fund.index[0].date(), "years": span_years,
            "aligned_years": aligned_years,
            "n": len(aligned), "interval_h": float(pd.Series(hours).round().mode().iloc[0]),
            "bad_gaps": bad_gaps, "mean8": float(np.mean(r8)), "median8": float(np.median(r8)),
            "neg_share": float(np.mean(aligned["rate"].to_numpy() < 0)),
            "rt": rt, "be_days": be_days, "rise_1d": rise_1d, "rise_7d": rise_7d,
            "ac": [float(pd.Series(r8).autocorr(lag)) for lag in (1, 3, 9, 21)],
            "ok": ok,
        })
        print(f"  {sym:<5} {span_years:>4.1f}y  {len(aligned):>6,} payments", flush=True)

    print("\n" + "=" * 86)
    print("STAGE 0 -- funding-rate carry, descriptive and power")
    print("=" * 86)

    print(f"\n  {'sym':<5} {'start':<11} {'yrs':>5} {'use':>5} {'int':>4} {'gaps':>5} "
          f"{'mean/8h':>9} {'med/8h':>9} {'neg':>6} {'RT cost':>8} {'BE days':>8}")
    for r in rows:
        print(f"  {r['sym']:<5} {str(r['start']):<11} {r['years']:>5.1f} "
              f"{r['aligned_years']:>5.1f} {r['interval_h']:>4.0f} {r['bad_gaps']:>5} {r['mean8']:>9.5%} "
              f"{r['median8']:>9.5%} {r['neg_share']:>6.1%} {r['rt']:>8.2%} "
              f"{r['be_days']:>8.1f}")

    print(f"\n  autocorrelation of per-8h funding (C1 and C2 depend on persistence):")
    print(f"  {'sym':<5} {'lag1':>7} {'lag3':>7} {'lag9':>7} {'lag21':>7}")
    for r in rows:
        print(f"  {r['sym']:<5} " + " ".join(f"{a:>7.3f}" for a in r["ac"]))

    print(f"\n  largest price rise vs the L={cfg.leverage:.0f} margin buffer "
          f"(+{cfg.liquidation_rise:.0%}):")
    print(f"  {'sym':<5} {'max 1d':>8} {'max 7d':>8}  {'7d breaches buffer?':>21}")
    for r in rows:
        print(f"  {r['sym']:<5} {r['rise_1d']:>8.1%} {r['rise_7d']:>8.1%}  "
              f"{'YES' if r['rise_7d'] >= cfg.liquidation_rise else 'no':>21}")

    if excluded:
        print(f"\n  excluded from pooled results:")
        for sym, why in excluded:
            print(f"    {sym:<5} {why}")

    print("\n  --- funding by calendar year (mean per 8h, share negative) ---")
    years = sorted({p.year for m in monthly_all.values() for p in m.index})
    print(f"  {'sym':<5} " + " ".join(f"{y:>14}" for y in years))
    for sym, m in monthly_all.items():
        cells = []
        for y in years:
            sel = m[[p.year == y for p in m.index]]
            cells.append(f"{sel.sum():>7.2%}/{len(sel):>2}m" if len(sel) else f"{'-':>10}")
        print(f"  {sym:<5} " + " ".join(f"{c:>14}" for c in cells))

    print("\n  --- gross funding yield, before costs, basis and liquidations ---")
    for sym, m in sorted(monthly_all.items(), key=lambda kv: -kv[1].mean()):
        ann = (1 + m.mean()) ** 12 - 1
        full = [y for y in {p.year for p in m.index}
                if sum(1 for p in m.index if p.year == y) == 12]
        worst = min((m[[p.year == y for p in m.index]].sum() for y in full), default=float("nan"))
        print(f"    {sym:<5} {ann:>7.2%}/yr   worst full year {worst:>8.2%}")
    print("  This is funding only. It is not C0: costs, basis moves, weekly rebalancing")
    print("  and liquidation losses all come out of it in Stage 1.")

    print("\n  --- minimum detectable difference (spec section 9.5) ---")
    if not monthly_all:
        print("  no symbol passed the history checks; cannot size.")
        return
    pooled = pd.concat(monthly_all.values(), axis=1).mean(axis=1).dropna()
    se = block_bootstrap_se(pooled, cfg)
    za, zb = nd.inv_cdf(0.975), nd.inv_cdf(cfg.power)
    mdd = (za + zb) * sqrt(2.0) * se * 12.0
    print(f"  pooled monthly carry return: mean {pooled.mean():.3%}, "
          f"sd {pooled.std():.3%}, {len(pooled)} months")
    print(f"  block-bootstrap SE of the mean ({cfg.block_months}-month blocks): {se:.4%}")
    print(f"  smallest annualised rule-minus-placebo difference detectable "
          f"at {cfg.power:.0%} power: {mdd:.2%}")
    print(f"  (the sqrt(2) assumes the two arms are uncorrelated, which is the")
    print(f"   conservative case; a paired comparison with positive correlation does better)")
    if mdd > cfg.underpowered_above_annual:
        print(f"\n  VERDICT: C1 and C2 comparisons are UNDERPOWERED "
              f"({mdd:.2%} > {cfg.underpowered_above_annual:.0%}). Label them so in advance.")
        print(f"  C0 is still evaluated -- it is judged against B, not against a placebo.")
    else:
        print(f"\n  VERDICT: C1 and C2 comparisons are adequately powered.")

    print(f"\n  Benchmark B: "
          f"{'NOT SET -- Stage 1 cannot run (spec section 3)' if cfg.benchmark_annual_pct is None else f'{cfg.benchmark_annual_pct}% per year'}")
    print("  Not modelled anywhere in this study: exchange counterparty risk, future")
    print("  changes to the funding formula, taxes and withdrawal fees, and the")
    print("  execution risk of legging in at different prices (spec section 11).")


if __name__ == "__main__":
    main()
