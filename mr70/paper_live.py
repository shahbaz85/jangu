"""MR-70 V3 paper observer. Logs signals forward for study. Places NO orders, ever.

This exists because the backtest says V3 is about 2 pp of win rate short of paying
its own costs. The one thing that could legitimately change that verdict is if live
behaviour differs from what the backtest assumed -- mainly whether limit entries
actually fill as often as modelled. So this records reality and lets you check.

Safety: this module never imports, constructs or calls any authenticated exchange
method. It reads public candles only. There is no API key, no order function, and
no code path that could place one.

What it records, per signal:
  - the full feature snapshot at the signal bar (for later conditioning research)
  - whether the limit entry filled, and on which bar
  - the outcome under BOTH geometries at once (0.75/2.0 and 1.5/3.0), since paper
    trades cost nothing to run in parallel
  - the next 40 raw candles after the signal, so ANY future geometry can be
    re-evaluated offline without waiting for new data

Restart-safe: in-flight trades and the last processed candle per symbol live in a
JSON state file, so closing the laptop loses nothing and missed candles are caught
up on the next run.

Usage:
  python mr70/paper_live.py --once      # single pass, useful for a first check
  python mr70/paper_live.py             # run continuously
"""
import argparse
import json
import os
import pathlib
import sys
import time
from dataclasses import replace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from data import fetch_ohlcv
from mr70.config import MR70Config
from mr70.indicators import build_features
from mr70.signals import v3_vwap_climax
from mr70.v3_followup import break_even

HERE = pathlib.Path(__file__).resolve().parent
STATE = HERE / "paper_state.json"
SIGNALS_CSV = HERE / "paper_signals.csv"
TRADES_CSV = HERE / "paper_trades.csv"
FORWARD_CSV = HERE / "paper_forward_bars.csv"

WARMUP_DAYS = 120          # enough for the 4H EMA(200) to converge
REFRESH_DAYS = 3           # incremental pull each cycle
FORWARD_BARS = 40          # raw candles kept after each signal for offline re-analysis

GEOMETRIES = {
    "old": {"tp_atr": 0.75, "sl_atr": 2.0, "max_bars": 16},
    "new": {"tp_atr": 1.5, "sl_atr": 3.0, "max_bars": 32},
}

FEATURE_SNAPSHOT = ["atr", "rsi", "rsi_fast", "vwap", "vwap_dev", "vwap_sd", "vol_avg",
                    "lower_wick", "upper_wick", "adx_1h", "close_4h", "ema50_4h",
                    "ema200_4h", "trend_4h", "ema_base", "bb_lo", "bb_hi"]


def fetch_with_retry(symbol: str, days: int, cfg, attempts: int = 5):
    """Binance rate-limits after heavy pulls; a bare failure silently skips a symbol."""
    delay = 2
    for k in range(attempts):
        try:
            return fetch_ohlcv(symbol, "15m", days, cfg.exchange_id)
        except Exception as e:
            if k == attempts - 1:
                raise
            print(f"  {symbol}: fetch failed ({type(e).__name__}), retry in {delay}s", flush=True)
            time.sleep(delay)
            delay *= 2


def notify(text: str):
    print(text, flush=True)
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            import requests
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=10)
        except Exception as e:
            print("telegram error:", e, flush=True)


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"symbols": {}}


def save_state(state: dict):
    STATE.write_text(json.dumps(state, indent=1, default=str))


def append_csv(path: pathlib.Path, rows: list):
    if rows:
        pd.DataFrame(rows).to_csv(path, mode="a", header=not path.exists(), index=False)


def _new_trade(sig_id, symbol, i, d, f, cfg):
    entry = float(f["close"].iloc[i])
    atr = float(f["atr"].iloc[i])
    rec = {
        "id": sig_id, "symbol": symbol, "dir": int(d),
        "side": "LONG" if d == 1 else "SHORT",
        "signal_time": str(f["close_time"].iloc[i]),
        "entry": entry, "atr": atr,
        "filled": False, "fill_time": None, "bars_waited": 0, "bars_since_fill": 0,
        "forward": [], "source": "live",
        "features": {k: (None if pd.isna(f[k].iloc[i]) else float(f[k].iloc[i]))
                     for k in FEATURE_SNAPSHOT},
        "geo": {},
    }
    for name, g in GEOMETRIES.items():
        be, cw, cl = break_even(entry, atr, replace(cfg, tp_atr=g["tp_atr"], sl_atr=g["sl_atr"]), symbol)
        rec["geo"][name] = {
            "tp": entry + d * g["tp_atr"] * atr,
            "sl": entry - d * g["sl_atr"] * atr,
            "max_bars": g["max_bars"],
            "required_hit_rate": be, "cost_win_atr": cw, "cost_loss_atr": cl,
            "status": "pending", "exit_time": None, "exit_px": None, "bars_held": None,
        }
    return rec


