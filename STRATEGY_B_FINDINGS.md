# Strategy B — findings (v3)

Handoff note for designing a follow-up. Produced under `AB_STRATEGY_SPEC.md` with no
tuning: thresholds were never changed, and the backtest was never run because the gate
was never passed.

This supersedes v2. v2's central claim — that a fixed time stop was truncating trades
and suppressing the hit rate — has been **measured on real data and falsified**. What
replaced it is a cleaner and more negative result.

**[real]** = 730 days of Binance 15m data, BTC/ETH/SOL/DOGE.
**[synthetic]** = random-walk data from `data.synthetic()`, used as a no-edge control.

Everything below marked as a diagnostic comes from `shared/diagnostics.py`, which is
measurement only: it loosens the stop cap and the horizon so that rejected and
truncated signals can be *observed*. No loosened setting feeds back into a
pre-registered decision.

## 1. What was tested

The four-level cascade exactly as specified: 4H market-structure trend → price tagging
an unmitigated 1H displacement zone → a 30m sweep within 12×30m bars → a decisive 15m
break of structure within 8 bars (body ≥60% of range and ≥1.2 ATR). Entry limit at the
15m FVG midpoint (else the break close), valid 8 bars. Stop beyond the 30m sweep extreme
±0.2 ATR(15m). TP1 1R closing 50% with the stop to breakeven, runner trailing 2.5 ATR.
Time stop 96 bars. Rejections: stop >2.5 ATR (`stop_too_wide`) or <5× round-trip cost
(`stop_too_tight_vs_fees`).

## 2. Gate result

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
| runner conversion P(2R \| TP1) | 45.2% |

How firmly to read it: the CI upper bound (53.72%) falls 0.24pp below the requirement
(53.96%). That is a rounding accident, not a finding — treat this as *at* the break-even
boundary, and note that the required rate is itself an estimate that moves with realised
slippage. Per-symbol figures (BTC +3.51pp above required, ETH −27.70, SOL −5.66, DOGE
−8.62) rest on ~24 trades each, i.e. intervals near ±20pp. Those symbols are **not
distinguishable from one another**; the spread is what noise looks like at this sample
size.

The verdict is superseded by §3 and §4 regardless.

## 3. The time-stop hypothesis is dead

v2 argued that a fixed 96-bar horizon against a stop width varying by 4× was truncating
unresolved trades into losses, and that this suppressed the measured hit rate. It was a
**[synthetic]**-only argument and v2 flagged it as a hypothesis. Measured:

**[real]** exit-reason split of the 96 trades:

| exit | n | share |
|---|---|---|
| TP1 (win) | 42 | 43.8% |
| stop | 53 | 55.2% |
| time stop | **1** | **1.0%** |

One trade in 96. The 43.75% hit rate is 53 genuine stop-outs against 42 genuine wins.
Truncation had essentially nothing to do with it, and **making the horizon proportional
to stop width would not have changed the result.**

The underlying geometry claim remains true and is worth keeping: on **[synthetic]** data
with the cap lifted, resolution time scales as roughly 6 × (stop width in ATR)², which is
what diffusion predicts, and the >6 ATR bucket does time out heavily (17%). It simply is
not what happened to the traded subset, because the 2.5 ATR cap had already excluded
every stop wide enough for it to matter. v2's §3 and §7.1 are therefore solving a
problem that does not exist.

## 4. The real finding: no measurable edge

Run the cascade with the stop cap and the fee floor lifted and the horizon at 500 bars,
so the whole signal population resolves rather than the cap-selected 12%. Then run the
identical code on random-walk data, where there is nothing to find by construction:

| | n | hit rate | Wilson 95% | required |
|---|---|---|---|---|
| **[real]** cascade | 581 | **51.1%** | [47.1%, 55.2%] | — |
| **[synthetic]** cascade | 589 | **50.1%** | [46.1%, 54.1%] | 52.6% |

**Difference: +1.0pp, standard error 2.9pp, 95% CI [−4.7pp, +6.7pp].** Real market data
gives this cascade nothing over a random walk.

That is a far stronger statement than the gate's "insufficient sample", because n=581 is
not a small sample. The gate could not measure the strategy; this can, and it finds
nothing.

Two corollaries:

- **The random control was never a fair baseline.** Under the same lifted settings the
  regime-matched control resolves at 44.1% **[real]** and 42.6% **[synthetic]** — the
  cascade leads it by 7.0pp on real data and 7.5pp on data with no edge in it. That gap
  is *geometry* (a limit entry at the FVG midpoint against a stop anchored to the swept
  extreme), not market reading. So v2's "B trailed its control by 4.88pp, no conclusion"
  was reading a broken instrument. A follow-up must either build the control with the
  cascade's own entry and stop construction, or drop the comparison.
