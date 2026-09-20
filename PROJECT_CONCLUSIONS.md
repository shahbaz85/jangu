# Jangu — where the project stands

Written at the close of the first working session. Four strategies were built, tested and
closed; the infrastructure and the method are what survive. This is the document to read
before starting again.

---

## 1. The short version

Four strategies were taken from specification to measurement. **None is tradeable.** They
failed for four genuinely different reasons, and the differences matter more than the
common outcome:

| strategy | sample | result | why it closed |
|---|---|---|---|
| **SMC 15m** | 17 signals, 8 symbols, 2 years | 5 trades, −4.09R | Too rare to measure. Not disproven — unmeasurable. |
| **EMA pullback (trend)** | 1,364 trades | −0.218R per trade | Large clean sample, clearly negative. A trailing stop changed nothing. |
| **MR-70 V3** | ~500 trades | 72.8% win rate, −0.029R per trade | A real edge, +3.4 pp over control — about 63% of what costs demand. |
| **Strategy A / B** | B: 1,187 trades | 48.3% vs 53.8% required | Measured below break-even, and below its zero-cost line at the point estimate. |

The single most useful number in the whole project: **round-trip cost on 15m crypto
perpetuals sets the break-even hit rate at 53–54% for a 1:1 target-and-stop.** Every
strategy above started 3–4 pp in that hole and none climbed out.

---

## 2. What was built

Five codebases, 37 commits, all tests passing.

**Root — SMC engine.** `config.py`, `features.py`, `engine.py`, `risk.py`, `backtest.py`,
`walkforward.py`, `live.py`, `tests.py`, `data.py`, `pool_symbols.py`. Liquidity sweeps,
BOS/CHoCH, FVGs, order blocks, displacement, kill zones, premium/discount, OTE.

**`trend_*.py` — EMA pullback.** Trend filter, pullback entry, ATR stops, optional
trailing exit after TP1.

**`tradingview/smc_signal_indicator.pine`** — the SMC engine ported to Pine v5 (~650
lines), so signals appear on a chart rather than only in a backtest.

**`mr70/`** — mean-reversion experiment. Five variants V0–V4, where **V0 is a dense random
control** rather than a strategy. `edge_gate.py`, `v3_followup.py`, `paper_live.py`.

**`shared/`** — Strategy A and B. Multi-timeframe feature assembly (15m/30m/1H/4H), the
1H/30m/15m cascade with ablation switches, the cost-inclusive gate, and `diagnostics.py`.

---

## 3. What each strategy actually showed

### SMC 15m — unmeasurable, not disproven

Eight symbols over two years produced 17 signals and 5 completed trades. Widening the
session window from London-only to 24h, and then to UAE evening hours, barely moved it;
relaxing the displacement threshold to 0.7 helped slightly. The conditions stack
multiplicatively and almost never all occur.

**This is the one strategy that was never given a fair test.** It has no verdict, only an
absence of data. Revisiting it would mean either loosening the conjunction or accepting a
far longer history.

### EMA pullback — clearly negative

1,364 trades, −0.218R per trade. A trailing stop after TP1 left it unchanged. This is the
cleanest negative in the project: a large sample, a simple rule, and no ambiguity.

### MR-70 V3 — a real edge, and not enough of one

V3 won 72.80% of its trades and still lost 0.029R on each. Against its random control it
was +3.4 pp — genuinely predictive, roughly **63% of the edge its costs require**.

Two follow-ups sharpened it. On fresh symbols with **larger geometry** it inverted and
failed. On fresh symbols with the **original geometry** it held at +3.40 pp. So the edge
is real and specific to that geometry, not an artifact of which symbols were picked.

At one point I claimed a better fee tier could close the 2 pp gap. That was wrong, and I
retracted it: closing it needs roughly a **30× cost reduction**, not a tier change.

### Strategy A and B — closed on a negative

A returned INSUFFICIENT SAMPLE. B produced 801 signals, of which 73% were rejected for a
stop wider than 2.5 ATR, leaving 96 trades at 43.75% against a 53.96% requirement.

The diagnostic work that followed is the most careful measurement in the project, and it
overturned two explanations in a row:

1. **The time-stop hypothesis died.** The theory was that a fixed 96-bar horizon was
   truncating wide-stop trades into losses. Measured: **1 of 96 trades timed out.**
2. **The random control was measuring geometry, not edge.** B led it by 7.0 pp on real
   data — and by 7.5 pp on random-walk data where no edge exists.
3. **The stop cap selected adversely.** The 587 rejected signals would have hit 52.3%
   against the traded subset's 43.8%.
4. **Every configuration sits below its own cost requirement.** Break-only, the largest
   and least-selected (n=1,187): 48.3% against 53.8%.
5. **The shortfall decomposes.** Of break-only's 5.5 pp gap, **3.3 pp is round-trip cost
   and 2.2 pp is a pre-cost deficit.** A zero-fee venue closes 60% of it and still leaves
   the strategy short.

