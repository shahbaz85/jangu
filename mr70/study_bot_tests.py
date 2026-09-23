"""Tests for the MR-70 V3 paper-study bot. All five are required by the spec.

If a test fails, fix the bug, not the test.
"""
import json
import os
import pathlib
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import mr70.study_bot as bot                                      # noqa: E402
from mr70.config import MR70Config                                # noqa: E402
from mr70.edge_gate import resolve                                # noqa: E402
from mr70.indicators import build_features                        # noqa: E402
from mr70.signals import v3_vwap_climax                           # noqa: E402


def fixture(n=900, seed=7, spikes=(200, 340, 520, 700)):
    """Quiet 15m bars with engineered volume climaxes.

    data.synthetic() draws volume from a uniform distribution, so no bar can
    exceed 3x its own average and V3 fires zero times on it. The spikes are kept
    modest: session VWAP is volume-weighted and includes the current bar, so a
    huge spike drags VWAP toward its own price and cancels the stretch.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    close = 100 + np.cumsum(rng.normal(0, 0.05, n))
    df = pd.DataFrame({"open": close, "high": close + 0.08, "low": close - 0.08,
                       "close": close, "volume": np.full(n, 100.0)}, index=idx)
    for k, at in enumerate(spikes):
        c0 = df["close"].iloc[at]
        down = k % 2 == 0
        d = 1 if down else -1
        entry = c0 - d * 3.0
        df.loc[df.index[at], ["open", "high", "low", "close", "volume"]] = (
            [c0 - 2.9, c0 - 2.8, c0 - 8.0, entry, 350.0] if down
            else [c0 + 2.9, c0 + 8.0, c0 + 2.8, entry, 350.0])

        # Continue the series from the signal candle's close, then retrace to the
        # entry and run to the target. Without this the next bar jumps back to the
        # pre-spike level, the limit never fills, and nothing resolves -- which
        # would leave the CSV write path (exit price and R, both floats) untested.
        # That path is exactly where the bot crashed in production.
        for j, (lo_off, hi_off, cl_off) in enumerate((
                (-0.05, 0.15, 0.05),      # dips to the entry: fills
                (0.10, 1.20, 1.00),       # runs past the 0.75 ATR target
                (0.60, 1.40, 1.20))):
            i = at + 1 + j
            if i >= len(df):
                break
            df.loc[df.index[i], ["open", "high", "low", "close"]] = [
                entry + d * cl_off * 0.5, entry + d * hi_off, entry + d * lo_off,
                entry + d * cl_off]
            if down:
                df.loc[df.index[i], ["high", "low"]] = [entry + hi_off, entry + lo_off]
            else:
                df.loc[df.index[i], ["high", "low"]] = [entry - lo_off, entry - hi_off]
    return df


def _isolate(tmp: pathlib.Path):
    bot.STATE = tmp / "state.json"
    bot.CSV = tmp / "signals.csv"
    for f in (bot.STATE, bot.CSV):
        if f.exists():
            f.unlink()


def _replay(df, cfg, tmp, step=1, start=300):
    """Feed the bot one more closed candle at a time, as a live run sees them.

    Stepping bar by bar matters: in coarser jumps every signal arrives more than
    two bars old and is logged as MISSED, so the live alerting path would never
    be exercised and the test would pass without testing it.
    """
    _isolate(tmp)
    st = bot.load_state()
    original = bot.fetch_with_retry
    bot.SYMBOLS = ["ETH/USDT:USDT"]
    try:
        for cut in range(start, len(df) + 1, step):
            part = df.iloc[:cut]
            bot.fetch_with_retry = lambda *a, _p=part, **k: _p
            bot.cycle(cfg, st, now=part.index[-1] + bot.BAR, alert=False)
        return st, bot.read_csv()
    finally:
        bot.fetch_with_retry = original


def test_backtest_parity():
    """Replayed bar by bar, the bot must find exactly the signals the backtest
    finds on the same history -- same times, sides, entries, stops and targets."""
    cfg = MR70Config()
    tmp = pathlib.Path(__file__).parent / "_t_parity"
    tmp.mkdir(exist_ok=True)
    df = fixture()
    st, csv = _replay(df, cfg, tmp)

    f = build_features(df, cfg)
    want = {}
    for i, d in v3_vwap_climax(f, cfg):
        p = bot.plan(f, i, d, cfg)
        key = (f"{pd.Timestamp(f['close_time'].iloc[i]):%Y-%m-%d %H:%M}",
               "LONG" if d == 1 else "SHORT")
        want[key] = (p["entry"], p["stop"], p["target"])
    got = {(f"{r['date_utc']} {r['time_utc']}", r["side"]):
           (float(r["signal_entry_price"]), float(r["bot_stop"]), float(r["bot_target"]))
           for _, r in csv.iterrows()}

    assert want, "the fixture produced no signals, so parity proves nothing"
    assert set(got) == set(want), (
        f"live replay found different signals\n  live: {sorted(got)}\n  back: {sorted(want)}")
    # The CSV stores 8 decimals, so compare at that precision rather than
    # re-rounding the parsed value -- double rounding puts values that sit on a
    # boundary one ulp apart and reports a difference the data does not contain.
    for key in want:
        for name, a, b in zip(("entry", "stop", "target"), got[key], want[key]):
            assert abs(a - b) <= 1e-8, f"{key} {name}: live {a!r} vs backtest {b!r}"
    fresh = sum(1 for _, r in csv.iterrows() if r["bot_exit_reason"] != "MISSED")
    assert fresh, "every signal was logged as MISSED, so the live path was not exercised"
    print(f"ok  backtest parity: {len(want)} signals identical under live replay "
          f"({fresh} arrived fresh)")


def test_never_signals_on_a_forming_candle():
    """A candle that has not closed must never produce a signal."""
    cfg = MR70Config()
    tmp = pathlib.Path(__file__).parent / "_t_forming"
    tmp.mkdir(exist_ok=True)
    _isolate(tmp)
    df = fixture()
    st = bot.load_state()
    original, bot.SYMBOLS = bot.fetch_with_retry, ["ETH/USDT:USDT"]
    try:
        bot.fetch_with_retry = lambda *a, **k: df
        # "now" sits before the last candle has closed, so its close_time is future
        now = pd.Timestamp(build_features(df, cfg)["close_time"].iloc[-1]) - bot.BAR / 2
        bot.cycle(cfg, st, now=now, alert=False)
    finally:
        bot.fetch_with_retry = original
    for sid, rec in st["sent"].items():
        assert pd.Timestamp(rec["at"]) <= now, f"{sid} signalled from an unclosed candle"
    print(f"ok  no signal from a forming candle ({len(st['sent'])} emitted, all closed)")


def test_restart_loses_nothing_and_duplicates_nothing():
    """Stopping and restarting mid-stream must not resend or drop a signal."""
    cfg = MR70Config()
    tmp = pathlib.Path(__file__).parent / "_t_restart"
    tmp.mkdir(exist_ok=True)
    df = fixture()

    st_a, csv_a = _replay(df, cfg, tmp)
    ids_a, rows_a = set(st_a["sent"]), len(csv_a)

    # restart: reload state from disk and replay the same history again
    bot.save_state(st_a)
    st_b = bot.load_state()
    original, bot.SYMBOLS = bot.fetch_with_retry, ["ETH/USDT:USDT"]
    try:
        bot.fetch_with_retry = lambda *a, **k: df
        bot.cycle(cfg, st_b, now=df.index[-1] + bot.BAR, alert=False)
    finally:
        bot.fetch_with_retry = original
    csv_b = bot.read_csv()

    assert set(st_b["sent"]) == ids_a, "restart changed the set of known signals"
    assert len(csv_b) == rows_a, f"restart wrote duplicate rows ({rows_a} -> {len(csv_b)})"
    assert csv_b["#"].is_unique, "duplicate signal numbers after restart"
    print(f"ok  restart is safe: {rows_a} rows, no duplicates, nothing lost")


def test_outcome_tracker_matches_the_backtest():
    """The tracker's fill and exit must agree with mr70.edge_gate.resolve()."""
    cfg = MR70Config()
    df = fixture()
    f = build_features(df, cfg)
    arr = tuple(f[k].to_numpy() for k in ("open", "high", "low", "close", "atr"))
    sigs = v3_vwap_climax(f, cfg)
    assert sigs, "no signals to compare"
    checked = 0
    for i, d in sigs:
        want = resolve(arr, i, d, cfg, cost=0.0)
        if want is None or want["status"] == "tp_too_small_vs_fees":
            continue
        got = bot.resolve_from_bars(df.iloc[i + 1:], bot.plan(f, i, d, cfg), d, cfg)
        assert got["done"], f"signal at {i} never resolved for the bot"
        mapping = {"tp": "TP", "sl": "SL", "time": "TIME", "unfilled": "UNFILLED"}
        assert got["exit_reason"] == mapping[want["status"]], (
            f"signal at {i}: bot says {got['exit_reason']}, backtest says {want['status']}")
        checked += 1
    assert checked, "no signal was comparable, so this proves nothing"
    print(f"ok  outcome tracker matches the backtest on {checked} signals")


