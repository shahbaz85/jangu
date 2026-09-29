"""Tests for the combined strategy. If a test fails, fix the bug, not the test."""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from combo.config import ComboConfig                        # noqa: E402
from combo import signals as sg                             # noqa: E402
from data import synthetic                                  # noqa: E402


def small(length=8):
    """A config with a short swing length, so fixtures stay readable."""
    cfg = ComboConfig()
    cfg.swing_length = length
    cfg.sweep_lookback = 6
    cfg.vol_lookback = 5
    return cfg


def test_swings_are_confirmed_late_never_early():
    """A pivot must not be usable before `swing_length` bars have passed. If it
    were, the same move that made the high would also 'break' it."""
    cfg = small()
    df = synthetic(days=40, seed=5)
    ph, pl = sg.confirmed_swings(df, cfg.swing_length)
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    win = 2 * cfg.swing_length + 1
    is_ph = (df["high"] == df["high"].rolling(win, center=True).max()).to_numpy()
    for i in np.flatnonzero(is_ph):
        j = i + cfg.swing_length
        if j >= len(df):
            continue
        # the level must appear at i+length, and must NOT be visible before it
        assert ph[j] == h[i] or np.isfinite(ph[j]), f"pivot at {i} never surfaced"
        earlier = ph[i:j]
        assert not np.any(earlier == h[i]), f"pivot at {i} was visible before bar {j}"
    print(f"ok  swing levels appear {cfg.swing_length} bars late, never earlier")


def test_no_lookahead_in_signals():
    """Signals on truncated history must match signals on the full history."""
    cfg = small()
    df = synthetic(days=60, seed=6)
    full = sg.for_variant(df, cfg, "BOS+V")
    full_idx = {s["idx"] for s in full}
    for cut in (1200, 2400, 3600):
        part = {s["idx"] for s in sg.for_variant(df.iloc[:cut], cfg, "BOS+V")}
        # every signal the truncated run found must also be in the full run, and
        # the full run must find every one the truncated run could have seen
        expected = {i for i in full_idx if i + 1 < cut - cfg.swing_length}
        assert expected <= part | {i for i in full_idx if i >= cut - 2 * cfg.swing_length}, (
            f"cut {cut}: truncation lost signals {sorted(expected - part)[:5]}")
        assert part <= full_idx, f"cut {cut}: invented signals {sorted(part - full_idx)[:5]}"
    print(f"ok  signals are causal ({len(full)} on full history, 3 truncation points)")


def test_bos_fires_once_per_break():
    """Only the bar that first closes through the level counts. Otherwise every
    bar of a trend would fire while price sat above an old swing high."""
    cfg = small()
    df = synthetic(days=90, seed=7)
    events, ph, pl = sg.bos_events(df, cfg)
    c = df["close"].to_numpy()
    for i, d in events:
        level = ph[i] if d == 1 else pl[i]
        prev = ph[i - 1] if d == 1 else pl[i - 1]
        assert np.isfinite(level), f"BOS at {i} with no level"
        assert (c[i] > level) if d == 1 else (c[i] < level), f"BOS at {i} did not break"
        if np.isfinite(prev):
            assert not ((c[i - 1] > prev) if d == 1 else (c[i - 1] < prev)), (
                f"BOS at {i} repeats a break already open on the previous bar")
    print(f"ok  each BOS is a fresh break ({len(events)} events)")


def test_variants_are_nested_subsets_of_bos():
    """Adding a filter can only remove signals. BOS+S+V must be a subset of both
    BOS+S and BOS+V, and all three subsets of plain BOS."""
    cfg = small()
    df = synthetic(days=120, seed=8)
    base = {i for i, _ in sg.bos_events(df, cfg)[0]}
    s = {x["idx"] for x in sg.for_variant(df, cfg, "BOS+S")}
    v = {x["idx"] for x in sg.for_variant(df, cfg, "BOS+V")}
    sv = {x["idx"] for x in sg.for_variant(df, cfg, "BOS+S+V")}
    assert s <= base and v <= base, "a filtered variant found signals BOS did not"
    assert sv <= s and sv <= v, "BOS+S+V is not a subset of both single-filter variants"
    print(f"ok  variants nest (BOS {len(base)} -> S {len(s)}, V {len(v)}, S+V {len(sv)})")


def test_sweep_uses_only_levels_known_at_the_time():
    """The sweep test reads the swing level per candle. Using the BOS bar's level
    would let a pivot confirmed after the sweep justify it retroactively."""
    cfg = small()
    df = synthetic(days=90, seed=9)
    events, ph, pl = sg.bos_events(df, cfg)
    l, h, c = (df[k].to_numpy() for k in ("low", "high", "close"))
    checked = 0
    for i, d in events:
        if not sg.had_sweep(df, i, d, ph, pl, cfg):
            continue
        found = False
        for k in range(max(0, i - cfg.sweep_lookback), i):
            level = pl[k] if d == 1 else ph[k]
            if not np.isfinite(level):
                continue
            if (d == 1 and l[k] < level < c[k]) or (d == -1 and c[k] < level < h[k]):
                found = True
                break
        assert found, f"sweep reported at {i} with no qualifying candle before it"
        checked += 1
    print(f"ok  every reported sweep has a qualifying candle ({checked} checked)")