Full detail is in `STRATEGY_B_FINDINGS.md`, which went through six revisions with Opus.

---

## 4. The method that emerged

This is worth more than any of the strategies, and it should carry into whatever comes
next.

**Always run a random control.** Nearly every apparent effect in this project turned out
to be a property of the *specification* rather than of the market. A control that shares
the strategy's regime filter, stop rule and entry construction is the only way to see it.

**Match the control's construction, not just its regime.** B's control entered at the bar
close while B entered at an FVG midpoint. That difference alone was worth 7 pp. A control
that differs from the strategy in geometry measures geometry.

**Falsify on synthetic data.** Every engine was run on random-walk data and required to
fail. This caught real bugs.

**Cost first, strategy second.** Compute the break-even hit rate *before* designing
anything. It is 53–54% here, and it is the reason three of four strategies were doomed
before a line of logic was written.

**Compute required rates per configuration.** Wider stops carry proportionally less cost.
Borrowing one configuration's requirement for another hid the conclusion for two full
revisions.

**Power before specification.** Detecting a +3 pp edge needs ~2,200 trades; +5 pp needs
~780. A 150-trade minimum was never defensible. If the data cannot supply the sample the
effect size demands, the question cannot be asked — and that is worth knowing in advance.

**Pre-register, and mean it.** Changing a threshold in a fresh spec is legitimate.
Changing it in response to results inside the same experiment is not.

**Honest intervals.** Wilson assumes independent trades. Measured here, cross-symbol
clustering was 0.91–1.17× — undetectable, so the intervals were close to honest. That was
measured, not assumed, and the assumption going in had been 20–40%.

**Never tune toward the result.** Held throughout. Several results would have looked much
better with one threshold moved, and none was moved.

---

## 5. Reusable infrastructure

Tested, no-lookahead verified, and independent of any strategy that failed:

- **Multi-timeframe features** with HTF values attaching only on candle close, verified by
  a test that truncated history must reproduce full-history values.
- **`shared/gate.py`** — cost-inclusive break-even per signal, Wilson intervals,
  one-position-at-a-time thinning, regime-matched random control.
- **`shared/diagnostics.py`** — exit-reason splits, stop-width tables, rejected-signal
  outcomes, per-ablation hit rates with per-set requirements, a moving-block bootstrap, a
  self-calibrating clustering ratio, a power calculator, and zero-cost break-even from
  measured runner conversion.
- **`data.py`** — fetching, caching, and a synthetic generator for falsification.
- **`mr70/paper_live.py`** — forward-only paper observer, watermarked, no order-capable
  code anywhere in it.

Nine tests pass in `shared/tests.py`, plus the suites in `mr70/` and the root.

---

## 6. What is still running

**The MR-70 V3 paper observer, on the laptop.** It has been logging since 20 September and
holds the first genuine forward signal (SHORT BTC @ 81,138.8, 16:30 UTC). It is the only
thing in the project still accumulating new information.

When there is enough of it, the question to ask is narrow and worth answering: **does the
live fill rate match the backtest's ~41% assumption?** If live fills come in materially
lower, every backtest in this repository is optimistic by an amount nothing here has
measured.

---

## 7. Starting points for tomorrow

In rough order of expected value:

1. **Check the paper log.** Cheapest, and it tests an assumption underneath everything
   else. `mr70/paper_trades.csv`.
2. **Design against the cost hurdle, not around it.** The break-even is 53–54% at 1:1 on
   15m. Either find a geometry where the hurdle is smaller (higher timeframe, so cost is a
   smaller fraction of the stop), or require a candidate edge large enough to clear it
   *detectably* — +5 pp, not +3 pp.
3. **Reconsider the timeframe.** Every strategy here traded 15m, where cost eats 3–4 pp.
   On 4H the same absolute cost is a far smaller fraction of a wider stop. Nothing in this
   project tested that, and it is the single largest untested lever.
4. **SMC deserves a fair test or a decision.** It is the only strategy with no verdict.
   Either give it enough data to be measurable or close it deliberately rather than by
   neglect.
5. **Leave Strategy B alone.** It is closed. Resolving its one open population needs about
   30 symbol-years and its point estimate is already on the wrong side.

Whatever comes next, write the power calculation and the cost hurdle **into the spec
before the first backtest runs.** That one habit would have saved most of this session.

---

## 8. A note on what this was worth

Four strategies, none tradeable. Measured against "find a profitable bot," that is a
failure. Measured against "find out whether these ideas work," it is four clean answers
and a method that produces them reliably — and the method is the part that transfers.

The most valuable single discovery was not about any strategy. It was that **apparent
edges kept turning out to be artifacts of the measurement**: a cap that selected the worse
half, a time stop blamed for losses it never caused, a control that flattered by 7 pp, a
required rate borrowed from the wrong configuration. Each looked like a finding. None was.
The discipline that caught them is the asset.
