# Changelog

All entries precede the first release (0.1.0). Dates are commit dates.

## 19 November 2026

- Cases 01 and 02: committed sources for the last decision-memo numbers: `scripts/power_table_case01.py --itt` writes `results/case01/power_itt_case01.csv` (per-arm size at the simulated ITT lift) and `scripts/aa_splits_case02.py` writes `results/case02/aa_splits_small.json` (400 A/A splits of the 15,143 users) (e874364).

## 16 November 2026

- Docs: the Case 01 memo and README state that the scan repair is checked against the routing records; Case 02 README, decision memo and data contract review fixes (run times cited from tracked run metadata, Monte Carlo errors for scenario A, missingness wording); root README wording and `uv sync --locked` in the quick start (6480f04).

## 9 November 2026

- Case 01 rollout: staged-rollout generator, estimators, canary, CLI, tests and committed outputs (64766e0).
- Case 01 and Case 02 READMEs: power table, wording fixes and the SHA-256 of the large-variant evidence; CHANGELOG bullets (c12ca9a).

## 26 October 2026

- Case 01 README: scan parity is checked against the routing records; an A/A test alone does not confirm it (d338261).
- Case 02 notebook builder tracked with a test; public-reader check in pre-commit and CI (139f2ec).
- Case 01 README: drop the unsourced runtime sentence (f3678f5).
- CHANGELOG: commit hashes on every bullet; sections for 25 and 26 October; 23 October re-dated to its merge date (7e08fc1).
- Case 02: commit the large-variant evidence bundle that large_variant_check.md cites (f8fbcfc).
- Case 02: large-variant feasibility check from the 2026-10-14 run (8d7ae98).

## 25 October 2026

- README: tagline states how the issues are handled, not who handles them (36e4a56).
- Case 02 sweeps: population mark from config, missing-key ValueError, no-file check (40a9a59).
- README: link the Case 01 post, correct the MC SE of the rescaled ship rate to 0.6, CI token read-only (2ea7d59).

## 24 October 2026

- Documentation pass over every tracked file: pointers to design documents kept outside the repository removed from the READMEs, data contracts, configs, source and tests; the Case 02 README, decision memo and data contract carry their claims without provenance comments (568ffc4).
- Case 01 README: provenance sentence restated around the 20 October regeneration; the final-look return date given as a date only; config and README no longer narrate the build of the seed rule or the return margin (568ffc4).
- Case 02 README: the order of decisions stated without dates; `abf oc case02` described as a single uninterrupted pass with no cache or resume (568ffc4).
- Case 02 config and manifest: the project-management `deferred` block removed (568ffc4).
- Code and tests: docstrings and messages reworded in the current vocabulary; `invariant_check` docstring matches its left join; `event_arrival_lag` counts missing receipt times and treats them as unavailable; the jackknife relative interval guards a zero leave-one-out ratio; `joint_ship_rate_check` read from `config.yaml`; the per-user-data guard skips when no `.git` directory exists; CI tests 3.12, 3.13 and 3.14 (568ffc4).

## 21 October 2026

- Design notes, method notes and templates untracked and kept on disk only (dadd6a8).

## 20 October 2026

- Case 01: identifiers renamed to the assembly-line setting (products, stations, scans, supply lines, batches, finished products, returns); manifest, operating-characteristic results and figures regenerated; pre-commit ruff bumped to v0.16.9 (3967810, bfb1dc8).
- Case 01: assembly-line category values (supply lines A, B and C; batches 1 to 6 and untagged) in code, notebook and docs; CI actions checkout@v5 and setup-uv@v7 (3967810).

## 16 October 2026

- Case 02: computed two-minute sentence, real notebook run, gap decomposition of the 1.5 ratio in the README and wording revisions (159aea4).
- `test_no_per_user_data.py` formatted with ruff (5cdf727).

## 14 October 2026

- Case 02 README and memo: the clicks-per-user offset, the resampling qualifier on sizing and the 16.8% interval stated (eb00a4f).
- Case 02 data contract: the old-tier cut stated exactly with the float64 tie rule; README, memo and data contract aligned; two-minute sentence aligned in the notebook and tests (1a635df, 6d2ede8, ba27505).

## 13 October 2026

- Case 02 data contract: keep the three-fact list intact around the age count (7741a27).
- Case 02 data contract added, with the count of negative-age in-views and old-tier cut ties (2f10f67).
- Fixes: population flag read from config, zero-split guard, sweep tables validated before writing (07d8cf3).
- Case 02 data contract (1a3fb18).
- Case 02 README: measured runtime outputs change on rerun; the sweep outputs that reproduce byte for byte stated after a fresh-clone rerun (25afa3b, d7ef8dc).

## 12 October 2026

- CHANGELOG: dated sections from 30 September to 11 October (18e6372).
- Tests: one per-user-data guard with an `os.walk` fallback when `.git` is absent (e317579).

## 11 October 2026

- `abf oc case02 --sweeps`: sweep tables and figure 4, measured at 41.12 minutes (f3082a3).
- Wording corrections to the Case 01 and Case 02 READMEs (arXiv id, arm sizes, active-user selection, tier wording) (a1bce11).
- Unit aggregation raises on non-finite values instead of silently totalling them as zero (52bb0d2).
- Independent-seed sizing confirmation (0.8215 at oc seed 20261011) recorded (65372a6).
- Case 02 README and decision memo (093e81c).
- Measured sweep and simulate timings recorded; an aliasing claim softened (edb7709).
- Case 02 manifest: parametric generator provenance block (0ee2be3).

