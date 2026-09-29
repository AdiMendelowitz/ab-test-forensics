"""Unit tests for srm_check.

Known-answer tests use the chi-square goodness-of-fit statistic computed by hand:
chi2 = sum((observed - expected)^2 / expected).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from abforensics import SRMResult, srm_check

FIFTY_FIFTY = {"control": 0.5, "treatment": 0.5}


def test_exact_split_is_compatible() -> None:
    result = srm_check({"control": 50_000, "treatment": 50_000}, FIFTY_FIFTY, level="assignment")
    assert isinstance(result, SRMResult)
    assert result.chi2_stat == pytest.approx(0.0)
    assert result.p_value == pytest.approx(1.0)
    assert result.flag_srm is False
    assert result.degrees_of_freedom == 1
    assert result.level == "assignment"


def test_known_answer_two_arms() -> None:
    # observed 50123 / 48911, total 99034, expected 49517 each
    result = srm_check({"control": 50_123, "treatment": 48_911}, FIFTY_FIFTY, level="exposure")
    expected = 99_034 / 2
    chi2 = (50_123 - expected) ** 2 / expected + (48_911 - expected) ** 2 / expected
    assert result.chi2_stat == pytest.approx(chi2)
    assert result.p_value == pytest.approx(stats.chi2.sf(chi2, df=1))
    assert result.expected_counts == {"control": expected, "treatment": expected}
    # chi2 ~ 14.8, p ~ 1.2e-4: flagged at the default alpha of 0.001
    assert result.flag_srm is True


def test_obvious_mismatch_is_flagged() -> None:
    result = srm_check({"control": 60_000, "treatment": 40_000}, FIFTY_FIFTY, level="assignment")
    assert result.flag_srm is True
    assert result.p_value < 1e-100


def test_multi_arm_unequal_allocation() -> None:
    alloc = {"control": 0.5, "t1": 0.25, "t2": 0.25}
    counts = {"control": 10_000, "t1": 5_000, "t2": 5_000}
    result = srm_check(counts, alloc, level="assignment")
    assert result.degrees_of_freedom == 2
    assert result.chi2_stat == pytest.approx(0.0)
    assert result.flag_srm is False
    assert result.expected_counts == {"control": 10_000.0, "t1": 5_000.0, "t2": 5_000.0}


def test_alpha_controls_flag() -> None:
    counts = {"control": 50_400, "treatment": 49_600}  # chi2 = 6.4, p ~ 0.011
    strict = srm_check(counts, FIFTY_FIFTY, level="assignment", alpha=0.001)
    loose = srm_check(counts, FIFTY_FIFTY, level="assignment", alpha=0.05)
    assert strict.flag_srm is False
    assert loose.flag_srm is True
    assert strict.chi2_stat == pytest.approx(6.4)


@pytest.mark.parametrize(
    ("counts", "alloc", "match"),
    [
        ({"control": -1, "treatment": 10}, FIFTY_FIFTY, "non-negative"),
        ({"control": 10, "treatment": 10}, {"control": 0.6, "treatment": 0.6}, "sum to 1"),
        ({"control": 10, "treatment": 10}, {"control": 1.0, "treatment": 0.0}, "positive"),
        ({"control": 10}, FIFTY_FIFTY, "differ"),
        ({"control": 10, "treatment": 10, "extra": 1}, FIFTY_FIFTY, "differ"),
        ({"control": 0, "treatment": 0}, FIFTY_FIFTY, "zero"),
        ({"control": 10.5, "treatment": 10}, FIFTY_FIFTY, "integers"),
        ({}, {}, "empty"),
    ],
)
def test_invalid_inputs_raise(counts, alloc, match) -> None:
    with pytest.raises(ValueError, match=match):
        srm_check(counts, alloc, level="assignment")


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5])
def test_invalid_alpha_raises(alpha) -> None:
    with pytest.raises(ValueError, match="alpha"):
        srm_check({"control": 10, "treatment": 10}, FIFTY_FIFTY, level="assignment", alpha=alpha)


def test_small_expected_count_warns() -> None:
    result = srm_check({"control": 4, "treatment": 3}, FIFTY_FIFTY, level="assignment")
    assert any("below 5" in w for w in result.warnings)


def test_result_is_frozen_and_carries_no_decision() -> None:
    result = srm_check({"control": 10, "treatment": 10}, FIFTY_FIFTY, level="assignment")
    with pytest.raises(AttributeError):
        result.flag_srm = True  # type: ignore[misc]
    assert not any(hasattr(result, name) for name in ("ship", "decision", "recommendation"))
    assert "diagnostic" in result.estimand


def test_summary_mentions_level_and_verdict() -> None:
    result = srm_check({"control": 60_000, "treatment": 40_000}, FIFTY_FIFTY, level="exposure")
    text = result.summary()
    assert "exposure" in text and "MISMATCH" in text


def test_null_rejection_rate_is_nominal() -> None:
    """Under a true 50/50 allocation the flag rate should sit near alpha.

    2,000 draws at alpha = 0.05: Monte Carlo SE of the rejection rate is
    sqrt(0.05 * 0.95 / 2000) ~ 0.0049, so a rate inside [0.035, 0.065] is compatible.
    """
    rng = np.random.default_rng(20260928)
    n, draws, alpha = 20_000, 2_000, 0.05
    flags = 0
    for _ in range(draws):
        t = int(rng.binomial(n, 0.5))
        counts = {"control": n - t, "treatment": t}
        flags += srm_check(counts, FIFTY_FIFTY, level="a", alpha=alpha).flag_srm
    rate = flags / draws
    assert math.isclose(rate, alpha, abs_tol=3 * 0.0049)
