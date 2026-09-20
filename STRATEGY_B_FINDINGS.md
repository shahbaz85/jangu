# Strategy B — findings (v5, closing)

Handoff note. Produced under `AB_STRATEGY_SPEC.md` with no tuning: thresholds were never
changed, and the backtest was never run because the gate was never passed.

Supersedes v4. v4 reframed v3's "no measurable edge" as a **power result** — the study
cannot resolve an edge of the size that would make B viable. That framing was right about
the cap-lifted population and wrong about the thing you would actually trade. With the
real-data cost requirement measured per configuration rather than borrowed from synthetic
data, **every as-specified configuration of Strategy B has a confidence interval lying
entirely below its own break-even rate.** That is a conclusion, and it closes the
question.

**[real]** = 730 days of Binance 15m data, BTC/ETH/SOL/DOGE.
**[synthetic]** = random-walk data from `data.synthetic()`.

All diagnostics come from `shared/diagnostics.py`, which is measurement only: it loosens
the stop cap and horizon so that rejected and truncated signals can be *observed*. No
loosened setting feeds back into a pre-registered decision.

## 1. What was tested

The four-level cascade exactly as specified: 4H market-structure trend → price tagging an
unmitigated 1H displacement zone → a 30m sweep within 12×30m bars → a decisive 15m break
of structure within 8 bars (body ≥60% of range and ≥1.2 ATR). Entry limit at the 15m FVG
midpoint (else the break close), valid 8 bars. Stop beyond the 30m sweep extreme ±0.2
ATR(15m). TP1 1R closing 50% with the stop to breakeven, runner trailing 2.5 ATR. Time
stop 96 bars. Rejections: stop >2.5 ATR (`stop_too_wide`) or <5× round-trip cost.

## 2. The closing result

**[real]** Each configuration against **its own** cost-inclusive requirement. The
requirement differs per configuration because stop width differs, and a wider stop carries
proportionally less cost:

| configuration | trades | hit rate | Wilson 95% | required | top of CI vs required |
|---|---|---|---|---|---|
| full cascade | 96 | 43.8% | [34.3%, 53.7%] | 54.0% | −0.3 pp |
| no 30m sweep | 453 | 45.7% | [41.2%, 50.3%] | 53.8% | −3.5 pp |
| no 1H zone | 249 | 44.6% | [38.5%, 50.8%] | 53.9% | −3.1 pp |
| **break only** | **1187** | **48.3%** | **[45.4%, 51.1%]** | **53.8%** | **−2.7 pp** |

Four for four below break-even, at every sample size from 96 to 1,187.

The break-only row carries the weight. It has the largest sample and the least selection
— its stops are tight, so the 2.5 ATR cap rejects almost nothing, meaning it is close to
the full signal population rather than a cap-selected slice. Stress-tested against every
correction that could plausibly widen it:

| treatment | interval | vs required 53.8% |
|---|---|---|
| block bootstrap as measured | [45.1%, 51.6%] | below |
| ×1.17 for measured clustering | [44.5%, 52.2%] | below |
| ×1.25 for bootstrap bias | [44.3%, 52.4%] | below |
| ×1.50, deliberately harsh | [43.5%, 53.2%] | below |

It does not reach break-even under any of them. **This is not an underpowered result. It
is a negative one.**

## 3. Clustering: measured, and smaller than assumed

v4's standing caveat assumed correlated symbols would inflate confidence by 20–40%.
Measured, against a label-shuffled copy of the same trades so the bootstrap's own bias
cancels:

| set | n | ratio |
|---|---|---|
| pooled cascade (cap lifted) | 581 | 0.91× |
| regime-matched control | 4034 | 0.96× |
| break only (as shipped) | 1187 | 1.17× |

All three sit inside the tool's ±25% resolution, i.e. **no clustering is detectable**.
The intervals in §2 stand roughly as computed; the correction is 0–17%, not 20–40%. v4's
caveat was well-motivated and the number it guessed was too pessimistic.

This also answers a design question: because the four symbols behave close to
independently, adding more symbols would buy close to linear sample growth. §5 explains
why that no longer matters.

