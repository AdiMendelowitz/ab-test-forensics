# Case 01: The lift we could not trust

## Decision and executive summary

Recommendation: do not ship. Stop the experiment, repair the redesigned widget's analytics emitter, A/A-validate the exposure pipeline, and rerun the experiment under the same pre-declared design with the fix in place.

A redesigned checkout widget is on a 50/50 user-randomised test planned to enrol for 21 days; this case is the interim look, which reads the 280,000 eligible users enrolled in the first 14 days once their outcomes are complete. In the committed run (one simulated experiment, seed 20260928, chosen by a stated rule as the first seed whose run shows this story; a random seed shows it 22.4% of the time), the team dashboard on day 14 showed a +6.4% relative lift in the 7-day completed-order rate (95% interval +1.6% to +11.5%, p = 0.009), clearing the ship rule. The dashboard compares users with a logged render event, and the redesigned widget's emitter loses that event for about 35% of the rendered treatment users on Android below version 8.4, the lowest-converting segment, so the exposed treatment population is enriched with better converters. The assignment-level sample-ratio check passes (chi-square 0.38); the exposure-level check fails (chi-square 85.6). The intent-to-treat estimate on the assigned population is +1.9% (interval −2.2% to +6.1%, p = 0.36), whose interval falls in the rule's continue zone; the rule itself gives no decision while the exposure and latency blockers are open. Across 5,000 simulated replicates of this incident, the day-14 dashboard's interval covers its own target 89.5% of the time (Monte Carlo SE 0.4) and the decision-relevant ITT lift 85.8% (SE 0.5), against 94.7% (SE 0.3) for the intent-to-treat interval; under a true null the dashboard rejects 10.7% of the time (SE 0.4) at a nominal 5%, and reports a significant positive lift 10.2% of the time against 2.6% with no incident (scenario A).

## Units and estimand

| Unit | Definition |
|---|---|
| Randomisation unit | User, by deterministic hash of user id at first eligibility |
| Eligibility unit | User requesting the checkout page in the experiment window |
| Exposure unit | User for whom the checkout widget (legacy in control, redesigned in treatment) emitted a render event |
| Outcome-observation unit | User; any completed order within 7 days of first eligibility |
| Analysis unit | User |
| Clustering unit | None assumed (one device per user; cross-device identity is an open risk) |

