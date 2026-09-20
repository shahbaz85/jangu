"""Shared indicators for Strategy A and Strategy B.

Self-contained on purpose: the MR-70 modules stay frozen so that experiment remains
reproducible, so the primitives that worked there are ported here rather than
imported across.

RULE, enforced by tests.py: every value on row i uses only information available at
the CLOSE of bar i. Higher-timeframe values attach via merge_asof on the HTF
candle's CLOSE time, so a 4H/1H/30m value is never visible to a 15m bar that closed
before it.
"""
import numpy as np
import pandas as pd


# ----------------------------------------------------------------- primitives
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
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
    out = 100 - 100 / (1 + gain / loss)
    return out.where(loss > 0, np.where(gain > 0, 100.0, np.nan))


def stoch_rsi(s: pd.Series, rsi_n: int = 14, stoch_n: int = 14, k: int = 3, d: int = 3):
    """Stochastic RSI: stochastic oscillator applied to RSI, then smoothed.
    Returns (%K, %D), both 0-100."""
    r = rsi(s, rsi_n)
    lo = r.rolling(stoch_n).min()
    hi = r.rolling(stoch_n).max()
    rng = (hi - lo).replace(0, np.nan)
    raw = 100 * (r - lo) / rng
    k_line = raw.rolling(k).mean()
    return k_line, k_line.rolling(d).mean()


