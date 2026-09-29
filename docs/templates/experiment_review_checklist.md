# Experiment review checklist

No overall score. Each item is done, failed, unknown or not applicable, with a note. Any failed or unknown item in the blocker section stops interpretation.

## Design
- Business decision and decision owner named
- Estimand stated separately from metric, estimator and decision rule
- Unit hierarchy stated (randomisation, eligibility, exposure, outcome, analysis, clustering)
- Power computed from stated baseline and MDE; assumptions of the calculation named
- Guardrails and invariants pre-declared

## Data quality (blockers)
- Assignment-level SRM
- Exposure-level SRM
- Invariants on the assigned population
- Invariants on the analysed population
- Event-arrival completeness by arm
- Differential missingness assessed

## Analysis
- Estimator matches the declared estimand and analysis unit
- Variance estimator stated, with its independence assumption
- Interval reported against the practical threshold, not only against zero
- Operating characteristics available for the estimator under the scenario's assumptions

## Decision
- Recommendation tied to the pre-declared rule
- Blockers listed with status
- What would change the decision stated
