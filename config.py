"""All tunable parameters for the SMC 15m futures strategy.

Every number here is a hypothesis to be tested, not a known-good value.
Change them in one place, then re-run the backtest / walk-forward.
"""
from dataclasses import dataclass, field


@dataclass
class Config:
    # --- timeframes ---
    base_tf: str = "15min"          # execution timeframe (pandas offset)

    # --- structure definitions ---
    fractal_n_base: int = 3         # swing = highest/lowest of n bars each side (15m)
    fractal_n_htf: int = 2          # same for 1H / 4H
    atr_len: int = 14
    displacement_body_atr: float = 1.5   # single candle body >= X * ATR
    displacement_leg_atr: float = 2.0    # or 3 same-colour candles spanning >= X * ATR
    fvg_min_atr: float = 0.2             # ignore gaps smaller than X * ATR
    eq_level_tol_atr: float = 0.1        # equal highs/lows tolerance
    h1_range_bars: int = 48              # 1H dealing range = last 48 hours high/low
    h1_fvg_max_age: int = 48             # 1H FVG stays a POI for 48 hours max

    # --- sessions (UTC hours, [start, end)) ---
    killzones: tuple = ((7.0, 10.0), (12.5, 15.5))   # London, New York
    sweep_pre_kz_hours: float = 1.0      # a sweep may happen up to 1h before a kill zone
    asia_session: tuple = (0.0, 6.0)

    # --- setup timing ---
    choch_window_bars: int = 8      # structure break must follow the sweep within 8 bars (2h)
    order_valid_bars: int = 8       # limit order expires after 8 bars (2h)
    max_bars_in_trade: int = 96     # force exit after 24h

    # --- entry / exits ---
    entry_mode: str = "fvg_mid"     # "fvg_mid" or "ob_top"
    sl_buffer_atr: float = 0.1
    tp1_r: float = 2.0
    tp1_close_frac: float = 0.5
    move_sl_to_be_after_tp1: bool = True
    min_rr: float = 2.0             # minimum R:R to TP2
    max_sl_atr: float = 2.0         # reject stops wider than X * ATR
    cancel_if_price_reaches: str = "tp2"   # "tp2" (default) or "tp1" (stricter): cancel unfilled order

    # --- filters ---
    require_premium_discount: bool = True   # longs only in discount, shorts only in premium
    allow_counter_trend: bool = False       # trade against 4H bias?
    atr_pct_window: int = 96 * 20           # 20 days of 15m bars
    atr_pct_low: float = 0.20
    atr_pct_high: float = 0.95
    news_block_minutes: int = 30
    funding_extreme: float = 0.0005         # 0.05% / 8h: don't long above, don't short below minus

    # --- scoring ---
    grade_a: int = 8
    grade_b: int = 6

    # --- risk ---
    risk_pct_a: float = 1.0
    risk_pct_b: float = 0.5
    max_trades_per_day: int = 3
    daily_loss_limit_r: float = 2.0
    max_consec_losses: int = 4
    pause_bars_after_streak: int = 96        # backtest: pause 1 day after the streak
    max_leverage: int = 10
    liq_buffer_mult: float = 2.5             # liquidation must be >= 2.5x stop distance away

    # --- costs (Binance USDT-M defaults, check your VIP tier) ---
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    slippage: float = 0.0002                 # applied to stop / market exits

    # --- live ---
    symbols: list = field(default_factory=lambda: ["BTC/USDT:USDT", "ETH/USDT:USDT"])
    exchange_id: str = "binanceusdm"
    account_equity: float = 1000.0           # used to size positions in signals
    correlated_groups: list = field(default_factory=lambda: [["BTC/USDT:USDT", "ETH/USDT:USDT"]])