def body_frac(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return (df["close"] - df["open"]).abs() / rng


# ------------------------------------------------------------------ structure
def fractal_pivots(df: pd.DataFrame, n: int):
    """True at the pivot bar itself. Uses n future bars, so callers must shift by n
    before treating a pivot as known."""
    h, l = df["high"], df["low"]
    left_h = h.rolling(n).max().shift(1)
    right_h = h[::-1].rolling(n).max().shift(1)[::-1]
    left_l = l.rolling(n).min().shift(1)
    right_l = l[::-1].rolling(n).min().shift(1)[::-1]
    return (h > left_h) & (h > right_h), (l < left_l) & (l < right_l)


def market_structure(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Confirmed swings, trend state and close-based break events.

    A swing is only 'known' n bars after it prints, which is what keeps this honest.
    event: +1 BOS up, +2 CHoCH up, -1 BOS down, -2 CHoCH down.
    """
    is_sh, is_sl = fractal_pivots(df, n)
    sh_conf = df["high"].where(is_sh).shift(n).to_numpy()
    sl_conf = df["low"].where(is_sl).shift(n).to_numpy()
    close = df["close"].to_numpy()
    N = len(df)
    last_sh, last_sl = np.full(N, np.nan), np.full(N, np.nan)
    trend, event = np.zeros(N, int), np.zeros(N, int)
    cur_sh = cur_sl = np.nan
    sh_live = sl_live = False
    t = 0
    for i in range(N):
        if not np.isnan(sh_conf[i]):
            cur_sh, sh_live = sh_conf[i], True
        if not np.isnan(sl_conf[i]):
            cur_sl, sl_live = sl_conf[i], True
        if sh_live and close[i] > cur_sh:
            event[i] = 1 if t == 1 else 2
            t, sh_live = 1, False
        elif sl_live and close[i] < cur_sl:
            event[i] = -1 if t == -1 else -2
            t, sl_live = -1, False
        last_sh[i], last_sl[i], trend[i] = cur_sh, cur_sl, t
    return pd.DataFrame({"last_sh": last_sh, "last_sl": last_sl, "trend": trend,
                         "event": event, "sh_conf": sh_conf, "sl_conf": sl_conf},
                        index=df.index)


# ------------------------------------------------------------ HTF attachment
def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "volume": "sum"}).dropna()


def attach_htf(base_close_time: pd.Series, htf_frame: pd.DataFrame, htf_len: str) -> pd.DataFrame:
    """Attach HTF columns to base bars only once the HTF candle has CLOSED."""
    x = htf_frame.copy()
    x["close_time"] = x.index + pd.Timedelta(htf_len)
    x = x.reset_index(drop=True).sort_values("close_time")
    b = pd.DataFrame({"close_time": base_close_time.to_numpy()})
    return pd.merge_asof(b, x, on="close_time", direction="backward").drop(columns="close_time")


# --------------------------------------------------- Strategy B: 1H zones
def displacement_zones(df: pd.DataFrame, atr_n: int, body_mult: float, lookback: int = 5):
    """Demand/supply zones from an impulse leg (Strategy B, level 2).

    A demand zone is the body of the last DOWN candle immediately preceding an UP
    impulse (body >= body_mult * ATR). Supply is the mirror. A zone stays active
    until a close breaks through it, and only the most recent active zone of each
    side is carried, which is what a trader would actually be watching.
    """
    a = atr(df, atr_n).to_numpy()
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    N = len(df)
    out = np.full((N, 4), np.nan)          # dem_bot, dem_top, sup_bot, sup_top
    db = dt = sb = st = np.nan

    for i in range(N):
        body = c[i] - o[i]
        if np.isfinite(a[i]) and a[i] > 0:
            if body >= body_mult * a[i]:                      # up impulse -> demand
                for j in range(i - 1, max(i - 1 - lookback, -1), -1):
                    if c[j] < o[j]:
                        db, dt = c[j], o[j]
                        break
            elif -body >= body_mult * a[i]:                   # down impulse -> supply
                for j in range(i - 1, max(i - 1 - lookback, -1), -1):
                    if c[j] > o[j]:
                        sb, st = o[j], c[j]
                        break
        if np.isfinite(db) and c[i] < db:                     # demand broken
            db = dt = np.nan
        if np.isfinite(st) and c[i] > st:                     # supply broken
            sb = st = np.nan
        out[i] = (db, dt, sb, st)

    return pd.DataFrame(out, index=df.index,
                        columns=["dem_bot", "dem_top", "sup_bot", "sup_top"])


# ------------------------------------------- Strategy B: 30m liquidity sweeps
def sweep_flags(df: pd.DataFrame, n_fractal: int, prev_day_low: pd.Series,
                prev_day_high: pd.Series):
    """30m sweep: wick through a level, close back on the original side.

    Levels are confirmed fractal swings on this timeframe plus the previous UTC
    day's extreme. Both are known without lookahead: a swing only counts n bars
    after it prints, and yesterday's extreme is fixed once the day ends.
    """
    ms = market_structure(df, n_fractal)
    h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    N = len(df)
    lo_lvl, hi_lvl = np.full(N, np.nan), np.full(N, np.nan)
    cur_lo = cur_hi = np.nan
    slc, shc = ms["sl_conf"].to_numpy(), ms["sh_conf"].to_numpy()
    pdl, pdh = prev_day_low.to_numpy(), prev_day_high.to_numpy()
    for i in range(N):
        if not np.isnan(slc[i]):
            cur_lo = slc[i]
        if not np.isnan(shc[i]):
            cur_hi = shc[i]
        lo_lvl[i], hi_lvl[i] = cur_lo, cur_hi

    def swept_low(level):
        return (l < level) & (c > level) & np.isfinite(level)

    def swept_high(level):
        return (h > level) & (c < level) & np.isfinite(level)

    sw_lo = swept_low(lo_lvl) | swept_low(pdl)
    sw_hi = swept_high(hi_lvl) | swept_high(pdh)
    return pd.DataFrame({"sweep_low": sw_lo, "sweep_high": sw_hi,
                         "sweep_low_px": np.where(sw_lo, l, np.nan),
                         "sweep_high_px": np.where(sw_hi, h, np.nan)}, index=df.index)


def prev_day_extremes(df: pd.DataFrame):
    """Previous UTC day's high/low, aligned onto this frame's bars."""
    day = df.index.normalize()
    hi = df["high"].groupby(day).max().shift(1)
    lo = df["low"].groupby(day).min().shift(1)
    return (pd.Series(lo.reindex(day).to_numpy(), index=df.index),
            pd.Series(hi.reindex(day).to_numpy(), index=df.index))
