"""Tests for the 1H translation of V3 (Stage 0 of V3_1H_SPEC.md).

A note that matters for the spec's Stage 1 falsification check: `data.synthetic()`
draws volume from a uniform distribution, so a bar can never exceed 3x its own
24-bar average. V3 therefore fires zero times on synthetic data, and "V3 fails the
gate on synthetic" would be satisfied for the wrong reason -- no signals rather
than no edge. These tests use a constructed fixture with real volume spikes so
the translated rule is exercised rather than starved.

If a test fails, fix the bug, not the test.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from mr70.indicators import build_features                              # noqa: E402
from mr70.signals import v3_vwap_climax                                 # noqa: E402
from mr70.v3_1h import V3OneHourConfig, required_rate, shifted_signals  # noqa: E402


def fixture(n=400, spike_at=300, seed=3):
    """Quiet 1H bars with one engineered climax: a stretch below session VWAP, a
    volume spike, and a long lower wick.

    The volume multiple is kept just above the 3x threshold rather than made
    dramatic. Session VWAP is volume-weighted and includes the current bar, so a
    large spike pulls VWAP down toward its own price and cancels the deviation it
    is supposed to create. That is a real property of V3, not an artefact: an
    early version of this fixture used 9x and produced a deviation less than half
    the threshold.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    close = 100 + np.cumsum(rng.normal(0, 0.05, n))
    df = pd.DataFrame({"open": close, "high": close + 0.1, "low": close - 0.1,
                       "close": close, "volume": np.full(n, 100.0)}, index=idx)
    c0 = df["close"].iloc[spike_at]
    df.loc[df.index[spike_at], ["open", "high", "low", "close", "volume"]] = [
        c0 - 2.9, c0 - 2.8, c0 - 8.0, c0 - 3.0, 350.0]
    return df


def test_config_converts_windows_by_time():
    """96 bars of 15m is 24 hours, so the 1H equivalent is 24 bars. The time stop
    follows the spec's diffusion argument: 64 x 15m = 16 x 1H."""
    cfg = V3OneHourConfig()
    assert cfg.vwap_std_len == 24 and cfg.vol_avg_len == 24, "VWAP/volume windows not converted"
    assert cfg.max_bars == 16, "time stop not converted"
    assert cfg.base_tf == "1h" and cfg.days == 1460, "wrong timeframe or history"
    assert cfg.tp_atr == 0.75 and cfg.sl_atr == 2.0, "geometry must not change"
    assert len(cfg.symbols) == 12, "spec fixes 12 symbols"
    print(f"ok  windows converted by time (24-bar VWAP/volume, {cfg.max_bars}-bar stop)")


def test_slippage_split_matches_spec():
    """0.02% for BTC and ETH, 0.04% for the other ten."""
    cfg = V3OneHourConfig()
    for s in ("BTC/USDT:USDT", "ETH/USDT:USDT"):
        assert cfg.slip_for(s) == 0.0002, f"{s} should use the low slippage tier"
    for s in ("SOL/USDT:USDT", "TRX/USDT:USDT", "LINK/USDT:USDT"):
        assert cfg.slip_for(s) == 0.0004, f"{s} should use the high slippage tier"
    print("ok  slippage is 0.02% for BTC/ETH and 0.04% for the other ten")


def test_v3_fires_on_a_real_climax():
    """The translated rule must still detect the thing it was written for."""
    cfg = V3OneHourConfig()
    f = build_features(fixture(), cfg)
    sigs = v3_vwap_climax(f, cfg)
    assert sigs, "V3 found no signal in a frame built to contain exactly one"
    i, d = sigs[0]
    assert d == 1, f"a stretch below VWAP with a lower wick must be long, got {d}"
    assert f["volume"].to_numpy()[i] > cfg.vol_mult * f["vol_avg"].to_numpy()[i]
    assert f["lower_wick"].to_numpy()[i] >= cfg.wick_frac
    print(f"ok  V3 fires on an engineered 1H climax ({len(sigs)} signal at bar {i})")


def test_shifted_placebo_preserves_construction():
    """The placebo must keep direction and count and move only in time, wrapping
    rather than dropping so late-sample signals are not discarded."""
    cfg = V3OneHourConfig()
    n = 1000
    sigs = [(10, 1), (500, -1), (990, 1)]
    for shift in (-800, -400, 400, 800):
        out = shifted_signals(sigs, n, shift)
        assert len(out) == len(sigs), f"shift {shift} lost signals"
        assert [d for _, d in out] == [d for _, d in sigs], f"shift {shift} changed direction"
        assert all(0 <= i < n for i, _ in out), f"shift {shift} left the sample"
        assert [i for i, _ in out] != [i for i, _ in sigs], f"shift {shift} moved nothing"
    print("ok  time-shifted placebo keeps direction and count, wraps at the edges")


def test_required_rate_is_sane_and_cost_ordered():
    """Break-even must exceed the cost-free 2.0/(0.75+2.0) = 72.7%, and must be
    higher for a symbol that pays more slippage."""
    cfg = V3OneHourConfig()
    f = build_features(fixture(), cfg)
    i = v3_vwap_climax(f, cfg)[0][0]
    free = cfg.sl_atr / (cfg.tp_atr + cfg.sl_atr)
    btc = required_rate(f, i, cfg, "BTC/USDT:USDT")
    alt = required_rate(f, i, cfg, "TRX/USDT:USDT")
    assert free < btc < 1.0, f"BTC break-even {btc:.4f} outside ({free:.4f}, 1)"
    assert alt > btc, f"higher slippage must demand a higher hit rate ({alt:.4f} vs {btc:.4f})"
    print(f"ok  break-even above the {free:.1%} cost-free line "
          f"(BTC {btc:.1%}, TRX {alt:.1%})")


def test_no_lookahead_in_1h_features():
    """Features on truncated history must equal features on full history."""
    cfg = V3OneHourConfig()
    df = fixture(n=600, spike_at=400)
    full = build_features(df, cfg)
    cols = ["atr", "vwap", "vwap_dev", "vwap_sd", "vol_avg", "lower_wick", "upper_wick"]
    for cut in (200, 350, 500):
        part = build_features(df.iloc[:cut], cfg)
        a = full.iloc[cut - 1][cols].to_numpy(float)
        b = part.iloc[-1][cols].to_numpy(float)
        assert np.allclose(a, b, equal_nan=True), f"bar {cut - 1} changed when the future was hidden"
    print(f"ok  1H features are causal ({len(cols)} columns, 3 truncation points)")


if __name__ == "__main__":
    test_config_converts_windows_by_time()
    test_slippage_split_matches_spec()
    test_v3_fires_on_a_real_climax()
    test_shifted_placebo_preserves_construction()
    test_required_rate_is_sane_and_cost_ordered()
    test_no_lookahead_in_1h_features()
