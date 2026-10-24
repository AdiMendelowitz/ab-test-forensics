"""Unit tests for the Case 02 generator.

All but the last test run on a small synthetic base, so they need no EB-NeRD download.
"""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from abforensics.simulation import ranking_incident as ri

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw" / "ebnerd_small"
CONFIG = {
    "seed": None,
    "base": {"raw_dir": "data/raw/ebnerd_small"},
    "design": {
        "total_draws": 6000,
        "allocation": {"control": 0.5, "treatment": 0.5},
        "arrival_delay_hours": {"min": 0, "max": 36},
    },
    "treatment": {
        "suppression_delta": 0.15,
        "click_lift_tau": 0.025,
        "never_remove_last_inview": True,
    },
    "guardrail_simulation": {
        "per_impression_rate": 0.005,
        "low_quality_multiplier": 3.0,
        "heterogeneity_shape": 2.0,
        "population_seed": 20261001,
    },
}


@pytest.fixture(scope="module")
def base() -> ri.RankingBase:
    return ri.make_synthetic_base(300, seed=3)


@pytest.fixture(scope="module")
def data(base: ri.RankingBase) -> ri.RankingExperimentData:
    return ri.simulate_ranking_experiment(CONFIG, seed=11, base=base)


# --- keyed streams -----------------------------------------------------------------------------


def test_keyed_uniform_golden_values() -> None:
    """Integer-only hashing: identical bits on Linux and Windows."""
    cases = [
        ((0, 0, 1), 0.2691303195904541),
        ((20261003, 0, 1), 0.11483560355249056),
        ((20261003, 1, 1), 0.7725780512874509),
        ((20261003, 128199, 2), 0.013368320785132393),
        ((2**63 + 5, 7, 13), 0.644317990876101),
    ]
    for (s, k, u), expected in cases:
        assert float(ri.keyed_uniform(s, np.array([k]), u)[0]) == expected
    assert (
        float(ri.keyed_uniform(20261003, np.array([5]), 9, np.array([3]))[0]) == 0.9252252249836339
    )


def test_keyed_uniform_depends_only_on_its_own_draw() -> None:
    keys = np.arange(10_000)
    full = ri.keyed_uniform(5, keys, ri.USE["lift"])
    perm = np.random.default_rng(0).permutation(keys)[:2_000]
    assert np.array_equal(ri.keyed_uniform(5, perm, ri.USE["lift"]), full[perm])


def test_keyed_uniform_is_uniform_and_streams_are_uncorrelated() -> None:
    keys = np.arange(200_000)
    a, b = ri.keyed_uniform(1, keys, 3), ri.keyed_uniform(1, keys, 4)
    assert stats.kstest(a, "uniform").pvalue > 1e-3
    assert abs(np.corrcoef(a, b)[0, 1]) < 0.01  # across uses, same key
    assert abs(np.corrcoef(a[:-1], a[1:])[0, 1]) < 0.01  # adjacent keys
    table = np.histogram2d(a, b, bins=10)[0]
    assert stats.chisquare(table.ravel()).pvalue > 1e-3
    assert a.min() >= 0.0 and a.max() < 1.0


def test_keyed_uniform_rejects_float_keys() -> None:
    with pytest.raises(TypeError):
        ri.keyed_uniform(1, np.array([1.0]), 1)


# --- binomial inverse CDF ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n,p",
    [
        (0, 0.3),
        (1, 0.15),
        (92, 0.15),
        (1284, 0.15),
        (2187, 0.15),
        (2187, 0.005),
        (2187, 0.06),
        (40, 0.9),
        (1100, 0.49),
        (5, 1.0),
        (7, 0.0),
    ],
)
def test_binom_icdf_matches_scipy(n: int, p: float) -> None:
    u = np.r_[np.random.default_rng(n).random(4000), 1e-15, 0.5, 1 - 2**-53]
    ours = ri.binom_icdf(u, n, p)
    ref = stats.binom.ppf(u, n, p)
    boundary = np.abs(stats.binom.cdf(ref, n, p) - u) < 1e-12
    mismatch = (ours != ref) & ~boundary
    if p > 0.5:  # drawn as n - Bin(n, 1 - p) at 1 - u: equal in distribution, not pointwise
        assert abs(ours.mean() - n * p) < 4 * np.sqrt(n * p * (1 - p) / u.size) + 1
    else:
        assert mismatch.sum() == 0, (n, p, u[mismatch][:5], ours[mismatch][:5], ref[mismatch][:5])


