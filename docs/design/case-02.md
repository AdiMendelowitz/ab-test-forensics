# Case 02 design specification: The metric that told two stories

Repository: ab-test-forensics, `case_studies/02_metric_that_told_two_stories/`
Status: v1.2, 30 September 2026. Written before any Case 02 code, as the contract the code will be built against. v1.1 recorded the data decision (section 6). v1.2 records the Case 02 inputs settled with Adi on 30 September (decision record v2, section 9; section 16 here): 64,100 users per arm sized on the joint ship rule, calendar-week enrolment, the article-age quality tier after the publisher totals were found to overlap the split, bootstrap placement, the licence rule and the base variant. Every figure the prose quotes is a row of the derived-figures table (section 17), cited as [Dn]; the table gives each figure's formula, inputs, value and source, and marks which are measured on the base, which are analytic, and which are v1.0 generator expectations awaiting `abf oc case02`. A change to data or parameters edits the table first and the prose second.

## 1. Executive summary

A feed-ranking change appears to raise click-through rate by about 7% [D13] with p far below 0.001 on the team's dashboard. The dashboard divides all clicks by all impressions and treats every impression as an independent trial. Users were randomised, not impressions; a user's impressions share that user's click propensity, and a few heavy users contribute hundreds of impressions each, so the dashboard's interval is about 1.5 times too narrow and its test rejects a true null about one time in six (measured on the base population of section 6: ratio 1.48, Type I error 16.8% [D1, D2]). Correcting the variance does not change the decision, because the click-through rate is not the quantity the decision is about. The ranking change suppresses low-quality items that heavy scrollers used to see, so impressions per user fall by about 7%, click-through rate rises by about 7%, and clicks per user, the engagement quantity the product lead asked about, is unchanged within its interval (v1.0 expected true effect +0.03% [D13]). Three estimands, three lifts, one decision: do not ship as an engagement gain; reframe the change as an efficiency change and decide it on that basis.

The case teaches one thing: a ratio metric has two moving parts, and when the treatment moves the denominator, the ratio can rise while the thing you care about does not. Its second lesson, the one shared with Cases 03 and 04, is that the variance of an estimate must respect the unit that was randomised, not the unit that was measured.

## 2. Business decision

Decision owner: feed product lead.
Action space: ship to 100%, do not ship, or take no decision and investigate. One look only: the case is about the metric, and monitoring policy belongs to Case 01 and Phase 2.
Timeline: enrolment and outcomes share one calendar week, the base population's 7-day split (section 6). A user is eligible at their first logged page view in the week, outcomes are the user's in-views and clicks from then to the end of the week, and an event arrival delay of up to 36 hours puts the analysis clock 36 hours after the week closes. Enrolment and maturity are still two clocks, as in Case 01: users enrolled late in the week have short windows, and the notebook shows outcomes by enrolment day so that nobody reads a late cohort's low count as an effect.
Decision rule (pre-declared), rows checked in order so every outcome maps to one action:

| Order | Condition | Action |
|---|---|---|
| 1 | The negative-feedback guardrail breaches | Do not ship |
| 2 | Any open decision blocker (section 9), or the must-pass guardrail inconclusive (neither pass nor breach) | No decision; investigate first |
| 3 | Upper bound of the 95% interval on relative lift in clicks per eligible user below 0 | Do not ship: engagement loss |
| 4 | Lower bound on relative lift in clicks per eligible user above +0.5% and the guardrail passes | Ship |
| 5 | Otherwise | Do not ship: the engagement gain is not shown to exceed the +0.5% floor |

The +0.5% threshold is the practical significance floor: the minimum relative engagement lift that justifies the serving cost of the new ranking model. It is a product input from the product lead, and the README says so. The rule is written on clicks per eligible user, not on click-through rate, and the README explains why in section 4's words before any number appears.

Bound convention: every bound the rule reads is an end of the two-sided 95% interval (z = 1.96), so each one-sided reading is at the 2.5% level; the derived figures use the same convention [D5 to D10].

## 3. Unit hierarchy

