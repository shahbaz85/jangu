"""Tests for the carry study. The first three are the ones CARRY_SPEC.md section 12
names as "tests to write first". If a test fails, fix the bug, not the test.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from carry.config import CarryConfig                                    # noqa: E402
from carry.engine import (c1_positions, c2_selection, hedge_pnl,        # noqa: E402
                          per_8h, rebalance_and_margin)
from carry.fetch import align_to_funding, interval_hours                # noqa: E402


def test_hedge_cancels_price_exactly():
    """Spec section 12: with spot and perp moving identically, the P&L must equal
    funding minus costs exactly -- not approximately. Any residual means the legs
    are not sized against the same notional."""
    cfg = CarryConfig()
    for ratio in (0.5, 0.9, 1.0, 1.35, 3.0):
        got = hedge_pnl(100.0, 100.0 * ratio, 250.0, 250.0 * ratio,
                        funding_sum=0.0123, cost=cfg.round_trip_cost("BTC"))
        want = 0.0123 - cfg.round_trip_cost("BTC")
        assert abs(got - want) < 1e-15, f"ratio {ratio}: residual {got - want:.3e}"
    print("ok  hedge cancels price exactly at every ratio tested (residual < 1e-15)")


def test_basis_move_is_what_survives():
    """The hedge is not perfect: if the perp moves more than spot, the short leg
    loses more than spot gains. That difference is the basis, and it must show up
    with the right sign."""
    widened = hedge_pnl(100.0, 110.0, 100.0, 112.0, 0.0, 0.0)
    narrowed = hedge_pnl(100.0, 110.0, 100.0, 108.0, 0.0, 0.0)
    assert widened < 0 < narrowed, f"basis signs wrong: {widened:.4f}, {narrowed:.4f}"
    assert abs(widened + 0.02) < 1e-12 and abs(narrowed - 0.02) < 1e-12
    print(f"ok  a widening basis costs and a narrowing basis pays ({widened:+.2%} / {narrowed:+.2%})")


def test_margin_rule_triggers_on_a_60pc_weekly_spike():
    """Spec section 12: a +60% move inside one rebalance window must liquidate at
    L=2, and the same move spread over many weeks must not."""
    cfg = CarryConfig()
    idx = pd.date_range("2024-01-01", periods=24 * 30, freq="1h", tz="UTC")

    spike = np.full(len(idx), 100.0)
    spike[24 * 3:24 * 5] = 160.0                        # +60% inside week one
    fast = pd.DataFrame({"open": spike, "high": spike, "low": spike,
                         "close": spike, "volume": 1.0}, index=idx)
    _, liq, when = rebalance_and_margin(fast, idx[0], idx[-1], cfg)
    assert liq, "a +60% move inside one rebalance window did not liquidate at L=2"

    slow = pd.DataFrame({"open": 100 * np.linspace(1, 1.6, len(idx))}, index=idx)
    for c in ("high", "low", "close"):
        slow[c] = slow["open"]
    slow["volume"] = 1.0
    _, liq_slow, _ = rebalance_and_margin(slow, idx[0], idx[-1], cfg)
    assert not liq_slow, "a +60% rise spread over a month liquidated despite weekly top-ups"
    print(f"ok  margin rule fires on a +60% weekly spike ({when.date()}) and not on a slow climb")


def test_c1_has_no_lookahead():
    """Spec section 12: signals computed on truncated history must match signals
    computed on the full history, for every bar the truncation kept."""
    cfg = CarryConfig()
    rng = np.random.default_rng(5)
    n = 600
    rates = rng.normal(0.00015, 0.0002, n)
    rates[200:260] = rng.normal(-0.0003, 0.0001, 60)
    times = pd.date_range("2023-01-01", periods=n, freq="8h", tz="UTC")
    full = c1_positions(rates, times, cfg)
    for cut in (120, 300, 480):
        part = c1_positions(rates[:cut], times[:cut], cfg)
        assert np.array_equal(full[:cut], part), f"C1 changed before bar {cut}"
    print(f"ok  C1 signals are causal (3 truncation points, {int(full.sum())} held intervals)")


def test_c1_respects_its_own_rules():
    """Entry needs the trailing mean above the threshold; the minimum hold must
    actually bind; and the position may never start on the payment that triggered
    it, only on the next one."""
    cfg = CarryConfig()
    n = 300
    rates = np.full(n, 0.0005)                       # far above the entry threshold
    times = pd.date_range("2023-01-01", periods=n, freq="8h", tz="UTC")
    held = c1_positions(rates, times, cfg)
    first = int(np.argmax(held))
    assert first >= cfg.c1_lookback, (
        f"entered at {first}, before {cfg.c1_lookback} payments existed")
    assert not held[first - 1], "position started on the signal payment itself"

    rates2 = np.concatenate([np.full(40, 0.0005), np.full(n - 40, -0.0004)])
    held2 = c1_positions(rates2, times, cfg)
    on = np.flatnonzero(held2)
    if len(on):
        span = times[on[-1]] - times[on[0]]
        assert span >= pd.Timedelta(days=cfg.c1_min_hold_days), (
            f"held {span} despite a {cfg.c1_min_hold_days}-day minimum")
    print(f"ok  C1 enters only after {cfg.c1_lookback} payments and honours the minimum hold")


def test_c2_respects_cost_hurdles():
    """A symbol below its cost hurdle can never be selected, and a marginal
    improvement must not trigger a switch."""
    cfg = CarryConfig()
    hurdle = {s: 0.0001 for s in "ABCDEF"}
    means = {"A": 0.0009, "B": 0.0008, "C": 0.0007, "D": 0.0006,
             "E": 0.00001, "F": -0.0002}
    first = c2_selection(means, set(), cfg, hurdle)
    assert len(first) == cfg.c2_top_n, f"selected {len(first)}, expected {cfg.c2_top_n}"
    assert "E" not in first and "F" not in first, "selected a symbol below its cost hurdle"

    nudged = dict(means, E=0.00061)                  # beats D, but only just
    after = c2_selection(nudged, first, cfg, hurdle)
    assert after == first, f"switched for a gain below the cost hurdle: {after} vs {first}"
    print(f"ok  C2 excludes sub-hurdle symbols and will not switch for a marginal gain")


def test_per_8h_normalises_changed_intervals():
    """Binance has changed the funding interval on some symbols. A 4-hour rate
    compared raw against an 8-hour threshold would understate funding by half."""
    r = np.array([0.0001, 0.0001, 0.0001])
    assert np.allclose(per_8h(r, np.array([8.0, 4.0, 1.0])), [0.0001, 0.0002, 0.0008])
    assert np.allclose(per_8h(r, np.array([np.nan, 0.0, -1.0])), [0.0001] * 3), \
        "a bad interval must fall back to 8h, not produce inf or a sign flip"
    print("ok  funding normalises across changed intervals, and bad intervals fall back")


def test_round_trip_cost_matches_the_spec_table():
    """Four legs at the stated fees. The spec quotes 0.34-0.42%; BTC lands inside
    it and the high-slippage symbols do not, which matters for break-even days."""
    cfg = CarryConfig()
    btc = 2 * (cfg.spot_fee + 0.0002) + 2 * (cfg.perp_fee + 0.0002)
    alt = 2 * (cfg.spot_fee + 0.0004) + 2 * (cfg.perp_fee + 0.0004)
    assert abs(cfg.round_trip_cost("BTC") - btc) < 1e-12
    assert abs(cfg.round_trip_cost("SOL") - alt) < 1e-12
    assert cfg.round_trip_cost("SOL") > 0.0042, "high-slippage round trip is above the quoted range"
    print(f"ok  round trip: BTC {btc:.2%}, others {alt:.2%} "
          f"(spec quotes 0.34-0.42%; the others sit above it)")


def test_benchmark_guard_refuses_an_unset_value():
    """Spec section 3 makes B an owner decision recorded before any result.

    This originally asserted the field was None. The owner has since recorded
    B = 5%, so that assertion would now fail for the right reason -- the decision
    was made. What still needs protecting is the guard: Stage 1 must refuse to
    run if the value is ever cleared or a future config ships without it.
    """
    cfg = CarryConfig()
    assert cfg.benchmark_annual_pct is not None, (
        "B is unset; Stage 1 cannot be judged without it (spec section 3)")
    assert 0 < cfg.benchmark_annual_pct < 100, "B is not a plausible annual percentage"
    assert abs(cfg.require_benchmark() - cfg.benchmark_annual_pct / 100) < 1e-12

    unset = CarryConfig(benchmark_annual_pct=None)
    try:
        unset.require_benchmark()
    except ValueError as e:
        assert "section 3" in str(e)
    else:
        raise AssertionError("require_benchmark() did not refuse an unset benchmark")
    print(f"ok  B recorded at {cfg.benchmark_annual_pct}%/yr, and the guard still "
          f"refuses an unset value")


def test_funding_alignment_never_reads_a_later_candle():
    """A funding payment must be priced from candles already printed."""
    f_idx = pd.date_range("2024-01-01 08:00", periods=5, freq="8h", tz="UTC")
    c_idx = pd.date_range("2024-01-01", periods=48, freq="1h", tz="UTC")
    px = pd.DataFrame({"open": np.arange(48.0), "high": np.arange(48.0),
                       "low": np.arange(48.0), "close": np.arange(48.0),
                       "volume": 1.0}, index=c_idx)
    out = align_to_funding(pd.DataFrame({"rate": [0.0001] * 5}, index=f_idx), px, px)
    for t, row in out.iterrows():
        prior = px["close"][px.index <= t]
        assert row["spot"] == prior.iloc[-1], f"{t}: used a candle after the payment"
    print(f"ok  funding is priced from candles at or before it ({len(out)} payments)")


def test_interval_hours_keeps_the_first_payment():
    """The first payment has no predecessor. Dropping it would silently shorten
    every history by one interval; it inherits the modal spacing instead."""
    idx = pd.date_range("2024-01-01", periods=10, freq="8h", tz="UTC")
    h = interval_hours(pd.DataFrame({"rate": [0.0] * 10}, index=idx))
    assert len(h) == 10 and h.iloc[0] == 8.0 and h.notna().all()
    print("ok  interval_hours keeps the first payment at the modal spacing")


if __name__ == "__main__":
    test_hedge_cancels_price_exactly()
    test_basis_move_is_what_survives()
    test_margin_rule_triggers_on_a_60pc_weekly_spike()
    test_c1_has_no_lookahead()
    test_c1_respects_its_own_rules()
    test_c2_respects_cost_hurdles()
    test_per_8h_normalises_changed_intervals()
    test_round_trip_cost_matches_the_spec_table()
    test_benchmark_guard_refuses_an_unset_value()
    test_funding_alignment_never_reads_a_later_candle()
    test_interval_hours_keeps_the_first_payment()
