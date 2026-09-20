"""Strategy B: SMC top-down cascade, 4H -> 1H -> 30m -> 15m.

Every level is a rule, and the levels must complete in order:

  1. 4H close-based market-structure trend agrees with the trade direction
  2. price trades into an unmitigated 1H zone whose origin leg showed displacement
  3. within 12 x 30m bars, a 30m candle sweeps a low/high and closes back inside
  4. within 8 x 15m bars, a 15m close breaks structure on a decisive candle
     (body >= 60% of range AND >= 1.2 x ATR)

Ablations exist to show which level carries the edge. They are reporting tools:
the spec forbids using them to select anything.
"""
import numpy as np
import pandas as pd

ZONE = "zone_tagged"
SWEPT = "swept"


def _entry_from_break(i, d, h, l, c):
    """Midpoint of the 15m FVG created by the break, else the break candle's close."""
    if i >= 2:
        if d == 1 and l[i] > h[i - 2]:
            return 0.5 * (h[i - 2] + l[i]), "FVG"
        if d == -1 and h[i] < l[i - 2]:
            return 0.5 * (l[i - 2] + h[i]), "FVG"
    return c[i], "CLOSE"


def cascade(f, cfg, require_zone: bool = True, require_sweep: bool = True):
    """Returns a list of signal dicts. Set require_* False for the ablations."""
    o, h, l, c = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr = f["atr"].to_numpy()
    bfrac = f["body_frac"].to_numpy()
    ev = f["event"].to_numpy()
    t4 = f["trend_4h"].to_numpy()
    ct = f["ct_epoch"].to_numpy().astype("float64")
    dem_bot, dem_top = f["dem_bot"].to_numpy(), f["dem_top"].to_numpy()
    sup_bot, sup_top = f["sup_bot"].to_numpy(), f["sup_top"].to_numpy()
    sw_lo_t, sw_hi_t = f["sweep_low_epoch"].to_numpy(), f["sweep_high_epoch"].to_numpy()
    sw_lo_px, sw_hi_px = f["sweep_low_px"].to_numpy(), f["sweep_high_px"].to_numpy()

    zone_window = float(pd.Timedelta(cfg.htf_30m).value * cfg.zone_to_sweep_bars_30m)
    break_window = float(pd.Timedelta(cfg.base_tf).value * cfg.sweep_to_break_bars_15m)

    tag = {1: np.nan, -1: np.nan}          # epoch when price last tagged the zone
    signals = []

    for i in range(len(f)):
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue

        # ---- level 2: price trading into an unmitigated displacement zone
        if np.isfinite(dem_top[i]) and l[i] <= dem_top[i]:
            tag[1] = ct[i]
        if np.isfinite(sup_bot[i]) and h[i] >= sup_bot[i]:
            tag[-1] = ct[i]

        for d in (1, -1):
            # ---- level 1: 4H agreement
            if t4[i] != d:
                continue
            # ---- level 2 gate
            if require_zone and not np.isfinite(tag[d]):
                continue
            # ---- level 3: a 30m sweep after the tag, inside the window
            sweep_t = sw_lo_t[i] if d == 1 else sw_hi_t[i]
            sweep_px = sw_lo_px[i] if d == 1 else sw_hi_px[i]
            if require_sweep:
                if not np.isfinite(sweep_t):
                    continue
                if require_zone and not (0 <= sweep_t - tag[d] <= zone_window):
                    continue
                if ct[i] - sweep_t > break_window:
                    continue
            # ---- level 4: decisive 15m break of structure
            if np.sign(ev[i]) != d:
                continue
            body = abs(c[i] - o[i])
            if not (np.isfinite(bfrac[i]) and bfrac[i] >= cfg.break_body_frac):
                continue
            if body < cfg.break_body_atr * atr[i]:
                continue

            entry, poi = _entry_from_break(i, d, h, l, c)
            anchor = sweep_px if (require_sweep and np.isfinite(sweep_px)) else (
                l[i] if d == 1 else h[i])
            stop = anchor - d * cfg.stop_buffer_atr_b * atr[i]
            if (entry - stop) * d <= 0:
                continue

            signals.append({
                "idx": i, "dir": d, "side": "LONG" if d == 1 else "SHORT",
                "time": f["close_time"].iloc[i], "entry": float(entry),
                "stop": float(stop), "atr": float(atr[i]), "poi": poi,
            })
            tag[d] = np.nan                  # consume the setup

    return signals


def count_report(f, cfg):
    """Cheap feasibility probe: how many signals does each configuration produce?"""
    return {
        "full_cascade": len(cascade(f, cfg)),
        "no_sweep_req": len(cascade(f, cfg, require_sweep=False)),
        "no_zone_req": len(cascade(f, cfg, require_zone=False)),
        "break_only": len(cascade(f, cfg, require_zone=False, require_sweep=False)),
    }


def regime_mask(f, cfg):
    """Bars where the 4H direction allows a trade. The random control uses the same
    filter, so it sits in the same regime rather than trading everywhere."""
    t4 = f["trend_4h"].to_numpy()
    return {1: t4 == 1, -1: t4 == -1}


def stop_for(f, i: int, d: int, cfg):
    """Strategy B stop: beyond the 30m sweep extreme, ATR-buffered. Falls back to
    the bar's own extreme when no sweep is on the tape, which is what the cascade
    itself does."""
    atr = f["atr"].to_numpy()[i]
    px = (f["sweep_low_px"].to_numpy()[i] if d == 1 else f["sweep_high_px"].to_numpy()[i])
    if not np.isfinite(px):
        px = f["low"].to_numpy()[i] if d == 1 else f["high"].to_numpy()[i]
    if not (np.isfinite(atr) and atr > 0 and np.isfinite(px)):
        return np.nan
    return px - d * cfg.stop_buffer_atr_b * atr
