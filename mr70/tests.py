"""Run: python mr70/tests.py

The critical test is no-lookahead: every indicator computed on history truncated
at bar k must equal the same indicator computed on the full history, for every
bar <= k. That includes the 1H ADX and 4H EMA attachments -- if a higher-timeframe
value leaks backwards into bars before its candle closed, the edge gate and the
backtest are both lying.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from data import synthetic
from mr70.config import MR70Config
from mr70.indicators import (build_features, FEATURE_COLS, atr, rsi, bollinger, adx,
                             session_vwap, ema)


def test_no_lookahead():
    cfg = MR70Config()
    df = synthetic(days=120, seed=9)
    full = build_features(df, cfg)
    for k in (2000, 5000, 8000, len(df) - 1):
        part = build_features(df.iloc[:k + 1], cfg)
        pd.testing.assert_frame_equal(full[FEATURE_COLS].iloc[:k + 1], part[FEATURE_COLS],
                                      check_dtype=False, obj=f"truncated at {k}")
    print("ok  no lookahead in features (incl. 1H ADX / 4H EMA attachments)")


def test_htf_not_early():
    """A 4H value must not be visible to a 15m bar that closed before the 4H candle did."""
    cfg = MR70Config()
    df = synthetic(days=40, seed=3)
    f = build_features(df, cfg)
    ct = pd.DatetimeIndex(f["close_time"])
    # the 4H candle covering [T, T+4h) closes at T+4h; its close must equal the
    # attached close_4h only from that moment on, never before
    h4 = df.resample("4h", label="left", closed="left").agg({"close": "last"}).dropna()
    probe = h4.index[10]
    closes_at = probe + pd.Timedelta("4h")
    val = float(h4.loc[probe, "close"])
    # strictly before the HTF candle closes: must NOT be visible
    before = f.loc[ct < closes_at, "close_4h"]
    assert not (before == val).any(), "4H close leaked into bars before its candle closed"
    # from the instant it closes (the final 15m bar closes at the same time) until
    # the next 4H candle closes: must be visible
    after = f.loc[(ct >= closes_at) & (ct < closes_at + pd.Timedelta("4h")), "close_4h"]
    assert len(after) and (after == val).all(), "4H close not attached once its candle closed"
    print("ok  4H values attach exactly when the HTF candle closes, not before")


def test_rsi_bounds():
    cfg = MR70Config()
    df = synthetic(days=30, seed=5)
    for n in (cfg.rsi_len, cfg.rsi_fast):
        r = rsi(df["close"], n).dropna()
        assert r.between(0, 100).all(), f"RSI({n}) out of bounds"
    # a monotonically rising series pins RSI at 100
    up = pd.Series(np.arange(100, dtype=float))
    assert rsi(up, 14).iloc[-1] == 100
    print("ok  RSI bounds and extremes")


def test_atr_matches_wilder():
    df = synthetic(days=20, seed=2)
    a = atr(df, 14)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - df["close"].shift(1)).abs(),
                    (df["low"] - df["close"].shift(1)).abs()], axis=1).max(axis=1)
    manual = tr.ewm(alpha=1 / 14, adjust=False).mean()
    pd.testing.assert_series_equal(a, manual, check_names=False)
    assert (a.dropna() > 0).all()
    print("ok  ATR is Wilder-smoothed true range")


def test_vwap_resets_daily():
    cfg = MR70Config()
    df = synthetic(days=10, seed=8)
    v = session_vwap(df)
    first_of_day = df.index.normalize() != pd.Series(df.index, index=df.index).shift(1).dt.normalize()
    tp = (df["high"] + df["low"] + df["close"]) / 3
    # on each day's first bar VWAP is just that bar's typical price
    same = np.isclose(v[first_of_day.to_numpy()], tp[first_of_day.to_numpy()])
    assert same.all(), "VWAP did not reset at 00:00 UTC"
    print("ok  session VWAP resets daily at 00:00 UTC")


def test_bollinger_geometry():
    cfg = MR70Config()
    df = synthetic(days=20, seed=4)
    lo, mid, hi = bollinger(df["close"], cfg.bb_len, cfg.bb_k)
    d = (hi - mid).dropna()
    assert np.allclose(d, (mid - lo).dropna()), "bands not symmetric"
    assert (d >= 0).all()
    print("ok  Bollinger bands symmetric around the mean")


def test_adx_range():
    cfg = MR70Config()
    df = synthetic(days=60, seed=6)
    a = adx(df, cfg.adx_len).dropna()
    assert a.between(0, 100).all(), "ADX out of bounds"
    print("ok  ADX within 0-100")


if __name__ == "__main__":
    test_atr_matches_wilder()
    test_rsi_bounds()
    test_bollinger_geometry()
    test_adx_range()
    test_vwap_resets_daily()
    test_htf_not_early()
    test_no_lookahead()
