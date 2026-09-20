"""Feature assembly for Strategy A (15m only) and Strategy B (4H/1H/30m/15m cascade).

Every higher-timeframe column is attached on the HTF candle's CLOSE time, so a 15m
bar can only ever see HTF information that had already finished forming.
"""
import numpy as np
import pandas as pd

from shared.indicators import (atr, attach_htf, body_frac, displacement_zones, ema,
                               fractal_pivots, market_structure, prev_day_extremes,
                               resample, stoch_rsi, sweep_flags)


def build_features(df: pd.DataFrame, cfg) -> pd.DataFrame:
    df = df.sort_index()
    f = df.copy()
    close = df["close"]

    # ---------------- 15m, shared ----------------
    f["atr"] = atr(df, cfg.atr_len)
    f["body_frac"] = body_frac(df)
    close_time = pd.Series(df.index + pd.Timedelta(cfg.base_tf))
    f["close_time"] = close_time.to_numpy()
    f["ct_epoch"] = close_time.astype("int64").to_numpy()

    # ---------------- Strategy A ----------------
    f["ema_fast"] = ema(close, cfg.ema_fast)
    f["ema_slow"] = ema(close, cfg.ema_slow)
    f["ema_fast_rising"] = f["ema_fast"] > f["ema_fast"].shift(cfg.ema_slope_bars)
    f["ema_slow_rising"] = f["ema_slow"] > f["ema_slow"].shift(cfg.ema_slope_bars)
    f["ema_cross_up_recent"] = ((f["ema_fast"] > f["ema_slow"]) &
                                (f["ema_fast"].shift(1) <= f["ema_slow"].shift(1))
                                ).rolling(cfg.cross_lookback, min_periods=1).max().astype(bool)
    f["ema_cross_dn_recent"] = ((f["ema_fast"] < f["ema_slow"]) &
                                (f["ema_fast"].shift(1) >= f["ema_slow"].shift(1))
                                ).rolling(cfg.cross_lookback, min_periods=1).max().astype(bool)

    k, d = stoch_rsi(close, cfg.rsi_len, cfg.stoch_len, cfg.stoch_k, cfg.stoch_d)
    f["stoch_k"], f["stoch_d"] = k, d
    f["k_rising"] = k > k.shift(1)
    crossed_up = (k > cfg.stoch_low) & (k.shift(1) <= cfg.stoch_low)
    crossed_dn = (k < cfg.stoch_high) & (k.shift(1) >= cfg.stoch_high)
    f["k_crossed_up_recent"] = crossed_up.rolling(cfg.stoch_cross_bars, min_periods=1).max().astype(bool)
    f["k_crossed_dn_recent"] = crossed_dn.rolling(cfg.stoch_cross_bars, min_periods=1).max().astype(bool)

    vol = df["volume"]
    f["vol_avg_prev"] = vol.rolling(cfg.vol_avg_bars).mean().shift(1)
    v5 = vol.rolling(cfg.vol_trend_bars).mean()
    f["vol_trend_up"] = v5 > v5.shift(cfg.vol_trend_bars)

    # confirmed 15m fractal swings, for the ABCD legs
    is_sh, is_sl = fractal_pivots(df, cfg.fractal_n)
    f["swing_high"] = df["high"].where(is_sh).shift(cfg.fractal_n)
    f["swing_low"] = df["low"].where(is_sl).shift(cfg.fractal_n)

    ms15 = market_structure(df, cfg.fractal_n)
    f["event"] = ms15["event"]
    f["last_sh"], f["last_sl"] = ms15["last_sh"], ms15["last_sl"]
    f["low_10"] = df["low"].rolling(cfg.stop_lookback).min()
    f["high_10"] = df["high"].rolling(cfg.stop_lookback).max()

    # ---------------- Strategy B: 4H direction ----------------
    h4 = resample(df, cfg.htf_4h)
    h4f = pd.DataFrame({"trend_4h": market_structure(h4, cfg.fractal_n_htf)["trend"]},
                       index=h4.index)

    # ---------------- Strategy B: 1H displacement zones ----------------
    h1 = resample(df, cfg.htf_1h)
    h1f = displacement_zones(h1, cfg.atr_len, cfg.impulse_body_atr)

    # ---------------- Strategy B: 30m sweeps ----------------
    m30 = resample(df, cfg.htf_30m)
    pdl30, pdh30 = prev_day_extremes(m30)
    sw = sweep_flags(m30, cfg.fractal_n_htf, pdl30, pdh30)
    # numeric epoch first: a datetime column cannot hold NaN for the ffill below
    m30_ct = pd.Series((m30.index + pd.Timedelta(cfg.htf_30m)).astype("int64").astype("float64"),
                       index=m30.index)
    m30f = pd.DataFrame({
        "sweep_low_epoch": m30_ct.where(sw["sweep_low"]).ffill(),
        "sweep_high_epoch": m30_ct.where(sw["sweep_high"]).ffill(),
        "sweep_low_px": sw["sweep_low_px"].ffill(),
        "sweep_high_px": sw["sweep_high_px"].ffill(),
    }, index=m30.index)

    for part, tf in ((attach_htf(close_time, h4f, cfg.htf_4h), cfg.htf_4h),
                     (attach_htf(close_time, h1f, cfg.htf_1h), cfg.htf_1h),
                     (attach_htf(close_time, m30f, cfg.htf_30m), cfg.htf_30m)):
        part.index = f.index
        f = f.join(part)

    f["trend_4h"] = f["trend_4h"].fillna(0).astype(int)
    return f


FEATURE_COLS = ["atr", "body_frac", "ema_fast", "ema_slow", "stoch_k", "stoch_d",
                "vol_avg_prev", "swing_high", "swing_low", "event", "last_sh", "last_sl",
                "low_10", "high_10", "trend_4h", "dem_bot", "dem_top", "sup_bot",
                "sup_top", "sweep_low_epoch", "sweep_high_epoch", "sweep_low_px",
                "sweep_high_px"]
