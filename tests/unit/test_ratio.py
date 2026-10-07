"""Unit tests for ratio_of_sums and naive_event_rate (spec case-02.md, sections 11 and 14)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from abforensics.inference import difference_in_proportions
from abforensics.metrics import naive_event_rate, ratio_of_sums
from abforensics.metrics.ratio import NAIVE_WARNING

EST = "impression-weighted click-through rate, ratio of expected totals"


def _arms(n_per_arm: int, seed: int, lift: float = 0.0, rows_per_user: int = 2) -> pd.DataFrame:
    """Impression-grain frame: heterogeneous propensities, heavy-tailed volume, two arms."""
    rng = np.random.default_rng(seed)
    n = 2 * n_per_arm
    arm = np.repeat(["treatment", "control"], n_per_arm)
    inviews = rng.negative_binomial(2, 0.05, size=(n, rows_per_user)) + 1
    p = 1 / (1 + np.exp(-rng.normal(-2.3, 0.6, size=n)))
    p = np.where(arm == "treatment", np.minimum(p * (1 + lift), 1.0), p)
    clicks = rng.binomial(inviews, p[:, None])
    return pd.DataFrame(
        {
            "user_id": np.repeat(np.arange(n), rows_per_user),
            "arm": np.repeat(arm, rows_per_user),
            "inviews": inviews.ravel(),
            "clicks": clicks.ravel(),
        }
    )


def _ros(frame: pd.DataFrame, **kw):
    return ratio_of_sums(frame, "clicks", "inviews", "user_id", "arm", estimand=EST, **kw)


def test_point_estimate_is_ratio_of_arm_totals_at_any_grain() -> None:
    f = _arms(300, seed=1)
    r = _ros(f)
    tot = f.groupby("arm")[["clicks", "inviews"]].sum()
    expected = tot["clicks"] / tot["inviews"]
    assert r.ratio_treatment == pytest.approx(expected["treatment"])
    assert r.ratio_control == pytest.approx(expected["control"])
    users = f.groupby(["user_id", "arm"], as_index=False)[["clicks", "inviews"]].sum()
    r_user = _ros(users)
    assert r_user.standard_error == pytest.approx(r.standard_error, rel=1e-12)
    assert r.relative_lift == pytest.approx(r.ratio_treatment / r.ratio_control - 1)


def test_delta_variance_equals_jackknife_to_first_order() -> None:
    f = _arms(20_000, seed=2)
    d, j = _ros(f, method="delta"), _ros(f, method="jackknife")
    assert j.se_ratio_treatment == pytest.approx(d.se_ratio_treatment, rel=0.005)
    assert j.se_ratio_control == pytest.approx(d.se_ratio_control, rel=0.005)
    assert j.standard_error == pytest.approx(d.standard_error, rel=0.005)
    for a, b in zip(j.relative_confidence_interval, d.relative_confidence_interval):
        assert a == pytest.approx(b, abs=1e-4)


def test_bootstrap_agrees_with_delta_and_is_reproducible() -> None:
    f = _arms(3_000, seed=3, lift=0.05)
    d = _ros(f)
    b1 = _ros(f, method="bootstrap", resamples=400, seed=11, interval="normal")
    b2 = _ros(f, method="bootstrap", resamples=400, seed=11, interval="normal")
    assert b1 == b2
    assert (b1.resamples, b1.seed, b1.method) == (400, 11, "bootstrap")
    assert b1.standard_error == pytest.approx(d.standard_error, rel=0.15)
    rel_width_b = b1.relative_confidence_interval[1] - b1.relative_confidence_interval[0]
    rel_width_d = d.relative_confidence_interval[1] - d.relative_confidence_interval[0]
    assert rel_width_b == pytest.approx(rel_width_d, rel=0.15)
    assert b1.relative_lift == d.relative_lift


def test_bootstrap_requires_seed_and_percentile_below_1000_warns() -> None:
    f = _arms(200, seed=4)
    with pytest.raises(ValueError, match="seed"):
        _ros(f, method="bootstrap", resamples=200)
    r = _ros(f, method="bootstrap", resamples=200, seed=5)
    assert any("1,000" in w for w in r.warnings)


def test_delta_interval_is_wider_than_naive_under_heterogeneity() -> None:
    f = _arms(5_000, seed=6)
    d = _ros(f)
    tot = f.groupby("arm")[["clicks", "inviews"]].sum()
    n = naive_event_rate(
        int(tot.loc["treatment", "clicks"]),
        int(tot.loc["treatment", "inviews"]),
        int(tot.loc["control", "clicks"]),
        int(tot.loc["control", "inviews"]),
    )
    assert n.absolute_difference == pytest.approx(d.absolute_difference)
    assert d.standard_error > 1.3 * n.standard_error


def test_naive_event_rate_carries_its_warning_and_matches_proportions() -> None:
    n = naive_event_rate(900, 10_000, 850, 10_000)
    ref = difference_in_proportions(
        900,
        10_000,
        850,
        10_000,
        estimand="x",
        analysis_unit="impression",
        population="all impressions",
    )
    assert n.warnings[0] == NAIVE_WARNING
    assert n.analysis_unit == "impression"
    assert n.confidence_interval == ref.confidence_interval
    assert n.p_value == ref.p_value
    assert "impressions are independent (violated when they cluster within users)" in n.assumptions


def test_input_errors() -> None:
    f = _arms(50, seed=7)
    with pytest.raises(ValueError, match="method"):
        _ros(f, method="bayes")
    bad = f.copy()
    bad.loc[0, "arm"] = "control"  # user 0 now has rows in both arms
    with pytest.raises(ValueError, match="more than one arm"):
        _ros(bad)
    with pytest.raises(ValueError, match="at least two units"):
        _ros(f[(f["arm"] == "control") | (f["user_id"] == 0)])
    with pytest.raises(ValueError, match="not in frame"):
        ratio_of_sums(f, "clicks", "views", "user_id", "arm", estimand=EST)


import dataclasses
import math
from fractions import Fraction

from abforensics.metrics.ratio import _delta_se, _jackknife_var, ratio_of_sums_arrays


def _unit_arrays(seed, n):
    rng = np.random.default_rng(seed)
    arrays = []
    for _ in range(2):
        m = rng.integers(1, 100, n)
        c = rng.integers(0, m + 1)
        arrays.extend((c.astype(np.float64), m.astype(np.float64)))
    return tuple(arrays)


def test_arrays_kernel_matches_frame_function():
    c_t, m_t, c_c, m_c = _unit_arrays(1, 80)
    parts = []
    for a, (arm, c, m) in enumerate((("treatment", c_t, m_t), ("control", c_c, m_c))):
        parts.append(
            pd.DataFrame(
                {
                    "user_id": np.repeat(a * 80 + np.arange(80), 2),
                    "arm": arm,
                    "clicks": np.column_stack((c - c // 2, c // 2)).ravel(),
                    "inviews": np.column_stack((m - m // 2, m // 2)).ravel(),
                }
            )
        )
    frame = pd.concat(parts, ignore_index=True)
    for method in ("delta", "jackknife", "bootstrap"):
        options = dict(resamples=200, seed=3, interval="normal") if method == "bootstrap" else {}
        assert ratio_of_sums(
            frame, "clicks", "inviews", "user_id", "arm", estimand="E2", method=method, **options
        ) == ratio_of_sums_arrays(
            c_t,
            m_t,
            c_c,
            m_c,
            estimand="E2",
            numerator="clicks",
            denominator="inviews",
            unit_col="user_id",
            method=method,
            **options,
        )


def test_delta_se_matches_exact_rational():
    c, m, _, _ = _unit_arrays(2, 50)
    n = len(c)
    r = Fraction(sum(map(int, c)), sum(map(int, m)))
    mbar = Fraction(sum(map(int, m)), n)
    var = sum((Fraction(int(ci)) - r * int(mi)) ** 2 for ci, mi in zip(c, m)) / (
        (n - 1) * n * mbar**2
    )
    assert math.isclose(_delta_se(c, m) ** 2, float(var), rel_tol=1e-12)


def test_jackknife_matches_brute_force_delete_one():
    c, m, _, _ = _unit_arrays(3, 40)
    n = len(c)
    ratios = [math.fsum(np.delete(c, i)) / math.fsum(np.delete(m, i)) for i in range(n)]
    for log in (False, True):
        thetas = [math.log(t) for t in ratios] if log else ratios
        mean = math.fsum(thetas) / n
        var = (n - 1) / n * math.fsum((t - mean) ** 2 for t in thetas)
        assert math.isclose(_jackknife_var(c, m, log), var, rel_tol=1e-12)


def test_constant_ratio_has_zero_delta_se():
    m = np.arange(1, 31, dtype=float)
    c = 2 * m
    assert _delta_se(c, m) == 0.0


def test_kernel_is_invariant_to_unit_order():
    arrays = _unit_arrays(4, 60)
    rng = np.random.default_rng(9)
    pt, pc = rng.permutation(60), rng.permutation(60)
    shuffled = (arrays[0][pt], arrays[1][pt], arrays[2][pc], arrays[3][pc])
    for method in ("delta", "jackknife"):
        a = dataclasses.asdict(ratio_of_sums_arrays(*arrays, estimand="E2", method=method))
        b = dataclasses.asdict(ratio_of_sums_arrays(*shuffled, estimand="E2", method=method))
        for name, value in a.items():
            if isinstance(value, float):
                assert math.isclose(value, b[name], rel_tol=1e-12, abs_tol=0)
            elif isinstance(value, tuple) and value and isinstance(value[0], float):
                assert all(
                    math.isclose(x, y, rel_tol=1e-12, abs_tol=0) for x, y in zip(value, b[name])
                )
            else:
                assert value == b[name]


def test_zero_leave_one_out_denominator_raises():
    _, _, c_c, m_c = _unit_arrays(5, 10)
    with pytest.raises(ValueError):
        ratio_of_sums_arrays([1, 0, 0], [10, 0, 0], c_c, m_c, estimand="E2", method="jackknife")


def test_arrays_kernel_validates_inputs():
    c_t, m_t, c_c, m_c = _unit_arrays(6, 10)
    bad = c_t.copy()
    bad[0] = np.nan
    with pytest.raises(ValueError):
        ratio_of_sums_arrays(bad, m_t, c_c, m_c, estimand="E2")
    with pytest.raises(ValueError):
        ratio_of_sums_arrays(c_t[:-1], m_t, c_c, m_c, estimand="E2")
    with pytest.raises(ValueError):
        ratio_of_sums_arrays(c_t[:1], m_t[:1], c_c, m_c, estimand="E2")
    with pytest.raises(ValueError):
        ratio_of_sums_arrays(c_t, np.zeros_like(m_t), c_c, m_c, estimand="E2")
