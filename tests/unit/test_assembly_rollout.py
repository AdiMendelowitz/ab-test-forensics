"""Implementation tests for the Case 01 rollout.

The spec-derived tests written by an independent executor are in test_assembly_rollout_spec.py.
"""

from pathlib import Path

import numpy as np
import pytest
import yaml

from abforensics import cli
from abforensics.simulation import assembly_rollout as ar
from abforensics.simulation import rollout_commands as rc

ROOT = Path(__file__).resolve().parents[2]
CFG = yaml.safe_load((ROOT / rc.CONFIG).read_text(encoding="utf-8"))
CASE01 = yaml.safe_load(
    (ROOT / "case_studies" / "01_lost_logs_found_lift" / "config.yaml").read_text(encoding="utf-8")
)


@pytest.fixture(scope="module")
def prm():
    return ar.validate_rollout_config(CFG)


def small(prm, per_day=2000):
    return ar.with_changes(prm, per_day=per_day)


def test_shared_values_match_case01_config():
    for key in (
        "eligible_products_per_day",
        "baseline_yield_rate",
        "true_relative_lift",
        "legacy_route_probability",
        "outcome_window_days",
    ):
        assert CFG[key] == CASE01[key], key
    for key in ("supply_line", "batch_below", "scan_loss_probability", "affects_arm"):
        assert CFG["incident"][key] == CASE01["incident"][key], key
    assert (
        CFG["decision_rule"]["ship_if_lower_bound_above_relative"]
        == CASE01["decision_rule"]["ship_if_lower_bound_above_relative"]
    )
    assert CFG["operating_characteristics"]["seed"] != CASE01["operating_characteristics"]["seed"]


def test_schedule_must_tile_the_days():
    bad = {**CFG, "schedule": [{"days": [1, 7], "p": 0.01}, {"days": [9, 28], "p": 0.5}]}
    with pytest.raises(ValueError, match="tile"):
        ar.validate_rollout_config(bad)


def test_expected_arm_means_follow_the_constants(prm):
    # spec section 7 test (c)
    p0, p1 = ar.expected_rates(prm)
    d = np.arange(1, 29)
    assert np.allclose(p0, 0.032 * (1 + 0.003125 * (d - 14.5)), rtol=0, atol=1e-15)
    assert np.allclose(p1 / p0, 1 + 0.85 * 0.03, rtol=1e-14)


def test_relative_estimate_unavailable_at_zero_control_mean(prm):
    # spec section 7 test (d): the aggregate zero rule
    c = ar.DayArmCounts(
        n1=np.full((1, 28), 100),
        y1=np.ones((1, 28), int),
        n0=np.full((1, 28), 100),
        y0=np.zeros((1, 28), int),
    )
    e = ar.estimate_day_standardised(c, prm)
    assert np.isnan(e["rel"][0]) and np.isfinite(e["abs"][0])
    one = c.y0.copy()
    one[0, 3] = 1  # a single non-zero day keeps the aggregate above the threshold
    e = ar.estimate_day_standardised(ar.DayArmCounts(c.n1, c.y1, c.n0, one), prm)
    assert np.isfinite(e["rel"][0])


def test_empty_arm_day_makes_day_standardised_unavailable(prm):
    c = ar.DayArmCounts(
        n1=np.full((1, 28), 100),
        y1=np.full((1, 28), 3),
        n0=np.full((1, 28), 100),
        y0=np.full((1, 28), 3),
    )
    c.n1[0, 0] = 0
    c.y1[0, 0] = 0
    assert np.isnan(ar.estimate_day_standardised(c, prm)["abs"][0])


def test_canary_statistic_zero_logged_scans_is_no_flag():
    assert ar.canary_statistic(np.asarray([0]), np.asarray([0]), 0.1)[0] == 0.0


def test_hash_uniform_is_sticky_and_uniform():
    ids = np.arange(1, 200_001)
    u1 = ar._hash_uniform(ids, 12345)
    assert np.array_equal(u1, ar._hash_uniform(ids, 12345))
    assert not np.array_equal(u1, ar._hash_uniform(ids, 12346))
    assert 0.0 <= u1.min() and u1.max() < 1.0
    assert abs(u1.mean() - 0.5) < 0.005
    assert abs((u1 < 0.01).mean() - 0.01) < 0.001


def test_product_level_run_is_reproducible(prm):
    p = small(prm)
    a = ar.simulate_rollout_population(p, 7)
    b = ar.simulate_rollout_population(p, 7)
    for key in a:
        assert np.array_equal(a[key], b[key]), key


def test_product_level_baseline_matches_the_day_means(prm):
    pop = ar.simulate_rollout_population(prm, CFG["seed"])
    p0, _ = ar.expected_rates(prm)
    by_day = np.bincount(pop["arrival_day"] - 1, weights=pop["p_control"]) / prm.per_day
    assert np.allclose(by_day, p0, rtol=0.01)
    assert abs(pop["p_control"].mean() - prm.baseline) < 1e-6  # calibrated on realised covariates


