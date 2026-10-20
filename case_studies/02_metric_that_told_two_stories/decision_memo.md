# Decision memo: feed-ranking change, click-through rate and engagement

To: Feed product lead
From: Experimentation analyst
Date: 11 October 2026

Recommendation: do not ship the ranking change as an engagement gain; decide it on its own efficiency rule if the serving-cost case matters.

What the experiment was meant to decide: whether the new ranking model raises engagement enough to justify its serving cost. The pre-declared rule ships if the lower bound of the 95% interval on relative lift in clicks per eligible user exceeds +0.5% and the negative-feedback guardrail passes its +5% margin. <!-- spec section 2 -->

What the dashboard showed: click-through rate up 5.37%, with a naive 95% interval of +5.07% to +5.66% and p far below 0.001. <!-- notebook cell 11, e2_naive -->

What we found: the dashboard divides all clicks by all impressions and treats each impression as an independent trial, although users were randomised and a user's impressions share that user's click propensity. On this draw the user-level interval is 1.49 times as wide as the dashboard's, and across 400 A/A splits of the real users the naive test rejects a true null 16.8% of the time. <!-- notebook cell 11, ABF_WIDTH_RATIO 1.485; spec D2 --> The ranking change also removes in-views that carried few clicks, so in-views per user fall by 5.29% (95% interval −6.60% to −3.97%) and the ratio rises. In the simulation's ground truth, the removal alone costs 2.44% of clicks per user and the +2.5% lift in click probability offsets that loss; on this draw, clicks per user were not shown to rise. <!-- notebook cell 9; oc_truths.csv B and D --> Assignment and invariant checks pass (control 57,381 and treatment 57,819 draws, p = 0.197). <!-- notebook cell 3 -->

What the corrected analysis says: with a user-level interval, click-through rate is still up 5.37% (95% interval +4.93% to +5.81%) and that statement concerns impression-weighted click-through. <!-- notebook cell 11, e2_delta --> Clicks per eligible user, the quantity this decision reads, changed by −0.21% (95% interval −1.53% to +1.12%), so the rule reaches row 5: the engagement gain is not shown to exceed the +0.5% floor. <!-- notebook cell 15, e1; notebook cell 23 --> The simulated negative-feedback guardrail passes (−9.82%, interval −11.79% to −7.81%). <!-- notebook cell 17 -->

Why the recommendation follows: the rule is written on clicks per eligible user, and its lower bound (−1.53%) sits below the +0.5% floor. One blocker applies to the metric: the click-through rate and the mean user-level rate have a post-treatment denominator, so neither supports an engagement claim.

What would change the decision: an experiment sized for the question, in which the lower bound on clicks per eligible user exceeds +0.5% with the guardrail passing; 57,600 resampled draws per arm give 0.81 to 0.82 probability of that outcome if the true gain is +2.5%, measured by resampling the 15,143-user base with replacement (3.80 draws per user per arm), so it describes the harness and gives no sizing for real traffic. <!-- oc_rule.csv C row4 0.814; oc_sizing_confirm.csv 0.8215 --> An inconclusive result at that size is weak evidence about a +1.0% gain, where analytic power against the floor is 0.110. <!-- spec D7 --> A separate decision on serving cost per impression would need its own floor and rule.

Open risks:
- The control arm is real EB-NeRD data and the treatment arm is simulated on the same users' rows, so every effect in this memo belongs to the simulation, and a draw is one resampled user.
- The negative-feedback guardrail is simulated in both arms, because the dataset records no explicit negative feedback.
- The population is EB-NeRD's active readers (5 to 1,000 clicks over 18 May to 8 June 2023, at least 5 in-view articles), and every interval is conditional on that selection. <!-- spec section 6 -->
- Users are treated as independent, and feed interference between users is not modelled.
- This is one simulated draw whose seed was chosen by a stated rule; a random seed shows the same story in 0.961 of replicates (MC SE 0.0043). <!-- oc_meta.json story_rate_D -->
