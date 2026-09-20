"""Run: python shared/tests.py

The load-bearing test is no-lookahead. Strategy B reads four timeframes at once, so
a single leaked higher-timeframe value would invent an edge that does not exist.
Every feature computed on truncated history must equal the full-history value up to
the cut, and each HTF value must appear exactly when its candle closes.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from data import synthetic
from shared.config import ABConfig
from shared.features import build_features, FEATURE_COLS
from shared.indicators import market_structure, resample, stoch_rsi
from shared.diagnostics import clustering_factor, trades_needed, zero_cost_breakeven
from shared.strategy_b import cascade


def test_no_lookahead():
    cfg = ABConfig()
    df = synthetic(days=120, seed=31)
    full = build_features(df, cfg)
    for k in (3000, 6000, len(df) - 1):
        part = build_features(df.iloc[:k + 1], cfg)
        pd.testing.assert_frame_equal(full[FEATURE_COLS].iloc[:k + 1], part[FEATURE_COLS],
                                      check_dtype=False, obj=f"truncated at {k}")
    print("ok  no lookahead across 15m / 30m / 1H / 4H features")


def test_htf_attaches_on_close():
    """A NEWLY FORMED higher-timeframe value must be invisible before its candle
    closes, and present from the instant it does.

    It has to be a newly formed one: a zone persists for many 1H bars once created,
    so seeing an old zone's level on earlier bars is correct carry-forward, not
    leakage. Probing an arbitrary bar tests the wrong thing.
    """
    from shared.indicators import displacement_zones
    cfg = ABConfig()
    df = synthetic(days=60, seed=32)
    f = build_features(df, cfg)
    ct = pd.DatetimeIndex(f["close_time"])
    h1 = resample(df, cfg.htf_1h)
    z = displacement_zones(h1, cfg.atr_len, cfg.impulse_body_atr)["dem_top"]

    fresh = np.flatnonzero((z.ne(z.shift()) & z.notna()).to_numpy())
    assert len(fresh) >= 3, "no newly formed zones to probe"
    checked = 0
    for k in fresh[2:8]:
        val = float(z.iloc[k])
        closes_at = z.index[k] + pd.Timedelta(cfg.htf_1h)
        assert not (f.loc[ct < closes_at, "dem_top"] == val).any(), \
            f"zone level {val} leaked before its 1H candle closed"
        window = f.loc[(ct >= closes_at) & (ct < closes_at + pd.Timedelta(cfg.htf_1h)), "dem_top"]
        assert len(window) and (window == val).all(), \
            "zone level not attached once its 1H candle closed"
        checked += 1
    print(f"ok  1H zones attach exactly on candle close, never before ({checked} probed)")


def test_stoch_rsi_bounds():
    df = synthetic(days=40, seed=33)
    k, d = stoch_rsi(df["close"])
    # tolerance is for float round-off in the rolling mean only: the observed
    # excess is ~4e-14, far below anything the 20/80 thresholds could notice.
    # Clamping the indicator instead would hide real errors behind cosmetics.
    eps = 1e-9
    for s, name in ((k, "%K"), (d, "%D")):
        v = s.dropna()
        assert v.between(-eps, 100 + eps).all(), \
            f"Stoch RSI {name} out of bounds by more than round-off: " \
            f"[{v.min():.6f}, {v.max():.6f}]"
    print("ok  Stochastic RSI within 0-100 (to float precision)")


def test_market_structure_is_causal():
    """A swing may only be used n bars after it printed."""
    cfg = ABConfig()
    df = synthetic(days=40, seed=34)
    ms = market_structure(df, cfg.fractal_n)
    full_high = df["high"].to_numpy()
    sh = ms["sh_conf"].to_numpy()
    for i in np.flatnonzero(~np.isnan(sh)):
        assert np.isclose(sh[i], full_high[i - cfg.fractal_n]), \
            "confirmed swing high is not the bar n back"
    print("ok  market structure confirms swings n bars late, never early")


def test_strategy_b_conditions_hold():
    """Every signal must satisfy all four cascade levels in the claimed direction."""
    cfg = ABConfig()
    df = synthetic(days=200, seed=35)
    f = build_features(df, cfg)
    sigs = cascade(f, cfg)
    assert sigs, "cascade produced nothing on 200 days; check the pipeline"
    ev, t4 = f["event"].to_numpy(), f["trend_4h"].to_numpy()
    bfrac, atr = f["body_frac"].to_numpy(), f["atr"].to_numpy()
    o, c = f["open"].to_numpy(), f["close"].to_numpy()
    for s in sigs:
        i, d = s["idx"], s["dir"]
        assert t4[i] == d, "signal disagrees with the 4H trend"
        assert np.sign(ev[i]) == d, "no 15m break of structure in the trade direction"
        assert bfrac[i] >= cfg.break_body_frac, "break candle body fraction too small"
        assert abs(c[i] - o[i]) >= cfg.break_body_atr * atr[i], "break candle not decisive"
        assert (s["entry"] - s["stop"]) * d > 0, "stop is on the wrong side of entry"
    assert [s["idx"] for s in sigs] == sorted(s["idx"] for s in sigs), "signals not chronological"
    print(f"ok  Strategy B: all {len(sigs)} signals satisfy every cascade level")


def test_ablations_are_supersets():
    """Dropping a requirement can only ever admit more setups, never fewer.
    If an ablation returns fewer signals, the gating logic is wrong."""
    cfg = ABConfig()
    df = synthetic(days=200, seed=35)
    f = build_features(df, cfg)
    full = len(cascade(f, cfg))
    no_sweep = len(cascade(f, cfg, require_sweep=False))
    no_zone = len(cascade(f, cfg, require_zone=False))
    assert no_sweep >= full, "dropping the sweep requirement reduced signals"
    assert no_zone >= full, "dropping the zone requirement reduced signals"
    print(f"ok  ablations are supersets (full {full}, no-sweep {no_sweep}, no-zone {no_zone})")


def test_zero_cost_breakeven():
    """p = 1/(1.5+q). Anchored on the one case with an obvious answer: when half
    the runners reach 2R, the scale-out is worth exactly what a plain 1:1 is, so
    it breaks even at 50%. Worse runners demand a higher hit rate, better ones a
    lower one."""
    def outs(n_win, n_loss, n_tp2):
        return ([{"win": True, "tp2": i < n_tp2} for i in range(n_win)]
                + [{"win": False, "tp2": False} for _ in range(n_loss)])

    q, be = zero_cost_breakeven(outs(100, 100, 50))
    assert abs(q - 0.50) < 1e-9, f"runner conversion read as {q}"
    assert abs(be - 0.50) < 1e-9, f"q=50% must break even at 50%, got {be:.4f}"
    assert abs(zero_cost_breakeven(outs(100, 0, 0))[1] - 1 / 1.5) < 1e-9
    assert zero_cost_breakeven(outs(100, 0, 90))[1] < be < zero_cost_breakeven(outs(100, 0, 10))[1], \
        "break-even must fall as runner conversion rises"
    assert np.isnan(zero_cost_breakeven(outs(0, 10, 0))[1]), "no winners means no estimate"
    print(f"ok  zero-cost break-even: q=50% -> {be:.1%}, q=0% -> {1 / 1.5:.1%}")


def test_trades_needed_matches_closed_form():
    """A textbook case, checked by hand: distinguishing 52.6% from 50.0% at 80%
    power and alpha 0.05 needs ~2,900 one-sample observations. The two-sample
    figure must be larger, because both arms are estimated rather than assumed."""
    one, two = trades_needed(0.50, 0.526)
    assert 2850 <= one <= 2950, f"one-sample n = {one:.0f}, expected ~2900"
    assert two > one, "a two-arm test cannot be cheaper than a one-sample test"
    assert 5600 <= two <= 6000, f"two-sample n = {two:.0f} per arm, expected ~5800"
    assert trades_needed(0.50, 0.55)[0] < one, "a larger effect must need fewer trades"
    print(f"ok  power calculator: {one:.0f} one-sample, {two:.0f} per arm two-sample")


def test_clustering_factor_sees_clustering():
    """The point of the ratio is to separate clustered trades from scattered ones.
    Scattered coin flips must land near 1x; a hit rate that drifts slowly through
    the sample must land well above it. The tolerance on the i.i.d. case is wide
    because ~26 blocks cannot pin a variance ratio more tightly than that."""
    rng = np.random.default_rng(7)
    n = 900
    ts = pd.to_datetime("2024-01-01", utc=True) + pd.to_timedelta(
        np.sort(rng.uniform(0, 730, n)), unit="D")
    scattered = rng.random(n) < 0.5
    drifting = rng.random(n) < (0.5 + 0.25 * np.sin(np.linspace(0, 6 * np.pi, n)))
    r_flat = clustering_factor(list(zip(ts, scattered)))[2]
    r_clus = clustering_factor(list(zip(ts, drifting)))[2]
    assert 0.6 <= r_flat <= 1.4, f"scattered trades reported {r_flat:.2f}x clustering"
    assert r_clus > 1.5, f"drifting trades reported only {r_clus:.2f}x clustering"
    assert r_clus > r_flat * 1.5, "clustered and scattered sets are not separated"
    print(f"ok  clustering factor: scattered {r_flat:.2f}x, drifting {r_clus:.2f}x")


if __name__ == "__main__":
    test_stoch_rsi_bounds()
    test_market_structure_is_causal()
    test_htf_attaches_on_close()
    test_strategy_b_conditions_hold()
    test_ablations_are_supersets()
    test_no_lookahead()
    test_zero_cost_breakeven()
    test_trades_needed_matches_closed_form()
    test_clustering_factor_sees_clustering()
