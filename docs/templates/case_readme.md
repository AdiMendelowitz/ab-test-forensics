# Case NN: <incident-style title>

## Decision and executive summary
<What was being decided, what the dashboard showed, what was actually wrong, what the decision is. Five sentences.>

## Units and estimand
| Unit | Definition |
|---|---|
| Randomisation unit | |
| Eligibility unit | |
| Exposure unit | |
| Outcome-observation unit | |
| Analysis unit | |
| Clustering unit (if different) | |

Primary estimand: <population-level causal quantity, in words and symbols>.
Secondary estimand (if the dashboard targets a different one): <and why it differs>.
Metric: <exact definition, window, denominator>.
Estimator: <calculation and interval>.
Decision rule (pre-declared): <thresholds and actions>.

## Data provenance
<Simulated / semi-synthetic / genuine randomised. Generator, seed, true effect, manifest path. Why this choice matches the question.>

## Diagnostic evidence
<The two or three decisive figures with one sentence each.>

## Result and recommendation
<Naive versus corrected estimate against the practical threshold. The recommendation and what evidence would change it.>

## Decision blockers
- [ ] Assignment issue unresolved
- [ ] Exposure measurement issue unresolved
- [ ] Outcome logging issue unresolved
- [ ] Missingness may be differential
- [ ] Guardrail breach or guardrail unmeasurable
- [ ] Duration insufficient for delayed outcomes
- [ ] Metric denominator is a post-treatment quantity

## Assumptions and unresolved risks
<Stated, not hidden.>

## Reproduce
```
uv run abf simulate caseNN && uv run abf run caseNN && uv run abf oc caseNN
```
