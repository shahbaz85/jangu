"""Tunables for the trend-following strategy: 4H EMA trend bias + 15m pullback entry.

Separate from config.py (the SMC strategy) so both can be kept and compared.
Every number here is a hypothesis, same as config.py -- validate before trusting it.
"""
from dataclasses import dataclass, field


@dataclass
class TrendConfig:
    base_tf: str = "15min"
    htf: str = "4h"                 # higher timeframe for the trend filter
    ema_fast_htf: int = 21
    ema_slow_htf: int = 55
    ema_pullback: int = 20          # 15m EMA the pullback reclaims
    atr_len: int = 14

    # trend must be at least this separated (in HTF ATR units) to trade at all;
    # at or above strong_trend_atr_sep it's graded A, otherwise B
    min_trend_atr_sep: float = 0.5
    strong_trend_atr_sep: float = 1.5

    entry_buffer_atr: float = 0.1   # limit entry placed this far behind the trigger close
    stop_atr_mult: float = 1.5
    min_rr: float = 2.0
    tp1_r: float = 1.0
    tp1_close_frac: float = 0.5
    move_sl_to_be_after_tp1: bool = True
    order_valid_bars: int = 6
    max_bars_in_trade: int = 96
    cancel_if_price_reaches: str = "tp2"

    atr_pct_window: int = 1920      # ~20 days of 15m bars
    atr_pct_low: float = 0.15
    atr_pct_high: float = 0.97

    grade_a_sep: float = 1.5        # kept for readability; mirrors strong_trend_atr_sep
    risk_pct_a: float = 1.0
    risk_pct_b: float = 0.5
    max_trades_per_day: int = 5
    daily_loss_limit_r: float = 3.0
    max_consec_losses: int = 5
    pause_bars_after_streak: int = 48
    max_leverage: int = 10
    liq_buffer_mult: float = 2.5

    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    slippage: float = 0.0002

    symbols: list = field(default_factory=lambda: ["BTC/USDT:USDT", "ETH/USDT:USDT"])
    exchange_id: str = "binanceusdm"
    account_equity: float = 1000.0
    correlated_groups: list = field(default_factory=lambda: [["BTC/USDT:USDT", "ETH/USDT:USDT"]])
