"""Trend-following signal engine: 4H EMA trend bias + 15m pullback-and-reclaim entry.

Per bar and direction: if the HTF trend is established (EMA separation exceeds a
minimum ATR threshold) and price just closed back on the trend side of the 15m
pullback EMA -- having closed on the other side the prior bar -- with a
same-direction candle, that's the trigger. Entry sits a small buffer behind the
trigger candle's close (so it fills almost immediately, unlike a level far from
price), stop/targets are fixed ATR/R multiples.

Order fills, stop/TP handling and risk limits live in backtest.py (reused as-is).
"""
import numpy as np
import pandas as pd

from trend_features import build_trend_features


class TrendEngine:
    def __init__(self, cfg, news_times=None):
        self.cfg = cfg
        self.news = pd.DatetimeIndex(news_times if news_times is not None else [], tz="UTC")

    def run(self, df: pd.DataFrame, symbol: str = "", funding=None, oi_rising=None):
        cfg = self.cfg
        f = build_trend_features(df, cfg)
        o, h, l, c = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
        at, ema, htf_trend, htf_sep, atr_pct = (f[k].to_numpy() for k in
            ("atr", "ema_pullback", "htf_trend", "htf_sep_atr", "atr_pct"))
        times = pd.DatetimeIndex(f["close_time"])
        N = len(f)
        warm = min(N, 300)
        signals, rejects = [], []

        def rj(i, d, reason, **extra):
            return {"ok": False, "idx": i, "time": times[i], "symbol": symbol,
                    "side": "LONG" if d == 1 else "SHORT", "reason": reason, **extra}

        for i in range(warm, N):
            for d in (1, -1):
                triggered = ((c[i - 1] - ema[i - 1]) * d < 0 and (c[i] - ema[i]) * d > 0
                             and (c[i] - o[i]) * d > 0)
                if not triggered:
                    continue

                atr_i = at[i]
                if np.isnan(atr_i) or atr_i <= 0:
                    continue
                if htf_trend[i] != d:
                    rejects.append(rj(i, d, "against_htf_trend"))
                    continue
                sep = htf_sep[i]
                if np.isnan(sep) or abs(sep) < cfg.min_trend_atr_sep:
                    rejects.append(rj(i, d, "trend_too_weak",
                                       sep=round(float(sep), 2) if not np.isnan(sep) else None))
                    continue
                p = atr_pct[i]
                if not np.isnan(p) and (p < cfg.atr_pct_low or p > cfg.atr_pct_high):
                    rejects.append(rj(i, d, "volatility_filter", atr_pct=round(float(p), 2)))
                    continue

                entry = c[i] - d * cfg.entry_buffer_atr * atr_i
                stop = entry - d * cfg.stop_atr_mult * atr_i
                risk = (entry - stop) * d
                if risk <= 0:
                    rejects.append(rj(i, d, "invalid_stop"))
                    continue

                tp2 = entry + d * cfg.min_rr * risk
                tp1_r = min(cfg.tp1_r, 0.6 * cfg.min_rr)
                tp1 = entry + d * tp1_r * risk
                grade = "A" if abs(sep) >= cfg.strong_trend_atr_sep else "B"
                t = times[i]
                signals.append({
                    "ok": True, "id": f"{symbol}-{t.strftime('%Y%m%d%H%M')}-{'L' if d == 1 else 'S'}",
                    "symbol": symbol, "side": "LONG" if d == 1 else "SHORT", "dir": d, "idx": i, "time": t,
                    "entry": float(entry), "stop": float(stop), "tp1": float(tp1), "tp2": float(tp2),
                    "rr_tp2": round(float(cfg.min_rr), 2), "tp1_r": round(float(tp1_r), 2),
                    "score": round(float(abs(sep)), 2), "grade": grade, "poi": "EMA_PULLBACK",
                    "risk_pct": cfg.risk_pct_a if grade == "A" else cfg.risk_pct_b,
                    "sl_atr": round(float(risk / atr_i), 2), "valid_until_idx": i + cfg.order_valid_bars,
                    "valid_until": t + pd.Timedelta(cfg.base_tf) * cfg.order_valid_bars,
                    "reasons": [f"4H trend {'up' if d == 1 else 'down'}, separation {abs(sep):.2f} ATR",
                                "15m pullback to EMA reclaimed with a same-direction candle"],
                })
        return signals, rejects, f
