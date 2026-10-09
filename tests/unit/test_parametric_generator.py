"""Generator checks from ADR-07 Amendment 1, G1 to G14."""

import numpy as np
import pytest

from abforensics.simulation import ranking_incident as ri

SETTINGS = {
    "population_users": 15143,
    "mean_inviews": 170.8,
    "sd_inviews": 205.2,
    "minimum_inviews": 5,
    "old_shares": [0.1672, 0.2182, 0.2472, 0.2710, 0.2720, 0.2868, 0.2946, 0.3066, 0.3312, 0.3851],
    "old_share_concentration": 9,
    "population_seed": 20261011,
    "theta": {"a": -2.09, "b": 0.020, "sigma": 0.135, "gamma": -1.02},
}


def test_counts_are_reproducible_positive_and_bounded():
    a = ri.parametric_counts(SETTINGS)
    b = ri.parametric_counts(SETTINGS)
    for name in ("inviews", "clicks", "old_inviews", "old_clicks", "decile"):
        np.testing.assert_array_equal(getattr(a, name), getattr(b, name))
    assert np.all(a.inviews >= 5)
    assert np.all((a.clicks >= 1) & (a.clicks <= a.inviews))
    assert np.all(a.old_clicks <= a.old_inviews)
    assert np.all(a.clicks - a.old_clicks <= a.inviews - a.old_inviews)


def test_gate_includes_median_uncertainty():
    values = np.linspace(1.45, 1.51, 20)
    result = ri.parametric_gate(values, 1.48, 0.03)
    expected = np.std(values, ddof=1) / np.sqrt(20) * 1.2533
    assert result["standard_error"] == pytest.approx(expected)
    assert result["used"] == pytest.approx(1.96 * expected)
    assert result["pass"]
    assert not ri.parametric_gate(values + 0.03, 1.48, 0.03)["pass"]


def test_common_draws_keep_volume_and_quality_fixed():
    draws = ri.prepare_parametric_draws(SETTINGS, 20261009)
    a = ri.parametric_counts(SETTINGS, prepared=draws)
    b = ri.parametric_counts(SETTINGS, prepared=draws, sigma=0.2)
    np.testing.assert_array_equal(a.inviews, b.inviews)
    np.testing.assert_array_equal(a.old_inviews, b.old_inviews)
    assert not np.array_equal(a.clicks, b.clicks)


def test_count_statistics_use_package_errors():
    counts = ri.parametric_counts(SETTINGS)
    result = ri.parametric_statistics(counts, splits=100, seed=20261009)
    assert result["E2"] == counts.clicks.sum() / counts.inviews.sum()
    assert result["E3"] == np.mean(counts.clicks / counts.inviews)
    assert result["old_rate"] == counts.old_clicks.sum() / counts.old_inviews.sum()
    assert result["se_ratio"] > 1


def test_frame_builder_preserves_counts_and_last_inview():
    settings = {**SETTINGS, "population_users": 100}
    counts = ri.parametric_counts(settings)
    iv, users = ri.parametric_frames(counts, seed=settings["population_seed"])
    assert "leaked_low" not in iv
    base = ri.build_base(iv, users, tier_cut_hours=9.2)
    np.testing.assert_array_equal(base.inviews, counts.inviews)
    np.testing.assert_array_equal(base.clicks, counts.clicks)
    np.testing.assert_array_equal(base.old_inviews, counts.old_inviews)
    old_clicks = np.add.reduceat(base.iv_old & base.iv_click, base.offsets[:-1])
    np.testing.assert_array_equal(old_clicks, counts.old_clicks)
    assert not base.iv_candidate[base.offsets[1:] - 1].any()
