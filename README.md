# ab-test-forensics

Reproducible case studies in how online experiments produce the wrong decision, and what a senior practitioner does about it.

Most A/B testing material teaches how to obtain p < 0.05. This repository covers the step before that: deciding whether the comparison in front of you still has a causal interpretation. Each case is an incident investigation on simulated or semi-synthetic data with a known ground truth, so every claim about bias, coverage or false-positive rate is measured by repeated simulation. Every function in the package states its estimand, its assumptions and its warnings, and none of them returns a ship decision.

## Cases

| # | Case | Failure mechanism | What survives | Status |
|---|---|---|---|---|
| 01 | The lift we could not trust | Exposure telemetry lost for one platform segment on the treatment side; the dashboard analyses exposed users | Assignment-level SRM passes, exposure-level fails; ITT stays valid, the triggered estimate does not | Built; operating characteristics at B = 5,000 per scenario |
| 02 | The metric that told two stories | User-randomised, event-analysed ratio metric with heavy-tailed exposure and a treatment-induced denominator shift | Three estimands stated side by side; the delta-method, jackknife and bootstrap intervals on the ratio; the naive interval's Type I error measured | Spec written (`docs/design/case-02.md`) |
| 03 | When time becomes the treatment unit | Carryover under autocorrelated outcomes in a time-block randomised experiment on real hourly load data | Block-aware analysis with washout; bias and power across block lengths | Planned |

Two claims run through the cases: the analysed population must be the randomised one (Case 01), and the variance of an estimate must respect the unit that was randomised, not the unit that was measured (Cases 02 and 03).

Phase 2 roadmap: CUPED under covariate drift and leakage; continuous peeking and sequential tests; metric governance and delayed outcomes; time-varying effects (trend testing, novelty and primacy); heterogeneity on genuinely randomised data.

## What each case contains

Five artefacts plus the case's `config.yaml`: `README.md` (decision, unit hierarchy, estimand, evidence, blockers), `analysis.ipynb` (the investigation, importing only from the package), `decision_memo.md` (one page to the decision owner), `data_contract.md` (tables, keys, units, event definitions), `figures/`. The design specification each case was built against is in `docs/design/`.

## Quick start

```
uv sync --group dev
uv run pytest                 # unit tests, seconds
uv run pytest -m slow         # coverage, bias and seed-rule simulations, about three minutes on two cores
uv run abf simulate case01    # regenerate the case's data from its config and seed
uv run abf run case01         # execute the notebook headless
uv run abf oc case01          # operating characteristics, tables in results/case01/ (runtime in the case README)
```

Python 3.14 is the development version; 3.12 and 3.14 are tested in CI.

## Standards

Every estimator is to be validated by repeated simulation with B stated and the Monte Carlo standard error reported next to every rate; the runner is `abf oc <case>`, and no case is declared complete before its tables exist (Case 01: see its README). Every case declares whether its data is simulated, semi-synthetic or genuinely randomised and why. SRM is treated as a diagnostic, and a validity verdict needs more than a passing check. Incidents are described as plausible mechanisms.

## Layout

```
src/abforensics/    diagnostics, inference, simulation, reporting, results
tests/unit/         fast, CI
tests/simulation/   slow, run before a case is declared done
case_studies/       one folder per case
docs/design/        design specs, written before code
docs/methodology/   one short note per method
docs/templates/     case README, decision memo, data contract, review checklist
data/manifests/     seed, true effect, parameters and checksums per generated dataset
```

Generated data, raw downloads and the operating-characteristic replicate cache are untracked and rebuilt by the commands above; the operating-characteristic summary tables under `results/` are tracked because the case READMEs cite them.

## Licence

MIT.
