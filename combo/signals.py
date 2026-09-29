"""Combined strategy signals: swing BOS, optional sweep, optional volume.

Everything is causal. A swing pivot at bar i is only *known* `swing_length` bars
later, which is how LuxAlgo's pivot functions behave and is the property that
makes a break of it meaningful -- a level confirmed with hindsight would be
broken by the same move that created it.
"""
import numpy as np
import pandas as pd

from shared.indicators import atr as atr_of


def confirmed_swings(df: pd.DataFrame, length: int):
    """Most recent CONFIRMED swing high and low as of each bar.

    A pivot high at bar i is the highest high of the window centred on i, and it
    becomes usable only at bar i + length. Shifting by `length` and forward
    filling is what enforces that.
    """
    win = 2 * length + 1
    is_ph = df["high"] == df["high"].rolling(win, center=True).max()
    is_pl = df["low"] == df["low"].rolling(win, center=True).min()
    ph = df["high"].where(is_ph).shift(length).ffill()
    pl = df["low"].where(is_pl).shift(length).ffill()
    return ph.to_numpy(), pl.to_numpy()


def bos_events(df: pd.DataFrame, cfg):
    """Fresh closes through the last confirmed swing level.

    Only the bar that first closes beyond the level counts. Without that, every
    bar of a trend would fire while price stayed above an old swing high.
    """
    c = df["close"].to_numpy()
    ph, pl = confirmed_swings(df, cfg.swing_length)
    out = []
    for i in range(1, len(df)):
        if np.isfinite(ph[i]) and c[i] > ph[i] and not (np.isfinite(ph[i - 1]) and c[i - 1] > ph[i - 1]):
            out.append((i, 1))
        elif np.isfinite(pl[i]) and c[i] < pl[i] and not (np.isfinite(pl[i - 1]) and c[i - 1] < pl[i - 1]):
            out.append((i, -1))
    return out, ph, pl


def had_sweep(df, i: int, d: int, ph, pl, cfg) -> bool:
    """A liquidity sweep in the `sweep_lookback` candles before the BOS candle.

    For a long: some candle wicked BELOW the swing low that was confirmed at that
    moment, and closed back above it. The level is read per candle, not from the
    BOS bar, so a pivot confirmed after the sweep cannot be used retroactively.
    """
    lo = max(0, i - cfg.sweep_lookback)
    l, h, c = (df[k].to_numpy() for k in ("low", "high", "close"))
    for k in range(lo, i):
        level = pl[k] if d == 1 else ph[k]
        if not np.isfinite(level):
            continue
        if d == 1 and l[k] < level and c[k] > level:
            return True
        if d == -1 and h[k] > level and c[k] < level:
            return True
    return False


def volume_ok(df, i: int, cfg) -> bool:
    """The BOS candle's volume against the average of the previous candles.

    The average excludes the BOS candle itself; including it would let a spike
    inflate the threshold it is being measured against.
    """
    if i < cfg.vol_lookback:
        return False
    v = df["volume"].to_numpy()
    avg = float(np.mean(v[i - cfg.vol_lookback:i]))
    return avg > 0 and v[i] >= cfg.vol_mult * avg


def signals(df: pd.DataFrame, cfg, require_sweep: bool, require_volume: bool):
    """Signals for one variant. Entry is the NEXT candle's open, so the signal
    bar's own close is the last information used."""
    events, ph, pl = bos_events(df, cfg)
    a = atr_of(df, cfg.atr_len).to_numpy()
    o = df["open"].to_numpy()
    out = []
    for i, d in events:
        if i + 1 >= len(df):
            continue
        if not (np.isfinite(a[i]) and a[i] > 0):
            continue
        if require_sweep and not had_sweep(df, i, d, ph, pl, cfg):
            continue
        if require_volume and not volume_ok(df, i, cfg):
            continue
        entry = float(o[i + 1])
        out.append({"idx": i, "fill_idx": i + 1, "dir": d, "entry": entry,
                    "atr": float(a[i]),
                    "stop": entry - d * cfg.sl_atr * a[i],
                    "target": entry + d * cfg.tp_atr * a[i],
                    "time": df.index[i]})
    return out


VARIANTS = {"BOS+S": (True, False), "BOS+V": (False, True), "BOS+S+V": (True, True)}


def for_variant(df, cfg, name: str):
    s, v = VARIANTS[name]
    return signals(df, cfg, require_sweep=s, require_volume=v)


def resolve(df, sig, cfg):
    """Walk one trade forward from its fill bar. Stop wins same-bar ties."""
    h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    f, d = sig["fill_idx"], sig["dir"]
    stop, target = sig["stop"], sig["target"]
    last = min(f + cfg.time_stop_bars, len(df) - 1)
    if f >= len(df):
        return None
    for j in range(f, last + 1):
        hit_sl = (l[j] <= stop) if d == 1 else (h[j] >= stop)
        hit_tp = (h[j] >= target) if d == 1 else (l[j] <= target)
        if hit_sl:
            return {"win": False, "exit_idx": j, "reason": "SL"}
        if hit_tp:
            return {"win": True, "exit_idx": j, "reason": "TP"}
    if last < f + cfg.time_stop_bars:
        return None                      # not enough history left to resolve
    return {"win": False, "exit_idx": last, "reason": "TIME"}


def shifted(df, sigs, cfg, shift: int):
    """Time-shifted placebo: same construction, alignment destroyed.

    The geometry has to be REBUILT at the shifted bar, not carried across. Moving
    a signal's absolute entry and stop to a bar thousands of candles away would
    place them nowhere near the market and measure nothing. What transfers is the
    recipe -- direction, entry at the next open, stop and target 1.5 ATR away --
    which is what makes this a construction-matched null.

    Shifts wrap, so late-sample signals are not dropped and the placebo samples
    the same stretch of history as the signal arm.
    """
    n = len(df)
    a = atr_of(df, cfg.atr_len).to_numpy()
    o = df["open"].to_numpy()
    out = []
    for s in sigs:
        i = (s["idx"] + shift) % n
        if i + 1 >= n or not (np.isfinite(a[i]) and a[i] > 0):
            continue
        d = s["dir"]
        entry = float(o[i + 1])
        out.append({"idx": i, "fill_idx": i + 1, "dir": d, "entry": entry,
                    "atr": float(a[i]),
                    "stop": entry - d * cfg.sl_atr * a[i],
                    "target": entry + d * cfg.tp_atr * a[i],
                    "time": df.index[i]})
    return out