## 4. What remains unresolved, and why it does not rescue B

One population is genuinely unresolved: the whole signal set with the cap and fee floor
lifted and a 500-bar horizon.

| | n | hit rate | bootstrap 95% | required |
|---|---|---|---|---|
| **[real]** cascade | 581 | 51.1% | [47.5%, 54.6%] | **53.1%** |
| **[synthetic]** cascade | 589 | 50.1% | — | 52.6% |

53.1% sits inside that interval, so this population cannot be called either way. Resolving
it needs ~2,178 trades against the 50.1% geometry baseline — about **30 symbol-years**,
against the 8 available. With clustering near 1.0× that is reachable: roughly 15 liquid
perpetuals over two years, using the existing code unchanged.

Three reasons it is not worth doing:

1. **The point estimate is on the wrong side.** 51.1% against a 53.1% requirement. Power
   analysis says a positive cannot be *excluded*; it does not say a positive is likely.
   The best available guess is that 30 symbol-years would confirm a negative.
2. **It is not a configuration you can trade.** Lifting the cap and the fee floor is an
   observation device. Every configuration that respects the spec's own risk rules is
   settled in §2.
3. **The requirement moved the wrong way.** Using the real per-set cost (53.1%) rather
   than the synthetic proxy (52.6%) widened the gap, and the required rate rises further
   with any realised slippage above the modelled figure.

## 5. What is established

- The 96-bar time stop is not the constraint: 1 of 96 trades timed out (v3 §3).
- Every as-specified configuration is below its cost-inclusive break-even, with the
  interval entirely below, at n up to 1,187 (§2).
- Cross-symbol clustering is not detectable, so the intervals are close to honest and
  adding symbols would scale nearly linearly (§3).
- The random-walk control measures geometry, not edge: the cascade leads it by 7.0 pp on
  real data and 7.5 pp where no edge exists (v3 §4). Any future work needs a
  construction-matched placebo instead.
- The 2.5 ATR cap selects adversely: rejects 52.3%, traded 43.8% (v3 §5).

## 6. What was asked for and not produced

A revised `shared/diagnostics.py` adding a construction-matched time-shifted placebo
(§8.2) and a paired bootstrap of the cascade-minus-placebo difference (§8.3b) was
specified but never reached the repository, so **the paired difference interval against a
placebo has not been computed.** Everything above rests on per-configuration break-even
comparisons, which do not need it: §2 compares each configuration against its own cost
requirement, not against a null arm.

That work would sharpen §4's unresolved population. It would not change §2.

## 7. Recommendation

Close Strategy B. Not because it was disproven in the abstract — §4 is genuinely open —
but because every tradeable form of it is measurably below break-even, and the one open
question has a point estimate on the wrong side and costs 30 symbol-years to settle.

If a follow-up happens regardless, three things carry forward and are worth more than the
strategy was:

1. **A construction-matched placebo**, not a random-walk control (§5).
2. **A power calculation before the spec is written**, not after the result. At these
   costs, an edge worth pursuing must clear break-even by enough to be detectable in the
   data available — roughly +5 pp, needing ~780 trades, not +3 pp needing ~2,200.
3. **Cost as a design input.** The requirement is 53–54% because round-trip cost eats
   3–4 pp. Any 1:1 TP/stop geometry on 15m crypto perpetuals starts from that hole. A
   design that cannot clear it by a detectable margin is not worth measuring, however
   sound its logic.

## 8. Infrastructure

Built, tested, no-lookahead verified (`shared/`): 15m/30m/1H/4H feature assembly with HTF
values attaching only on candle close; 1H displacement zones; 30m sweep detection; the
cascade with ablation switches; the cost-inclusive gate with per-signal break-even, Wilson
intervals, one-position thinning and a regime-matched random control (see §5 before
trusting it); a synthetic falsification check; and `shared/diagnostics.py` — exit-reason
splits, stop-width tables, rejected-signal outcomes, per-ablation hit rates with per-set
required rates, a moving-block bootstrap, a self-calibrating clustering ratio, and a power
calculator. Eight tests pass.
