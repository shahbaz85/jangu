"""Live signal agent. Runs the SAME engine as the backtest on each closed 15m candle.

It only SENDS signals. It does not place orders. Keep it that way until paper trading
confirms live signals match backtest behaviour.

If this process was offline for a while (laptop asleep/off), it catches up on any
signal formed during the gap that is still within its order-validity window when it
comes back, instead of only ever looking at the newest candle.

Setup:
  export TELEGRAM_BOT_TOKEN=...   (create a bot with @BotFather)
  export TELEGRAM_CHAT_ID=...     (your chat id, e.g. from @userinfobot)
  python live.py                  # add --once to evaluate a single candle and exit
"""
import argparse
import json
import os
import time
from pathlib import Path

import pandas as pd
import requests

from config import Config
from data import fetch_ohlcv
from engine import SMCEngine
from risk import position_size

STATE = Path("sent_signals.json")
JOURNAL = Path("signal_journal.csv")
NEWS = Path("news.csv")     # optional: column 'time' (UTC) of high-impact events


def notify(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    print(text, "\n")
    if token and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=10)
        except Exception as e:
            print("telegram error:", e)


def market_context(ex, symbol):
    """Funding rate and whether open interest rose over the last hour. Both optional."""
    funding = oi_rising = None
    try:
        funding = float(ex.fetch_funding_rate(symbol)["fundingRate"])
    except Exception:
        pass
    try:
        hist = ex.fetch_open_interest_history(symbol, "15m", limit=5)
        vals = [h.get("openInterestValue") or h.get("openInterestAmount") for h in hist]
        vals = [v for v in vals if v]
        if len(vals) >= 5:
            oi_rising = vals[-1] > vals[0]
    except Exception:
        pass
    return funding, oi_rising


def fmt(sig, sizing, funding):
    p = lambda x: f"{x:,.2f}" if x >= 100 else f"{x:.5g}"
    lines = [
        f"{'🟢' if sig['side'] == 'LONG' else '🔴'} {sig['side']} {sig['symbol']}  |  Grade {sig['grade']} ({sig['score']} pts)",
        f"Entry (limit, {sig['poi']}): {p(sig['entry'])}",
        f"Stop: {p(sig['stop'])}   ({sig['sl_atr']} ATR)",
        f"TP1: {p(sig['tp1'])}  ({sig['tp1_r']}R, close 50%, SL to BE)",
        f"TP2: {p(sig['tp2'])}  ({sig['rr_tp2']}R)",
        f"Risk {sig['risk_pct']}% = ${sizing['risk_usd']}  |  qty {sizing['qty']:.4f}  |  {sizing['leverage']}x isolated",
        f"Valid until {sig['valid_until']:%Y-%m-%d %H:%M} UTC",
        "Why: " + "; ".join(sig["reasons"]),
    ]
    if funding is not None:
        lines.append(f"Funding: {funding * 100:.4f}%")
    if sizing["note"]:
        lines.append("⚠️ " + sizing["note"])
    return "\n".join(lines)


def correlated_open(cfg, symbol, sent):
    """Crude correlation guard: an unexpired signal on a correlated symbol blocks new ones."""
    now = pd.Timestamp.now(tz="UTC")
    for group in cfg.correlated_groups:
        if symbol in group:
            for s in sent.values():
                if s["symbol"] in group and s["symbol"] != symbol and pd.Timestamp(s["valid_until"]) > now:
                    return s["symbol"]
    return None


def evaluate(cfg, ex, eng, sent):
    now = None    # newest candle close time seen this pass, across symbols; anchors staleness pruning
    for symbol in cfg.symbols:
        try:
            df = fetch_ohlcv(symbol, "15m", days=26, exchange_id=cfg.exchange_id)
        except Exception as e:
            print(symbol, "fetch error:", e)
            continue
        if df.empty:
            continue
        latest = df.index[-1] + pd.Timedelta(cfg.base_tf)   # close time of the newest candle we have
        now = latest if now is None else max(now, latest)
        funding, oi_rising = market_context(ex, symbol)
        signals, _, _ = eng.run(df, symbol, funding=funding, oi_rising=oi_rising)
        for sig in signals:
            # skip ones already alerted, and ones whose order window has already expired
            # (e.g. formed while this process was offline) rather than alerting on a dead order
            if sig["id"] in sent or sig["valid_until"] < latest:
                continue
            blocker = correlated_open(cfg, symbol, sent)
            sizing = position_size(cfg.account_equity, sig["risk_pct"], sig["entry"], sig["stop"], cfg)
            text = fmt(sig, sizing, funding)
            if blocker:
                text += f"\n⚠️ Correlated signal on {blocker} still active: count both as ONE risk unit."
            notify(text)
            sent[sig["id"]] = {"symbol": symbol, "valid_until": str(sig["valid_until"])}
            row = {k: sig[k] for k in ("id", "time", "symbol", "side", "grade", "score", "entry", "stop",
                                       "tp1", "tp2", "rr_tp2", "risk_pct")}
            row["reasons"] = "; ".join(sig["reasons"])
            pd.DataFrame([row]).to_csv(JOURNAL, mode="a", header=not JOURNAL.exists(), index=False)
    if now is not None:
        stale = now - pd.Timedelta(days=1)
        for k in [k for k, v in sent.items() if pd.Timestamp(v["valid_until"]) < stale]:
            del sent[k]
    STATE.write_text(json.dumps(sent, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    import ccxt
    cfg = Config()
    ex = getattr(ccxt, cfg.exchange_id)({"enableRateLimit": True})
    news = pd.to_datetime(pd.read_csv(NEWS)["time"], utc=True) if NEWS.exists() else None
    eng = SMCEngine(cfg, news)
    sent = json.loads(STATE.read_text()) if STATE.exists() else {}
    notify(f"SMC agent started: {', '.join(cfg.symbols)}")
    while True:
        evaluate(cfg, ex, eng, sent)
        if args.once:
            break
        now = time.time()
        time.sleep(900 - now % 900 + 8)    # wake 8s after each 15m candle closes


if __name__ == "__main__":
    main()
