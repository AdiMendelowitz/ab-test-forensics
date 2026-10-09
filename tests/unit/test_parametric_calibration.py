"""Packaged procedures match the ADR-07 C1/C2 scratch measurements."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from abforensics.simulation import ranking_incident as ri

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings():
    return yaml.safe_load(
        (REPO / "case_studies/02_metric_that_told_two_stories/config.yaml").read_text()
    )["parametric_generator"]


def test_validation_matches_scratch_golden(settings):
    from abforensics.simulation import parametric_calibration as pc

    expected = json.loads((REPO / "tests/fixtures/case02_parametric_validation.json").read_text())
    result = pc.validate_parametric(settings, populations=3, splits=40)
    assert result["populations"] == expected["populations"]
    assert result["splits"] == expected["splits"]
    for actual, reference in zip(
        result["population_values"], expected["population_values"], strict=True
    ):
        assert actual.keys() == reference.keys()
        for key in actual:
            np.testing.assert_allclose(actual[key], reference[key], rtol=0, atol=1e-12)
    assert result["summary"] == expected["summary"]


def test_validation_seeds_do_not_overlap_calibration_or_population(settings):
    from abforensics.simulation import parametric_calibration as pc

    calibration = pc.population_seeds(settings, "calibration")
    validation = pc.population_seeds(settings, "validation")
    assert calibration == list(range(20261009, 20261009 + 20))
    assert validation == list(range(20262010, 20262010 + 20))
    assert not set(calibration) & set(validation)
    assert settings["population_seed"] == 20263011
    assert settings["population_seed"] not in calibration + validation


def test_calibration_reuses_draws_and_preserves_stored_theta(settings, monkeypatch):
    from abforensics.simulation import parametric_calibration as pc

    original = dict(settings["theta"])
    theta = np.array([original[key] for key in ("a", "b", "sigma", "gamma")])
    residuals, starts = [], []

    def optimiser(fun, start, *, bounds, diff_step):
        starts.append(list(start))
        assert bounds[0][2] == 0
        assert diff_step == 0.02
        residuals.extend([fun(theta), fun(theta)])
        return SimpleNamespace(x=theta, cost=0.0, success=True, message="fixture")

    monkeypatch.setattr(pc, "least_squares", optimiser)
    result = pc.calibrate_parametric(settings, populations=3, splits=40)
    assert starts == [[-2.3, -0.1, 0.3, -0.9], [-2.0, -0.3, 0.6, -0.6]]
    assert result["pass"]
    assert settings["theta"] == original
    for residual in residuals[1:]:
        np.testing.assert_array_equal(residual, residuals[0])
    expected = []
    for seed in pc.population_seeds(settings, "calibration", populations=3):
        values = ri.parametric_statistics(
            ri.parametric_counts(settings, seed=seed), splits=40, seed=seed
        )
        expected.append([values[key] for key in pc.TARGETS])
    target = np.array([settings["targets"][key] for key in pc.TARGETS])
    tolerance = np.array([settings["tolerances"][key] for key in pc.TARGETS])
    np.testing.assert_array_equal(residuals[0], (np.mean(expected, axis=0) - target) / tolerance)
