"""SMC feature engine.

RULE: every value on row i uses only information available at the CLOSE of bar i.
That is what makes the backtest honest. Swings are only "known" n bars after they
print, and higher-timeframe values only appear once the HTF candle has closed.
"""
import numpy as np
import pandas as pd

# structure event codes
BOS_UP, CHOCH_UP, BOS_DN, CHOCH_DN = 1, 2, -1, -2


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def fractal_pivots(df: pd.DataFrame, n: int):
    """True at the pivot bar itself (uses future bars, so always shift by n before use)."""
    h, l = df["high"], df["low"]
    left_h = h.rolling(n).max().shift(1)
    right_h = h[::-1].rolling(n).max().shift(1)[::-1]
    left_l = l.rolling(n).min().shift(1)
    right_l = l[::-1].rolling(n).min().shift(1)[::-1]
    return (h > left_h) & (h > right_h), (l < left_l) & (l < right_l)


def market_structure(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Confirmed swings, trend state and BOS/CHoCH events (close-based breaks only)."""
    is_sh, is_sl = fractal_pivots(df, n)
    sh_conf = df["high"].where(is_sh).shift(n).to_numpy()   # swing confirmed on this bar
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
            event[i] = BOS_UP if t == 1 else CHOCH_UP
            t, sh_live = 1, False
        elif sl_live and close[i] < cur_sl:
            event[i] = BOS_DN if t == -1 else CHOCH_DN
            t, sl_live = -1, False
        last_sh[i], last_sl[i], trend[i] = cur_sh, cur_sl, t
    return pd.DataFrame({"last_sh": last_sh, "last_sl": last_sl, "trend": trend, "event": event,
                         "sh_conf": sh_conf, "sl_conf": sl_conf}, index=df.index)


def fvg_flags(df: pd.DataFrame, atr_s: pd.Series, min_atr: float):
    """Bull FVG at bar i: low[i] > high[i-2] (zone high[i-2]..low[i]). Bear is the mirror."""
    h2, l2 = df["high"].shift(2), df["low"].shift(2)
    bull = (df["low"] > h2) & ((df["low"] - h2) >= min_atr * atr_s)
    bear = (df["high"] < l2) & ((l2 - df["high"]) >= min_atr * atr_s)
    return bull.fillna(False), bear.fillna(False)


def is_displacement(o, h, l, c, a, e, d, body_mult, leg_mult) -> bool:
    """Displacement in direction d (1 up / -1 down) within the 3 bars ending at e."""
    for j in range(max(0, e - 2), e + 1):
        if (c[j] - o[j]) * d >= body_mult * a[j]:
            return True
    if e >= 2 and all((c[j] - o[j]) * d > 0 for j in range(e - 2, e + 1)):
        if h[e - 2:e + 1].max() - l[e - 2:e + 1].min() >= leg_mult * a[e]:
            return True
    return False


def in_windows(hours: np.ndarray, windows) -> np.ndarray:
    m = np.zeros(len(hours), bool)
    for s, e in windows:
        m |= (hours >= s) & (hours < e)
    return m


def daily_liquidity(df: pd.DataFrame, asia) -> pd.DataFrame:
    """PDH/PDL and Asian range, plus 'fresh' flags (not yet taken earlier today)."""
    idx = df.index
    date = idx.normalize()
    hour = np.asarray(idx.hour + idx.minute / 60.0)
    day_h = df["high"].groupby(date).max()
    day_l = df["low"].groupby(date).min()
    pdh = pd.Series(day_h.shift(1).reindex(date).to_numpy(), index=idx)
    pdl = pd.Series(day_l.shift(1).reindex(date).to_numpy(), index=idx)

    in_asia = (hour >= asia[0]) & (hour < asia[1])
    a_h = df["high"].where(in_asia).groupby(date).max()
    a_l = df["low"].where(in_asia).groupby(date).min()
    after = hour >= asia[1]
    asia_h = pd.Series(np.where(after, a_h.reindex(date).to_numpy(), np.nan), index=idx)
    asia_l = pd.Series(np.where(after, a_l.reindex(date).to_numpy(), np.nan), index=idx)

    # highest high / lowest low of today BEFORE this bar
    prev_day_max = df["high"].groupby(date).cummax().groupby(date).shift(1)
    prev_day_min = df["low"].groupby(date).cummin().groupby(date).shift(1)
    post_h = df["high"].where(after).groupby(date).cummax().groupby(date).shift(1)
    post_l = df["low"].where(after).groupby(date).cummin().groupby(date).shift(1)

    return pd.DataFrame({
        "pdh": pdh, "pdl": pdl, "asia_h": asia_h, "asia_l": asia_l,
        "fresh_pdh": prev_day_max.isna() | (prev_day_max <= pdh),
        "fresh_pdl": prev_day_min.isna() | (prev_day_min >= pdl),
        "fresh_asia_h": post_h.isna() | (post_h <= asia_h),
        "fresh_asia_l": post_l.isna() | (post_l >= asia_l),
    }, index=idx)


def equal_levels(df, ms, atr_s, tol, keep=30):
    """Nearest untaken equal-highs level above / equal-lows level below, as of bar open."""
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    shc, slc, a = ms["sh_conf"].to_numpy(), ms["sl_conf"].to_numpy(), atr_s.to_numpy()
    N = len(df)
    eqh_out, eql_out = np.full(N, np.nan), np.full(N, np.nan)
    act_sh, act_sl, eqh, eql = [], [], [], []
    for i in range(N):
        if i > 0:
            up = [x for x in eqh if x > c[i - 1]]
            dn = [x for x in eql if x < c[i - 1]]
            eqh_out[i] = min(up) if up else np.nan
            eql_out[i] = max(dn) if dn else np.nan
        eqh = [x for x in eqh if h[i] <= x]
        eql = [x for x in eql if l[i] >= x]
        act_sh = [x for x in act_sh if h[i] <= x]
        act_sl = [x for x in act_sl if l[i] >= x]
        if not np.isnan(shc[i]):
            p = shc[i]
            if any(abs(p - q) <= tol * a[i] for q in act_sh):
                eqh.append(max([p] + [q for q in act_sh if abs(p - q) <= tol * a[i]]))
            act_sh = (act_sh + [p])[-keep:]
        if not np.isnan(slc[i]):
            p = slc[i]
            if any(abs(p - q) <= tol * a[i] for q in act_sl):
                eql.append(min([p] + [q for q in act_sl if abs(p - q) <= tol * a[i]]))
            act_sl = (act_sl + [p])[-keep:]
        eqh, eql = eqh[-keep:], eql[-keep:]
    return pd.DataFrame({"eqh": eqh_out, "eql": eql_out}, index=df.index)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def active_fvg_zones(df, atr_s, min_atr, max_age):
    """Most recent unmitigated bull and bear FVG zone on each (HTF) bar."""
    bull, bear = fvg_flags(df, atr_s, min_atr)
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    bull, bear = bull.to_numpy(), bear.to_numpy()
    N = len(df)
    out = np.full((N, 4), np.nan)
    bb = bt = sb = st = np.nan
    bage = sage = 0
    for i in range(N):
        bage += 1; sage += 1
        if bull[i]:
            bb, bt, bage = h[i - 2], l[i], 0
        elif not np.isnan(bb) and (c[i] < bb or bage > max_age):
            bb = bt = np.nan
        if bear[i]:
            sb, st, sage = h[i], l[i - 2], 0
        elif not np.isnan(st) and (c[i] > st or sage > max_age):
            sb = st = np.nan
        out[i] = (bb, bt, sb, st)
    return pd.DataFrame(out, index=df.index, columns=["h1_bull_fvg_bot", "h1_bull_fvg_top",
                                                      "h1_bear_fvg_bot", "h1_bear_fvg_top"])


def _asof(base_close_time: pd.Series, htf: pd.DataFrame, htf_len: str) -> pd.DataFrame:
    """Attach HTF values to base bars only once the HTF candle has CLOSED."""
    x = htf.copy()
    x["close_time"] = x.index + pd.Timedelta(htf_len)
    x = x.reset_index(drop=True).sort_values("close_time")
    b = pd.DataFrame({"close_time": base_close_time.to_numpy()})
    return pd.merge_asof(b, x, on="close_time", direction="backward").drop(columns="close_time")


def build_features(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """df: OHLCV indexed by UTC candle OPEN time, closed candles only."""
    df = df.sort_index()
    f = df.copy()
    f["atr"] = atr(df, cfg.atr_len)
    f["atr_pct"] = f["atr"].rolling(cfg.atr_pct_window, min_periods=cfg.atr_pct_window // 4).rank(pct=True)

    ms = market_structure(df, cfg.fractal_n_base)
    f = f.join(ms)
    f["bull_fvg"], f["bear_fvg"] = fvg_flags(df, f["atr"], cfg.fvg_min_atr)
    f = f.join(daily_liquidity(df, cfg.asia_session))
    f = f.join(equal_levels(df, ms, f["atr"], cfg.eq_level_tol_atr))

    hours = np.asarray(df.index.hour + df.index.minute / 60.0)
    f["in_kz"] = in_windows(hours, cfg.killzones)
    pre = [(max(0.0, s - cfg.sweep_pre_kz_hours), e) for s, e in cfg.killzones]
    f["in_sweep_window"] = in_windows(hours, pre)

    close_time = pd.Series(df.index + pd.Timedelta(cfg.base_tf))
    f["close_time"] = close_time.to_numpy()

    # 4H bias
    h4 = resample(df, "4h")
    h4f = pd.DataFrame({"h4_trend": market_structure(h4, cfg.fractal_n_htf)["trend"]}, index=h4.index)
    # 1H dealing range + POIs
    h1 = resample(df, "1h")
    a1 = atr(h1, cfg.atr_len)
    h1f = pd.DataFrame({"h1_hi": h1["high"].rolling(cfg.h1_range_bars).max(),
                        "h1_lo": h1["low"].rolling(cfg.h1_range_bars).min()}, index=h1.index)
    h1f = h1f.join(active_fvg_zones(h1, a1, cfg.fvg_min_atr, cfg.h1_fvg_max_age))

    for part in (_asof(close_time, h4f, "4h"), _asof(close_time, h1f, "1h")):
        part.index = f.index
        f = f.join(part)
    f["h4_trend"] = f["h4_trend"].fillna(0).astype(int)
    return f