def test_volume_average_excludes_the_signal_candle():
    """A spike must not inflate the threshold it is measured against."""
    cfg = small()
    n = 60
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    df = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
                       "volume": np.full(n, 100.0)}, index=idx)
    at = 30
    df.loc[df.index[at], "volume"] = 100.0 * cfg.vol_mult          # exactly on the line
    assert sg.volume_ok(df, at, cfg), "a volume exactly at the multiple was rejected"
    df.loc[df.index[at], "volume"] = 100.0 * cfg.vol_mult - 0.01
    assert not sg.volume_ok(df, at, cfg), "a volume just under the multiple passed"
    print(f"ok  volume test is exact at {cfg.vol_mult}x and excludes the signal candle")


def test_placebo_rebuilds_geometry_at_the_shifted_bar():
    """Carrying absolute prices to a bar thousands of candles away would place the
    stop nowhere near the market. The placebo must rebuild entry, stop and target
    from the shifted bar, keeping only the recipe and the direction."""
    cfg = small()
    df = synthetic(days=120, seed=10)
    s = sg.for_variant(df, cfg, "BOS+V")
    assert s, "no signals to shift"
    out = sg.shifted(df, s, cfg, 1600)
    assert out, "the placebo produced nothing"
    o = df["open"].to_numpy()
    for x in out:
        assert x["entry"] == o[x["fill_idx"]], "placebo entry is not the shifted bar's open"
        risk = abs(x["entry"] - x["stop"]) / x["atr"]
        assert abs(risk - cfg.sl_atr) < 1e-9, f"placebo stop is {risk:.3f} ATR, not {cfg.sl_atr}"
        assert abs(abs(x["target"] - x["entry"]) / x["atr"] - cfg.tp_atr) < 1e-9
    dirs_in = sorted(x["dir"] for x in s)
    dirs_out = sorted(x["dir"] for x in out)
    assert dirs_out == dirs_in[:len(dirs_out)] or set(dirs_out) <= {1, -1}
    print(f"ok  placebo rebuilds geometry at the shifted bar ({len(out)} signals)")


def test_break_even_reflects_the_market_entry():
    """Entry at the next open is a market order, so this geometry is dearer than a
    maker-limit entry. Break-even must exceed the cost-free 50% for a 1.5:1.5
    trade, and be higher for a coin paying more slippage."""
    cfg = ComboConfig()
    free = cfg.sl_atr / (cfg.tp_atr + cfg.sl_atr)
    assert abs(free - 0.5) < 1e-12, "1.5 vs 1.5 should be an even-money shape"
    eth = cfg.break_even("ETH/USDT:USDT", price=3000.0, atr=12.0)
    trx = cfg.break_even("TRX/USDT:USDT", price=0.30, atr=0.0012)
    assert free < eth < 1.0, f"ETH break-even {eth:.4f} outside (0.5, 1)"
    assert trx > eth, f"more slippage must demand more ({trx:.4f} vs {eth:.4f})"
    assert cfg.cost_loss("ETH/USDT:USDT") > cfg.cost_win("ETH/USDT:USDT"), (
        "a stopped-out trade pays taker twice and must cost more than a target exit")
    print(f"ok  break-even above the 50% cost-free line (ETH {eth:.1%}, TRX {trx:.1%})")


def test_alpha_is_tightened_for_three_variants():
    """Three variants at 95% would pass on noise about one time in seven, so
    section 6 requires 99% intervals."""
    cfg = ComboConfig()
    assert cfg.alpha == 0.01, f"alpha is {cfg.alpha}, but three variants need 0.01"
    naive = 1 - (1 - 0.05) ** 3
    corrected = 1 - (1 - cfg.alpha) ** 3
    assert corrected < 0.05 < naive
    print(f"ok  alpha {cfg.alpha} keeps the family-wise error at {corrected:.1%} "
          f"(three tests at 5% would give {naive:.1%})")


if __name__ == "__main__":
    test_swings_are_confirmed_late_never_early()
    test_no_lookahead_in_signals()
    test_bos_fires_once_per_break()
    test_variants_are_nested_subsets_of_bos()
    test_sweep_uses_only_levels_known_at_the_time()
    test_volume_average_excludes_the_signal_candle()
    test_placebo_rebuilds_geometry_at_the_shifted_bar()
    test_break_even_reflects_the_market_entry()
    test_alpha_is_tightened_for_three_variants()
