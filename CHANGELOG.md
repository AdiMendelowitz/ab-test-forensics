# Changelog

## 28 September 2026 (unreleased)

- Repository scaffold, package skeleton, `srm_check` with unit tests.
- Case 01 generator `simulate_checkout_experiment` (six event tables, truth table, manifest with content checksums) and `abf simulate case01`.
- Case 01 design specification (docs/design/case-01.md).
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
- Case 02 design specification (`docs/design/case-02.md`, v1.0) written and reviewed before any Case 02 code: three estimands, a delete-one-user jackknife shared with later cases, sample size set against the decision margin; root README roadmap updated.
- `requires-python` raised to 3.12 to match the documented support; root README CI versions corrected.