- **Widening the stop does not buy viability.** The required rate falls only from 53.96%
  to 52.6% when the cap comes off, because cost-to-risk improves sub-linearly. The
  lifted-cap hit rate of 51.1% is still below it.

Two implementation checks were run and both came back clean: `resolve()` walking from the
fill bar itself is worth ~0.8pp (the bar that reached a long's limit usually opened above
it, so testing TP on it is mildly asymmetric), and the synthetic generator's block-drift
regimes do not reach the trades — the regime-aligned control resolves *below* 50%, and
the cascade takes 46.4% longs, i.e. slightly against the drift. Neither explains the
geometry gap.

## 5. What the stop cap selected

**[real]** the 587 `stop_too_wide` rejects, resolved under the lifted horizon: **52.3%**
(n=459, CI [47.7%, 56.8%]) against the traded subset's 43.8%.

The cap threw away the better half. The difference is 8.5pp with a standard error of
5.6pp, so it is suggestive rather than established, and the two sets were resolved under
different bar limits. But the direction matters: the cap is not a quality filter, and any
claim about "Strategy B's hit rate" that rests on the 96 trades is a claim about an
adversely selected subset. Note also that the rejects' 52.3% is itself indistinguishable
from the **[synthetic]** rejects' 48.7% (difference 3.6pp, SE 3.2pp) — consistent with
§4: no edge anywhere in the population.

## 6. Ablations

**[real]** hit rates under the shipped config, reporting only:

| | signals | trades | hit rate | Wilson 95% |
|---|---|---|---|---|
| full cascade | 801 | 96 | 43.8% | [34.3%, 53.7%] |
| no 30m sweep | 883 | 453 | 45.7% | [41.2%, 50.3%] |
| no 1H zone | 2648 | 249 | 44.6% | [38.5%, 50.8%] |
| break only | — | 1187 | 48.3% | [45.4%, 51.1%] |

Stripping filters does not hurt and may help; the plainest configuration (4H agreement
plus a decisive 15m break) has both the largest sample and the highest hit rate. No pair
of these is separated beyond noise.

**One caveat that keeps this from being a clean comparison:** dropping the sweep
requirement also changes the stop anchor, because `cascade()` falls back to the signal
bar's own extreme when there is no sweep. The no-sweep and break-only rows therefore have
materially tighter stops and different geometry, not just fewer filters. A follow-up that
wants to judge the filters on quality must hold the stop rule fixed across ablations.

## 7. What is now established, and what is not

Established:

- The 96-bar time stop is not the constraint (§3).
- The cascade does not separate real data from a random walk at n≈581 (§4).
- The 2.5 ATR cap selects adversely rather than filtering for quality (§5).
- The random control as built measures geometry, not edge (§4).

Not established:

- Whether any *sub*-population of the cascade carries an edge. §4 pools everything; a
  conditional split (session, volatility regime, distance from the 4H swing) was never
  tested and is the only place left where an edge could hide.
- Whether the 1H zone and 30m sweep help quality, for the stop-anchor reason in §6.

## 8. Design choices for a follow-up

Choices, not recommendations. Each must be fixed **before** results are seen; changing a
rule in a fresh pre-registration is legitimate, changing it in response to these numbers
inside the same experiment is not.

1. **Decide whether this cascade is worth another round at all.** §4 is the relevant
   number, and it is a null at a sample size large enough to mean it. The honest default
   is to stop, not to re-parameterise.
2. If it continues, **fix the control first.** A baseline that the strategy beats by 7pp
   on random-walk data cannot support any conclusion.
3. **Hold the stop rule fixed across ablations**, so the filters are judged on quality
   rather than on an incidental change in stop width.
4. **Set the sample requirement up front.** Detecting +5pp over a ~49% baseline at 80%
   power needs roughly 600–800 trades, not 150. The lifted-cap run reaches 581, so the
   measurement is feasible — it is the *gate* at 96 that was not. A follow-up that cannot
   reach that number should say so and plan to pool more symbols or a longer history.
5. **Decide what the cap is for.** Position sizing is already risk-normalised (0.75% of
   equity), so a wider stop reduces size rather than increasing risk. §5 shows the cap is
   costing signal quality; its real jobs are keeping the fee-to-stop ratio sane and the
   horizon tradeable, and both are better expressed directly.

## 9. Infrastructure available for reuse

Built, tested and no-lookahead verified (`shared/`): 15m/30m/1H/4H feature assembly with
HTF values attaching only on candle close; 1H displacement zones; 30m sweep detection;
the cascade with ablation switches; the cost-inclusive gate with per-signal break-even,
Wilson intervals, one-position-at-a-time thinning and a regime-matched random control
(see §4 before trusting it); a synthetic falsification check; and `shared/diagnostics.py`,
which produces every table above on either data source.

Geometry parameters live in `shared/config.py`. Changing the time-stop rule from fixed to
width-dependent needs a small change in `shared/gate.py` (`resolve`) — though §3 says
there is no reason to.
