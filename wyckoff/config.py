"""Wyckoff probe parameters.

These are starting values, not tuned ones, and the probe that reads them counts
signals rather than scoring them -- so there is nothing here to tune toward.
Any strategy built on this must fix its thresholds in a spec before results are
seen, as with every other experiment in this repository.
"""
from dataclasses import dataclass, field


@dataclass
class WyckoffConfig:
    exchange_id: str = "binance"
    days: int = 730
    symbols: tuple = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "DOGE/USDT:USDT")
    timeframes: tuple = ("15min", "1h", "4h")

    atr_len: int = 14
    fractal_n: int = 3

    # --- the range that a spring or upthrust must penetrate
    range_lookback: int = 40          # bars forming the range
    range_max_width_atr: float = 6.0  # a range wider than this is a trend, not a range

    # --- the sequence
    sos_body_frac: float = 0.60
    sos_body_atr: float = 1.2
    spring_to_sos_bars: int = 20
    sos_to_lps_bars: int = 20
    lps_tag_tol: float = 0.002        # how close a pullback must come to the broken level

    # --- the SMC leg, for the overlap measurement
    smc_sweep_to_bos_bars: int = 8
    coincide_bars: int = 8            # how close in time two events count as the same event
