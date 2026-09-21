# Jangu — where the project stands

Written at the close of the second working session. Four strategy families were built, tested and closed across five tests; the infrastructure and the method are what survive. This is the document to read before starting again.

---

## 1. The short version

Four strategy families, five tests, all taken from specification to measurement. **None is tradeable.** They failed for genuinely different reasons, and the differences matter more than the common outcome:

| strategy | sample | result | why it closed |
|---|---|---|---|
| **SMC 15m** | 17 signals, 8 symbols, 2 years | 5 trades, −4.09R | Too rare to measure. No verdict — see §7 for the recommendation to close it deliberately. |
| **EMA pullback (trend)** | 1,364 trades | −0.218R per trade | Large clean sample, clearly negative. A trailing stop changed nothing. |
| **MR-70 V3 (15m)** | 446 trades | 72.87% vs 74.97% required | Gross expectancy about zero — reaches break-even *before* costs, not after. Edge over a construction-matched null +2.55 pp [−2.40, +7.52]: unsupported, not refuted (§3). |
| **MR-70 V3 (1H)** | 947 trades | 68.95% vs 74.47% required | Below its placebo (69.97%); paired difference −1.0 pp [−5.45, +3.56]. |
| **Strategy A / B** | B: 1,187 trades | 48.3% vs 53.8% required | Measured below break-even, and below its zero-cost line at the point estimate. |

The single most useful number in the whole project: **round-trip cost on 15m crypto perpetuals sets the break-even hit rate at 53–54% for a 1:1 target-and-stop.** Every 15m strategy above started 3–4 pp in that hole and none climbed out. Moving to 1H helped less than expected. Measured, the hurdle for V3's geometry fell only from 74.97% to 74.47% — the cost drag above the 72.73% pre-cost line went from 2.24 pp to 1.74 pp, i.e. **22% of it, not a halving** (§3, "what the timeframe actually bought").

---

## 2. What was built

Six codebases, 44 tests passing across six suites (root 5, trend 2, mr70 10, shared 9, mr70 1H 13, wyckoff 5).

**Root — SMC engine.** `config.py`, `features.py`, `engine.py`, `risk.py`, `backtest.py`, `walkforward.py`, `live.py`, `tests.py`, `data.py`, `pool_symbols.py`. Liquidity sweeps, BOS/CHoCH, FVGs, order blocks, displacement, kill zones, premium/discount, OTE.

**`trend_*.py` — EMA pullback.** Trend filter, pullback entry, ATR stops, optional trailing exit after TP1.

**`tradingview/smc_signal_indicator.pine`** — the SMC engine ported to Pine v5 (~650 lines), so signals appear on a chart rather than only in a backtest.

**`mr70/`** — mean-reversion experiment. Five variants V0–V4, where **V0 is a dense random control** rather than a strategy (now retired as a null — see §4). `edge_gate.py`, `v3_followup.py`, `v3_1h.py`, `v3_15m_audit.py`, `paper_live.py`.

**`wyckoff/`** — Wyckoff event detection (trading range, spring/upthrust, SOS/SOW, LPS/LPSY) and a feasibility probe for combining it with SMC. Built, tested, never taken to a gate: the probe showed the conjunction fires too rarely to test at 4H and that ~58% of Wyckoff completions already coincide with an SMC signal on random-walk data.

**`shared/`** — Strategy A and B. Multi-timeframe feature assembly (15m/30m/1H/4H), the 1H/30m/15m cascade with ablation switches, the cost-inclusive gate, `diagnostics.py`, and `shifted_signals()` for the construction-matched placebo.

---

## 3. What each strategy actually showed

### SMC 15m — unmeasurable, not disproven

Eight symbols over two years produced 17 signals and 5 completed trades. Widening the session window from London-only to 24h, and then to UAE evening hours, barely moved it; relaxing the displacement threshold to 0.7 helped slightly. The conditions stack multiplicatively and almost never all occur.

It has no verdict, only an absence of data. §7 recommends closing it deliberately rather than revisiting it.

### EMA pullback — clearly negative

1,364 trades, −0.218R per trade. A trailing stop after TP1 left it unchanged. This is the cleanest negative in the project: a large sample, a simple rule, and no ambiguity.

