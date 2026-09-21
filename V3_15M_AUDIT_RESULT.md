# MR-70 V3 15m — placebo audit result

Run under `V3_15M_PLACEBO_AUDIT.md`. An audit of an existing claim, not a new experiment.
V3 was untouched: same signal, geometry, costs, symbols (BNB/ETH/SOL/DOGE) and cached 15m
data as the original run. Only the null changed.

## 1. Reproduction check

V3 reproduces: **72.87% on 446 trades** against the original 72.80%. The audit is
measuring the same thing, so everything below is comparable to the original.

## 2. The numbers

| | |
|---|---|
| V3 | 72.87% on 446 trades |
| time-shifted placebo (±1,600, ±3,200 bars) | 70.32% on 529 trades |
| V0 random control | 68.77% on 6,910 trades |
| pooled cost-inclusive break-even | 74.97% |
| **V3 − V0** | **+4.10 pp** |
| **V3 − placebo** | **+2.55 pp**, 95% [−2.40, +7.52] |

Paired block bootstrap, V3 − placebo: [−3.42, +8.06] at 7 days, [−2.40, +7.52] at 28,
[−2.00, +7.51] at 90. The interval spans zero at every block length.

## 3. What this does and does not establish

**Established — the null was inflating the figure.** 1.55 pp of the 4.10 pp, **38% of it**,
disappears when the null is construction-matched. The concern that prompted this audit was
correct, and it is now quantified rather than suspected.

**Not established either way — whether the remaining +2.55 pp is real.** The interval
contains zero. It also contains +3.4 and +4.1. It argues against neither. Power to detect
an effect of that size at these arm sizes is **14%**, and resolving it would need roughly
**4,900 trades per arm** against the 446 / 529 available — about **88 symbol-years** against
the 8 in hand.

**Not in doubt — V3 is not tradeable.** 72.87% against a 74.97% break-even, a 95% lower
bound of 67.62%, i.e. 7.35 pp below the bar. This never depended on the null and is
unchanged by the audit.

## 4. A correction to the spec's reading rule

The audit's pre-registered rule said: *if the interval spans zero, the original +3.4 pp was
not a real edge.* That rule conflates absence of evidence with evidence of absence. It is
the same error the Strategy B note made in v3 and that v4 correctly overturned — an
interval spanning zero at 14% power says the sample cannot resolve the effect, not that the
effect is absent.

I followed the rule's computation and then declined its conclusion, reporting **unsupported
rather than refuted**. `mr70/v3_15m_audit.py` was corrected to print the power and the
required sample instead of issuing that verdict. Flagging it because the rule was
pre-registered, and departing from a pre-registered reading is exactly the kind of thing
that should be stated rather than quietly done.

## 5. A provenance note

This run measures **+4.10 pp** against V0 on BNB/ETH/SOL/DOGE. The **+3.40 pp** recorded in
`PROJECT_CONCLUSIONS.md` came from the fresh-symbols follow-up on BTC/AVAX/XRP/ADA. Both
are V0 comparisons and both carry the same class of inflation, but they are different
measurements on different symbol sets. The conclusions document now states this run's
figure rather than reconciling the two silently.

## 6. The part that generalises

**Every V0-based comparison in the MR-70 work is inflated by something like this margin.**
V0 offers a random direction on every eligible bar with V3's TP/SL geometry, but it does
not reproduce V3's entry construction — and that difference was worth 1.55 pp here and 7 pp
in the Strategy B cascade. Any MR-70 claim of the form "variant X beats random by Y pp"
should be read as an upper bound until re-measured against a construction-matched null.

The cost of doing that is low: `shifted_signals()` exists, the data is cached, and the
audit script generalises to any variant by swapping one function.

## 7. The decision

1. **Stop here.** V3 is not tradeable on 15m or 1H, and the open question — is there a
   small real edge underneath — needs 88 symbol-years to answer and changes nothing if
   answered. This is the honest default.
2. **Re-audit the other MR-70 variants** against the placebo. Cheap, and it would establish
   whether V0 inflated V1/V2/V4 as well. Worth doing only if those results are going to be
   cited again; otherwise it is tidying up a closed drawer.
3. **Retire V0 as a null** in anything future, in favour of the time-shifted placebo. This
   costs nothing and prevents the next strategy inheriting the same flaw.

Option 3 is free and should happen regardless. Between 1 and 2, the deciding question is
whether any MR-70 number will be quoted again — if not, option 1.
