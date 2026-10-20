# Changelog

## 28 September 2026 (unreleased)

- Repository scaffold, package skeleton, `srm_check` with unit tests.
- Case 01 generator `simulate_checkout_experiment` (six event tables, truth table, manifest with content checksums) and `abf simulate case01`.
- Case 01 design specification (kept outside the repository).
- `difference_in_proportions`, `invariant_check`, `exposure_rate_by_segment`, `event_arrival_lag` with unit tests; `reporting.style`.
- Case 01 notebook executed with three figures, README, decision memo and data contract.
- Spec deviation recorded: the pre-period invariant is underpowered at the incident's magnitude; the case leads with composition invariants instead.

## 29 September 2026 (unreleased)

- Case 01 generator reworked for operating characteristics: per-replicate assignment salt; one independent random stream per block with every per-user quantity drawn by inverse CDF, so a parameter change moves only what depends on it; config keys and values validated with errors naming the key; an arm-independent fallback share (0.15) whose users load the payment step and order through the legacy checkout; `affects_arm` validated (treatment, control, both); configurable affected-cell conversion deficit; order timing drawn per user so the day-14 dashboard can be simulated; `payment_step_error` in place of `payment_failed`; `refunded_at` on orders; ITT and rendered truths in the manifest.
- `abf oc case01`: scenarios A to D and F (symmetric loss), four estimators (day-14 dashboard, exposed on complete data, exposed with the affected segment removed, ITT), each scored against its own target and the ITT estimand; loss and conversion-deficit sweeps; paired contrasts, the rescaled ship contrast, the case-story rate and Clopper-Pearson intervals; figure 4; units cached by a hash of their inputs, library versions and code, written atomically and resumable; summary tables tracked under `results/case01/`.
- Tests: coverage of the relative-lift interval at the case regime, scenario claims, the case-seed rule, the command's cache, budget and argument handling, per-user common random numbers, the sufficient-statistic estimators against explicit masks, config validation; a weekly, on-demand and pull-request CI job runs the slow tests, and CI installs from the lock file.
- Case 01 README, decision memo, data contract, spec and notebook refreshed: power against the +1.0% margin, an ordered decision rule, guardrail margins with a stated verdict rule and pass probabilities, the second-look boundary and its cost, payment-step errors per assigned user, latency with a user-clustered bootstrap, refunds on the matured cohort with their design completion date, the dashboard read on day 14, a segment-excluded estimate, the case seed chosen by a stated and tested rule with its selection disclosed, and the recommendation to rerun with the fix.
- Manifest regenerated so its embedded config carries the +10% refund margin (data checksums unchanged); a unit test now fails when the committed manifest and `config.yaml` disagree.
- `.gitattributes` normalises text files to LF so outputs regenerated on Windows do not show as whole-file diffs.
- `oc_meta.json` records the Python version and machine for each unit and, separately, for the run that assembled the tables (a cache-only rerun on another machine previously overwrote the single `python` field); keys are sorted and the OC CSVs and meta are written with LF line endings on every platform.
- Case 02 design specification (v1.0, kept outside the repository) written and reviewed before any Case 02 code: three estimands, a delete-one-user jackknife shared with later cases, sample size set against the decision margin; root README roadmap updated.
- `requires-python` raised to 3.12 to match the documented support; root README CI versions corrected.

## 30 September 2026 (unreleased)

- Case 02 spec v1.1: EB-NeRD data decision, sample size at the measured CV (a9c98dc)
- Case 02 spec v1.1: section 5 draws per arm (5425400)
- Case 02 spec v1.2 (inputs settled, derived-figures table); config.yaml; jackknife_variance and cluster_bootstrap with tests (73fb241)

## 1 October 2026 (unreleased)

- Case 02 spec v1.4; ratio_of_sums array kernel; Case 02 generator with tests (5459b28)

## 4 October 2026 (unreleased)

- Case 02 harness: leaked tier, oc_case02, abf oc and simulate case02, tests (c679227)
- Case 02 spec v1.5: re-sized to 57,600 per arm from the measured scenario-C check; abf oc case02 results at 115,200 draws (194ddcb)

## 5 October 2026 (unreleased)

- Case 02: LF pins in the manifest and OC writers, case seed 20261002, committed run at 115,200 draws, manifest test over both cases (90a3c2c)
- Case 02 notebook: builder kept outside the repository, abf run case02 through cmd_run, three tests, executed outputs and figures (191fa9e)

## 6 October 2026 (unreleased)

- README: Case 02 status after the notebook; ignore a local configuration file (2f131bf)
- Case 02 spec v1.6: section 14 ratio disclosure; section 16 item 7 closed, guardrail kept simulated and disclosed (8b99cd5)

## 7 October 2026 (unreleased)

- Case 01 README: provenance from fresh-clone runs on Windows 11 and Linux (WSL2) at 8b99cd5; oc_meta from the Linux run (c55a69d)
- LF line endings for the Case 01 manifest and executed notebooks, with tests; ruff format (30be84f)
- Lint: wrap long lines, zip strict=True, imports at top, lowercase test name (f122143)
- Case 02 notebook from the cold-cache run of 7 October (M1): 20.8 s wall-clock, 5.6 s in-notebook (b126047)
- README: hero figure, Case 01 headline numbers, case cards, mechanism diagram, collapsible reference sections (55f70f6)

## 8 October 2026 (unreleased)

- Export Case 02 functions and result classes and add denominator blockers (ccd1d5c)
- Spec section 11: cluster_bootstrap covers statistics of totals; the Case 01 p95 bootstrap stays in the notebook (144321e)
- Add tracked-data licence guard for Case 02 (e002486)

## 9 October 2026 (unreleased)

- Add Case 02 parametric generator, calibration and validation (partial) (68b7d2b)
- Package Case 02 calibration and fresh validation (d8ba714)
- Add isolated Case 02 synthetic simulation (ef94020)

## 11 October 2026 (unreleased)

- T5b2: abf oc case02 --sweeps, sweep tables, fig4, measured 41.12 min (35bc8ff)
- C1: public-text corrections to Case 01 and Case 02 specs (arXiv id, arm sizes, active-user selection, tier wording) (346834c)
- C2: unit aggregation raises on non-finite values instead of silently totalling them as zero (6b359ba)
- C3: independent-seed sizing confirmation (0.8215 at oc seed 20261011) recorded in spec section 5 (da73037)
- Case 02 README and decision memo (40d23c0)
- Record measured sweeps and simulate timings; soften an aliasing claim (f08006d)
- Case 02 manifest: add parametric generator provenance block (0ab2dc8)
