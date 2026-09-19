"""SMC signal engine.

Per symbol and direction it runs a small state machine:

    IDLE --(liquidity sweep in/near kill zone)--> SWEPT
    SWEPT --(structure break with displacement within N bars)--> build setup
    setup --(filters + score)--> SIGNAL (limit order) or REJECT (logged with reason)

Order fills, stop/TP handling and risk limits live in backtest.py (and in you, live).
"""
import numpy as np
import pandas as pd

from features import build_features, is_displacement


class SMCEngine:
    def __init__(self, cfg, news_times=None):
        self.cfg = cfg
        self.news = pd.DatetimeIndex(news_times if news_times is not None else [], tz="UTC")

    # ------------------------------------------------------------------ public
    def run(self, df: pd.DataFrame, symbol: str = "", funding=None, oi_rising=None, only_last=False):
        """Returns (signals, rejects, features). only_last=True: evaluate the latest bar only (live)."""
        cfg = self.cfg
        f = build_features(df, cfg)
        self.f = f
        self.a = {k: f[k].to_numpy() for k in (
            "open", "high", "low", "close", "atr", "atr_pct", "event", "trend", "bull_fvg", "bear_fvg",
            "pdh", "pdl", "asia_h", "asia_l", "fresh_pdh", "fresh_pdl", "fresh_asia_h", "fresh_asia_l",
            "eqh", "eql", "in_kz", "in_sweep_window", "h4_trend", "h1_hi", "h1_lo",
            "h1_bull_fvg_bot", "h1_bull_fvg_top", "h1_bear_fvg_bot", "h1_bear_fvg_top")}
        self.times = pd.DatetimeIndex(f["close_time"])
        N = len(f)
        warm = min(N, max(300, cfg.fractal_n_base * 4))
        signals, rejects = [], []
        trackers = {1: None, -1: None}
        a = self.a
        for i in range(warm, N):
            for d in (1, -1):
                tr = trackers[d]
                if tr and i - tr["idx"] > cfg.choch_window_bars:
                    trackers[d] = tr = None
                if a["in_sweep_window"][i]:
                    sw = self._sweep(i, d)
                    if sw:
                        extreme = a["low"][i] if d == 1 else a["high"][i]
                        if tr is None or (extreme - tr["extreme"]) * d < 0:   # deeper sweep replaces
                            trackers[d] = tr = {"idx": i, "extreme": extreme, **sw}
                if tr and np.sign(a["event"][i]) == d:
                    trackers[d] = None
                    if only_last and i != N - 1:
                        continue
                    res = self._build(i, d, tr, symbol, funding, oi_rising)
                    (signals if res["ok"] else rejects).append(res)
        if only_last:
            signals = [s for s in signals if s["idx"] == N - 1]
        return signals, rejects, f

    # ----------------------------------------------------------------- helpers
    def _sweep(self, i, d):
        a = self.a
        if d == 1:
            cands = [("PDL", a["pdl"][i], a["fresh_pdl"][i], True),
                     ("ASIA_LOW", a["asia_l"][i], a["fresh_asia_l"][i], True),
                     ("EQUAL_LOWS", a["eql"][i], True, False)]
            hits = [(n, lv, ext) for n, lv, fr, ext in cands
                    if not np.isnan(lv) and fr and a["low"][i] < lv < a["close"][i]]
        else:
            cands = [("PDH", a["pdh"][i], a["fresh_pdh"][i], True),
                     ("ASIA_HIGH", a["asia_h"][i], a["fresh_asia_h"][i], True),
                     ("EQUAL_HIGHS", a["eqh"][i], True, False)]
            hits = [(n, lv, ext) for n, lv, fr, ext in cands
                    if not np.isnan(lv) and fr and a["high"][i] > lv > a["close"][i]]
        if not hits:
            return None
        hits.sort(key=lambda x: not x[2])          # external liquidity first
        n, lv, ext = hits[0]
        return {"level_name": n, "level": float(lv), "external": bool(ext)}

    def _reject(self, i, d, symbol, reason, **extra):
        return {"ok": False, "idx": i, "time": self.times[i], "symbol": symbol,
                "side": "LONG" if d == 1 else "SHORT", "reason": reason, **extra}

    def _build(self, e, d, tr, symbol, funding, oi_rising):
        cfg, a = self.cfg, self.a
        o, h, l, c, at = a["open"], a["high"], a["low"], a["close"], a["atr"]
        atr_e = at[e]
        rj = lambda r, **k: self._reject(e, d, symbol, r, **k)

        if not is_displacement(o, h, l, c, at, e, d, cfg.displacement_body_atr, cfg.displacement_leg_atr):
            return rj("no_displacement")

        # FVG created inside the reversal leg, still unmitigated at its midpoint
        fvg = None
        flags = a["bull_fvg"] if d == 1 else a["bear_fvg"]
        for k in range(e, tr["idx"], -1):
            if flags[k]:
                zone = (h[k - 2], l[k]) if d == 1 else (h[k], l[k - 2])
                mid = sum(zone) / 2
                later = l[k + 1:e + 1] if d == 1 else h[k + 1:e + 1]
                if later.size == 0 or ((later.min() > mid) if d == 1 else (later.max() < mid)):
                    fvg = zone
                    break

        # Order block: last opposite candle at/before the extreme of the sweep leg
        seg = slice(tr["idx"], e + 1)
        ext_idx = tr["idx"] + (int(np.argmin(l[seg])) if d == 1 else int(np.argmax(h[seg])))
        ob = None
        for j in range(ext_idx, max(tr["idx"] - 5, 0) - 1, -1):
            if (c[j] - o[j]) * d < 0:
                ob = (l[j], o[j]) if d == 1 else (o[j], h[j])
                break

        if cfg.entry_mode == "fvg_mid" and fvg:
            entry, poi = sum(fvg) / 2, "FVG"
        elif ob:
            entry, poi = (ob[1] if d == 1 else ob[0]), "OB"
        elif fvg:
            entry, poi = sum(fvg) / 2, "FVG"
        else:
            return rj("no_fvg_or_ob")

        stop = tr["extreme"] - d * cfg.sl_buffer_atr * atr_e
        risk = (entry - stop) * d
        if risk <= 0:
            return rj("invalid_stop")
        if (c[e] - entry) * d <= 0:
            return rj("entry_not_on_retrace_side")
        if risk > cfg.max_sl_atr * atr_e:
            return rj("sl_too_wide", sl_atr=round(risk / atr_e, 2))

        # TP2 = nearest opposing liquidity beyond current price
        if d == 1:
            cands = [a["pdh"][e], a["asia_h"][e], a["eqh"][e], a["h1_hi"][e]]
            cands = [x for x in cands if not np.isnan(x) and x > c[e]]
            tp2 = min(cands) if cands else None
        else:
            cands = [a["pdl"][e], a["asia_l"][e], a["eql"][e], a["h1_lo"][e]]
            cands = [x for x in cands if not np.isnan(x) and x < c[e]]
            tp2 = max(cands) if cands else None
        if tp2 is None:
            return rj("no_target_liquidity")
        rr = (tp2 - entry) * d / risk
        if rr < cfg.min_rr:
            return rj("rr_too_low", rr=round(rr, 2))
        tp1_r = min(cfg.tp1_r, 0.6 * rr)
        tp1 = entry + d * tp1_r * risk
        runaway = tp1 if cfg.cancel_if_price_reaches == "tp1" else tp2
        if (h[e] >= runaway) if d == 1 else (l[e] <= runaway):
            return rj("move_already_hit_target")     # the trade already happened without us

        # ---- filters
        if not a["in_kz"][e]:
            return rj("outside_killzone")
        aligned = a["h4_trend"][e] == d
        if not aligned and not cfg.allow_counter_trend:
            return rj("against_4h_bias")
        t = self.times[e]
        if len(self.news) and np.any(np.abs((self.news - t).total_seconds()) <= cfg.news_block_minutes * 60):
            return rj("news_blackout")
        p = a["atr_pct"][e]
        if not np.isnan(p) and (p < cfg.atr_pct_low or p > cfg.atr_pct_high):
            return rj("volatility_filter", atr_pct=round(float(p), 2))
        hi, lo = a["h1_hi"][e], a["h1_lo"][e]
        rng = hi - lo if not (np.isnan(hi) or np.isnan(lo)) else np.nan
        if cfg.require_premium_discount and not np.isnan(rng) and rng > 0:
            eq = lo + rng / 2
            if (d == 1 and entry >= eq) or (d == -1 and entry <= eq):
                return rj("wrong_premium_discount")
        if funding is not None and funding * d > cfg.funding_extreme:
            return rj("funding_crowded", funding=funding)

        # ---- confluence score
        reasons = []
        score = 3                         # structure break + displacement (2) and kill zone (1), both required
        reasons.append(f"{tr['level_name']} swept")
        reasons.append(("15m CHoCH" if abs(a["event"][e]) == 2 else "15m BOS") + " with displacement")
        if aligned:
            score += 2; reasons.append("4H bias aligned")
        if tr["external"]:
            score += 2; reasons.append("external liquidity sweep")
        if fvg and ob and max(fvg[0], ob[0]) <= min(fvg[1], ob[1]):
            score += 1; reasons.append("FVG/OB overlap")
        in_ote = False
        if not np.isnan(rng) and rng > 0:
            in_ote = (hi - 0.79 * rng <= entry <= hi - 0.62 * rng) if d == 1 else (lo + 0.62 * rng <= entry <= lo + 0.79 * rng)
        if in_ote:
            score += 1; reasons.append("entry in 1H OTE")
        pb, pt = (a["h1_bull_fvg_bot"][e], a["h1_bull_fvg_top"][e]) if d == 1 else (a["h1_bear_fvg_bot"][e], a["h1_bear_fvg_top"][e])
        if not np.isnan(pb) and pb - 0.25 * atr_e <= tr["extreme"] <= pt + 0.25 * atr_e:
            score += 1; reasons.append("reacted from 1H FVG")
        if oi_rising:
            score += 1; reasons.append("open interest rising")
        if score >= cfg.grade_a:
            grade = "A"
        elif score >= cfg.grade_b:
            grade = "B"
        else:
            return rj("score_too_low", score=score)

        vi = e + cfg.order_valid_bars
        return {
            "ok": True, "id": f"{symbol}-{t.strftime('%Y%m%d%H%M')}-{'L' if d == 1 else 'S'}",
            "symbol": symbol, "side": "LONG" if d == 1 else "SHORT", "dir": d, "idx": e, "time": t,
            "entry": float(entry), "stop": float(stop), "tp1": float(tp1),
            "tp2": float(tp2), "rr_tp2": round(float(rr), 2), "tp1_r": round(float(tp1_r), 2),
            "score": score, "grade": grade, "poi": poi,
            "risk_pct": cfg.risk_pct_a if grade == "A" else cfg.risk_pct_b,
            "sl_atr": round(float(risk / atr_e), 2), "valid_until_idx": vi,
            "valid_until": t + pd.Timedelta(cfg.base_tf) * cfg.order_valid_bars,
            "reasons": reasons,
        }
