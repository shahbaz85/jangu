# Strategy B — findings (v6, closing)

Handoff note. Produced under `AB_STRATEGY_SPEC.md` with no tuning: thresholds were never changed, and the backtest was never run because the gate was never passed.

Supersedes v5. v5's conclusion stands — Strategy B is closed, and on a negative result rather than an underpowered one. v6 changes how that result is argued: it removes an overstatement (four nested configurations were presented as four independent confirmations), demotes a row that is a tie rather than a negative, and promotes the zero-cost observation in §3, which is the strongest single fact in the document and was buried.

Three corrections were applied to the v6 draft on review, all marked **[corrected]** in place: the nesting note in §2 was missing a second reason the rows are not comparable; §3's zero-cost line is stated for a plain 1:1 exit when the spec's exit is a scale-out, which makes the argument stronger but introduces a dependence on an unmeasured quantity; and §4's reading of the clustering measurement as evidence of a coin flip does not follow.

**[real]** = 730 days of Binance 15m data, BTC/ETH/SOL/DOGE.
**[synthetic]** = random-walk data from `data.synthetic()`.

All diagnostics come from `shared/diagnostics.py`, which is measurement only: it loosens the stop cap and horizon so that rejected and truncated signals can be *observed*. No loosened setting feeds back into a pre-registered decision.

## 1. What was tested

The four-level cascade exactly as specified: 4H market-structure trend → price tagging an unmitigated 1H displacement zone → a 30m sweep within 12×30m bars → a decisive 15m break of structure within 8 bars (body ≥60% of range and ≥1.2 ATR). Entry limit at the 15m FVG midpoint (else the break close), valid 8 bars. Stop beyond the 30m sweep extreme ±0.2 ATR(15m). TP1 1R closing 50% with the stop to breakeven, runner trailing 2.5 ATR. Time stop 96 bars. Rejections: stop >2.5 ATR (`stop_too_wide`) or <5× round-trip cost.

## 2. The closing result

**[real]** Each configuration against **its own** cost-inclusive requirement. The requirement differs per configuration because stop width differs, and a wider stop carries proportionally less cost:

| configuration | trades | hit rate | Wilson 95% | required | top of CI vs required |
|---|---|---|---|---|---|
| full cascade | 96 | 43.8% | [34.3%, 53.7%] | 54.0% | −0.3 pp (tie) |
| no 30m sweep | 453 | 45.7% | [41.2%, 50.3%] | 53.8% | −3.5 pp |
| no 1H zone | 249 | 44.6% | [38.5%, 50.8%] | 53.9% | −3.1 pp |
| **break only** | **1187** | **48.3%** | **[45.4%, 51.1%]** | **53.8%** | **−2.7 pp** |

**These are not four independent confirmations.** Break-only is a superset of the other three; the rows share most of their trades and are nested, not parallel. The correct reading is one large sample below break-even, with three subsets of it behaving the same way. That is sufficient, but it is a single result.

**[corrected]** The nesting is also not clean, for a second reason. `cascade()` anchors the stop to the 30m sweep extreme when the sweep is required and to the signal bar's own extreme when it is not, so the two `require_sweep=False` rows (no-30m-sweep and break-only) carry materially tighter stops and a different stop rule, not merely fewer filters. They are not subsets under the same geometry, and their required rates in the table above are lower for that reason. This does not weaken the conclusion — each row is judged against its own requirement — but it rules out reading the four rows as a dose-response curve in the number of filters.

**The full-cascade row is a tie and carries no weight.** A CI top 0.3 pp below the requirement was called a rounding accident in v3 and v4, and it remains one; it should not be counted as a negative simply because it falls on the useful side of the line. Nothing below depends on it.

**Break-only carries the conclusion.** It has the largest sample and the least selection — its stops are tight, so the 2.5 ATR cap rejects almost nothing, meaning it is close to the full signal population rather than a cap-selected slice. Stress-tested against every correction that could plausibly widen it:

| treatment | interval | vs required 53.8% |
|---|---|---|
| block bootstrap as measured | [45.1%, 51.6%] | below |
| ×1.17 for measured clustering | [44.5%, 52.2%] | below |
| ×1.25 for bootstrap bias | [44.3%, 52.4%] | below |
| ×1.50, deliberately harsh | [43.5%, 53.2%] | below |

