# Strategy B — findings, for designing a follow-up

Handoff note. Everything below was produced under `AB_STRATEGY_SPEC.md` with no
tuning: thresholds were never changed, and the backtest was never run because the
gate was never passed. Numbers marked **[real]** are from 730 days of Binance 15m
data on BTC/ETH/SOL/DOGE. Numbers marked **[synthetic]** are from random-walk data
and are indicative of *geometry*, not of edge.

## 1. What was tested

The four-level cascade exactly as specified: 4H market-structure trend → price
tagging an unmitigated 1H displacement zone → a 30m sweep within 12×30m bars →
a decisive 15m break of structure within 8 bars (body ≥60% of range and ≥1.2 ATR).
Entry limit at the 15m FVG midpoint (else the break close), valid 8 bars. Stop
beyond the 30m sweep extreme ±0.2 ATR(15m). TP1 1R closing 50% with the stop to
breakeven, runner trailing 2.5 ATR. Time stop 96 bars. Rejections: stop >2.5 ATR
(`stop_too_wide`) or <5× round-trip cost (`stop_too_tight_vs_fees`).

## 2. Result

**[real]** Gate verdict: **INSUFFICIENT SAMPLE** — 96 trades against a 150 minimum.

| | value |
|---|---|
| signals generated | 801 |
| rejected `stop_too_wide` | 587 (73.3%) |
| rejected `stop_too_tight_vs_fees` | 64 |
| unfilled limit entries | 54 |
| **trades** | **96** |
| hit rate (TP1 before stop) | 43.75% |
| Wilson 95% CI | [34.26%, 53.72%] |
| required (cost-inclusive) | 53.96% |
| random control, same 4H regime | 48.63% (n=8276) |
| runner conversion P(2R \| TP1) | 45.2% |

Two things are worth more than the verdict:

- The **entire** confidence interval sits below the requirement (53.72 < 53.96).
  On the 96 trades that were actually tested, B is not merely unmeasured — it is
  below break-even with 95% confidence.
- B underperformed its random control by 4.88 pp, though at n=96 that difference
  spans zero and is suggestive only.

Per-symbol was unstable: BTC +3.51 pp above required, ETH −27.70, SOL −5.66,
DOGE −8.62. One of four beat its bar.

## 3. The central problem: a fixed time stop with a variable stop width

The stop anchors to the **30m** sweep extreme while the cap is measured in **15m**
ATR, and the break can arrive 8 bars after the sweep. **[real]** median stop
distance is 3.88 ATR against a 2.5 cap, p75 is 5.85. That is a unit mismatch, and
it is why 73% of signals never traded.

The deeper issue is what happens to the ones that do. **[synthetic]** with the
width cap lifted but the 96-bar time stop kept:

| stop width | n | timed out | hit TP1 |
|---|---|---|---|
| ≤ 2.5 ATR | 80 | 1.2% | 55.0% |
| 2.5–4 ATR | 227 | 6.2% | 49.3% |
| 4–6 ATR | 133 | 18.0% | 43.6% |
| > 6 ATR | 181 | 64.1% | 16.0% |

That looks like wide stops being bad. They are not. With the **time stop also
lifted**, so trades resolve naturally:

| stop width | n | median bars | p75 | p90 | hit rate |
|---|---|---|---|---|---|
| ≤ 2.5 ATR | 65 | 13 | 31 | 56 | **55.4%** |
| 2.5–4 ATR | 175 | 31 | 69 | 100 | **54.3%** |
| 4–6 ATR | 103 | 67 | 147 | 414 | **54.4%** |
| > 6 ATR | 148 | 195 | 436 | 983 | **47.3%** |

**The hit rate is essentially flat across stop widths once trades are allowed to
finish.** The entire apparent degradation was the 96-bar limit truncating trades
that had not yet resolved, which the spec counts as losses.

Resolution time scales roughly as the **square** of stop width — 6 × width² fits
the table well — which is what a diffusion process predicts (time ∝ distance²).
A *fixed* time stop is therefore structurally mis-specified for a strategy whose
stop width varies by a factor of four. 96 bars is adequate for a 2.5 ATR stop and
far too short for a 5 ATR one.

## 4. What is already ruled out

- **Simply widening the stop cap will not work.** Tested: admitting everything
  under the current 96-bar limit drops the pooled hit rate from 55.0% to 39.1%.
  Width and time have to move together or not at all.
- **The scaled exit is not the problem here.** Runner conversion was 45.2%, so
  the scale-out needs 51.2% versus the gate's 54.0% — slightly easier, not harder.
  B clears neither.
- **Signal frequency is not the problem.** 801 signals over 730 days on four
  symbols is ample; the losses are all downstream of generation.

## 5. What is still genuinely unknown

**Whether Strategy B has any edge at all has not actually been measured.** 73% of
its signals were rejected before being tested, and the surviving 12% were judged
under a time limit suited only to the narrowest stops. The 43.75% hit rate is a
measurement of the *cap-selected subset*, not of the strategy.

One caution for the redesign: **[synthetic]** B fires 734 times on random-walk
data versus 801 **[real]**, only 9% more. A cascade genuinely reading market
structure would be expected to separate real data from a random walk by more than
that. This is a reason for modest expectations, not a verdict.

## 6. What a follow-up would need to decide

These are the open design choices, not recommendations:

1. **Make the time stop proportional to stop width** rather than fixed — the
   width² relationship above gives a principled basis. Or normalise the other
   way: define the stop in 15m ATR terms so widths stop varying so much.
2. **Resolve the timeframe mismatch** — if the stop anchors to a 30m extreme, the
   cap arguably belongs in 30m ATR, not 15m.
3. **Decide what the cap is for.** Position sizing is already risk-normalised
   (0.75% of equity), so a wider stop reduces size rather than increasing risk.
   The cap's real job is to keep the fee-to-stop ratio sane and the horizon
   tradeable — both of which are better expressed directly.
4. Whatever changes, it must be **pre-registered before seeing results**. Changing
   the cap now, in a fresh spec, is legitimate. Changing it in response to these
   numbers, inside the same experiment, would not be.

## 7. Infrastructure available for reuse

Already built, tested and no-lookahead verified (`shared/`): 15m/30m/1H/4H feature
assembly with HTF values attaching only on candle close; 1H displacement zones;
30m sweep detection; the cascade itself with ablation switches; the cost-inclusive
gate with per-signal break-even, Wilson intervals, one-position-at-a-time thinning
and a regime-matched random control; a synthetic falsification check that both
strategies correctly fail.

Changing geometry parameters requires editing `shared/config.py` only. Changing
the time-stop *rule* from fixed to width-dependent requires a small change in
`shared/gate.py` (`resolve`) and in the backtest when it is eventually run.

## 8. Ablations (reporting only, never used to select)

**[real]** dropping the 30m sweep requirement: 801 → 883 signals. Dropping the 1H
zone requirement: 801 → 2648. The 1H zone is doing nearly all of the filtering;
the 30m sweep admits only ~10% more when removed, so on frequency grounds it is
close to redundant. Whether it improves *quality* was never measurable, because
the gate never got a clean sample.
