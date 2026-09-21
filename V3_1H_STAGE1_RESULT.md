# V3 on 1H — Stage 1 result: FAIL

Run under `V3_1H_SPEC.md` + `V3_1H_ADDENDUM.md`. Single blind run, as the protocol
requires. Stage 2 not run. Per the addendum's own terms, **V3 on 1H is closed.**

## 1. The direct answer

The hypothesis was: *V3's edge over its null is larger on 1H than on 15m, by at least the
2–3.5 pp needed to clear the lower cost hurdle.*

**It is not larger. It is gone.** V3's 1H edge over its placebo is **−1.0 pp.**

| | |
|---|---|
| pooled hit rate | **68.95%** on 947 trades |
| block bootstrap 95% | [64.63%, 73.14%] |
| pooled break-even | 74.47% |
| placebo baseline | 69.97% on 2,241 trades |
| paired difference (V3 − placebo) | **−1.0 pp**, 95% [−5.45, +3.56] |
| p\* (+0.08R, Stage 2's bar) | 80.17% |

The entire bootstrap interval sits **below** break-even — the top of it, 73.14%, is 1.3 pp
short. And the paired difference straddles zero, so V3 is not distinguishable from a
construction-matched null that has no information in it at all.

## 2. Gate conditions

| condition | result |
|---|---|
| 1. Stage 0b satisfied | PASS (947 ≥ 435) |
| 2. bootstrap lower bound > break-even | **FAIL** (64.63% vs 74.47%) |
| 3. paired difference entirely above zero | **FAIL** (lower bound −5.45%) |
| 4. survives leave-one-symbol-out | FAIL — but see below |
| 5. synthetic fired and failed | **INCONCLUSIVE** — see below |

**Condition 4 carries no information here.** It asks whether condition 2 survives removing
each symbol. Condition 2 already fails pooled, so every removal fails too. All twelve
"FLIPS" lines say only that a failing result stays failing. The condition would have been
informative had condition 2 passed.

**Condition 5 is INCONCLUSIVE, not evidence against V3.** The synthetic arm produced 63
signals against the 300 floor. The amended gate lists "fired and failed" as a pass
requirement, so an unfired check cannot contribute a PASS — but it should not be read as
the falsification check finding something. It found nothing either way.

Why so few: block-shuffled volume preserves the 3× spike rate (verified — 7.74% real,
8.06% shuffled), but V3 needs a spike *and* a VWAP stretch *and* a rejection wick. On
random-walk prices with volume unlinked to price, that conjunction is rare. The addendum
anticipated this and set the INCONCLUSIVE fallback correctly.

**The verdict does not depend on either.** Conditions 2 and 3 fail decisively on their own.

## 3. Robustness

Everything points the same way.

- **Block length:** lower bound 64.73 / 64.63 / 64.56% at 7 / 28 / 90 days; paired lower
  bound −6.12 / −5.45 / −4.96%. No sensitivity to the choice.
- **Strict fill:** 70.0% fill rate (929 of 1,327) against the touch model's 100%. Hit rate
  68.35%, lower bound 63.98%, paired −6.06%. Fails identically, so the result is **not**
  FILL-DEPENDENT.
- **Provenance:** 69.77% on the four symbols the 15m parameters were chosen on, 68.47% on
  the other eight. Essentially identical. **There is no overfitting signature here** — the
  15m parameters were not tuned to those symbols in a way that inflated them. V3 simply
  does not work on 1H.

## 4. The finding that matters beyond this experiment

**The 1H test used a better null than the 15m test did, and that difference may account
for the whole result.**

- On 15m, V3 measured +3.4 pp over a **random control**.
- On 1H, V3 measures −1.0 pp over a **time-shifted placebo**.

These are not the same comparison. The Strategy B diagnostics established that a random
control which does not match the strategy's entry and stop construction measures
*geometry*, not edge — there it flattered the cascade by 7 pp on data with no edge in it.
The time-shifted placebo is construction-matched by definition and has no such flaw.

So there are two readings, and this experiment cannot distinguish them:

1. V3's edge is real on 15m and disappears at 1H.
2. V3's 15m "+3.4 pp" was partly or wholly the same geometry artifact, and a
   construction-matched null would have shown less or nothing there too.

**Reading 2 is checkable and cheap.** Run the time-shifted placebo against the existing
15m V3 results — same data, same code path, no new fetching. `shifted_signals()` already
exists and the 15m CSVs are cached.

This is not a continuation of the 1H experiment, which is closed. It is an audit of a
claim already made: `PROJECT_CONCLUSIONS.md` records MR-70 V3 as having "a real edge,
+3.4 pp, about 63% of what costs demand." If that number rests on a null the project has
since shown to be unfit, the conclusions document overstates it and should be corrected.

## 5. Incidental result worth keeping

The strict fill model filled **70% of signals against the touch model's 100%.** The 30-point
gap is the size of the optimism in every backtest in this repository that uses a
touch-based limit fill. The 15m paper observer was set up to measure exactly this and has
not reported yet; when it does, it now has a number to be compared against.

## 6. Status

- V3 on 1H: **closed**, per the addendum's protocol. No thresholds, windows, geometry,
  symbols or history were changed at any point, and nothing was changed after the run.
- Stage 2: not run, correctly.
- Recommended next action: the 15m placebo audit in §4 — one run, no new data, and it
  decides whether a headline claim in the project's conclusions is sound.