It does not reach break-even under any of them. **This is not an underpowered result. It is a negative one.**

## 3. The decisive fact: there is nothing there before costs

The 53.8% requirement exists because round-trip cost eats ~3.8 pp; with fees and slippage set to zero, a 1:1 TP-and-stop geometry breaks even at exactly 50%.

Break-only's point estimate is **48.3%**, with an interval of [45.4%, 51.1%].

That is below 50% *with costs switched off entirely*, and the interval reaches 50% only at its very top. So the result is not "a marginal edge that fees destroyed" — **there is no edge to destroy before costs are applied at all.** For the conclusion to flip, the modelled round-trip cost would have to be overstated by roughly 71%, and even then the set would move from negative to unresolved, not to profitable.

This comparison does more work than the whole stress-test table in §2, because it is immune to every objection about cost modelling, slippage assumptions and fee tiers.

**[corrected] The 50% line is for a plain 1:1 exit, which is not what the spec trades.** The specified exit closes 50% at TP1 and runs the remainder to 2R against a breakeven stop, so its zero-cost break-even is `1/(1.5 + q)` where `q` is runner conversion, not 50%:

| runner conversion q | zero-cost break-even |
|---|---|
| 40% | 52.6% |
| 45.2% (measured, full cascade) | 51.2% |
| 50% | 50.0% |
| 57% | 48.3% |

At the measured `q`, the zero-cost line is **51.2%**, and break-only's entire interval [45.4%, 51.1%] falls below it — a stronger statement than the one above. But `q` was measured on the full cascade, not on break-only, and break-only's tighter stops put its 2R target a smaller absolute distance away, which could raise it. The point estimate stays below the line for any `q` under 57%; the interval's top stays below only for `q` under 45.7%.

So §3 trades an immunity to cost assumptions for a dependence on one unmeasured number. The honest statement is: **break-only's point estimate is below its zero-cost break-even under any plausible runner conversion, and its whole interval is below it if break-only's `q` matches the 45.2% measured elsewhere.** Measuring `q` for the break-only set would close this, and it is one number.

## 4. Clustering: measured, smaller than assumed, and mildly corroborating

v4's standing caveat assumed correlated symbols would inflate confidence intervals by 20–40%. Measured, against a label-shuffled copy of the same trades so the bootstrap's own bias cancels:

| set | n | ratio |
|---|---|---|
| pooled cascade (cap lifted) | 581 | 0.91× |
| regime-matched control | 4034 | 0.96× |
| break only (as shipped) | 1187 | 1.17× |

All three sit inside the tool's ±25% resolution, i.e. **no clustering is detectable**. The intervals in §2 stand roughly as computed; the correction is 0–17%, not 20–40%. v4's caveat was well-motivated and its guessed magnitude was too pessimistic.

**[corrected]** The v6 draft read this as corroborating evidence — that absent clustering, outcomes "look like near-independent coin flips." That inference does not hold. A strategy with a genuine but *constant* edge produces i.i.d. Bernoulli trades with no temporal clustering at all, so a ratio near 1 is equally consistent with a 48% coin flip and with a steady 55% edge. The measurement bears on the *variance* of the estimate, not its mean, and cannot corroborate §3.

What it does rule out is narrower and still worth having: the pooled rate is not a low average masking strong regime-dependence, with good stretches offset by bad ones. That possibility is off the table; the rest of §3's work is done by §3 alone.

Two consequences: because the four symbols behave close to independently, adding symbols would buy nearly linear sample growth (§5 explains why that no longer matters); and 1.17× should **not** be carried forward as a universal constant — a strategy with a genuinely regime-dependent edge would be expected to show a higher ratio, so it must be re-measured per design.

## 5. What remains unresolved, and why it does not rescue B

One population is genuinely unresolved: the whole signal set with the cap and fee floor lifted and a 500-bar horizon.

| | n | hit rate | bootstrap 95% | required |
|---|---|---|---|---|
| **[real]** cascade | 581 | 51.1% | [47.5%, 54.6%] | **53.1%** |
| **[synthetic]** cascade | 589 | 50.1% | — | 52.6% |

