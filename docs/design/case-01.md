# Case 01 design specification: The lift we could not trust

Repository: ab-test-forensics, `case_studies/01_lift_we_could_not_trust/`
Status: v1.4, 29 September 2026. Written 28 September as the contract the code was built against; Case 01 is implemented (`abf simulate`, `abf run`, `abf oc`), and section 16 records the changes made after the build.

## 1. Executive summary

A checkout redesign appears to lift completed orders by about 6% with a conventionally significant p-value on the team's experiment dashboard. The dashboard analyses users with a logged render event, because the checkout widget only renders for users who reach it. Exposure logging for the redesigned widget fails for a fraction of Android users on older app versions, so the analysed treatment population is no longer comparable to the assigned population. Assignment-level counts pass the sample-ratio check; exposure-level counts fail it. The intent-to-treat estimate on the assigned population is valid and inconclusive under the pre-declared decision rule, the latency guardrail cannot be measured for the affected segment, and the refund guardrail is still maturing. The decision is to hold the redesign: repair the instrumentation, validate it with an A/A run, then rerun the experiment under the same design.

The case teaches one thing: a significant result is uninterpretable until the population that was analysed is shown to be the population that was randomised.

## 2. Business decision

Decision owner: checkout product lead.
Action space: ship to 100%, continue to the final look, stop, or take no decision and investigate.
Timeline: enrolment runs for 21 days. The interim look reads the users enrolled on days 1 to 14 once their outcomes are complete (day 22.5, 24 March 12:00); the final look reads all 21 days of users once theirs are (day 29.5, 31 March 12:00). The final-look decision waits for the refund guardrail, whose last cohort matures on 13 April 12:00: the primary metric is read on 31 March and the decision is taken on 13 April. The boundary calculation is unchanged, because the primary metric is not reread.
Decision rule. The case treats it as pre-declared; in the build, its ordering, the guardrail verdict rule, the final-look boundary and the refund guardrail's form were added or revised on 29 September, after the committed run had been inspected (section 16). None of these revisions changes this run's outcome.

Rows are checked in order at each look, so every outcome maps to exactly one action:

| Order | Condition | Action |
|---|---|---|
| 1 | Any guardrail breaches | Stop |
| 2 | Any open decision blocker (section 9): an exposure or assignment issue, a guardrail that cannot be measured, or, at the final look, a guardrail whose outcome window has not closed | No decision; investigate first |
| 3 | Upper bound of the 95% interval on relative ITT lift below 0 | Stop |
| 4 | Lower bound on relative ITT lift above +1.0% (95% interval at the interim look, 97.5% at the final look, z = 2.24) and the must-pass guardrails pass | Ship |
| 5 | Otherwise, at the interim look | Continue to the final look |
| 6 | Otherwise, at the final look | Do not ship: the lift is not shown to exceed the +1.0% floor |

The cost of the final look is stated. With the interim look at z = 1.96 and the final look at z = 2.24 (correlation sqrt(14/21) between the two statistics, treating information as proportional to enrolled users), the probability of shipping when the true relative ITT lift is exactly +1.0% is 3.0% over both looks, against 2.5% for a single look and 3.85% if the final look reused z = 1.96. These figures are asymptotic and are upper bounds, since the stop and guardrail conditions only remove ship outcomes. The two-sided analogue is 6.0%, or 7.7% with z = 1.96 at both of these unequal looks; Armitage, McPherson and Rowe (1969) give 8.3% for two equally spaced looks at 1.96. An O'Brien-Fleming boundary (z = 2.45 at the interim look, 2.00 at the final look) would hold 2.5% at the price of a stricter interim look; the design keeps the conventional interim look because the dashboard reads a 95% interval.

The 1.0% threshold is the practical significance floor: the minimum relative lift that justifies the maintenance cost of the new widget. It is a product input set by the product lead, and the README says so. The guardrail margins below are product inputs of the same kind.

## 3. Unit hierarchy

| Unit | Definition |
|---|---|
| Randomisation unit | User, by deterministic hash of user_id at first eligibility |
| Eligibility unit | User who requests the checkout page during the experiment window |
| Exposure unit | User for whom the checkout widget (legacy in control, redesigned in treatment) emits a render event; users served the server-rendered fallback have none in either arm |
| Outcome-observation unit | User; outcome is any completed order within 7 days of first eligibility |
| Analysis unit | User |
| Clustering unit | None assumed (one device per user). Cross-device identity is listed as an unresolved risk and left out of the v1 model |

