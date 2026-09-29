# Case 02 design specification: The metric that told two stories

Repository: ab-test-forensics, `case_studies/02_metric_that_told_two_stories/`
Status: v1.0, 29 September 2026. Written before any Case 02 code, as the contract the code will be built against. Figures marked "expected" are analytic or come from one scratch draw of the generator described in section 6; the measured values replace them in the case README when `abf oc case02` has run.

## 1. Executive summary

A feed-ranking change appears to raise click-through rate by about 7% with p far below 0.001 on the team's dashboard. The dashboard divides all clicks by all impressions and treats every impression as an independent trial. Users were randomised, not impressions; a user's impressions share that user's click propensity, and a few heavy users contribute hundreds of impressions each, so the dashboard's interval is about 1.5 times too narrow and its test rejects a true null about one time in five. Correcting the variance does not change the decision, because the click-through rate is not the quantity the decision is about. The ranking change suppresses low-quality items that heavy scrollers used to see, so impressions per user fall by about 7%, click-through rate rises by about 7%, and clicks per user, the engagement quantity the product lead asked about, is unchanged within its interval (true effect +0.03%). Three estimands, three lifts, one decision: do not ship as an engagement gain; reframe the change as an efficiency change and decide it on that basis.

The case teaches one thing: a ratio metric has two moving parts, and when the treatment moves the denominator, the ratio can rise while the thing you care about does not. Its second lesson, the one shared with Cases 03 and 04, is that the variance of an estimate must respect the unit that was randomised, not the unit that was measured.

## 2. Business decision

Decision owner: feed product lead.
Action space: ship to 100%, do not ship, or take no decision and investigate. One look only: the case is about the metric, and monitoring policy belongs to Case 01 and Phase 2.
Timeline: enrolment runs 14 days. Each user's outcome window is the 7 days from first eligibility, so the last cohort's window closes on day 21; event arrival delay of up to 36 hours puts the analysis clock at day 22.5. Enrolment and maturity are two clocks, as in Case 01.
Decision rule (pre-declared), rows checked in order so every outcome maps to one action:

| Order | Condition | Action |
|---|---|---|
| 1 | The negative-feedback guardrail breaches | Do not ship |
| 2 | Any open decision blocker (section 9), or the must-pass guardrail inconclusive (neither pass nor breach) | No decision; investigate first |
| 3 | Upper bound of the 95% interval on relative lift in clicks per eligible user below 0 | Do not ship: engagement loss |
| 4 | Lower bound on relative lift in clicks per eligible user above +0.5% and the guardrail passes | Ship |
| 5 | Otherwise | Do not ship: the engagement gain is not shown to exceed the +0.5% floor |

The +0.5% threshold is the practical significance floor: the minimum relative engagement lift that justifies the serving cost of the new ranking model. It is a product input from the product lead, and the README says so. The rule is written on clicks per eligible user, not on click-through rate, and the README explains why in section 4's words before any number appears.

## 3. Unit hierarchy

| Unit | Definition |
|---|---|
| Randomisation unit | User, by deterministic hash of user_id at first eligibility |
| Eligibility unit | User who opens the feed during the enrolment window |
| Exposure unit | Impression: one item rendered in the viewport for at least one second. This is finer than the randomisation unit, and that is the case |
| Outcome-observation unit | Impression (click or no click) and user (clicks in the 7-day window) |
| Analysis unit | User, declared. The dashboard's is the impression, undeclared |
| Clustering unit | User: every impression belongs to exactly one user, and a user's impressions share that user's propensity |

## 4. Metric, estimand, estimator, decision rule

Three estimands are stated side by side. Each is a legitimate population quantity; only one answers the product lead's question, and the README never writes "the correct CTR".

**E1, decision estimand: clicks per eligible user.** E[C(1)] − E[C(0)], where C is the user's clicks in their 7-day window, reported as an absolute difference and as a relative lift. Every assigned user is in the denominator, including the 8% with no impression. Estimator: difference in user means with a Welch interval; delta-method interval on the log ratio for the relative lift, as in Case 01. Expected control mean 1.25 clicks per user, standard deviation 2.0; half of users click at least once. All expected figures in this document are analytic expectations over the generator's potential outcomes at 2 million users, not single-draw values.

