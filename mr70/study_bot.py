"""MR-70 V3 paper-study signal bot. SIGNALS ONLY -- this file places no orders.

There is deliberately no order-placing code here and no private API call of any
kind. The bot reads public market data, sends Telegram alerts, and writes a CSV.
`study_bot_tests.py` scans this file and fails if an order method ever appears.

What it is for: V3 on 15m tested at 72.9% wins against a ~75% break-even, so the
rule is not expected to make money. The bot exists to measure two things the
backtest cannot -- how live fills compare with the touch-fill assumption, and
whether the owner's own take/skip judgement improves on the rule.

Usage:
  python mr70/study_bot.py                 # run continuously
  python mr70/study_bot.py --test-message  # send one Telegram message and exit
  python mr70/study_bot.py --once          # one cycle, then exit
"""
import argparse
import json
import os
import pathlib
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from mr70.config import MR70Config                      # noqa: E402
from mr70.indicators import build_features              # noqa: E402
from mr70.paper_live import fetch_with_retry            # noqa: E402
from mr70.signals import v3_vwap_climax                 # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
STATE = HERE / "study_state.json"
CSV = HERE / "study_signals.csv"

SYMBOLS = ["ETH/USDT:USDT", "BNB/USDT:USDT", "SOL/USDT:USDT", "DOGE/USDT:USDT"]

BAR = pd.Timedelta(minutes=15)
WAKE_DELAY_S = 20                 # let the exchange settle the closed candle
FETCH_DAYS = 5                    # comfortably more than the 96-bar windows need
STALE_BARS = 2                    # older than this: log as MISSED, do not alert
STRICT_FILL_ATR = 0.05            # "traded through" allowance
ROUND_TRIP_COST = 0.001           # 0.10% of price, as the spreadsheet assumes
UAE = pd.Timedelta(hours=4)

COLUMNS = ["#", "date_utc", "time_utc", "symbol", "side", "signal_entry_price",
           "bot_stop", "bot_target", "taken", "reason_skipped",
           "paper_fill_traded_through", "my_exit_price", "my_exit_time",
           "my_exit_reason", "bot_rule_exit_price", "bot_exit_reason",
           "touch_fill", "rule_R"]


SETUP_HINT = (
    "Set them, then open a NEW PowerShell window (they are read at startup):\n"
    '  [Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "123456:AA...", "User")\n'
    '  [Environment]::SetEnvironmentVariable("TELEGRAM_CHAT_ID",   "987654321",   "User")')


def telegram_send(text: str):
    """Send one message. Returns (ok, reason).

    Reports failure honestly rather than swallowing it: an unset variable, a
    network error and a Telegram API rejection are three different problems and
    a silent no-op looks like all three at once. Telegram answers every call
    with an `ok` field, so a 200 alone is not proof of delivery.
    """
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", token),
                                  ("TELEGRAM_CHAT_ID", chat)) if not v]
        return False, f"not configured: {', '.join(missing)} unset"
    try:
        import requests
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=10)
        body = r.json()
        if not body.get("ok"):
            return False, f"Telegram rejected it: {body.get('description', r.text[:120])}"
        return True, ""
    except Exception as e:                                       # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


_warned = {"telegram": False}


def notify(text: str):
    """Print, then send. A send failure is logged loudly once, then quietly."""
    print(text, flush=True)
    ok, why = telegram_send(text)
    if not ok and not _warned["telegram"]:
        print(f"  !! Telegram not delivering ({why}). Alerts are console-only.\n"
              f"{SETUP_HINT}", flush=True)
        _warned["telegram"] = True


def both_times(ts: pd.Timestamp) -> str:
    return f"{ts:%Y-%m-%d %H:%M} UTC ({ts + UAE:%H:%M} UAE)"


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"sent": {}, "next_number": 1, "open": {}, "watermark": {},
            "last_heartbeat": None, "last_weekly": None}


def save_state(st: dict):
    STATE.write_text(json.dumps(st, indent=2, default=str))


def read_csv() -> pd.DataFrame:
    if CSV.exists():
        return pd.read_csv(CSV, dtype=str).fillna("")
    return pd.DataFrame(columns=COLUMNS)