def _advance(rec, bar, cfg):
    """Feed one freshly closed candle to one in-flight record. Mirrors the backtest:
    entry is a limit at the signal close valid `entry_valid_bars`, SL wins same-bar
    ties, and an unresolved trade is cut at the time stop."""
    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    d, entry = rec["dir"], rec["entry"]

    if len(rec["forward"]) < FORWARD_BARS:
        rec["forward"].append({"t": str(bar["close_time"]), "o": o, "h": h, "l": l,
                               "c": c, "v": bar["volume"]})

    if not rec["filled"]:
        rec["bars_waited"] += 1
        hit = (l <= entry) if d == 1 else (h >= entry)
        if hit:
            rec["filled"] = True
            rec["fill_time"] = str(bar["close_time"])
        elif rec["bars_waited"] >= cfg.entry_valid_bars:
            for g in rec["geo"].values():
                g["status"] = "unfilled"
            return
        else:
            return

    rec["bars_since_fill"] += 1
    for g in rec["geo"].values():
        if g["status"] != "pending":
            continue
        hit_sl = (l <= g["sl"]) if d == 1 else (h >= g["sl"])
        hit_tp = (h >= g["tp"]) if d == 1 else (l <= g["tp"])
        if hit_sl:                                   # conservative: SL wins ties
            g.update(status="sl", exit_px=g["sl"])
        elif hit_tp:
            g.update(status="tp", exit_px=g["tp"])
        elif rec["bars_since_fill"] >= g["max_bars"]:
            g.update(status="time", exit_px=c)
        else:
            continue
        g["exit_time"] = str(bar["close_time"])
        g["bars_held"] = rec["bars_since_fill"]


def _done(rec) -> bool:
    return all(g["status"] != "pending" for g in rec["geo"].values()) and \
        (len(rec["forward"]) >= FORWARD_BARS or all(
            g["status"] == "unfilled" for g in rec["geo"].values()))


def _flush_rows(rec, cfg):
    """Turn a finished record into trade rows (one per geometry) plus forward bars."""
    trades, fwd = [], []
    for name, g in rec["geo"].items():
        row = {"id": rec["id"], "geometry": name, "source": rec.get("source", "live"),
               "symbol": rec["symbol"],
               "side": rec["side"], "signal_time": rec["signal_time"],
               "entry": rec["entry"], "atr": rec["atr"],
               "filled": rec["filled"], "fill_time": rec["fill_time"],
               "tp": g["tp"], "sl": g["sl"], "status": g["status"],
               "exit_time": g["exit_time"], "exit_px": g["exit_px"],
               "bars_held": g["bars_held"],
               "required_hit_rate": g["required_hit_rate"]}
        if g["status"] in ("tp", "sl", "time"):
            d = rec["dir"]
            risk = abs(rec["entry"] - g["sl"])
            gross = (g["exit_px"] - rec["entry"]) * d
            cost_atr = g["cost_win_atr"] if g["status"] == "tp" else g["cost_loss_atr"]
            row["R_gross"] = gross / risk
            row["R_net"] = (gross - cost_atr * rec["atr"]) / risk
            row["win"] = g["status"] == "tp"
        trades.append(row)
    for k, b in enumerate(rec["forward"]):
        fwd.append({"id": rec["id"], "offset": k, **b})
    return trades, fwd