**E2, the dashboard's estimand: platform-level click-through rate.** E[ΣC]/E[ΣM] per arm, the ratio of expected totals, an impression-weighted rate. Valid estimator: the ratio of arm sums with a delta-method (linearisation) variance computed at the user level (Deng, Knoblich and Lu 2018): with per-user pairs (C_i, M_i) and R the ratio of sums, Var(R) ≈ [Var(C) − 2R Cov(C, M) + R² Var(M)] / (n M̄²). Cross-checks: a bootstrap that resamples users, and a delete-one-user jackknife, which for a ratio of sums has a closed form from leave-one-out totals and costs one pass. Invalid estimator, kept in the package as `naive_event_rate` so that the case can show it: the pooled two-proportion z-test with n equal to the number of impressions. Expected control level 3.4%.

**E3: mean user-level click rate** among users with at least one impression, E[C_i/M_i | M_i > 0]. Each user weighs the same, so light users dominate, and the level is about 6.2% against E2's 3.4%. It conditions on a post-treatment event (having an impression); in this generator the treatment never removes a user's last impression, so the conditioning set is arm-independent by construction, and the README says this is a simplification. Estimator: Welch on user rates.

**Why they disagree.** E2's lift mixes a numerator effect with a denominator effect. When the treatment removes impressions that carried few clicks, E2 rises, E3 rises less (light users lose almost nothing), and E1 can fall. The case's expected truths (section 10) are +7.4% for E2, +5.5% for E3 and +0.03% for E1 in the committed scenario. Under a pure click lift (scenario C) all three move by the same +2.5%, because τ scales every user's click probability; the estimands separate only when the denominator moves.

**Impressions per eligible user** is reported as a metric in its own right, with its interval, because it is the denominator that moved. Expected −6.9%.

**Guardrail.** Negative-feedback actions ("hide", "not interested") per eligible user, must pass, margin +5% relative, verdict rule as Case 01 (pass if the upper 95% bound is at or below the margin, breach if the lower bound is above it, inconclusive otherwise). In the generator the per-impression negative-feedback rate is 0.5% with user heterogeneity and is three times higher on low-quality items, so the treatment reduces it; at 100,000 users per arm the guardrail's relative standard error is about 1.2%, so at no true effect it passes its +5% margin about 99% of the time and under the treatment (expected −11%) essentially always; the measured rate from scenario A goes in the README.

**Invariants** on the assigned population: assigned counts, platform mix, country mix, pre-period 28-day impressions and clicks per user. The pre-period activity columns are also the covariate a Phase 2 CUPED case would use.

## 5. Experiment design

Allocation 50/50. Enrolment 14 days at about 14,300 eligible users per day, 200,000 users, 100,000 per arm; the simulation generates the enrolment window and the 7-day outcome windows that follow it.

Power on E1, computed with the Welch interval at a control mean of 1.25 clicks per user and standard deviation 2.0, α = 0.05 two-sided. The relative standard error of the lift is 0.72% at 100,000 per arm. At the simulated engagement effect of +2.5% (scenario C, τ = 0.025), power against zero is 94% and power against the +0.5% ship margin is 80%; the sample size was chosen against the margin, per decision record v2, and 80% is reached at about 100,400 per arm. At +1.0%, power against the margin is 10%, which the README states so that an inconclusive E1 is not read as evidence of no effect. These figures assume independent users, a fixed horizon and a stable baseline, and the manifest's standard deviation replaces the analytic 2.0 when the data exists.

On E2 the naive interval is not sized; the point of the case is that its width is wrong. Expected relative standard error of the E2 lift at 100,000 per arm: 0.61% by the delta method against 0.39% by the naive binomial formula, a ratio of about 1.55, which gives the naive test an expected Type I error of about 20% at nominal 5% (analytic; a scratch Monte Carlo at 20,000 per arm gave 21.5%, SE 0.5, with the delta method at 6.0% and the E1 Welch test at 5.4%).

## 6. Data provenance and simulation disclosure

All data is simulated. The decision record of 28 September planned Case 02 as semi-synthetic, with a public dataset supplying the activity distribution. No public dataset found supplies impression-level exposure with user identity under a clean licence at the light-user end: MovieLens, the closest candidate, truncates at 20 ratings per user, which removes exactly the light-user mass the case needs. The generator therefore states its activity distribution explicitly, and the README discloses the deviation. This decision is open for review before the build (section 16).

The generator is seeded, records the true effect on each of the three estimands in its manifest, and writes long-format tables so the notebook does real joins with logged row counts:

