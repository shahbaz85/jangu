"""All MR-70 numbers in one place. Pre-registered in MR70_STRATEGY_SPEC.md --
do not tune anything here after seeing results except where the spec's
walk-forward step explicitly allows it (tp_atr, sl_atr, in training windows only).
"""
from dataclasses import dataclass, field


@dataclass
class MR70Config:
    # --- timeframes ---
    base_tf: str = "15min"
    htf_1h: str = "1h"
    htf_4h: str = "4h"

    # --- trade geometry (shared by every variant) ---
    tp_atr: float = 0.75
    sl_atr: float = 2.0
    max_bars: int = 16                # time stop: 4 hours
    entry_valid_bars: int = 2         # limit order at signal close, valid 2 bars
    cooldown_bars: int = 4            # after any exit, per symbol
    min_tp_cost_mult: float = 5.0     # reject if tp distance < 5x round-trip cost

    # --- indicator lengths ---
    atr_len: int = 14
    bb_len: int = 20
    bb_k: float = 2.0
    rsi_len: int = 14                 # V1
    rsi_fast: int = 2                 # V2
    adx_len: int = 14                 # V1, on 1H
    ema_htf_fast: int = 50            # 4H
    ema_htf_slow: int = 200           # 4H
    ema_base: int = 200               # 15m
    vwap_std_len: int = 96            # V3
    vol_avg_len: int = 96             # V3

    # --- variant thresholds ---
    adx_max: float = 25.0             # V1: 1H ADX below this = range
    rsi_low: float = 30.0             # V1
    rsi_high: float = 70.0            # V1
    rsi2_low: float = 5.0             # V2
    rsi2_high: float = 95.0           # V2
    vwap_k: float = 2.5               # V3
    vol_mult: float = 3.0             # V3
    wick_frac: float = 0.40           # V3

    # --- costs ---
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    slippage: float = 0.0002
    slippage_high: float = 0.0004     # SOL / DOGE
    high_slip_symbols: tuple = ("SOL/USDT:USDT", "DOGE/USDT:USDT")
    funding_per_8h: float = 0.0001    # 0.01% of notional per 8h held

    # --- risk / portfolio ---
    risk_pct: float = 0.5             # per trade, % of equity
    max_open_risk_pct: float = 1.5    # total across symbols (one correlation group)
    daily_loss_limit_r: float = 2.0
    max_consec_losses: int = 4
    pause_bars_after_streak: int = 96  # 1 day of 15m bars

    # --- universe ---
    symbols: list = field(default_factory=lambda: [
        "BNB/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "DOGE/USDT:USDT"])
    exchange_id: str = "binanceusdm"
    days: int = 730
    start_equity: float = 10_000.0

    # --- edge gate ---
    gate_min_lift_pp: float = 4.0     # signal hit rate must beat random by >= 4 pp
    random_samples_per_symbol: int = 3000
    seed: int = 7

    def slip_for(self, symbol: str) -> float:
        return self.slippage_high if symbol in self.high_slip_symbols else self.slippage

    def round_trip_cost(self, symbol: str) -> float:
        """Entry (maker) + worst-case exit (taker + slippage), as a fraction of price."""
        return self.maker_fee + self.taker_fee + self.slip_for(symbol)