@pytest.mark.slow
def test_two_layers_agree_in_distribution(prm):
    """The day-by-arm sampler against the product-level generator (module docstring claim)."""
    p = small(prm, per_day=4000)
    b = 300
    prod = [
        ar.counts_from_population(ar.simulate_rollout_population(p, 1000 + i), p) for i in range(b)
    ]
    n1 = np.vstack([c.n1 for c in prod])
    y1 = np.vstack([c.y1 for c in prod])
    y0 = np.vstack([c.y0 for c in prod])
    agg = ar.sample_day_arm_counts(p, 20_000, np.random.default_rng(5))
    for name, x, z in (("n1", n1, agg.n1), ("y1", y1, agg.y1), ("y0", y0, agg.y0)):
        for s in range(p.n_stages):
            m = ar.stage_of_day(p) == s
            a, g = x[:, m].sum(1), z[:, m].sum(1)
            se = np.sqrt(a.var(ddof=1) / a.size + g.var(ddof=1) / g.size)
            assert abs(a.mean() - g.mean()) < 4 * se + 1e-9, (name, s)
            assert 0.75 < a.var(ddof=1) / g.var(ddof=1) < 1.33, (name, s)


def test_adding_a_sweep_point_leaves_the_others_unchanged(prm):
    kw = dict(replicates=200, canary_replicates=200, seed=3, s2_stage_lifts=[0, 0.015, 0.03])
    a, _, _ = ar.run_operating_characteristics(prm, kappa_sweep=[0.0, 0.003125], **kw)
    b, _, _ = ar.run_operating_characteristics(prm, kappa_sweep=[0.0, 0.003125], **kw)
    assert a == b
    s1a = [r for r in a if r["scenario"] == "S1"]
    c, _, _ = ar.run_operating_characteristics(prm, kappa_sweep=[0.0], **kw)
    s1c = [r for r in c if r["scenario"] == "S1"]
    assert s1a != [] and len(s1a) == len(s1c)


def test_canary_flag_uses_reference_mcse():
    assert ar._canary_flag(1.0, 0.9999999490, 10_000) == ""
    assert ar._canary_flag(0.70, 0.6346, 10_000) != ""


def test_cli_writes_lf_tables(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    dest = tmp_path / rc.CONFIG
    dest.parent.mkdir(parents=True)
    cfg = {**CFG, "eligible_products_per_day": 500}
    cfg["operating_characteristics"] = {
        **CFG["operating_characteristics"],
        "replicates": 50,
        "canary_replicates": 50,
    }
    dest.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["simulate", "case01-rollout"]) == 0
    assert cli.main(["run", "case01-rollout"]) == 0
    assert cli.main(["oc", "case01-rollout"]) == 0
    out = tmp_path / rc.RESULTS
    for name in (
        "run_estimates.csv",
        "run_canary.csv",
        "oc_rollout_scenarios.csv",
        "oc_rollout_canary.csv",
        "oc_rollout_meta.json",
        "oc_rollout_s1_distribution.csv",
    ):
        raw = (out / name).read_bytes()
        assert raw and b"\r\n" not in raw, name
    reps = (out / "parts" / "oc_rollout_s1_replicates.csv").read_text(encoding="utf-8").splitlines()
    assert reps[0] == "replicate,pooled_rel,day_standardised_rel,final_stage_rel"
    dist = (out / "oc_rollout_s1_distribution.csv").read_text(encoding="utf-8").splitlines()
    assert dist[0] == "estimator,percentile,relative_estimate"
    assert len(dist) - 1 == 3 * 199 <= 1000  # the repository data guard's row limit
    manifest = tmp_path / "data" / "manifests" / "case01-rollout.json"
    assert b"\r\n" not in manifest.read_bytes()


def test_reading_rule_flags_relative_scale(prm):
    """A relative bias beyond 3 MCSE is flagged even when the absolute bias is not (spec v1.2)."""
    rng = np.random.default_rng(11)
    b = 4000
    truth = ar.truths(prm)
    ref = ar.pooled_expectation(prm)

    def est(abs_shift, rel_shift):
        t_abs, t_rel = truth["E_ramp_abs"], truth["E_ramp_rel"]
        a = t_abs + abs_shift + rng.normal(0, 1e-4, b)
        r = t_rel + rel_shift + rng.normal(0, 1e-3, b)
        return {
            "abs": a,
            "abs_lo": a - 1,
            "abs_hi": a + 1,
            "rel": r,
            "rel_lo": r - 1,
            "rel_hi": r + 1,
        }

    e = {
        "pooled": est(ref["abs"] - truth["E_ramp_abs"], ref["rel"] - truth["E_ramp_rel"]),
        "day_standardised": est(0.0, 0.001),
        "final_stage": est(
            truth["E_final_abs"] - truth["E_ramp_abs"], truth["E_final_rel"] - truth["E_ramp_rel"]
        ),
    }
    rows = {r["estimator"]: r for r in ar.summarise(e, truth, ref, prm, "S1", prm.kappa)}
    assert "mean_rel" in rows["day_standardised"]["flag"]
    assert "mean_abs" not in rows["day_standardised"]["flag"]


def test_s1_replicates_are_returned(prm):
    _, _, extra = ar.run_operating_characteristics(
        prm,
        replicates=50,
        canary_replicates=50,
        seed=1,
        kappa_sweep=[0.0],
        s2_stage_lifts=[0, 0.015, 0.03],
    )
    reps = extra["S1_replicates"]
    assert set(reps) == set(ar.ESTIMATORS)
    assert all(len(v) == 50 for v in reps.values())