### MR-70 V3 — break-even before costs on 15m, closed on 1H

**15m.** On the original symbols (BNB/ETH/SOL/DOGE), V3 won 72.80% of its trades and still lost 0.029R on each. The right way to state that is **gross expectancy about zero**: a hit rate at the 72.7% *pre-cost* break-even is enough edge to reach break-even before costs and not after. Closing the remaining gap would need roughly a **30× cost reduction**, not a fee-tier change; an earlier claim that a better tier could close it was wrong and was retracted.

**The edge over random was measured against V0 and has since been audited.** V0 is a random control that does not reproduce V3's entry construction. Re-run against the construction-matched time-shifted placebo (`mr70/v3_15m_audit.py`, same signal, geometry, costs, symbols and cached data):

| | |
|---|---|
| V3 | 72.87% on 446 trades (reproduces the original 72.80%) |
| time-shifted placebo | 70.32% on 529 trades |
| V0 random control | 68.77% on 6,910 trades |
| V3 − V0 | **+4.10 pp** |
| V3 − placebo | **+2.55 pp**, 95% [−2.40, +7.52] |
| pooled break-even | 74.97% |

Three statements, in decreasing order of firmness:

- **Established:** 1.55 pp of the 4.10 pp — **38% of it** — disappears when the null is construction-matched. That much of the original figure was the control, not V3.
- **Not established either way:** whether the remaining +2.55 pp is real. The interval spans zero but also contains +3.4 and +4.1. Power to detect an effect that size at these arm sizes is **14%**; resolving it would need roughly 4,900 trades per arm, about 88 symbol-years. This is *unsupported*, not refuted.
- **Not in doubt:** V3 at 72.87% sits below its own 74.97% break-even, with a 95% lower bound 7.35 pp below it. Whether or not an edge exists, it is not a tradeable one.

The audit's pre-registered reading rule called a zero-spanning interval "not a real edge". That rule conflated absence of evidence with evidence of absence; it is reported here as unsupported instead, and the audit script was corrected to report power rather than deliver that verdict.

**The earlier follow-ups carry the same caveat.** A follow-up on fresh symbols (BTC/AVAX/XRP/ADA) with the original geometry measured **+3.40 pp against V0**; with larger geometry the result inverted and failed. Both were V0 comparisons, so the +3.40 pp replicated a V0-based figure and should be read as an **upper bound**, not as independent confirmation of a real edge. Note that +3.40 pp (fresh symbols) and +4.10 pp (original symbols) are different measurements on different symbol sets.

**What the timeframe actually bought.** The 1H spec assumed 1H ATR would be roughly twice the 15m ATR, so costs would be half the fraction of each trade. Back out the implied ATR from the two measured break-evens and it grew **1.33×**, not the 2.00× that square-root-of-time scaling predicts: 0.89% of price on 15m against 1.19% on 1H. The cost drag fell 0.50 pp rather than ~1.1 pp. Caveat: the two figures come from different symbol sets (four symbols on 15m, twelve on 1H), so this is indicative rather than a clean measurement — but it is the right order of magnitude and it matters for §7.3, because the same logic applied to 4H predicts a further drag of about 1.3 pp, not the near-elimination the argument for higher timeframes implicitly assumes.

**1H — closed.** Tested under `V3_1H_SPEC.md` and its addendum on twelve symbols over four years, powered for the smallest tradeable edge and run blind in a single pass: 68.95% on 947 trades against a 74.47% break-even, with the whole bootstrap interval below it, and a paired difference against the time-shifted placebo of **−1.0 pp [−5.45, +3.56]**. Robust across block lengths, unchanged under a strict fill model, and identical on the symbols the 15m parameters were chosen on versus fresh ones — so no overfitting signature, simply no edge. Full detail in `V3_1H_STAGE1_RESULT.md`.

**MR-70 is closed.** Re-auditing V1, V2 and V4 against the placebo was considered and declined: those numbers will not be cited again, and any V0-based MR-70 figure should simply be read as an upper bound.

### Strategy A and B — closed on a negative

