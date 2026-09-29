# Sample ratio mismatch

An SRM check is a chi-square goodness-of-fit test of observed unit counts per arm against the allocation the experiment was configured with. `abforensics.srm_check` runs it at a stated level of the unit hierarchy (assignment, exposure, analysis), because the level at which counts stop matching is the finding.

What it tells you: that the units you are about to analyse were not produced by the randomisation you think produced them. What it does not tell you: whether the effect estimate is biased, by how much, or in which direction. Many invalid experiments show no SRM, and a transient SRM can appear in a valid one, so a pass is not a validity certificate and a fail is a trigger for diagnosis, not a verdict.

Threshold: the default alpha is 0.001, following large experimentation platforms where an SRM alert fires across many experiments per day. There is deliberately no default allocation; checking against an assumed 50/50 is one way to miss a misconfigured split.

Where it sits in the hierarchy (case 01): an assignment-level failure means the assigned population itself is corrupted and no estimand is identifiable. An exposure-level failure with clean assignment leaves the intent-to-treat estimand valid and biases only the exposure-conditioned one.

References: Fabijan et al., Diagnosing Sample Ratio Mismatch in Online Controlled Experiments, KDD 2019; Kohavi, Tang and Xu, Trustworthy Online Controlled Experiments, chapter 21.
