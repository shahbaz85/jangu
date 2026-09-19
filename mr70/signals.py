"""MR-70 signal variants. Pre-registered in the spec -- exactly these five, no others.

Every generator takes (features, cfg) and returns a chronologically sorted list of
(bar_index, direction) with direction +1 long / -1 short. Nothing here knows about
trade geometry or costs; that lives in edge_gate.py and backtest.py.
"""
import numpy as np

# columns that must be present for a bar to be tradeable at all (covers warmup)
REQUIRED = ["atr", "rsi", "rsi_fast", "bb_lo", "bb_hi", "ema_base", "vwap",
            "vwap_sd", "vol_avg", "adx_1h", "close_4h", "ema50_4h", "ema200_4h"]


def valid_mask(f) -> np.ndarray:
    return (f[REQUIRED].notna().all(axis=1) & (f["atr"] > 0)).to_numpy()


def _combine(long: np.ndarray, short: np.ndarray):
    """Long and short are mutually exclusive by construction in every variant."""
    both = long & short
    if both.any():
        raise AssertionError(f"{both.sum()} bars fired long and short simultaneously")
    idx = np.flatnonzero(long | short)
    return [(int(i), 1 if long[i] else -1) for i in idx]


def v0_random(f, cfg):
    """Baseline: random direction on random tradeable bars. Defines what the
    TP/SL geometry alone yields with zero edge. Fixed seed."""
    rng = np.random.default_rng(cfg.seed)
    valid = np.flatnonzero(valid_mask(f))
    if len(valid) == 0:
        return []
    n = min(cfg.random_samples_per_symbol, len(valid))
    picks = rng.choice(valid, size=n, replace=False)
    dirs = rng.choice(np.array([1, -1]), size=n)
    order = np.argsort(picks)
    return [(int(picks[j]), int(dirs[j])) for j in order]


def v1_bb_rsi(f, cfg):
    """Bollinger + RSI fade inside a 1H range. Control -- already failed on
    BTC/DOGE/AVAX in the spec's preliminary work."""
    v = valid_mask(f)
    c = f["close"].to_numpy()
    ranging = v & (f["adx_1h"].to_numpy() < cfg.adx_max)
    long = ranging & (c < f["bb_lo"].to_numpy()) & (f["rsi"].to_numpy() < cfg.rsi_low)
    short = ranging & (c > f["bb_hi"].to_numpy()) & (f["rsi"].to_numpy() > cfg.rsi_high)
    return _combine(long, short)


def v2_rsi2_trend(f, cfg):
    """Short-term exhaustion inside a larger trend, faded in the trend's direction."""
    v = valid_mask(f)
    c = f["close"].to_numpy()
    t = f["trend_4h"].to_numpy()
    r2 = f["rsi_fast"].to_numpy()
    e = f["ema_base"].to_numpy()
    long = v & (t == 1) & (r2 < cfg.rsi2_low) & (c > e)
    short = v & (t == -1) & (r2 > cfg.rsi2_high) & (c < e)
    return _combine(long, short)


def v3_vwap_climax(f, cfg):
    """VWAP stretch with a volume climax and a rejection wick."""
    v = valid_mask(f)
    dev = f["vwap_dev"].to_numpy()
    sd = f["vwap_sd"].to_numpy()
    climax = v & (f["volume"].to_numpy() > cfg.vol_mult * f["vol_avg"].to_numpy())
    stretch = cfg.vwap_k * sd
    long = climax & (dev < -stretch) & (f["lower_wick"].to_numpy() >= cfg.wick_frac)
    short = climax & (dev > stretch) & (f["upper_wick"].to_numpy() >= cfg.wick_frac)
    return _combine(long, short)


def v4_confluence(f, cfg):
    """V3's trigger, taken only when it points the same way as V2's 4H trend."""
    t = f["trend_4h"].to_numpy()
    return [(i, d) for i, d in v3_vwap_climax(f, cfg) if t[i] == d]


VARIANTS = {
    "V0": v0_random,
    "V1": v1_bb_rsi,
    "V2": v2_rsi2_trend,
    "V3": v3_vwap_climax,
    "V4": v4_confluence,
}