53.1% sits inside that interval, so this population cannot be called either way. Resolving it needs ~2,178 trades against the 50.1% geometry baseline — about **30 symbol-years**, against the 8 available. With clustering near 1.0× that is reachable: roughly 15 liquid perpetuals over two years, using the existing code unchanged.

Three reasons it is not worth doing:

1. **The point estimate is on the wrong side.** 51.1% against a 53.1% requirement. Power analysis says a positive cannot be *excluded*; it does not say a positive is likely. The best available guess is that 30 symbol-years would confirm a negative.
2. **It is not a configuration you can trade.** Lifting the cap and the fee floor is an observation device. Every configuration that respects the spec's own risk rules is settled in §2 and §3.
3. **The requirement moved the wrong way.** Using the real per-set cost (53.1%) rather than the synthetic proxy (52.6%) widened the gap, and the required rate rises further with any realised slippage above the modelled figure.

## 6. What is established

- The 96-bar time stop is not the constraint: 1 of 96 trades timed out (v3 §3).
- At the largest and least-selected sample (break-only, n=1,187), the hit rate is below 50% — below break-even even at zero cost — and below its cost-inclusive requirement under every widening correction tried (§2, §3).
- The as-specified configurations are nested subsets of that sample and behave the same way; the full-cascade row is a tie and carries no weight (§2).
- Cross-symbol clustering is not detectable, so the intervals are close to honest and adding symbols would scale nearly linearly. This bears on the width of the estimates, not on their level, and rules out regime-masking only (§4).
- The random-walk control measures geometry, not edge: the cascade leads it by 7.0 pp on real data and 7.5 pp where no edge exists (v3 §4). Any future work needs a construction-matched placebo instead.
- The 2.5 ATR cap selects adversely: rejects 52.3%, traded 43.8% (v3 §5).

## 7. What was asked for and not produced

A revised `shared/diagnostics.py` adding a construction-matched time-shifted placebo (§8.2) and a paired bootstrap of the cascade-minus-placebo difference (§8.3b) was specified but never reached the repository, so **the paired difference interval against a placebo has not been computed.**

Nothing above depends on it. §2 and §3 compare each configuration against its own cost requirement and against the zero-cost 50% line — neither needs a null arm. That work would sharpen §5's unresolved population; it would not change the conclusion.

## 8. Recommendation

Close Strategy B. Not because it was disproven in the abstract — §5 is genuinely open — but because the tradeable forms of it sit below break-even *and* below the zero-cost line, and the one open question has a point estimate on the wrong side and costs 30 symbol-years to settle.

If a follow-up happens regardless, three things carry forward and are worth more than the strategy was:

1. **A construction-matched placebo**, not a random-walk control (§6).
2. **A power calculation before the spec is written**, not after the result. At these costs, an edge worth pursuing must clear break-even by enough to be detectable in the data available — roughly +5 pp, needing ~780 trades, not +3 pp needing ~2,200.
3. **Cost as a design input.** The requirement is 53–54% because round-trip cost eats 3–4 pp. Any 1:1 TP/stop geometry on 15m crypto perpetuals starts from that hole. A design that cannot clear it by a detectable margin is not worth measuring, however sound its logic. The same arithmetic sank MR70 V3, though by a different route: its geometry was not 1:1 and its measured edge over control was real at +3.4 pp — it simply came to about 63% of what its own costs demanded. The common failure is not the exit geometry but the ratio of achievable edge to round-trip cost at this timeframe, and that ratio is knowable before any strategy logic is written.

## 9. Infrastructure

Built, tested, no-lookahead verified (`shared/`): 15m/30m/1H/4H feature assembly with HTF values attaching only on candle close; 1H displacement zones; 30m sweep detection; the cascade with ablation switches; the cost-inclusive gate with per-signal break-even, Wilson intervals, one-position thinning and a regime-matched random control (see §6 before trusting it); a synthetic falsification check; and `shared/diagnostics.py` — exit-reason splits, stop-width tables, rejected-signal outcomes, per-ablation hit rates with per-set required rates, a moving-block bootstrap, a self-calibrating clustering ratio, and a power calculator. Eight tests pass.
