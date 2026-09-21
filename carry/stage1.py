"""Stage 1 of CARRY_SPEC.md: simulate C0, C1, C2 and their placebos.

One run, all outputs together. Simulation only -- nothing here can place an order.

Two interpretations the spec leaves open, chosen before any result was seen and
stated here rather than buried:

  1. **After a liquidation, C0 re-enters at the next funding timestamp**, paying
     entry costs again. The spec says the position is "flat until the next entry
     signal", but C0's only entry signal is at the start, so a literal reading
     would leave ten of eleven symbols flat for most of five years and stop
     measuring the thing C0 is for. C1 and C2 do stay flat until their own rules
     fire again, which is what the spec describes.
  2. **C2's return is computed on deployed capital** -- the four slots it holds,
     not all twelve symbols. That makes it comparable to C0 per unit of capital
     at work. Dividing by twelve instead would understate it threefold.

Usage:
  python carry/stage1.py
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from carry.config import CarryConfig                                       # noqa: E402
from carry.engine import c1_positions, c2_selection, per_8h                # noqa: E402
from carry.fetch import (CACHE, align_to_funding, candles,                 # noqa: E402
                         funding, interval_hours)


def leg_costs(cfg, sym):
    """Cost of opening (or closing) both legs, as a fraction of notional."""
    slip = cfg.slip_for(sym)
    return (cfg.spot_fee + slip) + (cfg.perp_fee + slip)


def simulate(sym, aligned, perp_1h, held, cfg, leverage=None, cost_mult=1.0,
             rebal_days=None, reentry="next_signal"):
    """Walk one symbol's holding schedule and return its per-step P&L on capital.

    P&L is expressed against the notional at entry, so the two legs cancel
    exactly when spot and perp move together and the per-step figures telescope
    to the window total without drift.

    `reentry` controls what happens after a liquidation. "immediate" re-opens at
    the next step (C0, which is always-on by definition). "next_signal" stays
    flat until the schedule itself goes flat and turns on again, which is what
    the spec describes for the timing rules.
    """
    L = cfg.leverage if leverage is None else leverage
    rd = cfg.rebalance_days if rebal_days is None else rebal_days
    cap = cfg.capital_multiple(L)
    buffer = cfg.liquidation_rise * (2.0 / L)
    one_leg = leg_costs(cfg, sym) * cost_mult

    ts = aligned.index
    rate = aligned["rate"].to_numpy()
    spot = aligned["spot"].to_numpy()
    perp = aligned["perp"].to_numpy()
    hi = perp_1h["high"]

    pnl = np.zeros(len(ts))
    in_pos = blocked = False
    entry_spot = entry_perp = rebal_ref = np.nan
    last_rebal = None
    n_rebal = n_liq = n_trades = held_steps = 0

    for i in range(len(ts)):
        want = bool(held[i])
        if not want:
            blocked = False                      # a fresh signal cycle clears the block

        if in_pos:
            pnl[i] += (spot[i] - spot[i - 1]) / entry_spot      # long spot
            pnl[i] += (perp[i - 1] - perp[i]) / entry_perp      # short perp
            pnl[i] += rate[i]                                   # funding received
            held_steps += 1

            # Margin is checked on 1H highs since the last top-up, not on the
            # funding-timestamp close: a spike that liquidates intraday does not
            # wait for the close to do it.
            win = hi[(hi.index > ts[i - 1]) & (hi.index <= ts[i])]
            liquidated = len(win) and float(win.max()) / rebal_ref - 1.0 >= buffer
            if liquidated:
                pnl[i] -= cfg.liquidation_penalty + one_leg
                n_liq += 1
                in_pos = False
                blocked = (reentry != "immediate")
            else:
                if last_rebal is None or (ts[i] - last_rebal) >= pd.Timedelta(days=rd):
                    moved = max(0.0, perp[i] / rebal_ref - 1.0)
                    pnl[i] -= moved * (cfg.spot_fee + cfg.slip_for(sym)) * cost_mult
                    rebal_ref, last_rebal = perp[i], ts[i]
                    n_rebal += 1
                if not want:                                    # ordinary exit
                    pnl[i] -= one_leg
                    in_pos = False

        if not in_pos and want and not blocked:                 # enter
            pnl[i] -= one_leg
            in_pos = True
            entry_spot, entry_perp = spot[i], perp[i]
            rebal_ref, last_rebal = perp[i], ts[i]
            n_trades += 1

    if in_pos:
        pnl[-1] -= one_leg

    return {"pnl": pd.Series(pnl / cap, index=ts), "rebalances": n_rebal,
            "liquidations": n_liq, "trades": n_trades,
            "held_share": held_steps / max(1, len(ts)),
            "gross_funding": float(np.sum(rate * np.asarray(held, bool)) / cap)}


# ---------------------------------------------------------------- variants ---

def c0_mask(n):
    return np.ones(n, bool)


def c1_mask(aligned, hours, cfg):
    r8 = per_8h(aligned["rate"].to_numpy(), hours.reindex(aligned.index).to_numpy())
    return c1_positions(r8, aligned.index, cfg)


def c2_masks(data, cfg):
    """Rotation across symbols. Returns {symbol: bool mask on its own grid}.

    Decisions use only payments already made, and take effect from the first
    funding timestamp strictly after the rebalance date.
    """
    grid = sorted(set().union(*[set(d["aligned"].index) for d in data.values()]))
    start, end = grid[0], grid[-1]
    dates = pd.date_range(start, end, freq=f"{cfg.c2_rebalance_days}D", tz="UTC")
    masks = {s: np.zeros(len(d["aligned"]), bool) for s, d in data.items()}
    hurdle = {s: cfg.round_trip_cost(s) / cfg.c2_cost_recovery_days
              * (float(pd.Series(d["hours"]).round().mode().iloc[0]) / 24.0)
              for s, d in data.items()}
    prev = set()
    for k, date in enumerate(dates):
        means = {}
        for s, d in data.items():
            past = d["aligned"].loc[d["aligned"].index <= date, "rate"]
            means[s] = float(past.iloc[-cfg.c2_lookback:].mean()) \
                if len(past) >= cfg.c2_lookback else np.nan
        chosen = c2_selection(means, prev, cfg, hurdle)
        stop = dates[k + 1] if k + 1 < len(dates) else end + pd.Timedelta(days=1)
        for s in chosen:
            idx = data[s]["aligned"].index
            masks[s][(idx > date) & (idx <= stop)] = True
        prev = chosen
    return masks


def shifted_mask(mask, rng, cfg, per_day):
    """C1's placebo: the same holding windows, moved in time.

    Shifting the whole schedule circularly keeps the number and length of the
    windows exactly, so the placebo differs from C1 only in when it holds.
    """
    n = len(mask)
    min_shift = int(cfg.placebo_min_shift_days * per_day)
    if n <= 2 * min_shift:
        return mask.copy()
    off = int(rng.integers(min_shift, n - min_shift))
    return np.roll(mask, off)


# ------------------------------------------------------------------ report ---

def portfolio(pnls):
    """Equal-weight across symbols, on the union of their funding grids."""
    df = pd.concat(pnls, axis=1).fillna(0.0)
    return df.mean(axis=1)


def monthly(series):
    idx = series.index.tz_convert("UTC").tz_localize(None)
    s = pd.Series(series.to_numpy(), index=idx)
    return s.groupby(s.index.to_period("M")).sum()


def stats(series, cfg):
    m = monthly(series)
    eq = (1 + series).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    years = len(m) / 12.0
    total = float((1 + series).prod() - 1)
    ann = (1 + total) ** (1 / years) - 1 if years > 0 else float("nan")
    full = [y for y in {p.year for p in m.index}
            if sum(1 for p in m.index if p.year == y) == 12]
    by_year = {y: float(m[[p.year == y for p in m.index]].sum()) for y in sorted(full)}
    return {"ann": ann, "dd": dd, "worst_month": float(m.min()),
            "losing_share": float((m < 0).mean()), "by_year": by_year,
            "monthly": m, "total": total}


def paired_monthly_ci(a, b, cfg, iters=4000):
    """Paired 3-month block bootstrap of the monthly return difference.

    Both arms are resampled in the same blocks, because they share the same
    funding regimes -- resampling them independently would ignore that.
    """
    j = pd.concat([a, b], axis=1).dropna()
    if len(j) < 2 * cfg.block_months:
        return float("nan"), float("nan")
    d = (j.iloc[:, 0] - j.iloc[:, 1]).to_numpy()
    k, rng = cfg.block_months, np.random.default_rng(cfg.seed)
    starts = np.arange(len(d) - k + 1)
    n_blocks = max(1, len(d) // k)
    draws = [np.concatenate([d[s:s + k] for s in rng.choice(starts, n_blocks)]).mean()
             for _ in range(iters)]
    return float(np.percentile(draws, 2.5) * 12), float(np.percentile(draws, 97.5) * 12)


def line(name, st, cfg, extra=""):
    print(f"  {name:<22} {st['ann']:>8.2%} {st['dd']:>9.2%} {st['worst_month']:>9.2%} "
          f"{st['losing_share']:>8.1%}  {extra}")


def main():
    cfg = CarryConfig()
    B = cfg.require_benchmark()
    data, dropped = {}, []
    print(f"  cache: {CACHE.resolve()}", flush=True)
    for sym in cfg.symbols:
        try:
            fund = funding(sym, cfg)
            spot = candles(sym, cfg, "spot")
            perp = candles(sym, cfg, "perp")
        except Exception as e:                                   # noqa: BLE001
            dropped.append((sym, f"{type(e).__name__}: {e}"))
            continue
        empties = [n for n, df in (("funding", fund), ("spot", spot), ("perp", perp))
                   if df.empty]
        if empties:
            dropped.append((sym, f"empty series: {', '.join(empties)}"))
            continue
        hours = interval_hours(fund)
        aligned = align_to_funding(fund, spot, perp)
        if aligned.empty:
            dropped.append((sym, "no funding timestamp had both a spot and a perp price"))
            continue
        years = (aligned.index[-1] - aligned.index[0]).days / 365.25
        if years < cfg.min_years:
            dropped.append((sym, f"{years:.1f}y of history, under the {cfg.min_years}y minimum"))
            continue
        data[sym] = {"aligned": aligned, "perp": perp, "hours": hours}
        print(f"  {sym} loaded ({len(aligned):,} payments, {years:.1f}y)", flush=True)

    if dropped:
        print("\n  dropped:")
        for sym, why in dropped:
            print(f"    {sym:<5} {why}")
    if not data:
        raise SystemExit(
            "\n  No symbol survived loading, so there is nothing to simulate.\n"
            "  The reasons are listed above. If they are all fetch errors, run\n"
            "  carry/stage0.py first to populate the cache, and check that you are\n"
            f"  running from the directory containing {CACHE}/.")

    per_day = {s: 24.0 / float(pd.Series(d["hours"]).round().mode().iloc[0])
               for s, d in data.items()}
    c2 = c2_masks(data, cfg)
    rng = np.random.default_rng(cfg.seed)

    runs, meta = {}, {}
    for name in ("C0", "C1", "C2"):
        pnls, agg = {}, {"rebalances": 0, "liquidations": 0, "trades": 0,
                         "held": [], "gross": 0.0}
        for s, d in data.items():
            if name == "C0":
                mask, reentry = c0_mask(len(d["aligned"])), "immediate"
            elif name == "C1":
                mask, reentry = c1_mask(d["aligned"], d["hours"], cfg), "next_signal"
            else:
                mask, reentry = c2[s], "next_signal"
            r = simulate(s, d["aligned"], d["perp"], mask, cfg, reentry=reentry)
            pnls[s] = r["pnl"]
            for k in ("rebalances", "liquidations", "trades"):
                agg[k] += r[k]
            agg["held"].append(r["held_share"])
            agg["gross"] += r["gross_funding"]
        scale = cfg.c2_top_n / len(data) if name == "C2" else 1.0
        runs[name] = stats(portfolio(pnls) / scale, cfg)
        meta[name] = agg

    # placebos
    plac = {}
    for name in ("C1", "C2"):
        draws = []
        for _ in range(min(cfg.placebo_draws, 40)):     # 40 draws: each is a full sim
            pnls = {}
            if name == "C1":
                for s, d in data.items():
                    m = c1_mask(d["aligned"], d["hours"], cfg)
                    pnls[s] = simulate(s, d["aligned"], d["perp"],
                                       shifted_mask(m, rng, cfg, per_day[s]), cfg)["pnl"]
            else:
                syms = list(data)
                for s in syms:
                    pnls[s] = pd.Series(0.0, index=data[s]["aligned"].index)
                pick = rng.choice(syms, cfg.c2_top_n, replace=False)
                for s in pick:
                    pnls[s] = simulate(s, data[s]["aligned"], data[s]["perp"],
                                       c2[s][:], cfg)["pnl"]
            scale = cfg.c2_top_n / len(data) if name == "C2" else 1.0
            draws.append(monthly(portfolio(pnls) / scale))
        plac[name] = pd.concat(draws, axis=1).mean(axis=1)

    print("\n" + "=" * 86)
    print(f"STAGE 1 -- funding carry, net of all costs.  B = {B:.1%}/yr, "
          f"{len(data)} symbols, L={cfg.leverage:.0f}")
    print("=" * 86)
    print(f"  {'variant':<22} {'ann ret':>8} {'max DD':>9} {'worst mo':>9} {'losing':>8}")
    for name in ("C0", "C1", "C2"):
        a = meta[name]
        line(name, runs[name], cfg,
             f"held {np.mean(a['held']):.0%}, {a['trades']} entries, "
             f"{a['rebalances']:,} rebals, {a['liquidations']} liquidations")

    print(f"\n  return by full calendar year:")
    years = sorted({y for r in runs.values() for y in r["by_year"]})
    print(f"  {'variant':<22} " + " ".join(f"{y:>9}" for y in years))
    for name in ("C0", "C1", "C2"):
        print(f"  {name:<22} " + " ".join(
            f"{runs[name]['by_year'].get(y, float('nan')):>9.2%}" for y in years))

    print(f"\n  timing rules vs their placebos (paired 3-month block bootstrap, annualised):")
    for name in ("C1", "C2"):
        lo, hi = paired_monthly_ci(runs[name]["monthly"], plac[name], cfg)
        print(f"    {name} - placebo   [{lo:+.2%}, {hi:+.2%}]   "
              f"{'above zero' if lo > 0 else 'spans zero'}")
    print("    Stage 0 labelled these UNDERPOWERED in advance (4.38% detectable vs a 3%")
    print("    threshold), so neither can pass regardless of where the interval sits.")

    print(f"\n  C0 against its pass criteria:")
    c0 = runs["C0"]
    neg_years = [y for y, v in c0["by_year"].items() if v < 0]
    checks = [
        (f"annualised {c0['ann']:.2%} > B {B:.1%}", c0["ann"] > B),
        (f"every full year >= 0 (negative: {neg_years or 'none'})", not neg_years),
        (f"max drawdown {c0['dd']:.2%} within -10%", c0["dd"] >= -0.10),
        (f"zero liquidations at L=2 (got {meta['C0']['liquidations']})",
         meta["C0"]["liquidations"] == 0),
    ]
    for text, ok in checks:
        print(f"    {'PASS' if ok else 'FAIL'}  {text}")

    print(f"\n  stress (report only, never selection):")
    for label, kw in (("costs x2", {"cost_mult": 2.0}),
                      ("monthly rebalance", {"rebal_days": 30}),
                      ("L = 1", {"leverage": 1.0}),
                      ("L = 3", {"leverage": 3.0})):
        pnls, liq = {}, 0
        for s, d in data.items():
            r = simulate(s, d["aligned"], d["perp"], c0_mask(len(d["aligned"])),
                         cfg, reentry="immediate", **kw)
            pnls[s], liq = r["pnl"], liq + r["liquidations"]
        st = stats(portfolio(pnls), cfg)
        print(f"    C0, {label:<20} {st['ann']:>8.2%}/yr   maxDD {st['dd']:>8.2%}   "
              f"{liq} liquidations   {'> B' if st['ann'] > B else 'below B'}")

    print(f"\n  VERDICT: C0 {'PASSES' if all(ok for _, ok in checks) else 'FAILS'}; "
          f"C1 and C2 cannot pass (underpowered by Stage 0).")
    print("\n  Not modelled: exchange counterparty risk -- the largest risk here and the")
    print("  one no number above captures -- future funding-formula changes, taxes and")
    print("  withdrawal fees, and legging-in execution risk beyond the slippage allowance.")


if __name__ == "__main__":
    main()