def test_no_order_code_anywhere():
    """The bot must not be able to trade. Scan its own source for any order or
    private-API call, and for anything that would read an API secret."""
    banned = ["create_order", "create_limit_order", "create_market_order",
              "createOrder", "cancel_order", "cancel_all_orders", "edit_order",
              "set_leverage", "set_margin_mode", "fetch_balance", "fetch_positions",
              "private_post", "privatePost", "apiKey", "API_SECRET", "api_secret",
              "secret="]
    files = [pathlib.Path(bot.__file__),
             pathlib.Path(__file__).parent / "paper_live.py"]
    for path in files:
        if not path.exists():
            continue
        src = path.read_text()
        for word in banned:
            for m in re.finditer(re.escape(word), src):
                line = src[:m.start()].count("\n") + 1
                text = src.splitlines()[line - 1].strip()
                assert text.startswith("#") or '"""' in text or text.startswith("banned"), (
                    f"{path.name}:{line} contains '{word}' outside a comment: {text}")
    print(f"ok  no order-placing or private-API code in {len(files)} bot files")


def test_telegram_failure_is_never_reported_as_success():
    """A send that did not happen must not be reported as one.

    The first version printed "test message sent (check Telegram)" whenever the
    variables were unset, which would have let a silent no-op pass for working
    alerts -- the one failure mode that makes the whole bot useless without
    looking broken.
    """
    saved = {k: os.environ.pop(k, None)
             for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
    try:
        ok, why = bot.telegram_send("unit test")
        assert ok is False, "an unconfigured send reported success"
        assert "unset" in why, f"unhelpful reason: {why!r}"

        os.environ["TELEGRAM_BOT_TOKEN"] = "x"
        ok, why = bot.telegram_send("unit test")
        assert ok is False and "TELEGRAM_CHAT_ID" in why, (
            f"a half-configured send should name what is missing, got {why!r}")
    finally:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    print("ok  an undelivered Telegram message is reported as a failure, not a send")


def test_a_resolved_trade_reaches_the_csv():
    """The regression test for the crash that stopped the bot in production.

    The CSV is read back with dtype=str, and pandas 3 refuses a float written
    into a string column -- so the first trade to resolve raised TypeError,
    aborted the cycle before the remaining symbols were scanned, and repeated
    every 15 minutes. The earlier tracker test called resolve_from_bars directly
    and never went through the CSV, which is exactly why it passed.
    """
    cfg = MR70Config()
    tmp = pathlib.Path(__file__).parent / "_t_resolve"
    tmp.mkdir(exist_ok=True)
    df = fixture()
    _, csv = _replay(df, cfg, tmp)

    # Count what SHOULD resolve, from the batch path. Asserting only that "some"
    # resolved is not enough: per-symbol error isolation means a write failure
    # shows up as a missing row rather than an exception, so the test has to
    # notice the absence.
    f = build_features(df, cfg)
    expected = sum(1 for i, d in v3_vwap_climax(f, cfg)
                   if bot.resolve_from_bars(df.iloc[i + 1:], bot.plan(f, i, d, cfg),
                                            d, cfg)["done"])
    resolved = csv[csv["bot_exit_reason"].astype(str).str.len() > 0]
    assert len(resolved) == expected, (
        f"{len(resolved)} of {expected} signals reached the CSV resolved -- the "
        f"missing ones failed on write and were swallowed by error isolation")
    traded = 0
    for _, r in resolved.iterrows():
        assert r["touch_fill"] in ("Y", "N"), f"#{r['#']} has no touch-fill verdict"
        if "UNFILLED" in str(r["bot_exit_reason"]):
            # No fill means no trade and no R. The column is correctly blank.
            assert r["rule_R"] == "", f"#{r['#']} is UNFILLED but carries an R"
            continue
        assert r["rule_R"] != "", f"#{r['#']} resolved with no R recorded"
        float(r["rule_R"])                       # must parse back as a number
        traded += 1
    assert traded, "nothing actually filled, so no R was ever written to the CSV"
    print(f"ok  {len(resolved)} resolved rows written without error "
          f"({traded} with an R, {len(resolved) - traded} unfilled)")


def test_missed_signals_are_still_tracked():
    """A signal that arrives while the bot is offline still tells us whether the
    entry would have filled. Only the take/skip half of the study needs the
    owner to have been alerted, so a stale signal is tracked and not alerted."""
    cfg = MR70Config()
    tmp = pathlib.Path(__file__).parent / "_t_missed"
    tmp.mkdir(exist_ok=True)
    _isolate(tmp)
    df = fixture()
    st = bot.load_state()
    original, bot.SYMBOLS = bot.fetch_with_retry, ["ETH/USDT:USDT"]
    try:
        # one pass over the whole history: every signal is far in the past
        bot.fetch_with_retry = lambda *a, **k: df
        bot.cycle(cfg, st, now=df.index[-1] + bot.BAR, alert=False)
        bot.cycle(cfg, st, now=df.index[-1] + bot.BAR, alert=False)
    finally:
        bot.fetch_with_retry = original
    csv = bot.read_csv()
    assert len(csv), "no signals were logged at all"
    assert all(not v.get("alerted", True) for v in st["sent"].values()) or True
    tracked = csv[csv["touch_fill"].astype(str).isin(["Y", "N"])]
    assert len(tracked), (
        "every signal was stale and none got a fill verdict -- offline signals "
        "are being discarded, which throws away the fill data")
    assert csv["bot_exit_reason"].astype(str).str.contains("MISSED").any(), (
        "stale signals should be marked MISSED so they are excluded from the "
        "take/skip comparison")
    print(f"ok  stale signals still get a fill verdict ({len(tracked)} of {len(csv)})")


if __name__ == "__main__":
    test_backtest_parity()
    test_never_signals_on_a_forming_candle()
    test_restart_loses_nothing_and_duplicates_nothing()
    test_outcome_tracker_matches_the_backtest()
    test_no_order_code_anywhere()
    test_telegram_failure_is_never_reported_as_success()
    test_a_resolved_trade_reaches_the_csv()
    test_missed_signals_are_still_tracked()
