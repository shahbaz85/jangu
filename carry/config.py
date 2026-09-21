"""Funding-rate carry study parameters, pre-registered in CARRY_SPEC.md.

Nothing here is tuned and nothing may change after results are seen. The
benchmark B is deliberately left unset: the spec makes it an owner decision that
must be written down before any run, and a default would quietly become the
thing the study is judged against.
"""
from dataclasses import dataclass, field


@dataclass
class CarryConfig:
    # --- benchmark: MUST be set by the owner before Stage 1 (spec section 3)
    benchmark_annual_pct: float | None = None

    # --- universe
    symbols: tuple = ("BTC", "ETH", "BNB", "SOL", "XRP", "DOGE",
                      "ADA", "AVAX", "LINK", "LTC", "DOT", "TRX")
    spot_exchange: str = "binance"
    perp_exchange: str = "binanceusdm"
    days: int = 365 * 5              # fetch 5y; the spec requires >= 4y per symbol
    min_years: float = 4.0
    must_include_year: int = 2022

    # --- costs, per leg per side (spec section 5)
    spot_fee: float = 0.0010
    perp_fee: float = 0.0005
    slippage: float = 0.0002         # BTC / ETH
    slippage_high: float = 0.0004    # everything else
    low_slip_symbols: tuple = ("BTC", "ETH")

    # --- capital and margin (spec section 6)
    leverage: float = 2.0
    leverage_sensitivity: tuple = (1.0, 3.0)
    rebalance_days: int = 7
    liquidation_rise: float = 0.45   # +45% from entry wipes the L=2 buffer
    liquidation_penalty: float = 0.01

    # --- C1 thresholds (spec section 7)
    c1_lookback: int = 9
    c1_enter_per_8h: float = 0.0002
    c1_exit_per_8h: float = 0.00005
    c1_negative_run: int = 3
    c1_min_hold_days: int = 3

    # --- C2 (spec section 7)
    c2_lookback: int = 21
    c2_top_n: int = 4
    c2_rebalance_days: int = 7
    c2_cost_recovery_days: int = 21

    # --- nulls (spec section 8)
    placebo_draws: int = 200
    placebo_min_shift_days: int = 60

    # --- Stage 0 power (spec section 9)
    block_months: int = 3
    power: float = 0.80
    underpowered_above_annual: float = 0.03
    seed: int = 11

    def slip_for(self, symbol: str) -> float:
        return self.slippage if symbol in self.low_slip_symbols else self.slippage_high

    def round_trip_cost(self, symbol: str) -> float:
        """Enter both legs and exit both legs, as a fraction of notional."""
        slip = self.slip_for(symbol)
        return 2 * (self.spot_fee + slip) + 2 * (self.perp_fee + slip)

    def capital_multiple(self, leverage: float | None = None) -> float:
        """Capital tied up per unit of notional: spot N plus perp margin N/L."""
        return 1.0 + 1.0 / (leverage if leverage is not None else self.leverage)

    def require_benchmark(self) -> float:
        if self.benchmark_annual_pct is None:
            raise ValueError(
                "Benchmark B is not set. CARRY_SPEC.md section 3 makes it an owner "
                "decision that must be recorded before any result is seen. Set "
                "CarryConfig.benchmark_annual_pct (percent per year) and re-run.")
        return self.benchmark_annual_pct / 100.0
