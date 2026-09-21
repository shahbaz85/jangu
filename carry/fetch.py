"""Fetching and caching for the carry study: spot candles, perp candles, funding.

Funding intervals are NOT assumed to be 8 hours. Binance has changed the interval
on some symbols, so the actual spacing is measured from the timestamps and every
rate is normalised to a per-8-hour figure only where the spec asks for one.
"""
import pathlib
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import fetch_ohlcv, load_csv, save_csv        # noqa: E402

CACHE = pathlib.Path("carry_cache")


def with_retry(fn, *a, tries: int = 4, **kw):
    """Retry a fetch on transient network errors, backing off 2s, 4s, 8s.

    A RequestTimeout is not a data check failure -- losing a symbol to one would
    report an exchange listing gap that does not exist.
    """
    for attempt in range(tries):
        try:
            return fn(*a, **kw)
        except Exception as e:                        # noqa: BLE001
            transient = any(k in type(e).__name__ for k in
                            ("Timeout", "Network", "DDoSProtection", "ExchangeNotAvailable"))
            if not transient or attempt == tries - 1:
                raise
            wait = 2 ** (attempt + 1)
            print(f"    {type(e).__name__}, retrying in {wait}s "
                  f"({attempt + 1}/{tries - 1})...", flush=True)
            time.sleep(wait)


def _cache(name: str) -> pathlib.Path:
    CACHE.mkdir(exist_ok=True)
    return CACHE / name


def fetch_funding(symbol_perp: str, days: int, exchange_id: str) -> pd.DataFrame:
    """Historical funding payments with their real timestamps."""
    import ccxt
    ex = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    since = ex.milliseconds() - days * 86_400_000
    rows = []
    while True:
        batch = ex.fetch_funding_rate_history(symbol_perp, since=since, limit=1000)
        if not batch:
            break
        rows += batch
        nxt = batch[-1]["timestamp"] + 1
        if nxt <= since or nxt >= ex.milliseconds():
            break
        since = nxt
        time.sleep(ex.rateLimit / 1000)
    if not rows:
        return pd.DataFrame(columns=["rate"])
    df = pd.DataFrame([{"ts": r["timestamp"], "rate": float(r["fundingRate"])} for r in rows])
    df = df.drop_duplicates("ts").sort_values("ts")
    df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
    return df


def funding(symbol: str, cfg) -> pd.DataFrame:
    path = _cache(f"{symbol}_funding.csv")
    if path.exists():
        df = pd.read_csv(path)
        df.index = pd.to_datetime(df.pop(df.columns[0]), utc=True)
        return df[["rate"]].astype(float).sort_index()
    print(f"  fetching {symbol} funding...", flush=True)
    df = with_retry(fetch_funding, f"{symbol}/USDT:USDT", cfg.days, cfg.perp_exchange)
    df.to_csv(path, index_label="time")
    return df


def candles(symbol: str, cfg, leg: str) -> pd.DataFrame:
    """leg: 'spot' or 'perp'. 1H candles, cached."""
    path = _cache(f"{symbol}_{leg}_1h.csv")
    if path.exists():
        return load_csv(path)
    market = f"{symbol}/USDT" if leg == "spot" else f"{symbol}/USDT:USDT"
    ex = cfg.spot_exchange if leg == "spot" else cfg.perp_exchange
    print(f"  fetching {symbol} {leg} 1h...", flush=True)
    df = with_retry(fetch_ohlcv, market, "1h", cfg.days, ex)
    save_csv(df, path)
    return df


def interval_hours(fund: pd.DataFrame) -> pd.Series:
    """Hours between consecutive funding payments, as a per-payment series.

    The first payment has no predecessor, so it inherits the modal interval
    rather than being dropped -- dropping it would silently shorten the history.
    """
    gaps = fund.index.to_series().diff().dt.total_seconds() / 3600.0
    modal = gaps.dropna().round().mode()
    fill = float(modal.iloc[0]) if len(modal) else 8.0
    return gaps.fillna(fill)


def align_to_funding(fund: pd.DataFrame, spot: pd.DataFrame, perp: pd.DataFrame):
    """Attach the spot and perp close at or before each funding timestamp.

    Backward alignment only: a funding payment is settled using prices already
    printed, never a later candle.
    """
    out = fund.copy()
    for name, df in (("spot", spot), ("perp", perp)):
        s = df["close"].reindex(df.index.union(fund.index)).sort_index().ffill()
        out[name] = s.reindex(fund.index).to_numpy()
    return out.dropna()