A returned INSUFFICIENT SAMPLE. B produced 801 signals, of which 73% were rejected for a stop wider than 2.5 ATR, leaving 96 trades at 43.75% against a 53.96% requirement.

The diagnostic work that followed overturned two explanations in a row:

1. **The time-stop hypothesis died.** The theory was that a fixed 96-bar horizon was truncating wide-stop trades into losses. Measured: **1 of 96 trades timed out.**
2. **The random control was measuring geometry, not edge.** B led it by 7.0 pp on real data — and by 7.5 pp on random-walk data where no edge exists.
3. **The stop cap selected adversely.** The 587 rejected signals would have hit 52.3% against the traded subset's 43.8%.
4. **Every configuration sits below its own cost requirement.** Break-only, the largest and least-selected (n=1,187): 48.3% against 53.8%.
5. **The shortfall decomposes.** Of break-only's 5.5 pp gap, **3.3 pp is round-trip cost and 2.2 pp is a pre-cost deficit.** A zero-fee venue closes 60% of it and still leaves the strategy short.

Full detail is in `STRATEGY_B_FINDINGS.md` (v6).

---

## 4. The method that emerged

This is worth more than any of the strategies, and it should carry into whatever comes next.

**Always run a construction-matched null.** Nearly every apparent effect in this project turned out to be a property of the *specification* rather than of the market. The null must share the strategy's entry construction, stop rule and target, not just its regime. The **time-shifted placebo** (`shifted_signals()`) does this by definition: same prices, same construction, alignment destroyed. **Random controls such as V0 are retired as nulls.** They inflated the apparent edge by 1.55 pp for MR-70 V3 and by 7 pp for Strategy B.

**Falsify on synthetic data, and check the falsification can fire.** Every engine was run on random-walk data and required to fail. This caught real bugs. But a check that produces no signals is not a pass: V3 fired zero times on uniform-volume synthetic data, and only 63 times with real volume grafted on. A falsification check must report INCONCLUSIVE when it cannot fire.

**Cost first, strategy second.** Compute the break-even hit rate *before* designing anything. It is 53–54% at 1:1 on 15m, and it is why most of these strategies were doomed before a line of logic was written. Shrinking the target does not help: it raises the random win rate and the break-even rate together, leaving the gap unchanged.

**Compute required rates per configuration.** Wider stops carry proportionally less cost. Borrowing one configuration's requirement for another hid the conclusion for two full revisions.

**Power for the smallest edge worth trading, before specification.** Detecting a +3 pp edge needs ~2,200 trades; +5 pp needs ~780. But the right target is not the smallest edge that exists — it is the smallest edge that would pass the profitability bar. V3 on 1H was first declared underpowered for an edge it could never have traded; re-powered for the tradeable edge, 947 trades were more than enough and gave a clean answer.

**Absence of evidence is not evidence of absence.** An interval spanning zero at low power means "cannot tell", not "no effect". This error was made and corrected twice: in the Strategy B findings (v3 → v4) and in the MR-70 audit's own reading rule.

**Pre-register, and mean it.** Changing a threshold in a fresh spec is legitimate — including re-registering while the result is still unseen, as with V3 on 1H. Changing it in response to results inside the same experiment is not. Where a pre-registered *reading* rule turns out to be wrong, depart from it openly and say why.

**Honest intervals.** Wilson assumes independent trades. Measured here, cross-symbol clustering was 0.91–1.17× — undetectable, so the intervals were close to honest. That was measured, not assumed, and the assumption going in had been 20–40%.

**Model fills pessimistically.** A touch-based limit fill filled **100%** of V3's 1H signals. Requiring price to trade 0.05 ATR through the limit, closer to what queue position demands live, filled **70%**. That 30-point gap is a measured estimate of the optimism in touch-fill backtests in this repository.

**Never tune toward the result.** Held throughout. Several results would have looked much better with one threshold moved, and none was moved.

---

## 5. Reusable infrastructure

Tested, no-lookahead verified, and independent of any strategy that failed:

- **Multi-timeframe features** with HTF values attaching only on candle close, verified by a test that truncated history must reproduce full-history values.
- **`shared/gate.py`** — cost-inclusive break-even per signal, Wilson intervals, one-position-at-a-time thinning.
- **`shifted_signals()`** — the construction-matched time-shifted placebo. The default null for all future work.
- **`shared/diagnostics.py`** — exit-reason splits, stop-width tables, rejected-signal outcomes, per-ablation hit rates with per-set requirements, a moving-block bootstrap, a paired block bootstrap for differences, a self-calibrating clustering ratio, a power calculator, and zero-cost break-even from measured runner conversion.
- **`data.py`** — fetching, caching, and a synthetic generator for falsification (note: uniform volume; strategies with volume conditions need real volume grafted on).
- **`mr70/paper_live.py`** — forward-only paper observer, watermarked, no order-capable code anywhere in it.

---

## 6. What is still running

**The MR-70 V3 paper observer, on the laptop.** It has been logging since 20 September and holds the first genuine forward signal (SHORT BTC @ 81,138.8, 16:30 UTC). It is the only thing in the project still accumulating new information.

V3 itself is closed, so the observer's value is now purely as a **measurement of live fills**. Two reference figures exist and they are not the same measurement:

- the **15m backtest's ~41% fill rate** for V3 under the touch model, which is what the observer runs against directly. Treat this figure as unverified: the audit run turned 1,287 signals into 446 trades (34.7%), but that ratio conflates unfilled limits with non-overlap thinning and fee-floor rejections, so it is not a fill rate. **The observer needs a clean denominator — signals offered, of which filled — before this comparison means anything;**
- the **1H strict-model result** (70% filled vs 100% touch), which measures how much a trade-through requirement cuts fills, on a different timeframe.

The question for the observer: does the live 15m fill rate come in materially below ~41%? If it does, touch-fill backtests here are optimistic, and the 1H figure suggests by how much.

---

## 7. Starting points for next time

In rough order of expected value:

1. **Check the paper log** (`mr70/paper_trades.csv`) against the fill figures in §6. Cheapest, and it tests an assumption underneath every backtest.
2. **Consider a non-directional return source.** Every test here failed at the same wall: predicting short-term direction while paying costs on every trade. A **funding-rate carry** study (long spot, short the matching perpetual, collect funding) needs no directional prediction and trades rarely. It carries its own risks — negative funding periods, exchange counterparty risk, liquidation of the short leg — and needs the same pre-registered treatment.
3. **If staying directional, test 4H or daily — with tempered expectations.** 1H was tested for V3 and closed. On 4H the absolute cost is a smaller fraction of a wider stop, but the measured 15m→1H scaling (§3) says that relief arrives at about 1.33× per fourfold step in timeframe, not 2×, so 4H buys roughly another 0.4 pp of hurdle rather than removing it. Multi-day momentum does have more research support than anything on 15m. Expect higher clustering (crypto trends move together) and far fewer signals, so the power calculation comes first — and note the Wyckoff probe already found the SMC+Wyckoff conjunction fires about 1.2 times per symbol-year on 4H, which would need ~626 symbol-years to test.
4. **Close SMC deliberately.** Strategy B's break-only configuration — the loosest version of the same idea (trend agreement plus a decisive structure break) — sat at a coin flip on 1,187 trades, and B's ablations showed the SMC-style filters did not improve quality. Seventeen signals in two years would also make it untradeable even if it worked. Closing it is a decision, not neglect.
5. **Leave Strategy B and MR-70 alone.** Both are closed.

Whatever comes next, write the **cost hurdle, the power calculation for the smallest tradeable edge, and a construction-matched placebo into the spec before the first backtest runs.** Those three habits would have saved most of this project.

---

## 8. A note on what this was worth

Four strategy families, five tests, none tradeable. Measured against "find a profitable bot," that is a failure. Measured against "find out whether these ideas work," it is five clean answers and a method that produces them reliably — and the method is the part that transfers.

The most valuable single discovery was not about any strategy. It was that **apparent edges kept turning out to be artifacts of the measurement**: a cap that selected the worse half, a time stop blamed for losses it never caused, a random control that flattered by 7 pp in one strategy and 1.55 pp in another, a required rate borrowed from the wrong configuration, a verdict of "underpowered" for an edge that could never have been traded. Each looked like a finding. None was. The discipline that caught them is the asset.
