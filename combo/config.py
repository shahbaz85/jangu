"""Combined strategy parameters, pre-registered in FINAL_REPORT.md section 6.

Fixed before the LuxAlgo result is known, which is the whole point: it cannot be
shaped to fit a number nobody has seen yet. Nothing here may change afterwards.

Section 6 says "same costs as the other tests", which needs one decision made
explicit: entry is at the next candle's OPEN, so it is a market order and pays
taker plus slippage. A stop exit is also market. Only the target exit can rest as
a limit and pay maker. That makes this geometry more expensive than MR-70's
maker-limit entry, and the break-even rate below reflects it.
"""
from dataclasses import dataclass, field


@dataclass
class ComboConfig:
    exchange_id: str = "binanceusdm"
    days: int = 730
    base_tf: str = "15min"

    # Section 6 calls these the nine fresh coins. Note for the record: ETH, BNB
    # and SOL were three of the four MR-70 parameters were chosen on, so they are
    # fresh to this strategy but not to the project. Reported, not corrected --
    # changing the universe now would be exactly the tuning the spec forbids.
    symbols: tuple = ("ETH/USDT:USDT", "BNB/USDT:USDT", "SOL/USDT:USDT",
                      "XRP/USDT:USDT", "ADA/USDT:USDT", "LINK/USDT:USDT",
                      "LTC/USDT:USDT", "DOT/USDT:USDT", "TRX/USDT:USDT")
    reused_symbols: tuple = ("ETH/USDT:USDT", "BNB/USDT:USDT", "SOL/USDT:USDT")

    # --- core signal: LuxAlgo-style swing break of structure
    swing_length: int = 50

    # --- sweep (S)
    sweep_lookback: int = 20          # candles before the BOS candle

    # --- volume (V)
    vol_lookback: int = 20
    vol_mult: float = 1.5

    # --- trade
    atr_len: int = 14
    tp_atr: float = 1.5
    sl_atr: float = 1.5
    time_stop_bars: int = 32          # 8 hours of 15m candles

    # --- costs
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    slippage: float = 0.0002          # ETH
    slippage_high: float = 0.0004     # everything else
    low_slip_symbols: tuple = ("ETH/USDT:USDT",)

    # --- statistics
    # Three versions are tested, so section 6 requires 99% intervals rather than
    # 95%. That is a Bonferroni-style correction: three shots at a 5% threshold
    # would pass one in seven times on noise alone.
    alpha: float = 0.01
    power: float = 0.80
    block_days: int = 28
    placebo_shifts: tuple = (-3200, -1600, 1600, 3200)
    seed: int = 23

    variants: tuple = ("BOS+S", "BOS+V", "BOS+S+V")

    def slip_for(self, symbol: str) -> float:
        return self.slippage if symbol in self.low_slip_symbols else self.slippage_high

    def cost_win(self, symbol: str) -> float:
        """Market in, limit out at the target."""
        slip = self.slip_for(symbol)
        return (self.taker_fee + slip) + (self.maker_fee + slip)

    def cost_loss(self, symbol: str) -> float:
        """Market in, market out at the stop."""
        slip = self.slip_for(symbol)
        return 2 * (self.taker_fee + slip)

    def break_even(self, symbol: str, price: float, atr: float) -> float:
        """Cost-inclusive break-even hit rate for one trade, in ATR units."""
        if not (atr > 0 and price > 0):
            return float("nan")
        unit = price / atr
        cw, cl = self.cost_win(symbol) * unit, self.cost_loss(symbol) * unit
        return (self.sl_atr + cl) / (self.tp_atr + self.sl_atr + cl - cw)
