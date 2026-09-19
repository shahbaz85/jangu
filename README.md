# SMC 15m Crypto Futures Signal Agent

A deterministic Smart Money Concepts signal engine for USDT-M perpetuals. It runs the same code in the backtest and in live, so what you test is what you get alerted on.

**It sends signals only. It never places orders.** Not financial advice. Validate before risking money.

## Files

| File | Purpose |
|---|---|
| `config.py` | Every parameter in one place (sessions, ATR multiples, filters, scoring, risk, fees) |
| `features.py` | Swings, BOS/CHoCH, FVGs, liquidity (PDH/PDL, Asia range, equal highs/lows), 1H/4H context. No lookahead |
| `engine.py` | State machine: sweep → structure break with displacement → FVG/OB entry → filters → score/grade |
| `risk.py` | Position size from stop distance; leverage is an output, capped by a liquidation-distance rule |
| `backtest.py` | Bar-by-bar simulation with fees, slippage, partial TP, BE stop, daily limits, Monte Carlo drawdown |
| `walkforward.py` | Optimise on one window, test on the next unseen one, to catch curve fitting |
| `live.py` | Polls the exchange after every 15m close and sends Telegram alerts; journals every signal |
| `tests.py` | Lookahead-bias test, signal-stability test, live pipeline test |

## Setup

```bash
pip install -r requirements.txt
python tests.py                 # must print five "ok" lines
```

## Workflow

**1. Get data and backtest (at least 2 years):**
```bash
python backtest.py --fetch BTC/USDT:USDT --days 730      # also saves BTC_15m.csv
python backtest.py --csv BTC_15m.csv --news news.csv     # re-run from cache
```
The report shows win rate, expectancy (R), profit factor, max drawdown, 95th/99th percentile Monte Carlo drawdown, results by grade and side, and **reject reasons**. Trades go to `trades.csv`.

**2. Read the reject reasons.** They tell you which rule is the bottleneck. If `sl_too_wide` dominates, your stops are structurally wide; if `no_displacement` dominates, the market isn't showing intent. Loosen one rule at a time and see whether expectancy holds.

**3. Walk-forward:**
```bash
python walkforward.py --csv BTC_15m.csv --train-months 6 --test-months 2
```
Trust the **OOS combined** line, not the in-sample numbers.

**4. Paper-trade the live agent for 4 to 6 weeks:**
```bash
export TELEGRAM_BOT_TOKEN=xxxx   # from @BotFather
export TELEGRAM_CHAT_ID=xxxx
cp news.example.csv news.csv     # keep it updated with CPI / FOMC / NFP times (UTC)
python live.py
```
Compare `signal_journal.csv` with what the backtest would have done over the same weeks.

**5. Go live small** (0.25% risk) for the first 50 trades.

## Strategy rules as coded

- **Bias:** 4H market structure trend (close-based BOS/CHoCH on fractal swings).
- **Sweep:** a 15m candle wicks through untaken PDL/PDH, Asian low/high, or equal lows/highs and closes back inside, during or up to 1h before a kill zone.
- **Kill zones (UTC):** London 07:00–10:00, New York 12:30–15:30. (UAE time: add 4 hours.)
- **Trigger:** within 8 candles, a close-based 15m structure break in the new direction with displacement (body ≥ 1.5 ATR, or 3 same-colour candles spanning ≥ 2 ATR).
- **Entry:** limit at the midpoint of the unmitigated FVG created by the move (or the OB body edge).
- **Stop:** beyond the sweep extreme + 0.1 ATR. Rejected if wider than 2 ATR.
- **Targets:** TP2 = nearest opposing liquidity (PDH/PDL, Asia range, equal highs/lows, 1H range extreme); must be ≥ 2R. TP1 = 2R (or 60% of TP2 distance if smaller): close 50%, stop to breakeven.
- **Order expiry:** 8 candles, or earlier if price reaches TP2 or an opposite structure break prints.
- **Filters:** 4H bias, premium/discount of the 48h 1H range, ATR percentile 20–95%, news blackout ±30 min, funding extremes (live).
- **Score:** 3 base + 4H aligned 2 + external sweep 2 + FVG/OB overlap 1 + 1H OTE 1 + 1H FVG reaction 1 + rising OI 1 (live). Grade A ≥ 8 (1% risk), B ≥ 6 (0.5%).
- **Risk:** max 3 trades/day, stop for the day at −2R, pause after 4 straight losses.