| Unit | Definition |
|---|---|
| Randomisation unit | In the incident being simulated: user, by deterministic hash of user_id at first eligibility. In the harness: the draw; each arm is drawn independently with replacement from the 15,143-user base (section 5), arm sizes Binomial(128,200, 0.5), so a population user may appear in both arms |
| Eligibility unit | User with at least one logged page view in the week (section 6: EB-NeRD logs a page view only when an article is opened) |
| Exposure unit | Impression: one item rendered in the viewport for at least one second. This is finer than the randomisation unit, and that is the case |
| Outcome-observation unit | Impression (click or no click) and user (clicks from first eligibility to the end of the split week, section 2) |
| Analysis unit | Draw (a user in the incident's terms), declared. The dashboard's is the impression, undeclared |
| Clustering unit | Draw: every impression belongs to exactly one draw, and a draw's impressions share that user's propensity |

## 4. Metric, estimand, estimator, decision rule

Three estimands are stated side by side. Each is a legitimate population quantity; only one answers the product lead's question, and the README never writes "the correct CTR".

**E1, decision estimand: clicks per eligible user.** E[C(1)] − E[C(0)], where C is the user's clicks from first eligibility to the end of the split week (section 2), reported as an absolute difference and as a relative lift. Every assigned user is in the denominator; on the base population every user has at least one in-view and at least one click (section 6), so E1 is clicks per reading user. Estimator: difference in user means with a Welch interval; delta-method interval on the log ratio for the relative lift, as in Case 01. Control mean 15.47 clicks per user, standard deviation 17.84, measured on the base [D3]. The v1.0 expected figures still quoted in sections 1, 4, 5, 7 and 10 are analytic expectations over the parametric generator's potential outcomes at 2 million users, not single-draw values; they are collected in [D13] and replaced there when `abf oc case02` has run on the base.

**E2, the dashboard's estimand: platform-level click-through rate.** E[ΣC]/E[ΣM] per arm, the ratio of expected totals, an impression-weighted rate. Valid estimator: the ratio of arm sums with a delta-method (linearisation) variance computed at the user level (Deng, Knoblich and Lu 2018): with per-user pairs (C_i, M_i) and R the ratio of sums, Var(R) ≈ [Var(C) − 2R Cov(C, M) + R² Var(M)] / (n M̄²). Cross-checks: a bootstrap that resamples users, and a delete-one-user jackknife, which for a ratio of sums has a closed form from leave-one-out totals and costs one pass. Invalid estimator, kept in the package as `naive_event_rate` so that the case can show it: the pooled two-proportion z-test with n equal to the number of impressions. Control level 0.0906, measured on the base [D3].

**E3: mean user-level click rate** among users with at least one impression, E[C_i/M_i | M_i > 0]. Each user weighs the same, so light users dominate, and the level is 0.1012 against E2's 0.0906, measured on the base (E3/E2 = 1.12). It conditions on a post-treatment event (having an impression); in this design the treatment never removes a user's last impression, so the conditioning set is arm-independent by construction, and the README says this is a simplification. Estimator: Welch on user rates.

**Why they disagree.** E2's lift mixes a numerator effect with a denominator effect. When the treatment removes impressions that carried few clicks, E2 rises, E3 rises less (light users lose almost nothing), and E1 can fall. The v1.0 generator's expected truths [D13] were +7.4% for E2, +5.5% for E3 and +0.03% for E1 in the committed scenario; the harness measures them on the base (section 10). Under a pure click lift (scenario C) all three move by the same +2.5%, because τ scales every user's click probability; the estimands separate only when the denominator moves.

**Impressions per eligible user** is reported as a metric in its own right, with its interval, because it is the denominator that moved. Expected −6.9% (v1.0, [D13]).

**Guardrail.** Negative-feedback actions ("hide", "not interested") per eligible user, must pass, margin +5% relative, verdict rule as Case 01 (pass if the upper 95% bound is at or below the margin, breach if the lower bound is above it, inconclusive otherwise). In the generator the per-impression negative-feedback rate is 0.5% with user heterogeneity and is three times higher on low-quality items, so the treatment reduces it; its relative standard error, scaled from the v1.0 figure of 1.2% at 100,000 per arm, is 1.50% at the section 5 sample size [D8], so at no true effect it passes its +5% margin with probability 0.916 [D9] and under the treatment (v1.0 expectation −11% [D13]) essentially always. v1.1 quoted "about 99%", which held only at 100,000 per arm and was not re-derived when the sample size fell; section 5 now sizes against that error. The guardrail figures are provisional: v1.2 moves the tier to article age (section 6), which changes which in-views carry the threefold rate, so the harness's scenario A rate replaces [D8] and [D9]. The notebook also re-reads the same replicates at a +3% margin, where the pass rate at no effect is 0.517 [D9]: a guardrail left unpowered for its margin, which a team then either stalls on or misreads as a pass (Schultzberg, Ankargren and Frånberg 2024). The re-read costs no simulation, because the verdict is a function of an interval the harness already computes.

**Invariants** on the assigned population: assigned counts, device-type mix, subscriber share, pre-period 21-day clicks per user (from `history.parquet`, section 6). The pre-period click column is also the covariate a Phase 2 CUPED case would use.

## 5. Experiment design

Allocation 50/50. The population is the 15,143 users of the base split. Each replicate, and the committed single run, draws 128,200 users in total with replacement, with arm sizes Binomial(128,200, 0.5) so that 64,100 per arm is the expectation and the assignment SRM check of section 8 tests a real allocation, 4.23 draws per population user per arm on average [D11]; the two arms are drawn independently. The analysis and clustering unit of every estimator is the draw, not the population user: a user drawn three times is three i.i.d. draws from the empirical distribution, and each draw carries its own random stream for the simulated treatment and the guardrail, keyed by draw index, so duplicated users share no treatment noise (section 10).

Sizing rule: an 80% probability that the full ship rule fires (row 4 of section 2: E1's lower bound above the +0.5% floor and the guardrail passes) under the simulated engagement effect of +2.5% (scenario C, τ = 0.025). Both conditions must hold, so the size is set on their joint probability rather than on E1 alone (Schultzberg, Ankargren and Frånberg 2024). At the base's coefficient of variation of 1.153 [D4] the relative standard error of E1's lift is 0.644% at 64,100 per arm [D5]; power against zero is 0.973 and against the margin 0.874 [D6]; the guardrail passes at no effect with probability 0.916 [D9]; the product is 0.800 [D10], treating the two as independent. v1.1 sized on E1 alone and set 52,500 per arm: E1 power against the margin 0.802, guardrail pass 0.855, joint 0.686 [D10]. 52,500 stays in the operating characteristics as a contrast row, the size a team reaches when the guardrail is left out of the power calculation. At +1.0%, power against the margin is 0.118 [D7], which the README states so that an inconclusive E1 is not read as evidence of no effect.

The joint figure is provisional on two counts: the guardrail standard error is scaled from a v1.0 simulated figure, and E1 and the guardrail are computed on the same draws, so they need not be independent. Scenario C at 64,100 per arm checks it: a measured joint ship-rule rate inside 0.80 ± 0.025 confirms the size; outside that band the size is re-derived from the harness before the committed run and the change is recorded in the decision record. The figures assume a fixed horizon and a stable baseline; the manifest's control standard deviation under the tuned δ replaces 17.84 when the data exists.

On E2 the naive interval is not sized; the point of the case is that its width is wrong. Measured on the base (section 6): the delta-method standard error of the E2 lift is 1.48 times the naive binomial one [D1], which gives the naive test a Type I error of 16.8% at nominal 5% (400 A/A splits, MC SE 1.9 points at 16.8%; delta method 4.5%, E1 Welch 5.5%, MC SE 1.1 points at 5%) [D2]. The v1.0 generator's figures were a ratio of 1.55 and about 20%.

## 6. Data provenance and simulation disclosure

Decision of 30 September 2026 (decision record v2, section 8, "Case 02 data decision"): real control, simulated treatment. The base population is the EB-NeRD small variant (Ekstra Bladet News Recommendation Dataset, `train` split: 15,143 users, 232,887 logged page views, 18 to 25 May 2023). The threshold was fixed before the run: a delta-to-naive standard-error ratio at or above 1.4 on A/A splits of the real users adopts EB-NeRD, below it the v1.0 parametric generator stays. The feasibility run (`notes/tools/ebnerd_feasibility.py`, 400 A/A splits, seed 20260929) measured a median ratio of 1.48 (IQR 1.48 to 1.48) for the ratio-of-sums estimator; the naive impression-level test rejected a true null 16.8% of the time (Monte Carlo standard error 1.9 points) against 4.5% for the delta method and 5.5% for the E1 Welch test (about 1.1 points at 5%) [D2]. The headline of section 1 is therefore measured on a real population rather than assumed by a generator. The decision record of 28 September planned Case 02 as semi-synthetic; MovieLens was rejected because it truncates at 20 ratings per user, and EB-NeRD logs every in-view article, including the light-user end.

What is real and what is simulated:

- Control arm, real. Each user's observed 7-day rows. An EB-NeRD behaviour row is one logged page view with its list of in-view articles (`article_ids_inview`) and the article(s) clicked (`article_ids_clicked`). The case's impression is an in-view article (mean 170.8 per user, median 98, p90 420, p99 950, max 2,187, sd 205.2) and the case's click is a clicked in-view (mean 15.5 per user, sd 17.8). The top 1% of users by in-view hold 7.2% of in-views and 6.1% of clicks. Observed estimand levels: E1 = 15.47 clicks per user, E2 = 0.0906 clicks per in-view as a ratio of sums, E3 = 0.1012 mean user-level rate.
- Treatment arm, simulated on the same users' rows by the two-parameter mechanism of section 7. `suppression` δ removes that fraction of a user's low-quality in-views, never the user's last in-view; a removed in-view takes its click with it. `click_lift` τ turns each retained unclicked in-view into a click with probability τ p_i / (1 − p_i), where p_i is the user's observed click rate over retained in-views, so that expected clicks per retained in-view are p_i (1 + τ). Both draws come from the draw's own random stream, keyed by draw index (section 5), so common random numbers hold across scenarios and duplicated users share no treatment noise.
- Quality tier, from article age at impression. EB-NeRD's article totals (`total_inviews`, `total_pageviews`) count only activity "within the first 7 days after it was published" and exist only for articles published after 16 February 2023 (Kruse et al. 2024). On the base, 89.1% of split in-views fall on articles carrying totals, and 84.5% on articles whose 7-day window overlaps the split [D12], so a totals-based proxy is built partly from the split's own clicks. The tier is therefore the oldest tercile of the split's in-views by article age at impression (`impression_time` minus `published_time`), defined for every in-view and free of the evaluation week by construction. Two checks go with it. Validation: on the in-views whose article window closed before the split (4.7% [D12]), the notebook reports the Spearman correlation between each article's median age at impression and its totals proxy, one row per article, and the age terciles the subset covers, a tercile counting as covered when at least 5% of the subset's in-views fall in it; the pre-set reading is that age is supported as a quality proxy if the correlation is at or below −0.3 and at least two terciles are covered, and otherwise the validation is reported as uninformative and the README says the tier rests on the mechanism alone. Contrast: scenario D is rerun with the leaked tier, the bottom tercile of the split's in-views by `total_pageviews / total_inviews`, with in-views on articles without totals assigned by article age as in the main tier, and the shift in the three truths is reported as the size of the leakage (Kaufman, Rosset, Perlich and Stitelman 2012). Mechanism check, before the build: the notebook reports the old tier's share of in-views by activity decile and the click rate by tier; the two-stories headline needs the old tier concentrated among heavy users and clicking below E2 [D3], and if either fails the headline is re-derived and the decision record amended before any simulation code is written.
- Negative-feedback guardrail, simulated in both arms. EB-NeRD carries no "hide" or "not interested" action, so the `feedback` table keeps the v1.0 mechanism (per-impression rate 0.5%, three times higher on low-quality in-views), and every guardrail figure is labelled simulated. A real guardrail from `read_time` or `scroll_percentage` is an option for a later revision (section 16, item 7).
- Pre-period covariate, real. The 21-day click history per user from `history.parquet` supplies `pre_period_clicks_21d`; the v1.0 column `pre_period_impressions_28d` is dropped because the history file carries clicks only.
- Timing. `impression_time` is real; `received_at` adds the 0 to 36 hour pipeline delay identical across arms, as in v1.0.

Two properties of the base population the README discloses. First, every logged user has at least one click in the 7 days (zero-click share 0.000), because EB-NeRD logs a page view only when the user opened an article: the population is "users who read at least one article in the week", not every user exposed to the feed, and E1 is clicks per reading user. Randomisation is within that population, so the A/B comparison is unbiased for it; the v1.0 sentence "including the 8% with no impression" in section 4 no longer applies. Second, E3/E2 is 1.12 against the v1.0 generator's 1.83: heavy readers' rates sit closer to light readers' than the generator assumed. That ratio measures the covariance of rate with volume, not the propensity spread; the 1.48 design effect is between-user propensity variance amplified by squared in-view counts (sd 205 on a mean of 171), and the harness reports the base's user-level rate variance beside it. The sweep in section 10 that varies propensity spread keeps the parametric generator because that spread is not a knob on real data.

Finite population. The 15,143 users are the population. Each replicate of the operating-characteristic harness draws n users per arm with replacement from it, and the truth for every scenario is the finite population's: the mean over its users of the expected potential outcomes under each arm, computed once per scenario and written to the scenario manifest before any replicate runs. Sample sizes above 15,143 per arm (section 5 sets 64,100) draw each user 4.23 times on average [D11]; the estimators then see an i.i.d. sample from the empirical distribution, which is the bootstrap world, and every Case 02 figure quoted at such a sample size says so. The large variant is not used; a one-off feasibility run on it, checking whether the ratio and the coefficient of variation hold at scale, is deferred to 31 October 2026 (section 16, item 5).

Tables the generator writes (long format, so the notebook does real joins with logged row counts): as v1.0, keyed by `draw_id` (the key of `users`, `impressions` and `feedback`, with `user_id` kept as an attribute), with `users` carrying `draw_id, user_id, device_type, is_subscriber, pre_period_clicks_21d` in place of platform, country and the two 28-day columns; `impressions` carrying the real `article_id` in place of `item_id`; `truth` one row per user, audit only, with expected potential in-views and clicks under each arm. Raw data stays under `data/raw/`, untracked.

Licence (section 16, item 6). The EB-NeRD general licence terms grant a research, non-commercial licence (1.1, 4.2), allow publishing results "to the extent that the results do not contain exact parts of the Dataset" (4.2), forbid copying beyond the download (4.3), limit use to obtaining results from recommender systems (4.6), and forbid Ekstra Bladet marks in publications (4.8). The repository therefore commits aggregates only: the manifest (population means and standard deviations, the three truths, and the md5 of each raw parquet file so a reader can confirm identical bytes), the operating-characteristic tables and the figures. Per-user and per-in-view frames are regenerated locally by `abf simulate case02` from the reader's own download and are never tracked; the README frames the case as the evaluation of a simulated feed-ranking change, quotes no article text, carries no publisher marks, cites the terms and names the download step. A unit test fails if any tracked file, notebook cell outputs included, contains a column named `user_id`, `article_id` or `draw_id`, or if any tracked file outside an allowlist (the manifest, `results/case02/*.csv`, `figures/`) contains a table with more than 1,000 rows. `abf simulate case02 --synthetic` runs the whole case on the v1.0 parametric generator calibrated to the base's aggregates, so the repository runs without the download; every figure from that path is labelled simulated, and headline figures come only from the base. This is a reading of the terms, not legal advice.

Single-run disclosure, as in Case 01 and unchanged from v1.0. The committed run shows the story when the naive test and the delta-method test both reject on E2, the E1 interval lands in row 5 of the rule (upper bound above 0, lower bound below +0.5%), the guardrail passes and assignment SRM passes. The seed is the first at or above a stated start whose run shows it; the README states the rule, the seed, and the rate at which a random seed shows the story, measured in scenario D. Choosing the seed selects among noise draws, not among truths, and the disclosure says so.

True effects are computed from each user's expected potential outcomes and recorded in the manifest as `expected_relative_lift_clicks_per_user`, `expected_relative_lift_ctr` and `expected_relative_lift_user_rate`.

## 7. Incident mechanism

The new ranking model demotes low-quality items. Heavy scrollers, who reach deep into the ranking and see more old articles (if the mechanism check of section 6 confirms it), see fewer of them; light users see almost the same feed. Two things then go wrong on the dashboard, and the notebook separates them.

1. **The interval is wrong.** The dashboard computes clicks over impressions and a binomial standard error with n equal to the impression count. Impressions are not independent trials: they cluster within users whose propensities differ (a user-level standard deviation of 0.6 on the logit scale) and whose counts are heavy-tailed (size-weighted mean impressions per user about 120 against a plain mean of 37). The naive standard error is about 1.5 times too small and the test rejects a true null about one time in six (16.8% measured on the base population; the propensity and count figures in this paragraph are v1.0 generator values).
2. **The estimand does not answer the question.** Fix the variance with the delta method and the click-through rate still rises by about 7.4% (v1.0 expectation [D13]) with a valid interval that excludes zero. The rate did rise. Impressions per user fell by 6.9% and clicks per user moved by +0.03% (v1.0 expectations [D13]), and the product lead asked about engagement. This is the sharper half of the case: a valid interval on an estimand the decision does not read ships the wrong thing.

Consequences the analysis has to discover, in order: assignment SRM and invariants pass; impressions per user differ by arm, concentrated in the top activity deciles; the naive and delta-method intervals on the same point estimate differ in width by 1.5 times; the three estimands give three lifts; clicks per user is inconclusive under the rule and the guardrail passes.

Wording rule for the README: "This incident is simulated on real reading behaviour: the control arm is observed EB-NeRD activity; the ranking change, its effect and the dashboard are modelled." Neither the change nor the dashboard is described as real.

## 8. Diagnostic workflow (the notebook's spine)

1. Assignment SRM on assigned counts. Expected to pass.
2. Invariant checks on the assigned population, including pre-period activity. Expected to pass.
3. Impressions per eligible user by arm, overall and by pre-period 21-day click decile. The figure shows where the denominator moved.
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

B = 5,000 replicates per scenario with Monte Carlo standard errors on every rate, run through the Case 01 harness (`simulate_replicates`, `summarise`, paired contrasts under common random numbers, per-unit cache). Six estimators: naive impression-level z-test on E2; ratio of sums with delta method on E2; ratio of sums with user jackknife on E2; ratio of sums with user bootstrap on E2 (scenarios A and D only: standard error from 200 resamples with a normal interval, at B = 1,000 replicates; resamples per replicate set the precision of each bootstrap standard error and replicates per scenario the precision of the coverage estimate, two budgets sized from different error targets, Efron and Tibshirani 1993, chapter 6); Welch on E3; Welch and delta-method log ratio on E1. Each row reports bias against its own estimand, coverage, rejection rate and, for E1, the ship-rule rate.

| Scenario | δ | τ | Truths (E2, E3, E1) | What it establishes |
|---|---|---|---|---|
| A | 0 | 0 | 0, 0, 0 by construction | Calibration: the valid estimators hold 5% Type I error and 95% coverage; the naive test's Type I error is measured by the harness; the feasibility run's 16.8% (400 A/A splits of the real users at about 7,570 per arm, MC SE 1.9 points) is the prior |
| B | 0.15 | 0 | measured from the base population | A pure efficiency change: E2 rises, E1 falls; the rate at which row 3 of the rule (upper bound below 0) fires on E1 (v1.0 expectation 92% [D13]) |
| C | 0 | 0.025 | measured from the base population; the three lifts coincide because τ scales every user's click probability | A pure engagement gain: power of each estimator; the rate at which the full ship rule fires (analytic 0.800 [D10]; the size check of section 5, tolerance ±0.025) |
| D | 0.15 | 0.025 | measured from the base population | The committed run: the two stories; the rate at which E1 lands in row 5 (v1.0 expectation 97% [D13]) |
| C at 52,500 | 0 | 0.025 | as C | Contrast: E1-only sizing; the ship-rule rate against C's (analytic 0.686 against 0.800 [D10]) |
| D, leaked tier | 0.15 | 0.025 | measured with the tier on the totals proxy | Leakage contrast: how far the three truths move when the tier is built on split-contaminated totals (section 6) |

Scenarios A, C and D are also re-read at a +3% guardrail margin from the same replicates (section 4); no extra simulation runs.

Truths are computed once per scenario from the finite population's expected potential outcomes under (δ, τ) and written to the scenario manifest before any replicate runs (section 6); the v1.0 generator's expectations (+4.8%, +2.9%, −2.4% for B; +2.5% for C; +7.4%, +5.5%, +0.03% for D) are retired to the CHANGELOG, and δ = 0.15 is re-tuned on the base so that scenario D keeps E1 inside the rule's row 5, with the tuned value and its reason stated in `config.yaml`. On E1 and E2, D still equals B compounded with C.

Finite population: every replicate resamples users with replacement from the 15,143-user base, and coverage is measured against the finite population's truth, fixed once per scenario, not against the drawn sample's mean of potential outcomes; the difference between the two is the between-user sampling variance the estimators exist to capture. Arms are drawn independently, the draw is the resampling and clustering unit of `jackknife_variance` and `cluster_bootstrap`, and each draw's random stream is keyed by draw index (section 5).

Sweeps, B = 1,000 per point. The first two vary parameters the real population does not expose, so they run on the v1.0 parametric generator calibrated to the base (mean in-view count 170.8 and its dispersion fitted to the per-user in-view counts, E2 = 0.0906) and every figure they produce is labelled simulated; the third runs on the base by resampling.

- User heterogeneity, σ of u_i from 0 to 1.0: the naive Type I error against the delta method's. At σ = 0 users still differ through the depth term and the quality share (v1.0 generator figure: naive-to-delta SE ratio about 1.17), so the sweep shows the naive error rising from a non-zero floor; one extra point with the depth term switched off shows the floor at nominal.
- Tail heaviness, NB dispersion r from 0.25 to 4: the naive Type I error, and the delta method's and bootstrap's coverage at the heavy end, where both are known to undercover at small n.
- Sample size, 10,000 to 200,000 per arm, on the base by resampling with replacement (points above 15,143 per arm are in the bootstrap world of section 6): coverage of the delta method and bootstrap, and wall-clock time of the delta method, the jackknife and the bootstrap. The record asked for this comparison; the expected result is that the delta method and the closed-form jackknife cost one pass each and the bootstrap costs 1,000, at the same coverage above about 50,000 users. Its bootstrap is the 1,000-resample percentile interval. The sweep also reports, at each size, the naive test's Type I error under scenario A and the ship-rule rate under scenario C, with 15,143, 52,500 and 64,100 marked. The expected shape of the first is flat near 17% [D2]: the design effect comes from how in-views cluster within users, so more users narrow every interval and never repair the miscalibrated one.

Joint coverage is not reported: the rule reads one parameter.

## 11. Package functions this case requires

Nothing else is built for this case. The delete-one-unit jackknife is written once here and reused by Cases 03 and 04 (decision record v2, decision 2).

| Function | Input grain | Returns |
|---|---|---|
| `ratio_of_sums(numerator, denominator, unit, alpha, method)` | user-level pairs | estimate of the ratio and of its relative lift by arm, with a delta-method interval (default), a user-jackknife interval or a user-bootstrap interval; estimand description names the ratio of expectations |
| `jackknife_variance(frame, unit_col, statistic)` | unit-level frame | delete-one-unit variance of any statistic of the arm totals; closed form when the statistic is a ratio of sums |
| `cluster_bootstrap(frame, unit_col, statistic, resamples, seed)` | unit-level frame | percentile interval, or a standard error with a normal interval; reports the resample count and seed |
| `difference_in_means(y_t, y_c, alpha)` | user-level | Welch interval on the difference and a delta-method interval on the log ratio; assumptions (independent units, finite variance) |
| `naive_event_rate(clicks_t, impressions_t, clicks_c, impressions_c)` | arm totals | the dashboard's estimate and interval, with a warning in the result that the interval assumes independent impressions |
| `simulate_ranking_experiment(config, seed)` | none | the six tables plus a manifest with the three true lifts |

Reused from Case 01: `srm_check`, `invariant_check`, `event_arrival_lag`, the result dataclasses and the operating-characteristic harness. Case 01's notebook carries an inline user-clustered bootstrap for its latency guardrail; when `cluster_bootstrap` lands, that notebook is re-executed on the package function and its interval is checked to match, so no reusable logic lives only in a notebook. Layout: `metrics/ratio.py` (ratio_of_sums, naive_event_rate), `inference/resampling.py` (jackknife_variance, cluster_bootstrap), `inference/means.py` (difference_in_means), `simulation/ranking_incident.py`.

## 12. Artefacts

README.md, analysis.ipynb, decision_memo.md, data_contract.md, config.yaml and figures/ (fig1 impressions per user by arm and activity decile; fig2 one estimate, four intervals; fig3 three estimands against the rule; fig4 operating characteristics: naive Type I error against heterogeneity and tail, and runtime against N). The scenario table lives in the README and in `results/case02/oc_scenarios.csv`. One template change: `docs/templates/experiment_review_checklist.md` gains the row "Metric denominator is a post-treatment quantity", with the same wording in the case README template's blocker list.

## 13. Assumptions and unresolved risks

Stated in the README: users are independent (no interference through the feed, which for a social feed is a real risk and is deferred to a Phase 2 interference case); item-level effects are collapsed into two quality tiers; position effects are carried by the old-article share, not modelled; no novelty effect within 7 days; one device per user; the treatment never removes a user's last impression, so E3's conditioning set is arm-independent by construction. Unresolved: whether an impression-weighted rate is ever the decision quantity (it is, for ad-load decisions, and the README says so, which is why the case never calls it wrong).

## 14. Acceptance criteria (definition of done)

- `uv run abf simulate case02`, `uv run abf run case02` and `uv run abf oc case02` regenerate data, execute the notebook and write the operating-characteristic tables from fixed seeds; the first two under a minute, the third within the limit of [D15] (about 35 minutes on two cores with the per-unit cache, an unmeasured estimate, reset to the measured time once the harness runs).
- Unit tests for the six functions, including: the closed-form jackknife equals the general delete-one-unit computation on a small frame; the delta-method variance equals the jackknife variance to first order on a smooth example; `naive_event_rate` carries its warning; the manifest's three truths match the potential outcomes.
- The committed manifest matches `config.yaml` (the Case 01 test, parametrised over cases).
- Scenario A at 64,100 per arm rejects inside 5% ± 1.96 × [D14] at the scenario's B on E1 and on the valid E2 estimators (draw-level clustering, section 10); scenario C's joint ship-rule rate lies inside 0.80 ± 0.025 or the size is re-derived (section 5).
- The no-per-user-data test of section 6 passes, and `abf simulate case02 --synthetic` runs end to end.
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
- Kruse et al. EB-NeRD: A Large-Scale Dataset for News Recommendation. RecSys Challenge 2024; arXiv 2410.03432 (article totals window, section 6).
- Ekstra Bladet / JP/Politikens Hus. General License Terms for Use of Open Source Dataset, https://recsys.eb.dk/assets/pdf/general_license_terms.pdf, clauses 1.1, 3.1, 4.2, 4.3, 4.6 and 4.8.
- Schultzberg, Ankargren, Frånberg. Risk-aware product decisions in A/B tests with multiple metrics. arXiv 2402.11609, 2024.
- Kaufman, Rosset, Perlich, Stitelman. Leakage in data mining: formulation, detection, and avoidance. ACM TKDD 6(4), 2012.
- EB-NeRD, the Ekstra Bladet News Recommendation Dataset (RecSys Challenge 2024), https://recsys.eb.dk/; small variant `ebnerd_small.zip`, `train` split, 7-day behaviours and 21-day history. Licence: section 6 and section 16, item 6.

## 16. Open before the build

Every item is closed, changed or deferred on Adi's word, 30 September 2026 (decision record v2, section 9).

1. Data source: closed 30 September 2026. "Real control, simulated treatment" on the EB-NeRD small base (section 6; decision record v2, section 8), on a measured ratio of 1.48 against the pre-set threshold of 1.4.
2. Floor and margin: closed 30 September 2026. The +0.5% floor and the +5% negative-feedback margin are confirmed; the notebook re-reads the same replicates at +3% (section 4).
3. Bootstrap placement: closed 30 September 2026. A 200-resample bootstrap standard error in scenarios A and D at B = 1,000; the 1,000-resample percentile bootstrap in the sample-size sweep (section 10).
4. Quality tier: changed 30 September 2026. The totals cover each article's first 7 days (Kruse et al. 2024) and overlap the split on 84.5% of in-views [D12]; the tier moves to article age at impression, with a validation and a leakage contrast (section 6).
5. Base variant: closed 30 September 2026, the small variant; the large-variant feasibility check is deferred to 31 October 2026.
6. Licence: closed 30 September 2026 on the general licence terms: aggregates-only commits, local regeneration, a `--synthetic` path, md5 provenance and a no-per-user-data test (section 6).
7. Guardrail signal: deferred without a date, to the next spec revision after `abf oc case02` runs. The negative-feedback table stays simulated in both arms.
8. Sample size and enrolment (retro action A13): closed 30 September 2026. 64,100 per arm on the joint rule, with 52,500 as a contrast row and the size sweep extended (sections 5 and 10); calendar-week enrolment kept (section 2).

## 17. Derived figures

One row per figure the prose quotes. "Measured" rows come from the base; "analytic" rows are closed-form from the stated inputs; "provisional" rows rest on a simulated input and are replaced by the harness.

| Id | Figure | Formula | Inputs | Value | Source |
|---|---|---|---|---|---|
| D1 | Delta-to-naive SE ratio, E2 lift | median over A/A splits of SE_delta / SE_naive | base, 400 A/A splits, seed 20260929 | 1.48 (IQR 1.48 to 1.48) | measured; `notes/tools/ebnerd_feasibility.py`; decision record section 8 |
| D2 | Type I error at nominal 5% | rejection share over A/A splits | as D1, about 7,570 users per arm | naive 0.168, delta 0.045, E1 Welch 0.055; MC SE 0.019 at 16.8%, 0.011 at 5% | measured, as D1 |
| D3 | Base levels | control means over users | 15,143 users | E1 15.47 (sd 17.84); E2 0.0906; E3 0.1012; E3/E2 1.12 | measured, as D1 |
| D4 | Coefficient of variation of clicks per user | sd / mean | D3 | 1.153 | analytic |
| D5 | Relative SE of the E1 lift | CV · √(2/n) | D4; n per arm | 0.644% at 64,100; 0.712% at 52,500 | analytic |
| D6 | E1 power at +2.5% | 1 − Φ(1.96 − (τ − m)/D5), m = 0 or 0.005 | D5; τ = 0.025 | 64,100: 0.973 against 0, 0.874 against the margin; 52,500: 0.940, 0.802 | analytic |
| D7 | E1 power at +1.0% against the margin | as D6 with τ = 0.010 | D5 | 0.118 at 64,100; 0.104 at 52,500 | analytic |
| D8 | Guardrail relative SE | 1.2% · √(100,000/n) | v1.0 generator figure, 1.2% at 100,000 per arm | 1.50% at 64,100; 1.66% at 52,500 | analytic on a simulated input; provisional |
| D9 | Guardrail pass rate at no effect | Φ((M − 1.96 · D8)/D8) | D8; M = 5% or 3% | 64,100: 0.916 (+5%), 0.517 (+3%); 52,500: 0.855, 0.441; 100,000: 0.986 (v1.1's "about 99%") | analytic; provisional |
| D10 | Joint ship-rule rate, scenario C | D6 (against the margin) × D9 (+5%), independence assumed | D6, D9 | 0.800 at 64,100; 0.686 at 52,500 | analytic; provisional; checked by scenario C, tolerance ±0.025 |
| D11 | Draws per population user per arm | n / 15,143 | n per arm | 4.23 at 64,100; 3.47 at 52,500 | analytic |
| D12 | Split in-views by article-totals status | shares of the split's 2,585,747 in-views | base `train` split; `articles.parquet` | carrying totals 0.891 (none 0.109); window closed before the split 0.047; window overlapping the split 0.845 | measured on the VM, 30 September 2026 |
| D13 | v1.0 expectations | analytic over the v1.0 generator's potential outcomes at 2 million users | v1.0 generator | D: E2 +7.4%, E3 +5.5%, E1 +0.03%, in-views −6.9%, guardrail −11%; B: +4.8%, +2.9%, −2.4%; C: +2.5% on all three; rule rates B 92%, C 80%, D 97% | spec v1.0; replaced when `abf oc case02` runs |
| D14 | Monte Carlo SE of a rate | √(p(1 − p)/B) | p = 0.05 or 0.95 | 0.31 points at B = 5,000; 0.69 at B = 1,000; 1.09 at B = 400 | analytic |
| D15 | `abf oc case02` time limit | measured seconds per replicate × replicates × 1.25 | not yet measured | about 35 minutes, estimate | to be measured on Adi's machine |
