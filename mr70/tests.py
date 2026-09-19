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


def _climax_fixture(direction: int, n: int = 400, spike_at: int = 320):
    """Flat, low-volume tape with one deliberate climax candle: a volume spike that
    closes far from VWAP with a long rejection wick. Used to prove V3's code path
    fires -- synthetic() has uniform volume and can never trigger a 3x climax."""
    idx = pd.date_range("2025-03-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(1)
    close = 100 + rng.normal(0, 0.02, n)
    o = close + rng.normal(0, 0.01, n)
    h = np.maximum(o, close) + 0.02
    l = np.minimum(o, close) - 0.02
    vol = np.full(n, 100.0)
    if direction == 1:                      # capitulation low: deep wick, closes below VWAP
        l[spike_at] = 90.0
        close[spike_at] = 99.0
        o[spike_at] = 100.0
        h[spike_at] = 100.2
    else:                                   # blow-off high: mirror
        h[spike_at] = 110.0
        close[spike_at] = 101.0
        o[spike_at] = 100.0
        l[spike_at] = 99.8
    # 5x the average: comfortably past the 3x gate, but NOT so large that the candle's
    # own volume drags session VWAP onto itself and cancels its own deviation --
    # a real effect in V3, strongest early in the UTC day (see step 2 notes)
    vol[spike_at] = 500.0
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": close, "volume": vol}, index=idx)


def test_v3_fires_on_a_real_climax():
    cfg = MR70Config()
    from mr70.signals import v3_vwap_climax
    for d in (1, -1):
        f = build_features(_climax_fixture(d), cfg)
        sig = v3_vwap_climax(f, cfg)
        assert sig, f"V3 did not fire on a deliberate {'long' if d == 1 else 'short'} climax"
        assert all(s[1] == d for s in sig), "V3 fired in the wrong direction on the climax"
    print("ok  V3 fires on a constructed volume climax (code path verified)")


def test_signal_conditions_hold():
    """Every variant must actually satisfy its own definition at the bars it fires,
    in the direction it claims. Catches inverted logic, which would otherwise show
    up as a fake edge."""
    from mr70.signals import VARIANTS, valid_mask
    cfg = MR70Config()
    df = synthetic(days=200, seed=11)
    f = build_features(df, cfg)
    v = valid_mask(f)
    c, t = f["close"].to_numpy(), f["trend_4h"].to_numpy()
    counts = {}

    for name, fn in VARIANTS.items():
        sig = fn(f, cfg)
        counts[name] = len(sig)
        assert all(v[i] for i, _ in sig), f"{name} fired on a bar with missing features"
        assert list(sig) == sorted(sig), f"{name} signals not chronological"

    for i, d in VARIANTS["V1"](f, cfg):
        assert f["adx_1h"].iloc[i] < cfg.adx_max
        if d == 1:
            assert c[i] < f["bb_lo"].iloc[i] and f["rsi"].iloc[i] < cfg.rsi_low
        else:
            assert c[i] > f["bb_hi"].iloc[i] and f["rsi"].iloc[i] > cfg.rsi_high

    for i, d in VARIANTS["V2"](f, cfg):
        assert t[i] == d, "V2 traded against its own 4H trend filter"
        if d == 1:
            assert f["rsi_fast"].iloc[i] < cfg.rsi2_low and c[i] > f["ema_base"].iloc[i]
        else:
            assert f["rsi_fast"].iloc[i] > cfg.rsi2_high and c[i] < f["ema_base"].iloc[i]

    for i, d in VARIANTS["V3"](f, cfg):
        assert f["volume"].iloc[i] > cfg.vol_mult * f["vol_avg"].iloc[i]
        dev, sd = f["vwap_dev"].iloc[i], f["vwap_sd"].iloc[i]
        if d == 1:
            assert dev < -cfg.vwap_k * sd, "V3 long did not fire below VWAP stretch"
            assert f["lower_wick"].iloc[i] >= cfg.wick_frac
        else:
            assert dev > cfg.vwap_k * sd, "V3 short did not fire above VWAP stretch"
            assert f["upper_wick"].iloc[i] >= cfg.wick_frac

    v3 = set(VARIANTS["V3"](f, cfg))
    for s in VARIANTS["V4"](f, cfg):
        assert s in v3, "V4 fired where V3 did not"
        assert t[s[0]] == s[1], "V4 traded against the 4H trend"

    print(f"ok  signal conditions hold for every variant {counts}")


def test_paper_observer():
    """The paper observer must: detect a signal, resolve both geometries, never
    duplicate a signal across restarts, never invent a result for an unfilled
    entry, and contain no order-placing code at all."""
    import pandas as pd
    from dataclasses import replace as _replace
    import mr70.paper_live as pl

    cfg = _replace(MR70Config(), symbols=["T/USDT:USDT"])
    base = _climax_fixture(1, n=400, spike_at=320)
    cols = list(base.columns)
    ci = {k: cols.index(k) for k in cols}

    def tape(fill: bool):
        v = base.values.copy()
        if fill:
            v[321, ci["low"]] = 98.5
            for k in range(322, 340):
                v[k, ci["open"]], v[k, ci["close"]] = 99.5, 101.0
                v[k, ci["high"]], v[k, ci["low"]] = 101.5, 99.4
        else:                                   # gaps away, limit never trades
            for k in range(321, 340):
                v[k, ci["low"]], v[k, ci["open"]] = 100.5, 100.6
                v[k, ci["close"]], v[k, ci["high"]] = 101.0, 101.5
        return pd.DataFrame(v, index=base.index, columns=cols)

    orig = pl.fetch_ohlcv
    try:
        pl.fetch_ohlcv = lambda *a, **k: tape(True)

        # a symbol's first run must NOT log history as if it were forward data:
        # it only sets the watermark, keeping the logged sample out of sample
        st0 = {"symbols": {}}
        s0, t0, f0 = pl.process_symbol("T/USDT:USDT", cfg, st0, True)
        assert not s0 and not t0 and not f0, "first run logged backfill as live data"
        assert st0["symbols"]["T/USDT:USDT"]["last_ts"], "watermark not set on first run"

        st = {"symbols": {}}
        sigs, trades, fwd = pl.process_symbol("T/USDT:USDT", cfg, st, True, backfill=True)
        assert len(sigs) == 1, "observer missed the climax signal"
        assert len(trades) == len(pl.GEOMETRIES), "both geometries must be reported"
        assert all(t["status"] == "tp" for t in trades), "engineered rally should hit TP"
        assert all(t["R_net"] < t["R_gross"] for t in trades), "costs must reduce net R"
        assert len(fwd) == pl.FORWARD_BARS, "forward bars not captured for re-analysis"

        again, _, _ = pl.process_symbol("T/USDT:USDT", cfg, st, False, backfill=True)
        assert not again, "same signal logged twice across a restart"

        pl.fetch_ohlcv = lambda *a, **k: tape(False)
        st2 = {"symbols": {}}
        _, unf, _ = pl.process_symbol("T/USDT:USDT", cfg, st2, True, backfill=True)
        assert all(t["status"] == "unfilled" for t in unf), "entry should not have filled"
        assert not any("R_net" in t for t in unf), "unfilled entry must not produce a result"
    finally:
        pl.fetch_ohlcv = orig

    src = (pathlib.Path(pl.__file__)).read_text()
    banned = ("create_order", "apiKey", "secret", "private_", "place_order")
    found = [w for w in banned if w in src]
    assert not found, f"paper observer contains order-capable code: {found}"
    print("ok  paper observer: forward-only by default, both geometries, "
          "restart-safe, no order code")


if __name__ == "__main__":
    test_atr_matches_wilder()
    test_rsi_bounds()
    test_bollinger_geometry()
    test_adx_range()
    test_vwap_resets_daily()
    test_htf_not_early()
    test_no_lookahead()
    test_v3_fires_on_a_real_climax()
    test_signal_conditions_hold()
    test_paper_observer()