## Honest limits

- The backtest is conservative (stop assumed before target in the same candle), but it cannot model queue position on limit fills, funding payments, or exchange outages.
- Open interest and funding only exist live, so the live scoring can differ slightly from the backtest.
- The code was tested on synthetic random-walk data for correctness, not for profitability. Expect **low signal frequency** with the default filters: this is a selective strategy. Your real-data backtest decides whether it has an edge.
- The correlation guard in live is advisory: it warns rather than blocks.

## Real-data findings (2024–2026, 8 symbols)

The strategy was backtested against real Binance USDT-M 15m data, 730 days, on
BTC/ETH/BNB/SOL/XRP/ADA/DOGE/AVAX, with the config defaults as coded (no
cherry-picked thresholds). See `pool_symbols.py`.

- **Setup frequency is very low.** Only 17 signals reached full scoring across
  all 8 symbols combined over 2 years (~1 per symbol per year). This held on
  every symbol tested individually too, not just BTC.
- **Fill rate is low.** Only 5 of those 17 (29%) actually filled as trades; the
  rest were pending limit orders (entry at the FVG midpoint/OB edge) that
  expired or were cancelled before price retraced to them.
- **Pooled result on the 5 that filled:** 20% win rate, expectancy -0.818R,
  profit factor 0.12, net -4.09R. Too small a sample to prove the strategy
  loses money, but no evidence of positive edge either.
- Aggressively loosening the single biggest bottleneck
  (`displacement_body_atr` down to 0.7, vs the 1.5 default) on BTC and BNB
  individually still only produced 3-4 trades per symbol over 2 years --
  confirming the low frequency isn't a threshold-tuning problem. It's
  structural to how rarely this specific sweep-then-structure-break pattern
  occurs at 15m within the London/NY kill zone hours, on these symbols.

**Conclusion:** as currently specified, this rule set has not shown validated
edge and is not recommended for live capital. The engine/backtest/live
infrastructure held up well throughout this testing (deterministic,
no-lookahead, walk-forward-capable) and is reusable for a different strategy.
A future attempt at this specific idea would need either a materially
different setup definition (broader sweep detection, a longer confirmation
window, a market-order entry instead of a limit order to fix the low fill
rate) or a much longer/broader dataset to validate at this level of
selectivity.

## trend_config.py / trend_engine.py / trend_backtest.py: findings

A second, unrelated strategy: a 4H EMA(21/55) trend filter plus a 15m
pullback-to-EMA(20) reclaim entry, fixed ATR stop, tested both with a fixed 2R
target and with a trailing stop after TP1 (`trend_backtest.py --trailing`).
Unlike the SMC strategy, this fires often -- built specifically to answer the
low-frequency problem above.

On 730 days of real BTC 15m data:

- **Fixed R:R:** 1,364 trades, 47.1% win rate, expectancy -0.218R, profit
  factor 0.66, -82.3% return.
- **Trailing stop:** 1,386 trades, 46.9% win rate, expectancy -0.239R, profit
  factor 0.63, -85.6% return -- slightly worse, not better.

Both are large, statistically solid samples (not a sample-size problem like
the SMC strategy). The trailing-stop test specifically ruled out "the fixed
target caps winners" as the cause. **Conclusion:** the entry signal itself
(price reclaiming a 20-EMA in a moderately-trending 4H regime) has no
measurable edge on BTC -- a plausible reason is that it's a well-known,
widely-traded pattern on a highly liquid, closely-watched asset. Not
recommended for live capital. `pool_symbols.py --strategy trend` is available
to test it across more symbols if that's ever worth revisiting.
