"""Run: python tests.py

The most important test is the lookahead check: features computed on data truncated at bar k
must equal features computed on the full data for every bar <= k. If this ever fails after you
edit features.py, your backtest is lying to you.
"""
import numpy as np
import pandas as pd

from config import Config
from data import synthetic
from features import build_features, fvg_flags, atr
from engine import SMCEngine
from risk import position_size

COLS = ["atr", "last_sh", "last_sl", "trend", "event", "bull_fvg", "bear_fvg", "pdh", "pdl", "asia_h",
        "asia_l", "fresh_pdl", "fresh_asia_h", "eqh", "eql", "h4_trend", "h1_hi", "h1_lo",
        "h1_bull_fvg_bot", "h1_bear_fvg_top"]


def test_no_lookahead():
    cfg = Config()
    df = synthetic(days=40, seed=5)
    full = build_features(df, cfg)
    for k in (1500, 2222, 3000, len(df) - 1):
        part = build_features(df.iloc[:k + 1], cfg)
        a, b = full[COLS].iloc[:k + 1], part[COLS]
        pd.testing.assert_frame_equal(a, b, check_dtype=False, obj=f"truncated at {k}")
    print("ok  no lookahead in features")


def test_signals_stable():
    """Signals found on truncated history must be identical to those found on full history."""
    cfg = Config(displacement_body_atr=1.0, max_sl_atr=3.0, allow_counter_trend=True,
                 require_premium_discount=False, grade_b=3, min_rr=1.5)
    df = synthetic(days=120, seed=3)
    full, _, _ = SMCEngine(cfg).run(df, "T")
    cut = len(df) * 2 // 3
    part, _, _ = SMCEngine(cfg).run(df.iloc[:cut], "T")
    a = [(s["id"], round(s["entry"], 6)) for s in full if s["idx"] < cut]
    b = [(s["id"], round(s["entry"], 6)) for s in part]
    assert a == b, (a, b)
    print(f"ok  signals stable under truncation ({len(b)} signals compared)")


def test_fvg():
    idx = pd.date_range("2025-01-01", periods=3, freq="15min", tz="UTC")
    df = pd.DataFrame({"open": [100, 101, 106], "high": [101, 107, 108], "low": [99, 100.5, 103],
                       "close": [100.5, 106, 107], "volume": 1}, index=idx)
    bull, bear = fvg_flags(df, pd.Series(1.0, index=idx), 0.2)
    assert bull.iloc[2] and not bear.any()
    print("ok  FVG detection")


def test_sizing():
    cfg = Config()
    s = position_size(1000, 1.0, 60000, 59700, cfg)
    assert abs(s["qty"] * 300 - 10) < 1e-9          # $10 risk over a $300 stop
    assert s["leverage"] <= cfg.max_leverage
    print(f"ok  sizing {s}")


def test_live_pipeline():
    import live
    cfg = Config(displacement_body_atr=1.0, max_sl_atr=3.0, allow_counter_trend=True,
                 require_premium_discount=False, grade_b=3, min_rr=1.5, symbols=["TEST"])
    df = synthetic(days=120, seed=3)
    sigs, _, _ = SMCEngine(cfg).run(df, "TEST")
    k = sigs[-1]["idx"]
    live.fetch_ohlcv = lambda *a, **kw: df.iloc[:k + 1]      # pretend the signal candle just closed
    live.market_context = lambda ex, s: (0.0001, True)
    live.STATE = live.Path("/tmp/_sent.json"); live.JOURNAL = live.Path("/tmp/_journal.csv")
    sent = {}
    live.evaluate(cfg, None, SMCEngine(cfg), sent)
    assert len(sent) == 1
    live.evaluate(cfg, None, SMCEngine(cfg), sent)            # same candle again: no duplicate
    assert len(sent) == 1
    print("ok  live pipeline + dedup")


if __name__ == "__main__":
    test_fvg()
    test_sizing()
    test_no_lookahead()
    test_signals_stable()
    test_live_pipeline()
