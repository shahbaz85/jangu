"""Strategy A: EMA trend + Stochastic RSI + volume + ABCD pattern.

All four must agree on the trigger bar:
  trend    EMA(100) > EMA(200), both rising (vs 10 bars ago)   [A2: a recent cross]
  momentum %K crossed above 20 in the last 5 bars, %K rising, %K < 80
  volume   bar volume >= 1.2x the previous 15 bars, and the 5-bar average is
           above where it was 5 bars earlier
  pattern  ABCD -- A swing low, B swing high with B-A >= 1.5 ATR, C swing low
           retracing 50-78.6% of AB and holding above A; the trigger is the first
           close above B within 20 bars of C

Shorts mirror throughout. Swings come from confirmed 3-bar fractals, so a swing is
only usable n bars after it printed.
"""
import numpy as np


def regime_mask(f, cfg, variant: str = "A"):
    """Bars where the trend filter allows a trade, per direction. Used both by the
    strategy and by the random control, so the control sits in the same regime."""
    fast, slow = f["ema_fast"].to_numpy(), f["ema_slow"].to_numpy()
    fr, sr = f["ema_fast_rising"].to_numpy(), f["ema_slow_rising"].to_numpy()
    if variant == "A2":
        up = f["ema_cross_up_recent"].to_numpy() & (fast > slow)
        dn = f["ema_cross_dn_recent"].to_numpy() & (fast < slow)
    else:
        up = (fast > slow) & fr & sr
        dn = (fast < slow) & (~fr) & (~sr)
    return {1: up & np.isfinite(fast) & np.isfinite(slow),
            -1: dn & np.isfinite(fast) & np.isfinite(slow)}


def stop_for(f, i: int, d: int, cfg):
    """Strategy A stop: beyond the 10-bar extreme, with an ATR buffer."""
    atr = f["atr"].to_numpy()[i]
    ext = f["low_10"].to_numpy()[i] if d == 1 else f["high_10"].to_numpy()[i]
    if not (np.isfinite(atr) and np.isfinite(ext)) or atr <= 0:
        return np.nan
    return ext - d * cfg.stop_buffer_atr_a * atr


def _abcd_setups(f, cfg, d: int):
    """Arm a setup whenever a confirmed swing completes a valid ABCD leg.

    Returns {trigger_deadline_index: (B_price, C_index)} style records as a list of
    (armed_at, deadline, b_price).
    """
    sh = f["swing_high"].to_numpy()
    sl = f["swing_low"].to_numpy()
    atr = f["atr"].to_numpy()
    highs = sh if d == 1 else sl          # B is a swing high for longs
    lows = sl if d == 1 else sh           # A and C are swing lows for longs

    out = []
    a_idx = b_idx = None
    a_px = b_px = np.nan
    for i in range(len(f)):
        lo, hi = lows[i], highs[i]
        if np.isfinite(hi) and a_idx is not None:
            # a swing in the B direction after an A candidate
            if b_idx is None or (hi - b_px) * d > 0:
                b_idx, b_px = i, hi
        if np.isfinite(lo):
            if b_idx is not None and a_idx is not None:
                leg = (b_px - a_px) * d
                if leg >= cfg.ab_min_atr * atr[i] and leg > 0:
                    retr = (b_px - lo) * d / leg
                    beyond_a = (lo - a_px) * d > 0
                    if cfg.bc_retrace_lo <= retr <= cfg.bc_retrace_hi and beyond_a:
                        out.append((i, i + cfg.abcd_max_bars, b_px))
                        a_idx, a_px, b_idx, b_px = i, lo, None, np.nan
                        continue
            # otherwise this swing becomes the new A candidate
            if a_idx is None or (lo - a_px) * d < 0 or b_idx is None:
                a_idx, a_px, b_idx, b_px = i, lo, None, np.nan
    return out


def signals(f, cfg, variant: str = "A"):
    c = f["close"].to_numpy()
    atr = f["atr"].to_numpy()
    vol = f["volume"].to_numpy()
    vavg = f["vol_avg_prev"].to_numpy()
    vtrend = f["vol_trend_up"].to_numpy()
    k = f["stoch_k"].to_numpy()
    krise = f["k_rising"].to_numpy()
    kup = f["k_crossed_up_recent"].to_numpy()
    kdn = f["k_crossed_dn_recent"].to_numpy()
    regime = regime_mask(f, cfg, variant)

    out = []
    for d in (1, -1):
        armed = _abcd_setups(f, cfg, d)
        for armed_at, deadline, b_px in armed:
            for i in range(armed_at + 1, min(deadline, len(f) - 1) + 1):
                if (c[i] - b_px) * d <= 0:            # wait for the break of B
                    continue
                if not regime[d][i]:
                    break
                mom = (kup[i] and krise[i] and k[i] < cfg.stoch_high) if d == 1 else \
                      (kdn[i] and not krise[i] and k[i] > cfg.stoch_low)
                if not mom:
                    break
                if not (np.isfinite(vavg[i]) and vol[i] >= cfg.vol_mult * vavg[i] and vtrend[i]):
                    break
                stop = stop_for(f, i, d, cfg)
                if not np.isfinite(stop) or (c[i] - stop) * d <= 0:
                    break
                out.append({"idx": i, "dir": d, "side": "LONG" if d == 1 else "SHORT",
                            "time": f["close_time"].iloc[i], "entry": float(c[i]),
                            "stop": float(stop), "atr": float(atr[i]), "poi": "ABCD"})
                break                                   # first break of B only
    out.sort(key=lambda s: s["idx"])
    return out
