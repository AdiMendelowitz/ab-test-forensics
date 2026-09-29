"""Unit tests for difference_in_proportions."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from abforensics import difference_in_proportions

KW = {
    "estimand": "ITT difference in 7-day order rate",
    "analysis_unit": "user",
    "population": "assigned",
}


def test_known_answer() -> None:
    r = difference_in_proportions(4_636, 139_778, 4_485, 140_222, **KW)
    p_t, p_c = 4_636 / 139_778, 4_485 / 140_222
    assert r.rate_treatment == pytest.approx(p_t)
    assert r.absolute_difference == pytest.approx(p_t - p_c)
    se = math.sqrt(p_t * (1 - p_t) / 139_778 + p_c * (1 - p_c) / 140_222)
    assert r.standard_error == pytest.approx(se)
    z = stats.norm.ppf(0.975)
    assert r.confidence_interval == pytest.approx((p_t - p_c - z * se, p_t - p_c + z * se))
    assert r.relative_lift == pytest.approx(p_t / p_c - 1)
    assert r.confidence_interval[0] < r.absolute_difference < r.confidence_interval[1]
    assert r.relative_confidence_interval[0] < r.relative_lift < r.relative_confidence_interval[1]
    assert r.estimand == KW["estimand"] and r.population == "assigned"


def test_equal_rates_give_zero_difference_and_p_one() -> None:
    r = difference_in_proportions(320, 10_000, 320, 10_000, **KW)
    assert r.absolute_difference == 0.0
    assert r.p_value == pytest.approx(1.0)
    assert r.relative_lift == pytest.approx(0.0)


def test_pooled_p_value_matches_scipy_chi2_for_two_by_two() -> None:
    # The two-sided pooled z-test equals the 2x2 chi-square test without continuity correction.
    a, n1, b, n2 = 300, 10_000, 360, 10_000
    r = difference_in_proportions(a, n1, b, n2, **KW)
    chi2, p, _, _ = stats.chi2_contingency([[a, n1 - a], [b, n2 - b]], correction=False)
    assert r.p_value == pytest.approx(p)


@pytest.mark.parametrize(
    ("args", "match"),
    [
        ((-1, 10, 1, 10), "non-negative"),
        ((11, 10, 1, 10), "exceed"),
        ((1, 0, 1, 10), "at least one"),
        ((1.5, 10, 1, 10), "non-negative integer"),
    ],
)
def test_invalid_counts_raise(args, match) -> None:
    with pytest.raises(ValueError, match=match):
        difference_in_proportions(*args, **KW)


def test_alpha_validation() -> None:
    with pytest.raises(ValueError, match="alpha"):
        difference_in_proportions(1, 10, 1, 10, alpha=1.0, **KW)


def test_small_cell_warns_and_zero_rate_is_handled() -> None:
    r = difference_in_proportions(3, 100, 0, 100, **KW)
    assert any("fewer than" in w for w in r.warnings)
    assert math.isnan(r.relative_lift)
    assert any("zero" in w for w in r.warnings)


def test_coverage_is_nominal_under_the_null() -> None:
    """3,000 draws at n=20,000 per arm, p=0.032: coverage of the 95% Wald interval.

    Monte Carlo SE of a 95% coverage estimate at B=3,000 is sqrt(0.95*0.05/3000) ~ 0.004.
    """
    rng = np.random.default_rng(1)
    n, p, draws = 20_000, 0.032, 3_000
    covered = 0
    for _ in range(draws):
        a, b = rng.binomial(n, p, size=2)
        r = difference_in_proportions(int(a), n, int(b), n, **KW)
        lo, hi = r.confidence_interval
        covered += lo <= 0.0 <= hi
    assert abs(covered / draws - 0.95) < 3 * 0.004
