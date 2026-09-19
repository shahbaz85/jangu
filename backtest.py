"""Bar-by-bar backtest of engine signals.

Conservative assumptions (so live results are unlikely to be *worse* for mechanical reasons):
- signal is known at the close of bar e; the limit order can fill from bar e+1
- if stop and target are both inside the same candle, the STOP is assumed hit first
- no take-profit on the fill candle; stop on the fill candle is checked
- limit entry and TP exits pay maker fee; stops and time exits pay taker fee + slippage
- pending order is cancelled if price reaches the runaway target first (cfg.cancel_if_price_reaches)
- one position per symbol; daily trade cap, daily loss limit, losing-streak pause

Usage:
  python backtest.py --synthetic
  python backtest.py --fetch BTC/USDT:USDT --days 730
  python backtest.py --csv data/BTCUSDT_15m.csv
"""
import argparse
import json
import numpy as np
import pandas as pd

from config import Config
from engine import SMCEngine


def simulate(f, signals, cfg, start_equity=10_000.0):
    by_idx = {}
    for s in signals:
        by_idx.setdefault(s["idx"], s)
    o, h, l, c = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
    ev = f["event"].to_numpy()
    runaway_key = "tp1" if cfg.cancel_if_price_reaches == "tp1" else "tp2"
    ct = pd.DatetimeIndex(f["close_time"])   # all reported times = candle CLOSE time (UTC)
    days = pd.DatetimeIndex(f["close_time"]).normalize()
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

        # ---- manage open position
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
                if (h[i] >= pos["tp2"]) if d == 1 else (l[i] <= pos["tp2"]):
                    pos["exits"].append((pos["remaining"], pos["tp2"], cfg.maker_fee))
                    close_trade(pos, i, "tp2"); pos = None
                elif i - pos["fill_idx"] >= cfg.max_bars_in_trade:
                    pos["exits"].append((pos["remaining"], c[i] * (1 - d * cfg.slippage), cfg.taker_fee))
                    close_trade(pos, i, "time"); pos = None

        # ---- manage pending limit order
        if pending is not None and i > pending["idx"]:
            p, d = pending, pending["dir"]
            if (l[i] <= p["entry"]) if d == 1 else (h[i] >= p["entry"]):
                pos = {**p, "fill_idx": i, "risk": abs(p["entry"] - p["stop"]), "cur_stop": p["stop"],
                       "remaining": 1.0, "tp1_done": False, "exits": []}
                pending = None
                day_trades += 1
                if (l[i] <= pos["stop"]) if d == 1 else (h[i] >= pos["stop"]):
                    pos["exits"].append((1.0, pos["stop"] * (1 - d * cfg.slippage), cfg.taker_fee))
                    close_trade(pos, i, "stop"); pos = None
            elif ((h[i] >= p[runaway_key]) if d == 1 else (l[i] <= p[runaway_key])) or np.sign(ev[i]) == -d or i >= p["valid_until_idx"]:
                pending = None

        # ---- new signal
        s = by_idx.get(i)
        if s and pos is None and pending is None:
            if streak >= cfg.max_consec_losses:
                paused_until, streak = i + cfg.pause_bars_after_streak, 0
            if i >= paused_until and day_trades < cfg.max_trades_per_day and day_r > -cfg.daily_loss_limit_r:
                pending = dict(s)
    return pd.DataFrame(trades), equity


def max_drawdown(x):
    peak = np.maximum.accumulate(x)
    return float((peak - x).max())


def stats(tr, start_equity, final_equity, mc_runs=5000, seed=1):
    if tr.empty:
        return {"trades": 0}
    R = tr["R"].to_numpy()
    wins, losses = R[R > 0], R[R <= 0]
    cum = np.r_[0, np.cumsum(R)]
    streaks, run = [], 0
    for r in R:
        run = run + 1 if r <= 0 else 0
        streaks.append(run)
    rng = np.random.default_rng(seed)
    mc_dd = [max_drawdown(np.r_[0, np.cumsum(rng.permutation(R))]) for _ in range(mc_runs)]
    eq = np.r_[start_equity, tr["equity"].to_numpy()]
    return {
        "trades": len(R),
        "win_rate": round(len(wins) / len(R), 3),
        "avg_win_R": round(float(wins.mean()), 2) if len(wins) else 0,
        "avg_loss_R": round(float(losses.mean()), 2) if len(losses) else 0,
        "expectancy_R": round(float(R.mean()), 3),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None,
        "total_R": round(float(R.sum()), 2),
        "max_drawdown_R": round(max_drawdown(cum), 2),
        "max_drawdown_equity_pct": round(float(((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max() * 100), 2),
        "longest_losing_streak": int(max(streaks)),
        "mc_95pct_drawdown_R": round(float(np.percentile(mc_dd, 95)), 2),
        "mc_99pct_drawdown_R": round(float(np.percentile(mc_dd, 99)), 2),
        "final_equity": round(final_equity, 2),
        "return_pct": round((final_equity / start_equity - 1) * 100, 2),
        "exit_reasons": tr["exit_reason"].value_counts().to_dict(),
        "by_grade": tr.groupby("grade")["R"].agg(["count", "mean"]).round(3).to_dict("index"),
        "by_side": tr.groupby("side")["R"].agg(["count", "mean"]).round(3).to_dict("index"),
    }


def run_backtest(df, cfg, symbol="", news=None, verbose=True):
    eng = SMCEngine(cfg, news)
    signals, rejects, f = eng.run(df, symbol)
    tr, final_eq = simulate(f, signals, cfg)
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
    ap.add_argument("--news", help="CSV with a UTC 'time' column of high-impact events")
    ap.add_argument("--out", default="trades.csv")
    args = ap.parse_args()
    cfg = Config()
    from data import load_csv, fetch_ohlcv, synthetic, save_csv
    if args.csv:
        df, sym = load_csv(args.csv), args.csv
    elif args.fetch:
        df, sym = fetch_ohlcv(args.fetch, "15m", args.days, cfg.exchange_id), args.fetch
        save_csv(df, f"{args.fetch.split('/')[0]}_15m.csv")
    else:
        df, sym = synthetic(days=365), "SYNTH"
    news = pd.to_datetime(pd.read_csv(args.news)["time"], utc=True) if args.news else None
    st, tr, *_ = run_backtest(df, cfg, sym, news)
    tr.to_csv(args.out, index=False)
    print(f"\n{len(tr)} trades written to {args.out}")