## 4. Metric, estimand, estimator, decision rule

Primary metric: 7-day completed-order rate per eligible assigned user.

Estimand (primary): the intent-to-treat difference in expected 7-day completed-order rate among eligible users, E[Y(1) − Y(0)], reported as an absolute difference and as a relative lift over the control rate.

Estimand (secondary, the one the dashboard targets without saying so): the same difference among users whose widget renders. It exceeds the ITT lift by the dilution factor, because users served the fallback cannot be affected (3.0% against 2.55% here). The logged-exposed population targets it only when exposure is logged independently of arm, which is exactly what the incident breaks. The README states both estimands side by side and names which one each number refers to.

Estimator: difference in arm-level user proportions with a Wald interval, and a delta-method interval on the log ratio for the relative lift, computed on the assigned population for the primary estimand and on the exposed population for the secondary one. No covariate adjustment in this case.

Guardrail metrics, each with a non-inferiority margin (`config.yaml`, `decision_rule.guardrails`) on the treatment-minus-control difference: a guardrail passes when the upper 95% bound is at or below its margin, breaches when the lower 95% bound is above it, and is inconclusive otherwise. The payment-step and latency guardrails must pass for a ship; at no true effect the payment-step guardrail passes about 95% of the time at 140,000 users per arm. The refund guardrail is breach-only: its +10% margin is the harm tolerance (at the +1.0% ship floor, a refund increase above +18.8% would cancel the gain in kept orders), and passing it at no true effect happens only about 18% of the time on a complete 14-day cohort (about 4,500 orders per arm at a 5% refund rate) and 25% on a 21-day cohort, with 80% needing about 33,000 orders per arm. A guardrail that must pass but cannot is a design error, so this one can stop a launch without being required to pass. During the review the refund margin was briefly raised to +25% so that a pass was reachable, then restored to +10% as breach-only; this run's refund verdict (inconclusive, no breach) is the same under every version.

- Payment-step errors: share of assigned users with at least one load of the payment step that raised a client error, through the widget or the fallback, margin +0.3 percentage points. A load is a visit to the payment step and is not a payment, so most loads end without an order. Every assigned user is in the denominator, so no post-assignment filter enters it; loads per user are reported by arm. The per-load error rate is a ratio metric and belongs to Case 02.
- Latency: ratio of treatment to control p95 checkout latency from the widget's own timing event, with a 95% interval from a bootstrap that resamples users, overall and within the affected segment, margin +5% relative. The guardrail depends on the exposure event pipeline, and when exposure logging fails the guardrail fails with it.
- Refunds: 14-day refund rate per order, margin +10% relative, breach-only. A refund can land up to 14 days after the order, so at the analysis clock the window is still open for every order placed in the last 14 days, and a guardrail whose window is open is unmeasured. It is reported on the matured cohort (orders whose window has closed by the analysis clock), with the share of orders that cohort covers, the enrolment period it represents and the design date on which it becomes complete: the end of the last user's outcome window plus 14 days, 6 April 2026 for the interim look's users and 13 April for the final look's. The slowest guardrail sets the earliest date on which a complete decision can be taken. With one order per converter in v1, refund per order conditions on having ordered, a post-treatment event; that is harmless only while the effect on refunds is zero, and the README flags it.

Invariant metrics (must not differ by arm on the assigned population): assigned user counts, platform mix, app-version mix, country mix, pre-period 28-day order rate.

## 5. Experiment design

Allocation 50/50. Enrolment runs 21 days, three full weekly cycles, with an interim look at the users of the first 14 days (section 2). Eligible users arrive at roughly 20,000 per day; the simulation generates only the 14 days of cohorts the interim look reads.

Power, computed with the delta-method interval on the log ratio that the rule reads, at a baseline control rate of 3.2% per eligible user and α = 0.05 two-sided. A 6% relative effect (0.19 percentage points absolute) has 80% power against zero at about 136,000 users per arm (78.9% at 132,000). The ship rule asks more than a nonzero effect: the lower bound of the relative interval must clear +1.0%. Against that margin, power at a 6% lift is 63.0% at 132,000 per arm and 65.5% at the 140,000 per arm the interim look reads, and 80% needs about 197,000 per arm. Decided 29 September 2026: the sample size stays at 140,000 per arm and the design states 65.5% against the margin, because the case is about validity and a larger N would teach nothing further. At the simulated effects the probability of clearing the margin is 11.1% for the ITT analysis (2.55% on 140,000 per arm; measured 11.1%, Monte Carlo SE 0.4, scenario C) and 13.9% for a triggered analysis (3.0% on about 119,000 per arm; measured 14.0%), and power against zero is 22.9% and 26.2% respectively (measured 23.2% and 26.1%). Over both looks the ITT analysis would clear the margin about 14% of the time, and 80% against the margin at a +2.55% ITT lift would need about 2 million users per arm, roughly seven months of traffic; at this effect size the design cannot confirm the redesign with this traffic, and the README says so. These figures assume a fixed horizon, independent users and a stable baseline.