def write_csv(df: pd.DataFrame):
    df.reindex(columns=COLUMNS).to_csv(CSV, index=False)


def plan(f, i: int, d: int, cfg) -> dict:
    """The trade plan attached to a signal, exactly as backtested."""
    entry = float(f["close"].to_numpy()[i])
    atr = float(f["atr"].to_numpy()[i])
    return {"entry": entry, "atr": atr,
            "stop": entry - d * cfg.sl_atr * atr,
            "target": entry + d * cfg.tp_atr * atr,
            "risk_frac": abs(cfg.sl_atr * atr) / entry}


def signal_id(symbol: str, close_time: pd.Timestamp, d: int) -> str:
    return f"{symbol.split('/')[0]}-{close_time:%Y%m%d%H%M}-{'L' if d == 1 else 'S'}"


def resolve_from_bars(bars: pd.DataFrame, p: dict, d: int, cfg):
    """What the rule would have done, mirroring mr70.edge_gate.resolve().

    `bars` are the closed candles AFTER the signal candle. Returns a dict that is
    complete only once the trade has resolved; until then `done` is False so the
    caller keeps watching.
    """
    h, l = bars["high"].to_numpy(), bars["low"].to_numpy()
    entry, stop, target = p["entry"], p["stop"], p["target"]
    trig = entry - d * STRICT_FILL_ATR * p["atr"]

    fill = None
    touch = strict = False
    for j in range(min(cfg.entry_valid_bars, len(bars))):
        if (l[j] <= entry) if d == 1 else (h[j] >= entry):
            touch = True
            fill = j if fill is None else fill
        if (l[j] <= trig) if d == 1 else (h[j] >= trig):
            strict = True
    if fill is None:
        if len(bars) >= cfg.entry_valid_bars:
            return {"done": True, "touch": False, "strict": False,
                    "exit_reason": "UNFILLED", "exit_price": "", "R": ""}
        return {"done": False, "touch": touch, "strict": strict}

    for j in range(fill, min(fill + cfg.max_bars + 1, len(bars))):
        hit_sl = (l[j] <= stop) if d == 1 else (h[j] >= stop)
        hit_tp = (h[j] >= target) if d == 1 else (l[j] <= target)
        if hit_sl:                                   # stop wins same-bar ties
            return _out(bars.index[j], stop, "SL", entry, stop, d, touch, strict, p)
        if hit_tp:
            return _out(bars.index[j], target, "TP", entry, stop, d, touch, strict, p)
    if len(bars) >= fill + cfg.max_bars + 1:
        j = fill + cfg.max_bars
        return _out(bars.index[j], float(bars["close"].to_numpy()[j]), "TIME",
                    entry, stop, d, touch, strict, p)
    return {"done": False, "touch": touch, "strict": strict}


def _out(when, price, reason, entry, stop, d, touch, strict, p):
    r = (price - entry) * d / abs(entry - stop) - ROUND_TRIP_COST / p["risk_frac"]
    return {"done": True, "touch": touch, "strict": strict, "exit_reason": reason,
            "exit_price": round(price, 8), "exit_time": when, "R": round(r, 4)}


def alert_new(n: int, symbol: str, d: int, close_time, p: dict) -> str:
    side = "LONG" if d == 1 else "SHORT"
    valid = close_time + STALE_BARS * BAR
    tstop = close_time + MR70Config().max_bars * BAR
    return (f"PAPER STUDY -- MR-70 V3 (tested below break-even)\n"
            f"#{n}  {symbol.split('/')[0]}  {side}\n"
            f"Signal close: {both_times(close_time)}\n"
            f"Entry (limit): {p['entry']:,.4f}  -- valid until {valid:%H:%M} UTC\n"
            f"Stop: {p['stop']:,.4f}   Target: {p['target']:,.4f}\n"
            f"Risk: {p['risk_frac']:.2%} of price   "
            f"Time stop: {tstop:%H:%M} UTC ({MR70Config().max_bars // 4}h)\n"
            f"Log it in the sheet: take or skip, and why.")


