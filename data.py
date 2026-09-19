"""Market data: fetch from the exchange with ccxt, cache to CSV, or generate synthetic test data."""
import time
import numpy as np
import pandas as pd


def fetch_ohlcv(symbol="BTC/USDT:USDT", timeframe="15m", days=730, exchange_id="binanceusdm", drop_open=True):
    import ccxt
    ex = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    tf_ms = ex.parse_timeframe(timeframe) * 1000
    since = ex.milliseconds() - days * 86_400_000
    rows = []
    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1500)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + tf_ms
        if len(batch) < 2 or since >= ex.milliseconds():
            break
        time.sleep(ex.rateLimit / 1000)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
    df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
    if drop_open and len(df) and df.index[-1] + pd.Timedelta(milliseconds=tf_ms) > pd.Timestamp.now(tz="UTC"):
        df = df.iloc[:-1]                     # never use the candle that is still forming
    return df.astype(float)


def save_csv(df, path):
    df.to_csv(path, index_label="time")


def load_csv(path):
    df = pd.read_csv(path)
    df.index = pd.to_datetime(df.pop(df.columns[0]), utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float).sort_index()


def synthetic(days=120, seed=7, start="2025-01-01"):
    """Random-walk candles with session volatility and regimes. For testing the plumbing only:
    it has no real edge, so do not read anything into its backtest results."""
    rng = np.random.default_rng(seed)
    n = days * 96
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    hour = idx.hour.to_numpy()
    sess = np.where((hour >= 7) & (hour < 16), 1.6, np.where(hour < 6, 0.7, 1.0))
    lv = np.zeros(n)
    for i in range(1, n):                      # mean-reverting log-volatility (vol clustering)
        lv[i] = 0.995 * lv[i - 1] + rng.normal(0, 0.05)
    vol = np.exp(lv) * 0.0022 * sess
    drift = np.repeat(rng.normal(0, 0.00012, n // 384 + 1), 384)[:n]
    ret = drift + vol * rng.standard_t(4, n) / 1.4
    close = 60000 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, 1, (2, n))) * vol * close * 0.6
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.uniform(100, 1000, n)}, index=idx)