## 9 October 2026

- Case 02 parametric generator, calibration and validation added; calibration packaged with fresh validation (5cbe962, 23b18eb).
- Isolated Case 02 synthetic simulation (`abf simulate case02 --synthetic`) (341f1b4).

## 8 October 2026

- Case 02 functions and result classes exported; denominator blockers added (1cbc091).
- `cluster_bootstrap` covers statistics of totals; the Case 01 p95 bootstrap stays in the notebook (987c2f5).
- Tracked-data licence guard for Case 02 (47865ce).

## 7 October 2026

- Case 01 README: provenance from fresh-clone runs on Windows 11 and Linux (WSL2) (da95b7b).
- LF line endings for the Case 01 manifest and executed notebooks, with tests; ruff format (e630c48).
- Lint: long lines wrapped, `zip(strict=True)`, imports at top, lowercase test name (85e6ce2).
- Case 02 notebook from the cold-cache run of 7 October: 20.8 s wall-clock, 5.6 s in-notebook (bd32e1f).
- Root README: hero figure, Case 01 headline numbers, case cards, mechanism diagram, collapsible reference sections (553189c).

## 6 October 2026

- Root README: Case 02 status after the notebook; a local configuration file ignored (2c358e0).
- Case 02: ratio disclosure in the README; the guardrail kept simulated and disclosed (3908578).

## 5 October 2026

- Case 02: LF pins in the manifest and OC writers, case seed 20261002, committed run at 115,200 draws, manifest test over both cases (5512b36).
- Case 02 notebook: `abf run case02` through `cmd_run`, three tests, executed outputs and figures (f6076d8).

## 4 October 2026

- Case 02 harness: leaked tier, `oc_case02`, `abf oc case02` and `abf simulate case02`, with tests (d1f4905).
- Case 02 re-sized to 57,600 per arm from the measured scenario-C check; `abf oc case02` results at 115,200 draws (900b10e).

## 1 October 2026

- `ratio_of_sums` array kernel; Case 02 generator with tests (29245c8).

## 30 September 2026

- Case 02 design: EB-NeRD data decision, sample size at the measured CV, draws per arm, derived-figures table; `config.yaml`; `jackknife_variance` and `cluster_bootstrap` with tests (9d9ed2b, 6eef6f4, c6f8788).

## 29 September 2026

- Case 01 generator reworked for operating characteristics: per-replicate assignment salt; one independent random stream per block with every per-product quantity drawn by inverse CDF, so a parameter change moves only what depends on it; config keys and values validated with errors naming the key; an arm-independent legacy-route share (0.15) whose products never reach the scanner; `affects_arm` validated (treatment, control, both); configurable yield deficit for the affected cell; finish timing drawn per product so the day-14 dashboard can be simulated; packing-step errors and `returned_at` on finished products; ITT and scanner-reach truths in the manifest (1acea7e).
- `abf oc case01`: scenarios A to D and F (symmetric loss), four estimators (day-14 dashboard, logged scans on complete data, logged scans with the older batch removed, ITT), each scored against its own target and the ITT estimand; loss and yield-deficit sweeps; paired contrasts, the rescaled ship contrast, the case-story rate and Clopper-Pearson intervals; figure 4; units cached by a hash of their inputs, library versions and code, written atomically and resumable; summary tables tracked under `results/case01/` (1acea7e).
- Tests: coverage of the relative-lift interval at the case regime, scenario claims, the case-seed rule, the command's cache, budget and argument handling, per-product common random numbers, the sufficient-statistic estimators against explicit masks, config validation; a weekly, on-demand and pull-request CI job runs the slow tests, and CI installs from the lock file (1acea7e).
- Case 01 README, decision memo, data contract and notebook refreshed: power against the +1.0% margin, an ordered decision rule, guardrail margins with a stated verdict rule and pass probabilities, the second-look boundary and its cost, packing-step errors per routed product, cycle time with a product-clustered bootstrap, returns on the matured cohort with their design completion date, the dashboard read on day 14, a batch-excluded estimate, the case seed chosen by a stated and tested rule with its selection disclosed, and the recommendation to rerun with the fix (1acea7e).
- Manifest regenerated so its embedded config carries the +10% return margin (data checksums unchanged); a unit test now fails when the committed manifest and `config.yaml` disagree (1acea7e).
- `.gitattributes` normalises text files to LF so outputs regenerated on Windows do not show as whole-file diffs (1acea7e).
- `oc_meta.json` records the Python version and machine for each unit and, separately, for the run that assembled the tables; keys are sorted and the OC CSVs and meta are written with LF line endings on every platform (1acea7e).
- Case 02 design written before any Case 02 code: three estimands, a delete-one-user jackknife shared with later cases, sample size set against the decision margin; root README roadmap updated (905fca9).
- `requires-python` raised to 3.12 to match the documented support; root README CI versions corrected (1acea7e).

## 28 September 2026

- Repository scaffold, package skeleton, `srm_check` with unit tests (no matching commit).
- Case 01 generator `simulate_assembly_experiment` (six event tables, truth table, manifest with content checksums) and `abf simulate case01` (no matching commit).
- `difference_in_proportions`, `invariant_check`, `exposure_rate_by_segment`, `event_arrival_lag` with unit tests; `reporting.style` (no matching commit).
- Case 01 notebook executed with three figures, README, decision memo and data contract (9fa864e).
- Design change: the pre-period invariant is underpowered at the incident's magnitude; the case leads with composition invariants instead (no matching commit).