Primary estimand: intent-to-treat difference in expected 7-day order rate among eligible users, E[Y(1) − Y(0)], reported absolute and as a relative lift over control.
Secondary estimand (the dashboard's): the same difference among users whose widget renders. For about 15% of eligible users, in both arms alike, the page falls back to the server-rendered legacy checkout and no widget renders; they can still order but cannot be affected by the widget, so the ITT effect is the rendered-user effect diluted by them (+2.55% against +3.0%). A triggered analysis of rendered users is valid when the render event is logged the same way in both arms, and it is then more powerful against zero than ITT (scenario C below). Here the render event is lost differentially, so the logged-exposed population targets neither estimand. Removing the affected segment from both arms, a pre-treatment exclusion, restores a valid triggered comparison for rendered users outside that segment (about 76% of eligible users); its estimand is the lift among them, on the triggered scale.
Metric: 7-day completed-order rate per eligible user, orders counted when received by the analysis clock.
Estimator: difference in arm-level user proportions, unpooled Wald interval, and a delta-method interval on the log ratio, exponentiated for the relative lift (`difference_in_proportions`).

Timeline: enrolment runs for 21 days. The interim look reads the users enrolled on days 1 to 14 once their outcomes are complete (24 March, 12:00); the final look reads all 21 days of users when theirs are (31 March, 12:00). The final-look decision itself waits for the refund guardrail: its last cohort matures on 13 April, 12:00, so the primary metric is read on 31 March and the decision is taken on 13 April. The interim look reads refunds on the orders matured by its clock.

Decision rule (the case treats it as pre-declared; section 16 of the spec records which parts were added or revised during the build), checked in this order at each look:

1. Stop if any guardrail breaches.
2. No decision while a decision blocker is open (an exposure or assignment issue, a guardrail that cannot be measured, or, at the final look, a guardrail whose outcome window has not closed); investigate first.
3. Stop if the upper 95% bound on the relative ITT lift is below 0.
4. Ship if the lower bound on the relative ITT lift exceeds +1.0% (95% interval at the interim look, 97.5% at the final look, z = 2.24) and the must-pass guardrails pass.
5. Otherwise, at the interim look: continue to the final look.
6. Otherwise, at the final look: do not ship; the lift is not shown to exceed the +1.0% floor.

Guardrails, each against a non-inferiority margin on the treatment-minus-control difference (`config.yaml`): payment-step errors +0.3 points and p95 latency +5% relative, both of which must pass; refund rate +10% relative, which is breach-only. A guardrail passes when the upper 95% bound is at or below its margin, breaches when the lower 95% bound is above it, and is inconclusive otherwise. At no true effect the payment-step guardrail passes about 95% of the time at this sample size. A +10% refund margin would pass a harmless change only about 18% of the time on a complete 14-day cohort (about 4,500 orders per arm at a 5% refund rate), and 80% of the time would need about 33,000 orders per arm, so the refund guardrail can stop a launch but is not required to pass. +10% is the harm tolerance: at the +1.0% ship floor, a refund increase above +18.8% would cancel the gain in kept orders. During the review the refund margin was briefly raised to +25% so that a pass was reachable, then restored to +10% as breach-only; this run's refund verdict (inconclusive, no breach) is the same under every version.

The final look has a stated cost. With the interim look at z = 1.96 and the final look at z = 2.24 (correlation sqrt(14/21) between the two statistics, treating information as proportional to enrolled users), the probability of shipping when the true relative ITT lift is exactly +1.0% is 3.0% over both looks, against 2.5% for a single look and 3.85% if the final look reused z = 1.96. These are asymptotic and upper bounds, since the stop and guardrail conditions only remove ship outcomes. The two-sided analogue is 6.0% (7.7% with 1.96 at both of these unequal looks; Armitage, McPherson and Rowe, 1969, give 8.3% for two equally spaced looks). An O'Brien-Fleming boundary (z = 2.45 at day 14, 2.00 at day 21) would hold 2.5%, at the price of a stricter first look. Monitoring policy is the subject of a Phase 2 case.

Power: at 140,000 users per arm a 6% relative lift has 81% power to reject zero, while the ship rule asks the lower bound to clear +1.0%; against that margin power at a 6% lift is 65.5%, and 80% would need about 197,000 users per arm. The design keeps 140,000 and states the 65.5% (`docs/design/case-01.md`, section 5). The simulated effect is +3.0% among rendered users and +2.55% intent-to-treat after dilution. At those effects the ITT ship-rule rate at the interim look is 11.1% (Monte Carlo SE 0.4; analytic 11.1%), and even a clean rerun would clear the bar at either look about one time in seven (14%, analytic, on the primary metric alone). Confirming a +2.55% lift against the +1.0% floor with 80% probability would need about 2 million users per arm, roughly seven months of traffic. An inconclusive ITT result is therefore the expected outcome, and it is not evidence against the redesign; the product question it raises is whether a lift of this size is worth confirming with this traffic.

## Data provenance

All data is simulated by `abforensics.simulation.checkout_incident` (`abf simulate case01`). Seed, the true effect (+3.0% among rendered users; the ITT truth, +2.55%, is `expected_relative_lift_itt`), every incident parameter, row counts and content checksums are in `data/manifests/case01.json`. The case seed was chosen by a stated rule, recorded in `config.yaml` and re-checked by `tests/simulation/test_case_seed.py`: the first seed from 20260928 upwards whose single run shows the case's story (the day-14 dashboard clears the ship rule, the complete-data ITT interval lands in the continue zone, the assignment SRM check passes). The rule was written during the generator rework after 20260928 failed it under an intermediate version; under the final generator 20260928, the first candidate, meets it. Across replicates of the incident the story appears 22.4% of the time (MC SE 0.6), so the single run illustrates the mechanism in a run selected to show it, and every rate and bias claim comes from the operating characteristics below. Under the pre-rework generator, seed 20260928 reproduced all seven content checksums on Windows (CPython 3.14.0) and Linux (CPython 3.14.7) with the locked library versions. The current manifest and the operating characteristics were produced on Linux (CPython 3.14.0rc2, recorded in `oc_meta.json`). A second Linux machine (CPython 3.14.7) reproduced all seven manifest checksums and a hash of the first 20 replicates of the effect scenarios byte for byte; the Windows check is still pending. The simulation generates only the cohorts the interim look reads (days 1 to 14). The incident is a plausible telemetry mechanism and is described as such throughout. The notebook never reads the `truth` table, which is written for audit only. See `docs/design/case-01.md` for the design this was built against and `data_contract.md` for the tables.

## Diagnostic evidence

Figures 1 to 3 and the numbers in this section come from the single committed run (seed 20260928).

![Exposure gap by segment](figures/fig1_exposure_gap_by_segment.png)

Treatment-minus-control exposure rate by platform and app version. The gap is confined to Android below 8.4 on the treatment side, at 30 to 31 points of those users (35% of the rendered ones); every other cell is at parity. Both arms also have about 15% of users with no render event, the fallback share, equal by arm.

![Affected-cell share by population](figures/fig2_affected_cell_share.png)

Share of users on Android below 8.4, a pre-treatment attribute, per arm. Balanced on the assigned population (10.07% control, 10.00% treatment), unbalanced on the exposed population (10.07% against 6.69%). The pre-period order count on the exposed population shifts in the expected direction without being flagged (p = 0.28 against a 0.001 threshold): the excess lost users are 3.6% of rendered treatment users (3.0% of the arm) and average 0.36 pre-period orders against 0.59, an expected shift of 0.008 against a standard error of 0.007. Composition invariants catch what outcome-proxy invariants miss at this magnitude.

![Exposed versus ITT](figures/fig3_naive_vs_itt.png)

The exposed-population and ITT estimates on complete data against the +1.0% ship threshold (the exposed estimate is +5.5% at the data-complete date and +6.4% on the day-14 dashboard). In this run the gap between them is 3.6 points. Across all replicates of scenario D the mean gap is 2.18 points: 1.76 points of upward bias in the exposed estimate against its own target, plus 0.45 points because the two estimators target different lifts (3.0% and 2.55%), less the ITT estimate's own +0.03 (SE 0.03) of Monte Carlo noise. Among the replicates that show the case's story the mean gap is 2.75 points and the mean ITT estimate 3.8% against a 2.55% truth, so the size of this run's gap reflects how the seed was chosen as well as the bias.

## Operating characteristics

`abf oc case01` re-runs the generator with a fresh assignment salt on every replicate and applies four estimators: the dashboard as read on day 14, the same exposed population on complete data, the exposed population with the affected segment removed from both arms, and ITT. Each is scored against its own target (the rendered-user lift for the exposure-based estimators, the all-eligible lift for ITT) and against the ITT lift, the decision estimand. B = 5,000 replicates per scenario and per loss-sweep point, 1,000 per conversion-deficit point. Every rate carries its Monte Carlo SE, sqrt(r(1 − r)/B), and a Clopper-Pearson 95% interval where a rate is 0% or 100%; every bias carries sd/sqrt(B). The run took 21 minutes of unit time on a two-core Linux machine. Tables: `results/case01/oc_*.csv`, with run metadata in `oc_meta.json`.

| Scenario | Estimator | True lift | Bias, own target (points of relative lift) | Bias against ITT lift | Coverage, own target | Rejects zero | Ship-rule rate | Exposure SRM flagged (alpha 0.001) |
|---|---|---|---|---|---|---|---|---|
| A: null, no incident | dashboard, day 14 | +0.00% | +0.03 (0.03) | +0.03 (0.03) | 95.0% (0.3) | 5.0% (0.3) | 0.96% (0.14) | 0.12% (0.05) |
| A: null, no incident | exposed, complete data | +0.00% | +0.03 (0.03) | +0.03 (0.03) | 94.9% (0.3) | 5.1% (0.3) | 1.1% (0.1) | 0.12% (0.05) |
| A: null, no incident | exposed, segment removed | +0.00% | +0.03 (0.03) | +0.03 (0.03) | 94.9% (0.3) | 5.1% (0.3) | 1.0% (0.1) | 0.12% (0.05) |
| A: null, no incident | ITT | +0.00% | +0.03 (0.03) | +0.03 (0.03) | 94.9% (0.3) | 5.1% (0.3) | 0.92% (0.14) | 0.12% (0.05) |
| B: null, incident | dashboard, day 14 | +0.00% | +1.70 (0.03) | +1.70 (0.03) | 89.3% (0.4) | 10.7% (0.4) | 4.5% (0.3) | 100% (CI ≥99.92%) |
| B: null, incident | exposed, complete data | +0.00% | +1.71 (0.03) | +1.71 (0.03) | 88.5% (0.5) | 11.5% (0.5) | 5.0% (0.3) | 100% (CI ≥99.92%) |
| B: null, incident | exposed, segment removed | +0.00% | +0.03 (0.03) | +0.03 (0.03) | 94.9% (0.3) | 5.1% (0.3) | 1.0% (0.1) | 100% (CI ≥99.92%) |
| B: null, incident | ITT | +0.00% | +0.03 (0.03) | +0.03 (0.03) | 94.9% (0.3) | 5.1% (0.3) | 0.92% (0.14) | 100% (CI ≥99.92%) |
| C: effect, no incident | dashboard, day 14 | +3.00% | +0.02 (0.03) | +0.47 (0.03) | 95.2% (0.3) | 23.6% (0.6) | 12.7% (0.5) | 0.12% (0.05) |
| C: effect, no incident | exposed, complete data | +3.00% | +0.03 (0.03) | +0.48 (0.03) | 95.0% (0.3) | 26.1% (0.6) | 14.0% (0.5) | 0.12% (0.05) |
| C: effect, no incident | exposed, segment removed | +3.00% | +0.03 (0.03) | +0.48 (0.03) | 95.1% (0.3) | 24.9% (0.6) | 13.4% (0.5) | 0.12% (0.05) |
| C: effect, no incident | ITT | +2.55% | +0.03 (0.03) | +0.03 (0.03) | 94.7% (0.3) | 23.2% (0.6) | 11.1% (0.4) | 0.12% (0.05) |
| D: effect, incident | dashboard, day 14 | +3.00% | +1.75 (0.03) | +2.20 (0.03) | 89.5% (0.4) | 49.3% (0.7) | 33.0% (0.7) | 100% (CI ≥99.92%) |
| D: effect, incident | exposed, complete data | +3.00% | +1.76 (0.03) | +2.21 (0.03) | 88.3% (0.5) | 53.4% (0.7) | 36.0% (0.7) | 100% (CI ≥99.92%) |
| D: effect, incident | exposed, segment removed | +3.00% | +0.03 (0.03) | +0.48 (0.03) | 95.1% (0.3) | 24.9% (0.6) | 13.4% (0.5) | 100% (CI ≥99.92%) |
| D: effect, incident | ITT | +2.55% | +0.03 (0.03) | +0.03 (0.03) | 94.7% (0.3) | 23.2% (0.6) | 11.1% (0.4) | 100% (CI ≥99.92%) |
| F: effect, loss in both arms | dashboard, day 14 | +3.00% | +0.02 (0.03) | +0.47 (0.03) | 95.3% (0.3) | 23.4% (0.6) | 12.4% (0.5) | 0.10% (0.04) |
| F: effect, loss in both arms | exposed, complete data | +3.00% | +0.03 (0.03) | +0.48 (0.03) | 94.9% (0.3) | 25.9% (0.6) | 13.7% (0.5) | 0.10% (0.04) |
| F: effect, loss in both arms | exposed, segment removed | +3.00% | +0.03 (0.03) | +0.48 (0.03) | 95.1% (0.3) | 24.9% (0.6) | 13.4% (0.5) | 0.10% (0.04) |
| F: effect, loss in both arms | ITT | +2.55% | +0.03 (0.03) | +0.03 (0.03) | 94.7% (0.3) | 23.2% (0.6) | 11.1% (0.4) | 0.10% (0.04) |

Monte Carlo standard errors in brackets. Rows share replicates by design (common random numbers): the segment-excluded and ITT rows are identical within A and B and within C, D and F, because the incident never touches those analyses. Differences between rows are therefore read from paired contrasts, whose Monte Carlo SE is 47% to 81% of the unpaired SE for rates and far smaller for the bias contrast:

| Contrast | Difference | Paired MC SE |
|---|---|---|
| C: exposed minus ITT, rejects zero (26.1% against 23.2%) | +2.9 percentage points | 0.4 |
| C: exposed minus ITT, ship rule (14.0% against 11.1%) | +2.9 percentage points | 0.4 |
| C: exposed (bound rescaled to the ITT lift) minus ITT, ship rule (12.3% against 11.1%) | +1.2 percentage points | 0.4 |
| B minus A: dashboard rejects zero (10.7% against 5.0%) | +5.7 percentage points | 0.4 |
| D minus C: dashboard ship rule (33.0% against 12.7%) | +20.3 percentage points | 0.6 |
| D minus C: dashboard coverage (89.5% against 95.2%) | −5.7 percentage points | 0.4 |
| D: dashboard day 14 minus exposed complete, ship rule (33.0% against 36.0%) | −3.1 percentage points | 0.4 |
| D: segment-excluded minus ITT, rejects zero (24.9% against 23.2%) | +1.7 percentage points | 0.5 |
| F minus C: exposed bias (+0.028 against +0.030 points of relative lift) | −0.002 points | 0.005 |

The assignment-level SRM check flags 4.7% of replicates at alpha = 0.05 (SE 0.3), consistent with its nominal rate, and 0.06% at alpha = 0.001 (Clopper-Pearson interval 0.01% to 0.18%).

Scenario C measures the claim that triggered analysis is legitimate and more powerful: with logging intact the exposed estimator is unbiased for its own target and rejects zero 2.9 points more often than ITT (paired SE 0.4). Its ship-rule rate exceeds ITT's by 2.9 points (paired SE 0.4), and part of that is a change of scale: the +1.0% margin is defined on the ITT lift, and the triggered lift is larger by the dilution factor. With the triggered lower bound rescaled to the ITT lift (multiplied by the replicate's true dilution factor, 0.850; an estimate from the control arm would differ by about 0.5% relative) the excess is 1.2 points (paired SE 0.4), the part that is power; the remaining 1.7 points come from applying an ITT margin to a triggered estimate.