| Table | Grain | Key columns |
|---|---|---|
| users | one row per eligible user | user_id, platform (ios, android, web), country, pre_period_impressions_28d, pre_period_clicks_28d |
| assignments | one row per assigned user | user_id, arm, assigned_at |
| impressions | one row per impression | impression_id, user_id, item_id, shown_at, received_at, position, quality_tier (high, low) |
| clicks | one row per click | click_id, impression_id, user_id, clicked_at |
| feedback | one row per negative-feedback action | feedback_id, impression_id, user_id, action, acted_at |
| truth | one row per user, audit only | potential impressions and clicks under each arm |

Data-generating process, per user, every quantity drawn by inverse CDF from its own random stream so that common random numbers hold across scenarios (the Case 01 layout):

- Activity: impressions in the 7-day window M_i ~ zero-inflated negative binomial, zero with probability 0.08, otherwise NB with dispersion r = 0.5 and mean 40, floored at 1. Expected quantiles: median 15, 90th percentile 103, 99th percentile 260, maximum near 800 at 200,000 users. The top 1% of users hold about 9% of impressions and 5% of clicks.
- Click propensity: logit p_i = logit(0.05) + u_i − 0.25 log(M_i/40), u_i ~ N(0, 0.6²). Heavy scrollers have lower propensity per impression, which is what makes the denominator shift bite.
- Quality: each impression is high or low quality; the low-quality share is 0.2 + 0.4(1 − exp(−M_i/80)), rising with depth. A low-quality impression clicks at 0.25 times the user's propensity.
- Treatment, two parameters: `suppression` δ = 0.15 removes that fraction of a user's low-quality impressions (never the last impression); `click_lift` τ = 0.025 multiplies the click probability on every shown impression. The committed run uses both; section 10 varies them.
- Timing: shown_at uniform over the user's window; received_at adds a 0 to 36 hour pipeline delay identical across arms.
- Pre-period activity is a noisy function of M_i, so it is predictive and balanced.

