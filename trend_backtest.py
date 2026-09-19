"""Backtest for the trend-following engine. Reuses simulate()/stats() from backtest.py
unchanged -- only the signal-generation logic (trend_engine.py) is new.

Usage:
  python trend_backtest.py --synthetic
  python trend_backtest.py --fetch BTC/USDT:USDT --days 730
  python trend_backtest.py --csv BTC_15m.csv
"""
import argparse
import json

import pandas as pd

from trend_config import TrendConfig
from trend_engine import TrendEngine
from backtest import simulate, stats


def simulate_trend(f, signals, cfg, start_equity=10_000.0):
    """Like backtest.simulate(), but after TP1 the remainder trails an ATR-multiple
    stop instead of exiting at a fixed TP2. A fixed R:R target caps every winner at
    the same size, which is the wrong shape for a trend-following entry -- the edge
    in trend-following comes from occasionally letting a big move run, while still
    cutting losers at the initial stop. This is the standard trend-following exit."""
    by_idx = {}
    for s in signals:
        by_idx.setdefault(s["idx"], s)
    o, h, l, c, at = (f[k].to_numpy() for k in ("open", "high", "low", "close", "atr"))
    ct = pd.DatetimeIndex(f["close_time"])
    days = ct.normalize()
    trades, pending, pos = [], None, None
    day_trades, day_r, cur_day = 0, 0.0, None
    streak, paused_until = 0, -1
    equity = start_equity

    def close_trade(p, i, reason):
        nonlocal equity, day_r, streak
        d, risk = p["dir"], p["risk"]
        pnl = sum(fr * (px - p["entry"]) * d for fr, px, _ in p["exits"])
        fees = p["entry"] * cfg.maker_fee + sum(fr * px * fee for fr, px, fee in p["exits"])
        r = (pnl - fees) / risk
        equity *= 1 + r * p["risk_pct"] / 100
        day_r += r
        streak = streak + 1 if r < 0 else 0
        trades.append({**{k: p[k] for k in ("id", "side", "grade", "score", "entry", "stop", "tp1", "tp2", "rr_tp2")},
                       "signal_time": p["time"], "fill_time": ct[p["fill_idx"]], "exit_time": ct[i],
                       "exit_reason": reason, "bars_held": i - p["fill_idx"], "R": r, "equity": equity})

    for i in range(len(f)):
        if days[i] != cur_day:
            cur_day, day_trades, day_r = days[i], 0, 0.0

        if pos is not None and i > pos["fill_idx"]:
            d, rem = pos["dir"], pos["remaining"]
            stop_hit = (l[i] <= pos["cur_stop"]) if d == 1 else (h[i] >= pos["cur_stop"])
            if stop_hit:
                px = pos["cur_stop"] * (1 - d * cfg.slippage)
                pos["exits"].append((rem, px, cfg.taker_fee))
                close_trade(pos, i, "breakeven" if pos["tp1_done"] else "stop"); pos = None
            else:
                if not pos["tp1_done"] and ((h[i] >= pos["tp1"]) if d == 1 else (l[i] <= pos["tp1"])):
                    pos["exits"].append((cfg.tp1_close_frac, pos["tp1"], cfg.maker_fee))
                    pos["remaining"] -= cfg.tp1_close_frac
                    pos["tp1_done"] = True
                    if cfg.move_sl_to_be_after_tp1:
                        pos["cur_stop"] = pos["entry"]
                    pos["extreme"] = h[i] if d == 1 else l[i]
                if pos["tp1_done"]:
                    pos["extreme"] = max(pos["extreme"], h[i]) if d == 1 else min(pos["extreme"], l[i])
                    trail = pos["extreme"] - d * cfg.trail_atr_mult * at[i]
                    pos["cur_stop"] = max(pos["cur_stop"], trail) if d == 1 else min(pos["cur_stop"], trail)
                if pos is not None and i - pos["fill_idx"] >= cfg.max_bars_in_trade:
                    pos["exits"].append((pos["remaining"], c[i] * (1 - d * cfg.slippage), cfg.taker_fee))
                    close_trade(pos, i, "time"); pos = None

        if pending is not None and i > pending["idx"]:
            p, d = pending, pending["dir"]
            if (l[i] <= p["entry"]) if d == 1 else (h[i] >= p["entry"]):
                pos = {**p, "fill_idx": i, "risk": abs(p["entry"] - p["stop"]), "cur_stop": p["stop"],
                       "remaining": 1.0, "tp1_done": False, "exits": [], "extreme": p["entry"]}
                pending = None
                day_trades += 1
                if (l[i] <= pos["stop"]) if d == 1 else (h[i] >= pos["stop"]):
                    pos["exits"].append((1.0, pos["stop"] * (1 - d * cfg.slippage), cfg.taker_fee))
                    close_trade(pos, i, "stop"); pos = None
            elif i >= p["valid_until_idx"]:
                pending = None

        s = by_idx.get(i)
        if s and pos is None and pending is None:
            if streak >= cfg.max_consec_losses:
                paused_until, streak = i + cfg.pause_bars_after_streak, 0
            if i >= paused_until and day_trades < cfg.max_trades_per_day and day_r > -cfg.daily_loss_limit_r:
                pending = dict(s)
    return pd.DataFrame(trades), equity


def run_trend_backtest(df, cfg, symbol="", news=None, verbose=True, trailing=False):
    eng = TrendEngine(cfg, news)
    signals, rejects, f = eng.run(df, symbol)
    tr, final_eq = (simulate_trend if trailing else simulate)(f, signals, cfg)
    st = stats(tr, 10_000.0, final_eq)
    st["signals"] = len(signals)
    st["reject_reasons"] = pd.Series([r["reason"] for r in rejects]).value_counts().to_dict() if rejects else {}
    if verbose:
        print(json.dumps(st, indent=2, default=str))
    return st, tr, signals, rejects


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--fetch", help="ccxt symbol, e.g. BTC/USDT:USDT")
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--trailing", action="store_true", help="trail a stop after TP1 instead of a fixed TP2")
    ap.add_argument("--out", default="trend_trades.csv")
    args = ap.parse_args()
    cfg = TrendConfig()
    from data import load_csv, fetch_ohlcv, synthetic, save_csv
    if args.csv:
        df, sym = load_csv(args.csv), args.csv
    elif args.fetch:
        df, sym = fetch_ohlcv(args.fetch, "15m", args.days, cfg.exchange_id), args.fetch
        save_csv(df, f"{args.fetch.split('/')[0]}_15m.csv")
    else:
        df, sym = synthetic(days=365), "SYNTH"
    st, tr, *_ = run_trend_backtest(df, cfg, sym, trailing=args.trailing)
    tr.to_csv(args.out, index=False)
    print(f"\n{len(tr)} trades written to {args.out}")
