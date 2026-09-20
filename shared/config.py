"""All Strategy A / Strategy B numbers, pre-registered in AB_STRATEGY_SPEC.md.

Nothing here is tuned. The spec forbids optimisation in this round.
"""
from dataclasses import dataclass, field


@dataclass
class ABConfig:
    # --- timeframes ---
    base_tf: str = "15min"
    htf_30m: str = "30min"
    htf_1h: str = "1h"
    htf_4h: str = "4h"

    atr_len: int = 14
    fractal_n: int = 3            # 15m swings (ABCD legs, 15m structure)
    fractal_n_htf: int = 2        # 30m / 4H swings

    # --- Strategy A: trend ---
    ema_fast: int = 100
    ema_slow: int = 200
    ema_slope_bars: int = 10      # "rising" = above its value 10 bars ago
    cross_lookback: int = 20      # variant A2 only

    # --- Strategy A: momentum ---
    rsi_len: int = 14
    stoch_len: int = 14
    stoch_k: int = 3
    stoch_d: int = 3
    stoch_low: float = 20.0
    stoch_high: float = 80.0
    stoch_cross_bars: int = 5

    # --- Strategy A: volume ---
    vol_avg_bars: int = 15
    vol_mult: float = 1.2
    vol_trend_bars: int = 5

    # --- Strategy A: ABCD ---
    ab_min_atr: float = 1.5       # B - A must span at least this many ATR
    bc_retrace_lo: float = 0.50
    bc_retrace_hi: float = 0.786
    abcd_max_bars: int = 20       # trigger must come within this many bars of C

    # --- Strategy B: cascade ---
    impulse_body_atr: float = 1.5   # 1H impulse defining a zone
    zone_to_sweep_bars_30m: int = 12
    sweep_to_break_bars_15m: int = 8
    break_body_frac: float = 0.60
    break_body_atr: float = 1.2

    # --- entries / exits ---
    entry_valid_bars_a: int = 2
    entry_valid_bars_b: int = 8
    stop_lookback: int = 10         # Strategy A stop: extreme of last 10 bars
    stop_buffer_atr_a: float = 0.2
    stop_buffer_atr_b: float = 0.2
    max_stop_atr: float = 2.5       # reject: stop_too_wide
    min_stop_cost_mult: float = 5.0  # reject: stop_too_tight_vs_fees
    tp1_r: float = 1.0
    tp1_close_frac: float = 0.5
    tp2_r: float = 2.0              # Strategy A only
    trail_atr_b: float = 2.5        # Strategy B runner
    max_bars_a: int = 48
    max_bars_b: int = 96

    # --- costs ---
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    slippage: float = 0.0002        # BTC / ETH
    slippage_high: float = 0.0004   # SOL / DOGE
    high_slip_symbols: tuple = ("SOL/USDT:USDT", "DOGE/USDT:USDT")
    funding_per_8h: float = 0.0001

    # --- portfolio risk ---
    risk_pct: float = 0.75
    max_open_risk_pct: float = 1.5
    daily_loss_limit_r: float = 2.0
    max_consec_losses: int = 4
    pause_bars_after_streak: int = 96
    max_leverage: int = 10
    liq_buffer_mult: float = 2.5

    # --- gate ---
    gate_min_signals: int = 150
    gate_symbol_majority: int = 3
    wilson_z: float = 1.96          # 95% two-sided == 97.5% one-sided, for two hypotheses

    # --- universe ---
    symbols: list = field(default_factory=lambda: [
        "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "DOGE/USDT:USDT"])
    exchange_id: str = "binanceusdm"
    days: int = 730
    start_equity: float = 10_000.0
    seed: int = 11

    def slip_for(self, symbol: str) -> float:
        return self.slippage_high if symbol in self.high_slip_symbols else self.slippage

    def round_trip_cost(self, symbol: str) -> float:
        return self.maker_fee + self.taker_fee + self.slip_for(symbol)
