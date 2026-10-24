"""Tests for the operating-characteristic runner and the ``abf oc`` command.

Fast tests check structure, determinism, the common-random-number contract, input
validation, the Monte Carlo arithmetic and the command's cache on a small population. Slow
tests check the statistical claims the case README makes, each at a stated B with a
tolerance of four Monte Carlo standard errors, on a fixed seed (so they are deterministic).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from abforensics.simulation.assembly_incident import simulate_population
from abforensics.simulation.operating_characteristics import (
    ESTIMATORS,
    SCENARIOS,
    Variant,
    _one_replicate,
    case_story,
    paired_contrasts,
    run_scenarios,
    run_sweep,
    scenario_config,
    simulate_replicates,
    summarise,
)

SMALL = {
    "seed": 1,
    "days": 2,
    "eligible_products_per_day": 2_000,
    "baseline_yield_rate": 0.032,
    "true_relative_lift": 0.03,
    "outcome_window_days": 7,
    "legacy_route_probability": 0.15,
    "decision_rule": {"ship_if_lower_bound_above_relative": 0.01},
    "records_pipeline": {"arrival_delay_hours": {"min": 0, "max": 36}},
    "incident": {
        "supply_line": "A",
        "batch_below": "4",
        "scan_loss_probability": 0.35,
        "affects_arm": "treatment",
    },
}
FULL = {**SMALL, "days": 14, "eligible_products_per_day": 20_000}


def test_scenario_config_does_not_mutate_and_rejects_unknown_keys() -> None:
    cfg = scenario_config(SMALL, lift=0.0, overrides={"incident.scan_loss_probability": 0.1})
    assert cfg["incident"]["scan_loss_probability"] == 0.1
    assert cfg["true_relative_lift"] == 0.0
    assert SMALL["incident"]["scan_loss_probability"] == 0.35
    with pytest.raises(ValueError, match="cannot vary"):
        scenario_config(SMALL, lift=0.03, overrides={"incident.scan_loss": 0.9})


def test_sweep_rejects_unknown_parameter() -> None:
    with pytest.raises(ValueError, match="cannot sweep"):
        run_sweep(SMALL, "older_batch_offset", [0.0], B=2, seed=0)


def test_scenarios_have_one_row_per_scenario_and_estimator() -> None:
    table, reps = run_scenarios(SMALL, B=5, seed=0)
    k = len(ESTIMATORS)
    assert len(table) == k * len(SCENARIOS)
    assert table["scenario"].tolist()[::k] == list(SCENARIOS)
    assert table["estimator"].tolist()[:k] == list(ESTIMATORS)
    for col in (
        "bias_mc_se",
        "bias_vs_itt_mc_se",
        "coverage_mc_se",
        "coverage_ci_low",
        "rejection_rate_ci_high",
        "rule_ship_rate_mc_se",
    ):
        assert table[col].notna().all(), col
    assert (table["B"] == 5).all() and (table["n_undefined"] == 0).all()
    assert len(reps) == 5 * k * len(SCENARIOS)


def test_replicates_are_deterministic_and_independent_of_chunking() -> None:
    from concurrent.futures import ProcessPoolExecutor

    variants = [Variant("D", 0.35, "treatment")]
    a = simulate_replicates(SMALL, variants, 6, seed=11)
    with ProcessPoolExecutor(max_workers=2) as pool:
        b = simulate_replicates(SMALL, variants, 6, seed=11, executor=pool, chunks=3)
    pd.testing.assert_frame_equal(a, b)
    c = simulate_replicates(SMALL, variants, 6, seed=12)
    assert not a["estimate"].equals(c["estimate"])


def test_common_random_numbers_across_scenarios() -> None:
    _, reps = run_scenarios(SMALL, B=8, seed=4)
    itt = reps[reps["estimator"] == "itt_routed"].set_index(["variant", "replicate"])
    # The incident never touches the ITT analysis, and A/B (C/D) share one population.
    pd.testing.assert_series_equal(
        itt.loc["A", "estimate"], itt.loc["B", "estimate"], check_names=False
    )
    pd.testing.assert_series_equal(
        itt.loc["C", "estimate"], itt.loc["D", "estimate"], check_names=False
    )
    assert reps.groupby("replicate")["assignment_srm_p"].nunique().eq(1).all()


def test_loss_sweep_on_one_population_matches_separate_runs() -> None:
    swept = run_sweep(SMALL, "incident.scan_loss_probability", [0.1, 0.5], B=3, seed=0)
    assert swept["value"].tolist() == [0.1] * len(ESTIMATORS) + [0.5] * len(ESTIMATORS)
    single = run_sweep(SMALL, "incident.scan_loss_probability", [0.5], B=3, seed=0)
    cols = ["estimator", "mean_estimate", "coverage", "scan_srm_rate_0.001"]
    pd.testing.assert_frame_equal(
        swept[swept["value"] == 0.5][cols].reset_index(drop=True), single[cols]
    )


def test_assignment_varies_across_replicates() -> None:
    reps = simulate_replicates(SMALL, [Variant("A", 0.0, "treatment")], 20, seed=3)
    assert reps["assignment_srm_p"].nunique() > 1


def _reps(**cols: list) -> pd.DataFrame:
    n = len(cols["estimate"])
    base = {
        "variant": ["x"] * n,
        "estimator": ["e"] * n,
        "replicate": list(range(n)),
        "true_effect": [0.0] * n,
        "true_effect_itt": [0.0] * n,
        "ci_low": [-1.0] * n,
        "ci_high": [1.0] * n,
        "p_value": [0.5] * n,
        "rule_margin": [0.01] * n,
        "dilution": [1.0] * n,
        "n_analysed": [10] * n,
        "assignment_srm_p": [0.5] * n,
        "scan_srm_p": [0.5] * n,
    }
    base.update(cols)
    return pd.DataFrame(base)


def test_mc_se_and_clopper_pearson() -> None:
    reps = _reps(estimate=[0.1, -0.1, 0.2, -0.2], ci_high=[1.0, 1.0, 1.0, -0.5])
    s = summarise(reps).iloc[0]
    assert s["coverage"] == 0.75
    assert s["coverage_mc_se"] == pytest.approx(math.sqrt(0.75 * 0.25 / 4))
    assert s["bias"] == pytest.approx(0.0)
    assert s["bias_mc_se"] == pytest.approx(np.std([0.1, -0.1, 0.2, -0.2], ddof=1) / 2)
    # 0 of 4: Wald SE is 0 but the Clopper-Pearson upper bound is 1 - 0.025 ** (1/4).
    assert s["rejection_rate"] == 0.0 and s["rejection_rate_mc_se"] == 0.0
    assert s["rejection_rate_ci_high"] == pytest.approx(1 - 0.025**0.25)


def test_undefined_estimates_share_one_denominator() -> None:
    reps = _reps(estimate=[0.1, np.nan, 0.2, -0.2, 0.0])
    s = summarise(reps).iloc[0]
    assert s["B"] == 4 and s["n_undefined"] == 1
    assert s["coverage"] == 1.0


def test_paired_contrast_se_uses_the_pairing() -> None:
    a = _reps(estimate=[0.1] * 6, p_value=[0.01, 0.01, 0.5, 0.5, 0.01, 0.5])
    b = _reps(estimate=[0.1] * 6, p_value=[0.01, 0.01, 0.5, 0.5, 0.5, 0.5])
    reps = pd.concat([a, b.assign(estimator="f")], ignore_index=True)
    out = paired_contrasts(reps, [("t", ("x", "e"), ("x", "f"), "rejected")]).iloc[0]
    assert out["difference"] == pytest.approx(1 / 6)
    assert out["paired_mc_se"] == pytest.approx(np.std([0, 0, 0, 0, 1, 0], ddof=1) / math.sqrt(6))
    assert out["paired_mc_se"] < out["unpaired_mc_se"]


def _tiny_repo(root: Path, **oc_changes: object) -> None:
    case = root / "case_studies" / "01_lost_logs_found_lift"
    case.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    cfg = {
        **SMALL,
        "days": 1,
        "eligible_products_per_day": 1_500,
        "operating_characteristics": {
            "seed": 5,
            "replicates": 3,
            "sweep_replicates": 2,
            "scenario_lift": 0.03,
            "loss_probability_sweep": [0.1, 0.4],
            "older_batch_logit_offset_sweep": [-0.3, 0.0],
            **oc_changes,
        },
    }
    (case / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_abf_oc_writes_outputs_and_never_reuses_a_stale_cache(tmp_path, monkeypatch) -> None:
    from abforensics.cli import main

    _tiny_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["oc", "case01", "--jobs", "1"]) == 0
    out = tmp_path / "results" / "case01"
    for name in ("oc_scenarios", "oc_contrasts", "oc_story", "oc_loss_sweep", "oc_deficit_sweep"):
        assert (out / f"{name}.csv").exists(), name
    assert (
        tmp_path / "case_studies/01_lost_logs_found_lift/figures/fig4_operating_characteristics.png"
    ).exists()
    assert (out / "oc_story.csv").exists() and (out / "oc_meta.json").exists()
    first = pd.read_csv(out / "oc_scenarios.csv")
    n_parts = len(list((out / "parts").glob("*.parquet")))
    meta_bytes = (out / "oc_meta.json").read_bytes()
    meta = json.loads(meta_bytes)
    assert {"summary_python", "summary_machine"} <= meta.keys()
    assert all({"python", "machine", "seconds"} <= u.keys() for u in meta["units"].values())

    # Same inputs: every unit is a cache hit, outputs identical byte for byte, LF only.
    assert main(["oc", "case01", "--jobs", "1"]) == 0
    pd.testing.assert_frame_equal(first, pd.read_csv(out / "oc_scenarios.csv"))
    assert len(list((out / "parts").glob("*.parquet"))) == n_parts
    assert (out / "oc_meta.json").read_bytes() == meta_bytes
    for f in [*out.glob("oc_*.csv"), out / "oc_meta.json"]:
        assert b"\r" not in f.read_bytes(), f.name

    # A changed seed, on the command line or in the config, must recompute.
    assert main(["oc", "case01", "--jobs", "1", "--seed", "99"]) == 0
    assert not first["mean_estimate"].equals(pd.read_csv(out / "oc_scenarios.csv")["mean_estimate"])
    _tiny_repo(tmp_path, scenario_lift=0.2)
    assert main(["oc", "case01", "--jobs", "1"]) == 0
    changed = pd.read_csv(out / "oc_scenarios.csv")
    assert changed.loc[changed["scenario"] == "C", "tau_reached_scanner"].iloc[0] == 0.2
    assert not first["mean_estimate"].equals(changed["mean_estimate"])


def test_abf_oc_rejects_bad_counts(tmp_path, monkeypatch) -> None:
    from abforensics.cli import main

    _tiny_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    for argv in (["--replicates", "0"], ["--sweep-replicates", "1"], ["--jobs", "0"]):
        with pytest.raises(SystemExit):
            main(["oc", "case01", *argv])


def test_abf_oc_max_seconds_stops_between_units_and_resumes(tmp_path, monkeypatch) -> None:
    import json

    from abforensics.cli import main

    _tiny_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    # A tiny budget lets the first unit finish, then stops before the second.
    assert main(["oc", "case01", "--jobs", "1", "--max-seconds", "1e-9"]) == 3
    parts = sorted((tmp_path / "results/case01/parts").glob("*.parquet"))
    assert len(parts) == 1
    mtime = parts[0].stat().st_mtime_ns
    assert main(["oc", "case01", "--jobs", "1"]) == 0
    assert parts[0].stat().st_mtime_ns == mtime  # reused, not recomputed
    meta = json.loads((tmp_path / "results/case01/oc_meta.json").read_text(encoding="utf-8"))
    assert all("seconds" in u and "jobs" in u for u in meta["units"].values())
    with pytest.raises(SystemExit):
        main(["oc", "case01", "--max-seconds", "-5"])


def test_an_unrelated_config_edit_reuses_the_cache(tmp_path, monkeypatch) -> None:
    from abforensics.cli import main

    _tiny_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["oc", "case01", "--jobs", "1"]) == 0
    parts = tmp_path / "results/case01/parts"
    before = {p.name: p.stat().st_mtime_ns for p in parts.glob("*.parquet")}
    cfg = yaml.safe_load(
        (tmp_path / "case_studies/01_lost_logs_found_lift/config.yaml").read_text()
    )
    cfg["decision_rule"]["guardrails"] = {"return_rate_max_increase_relative": 0.5}
    cfg["outcome_window_days"] = 7.0  # same value, different YAML type
    (tmp_path / "case_studies/01_lost_logs_found_lift/config.yaml").write_text(yaml.safe_dump(cfg))
    assert main(["oc", "case01", "--jobs", "1"]) == 0
    after = {p.name: p.stat().st_mtime_ns for p in parts.glob("*.parquet")}
    assert after == before


def test_an_unreadable_sidecar_is_a_cache_miss(tmp_path, monkeypatch) -> None:
    from abforensics.cli import main

    _tiny_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["oc", "case01", "--jobs", "1"]) == 0
    parts = tmp_path / "results/case01/parts"
    side = sorted(parts.glob("*.json"))[0]
    side.write_text("{truncated", encoding="utf-8")
    assert main(["oc", "case01", "--jobs", "1"]) == 0
    assert side.read_text(encoding="utf-8").startswith("{\n")


def test_sufficient_statistics_match_explicit_masks() -> None:
    from abforensics.inference.proportions import difference_in_proportions

    seed = np.random.SeedSequence(3).spawn(1)[0]
    variants = [
        Variant("a", 0.35, "treatment"),
        Variant("b", 0.8, "both"),
        Variant("c", 0.0, "control"),
    ]
    rows = pd.DataFrame(_one_replicate(SMALL, seed, variants)).set_index(["variant", "estimator"])
    pop = simulate_population(SMALL, np.random.default_rng(seed))
    y = pop.y
    y14 = pop.received_by(SMALL["days"] * 24.0)
    treat = pop.arm == 1
    for v in variants:
        logged = pop.logged_under(v.loss_probability, v.affects_arm)
        for name, mask, outcome in (
            ("dashboard_day14", logged, y14),
            ("scanned_complete", logged, y),
            ("scanned_excluding_older_batch", pop.reached_scanner & ~pop.in_older_batch, y),
            ("itt_routed", np.ones(pop.n, dtype=bool), y),
        ):
            t, c = mask & treat, mask & ~treat
            ref = difference_in_proportions(
                int(outcome[t].sum()),
                int(t.sum()),
                int(outcome[c].sum()),
                int(c.sum()),
                estimand="x",
                analysis_unit="user",
                population="x",
            )
            got = rows.loc[(v.name, name)]
            assert got["estimate"] == pytest.approx(ref.relative_lift, rel=1e-12), (v, name)
            assert got["n_analysed"] == ref.n_treatment + ref.n_control


def test_case_story_counts_the_joint_event() -> None:
    # Six replicates covering each condition: (dashboard lower bound, ITT bounds, SRM p).
    cases = [
        (0.02, (-0.01, 0.05), 0.5),  # story
        (0.02, (0.02, 0.06), 0.5),  # ITT ships: no story
        (0.02, (-0.05, -0.01), 0.5),  # ITT below zero: no story
        (0.02, (-0.01, 0.05), 0.0001),  # assignment SRM flagged: no story
        (0.005, (-0.01, 0.05), 0.5),  # dashboard does not ship: no story
        (0.03, (0.0, 0.04), 0.5),  # story
    ]
    rows = []
    for i, (dash_low, (itt_low, itt_high), srm_p) in enumerate(cases):
        for name, lo, hi, est in (
            ("dashboard_day14", dash_low, dash_low + 0.08, 0.04 + i / 100),
            ("scanned_complete", dash_low, dash_low + 0.08, 0.05 + i / 100),
            ("itt_routed", itt_low, itt_high, (itt_low + itt_high) / 2),
        ):
            rows.append(
                {
                    "variant": "D",
                    "estimator": name,
                    "replicate": i,
                    "estimate": est,
                    "ci_low": lo,
                    "ci_high": hi,
                    "rule_margin": 0.01,
                    "assignment_srm_p": srm_p,
                }
            )
    story = case_story(pd.DataFrame(rows), "D").iloc[0]
    assert story["B"] == 6
    assert story["story_rate"] == pytest.approx(2 / 6)
    assert story["dashboard_ship_rate"] == pytest.approx(5 / 6)
    assert story["itt_extend_given_dashboard_ship"] == pytest.approx(3 / 5)
    assert story["mean_dashboard_day14_story"] == pytest.approx((0.04 + 0.09) / 2)
    assert story["mean_itt_story"] == pytest.approx((0.02 + 0.02) / 2)


def _within(value: float, target: float, se: float, k: float = 4.0) -> bool:
    return abs(value - target) <= k * se


@pytest.mark.slow
def test_scenario_claims_hold_at_the_case_regime() -> None:
    """The README's qualitative claims, at B = 600 on the case's full population."""
    from concurrent.futures import ProcessPoolExecutor

    b = 600
    with ProcessPoolExecutor(max_workers=2) as pool:
        table, reps = run_scenarios(FULL, B=b, seed=2026, executor=pool, chunks=2)
    t = table.set_index(["scenario", "estimator"])
    nominal_cov = math.sqrt(0.95 * 0.05 / b)
    nominal_rej = math.sqrt(0.05 * 0.95 / b)
    for est in ("dashboard_day14", "scanned_complete", "itt_routed"):
        a = t.loc[("A", est)]
        assert _within(a["coverage"], 0.95, nominal_cov), est
        assert _within(a["rejection_rate"], 0.05, nominal_rej), est
        assert _within(a["bias"], 0.0, a["bias_mc_se"]), est
    for s in ("B", "C", "D", "F"):
        itt = t.loc[(s, "itt_routed")]
        assert _within(itt["coverage"], 0.95, nominal_cov), s
        assert _within(itt["bias"], 0.0, itt["bias_mc_se"]), s
    for s in ("C", "D", "F"):
        x = t.loc[(s, "scanned_excluding_older_batch")]
        assert _within(x["bias"], 0.0, x["bias_mc_se"]), s
        assert _within(x["coverage"], 0.95, nominal_cov), s
    d = t.loc[("D", "dashboard_day14")]
    assert d["bias"] > 10 * d["bias_mc_se"]
    assert d["coverage"] < 0.95 - 3 * nominal_cov
    assert d["scan_srm_rate_0.001_ci_low"] > 0.99
    assert t.loc[("B", "dashboard_day14"), "rejection_rate"] > 0.05 + 3 * nominal_rej
    # Symmetric loss: no scan-level SRM beyond nominal and no bias against its own target.
    f = t.loc[("F", "dashboard_day14")]
    assert _within(f["bias"], 0.0, f["bias_mc_se"])
    assert f["scan_srm_rate_0.05"] < 0.05 + 4 * nominal_rej
    # Triggered analysis rejects zero more often than ITT when logging is intact: the paired
    # difference matches the analytic power gain (26.1% at 3.0% on ~119,000 per arm against
    # 22.9% at 2.55% on 140,000 per arm), within four paired Monte Carlo SEs.
    c = paired_contrasts(
        reps, [("c", ("C", "scanned_complete"), ("C", "itt_routed"), "rejected")]
    ).iloc[0]
    assert _within(c["difference"], 0.261 - 0.229, c["paired_mc_se"])