## 6. Data provenance and simulation disclosure

All data is simulated. The README says this in its first section. The generator is seeded, logs the true effect τ* and every incident parameter, and writes six long-format tables so that the analysis has to do real joins with logged row counts:

| Table | Grain | Key columns |
|---|---|---|
| users | one row per user | user_id, platform (ios, android, web), app_version, country, pre_period_orders_28d |
| assignments | one row per assigned user | user_id, arm, assigned_at |
| exposures | one row per render event | user_id, exposed_at, widget_version, event_id |
| checkout_attempts | one row per load of the payment step | user_id, attempted_at, payment_step_error, latency_ms |
| orders | one row per order | user_id, order_id, ordered_at, received_at, amount, refunded_at |
| telemetry_health | one row per platform × app_version × day | platform, app_version, day, users, event_loss_rate |

Realism requirements on the data-generating process: baseline conversion differs by platform (Android on old versions lowest, iOS highest, a spread of roughly 2.5 percentage points); pre_period_orders_28d is heavy-tailed (81% of users outside the affected cell and 87% inside it at zero, a long right tail) and predictive of conversion; order events arrive through a batch pipeline with a 0 to 36 hour delay between ordered_at and received_at, identical across arms, so the final day of any window is incomplete for both arms at analysis time.

True treatment effect τ*: +3.0% relative among users whose checkout widget renders, constant across those users in v1. For 15% of eligible users (`non_render_probability`, independent of arm and covariates) the page falls back to the server-rendered legacy checkout; they can load the payment step and order through it but cannot be affected by the widget, so their two potential outcomes are equal and the intent-to-treat effect is diluted to about +2.55% relative. The manifest records both truths (`expected_relative_lift_rendered`, `expected_relative_lift_itt`) and the absolute ITT effect `expected_ate_absolute`. The logged-exposed population is therefore a genuine subset of the assigned one, with its own valid estimand when exposure is logged independently of arm (added 29 September 2026; before that, exposure equalled eligibility by construction, and the claim that triggered analysis is valid and more sensitive was asserted without being shown). Heterogeneous effects are out of scope for this case. τ* is half the 6% MDE the design was sized for, so the ITT interval is expected to be inconclusive (section 5), by design: the case is about validity. With this effect size and incident the day-14 dashboard clears the ship rule in 33.0% of replicates against 11.1% for ITT (scenario D), and the README states this so that nobody reads the inconclusive ITT as a finding about the redesign.

The committed data uses seed 20260928, chosen by a rule stated in `config.yaml` and re-checked by `tests/simulation/test_case_seed.py`: the first seed from 20260928 upwards whose single run shows the case's story (the day-14 dashboard clears the ship rule, the complete-data ITT interval lands in the continue zone, the assignment SRM check passes). The rule was written during the generator rework, after 20260928 failed it under an intermediate version; under the final generator 20260928, the first candidate, meets it. A random seed shows the story 22.4% of the time (Monte Carlo SE 0.6, `results/case01/oc_story.csv`), so the single run is a selected illustration of the mechanism: among story replicates the mean gap between the exposed and ITT estimates is 2.75 points against 2.18 over all replicates. Every rate and bias claim comes from the operating characteristics (section 10), whose seed (20261001) is separate.

## 7. Incident mechanism

The redesigned widget initialises its analytics emitter after first paint. On Android app versions below 8.4 the emitter loses the render event with probability 0.35 among users it renders for, described as users who navigate within the first second. The simulation draws the loss at random within the affected cell, independently of the user's outcome and latency; a loss that selected on behaviour would change the size of the bias, and v1 leaves that out (section 13). The control widget's emitter is unaffected. The platform sees server-side renders only as aggregate counts per platform, app version and day (`telemetry_health`), so the analysis cannot re-trigger on server renders at the user level. Consequences the analysis has to discover, in order:

1. Assigned counts by arm are balanced; exposed counts are not. SRM appears one level below where the standard check looks.
2. The missing treatment exposures are concentrated in the lowest-converting segment (Android, old versions), so the exposed treatment population is enriched with higher-converting users. Across replicates the day-14 dashboard's mean estimate is +4.75% against a 3.0% target (scenario D); the size of the inflation is an output of the runs.
3. The platform and app-version mix, both invariants, are balanced on the assigned population and unbalanced on the exposed population. This is the cleanest single figure in the case. (Build finding, 28 Sep 2026, figures refreshed from the 29 Sep run: the spec originally named the pre-period order rate here. At a 35% loss on a 10% segment, the lost users are about 3.6% of rendered treatment users (3.0% of the arm) and the expected shift in mean pre-period orders is about 1.2 standard errors in the committed run (realised p = 0.28), so that invariant is not flagged at alpha = 0.001. The case shows this explicitly as the difference between composition invariants and outcome-proxy invariants.)
4. Latency for the affected segment is measured on about 55% of its treatment loads against 85% elsewhere. With the loss random within the cell, as simulated, the measured loads still represent the segment; the mechanism described (navigating away within the first second) would tie the loss to behaviour, and until it is verified the segment's latency is a blocker.
5. The batch delay on orders is a second, non-differential missingness mechanism. It produces no SRM and no bias by arm, and the case shows that explicitly so that the reader learns which class of problem SRM catches and which it misses.

Wording rule for the README: "This semi-synthetic incident models a plausible telemetry failure." It is never described as a real outage.

## 8. Diagnostic workflow (the notebook's spine)

Each step is one package call and one figure or table. The order is the order a competent investigation would follow.

1. Assignment SRM: chi-square goodness of fit on assigned counts against 50/50, expected to pass.
2. Exposure SRM: the same test on exposed counts, expected to fail decisively.
3. Exposure rate by arm × platform × app_version: locates the cell. The figure is a heat map of treatment-minus-control exposure rate by segment.
4. Invariant checks on the assigned population (pass) and the exposed population (platform and app-version mix fail; the pre-period order rate moves in the expected direction without being flagged at alpha = 0.001, see section 7, item 3).
5. Order-arrival lag by arm: shows the batch delay is identical across arms and quantifies the incomplete final day.
6. The dashboard's estimate as read on day 14, the same exposed-population estimate on complete data, the exposed estimate with the affected segment removed from both arms, and the ITT estimate on the assigned population, each with a 95% interval, against the 1.0% practical threshold.
7. Guardrails against their margins, then the decision memo.

## 9. Decision blockers

The README carries this checklist filled in.

- Exposure measurement issue unresolved: yes, blocks the exposure-conditioned estimate and the latency guardrail.
- Assignment issue: no.
- Outcome logging issue: final-day incompleteness, non-differential, handled by using the completion clock (`analysis_time`); not a blocker.
- Differential missingness: yes, on exposure only.
- Guardrail breach, unmeasurable or inconclusive: latency missing for about 45% of the affected treatment loads under an unverified loss mechanism, and the refund guardrail on the matured cohort only until 6 April; each unknown is itself a blocker.
- Duration insufficient for delayed outcomes: no, given the 7-day outcome window and 36-hour maximum delay.

## 10. Operating-characteristic validation

`abf oc case01` runs B = 5,000 replicates of the generator per scenario and per loss-sweep point and 1,000 per conversion-deficit point, reporting the Monte Carlo standard error of every rate and bias and a Clopper-Pearson interval where a rate is 0% or 100%. Four estimators (the dashboard as read on day 14, the exposed population on complete data, the exposed population with the affected segment removed from both arms, and ITT on the assigned population), each scored against its own target and against the ITT (decision) estimand, under five scenarios:

| Scenario | τ* (rendered users) | Incident | What it establishes |
|---|---|---|---|
| A | 0 | off | All four estimators hold nominal 5% Type I error and 95% coverage |
| B | 0 | on, treatment | The dashboard's false-positive rate under the null with the incident; ITT stays nominal |
| C | +3% | off | Power of each estimator at the planned N, and the triggered analysis's gain in power |
| D | +3% | on, treatment | Bias and coverage of the dashboard; ITT unbiased |
| F | +3% | on, both arms | Symmetric loss: no exposure SRM, and, with a constant relative effect, no bias for the users whose events survive |