def alert_done(n: int, symbol: str, d: int, res: dict) -> str:
    side = "LONG" if d == 1 else "SHORT"
    if res["exit_reason"] == "UNFILLED":
        return (f"#{n} {symbol.split('/')[0]} {side} -- rule outcome: NEVER FILLED\n"
                f"Filled (touch): no   Filled (traded through): no")
    name = {"TP": "TARGET hit", "SL": "STOP hit", "TIME": "TIME STOP"}[res["exit_reason"]]
    return (f"#{n} {symbol.split('/')[0]} {side} -- rule outcome: {name} at "
            f"{res['exit_time']:%H:%M} UTC\n"
            f"Filled (touch): {'yes' if res['touch'] else 'no'}   "
            f"Filled (traded through): {'yes' if res['strict'] else 'no'}\n"
            f"Rule result: {res['R']:+.2f} R")


def upsert(df: pd.DataFrame, number: str, fields: dict) -> pd.DataFrame:
    """Update the row for this signal number, preserving the owner's own columns."""
    mask = df["#"].astype(str) == str(number)
    if mask.any():
        for k, v in fields.items():
            df.loc[mask, k] = v
        return df
    row = {c: "" for c in COLUMNS}
    row["#"] = str(number)
    row.update(fields)
    return pd.concat([df, pd.DataFrame([row])], ignore_index=True)


def cycle(cfg: MR70Config, st: dict, now=None, alert=True):
    """One pass: look for new signals, then advance every open trade."""
    df = read_csv()
    now = pd.Timestamp.now(tz="UTC") if now is None else now

    for symbol in SYMBOLS:
        try:
            raw = fetch_with_retry(symbol, FETCH_DAYS, cfg)
        except Exception as e:                                   # noqa: BLE001
            print(f"  {symbol}: fetch failed ({type(e).__name__}), skipping", flush=True)
            continue
        if raw is None or raw.empty:
            continue
        f = build_features(raw, cfg)
        ct = f["close_time"]
        mark = pd.Timestamp(st["watermark"].get(symbol)) if st["watermark"].get(symbol) else None

        for i, d in v3_vwap_climax(f, cfg):
            close_time = pd.Timestamp(ct.iloc[i])
            if mark is not None and close_time <= mark:
                continue
            if close_time > now:
                # The candle has not closed yet. data.fetch_ohlcv already drops a
                # forming bar, but a clock skew or a cached frame could slip one
                # through, and a signal from an unclosed candle is lookahead.
                continue
            sid = signal_id(symbol, close_time, d)
            if sid in st["sent"]:
                continue
            p = plan(f, i, d, cfg)
            stale = (now - close_time) > STALE_BARS * BAR
            n = st["next_number"]
            st["next_number"] += 1
            st["sent"][sid] = {"n": n, "at": str(close_time)}
            df = upsert(df, n, {
                "date_utc": f"{close_time:%Y-%m-%d}", "time_utc": f"{close_time:%H:%M}",
                "symbol": symbol.split("/")[0], "side": "LONG" if d == 1 else "SHORT",
                "signal_entry_price": f"{p['entry']:.8f}",
                "bot_stop": f"{p['stop']:.8f}", "bot_target": f"{p['target']:.8f}",
                "bot_exit_reason": "MISSED" if stale else "",
            })
            if stale:
                print(f"  MISSED (stale) {sid}", flush=True)
            else:
                st["open"][sid] = {"n": n, "symbol": symbol, "dir": d,
                                   "close_time": str(close_time), **{
                                       k: float(v) for k, v in p.items()}}
                if alert:
                    notify(alert_new(n, symbol, d, close_time, p))
                print(f"  SIGNAL #{n} {sid}", flush=True)

        st["watermark"][symbol] = str(ct.iloc[-1])

        # advance the open trades on this symbol
        for sid, rec in list(st["open"].items()):
            if rec["symbol"] != symbol:
                continue
            after = raw[raw.index > pd.Timestamp(rec["close_time"]) - BAR]
            if after.empty:
                continue
            p = {k: rec[k] for k in ("entry", "atr", "stop", "target", "risk_frac")}
            res = resolve_from_bars(after, p, rec["dir"], cfg)
            if not res["done"]:
                continue
            df = upsert(df, rec["n"], {
                "paper_fill_traded_through": "Y" if res["strict"] else "N",
                "touch_fill": "Y" if res["touch"] else "N",
                "bot_rule_exit_price": res["exit_price"],
                "bot_exit_reason": res["exit_reason"], "rule_R": res["R"]})
            if alert:
                notify(alert_done(rec["n"], symbol, rec["dir"], res))
            print(f"  RESOLVED #{rec['n']} {sid} {res['exit_reason']}", flush=True)
            del st["open"][sid]

    write_csv(df)
    return df