# --- generator -----------------------------------------------------------------------------------


def test_potential_outcomes_match_the_simulated_draws(base: ri.RankingBase) -> None:
    """Section 14: the truths match the potential outcomes the generator samples from."""
    prm = ri.RankingParameters(0.15, 0.025)
    po = ri.potential_outcomes(base, prm)
    dr = ri.simulate_draws(base, prm, seed=21, total_draws=400_000)
    t = dr.arm == 1
    pop = dr.population_index[t]
    for sim, col in [
        (dr.clicks[t], "expected_clicks_treatment"),
        (dr.inviews[t], "expected_inviews_treatment"),
        (dr.clicks[t] / dr.inviews[t], "expected_user_rate_treatment"),
        (dr.feedback[t], "expected_feedback_treatment"),
    ]:
        resid = sim - po[col].to_numpy()[pop]
        assert abs(resid.mean()) < 4 * resid.std() / np.sqrt(resid.size), col
    c = ~t
    resid = dr.feedback[c] - po["expected_feedback_control"].to_numpy()[dr.population_index[c]]
    assert abs(resid.mean()) < 4 * resid.std() / np.sqrt(resid.size)


def test_truths_closed_forms(base: ri.RankingBase) -> None:
    po_c = ri.potential_outcomes(base, ri.RankingParameters(0.0, 0.025))
    m, c = base.inviews, base.clicks
    lift = np.minimum(1.0, 0.025 * c / np.maximum(m - c, 1)) * (m - c)  # zero where m == c
    assert np.allclose(po_c["expected_clicks_treatment"], c + lift)
    uncapped = c / m < 1 / 1.025
    assert np.allclose(po_c["expected_clicks_treatment"][uncapped], 1.025 * c[uncapped])
    po = ri.potential_outcomes(base, ri.RankingParameters(0.15, 0.0))
    removed = 0.15 * (base.old_candidates_clicked + base.old_candidates_unclicked)
    assert np.allclose(po["expected_inviews_treatment"], base.inviews - removed)
    assert np.allclose(
        po["expected_clicks_treatment"], base.clicks - 0.15 * base.old_candidates_clicked
    )
    tr_a = ri.scenario_truths(ri.potential_outcomes(base, ri.RankingParameters(0.0, 0.0)))
    assert all(abs(v) < 1e-12 for k, v in tr_a.items() if k.startswith("expected_relative_lift"))


def test_manifest_truths_match_potential_outcomes(data: ri.RankingExperimentData) -> None:
    tr = ri.scenario_truths(data.truth)
    for k, v in tr.items():
        assert data.manifest[k] == v
    assert (
        data.manifest["expected_relative_lift_ctr"]
        > data.manifest["expected_relative_lift_clicks_per_user"]
    )


def test_guardrail_multipliers_have_unit_weighted_mean(base: ri.RankingBase) -> None:
    g = ri.guardrail_multipliers(base, ri.RankingParameters(0.15, 0.025))
    assert np.dot(base.inviews, g) / base.inviews.sum() == pytest.approx(1.0, abs=1e-12)


def test_duplicated_draws_share_no_treatment_noise(base: ri.RankingBase) -> None:
    prm = ri.RankingParameters(0.15, 0.025)
    dr = ri.simulate_draws(base, prm, seed=5, total_draws=200_000)
    t = np.flatnonzero(dr.arm == 1)
    order = t[np.argsort(dr.population_index[t], kind="stable")]
    pop = dr.population_index[order]
    same = pop[1:] == pop[:-1]
    first, second = order[:-1][same], order[1:][same]
    u1 = ri.keyed_uniform(5, dr.draw_id[first], ri.USE["remove_unclicked"])
    u2 = ri.keyed_uniform(5, dr.draw_id[second], ri.USE["remove_unclicked"])
    assert not np.any(u1 == u2)
    centred = dr.removed_unclicked - base.old_candidates_unclicked[dr.population_index] * 0.15
    assert abs(np.corrcoef(centred[first], centred[second])[0, 1]) < 0.02