Replicate i uses the same random streams in every scenario (common random numbers), so differences between scenarios are reported as paired contrasts with their own Monte Carlo standard errors. The runner also reports how often a single replicate shows the case's story (section 6) and, for the exposed estimator in scenario C, the ship-rule rate with its lower bound rescaled to the ITT lift, which separates the gain in power from the change of scale. A sweep varies the loss probability from 0.05 to 0.50 in steps of 0.05 and reports the exposure-level SRM detection rate and the dashboard's bias and coverage at each level, so the reader sees how small a logging failure has to be before the chi-square test stops catching it while the bias is still material. A second sweep varies the affected cell's conversion deficit.

### Contrast scenario E: the textbook assignment-level SRM (planned, not yet built)

Decided 28 September 2026: the primary incident stays at the exposure level. The assignment-level version, the 60/40 split every tutorial uses, is planned as a contrast bounded to one table and one paragraph, with no new package function. Mechanism: an eligibility bug excludes a random 20% of treatment users after assignment (for example a feature-flag evaluation that fails on one client and falls back to control). The point of the contrast is the hierarchy it exposes:

| Where the failure sits | Assignment SRM | Exposure SRM | ITT estimand | Exposure-conditioned estimand |
|---|---|---|---|---|
| Assignment level (scenario E) | fails | fails | not identifiable: the assigned population itself is corrupted | not identifiable |
| Exposure level (primary incident) | passes | fails | identifiable and valid | biased |

The reader learns that the level at which the failure occurs determines which estimands survive, and that the standard SRM check answers only the first row. Step 1 of the workflow catches scenario E in one line, which is why the exposure-level incident carries the case.

## 11. Package functions this case requires

| Function | Input grain | Returns |
|---|---|---|
| `srm_check(observed_counts, expected_allocation, *, level, alpha=0.001)` | counts per arm | chi-square statistic, p-value, expected counts, flag; a warning that SRM is evidence of a mismatch and does not by itself prove bias |
| `exposure_rate_by_segment(assignments, exposures, users, *, segment_cols, treatment_arm, control_arm, ...)` | user-level joins | exposure rate and count per arm × segment, with treatment-minus-control difference |
| `invariant_check(population, *, arm_col, metric_cols, level, treatment_arm, control_arm, ...)` | user-level | per-metric difference by arm with interval and flag |
| `difference_in_proportions(successes_t, n_t, successes_c, n_c, *, estimand, analysis_unit, population, alpha=0.05)` | arm totals | estimate, standard error, interval, p-value, assumptions (Bernoulli, independent units, random assignment) |
| `event_arrival_lag(events, assignments, *, event_time_col, received_time_col, analysis_time, ...)` | event-level | lag distribution by arm, share of events unavailable by analysis time |
| `simulate_checkout_experiment(config, seed)` | none | the six tables plus a manifest logging τ*, seed and incident parameters |

Simulation support, outside the public analysis API: `abforensics.simulation.operating_characteristics` (`run_scenarios`, `run_sweep`, `summarise`, `paired_contrasts`, `case_story`), which the `abf oc` command drives.

Every result object carries an estimand description (or "none, diagnostic"), estimate, uncertainty, assumptions and warnings. No function infers the unit, drops missing rows silently, or returns a ship decision.

## 12. Artefacts

README.md (executive summary with the decision first, on one screen; unit hierarchy and estimands, provenance, the four figures, the scenario and contrast tables, recommendation, decision blockers, reproduction), analysis.ipynb (the section 8 workflow, importing only from the package), decision_memo.md (one page addressed to the product lead), data_contract.md (section 6 tables with types, keys and event definitions), figures/ (fig1 exposure-rate gap by segment; fig2 affected-cell share on assigned versus exposed populations, the composition invariant that replaced the pre-period invariant, see section 7, item 3; fig3 exposed-population versus ITT estimates on complete data against the threshold; fig4 operating characteristics against the loss probability: bias, coverage and exposure-SRM detection for the day-14 dashboard, the segment-excluded estimator and ITT). The scenario and contrast tables and the story rate live in the README; all of them are in `results/case01/oc_*.csv`, which are tracked.

## 13. Assumptions and unresolved risks

Stated in the README: one device per user; no interference between users; server-side order logging is intact (the incident is client-side only); hash-based assignment is uniform; the 7-day outcome window captures the effect (no delayed conversion beyond it); the treatment effect is constant among rendered users and zero for users served the fallback; the render-event loss is random within the affected cell; no covariate adjustment (CUPED is a Phase 2 case). Unresolved risks named for the reader: cross-device identity joins, bot traffic filtered asymmetrically, novelty effects and behaviour-dependent event loss, each deferred to a later case.

## 14. Acceptance criteria (definition of done)

