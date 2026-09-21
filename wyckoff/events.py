"""Wyckoff accumulation and distribution events, detected causally.

The sequence this looks for is the classic one, in order:

  1. a trading range -- price contained within a band narrow relative to ATR
  2. a spring (accumulation) or upthrust (distribution) -- price penetrates the
     range boundary and closes back inside, i.e. the stops beyond it are taken
  3. SOS / SOW -- a decisive close through the opposite boundary
  4. LPS / LPSY -- a pullback that holds the broken boundary

Every value on bar i uses bars <= i only. Range boundaries are taken from bars
strictly before i, so the bar that breaks a boundary cannot have moved it.

The vocabulary deliberately mirrors `shared/indicators.py`: a spring is a sweep
of a range low, and an SOS is a displacement break of structure. Whether that
overlap makes the two frameworks redundant is the question `probe.py` measures
rather than assumes.
"""
import numpy as np
import pandas as pd

from shared.indicators import atr as atr_of, body_frac


def trading_range(df: pd.DataFrame, lookback: int, max_width_atr: float, atr_n: int = 14):
    """Rolling range boundaries, and whether the band is tight enough to count.

    Boundaries come from the `lookback` bars ending at i-1. Shifting is what makes
    a spring detectable at all: if bar i could widen its own range low, nothing
    would ever penetrate it.
    """
    a = atr_of(df, atr_n)
    hi = df["high"].rolling(lookback).max().shift(1)
    lo = df["low"].rolling(lookback).min().shift(1)
    width = (hi - lo) / a
    return pd.DataFrame({"range_hi": hi, "range_lo": lo, "range_width_atr": width,
                         "in_range": width <= max_width_atr}, index=df.index)


def events(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Per-bar Wyckoff event flags and the completed accumulation/distribution setups.

    Returns one row per bar with:
      spring / upthrust  -- boundary penetrated, close back inside a tight range
      sos / sow          -- decisive close through the far boundary after that
      lps / lpsy         -- pullback holding the broken boundary after the SOS/SOW
      phase_dir          -- +1 accumulation, -1 distribution, 0 none, at completion
    """
    rng = trading_range(df, cfg.range_lookback, cfg.range_max_width_atr, cfg.atr_len)
    a = atr_of(df, cfg.atr_len).to_numpy()
    bf = body_frac(df).to_numpy()
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    r_hi, r_lo = rng["range_hi"].to_numpy(), rng["range_lo"].to_numpy()
    tight = rng["in_range"].to_numpy()
    n = len(df)

    spring = np.zeros(n, bool); upthrust = np.zeros(n, bool)
    sos = np.zeros(n, bool);    sow = np.zeros(n, bool)
    lps = np.zeros(n, bool);    lpsy = np.zeros(n, bool)
    phase = np.zeros(n, np.int8)

    # State for the sequence in progress, one per direction. -1 means "not seen".
    spring_at = {1: -1, -1: -1}
    sos_at = {1: -1, -1: -1}
    level = {1: np.nan, -1: np.nan}      # the boundary the SOS broke, which an LPS must hold

    for i in range(n):
        if not (np.isfinite(a[i]) and a[i] > 0 and np.isfinite(r_hi[i]) and np.isfinite(r_lo[i])):
            continue

        # --- 2. spring / upthrust: take the stops beyond the range, close back inside
        if tight[i]:
            if l[i] < r_lo[i] and c[i] > r_lo[i]:
                spring[i] = True
                spring_at[1], sos_at[1] = i, -1
            if h[i] > r_hi[i] and c[i] < r_hi[i]:
                upthrust[i] = True
                spring_at[-1], sos_at[-1] = i, -1

        decisive = (np.isfinite(bf[i]) and bf[i] >= cfg.sos_body_frac
                    and abs(c[i] - o[i]) >= cfg.sos_body_atr * a[i])

        # --- 3. SOS / SOW: a decisive close through the opposite boundary
        #
        # Strictly after the spring. A single outside bar can dip below the range
        # low and close above the range high, which would otherwise be recorded as
        # a spring and its own sign of strength -- one candle, not a sequence. On
        # random-walk data that was 3 of 46 breaks.
        if decisive:
            if (spring_at[1] >= 0 and sos_at[1] < 0 and i > spring_at[1]
                    and i - spring_at[1] <= cfg.spring_to_sos_bars and c[i] > r_hi[i]):
                sos[i] = True
                sos_at[1], level[1] = i, r_hi[i]
            if (spring_at[-1] >= 0 and sos_at[-1] < 0 and i > spring_at[-1]
                    and i - spring_at[-1] <= cfg.spring_to_sos_bars and c[i] < r_lo[i]):
                sow[i] = True
                sos_at[-1], level[-1] = i, r_lo[i]

        # --- 4. LPS / LPSY: a pullback that holds the level the SOS broke
        for d, (flag, held) in ((1, (lps, l[i] <= level[1] * (1 + cfg.lps_tag_tol)
                                     and c[i] > level[1])),
                                (-1, (lpsy, h[i] >= level[-1] * (1 - cfg.lps_tag_tol)
                                      and c[i] < level[-1]))):
            if sos_at[d] >= 0 and i > sos_at[d] and i - sos_at[d] <= cfg.sos_to_lps_bars:
                if np.isfinite(level[d]) and held:
                    flag[i] = True
                    phase[i] = d
                    spring_at[d] = sos_at[d] = -1       # consume the setup
                    level[d] = np.nan

        for d in (1, -1):                                # expire stale sequences
            if spring_at[d] >= 0 and sos_at[d] < 0 and i - spring_at[d] > cfg.spring_to_sos_bars:
                spring_at[d] = -1
            if sos_at[d] >= 0 and i - sos_at[d] > cfg.sos_to_lps_bars:
                spring_at[d] = sos_at[d] = -1
                level[d] = np.nan

    return pd.DataFrame({"spring": spring, "upthrust": upthrust, "sos": sos, "sow": sow,
                         "lps": lps, "lpsy": lpsy, "phase_dir": phase,
                         "range_hi": r_hi, "range_lo": r_lo,
                         "range_width_atr": rng["range_width_atr"].to_numpy()},
                        index=df.index)