In scenario D, the incident, the dashboard's ship-rule rate rises by 20.3 points over C (paired SE 0.6), all of it caused by the incident, because C and D analyse the same simulated experiments and differ only in which render events are logged. Read on day 14 the dashboard ships 3.1 points less often than the same comparison on complete data (paired SE 0.4). Removing the affected segment from both arms removes the bias: that estimator is unbiased for its own target in D (bias +0.03 points, SE 0.03) and rejects zero 1.7 points more often than ITT (paired SE 0.5). The story the committed run shows (day-14 dashboard ships, ITT continues, assignment SRM passes) occurs in 22.4% of scenario-D replicates (SE 0.6); given that the dashboard ships, ITT lands in the continue zone 67.9% of the time.

Scenario F loses render events in both arms at the same rate: the exposure SRM check flags 4.3% of replicates at alpha = 0.05 (SE 0.3), close to its nominal 5%, and the exposed estimate's bias differs from C's by −0.002 points (paired SE 0.005). With a constant relative effect the estimate stays valid for the users whose events survive, and the check has nothing to detect.

![Operating characteristics against the loss probability](figures/fig4_operating_characteristics.png)

Sweeping the loss probability from 0.05 to 0.50 (true lift 3%, B = 5,000 per point): the day-14 dashboard's bias against its own target grows linearly, from +0.26 to +2.53 points (MC SE 0.04 or less), and its coverage falls from 95.0% (SE 0.3) to 83.1% (SE 0.5); the segment-excluded estimator stays unbiased throughout. The exposure-SRM check at alpha = 0.001 flags a 5% loss in 1.8% of replicates (SE 0.2), a 10% loss in 19.4% (SE 0.6) and a 15% loss in 65.5% (SE 0.7); it catches almost every run from a 20% loss (95.2%, SE 0.3). A 10% loss, which the check misses 80.6% of the time, still moves the dashboard by 0.50 points against its own target and 0.95 points against the ITT lift. Sweeping the affected cell's conversion deficit (`affected_cell_logit_offset`, −0.6 to 0, B = 1,000 per point) moves the dashboard's bias from +2.36 to +1.19 points (MC SE 0.08 or less): even with no logit deficit the cell converts less (Android's platform offset and fewer pre-period orders), so the bias persists at a zero offset.

