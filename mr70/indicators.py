"""MR-70 indicators and feature assembly.

RULE (same as the rest of this repo): every value on row i uses only information
available at the CLOSE of bar i. Higher-timeframe values are attached with
merge_asof on the HTF candle's CLOSE time, so a 4H value only becomes visible to
15m bars after that 4H candle has actually finished.

tests.py enforces this: features computed on truncated history must equal features
computed on full history, for every bar up to the cut.
"""
import numpy as np
import pandas as pd


# ----------------------------------------------------------------- primitives
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing == ewm(alpha=1/n)."""
    return s.ewm(alpha=1 / n, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"],
                      (df["high"] - pc).abs(),
                      (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    gain = wilder(d.clip(lower=0), n)
    loss = wilder((-d).clip(lower=0), n)
    # all-gain windows -> RSI 100, all-loss -> 0, dead-flat -> NaN (no signal)
    out = 100 - 100 / (1 + gain / loss)
    out = out.where(loss > 0, np.where(gain > 0, 100.0, np.nan))
    return out


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0):
    mid = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr_n = wilder(true_range(df), n)
    pdi = 100 * wilder(plus_dm, n) / tr_n
    mdi = 100 * wilder(minus_dm, n) / tr_n
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return wilder(dx.fillna(0.0), n)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price, reset at 00:00 UTC. Cumulative within the
    day, so bar i only ever sees bars <= i of that same day."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.normalize()
    cum_pv = (tp * df["volume"]).groupby(day).cumsum()
    cum_v = df["volume"].groupby(day).cumsum()
    return cum_pv / cum_v.replace(0, np.nan)


def lower_wick_frac(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return (df[["open", "close"]].min(axis=1) - df["low"]) / rng


def upper_wick_frac(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return (df["high"] - df[["open", "close"]].max(axis=1)) / rng


# ------------------------------------------------------------ HTF attachment
def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def attach_htf(base_close_time: pd.Series, htf_frame: pd.DataFrame, htf_len: str) -> pd.DataFrame:
    """Attach HTF columns to base bars only once the HTF candle has CLOSED."""
    x = htf_frame.copy()
    x["close_time"] = x.index + pd.Timedelta(htf_len)
    x = x.reset_index(drop=True).sort_values("close_time")
    b = pd.DataFrame({"close_time": base_close_time.to_numpy()})
    return pd.merge_asof(b, x, on="close_time", direction="backward").drop(columns="close_time")


# ------------------------------------------------------------------ features
def build_features(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """df: OHLCV indexed by UTC candle OPEN time, closed candles only."""
    df = df.sort_index()
    f = df.copy()

    f["atr"] = atr(df, cfg.atr_len)
    f["rsi"] = rsi(df["close"], cfg.rsi_len)
    f["rsi_fast"] = rsi(df["close"], cfg.rsi_fast)
    f["bb_lo"], f["bb_mid"], f["bb_hi"] = bollinger(df["close"], cfg.bb_len, cfg.bb_k)
    f["ema_base"] = ema(df["close"], cfg.ema_base)

    f["vwap"] = session_vwap(df)
    f["vwap_dev"] = df["close"] - f["vwap"]
    f["vwap_sd"] = f["vwap_dev"].rolling(cfg.vwap_std_len).std(ddof=0)
    # trailing average EXCLUDING the current bar, so a volume spike doesn't
    # inflate the threshold it is being measured against
    f["vol_avg"] = df["volume"].rolling(cfg.vol_avg_len).mean().shift(1)
    f["lower_wick"] = lower_wick_frac(df)
    f["upper_wick"] = upper_wick_frac(df)

    close_time = pd.Series(df.index + pd.Timedelta(cfg.base_tf))
    f["close_time"] = close_time.to_numpy()

    h1 = resample(df, cfg.htf_1h)
    h1f = pd.DataFrame({"adx_1h": adx(h1, cfg.adx_len)}, index=h1.index)

    h4 = resample(df, cfg.htf_4h)
    h4f = pd.DataFrame({
        "close_4h": h4["close"],
        "ema50_4h": ema(h4["close"], cfg.ema_htf_fast),
        "ema200_4h": ema(h4["close"], cfg.ema_htf_slow),
    }, index=h4.index)

    for part, tf in ((attach_htf(close_time, h1f, cfg.htf_1h), cfg.htf_1h),
                     (attach_htf(close_time, h4f, cfg.htf_4h), cfg.htf_4h)):
        part.index = f.index
        f = f.join(part)

    f["trend_4h"] = np.where((f["close_4h"] > f["ema200_4h"]) & (f["ema50_4h"] > f["ema200_4h"]), 1,
                      np.where((f["close_4h"] < f["ema200_4h"]) & (f["ema50_4h"] < f["ema200_4h"]), -1, 0))
    return f


FEATURE_COLS = ["atr", "rsi", "rsi_fast", "bb_lo", "bb_mid", "bb_hi", "ema_base",
                "vwap", "vwap_dev", "vwap_sd", "vol_avg", "lower_wick", "upper_wick",
                "adx_1h", "close_4h", "ema50_4h", "ema200_4h", "trend_4h"]
