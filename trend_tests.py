"""Run: python trend_tests.py

Same discipline as tests.py: features computed on truncated history must match
features computed on the full history for every bar <= the truncation point, and
signals found on truncated history must be identical to those found on full history.
"""
import pandas as pd

from trend_config import TrendConfig
from data import synthetic
from trend_features import build_trend_features
from trend_engine import TrendEngine

COLS = ["atr", "ema_pullback", "htf_trend", "htf_sep_atr"]


def test_no_lookahead():
    cfg = TrendConfig()
    df = synthetic(days=200, seed=4)
    full = build_trend_features(df, cfg)
    for k in (1500, 3000, len(df) - 1):
        part = build_trend_features(df.iloc[:k + 1], cfg)
        a, b = full[COLS].iloc[:k + 1], part[COLS]
        pd.testing.assert_frame_equal(a, b, check_dtype=False, obj=f"truncated at {k}")
    print("ok  no lookahead in trend_features")


def test_signals_stable():
    cfg = TrendConfig(min_trend_atr_sep=0.3, atr_pct_low=0.05, atr_pct_high=0.99)
    df = synthetic(days=200, seed=4)
    full, _, _ = TrendEngine(cfg).run(df, "T")
    cut = len(df) * 2 // 3
    part, _, _ = TrendEngine(cfg).run(df.iloc[:cut], "T")
    a = [(s["id"], round(s["entry"], 6)) for s in full if s["idx"] < cut]
    b = [(s["id"], round(s["entry"], 6)) for s in part]
    assert a == b, (a, b)
    print(f"ok  trend signals stable under truncation ({len(b)} signals compared)")


if __name__ == "__main__":
    test_no_lookahead()
    test_signals_stable()