def heartbeat_and_summary(st: dict, now, alert=True):
    """Daily 'still alive' and the Sunday weekly summary."""
    df = read_csv()
    day = now.normalize()
    if now.hour >= 6 and st.get("last_heartbeat") != str(day):
        since = now - pd.Timedelta(days=1)
        recent = [v for v in st["sent"].values() if pd.Timestamp(v["at"]) >= since]
        if alert:
            notify(f"Bot alive -- {len(recent)} signals in last 24h")
        st["last_heartbeat"] = str(day)

    week = f"{now.isocalendar().year}-{now.isocalendar().week}"
    if now.weekday() == 6 and now.hour >= 18 and st.get("last_weekly") != week:
        done = df[df["rule_R"].astype(str) != ""] if len(df) else df
        n_week = sum(1 for v in st["sent"].values()
                     if pd.Timestamp(v["at"]) >= now - pd.Timedelta(days=7))
        msg = [f"Weekly summary -- {n_week} signals this week, {len(st['sent'])} total"]
        if len(done):
            r = done["rule_R"].astype(float)
            touch = (done["touch_fill"] == "Y").mean()
            strict = (done["paper_fill_traded_through"] == "Y").mean()
            msg.append(f"Fill rate: touch {touch:.0%}, traded-through {strict:.0%}")
            msg.append(f"Rule win rate {(r > 0).mean():.0%}, average {r.mean():+.2f} R "
                       f"on {len(r)} resolved")
        msg.append("Fewer than 50 signals: treat these numbers as noise."
                   if len(done) < 50 else "Sample is past 50; still small.")
        if alert:
            notify("\n".join(msg))
        st["last_weekly"] = week


def sleep_to_next_bar():
    now = pd.Timestamp.now(tz="UTC")
    nxt = now.ceil("15min") + pd.Timedelta(seconds=WAKE_DELAY_S)
    if nxt <= now:
        nxt += BAR
    time.sleep(max(1.0, (nxt - now).total_seconds()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--test-message", action="store_true")
    args = ap.parse_args()
    cfg = MR70Config()

    if args.test_message:
        ok, why = telegram_send(
            "MR-70 V3 paper study bot: test message. Signals only, no orders.")
        if ok:
            print("Telegram accepted the message -- check your chat.")
            return
        print(f"NOT SENT -- {why}\n{SETUP_HINT}")
        sys.exit(1)

    ok, why = telegram_send("MR-70 V3 paper study bot starting. Signals only.")
    if not ok:
        print(f"WARNING: Telegram is not delivering ({why}).\n"
              f"The bot will run and write the CSV, but you will get no alerts.\n"
              f"{SETUP_HINT}\n", flush=True)
        _warned["telegram"] = True

    st = load_state()
    print(f"watching {', '.join(s.split('/')[0] for s in SYMBOLS)} on 15m; "
          f"CSV -> {CSV}", flush=True)
    while True:
        try:
            now = pd.Timestamp.now(tz="UTC")
            cycle(cfg, st, now)
            heartbeat_and_summary(st, now)
            save_state(st)
        except KeyboardInterrupt:
            print("stopped by user")
            save_state(st)
            return
        except Exception as e:                                   # noqa: BLE001
            # One bad cycle must never end the run: the point of the bot is to be
            # there when a signal fires, and a Wi-Fi drop is not a reason to stop.
            print(f"cycle error ({type(e).__name__}): {e}", flush=True)
        if args.once:
            return
        sleep_to_next_bar()


if __name__ == "__main__":
    main()