def process_symbol(symbol: str, cfg: MR70Config, st: dict, first_run: bool,
                   backfill: bool = False):
    """first_run warms state from history. Those signals are NOT forward
    observations -- they re-derive candles the backtest already saw -- so unless
    --backfill is asked for they are used only to set the watermark, keeping the
    logged sample genuinely out of sample."""
    days = WARMUP_DAYS if first_run else max(REFRESH_DAYS, 1)
    df = fetch_with_retry(symbol, days, cfg)
    if df.empty:
        return [], [], []
    f = build_features(df, cfg)
    f = f.reset_index(drop=True)

    sym_state = st["symbols"].setdefault(symbol, {"last_ts": None, "live": []})
    last_ts = sym_state["last_ts"]
    times = [str(t) for t in f["close_time"]]

    if first_run and not backfill:
        sym_state["last_ts"] = times[-1] if times else None
        return [], [], []

    start = 0
    if last_ts is not None and last_ts in times:
        start = times.index(last_ts) + 1
    elif last_ts is not None:
        start = 0               # gap too large for this window; process what we have

    sig_idx = {i for i, _ in v3_vwap_climax(f, cfg)}
    live = sym_state["live"]
    new_signals, trades, fwd = [], [], []

    for i in range(start, len(f)):
        bar = {k: (float(f[k].iloc[i]) if k != "close_time" else f["close_time"].iloc[i])
               for k in ("open", "high", "low", "close", "volume", "close_time")}
        for rec in live:
            _advance(rec, bar, cfg)

        if i in sig_idx:
            sig_id = f"{symbol.split('/')[0]}-{pd.Timestamp(f['close_time'].iloc[i]):%Y%m%d%H%M}"
            if not any(r["id"] == sig_id for r in live):
                rec = _new_trade(sig_id, symbol, i, dict(v3_vwap_climax(f, cfg))[i], f, cfg)
                live.append(rec)
                new_signals.append({"id": sig_id, "symbol": symbol, "side": rec["side"],
                                    "signal_time": rec["signal_time"], "entry": rec["entry"],
                                    "atr": rec["atr"], **rec["features"]})

        finished = [r for r in live if _done(r)]
        for r in finished:
            t, w = _flush_rows(r, cfg)
            trades += t
            fwd += w
        if finished:
            live[:] = [r for r in live if r not in finished]

    sym_state["last_ts"] = times[-1] if times else last_ts
    return new_signals, trades, fwd


def cycle(cfg: MR70Config, st: dict, backfill: bool = False):
    for symbol in cfg.symbols:
        first_run = symbol not in st["symbols"]      # per symbol, so a failed
        try:                                          # symbol still starts clean later
            sigs, trades, fwd = process_symbol(symbol, cfg, st, first_run, backfill)
        except Exception as e:
            print(f"{symbol}: error {e}", flush=True)
            save_state(st)
            continue
        if first_run and not backfill:
            print(f"  {symbol}: watermark set at {st['symbols'][symbol]['last_ts']} "
                  f"(history not logged; forward signals only)", flush=True)
            save_state(st)
            continue
        append_csv(SIGNALS_CSV, sigs)
        append_csv(TRADES_CSV, trades)
        append_csv(FORWARD_CSV, fwd)
        for s in sigs:
            notify(f"MR70 V3 PAPER {s['side']} {symbol}\n"
                   f"entry {s['entry']:.6g}  atr {s['atr']:.6g}\n"
                   f"{s['signal_time']}\nNOT A TRADE -- observation only")
        n_live = len(st["symbols"].get(symbol, {}).get("live", []))
        print(f"  {symbol}: {len(sigs)} new, {n_live} in flight, "
              f"{len(trades)} closed rows", flush=True)
    save_state(st)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--backfill", action="store_true",
                    help="also log historical signals on a symbol's first run "
                         "(NOT forward out-of-sample data -- keep it separate)")
    args = ap.parse_args()
    cfg = replace(MR70Config(),
                  symbols=["BTC/USDT:USDT", "AVAX/USDT:USDT", "XRP/USDT:USDT", "ADA/USDT:USDT"],
                  high_slip_symbols=("AVAX/USDT:USDT", "XRP/USDT:USDT", "ADA/USDT:USDT"))
    st = load_state()
    notify("MR70 V3 paper observer started (signals only, never places orders): "
           + ", ".join(s.split("/")[0] for s in cfg.symbols))
    while True:
        cycle(cfg, st, args.backfill)
        if args.once:
            break
        now = time.time()
        time.sleep(900 - now % 900 + 8)      # wake 8s after each 15m close


if __name__ == "__main__":
    main()
