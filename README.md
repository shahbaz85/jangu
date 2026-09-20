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

## mr70/: MR-70 high-win-rate mean reversion — findings

Pre-registered spec (`MR70_STRATEGY_SPEC.md`): TP 0.75 ATR / SL 2.0 ATR on 15m
BNB/ETH/SOL/DOGE, 730 days, four signal variants plus a random control, with a
gate requiring a pooled hit-rate lift of >= +4 pp over random with the 95% CI
lower bound above zero.

**No variant passed the gate.** Pooled, against a 6,883-trade random baseline of
68.73%:

| variant | trades | hit rate | lift | 95% CI | verdict |
|---|---|---|---|---|---|
| V1 Bollinger+RSI | 569 | 69.2% | +0.51 pp | [-3.44, +4.46] | fail |
| V2 RSI(2) in trend | 648 | 71.5% | +2.72 pp | [-0.93, +6.36] | fail |
| V3 VWAP climax | 446 | 72.9% | +4.14 pp | [-0.13, +8.40] | fail (CI) |
| V4 V3 + trend | 159 | 73.6% | +4.85 pp | [-2.09, +11.79] | fail (CI) |

Validation that the machinery works: on `synthetic()` random-walk data the same
control reproduces a 70.8% baseline and no variant beats it, independently
matching the spec's own preliminary 71-72% figure.

**The more important finding is that the +4 pp gate was never sufficient.** The
random baseline on this symbol set is 68.73%, lower than the 71-72% the spec's
preliminary work saw on BTC/DOGE/AVAX. So baseline + 4 pp = 72.73%, which is
*exactly* break-even BEFORE costs. Including costs, break-even is:

    p = (sl_atr + cost/ATR) / (tp_atr + sl_atr)

which for a round trip of 0.09-0.11% of price and ATR of 0.3-1.0% of price lands
between **76% and 84%**. Reaching that from a 68.73% baseline needs roughly
**+8 to +12 pp**, not +4. The best variant managed +4.85 pp on 159 trades.

**Biggest weakness in the design:** the geometry is cost-hostile. A 0.75 ATR
take-profit is small enough that fixed costs eat 20-30% of every winning trade,
so the hit rate required for profitability climbs far above the headline 72.73%.
High win rate and cost efficiency pull against each other here, and shrinking the
target to raise the win rate makes the cost drag worse, not better.

Per the spec, the backtest (step 4/5) was not run on any variant, since none
passed the gate. V3 was the most consistent across symbols (70.1-74.8%) and is
the only one worth revisiting if the geometry were changed.

### V3 follow-up (fresh symbols, larger geometry) — FAIL

Pre-registered in `MR70_V3_FOLLOWUP.md`: V3 unchanged, run only on symbols it had
never seen (BTC, AVAX, XRP, ADA), with TP 1.5 / SL 3.0 ATR and a 32-bar time stop,
against an absolute cost-aware break-even instead of the +4 pp rule. See
`mr70/v3_followup.py`.

| symbol | trades | hit | required | margin |
|---|---|---|---|---|
| BTC | 177 | 53.7% | 69.4% | −15.77 pp |
| AVAX | 203 | 58.6% | 68.7% | −10.05 pp |
| XRP | 202 | 60.9% | 68.7% | −7.83 pp |
| ADA | 199 | 55.8% | 68.5% | −12.70 pp |

Pooled: **781 trades, hit rate 57.36%**, Wilson 95% CI [53.87%, 60.79%], required
68.81%. Margin on the Wilson lower bound **−14.94 pp**. Beat the bar on **0 of 4**
symbols. **VERDICT: FAIL**, by a wide margin, not a marginal miss.

**V3 came in 2.43 pp BELOW random entry** (57.36% vs 59.79% on 12,509 control
trades) — a sign reversal from the +4.14 pp it showed at the original 0.75/2.0
geometry on the original symbols.

Why the bar sits where it does, decomposed:

- theoretical random, no time stop, no costs: 66.67%
- observed random: 59.79% → the **32-bar time stop costs 6.88 pp**, truncating
  11.7% of trades of which roughly 59% would otherwise have won
- costs add a further 2.14 pp → required 68.81%
- so breaking even needs **+9.02 pp over random**; V3 delivers −2.43 pp

**Biggest weakness:** V3's edge, to whatever extent it was ever real, lives only at
short horizons and small targets — exactly where fixed costs are proportionally
largest. Widen the target so costs become affordable and the edge disappears
entirely. That is a squeeze, not a tuning problem: the edge exists where it cannot
be traded profitably, and vanishes where it could be.

**Honest caveat:** the follow-up changed two things at once — fresh symbols *and* a
larger geometry. So the reversal cannot be attributed to geometry alone; part of
the original +4.14 pp may simply have been selection noise (V3 was the best of four
variants) that failed to replicate out of sample. Both readings are consistent with
this data, and both are negative for V3.

Per the addendum, the backtest was not run and no other geometries, thresholds or
symbols were tried.

### V3: old geometry on fresh symbols — separating geometry from selection noise

Requested explicitly to resolve the confound in the follow-up (which varied
symbols *and* geometry together). Run with
`mr70/v3_followup.py --geometry old`.

Completed 2x2, V3 lift over the V0 random control:

| | old symbols (BNB/ETH/SOL/DOGE) | fresh symbols (BTC/AVAX/XRP/ADA) |
|---|---|---|
| **old geometry** 0.75/2.0, 16 bars | +4.14 pp | **+3.40 pp** |
| **new geometry** 1.5/3.0, 32 bars | — | −2.43 pp |

**Geometry effect: confirmed.** The two fresh-symbol cells differ only in
geometry — same symbols, same signal — so +3.40 pp vs −2.43 pp isolates it with
no selection confound. V3's edge is real at short horizons and inverts when the
target is widened.

**Selection noise: largely but not conclusively ruled out.** +4.14 → +3.40 pp on
entirely unseen symbols is only 0.74 pp of regression; a best-of-four winner's
curse would decay harder. But at 364 trades the lift's 95% CI is [−1.30, +8.10],
which spans zero, so it is not independently significant.

**Gate result: FAIL anyway.** Pooled 364 trades, hit 72.80%, Wilson lower bound
68.01% against a required 74.83%; 1 of 4 symbols beat the bar.

| geometry | edge needed over random | delivered | shortfall |
|---|---|---|---|
| 0.75/2.0 | +5.43 pp | +3.40 pp | −2.03 pp |
| 1.5/3.0 | +9.02 pp | −2.43 pp | −11.45 pp |

**Definitive characterisation: V3's edge is real and is about 63% of the size
needed to pay its own costs.**

**Why no geometry fixes it — bracketed from both sides.** Shrink the target and
fixed costs eat a larger share of each win: on BTC only 35 of 367 signals were
even allowed to trade, the rest rejected by `tp_too_small_vs_fees`. Widen the
target and the edge inverts to below random. There is no window where both hold.

Secondary red flag: 22 pp spread across symbols (BTC 60.0%, ADA 69.1%, XRP 71.6%,
AVAX 82.0%). Even at the favourable geometry it is not stable across instruments.

Note on process: the pre-registered reading of this run was "lift >= +3 pp with CI
above zero" for a real effect, "<= +2 pp" for noise. The actual +3.40 pp with a CI
spanning zero fell in a gap between those branches, and is recorded as such rather
than assigned to whichever branch suited the conclusion.

## shared/: Strategy A and Strategy B — gate results

Pre-registered in `AB_STRATEGY_SPEC.md`. Both judged independently with a 95%
two-sided Wilson bound (two hypotheses in one round). See `shared/run_gates.py`.

**Both report INSUFFICIENT SAMPLE.** Neither reaches the 150-trade minimum, so
per the spec no backtest was run.

| | signals | trades | hit | required | Wilson 95% | verdict |
|---|---|---|---|---|---|---|
| A (EMA + StochRSI + volume + ABCD) | 95 | 6 | 33.3% | 54.1% | [9.7%, 70.0%] | insufficient |
| B (SMC 4H→1H→30m→15m cascade) | 801 | 96 | 43.8% | 54.0% | [34.3%, 53.7%] | insufficient |

**B's result is stronger than "insufficient" implies.** Its entire 95% interval
(34.26–53.72%) lies *below* the cost-inclusive requirement of 53.96%. So B is not
merely unmeasured: at 95% confidence its true hit rate is below what it needs to
pay its own costs. It also came in 4.88 pp under a random control taken in the
same 4H regime (48.63%, n=8276), though at n=96 that difference spans zero and is
suggestive rather than proven.

**A is genuinely unknown.** Six trades; the interval spans 9.7–70.0% and excludes
nothing.

### The binding constraint is the 2.5 ATR stop cap, for both

| | signals | rejected `stop_too_wide` | tradeable |
|---|---|---|---|
| A | 95 | 89 (93.7%) | 6 |
| B | 801 | 587 (73.3%) | 96 |

Flagged before the run, from synthetic data, and worse on real data. For B the
cause is a unit mismatch: the stop anchors to the **30m** sweep extreme while the
cap is measured in **15m** ATR, and the break may arrive 8 bars after the sweep.
Median stop distance is 3.88 ATR against a 2.5 cap.

Recovering those rejected signals was tested as a diagnostic and **makes things
worse**, because a wider stop pushes TP1 further away in price while the 96-bar
time stop stays fixed, and an unresolved trade counts as a loss:

| stop width | n | timed out | hit TP1 |
|---|---|---|---|
| ≤ 2.5 ATR (admitted) | 80 | 1.2% | 55.0% |
| 2.5–4 ATR | 227 | 6.2% | 49.3% |
| 4–6 ATR | 133 | 18.0% | 43.6% |
| > 6 ATR | 181 | 64.1% | 16.0% |

Admitting everything drops the pooled hit rate from 55.0% to 39.1%. The cap is
selecting the only tradeable subset, not discarding good trades.

### Other findings

- **Strategy A's filters fight each other.** Breaking B is a breakout, so median
  StochRSI %K at the trigger is 82.7 — already overbought — while the momentum
  rule wants a fresh cross above 20 still under 80. Only 14.3% of break bars
  satisfy both.
- **Strategy B fires almost as often on random-walk data as on real data** (734
  synthetic vs 801 real, +9%). A cascade genuinely reading market structure would
  be expected to separate the two more than that.
- Runner conversion P(2R | TP1) was 45.2% for B, so its scaled exit needs 51.2%
  rather than the gate's 54.0% — the scale-out was less punishing here than
  feared, but B clears neither number.