- `uv run abf simulate case01`, `uv run abf run case01` and `uv run abf oc case01` regenerate data, run the notebook headless and write figures and the operating-characteristic tables from fixed seeds. The first two take under a minute on a laptop; `abf oc` took 21 minutes of unit time on a two-core Linux machine at the configured B, caches each unit of work under `results/case01/parts/` keyed by a hash of its inputs, the library versions and the code, and resumes if interrupted (`--max-seconds` splits it between units).
- `uv run pytest` passes; `uv run pytest -m slow` passes (coverage of the relative interval at the case regime, the scenario claims at B = 600, the case-seed rule; about three minutes on two cores).
- Unit tests pass for the six functions, including an exact 50/50 case, a multi-arm case and invalid-allocation rejection for `srm_check`, and known-answer tests for `difference_in_proportions`.
- Operating-characteristic tables report B and Monte Carlo uncertainty on every rate.
- The README executive summary fits on one screen, opens with the decision and passes the two-minute spoken pitch.
- The decision memo is one page, names the decision owner, the recommendation, the blockers and what evidence would change the decision.
- No sentence describes the incident as real.

## 15. Sources

- Armitage, McPherson, Rowe. Repeated Significance Tests on Accumulating Data. Journal of the Royal Statistical Society, Series A, 132(2), 1969.
- Clopper, Pearson. The Use of Confidence or Fiducial Limits Illustrated in the Case of the Binomial. Biometrika, 26(4), 1934.
- Fabijan, Gupchup, Gupta, Omhover, Qin, Vermeer, Dmitriev. Diagnosing Sample Ratio Mismatch in Online Controlled Experiments: A Taxonomy and Rules of Thumb for Practitioners. KDD 2019.
- Kohavi, Tang, Xu. Trustworthy Online Controlled Experiments. Cambridge University Press, 2020. Chapter 20 (triggering) and chapter 21 (sample ratio mismatch and other trust-related guardrail metrics).
- Deng, Lu, Litz. Trustworthy Analysis of Online A/B Tests: Pitfalls, Challenges and Solutions. WSDM 2017.
- Kohavi, Longbotham. Unexpected Results in Online Controlled Experiments. SIGKDD Explorations, 2010.
- Chen, Liu, Xu. Automatic Detection and Diagnosis of Biased Online Experiments. arXiv:1808.05513, 2018 (LinkedIn's SRM diagnosis workflow).

## 16. Changes after the build (29 September 2026)

- Generator reworked for operating characteristics (retro action A1): the assignment hash salt is drawn from the random generator, so every replicate re-randomises; an arm-independent fallback share (0.15) makes the exposed population a true subset; `affects_arm` accepts treatment, control or both and rejects anything else; the affected cell's conversion deficit is a config key (`affected_cell_logit_offset`) so it can be swept; orders carry `refunded_at`.
- The committed seed-20260928 data was regenerated on Linux (CPython 3.14.7) before the rework and all seven content checksums matched the Windows (CPython 3.14.0) manifest, with the same locked library versions. The rework changes the data by design.
- Power restated against the +1.0% margin (section 5); τ* defined among rendered users with the ITT truth diluted (section 6).
- Refund guardrail kept as a guardrail-maturity lesson (Adi, 29 September): reported on the matured cohort with its design completion date (section 4).
- Payment guardrail moved to the user level (section 4).
- Package signatures carry explicit `level`, arm-name and estimand arguments (section 11 updated to match the code).
- After three rounds of adversarial review the same day: the generator draws every per-user quantity by inverse CDF from its own random stream, so a parameter change moves only what depends on it; config values and keys are validated; users served the fallback load the payment step and order through it, and the payment guardrail measures payment-step errors per assigned user; guardrail margins, their verdict rule and the final-look boundary were added to the design after the committed run had been inspected, and within the case they are treated as declared before the experiment (no verdict for this run changes under any version); the refund guardrail is breach-only at +10%, after a brief change to +25% during review (sections 2 and 4); the decision rule is ordered with a final row so every outcome at either look maps to one action, and the extension is an interim and a final look on continuous 21-day enrolment; the case seed is chosen by a stated, tested rule and its selection is disclosed with the story rate (section 6); the operating characteristics add scenario F, the day-14 dashboard and segment-excluded estimators, scoring against the ITT estimand, paired contrasts, the rescaled ship contrast, the story rate and Clopper-Pearson intervals, and their tables are tracked; the recommendation is a rerun with the fix, and the latency guardrail is estimated with a user-clustered bootstrap.