Single-run disclosure, as in Case 01. The committed run shows the story when the naive test and the delta-method test both reject on E2, the E1 interval lands in row 5 of the rule (upper bound above 0, lower bound below +0.5%), the guardrail passes and assignment SRM passes. The seed is the first at or above a stated start whose run shows it; the README states the rule, the seed, and the rate at which a random seed shows the story, measured in scenario D (expected about 97%, since E1's truth is +0.03% against a standard error of 0.72%). Choosing the seed selects among noise draws, not among truths, and the disclosure says so.

True effects are computed from each user's potential outcomes and recorded in the manifest as `expected_relative_lift_clicks_per_user`, `expected_relative_lift_ctr` and `expected_relative_lift_user_rate`.

## 7. Incident mechanism

The new ranking model demotes low-quality items. Heavy scrollers, who reach deep into the ranking, see fewer of them; light users see almost the same feed. Two things then go wrong on the dashboard, and the notebook separates them.

1. **The interval is wrong.** The dashboard computes clicks over impressions and a binomial standard error with n equal to the impression count. Impressions are not independent trials: they cluster within users whose propensities differ (a user-level standard deviation of 0.6 on the logit scale) and whose counts are heavy-tailed (size-weighted mean impressions per user about 120 against a plain mean of 37). The naive standard error is about 1.5 times too small and the test rejects a true null about 20% of the time.
2. **The estimand does not answer the question.** Fix the variance with the delta method and the click-through rate still rises by about 7.4% with a valid interval that excludes zero. The rate did rise. Impressions per user fell by 6.9%, clicks per user moved by +0.03%, and the product lead asked about engagement. This is the sharper half of the case: a valid interval on an estimand the decision does not read ships the wrong thing.

Consequences the analysis has to discover, in order: assignment SRM and invariants pass; impressions per user differ by arm, concentrated in the top activity deciles; the naive and delta-method intervals on the same point estimate differ in width by 1.5 times; the three estimands give three lifts; clicks per user is inconclusive under the rule and the guardrail passes.

Wording rule for the README: "This simulated incident models a plausible ranking change and a plausible dashboard." Neither is described as real.

## 8. Diagnostic workflow (the notebook's spine)

1. Assignment SRM on assigned counts. Expected to pass.
2. Invariant checks on the assigned population, including pre-period activity. Expected to pass.
3. Impressions per eligible user by arm, overall and by pre-period activity decile. The figure shows where the denominator moved.
4. One point estimate, four intervals: the E2 lift with the naive, delta-method, user-bootstrap and jackknife intervals. The three valid intervals agree; the naive one is 1.5 times narrower.
5. Three estimands side by side against the rule: E2, E3 and E1 with valid intervals and the +0.5% floor drawn on E1 only.
6. Clicks per user by activity decile: where the change gained and where it lost.
7. Decision memo.

## 9. Decision blockers

- Assignment issue: no.
- Exposure measurement issue: no logging failure. The exposure unit is finer than the randomisation unit and the treatment changes how many exposures a user gets; this is a metric-construction issue, and the case proposes a new checklist row for it (section 12).
- Outcome logging issue: final-day incompleteness, non-differential, handled by the analysis clock; not a blocker.
- Differential missingness: no.
- Guardrail breach or unmeasurable: no; the guardrail is measured on every assigned user.
- Duration insufficient for delayed outcomes: no, with a 7-day window and a 36-hour maximum delay.
- Metric denominator is a post-treatment quantity: yes, for E2 and E3. This blocks reading either as an engagement estimate; it does not block the experiment, because E1 is available.

## 10. Operating-characteristic validation

B = 5,000 replicates per scenario with Monte Carlo standard errors on every rate, run through the Case 01 harness (`simulate_replicates`, `summarise`, paired contrasts under common random numbers, per-unit cache). Six estimators: naive impression-level z-test on E2; ratio of sums with delta method on E2; ratio of sums with user jackknife on E2; ratio of sums with user bootstrap on E2 (1,000 resamples; run at B = 1,000 replicates because of cost, stated where quoted); Welch on E3; Welch and delta-method log ratio on E1. Each row reports bias against its own estimand, coverage, rejection rate and, for E1, the ship-rule rate.

| Scenario | δ | τ | Expected truths (E2, E3, E1) | What it establishes |
|---|---|---|---|---|
| A | 0 | 0 | 0, 0, 0 | Calibration: the valid estimators hold 5% Type I error and 95% coverage; the naive test's Type I error is measured (expected about 20%) |
| B | 0.15 | 0 | +4.8%, +2.9%, −2.4% | A pure efficiency change: E2 rises, E1 falls; row 3 of the rule (upper bound below 0) fires on E1 about 92% of the time |
| C | 0 | 0.025 | +2.5%, +2.5%, +2.5% | A pure engagement gain: power of each estimator; E1 clears the margin 80% of the time |
| D | 0.15 | 0.025 | +7.4%, +5.5%, +0.03% | The committed run: the two stories; E1 lands in row 5 about 97% of the time |

On E1 and E2, D equals B compounded with C: (1 − 0.024)(1.025) − 1 = +0.03% on E1. Truths are analytic expectations over potential outcomes at 2 million users and will be restated from the manifest.

Sweeps, B = 1,000 per point:

- User heterogeneity, σ of u_i from 0 to 1.0: the naive Type I error against the delta method's. At σ = 0 users still differ through the depth term and the quality share (expected naive-to-delta SE ratio about 1.17), so the sweep shows the naive error rising from a non-zero floor; one extra point with the depth term switched off shows the floor at nominal.
- Tail heaviness, NB dispersion r from 0.25 to 4: the naive Type I error, and the delta method's and bootstrap's coverage at the heavy end, where both are known to undercover at small n.
- Sample size, 10,000 to 200,000 per arm: coverage of the delta method and bootstrap, and wall-clock time of the delta method, the jackknife and the bootstrap. The record asked for this comparison; the expected result is that the delta method and the closed-form jackknife cost one pass each and the bootstrap costs 1,000, at the same coverage above about 50,000 users.

Joint coverage is not reported: the rule reads one parameter. Scenario truths are computed from potential outcomes per replicate, not fixed in advance; the table's figures are the analytic expectations stated above.

## 11. Package functions this case requires

Nothing else is built for this case. The delete-one-unit jackknife is written once here and reused by Cases 03 and 04 (decision record v2, decision 2).

| Function | Input grain | Returns |
|---|---|---|
| `ratio_of_sums(numerator, denominator, unit, alpha, method)` | user-level pairs | estimate of the ratio and of its relative lift by arm, with a delta-method interval (default), a user-jackknife interval or a user-bootstrap interval; estimand description names the ratio of expectations |
| `jackknife_variance(frame, unit_col, statistic)` | unit-level frame | delete-one-unit variance of any statistic of the arm totals; closed form when the statistic is a ratio of sums |
| `cluster_bootstrap(frame, unit_col, statistic, resamples, seed)` | unit-level frame | percentile interval; reports the resample count and seed |
| `difference_in_means(y_t, y_c, alpha)` | user-level | Welch interval on the difference and a delta-method interval on the log ratio; assumptions (independent units, finite variance) |
| `naive_event_rate(clicks_t, impressions_t, clicks_c, impressions_c)` | arm totals | the dashboard's estimate and interval, with a warning in the result that the interval assumes independent impressions |
| `simulate_ranking_experiment(config, seed)` | none | the six tables plus a manifest with the three true lifts |

Reused from Case 01: `srm_check`, `invariant_check`, `event_arrival_lag`, the result dataclasses and the operating-characteristic harness. Case 01's notebook carries an inline user-clustered bootstrap for its latency guardrail; when `cluster_bootstrap` lands, that notebook is re-executed on the package function and its interval is checked to match, so no reusable logic lives only in a notebook. Layout: `metrics/ratio.py` (ratio_of_sums, naive_event_rate), `inference/resampling.py` (jackknife_variance, cluster_bootstrap), `inference/means.py` (difference_in_means), `simulation/ranking_incident.py`.

## 12. Artefacts

README.md, analysis.ipynb, decision_memo.md, data_contract.md, config.yaml and figures/ (fig1 impressions per user by arm and activity decile; fig2 one estimate, four intervals; fig3 three estimands against the rule; fig4 operating characteristics: naive Type I error against heterogeneity and tail, and runtime against N). The scenario table lives in the README and in `results/case02/oc_scenarios.csv`. One template change: `docs/templates/experiment_review_checklist.md` gains the row "Metric denominator is a post-treatment quantity", with the same wording in the case README template's blocker list.

## 13. Assumptions and unresolved risks

Stated in the README: users are independent (no interference through the feed, which for a social feed is a real risk and is deferred to a Phase 2 interference case); item-level effects are collapsed into two quality tiers; position effects are carried by the quality share, not modelled; no novelty effect within 7 days; one device per user; the treatment never removes a user's last impression, so E3's conditioning set is arm-independent by construction. Unresolved: whether an impression-weighted rate is ever the decision quantity (it is, for ad-load decisions, and the README says so, which is why the case never calls it wrong).

## 14. Acceptance criteria (definition of done)

- `uv run abf simulate case02`, `uv run abf run case02` and `uv run abf oc case02` regenerate data, execute the notebook and write the operating-characteristic tables from fixed seeds; the first two under a minute, the third within about 20 minutes on two cores with the per-unit cache.
- Unit tests for the six functions, including: the closed-form jackknife equals the general delete-one-unit computation on a small frame; the delta-method variance equals the jackknife variance to first order on a smooth example; `naive_event_rate` carries its warning; the manifest's three truths match the potential outcomes.
- The committed manifest matches `config.yaml` (the Case 01 test, parametrised over cases).
- Operating-characteristic tables report B and Monte Carlo standard error on every rate; scenario A shows the naive Type I error with its interval.
- The README's two-minute version: "Users were randomised and impressions were counted. The interval was 1.5 times too narrow, and fixing it did not change the decision, because the rate rose by removing impressions, not by adding clicks."
- No sentence calls any of the three estimands correct, incorrect, right or wrong.

## 15. Sources

- Deng, Knoblich, Lu. Applying the Delta Method in Metric Analytics: A Practical Guide with Novel Ideas. KDD 2018.
- Bakshy, Eckles. Uncertainty in Online Experiments with Dependent Data: An Evaluation of Bootstrap Methods. KDD 2013.
- Budylin, Drutsa, Katsev, Tsoy. Consistent Transformation of Ratio Metrics for Efficient Online Controlled Experiments. WSDM 2018.
- Kohavi, Tang, Xu. Trustworthy Online Controlled Experiments. Cambridge University Press, 2020, chapter 22 (leakage and interference) and the metric chapters on ratio metrics and analysis units.
- Efron, Tibshirani. An Introduction to the Bootstrap. Chapman and Hall, 1993, chapters 6 and 11.
- Haulk, Richardson, Soriano. Testing for Trends in Online Experiments. arXiv 2609.01973, 2026 (the jackknife-by-unit precedent that Case 04 will reuse).

## 16. Open before the build

1. Simulated rather than semi-synthetic data (section 6): confirm, or name a public activity dataset with light users and a clean licence.
2. The +0.5% floor and the +5% negative-feedback margin are placeholders for product inputs; confirm or change before `config.yaml` is written.
3. Whether the bootstrap runs at B = 1,000 replicates in the operating characteristics or only in the sample-size sweep.
