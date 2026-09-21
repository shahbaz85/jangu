"""Carry mechanics: hedge P&L, margin and liquidation, and the C0/C1/C2 signals.

Measurement and simulation only. Nothing here can place an order.

Sign conventions, stated once because the whole study turns on them:
  * the position is long spot and short the same notional of the perpetual;
  * a POSITIVE funding rate is PAID BY LONGS, so this position RECEIVES it;
  * price P&L is quoted per unit of notional, so the two legs cancel exactly
    when spot and perp move by the same proportion. What survives is the basis.
"""
import numpy as np
import pandas as pd


def per_8h(rates: np.ndarray, hours: np.ndarray) -> np.ndarray:
    """Funding normalised to a per-8-hour figure.

    The spec's thresholds are quoted per 8 hours, but Binance has changed the
    interval on some symbols, so comparing a raw 4-hour rate against an 8-hour
    threshold would halve the apparent funding for no reason.
    """
    h = np.where(np.isfinite(hours) & (hours > 0), hours, 8.0)
    return rates * (8.0 / h)


def hedge_pnl(spot_in, spot_out, perp_in, perp_out, funding_sum, cost, rebal_cost=0.0):
    """Net return per unit of notional over one holding window.

    Long spot returns (spot_out/spot_in - 1); short perp returns
    (1 - perp_out/perp_in). Their sum is the basis move, which is zero when both
    legs travel the same proportion.
    """
    leg_spot = spot_out / spot_in - 1.0
    leg_perp = 1.0 - perp_out / perp_in
    return leg_spot + leg_perp + funding_sum - cost - rebal_cost


def rebalance_and_margin(perp_1h: pd.DataFrame, start, end, cfg, leverage=None):
    """Walk one holding window in rebalance-length steps.

    Returns (rebalance_cost, liquidated, breach_time). The margin reference resets
    at each rebalance, because the weekly top-up is what restores the buffer; using
    the original entry price instead would report liquidations that topping up
    would have prevented.
    """
    L = cfg.leverage if leverage is None else leverage
    buffer = cfg.liquidation_rise * (2.0 / L)      # the +45% figure is quoted at L=2
    step = pd.Timedelta(days=cfg.rebalance_days)
    slip = 0.0
    cost = 0.0
    t = pd.Timestamp(start)
    end = pd.Timestamp(end)
    while t < end:
        stop = min(t + step, end)
        win = perp_1h.loc[(perp_1h.index >= t) & (perp_1h.index < stop)]
        if win.empty:
            t = stop
            continue
        ref = float(win["close"].iloc[0])
        rise = float(win["high"].max()) / ref - 1.0
        if rise >= buffer:
            return cost, True, win.index[int(np.argmax(win["high"].to_numpy() / ref - 1.0 >= buffer))]
        # weekly top-up: only a price RISE erodes the short leg's margin
        moved = max(0.0, float(win["close"].iloc[-1]) / ref - 1.0)
        cost += moved * (cfg.spot_fee + slip)
        t = stop
    return cost, False, None


def c1_positions(rates8: np.ndarray, times, cfg) -> np.ndarray:
    """C1 threshold timing. Returns a bool array: is the position held over the
    interval FOLLOWING each payment?

    The decision at payment i uses payments up to and including i, and takes
    effect from i+1. Acting on the same payment that produced the signal would
    be trading on information that arrives at the moment of the trade.
    """
    n = len(rates8)
    held = np.zeros(n, bool)
    on = False
    entered_at = None
    for i in range(n):
        if i + 1 >= cfg.c1_lookback:
            window = rates8[i + 1 - cfg.c1_lookback:i + 1]
            mean = float(np.mean(window))
            neg_run = int(np.all(rates8[max(0, i - cfg.c1_negative_run + 1):i + 1] < 0)) \
                if i + 1 >= cfg.c1_negative_run else 0
            if not on and mean >= cfg.c1_enter_per_8h:
                on, entered_at = True, times[i]
            elif on:
                min_hold = pd.Timedelta(days=cfg.c1_min_hold_days)
                if times[i] - entered_at >= min_hold and (
                        mean < cfg.c1_exit_per_8h or neg_run):
                    on = False
        if i + 1 < n:
            held[i + 1] = on
    return held


def c2_selection(mean_by_symbol: dict, prev: set, cfg, cost_hurdle: dict) -> set:
    """C2 rotation: the top N by trailing mean funding, subject to two filters.

    A candidate must clear the funding needed to recover its round-trip cost
    within the recovery window, and an incoming symbol must beat the outgoing one
    by at least that same hurdle -- otherwise the switch costs more than it wins.
    """
    eligible = {s: m for s, m in mean_by_symbol.items()
                if np.isfinite(m) and m >= cost_hurdle[s]}
    ranked = sorted(eligible, key=lambda s: eligible[s], reverse=True)
    target = set(ranked[:cfg.c2_top_n])
    if not prev:
        return target
    keep = prev & set(eligible)
    incoming = [s for s in ranked if s not in prev]
    out = set(keep)
    for s in incoming:
        if len(out) >= cfg.c2_top_n:
            break
        weakest = min(out, key=lambda x: eligible.get(x, -np.inf)) if out else None
        if weakest is None or len(out) < cfg.c2_top_n:
            out.add(s)
            continue
        if eligible[s] - eligible.get(weakest, -np.inf) >= cost_hurdle[s]:
            out.discard(weakest)
            out.add(s)
    return set(list(out)[:cfg.c2_top_n])
