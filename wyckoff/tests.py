"""Tests for the Wyckoff event detector.

The property that matters most is causality. Range boundaries come from a rolling
window, and a bar that could widen its own boundary would never be able to
penetrate it -- so a shifted window is not a stylistic choice, it is what makes a
spring detectable at all. If one of these fails, fix the detector, not the test.
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import synthetic                                    # noqa: E402
from wyckoff.config import WyckoffConfig                       # noqa: E402
from wyckoff.events import events, trading_range               # noqa: E402

FLAGS = ["spring", "upthrust", "sos", "sow", "lps", "lpsy", "phase_dir"]


def test_no_lookahead():
    """Events on truncated history must equal events on full history. Anything
    that reads a future bar shows up here as a mismatch."""
    cfg = WyckoffConfig()
    df = synthetic(days=120, seed=41)
    full = events(df, cfg)
    for cut in (400, 900, 1500, 2600):
        part = events(df.iloc[:cut], cfg)
        a = full.iloc[cut - 1][FLAGS].to_numpy()
        b = part.iloc[-1][FLAGS].to_numpy()
        assert np.array_equal(a, b), f"bar {cut - 1} changed when later bars were hidden"
    print(f"ok  Wyckoff events are causal (4 truncation points, {len(FLAGS)} flags)")


def test_range_boundaries_exclude_the_current_bar():
    """The range high/low must come from bars strictly before i. If bar i's own
    high fed the boundary, an upthrust could never close back inside it."""
    cfg = WyckoffConfig()
    df = synthetic(days=60, seed=42)
    rng = trading_range(df, cfg.range_lookback, cfg.range_max_width_atr, cfg.atr_len)
    hi, lo = rng["range_hi"].to_numpy(), rng["range_lo"].to_numpy()
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    n = cfg.range_lookback
    for i in range(n + 1, len(df)):
        if not np.isfinite(hi[i]):
            continue
        assert abs(hi[i] - h[i - n:i].max()) < 1e-9, f"range high at {i} is not the prior window"
        assert abs(lo[i] - l[i - n:i].min()) < 1e-9, f"range low at {i} is not the prior window"
    print(f"ok  range boundaries use only prior bars ({len(df) - n - 1} probed)")


def test_spring_closes_back_inside():
    """A spring penetrates the range low and closes above it. A bar that breaks
    down and stays down is a breakdown, not a spring, and must not be flagged."""
    cfg = WyckoffConfig()
    df = synthetic(days=90, seed=43)
    ev = events(df, cfg)
    l, h, c = (df[k].to_numpy() for k in ("low", "high", "close"))
    for i in np.flatnonzero(ev["spring"].to_numpy()):
        assert l[i] < ev["range_lo"].to_numpy()[i], f"spring at {i} never broke the low"
        assert c[i] > ev["range_lo"].to_numpy()[i], f"spring at {i} closed below the low"
    for i in np.flatnonzero(ev["upthrust"].to_numpy()):
        assert h[i] > ev["range_hi"].to_numpy()[i], f"upthrust at {i} never broke the high"
        assert c[i] < ev["range_hi"].to_numpy()[i], f"upthrust at {i} closed above the high"
    n = int(ev["spring"].sum() + ev["upthrust"].sum())
    print(f"ok  springs and upthrusts close back inside the range ({n} checked)")


def test_sequence_order_is_enforced():
    """Wyckoff is a sequence, not a set. Every LPS must be preceded by an SOS,
    and every SOS by a spring, within the configured windows."""
    cfg = WyckoffConfig()
    df = synthetic(days=180, seed=44)
    ev = events(df, cfg)
    spring = np.flatnonzero(ev["spring"].to_numpy())
    sos = np.flatnonzero(ev["sos"].to_numpy())
    lps = np.flatnonzero(ev["lps"].to_numpy())
    for i in sos:
        prior = spring[(spring < i) & (spring >= i - cfg.spring_to_sos_bars)]
        assert len(prior), f"SOS at {i} has no spring within {cfg.spring_to_sos_bars} bars"
    for i in lps:
        prior = sos[(sos < i) & (sos >= i - cfg.sos_to_lps_bars)]
        assert len(prior), f"LPS at {i} has no SOS within {cfg.sos_to_lps_bars} bars"
    print(f"ok  sequence order holds ({len(spring)} springs, {len(sos)} SOS, {len(lps)} LPS)")


def test_completions_are_rarer_than_their_parts():
    """A conjunction cannot be commoner than its rarest term. This is the whole
    reason the probe exists, so it is worth asserting rather than assuming."""
    cfg = WyckoffConfig()
    df = synthetic(days=240, seed=45)
    ev = events(df, cfg)
    n_spring = int(ev["spring"].sum() + ev["upthrust"].sum())
    n_sos = int(ev["sos"].sum() + ev["sow"].sum())
    n_done = int((ev["phase_dir"].to_numpy() != 0).sum())
    assert n_done <= n_sos <= n_spring, (
        f"completions {n_done} / breaks {n_sos} / springs {n_spring} are not nested")
    print(f"ok  completions nest inside their parts ({n_spring} -> {n_sos} -> {n_done})")


if __name__ == "__main__":
    test_no_lookahead()
    test_range_boundaries_exclude_the_current_bar()
    test_spring_closes_back_inside()
    test_sequence_order_is_enforced()
    test_completions_are_rarer_than_their_parts()