## Result and recommendation

Single run, seed 20260928 (the committed data, selected by the rule above); rates across replicates are in the operating-characteristics section.

| Population and clock | Relative lift | 95% interval | p | Zone of the interval against the +1.0% floor (ignoring blockers) |
|---|---|---|---|---|
| Exposed, dashboard on day 14 | +6.4% | +1.6% to +11.5% | 0.009 | ship |
| Exposed, complete data | +5.5% | +0.96% to +10.3% | 0.017 | continue |
| Exposed, affected segment removed from both arms, complete data | +3.8% | −0.7% to +8.6% | 0.10 | not comparable (triggered scale) |
| Assigned (pre-registered ITT), complete data | +1.9% | −2.2% to +6.1% | 0.36 | continue |

Under the ordered rule the outcome of this interim look is no decision: the exposure and latency blockers (rule 2) come before any reading of the estimate.

Guardrails, against the pre-declared margins:

- Payment-step errors, per assigned user (the randomisation unit): 5.12% of treatment users and 5.16% of control users had a payment-step load that raised an error (difference −0.03 points, interval −0.20 to +0.13, p = 0.70); the upper bound is below the +0.3-point margin, so the guardrail passes. Loads per user are equal by arm (1.30).
- Latency: the p95 ratio on measured loads is +0.3% (interval −0.2% to +0.8%) overall and +0.1% (−1.6% to +1.8%) within the affected segment (user-clustered bootstrap, 2,000 resamples), both inside the +5% margin. The affected segment is 6.6% of measured treatment loads against 10.1% of control; its p95 is within about 1% of the rest, so the different mix does not move the overall ratio here. The timing event rides on the render event, so it is missing for 45% of affected treatment loads against 15% elsewhere. In this simulation the loss is random within the segment, so the measured loads represent it; the incident described (users navigating away within the first second) would tie the loss to behaviour, and until the mechanism is verified the affected-segment latency stays a blocker.
- Refunds: a refund can land up to 14 days after the order, so at the data-complete date (24 March) the window is open for 45% of orders and counting refunds seen so far gives 4.63%, an understatement. On the matured orders the rates are 5.04% in treatment and 4.98% in control (relative +1.4%, interval −20.5% to +29.3%, p = 0.91). The lower bound is below the +10% margin, so there is no breach so far; the interval also spans the margin, which is why this guardrail is breach-only. For the interim cohorts it is complete by design on 6 April 2026, 12 and a half days after the rest of their data (this run's last order matures on 4 April), and the matured orders come only from users enrolled in the first 8.4 days. For the final look's cohorts the date is 13 April. The slowest guardrail sets the date of a complete decision.

Recommendation: do not ship. Stop the experiment, whose remaining enrolment carries the same defect; fix the emitter on Android below 8.4, run an A/A test of the exposure pipeline to confirm parity, and rerun the experiment with the fix in place under the same pre-declared design. The ITT estimate from this run is valid and inconclusive. It is not pooled with the rerun: a restart after the fix does not form the contiguous final look the boundary assumes, and the latency guardrail for these cohorts cannot be recovered. The segment-excluded estimate is valid for rendered users outside the affected segment and does not answer the decision about all users. Before rerunning, the product lead should accept that a lift of the simulated size would clear the bar only about one time in seven. Evidence that would change the decision: exposure parity across arms after the fix, a lower ITT bound above +1.0% at the interim (95%) or final (97.5%) look, latency within its margin with a verified logging mechanism, and no refund breach on the complete cohort.

## Decision blockers

- [ ] Assignment issue unresolved: none, assignment-level counts and invariants pass
- [x] Exposure measurement issue unresolved: treatment-side render event lost for Android below 8.4
- [ ] Outcome logging issue unresolved: batch delay is non-differential; handled by using the completion clock
- [x] Missingness may be differential: on exposure only
- [x] Guardrail unmeasurable or not yet mature: latency missing for 45% of affected treatment loads under an unverified loss mechanism; refunds read on the matured cohort only until 6 April (13 April for the final look)
- [ ] Duration insufficient for delayed outcomes: none once the completion clock is used

## Assumptions and unresolved risks

The size of the dashboard's bias follows from data-generating constants, none of them sourced from a real product: the affected cell converts at about half the rate of the rest (1.74% against 3.34% in the control arm of this run; logit offsets −0.30 for Android and −0.30 for versions below 8.4, plus fewer pre-period orders), it is about 10% of users, it loses 35% of rendered treatment exposures, and 15% of users never render the widget. The two sweeps above show how the bias moves with the loss probability and the conversion deficit. The loss is random within the affected cell; a loss that selected on behaviour (fast navigators who convert less or more) would change the bias and is the cheapest assumption for a reviewer to challenge. The platform sees server-side renders only as aggregate counts per platform, version and day, so a user-level re-triggering on server renders is unavailable. One device per user; no interference between users; server-side order logging is intact (the incident is client-side only); hash-based assignment is uniform; the 7-day window captures the effect; the treatment effect is a constant relative lift among rendered users and zero for the rest. No covariate adjustment is used, although pre-period orders predict conversion; variance reduction (CUPED) is a Phase 2 case. Open risks outside this model: cross-device identity, asymmetric bot filtering, novelty effects. The refund comparison conditions on having ordered, a post-treatment event, which is harmless here only because the simulated effect on refunds is zero.

## Reproduce

```
uv run abf simulate case01
uv run abf run case01
uv run abf oc case01
```

`abf oc` caches each unit of work under `results/case01/parts/`, keyed by a hash of its inputs, the library versions and the source of the simulation and estimation modules, and resumes if interrupted; `--max-seconds` splits a long run between units.
