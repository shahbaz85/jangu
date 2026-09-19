"""Trend-following features: HTF EMA trend bias + 15m EMA pullback trigger.

Same no-lookahead rule as features.py: the HTF trend value only attaches to a base-tf
bar once the HTF candle that produced it has actually closed (via merge_asof).
"""
import numpy as np
import pandas as pd

from features import atr, resample, _asof


def build_trend_features(df: pd.DataFrame, cfg) -> pd.DataFrame:
    df = df.sort_index()
    f = df.copy()
    f["atr"] = atr(df, cfg.atr_len)
    f["atr_pct"] = f["atr"].rolling(cfg.atr_pct_window, min_periods=cfg.atr_pct_window // 4).rank(pct=True)
    f["ema_pullback"] = df["close"].ewm(span=cfg.ema_pullback, adjust=False).mean()
    # backtest.py's shared simulate() reads "event" to cancel a pending order early if
    # an opposing structure break fires (an SMC concept). This engine has no equivalent
    # invalidation signal yet, so it's always 0: cancellation instead relies on
    # order_valid_bars expiry and the runaway-target check, both already active.
    f["event"] = 0

    close_time = pd.Series(df.index + pd.Timedelta(cfg.base_tf))
    f["close_time"] = close_time.to_numpy()

    htf = resample(df, cfg.htf)
    ema_fast = htf["close"].ewm(span=cfg.ema_fast_htf, adjust=False).mean()
    ema_slow = htf["close"].ewm(span=cfg.ema_slow_htf, adjust=False).mean()
    htf_atr = atr(htf, cfg.atr_len)
    htf_f = pd.DataFrame({
        "htf_trend": np.sign(ema_fast - ema_slow).astype(int),
        "htf_sep_atr": (ema_fast - ema_slow) / htf_atr,
    }, index=htf.index)

    part = _asof(close_time, htf_f, cfg.htf)
    part.index = f.index
    f = f.join(part)
    f["htf_trend"] = f["htf_trend"].fillna(0).astype(int)
    return f
