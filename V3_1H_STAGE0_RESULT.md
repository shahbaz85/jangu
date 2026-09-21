# V3 on 1H — Stage 0 result

Run under `V3_1H_SPEC.md`. Stage 0 only. **Stage 1 was not run**, as the spec directs.

New files only (`mr70/v3_1h.py`, `mr70/v3_1h_tests.py`); the 15m V3 code and its results
are untouched. Six tests pass. Nothing was tuned: the windows were converted by time as
specified and no threshold was altered.

**The experiment is still blind.** V3's own 1H hit rate was never computed into the
report and has not been seen by anyone. Only the placebo baseline was measured, because
Stage 0 requires it to size the sample. Whatever is decided below can still be
pre-registered honestly.

## 1. Verdict

**UNDERPOWERED — do not run the gate.** 947 filled trades against a requirement of 1,554.

But the requirement was computed for two equal arms, and the arms are not equal — the
placebo runs four shifts and carries 2,241 trades. Recomputed with the arms as they
actually are:

| | |
|---|---|
| V3 arm | 947 trades |
| placebo arm | 2,241 trades |
| effect the hypothesis needs | +4.5 pp |
| standard error of the difference | 0.0172 |
| z | 2.62 |
| **actual power** | **74.6%** |
| equal-arm equivalent | 1,331 per arm, i.e. 86% of the 1,554 |

So the shortfall is **about 134 trades, or roughly 7 more symbol-years** — not the 31 the
raw comparison implies. The conservative figure in the tool's own output is an artefact of
assuming equal arms, and is corrected here.

**The one-sample condition is already satisfied:** 947 against the 792 needed to test the
hit rate against its own required rate. Only the paired-difference condition falls short,
and the spec's Stage 1 uses a *paired* block bootstrap sharing time blocks between arms,
which is more efficient than the independent two-sample formula used above. The true
requirement is therefore somewhere below 1,081 and was not computed, because doing so
needs the paired correlation, which needs the V3 arm's outcomes — which would break the
blind.

## 2. The counts

| symbol | signals | unfilled | trades | per year |
|---|---|---|---|---|
| BTC | 147 | 0 | 76 | 19.0 |
| ETH | 152 | 0 | 109 | 27.3 |
| BNB | 93 | 0 | 44 | 11.0 |
| SOL | 101 | 0 | 82 | 20.5 |
| XRP | 151 | 0 | 106 | 26.5 |
| DOGE | 147 | 0 | 119 | 29.8 |
| ADA | 90 | 0 | 74 | 18.5 |
| AVAX | 97 | 0 | 83 | 20.8 |
| LINK | 81 | 0 | 71 | 17.8 |
| LTC | 103 | 0 | 78 | 19.5 |
| DOT | 95 | 0 | 73 | 18.3 |
| TRX | 70 | 0 | 32 | 8.0 |
| **pooled** | **1,327** | **0** | **947** | **19.7** |

- Placebo baseline (time-shifted, 4 shifts): **69.97%** on 2,241 trades.
- Pooled cost-inclusive break-even: **74.47%**, inside the 74.8–76.2% the spec predicted
  (slightly below, so marginally easier than assumed).
- Split by provenance: **593 trades from the eight symbols the 15m parameters were not
  chosen on**, 354 from the four that were. The independence concern §Symbols raised is
  weaker than it might have been.

## 3. Four findings that affect Stage 1 whatever is decided

1. **The synthetic falsification check cannot work as specified.** `data.synthetic()`
   draws volume from a uniform distribution, so no bar can exceed 3× its own 24-bar
   average and **V3 fires zero times on it**. "V3 fails the gate on synthetic" would be
   satisfied for the wrong reason — no signals rather than no edge. A generator with
   volume clustering is needed before that check means anything.

2. **V3 structurally prefers modest volume spikes.** Session VWAP is volume-weighted and
   includes the current bar, so a large spike pulls VWAP toward its own price and cancels
   the deviation the rule is looking for. A test fixture built at 9× produced a deviation
   less than half the threshold; at 3.5× it fired. This is a property of the rule, not a
   bug, and it bears on how the 1H result should be read.

3. **Zero unfilled entries across 1,327 signals.** Every limit at the signal close was hit
   within two bars. Plausible for a mean-reversion entry at a stretched close, but a 100%
   fill rate deserves verification before Stage 2 — it is precisely the assumption the 15m
   paper observer was set up to test, and it has not reported yet.

4. **TRX (32) and BNB (44) are thin.** Stage 1's "point estimate above required on ≥8 of
   12 symbols" would rest partly on symbols whose intervals span ±15 pp or worse. That
   condition may be doing less work than intended.

## 4. The decision

Each of these is a fresh pre-registration, not a patch to this experiment.

1. **Extend history to five years.** ~1,180 trades, clearing 80% power outright. Check
   first that all twelve perpetuals have five years of 1H data — all twelve returned
   exactly 4.0 years here, so some may be listing-constrained.
2. **Re-register the power calculation on the paired design** rather than the independent
   two-sample approximation. The requirement would fall below 1,081 and may already be
   met by the 947 in hand. This costs no new data.
3. **Accept 74.6% power and say so.** Defensible if stated in advance, with the raised
   type-II risk acknowledged.
4. **Add symbols.** Cross-symbol clustering measured near 1.0× on the 15m work, so this
   scales close to linearly — but it dilutes the liquidity tier and changes the slippage
   assumption.
5. **Stop.** The hypothesis needs V3's edge over placebo to be 2–3.5 pp larger on 1H than
   on 15m. Nothing in Stage 0 speaks to whether it is.

Option 2 is the cheapest and the most honest: the requirement quoted in the verdict is
known to be too strict, and fixing the calculation is not the same as loosening the test.

## 5. What was not measured

V3's 1H hit rate, its per-symbol breakdown, the paired difference against the placebo, the
clustering ratio and the block-length sweep. All belong to Stage 1 and the spec forbids
running it. They remain unseen.
