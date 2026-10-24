"""Unit tests for difference_in_means."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from abforensics.inference import difference_in_means

EST = "clicks per eligible user"


def test_welch_matches_scipy() -> None:
    rng = np.random.default_rng(20)
    y_t = rng.negative_binomial(1, 0.06, size=800).astype(float)
    y_c = rng.negative_binomial(1.3, 0.08, size=1_100).astype(float)
    r = difference_in_means(y_t, y_c, estimand=EST)
    ref = stats.ttest_ind(y_t, y_c, equal_var=False)
    assert r.p_value == pytest.approx(ref.pvalue, rel=1e-9)
    ci = ref.confidence_interval(0.95)
    assert r.confidence_interval == pytest.approx((ci.low, ci.high), rel=1e-9)
    assert r.degrees_of_freedom == pytest.approx(ref.df, rel=1e-9)


def test_log_ratio_interval_covers_at_nominal_rate() -> None:
    rng = np.random.default_rng(21)
    reps, n, mu = 2_000, 2_000, 15.5
    size = 0.75  # negative binomial size, CV about 1.2 as on the base (measured CV 1.153)
    p = size / (size + mu)
    covered = 0
    for _ in range(reps):
        y_t = rng.negative_binomial(size, p, n)
        y_c = rng.negative_binomial(size, p, n)
        lo, hi = difference_in_means(y_t, y_c, estimand=EST).relative_confidence_interval
        covered += lo <= 0.0 <= hi
    # MC SE at 95% and 2,000 reps is 0.49 points; the band is about 4 SE wide either side.
    assert 0.93 <= covered / reps <= 0.97


def test_relative_lift_and_known_answer() -> None:
    y_t = np.array([2.0, 4.0, 6.0, 8.0])
    y_c = np.array([1.0, 2.0, 3.0, 4.0])
    r = difference_in_means(y_t, y_c, estimand=EST)
    assert r.absolute_difference == pytest.approx(2.5)
    assert r.relative_lift == pytest.approx(1.0)
    se_log = np.sqrt(np.var(y_t, ddof=1) / (4 * 25) + np.var(y_c, ddof=1) / (4 * 6.25))
    lo, hi = r.relative_confidence_interval
    assert np.log(1 + hi) - np.log(2) == pytest.approx(stats.norm.ppf(0.975) * se_log)
    assert lo < 1.0 < hi


def test_zero_mean_and_input_errors() -> None:
    r = difference_in_means(np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.0, 0.0]), estimand=EST)
    assert np.isnan(r.relative_lift)
    assert any("log-ratio" in w for w in r.warnings)
    with pytest.raises(ValueError, match="two units"):
        difference_in_means(np.array([1.0]), np.array([1.0, 2.0]), estimand=EST)
    with pytest.raises(ValueError, match="finite"):
        difference_in_means(np.array([1.0, np.nan]), np.array([1.0, 2.0]), estimand=EST)
    with pytest.raises(ValueError, match="alpha"):
        difference_in_means(np.array([1.0, 2.0]), np.array([1.0, 2.0]), 1.5, estimand=EST)