def test_common_random_numbers_across_scenarios(base: ri.RankingBase) -> None:
    out = {
        s: ri.simulate_draws(base, ri.parameters_from_config(CONFIG, s), 9, 20_000) for s in "ABCD"
    }
    for s in "BCD":
        assert np.array_equal(out[s].population_index, out["A"].population_index)
        assert np.array_equal(out[s].arm, out["A"].arm)
    assert np.array_equal(out["B"].inviews, out["D"].inviews)
    assert np.array_equal(out["B"].removed_clicked, out["D"].removed_clicked)
    assert np.array_equal(out["A"].clicks, out["B"].clicks + out["B"].removed_clicked)
    assert np.array_equal(out["A"].feedback_other, out["D"].feedback_other)
    assert np.all(out["C"].clicks >= out["A"].clicks)


def test_last_inview_is_never_removed(base: ri.RankingBase) -> None:
    prm = ri.RankingParameters(1.0, 0.0)  # remove every candidate
    dr = ri.simulate_draws(base, prm, seed=2, total_draws=4_000)
    ex = ri.expand_inviews(base, dr, seed=2)
    last = np.zeros(base.iv_old.size, dtype=bool)
    last[base.offsets[1:] - 1] = True
    assert ex.loc[last[ex["inview_index"].to_numpy()], "kept"].all()
    assert dr.inviews.min() >= 1
    t = dr.arm == 1
    assert np.array_equal(
        dr.inviews[t],
        base.inviews[dr.population_index[t]]
        - base.old_inviews[dr.population_index[t]]
        + base.iv_old[base.offsets[1:] - 1][dr.population_index[t]],
    )


def test_tables_reproduce_the_fast_path_counts(data: ri.RankingExperimentData) -> None:
    dr = data.draws
    n_imp = data.impressions.groupby("draw_id").size().reindex(dr.draw_id, fill_value=0).to_numpy()
    n_clk = data.clicks.groupby("draw_id").size().reindex(dr.draw_id, fill_value=0).to_numpy()
    n_fb = data.feedback.groupby("draw_id").size().reindex(dr.draw_id, fill_value=0).to_numpy()
    assert np.array_equal(n_imp, dr.inviews)
    assert np.array_equal(n_clk, dr.clicks)
    assert np.array_equal(n_fb, dr.feedback)
    assert len(data.users) == len(data.assignments) == CONFIG["design"]["total_draws"]
    assert set(data.assignments["arm"]) == {"control", "treatment"}
    assert (data.clicks["received_at"] >= data.clicks["clicked_at"]).all()
    assert data.telemetry_health["inviews"].sum() == len(data.impressions)


def test_removed_counts_by_class(base: ri.RankingBase) -> None:
    prm = ri.RankingParameters(0.5, 0.0)
    dr = ri.simulate_draws(base, prm, seed=8, total_draws=3_000)
    ex = ri.expand_inviews(base, dr, seed=8)
    clicked = base.iv_click[ex["inview_index"].to_numpy()]
    rem = ~ex["kept"].to_numpy()
    by = (
        pd.DataFrame({"d": ex["draw_id"], "rc": rem & clicked, "ru": rem & ~clicked})
        .groupby("d")
        .sum()
    )
    assert np.array_equal(by["rc"].to_numpy(), dr.removed_clicked)
    assert np.array_equal(by["ru"].to_numpy(), dr.removed_unclicked)


def test_simulation_is_reproducible(base: ri.RankingBase) -> None:
    a = ri.simulate_ranking_experiment(CONFIG, seed=4, base=base)
    b = ri.simulate_ranking_experiment(copy.deepcopy(CONFIG), seed=4, base=base)
    for name in a.tables():
        pd.testing.assert_frame_equal(a.tables()[name], b.tables()[name])


def test_null_seed_is_rejected(base: ri.RankingBase) -> None:
    with pytest.raises(ValueError, match="seed"):
        ri.simulate_ranking_experiment(CONFIG, base=base)


# --- licence: no per-user data in tracked files ---------------------------------------------------

# --- the real base (skipped without the download) -------------------------------------------------


@pytest.mark.skipif(
    not (RAW / "train" / "behaviors.parquet").exists(), reason="EB-NeRD small not downloaded"
)
def test_real_base_matches_the_base_measurements() -> None:
    b = ri.load_base(RAW)
    assert b.n_users == 15_143
    assert b.summary["inviews"] == 2_585_747
    assert b.tier_cut_hours == pytest.approx(9.2, abs=0.1)
    assert b.summary["ctr_ratio_of_sums"] == pytest.approx(0.0906, abs=5e-4)
    assert b.inviews.max() < 4096
    assert (b.old_candidates_clicked + b.old_candidates_unclicked <= b.inviews - 1).all()
