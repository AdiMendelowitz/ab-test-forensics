"""Independent tests of the rollout implementation against its design specification.

Every expected value is derived here from the specification with numpy, scipy and plain
arithmetic. Nothing is taken from the implementation except the function under test.
"""

from __future__ import annotations

import itertools
import math
from pathlib import Path

import numpy as np
import pytest
import yaml
from scipy import stats

from abforensics.inference.proportions import difference_in_proportions
from abforensics.simulation import assembly_rollout as ar

CONFIG = (
    Path(__file__).resolve().parents[2]
    / "case_studies"
    / "01_lost_logs_found_lift"
    / "rollout"
    / "config.yaml"
)

# Spec constants (sections 3 to 6)
DAYS, N_D = 28, 20_000
STAGES = ((1, 7, 0.01), (8, 14, 0.10), (15, 28, 0.50))
BASELINE, LEGACY, LIFT = 0.032, 0.15, 0.03
KAPPA = 0.003125
CRIT = stats.chi2.isf(0.001, 1)  # 10.828 in the spec


@pytest.fixture(scope="module")
def prm():
    with open(CONFIG) as fh:
        return ar.validate_rollout_config(yaml.safe_load(fh))


# ---- spec-side derivations ------------------------------------------------------------------
def spec_p():
    return np.array([p for a, b, p in STAGES for _ in range(a, b + 1)])


def spec_g(kappa):
    return 1 + kappa * (np.arange(1, DAYS + 1) - 14.5)


def spec_lift_by_day(stage_lift):
    return np.array(
        [lf for (a, b, _), lf in zip(STAGES, stage_lift, strict=True) for _ in range(a, b + 1)]
    )


def spec_means(kappa, stage_lift):
    p0 = BASELINE * spec_g(kappa)
    p1 = p0 * (1 + (1 - LEGACY) * spec_lift_by_day(stage_lift))
    return p0, p1


def spec_pooled(kappa, stage_lift):
    p0, p1 = spec_means(kappa, stage_lift)
    pd = spec_p()
    n1, n0 = N_D * pd, N_D * (1 - pd)
    r1 = np.sum(n1 * p1) / np.sum(n1)
    r0 = np.sum(n0 * p0) / np.sum(n0)
    return r1 - r0, r1 / r0 - 1


def spec_exact_power(n, p, e1, e0, alpha=0.001):
    crit = stats.chi2.isf(alpha, 1)
    q = p * e1 + (1 - p) * e0
    theta = p * e1 / q
    sd = math.sqrt(n * q * (1 - q))
    lo = max(1, int(n * q - 12 * sd))
    hi = min(n, int(math.ceil(n * q + 12 * sd)))
    total = 0.0
    for m in range(lo, hi + 1):
        # flag region: |t - p m| >= sqrt(crit m p (1-p)); sum the binomial tails directly
        half = math.sqrt(crit * m * p * (1 - p))
        t_hi = math.ceil(p * m + half)  # t >= t_hi flags
        t_lo = math.floor(p * m - half)  # t <= t_lo flags
        tail = stats.binom.sf(t_hi - 1, m, theta) + (
            stats.binom.cdf(t_lo, m, theta) if t_lo >= 0 else 0.0
        )
        total += stats.binom.pmf(m, n, q) * tail
    return total


# ---- 1. routing schedule (C1) ---------------------------------------------------------------
def test_routing_schedule(prm):
    assert np.allclose(ar.routing_probability(prm), spec_p(), atol=0, rtol=0)
    assert len(ar.routing_probability(prm)) == DAYS


def test_stage_day_ranges_and_products(prm):
    assert tuple(tuple(b) for b in prm.stage_bounds) == ((1, 7), (8, 14), (15, 28))
    assert tuple(prm.stage_p) == (0.01, 0.10, 0.50)
    stage = ar.stage_of_day(prm)
    products = [int(np.sum(stage == s)) * N_D for s in range(3)]
    assert products == [140_000, 140_000, 280_000]
    for s, (a, b, _) in enumerate(STAGES):
        assert np.all(stage[a - 1 : b] == s)
    assert prm.per_day == N_D and prm.days == DAYS


# ---- 2. drift and baselines -----------------------------------------------------------------
def test_trend_factor_and_baselines(prm):
    g = ar.trend_factor(prm)
    assert np.allclose(g, spec_g(KAPPA), rtol=1e-12, atol=0)
    p0, _ = ar.expected_rates(prm)
    assert np.allclose(p0, BASELINE * spec_g(KAPPA), rtol=1e-12)
    assert p0[0] == pytest.approx(0.0306, abs=6e-5)
    assert p0[-1] == pytest.approx(0.0334, abs=6e-5)


# ---- 3. truths ------------------------------------------------------------------------------
def test_truth_s1(prm):
    t = ar.truths(prm)
    assert t["E_ramp_rel"] == pytest.approx(0.85 * 0.03, abs=1e-12)
    assert t["E_final_rel"] == pytest.approx(0.0255, abs=1e-12)
    p0, p1 = spec_means(KAPPA, (LIFT,) * 3)
    assert np.allclose(p1 / p0 - 1, 0.0255, atol=1e-12)
    assert t["E_ramp_abs"] == pytest.approx(np.mean(p1 - p0), rel=1e-10)
    assert t["E_final_abs"] == pytest.approx(np.mean((p1 - p0)[14:]), rel=1e-10)


def test_truth_s2(prm):
    lifts = (0.0, 0.015, 0.03)
    p0, p1 = spec_means(KAPPA, lifts)
    e_ramp_rel = np.sum(p1 - p0) / np.sum(p0)
    e_final_rel = np.sum((p1 - p0)[14:]) / np.sum(p0[14:])
    t = ar.truths(prm, stage_lift=lifts)
    assert t["E_ramp_rel"] == pytest.approx(e_ramp_rel, rel=1e-10)
    assert t["E_final_rel"] == pytest.approx(e_final_rel, rel=1e-10)
    assert t["E_ramp_abs"] == pytest.approx(np.mean(p1 - p0), rel=1e-10)
    assert t["E_final_abs"] == pytest.approx(np.mean((p1 - p0)[14:]), rel=1e-10)
    assert e_final_rel == pytest.approx(0.0255, abs=1e-12)
    assert t["E_ramp_rel"] < t["E_final_rel"]


# ---- 4. pooled expectation under no effect --------------------------------------------------
@pytest.mark.parametrize(
    ("kappa", "approx_rel"), [(0.0015625, 0.0128), (0.003125, 0.0257), (0.00625, 0.0517)]
)
def test_pooled_expectation_null(prm, kappa, approx_rel):
    zero = ar.with_changes(prm, lift=0.0, kappa=kappa)
    exp_abs, exp_rel = spec_pooled(kappa, (0.0, 0.0, 0.0))
    got = ar.pooled_expectation(zero)
    assert got["abs"] == pytest.approx(exp_abs, rel=1e-9)
    assert got["rel"] == pytest.approx(exp_rel, rel=1e-9)
    assert exp_rel == pytest.approx(approx_rel, abs=6e-4)  # sanity against the spec's table


def test_pooled_expectation_kappa_zero_is_zero(prm):
    got = ar.pooled_expectation(ar.with_changes(prm, lift=0.0, kappa=0.0))
    assert abs(got["abs"]) < 1e-12 and abs(got["rel"]) < 1e-12


def test_pooled_expectation_s2(prm):
    lifts = (0.0, 0.015, 0.03)
    got = ar.pooled_expectation(prm, stage_lift=lifts)
    exp_abs, exp_rel = spec_pooled(KAPPA, lifts)
    assert got["abs"] == pytest.approx(exp_abs, rel=1e-9)
    assert got["rel"] == pytest.approx(exp_rel, rel=1e-9)


def test_mean_arrival_days_in_spec(prm):
    d, pd = np.arange(1, DAYS + 1), spec_p()
    assert np.sum(d * pd) / np.sum(pd) == pytest.approx(20.4, abs=0.05)
    assert np.sum(d * (1 - pd)) / np.sum(1 - pd) == pytest.approx(12.2, abs=0.05)


# ---- 5. variance shares ---------------------------------------------------------------------
def test_variance_shares(prm):
    pd = spec_p()
    r = BASELINE  # "at the default baseline": a constant rate, so the shares depend on p_d only
    w = 1 / DAYS
    var_d = w**2 * (r * (1 - r) / (N_D * pd) + r * (1 - r) / (N_D * (1 - pd)))
    mine = np.array([var_d[a - 1 : b].sum() for a, b, _ in STAGES]) / var_d.sum()
    got = ar.day_standardised_variance_shares(prm)
    assert got.sum() == pytest.approx(1.0, abs=1e-12)
    assert np.allclose(got, mine, atol=2e-3)
    assert np.allclose(got, [0.841, 0.092, 0.067], atol=1.5e-3)


# ---- 6. exact canary power ------------------------------------------------------------------
E1 = 0.85 * (1 - 0.40 * (0.04 + 0.08 + 0.13) * 0.35)
E0 = 0.85


def test_affected_share_and_logged_probabilities(prm):
    share = 0.40 * (0.04 + 0.08 + 0.13)
    assert share == pytest.approx(0.10)
    assert ar.affected_share("A", "4") == pytest.approx(share, abs=1e-12)
    assert prm.affected_share == pytest.approx(share, abs=1e-12)
    assert E1 == pytest.approx(0.82025, abs=1e-12)
    e1, e0 = ar.logged_scan_probabilities(prm, defect_on=True)
    assert e1 == pytest.approx(E1, abs=1e-12) and e0 == pytest.approx(E0, abs=1e-12)
    e1_off, e0_off = ar.logged_scan_probabilities(prm, defect_on=False)
    assert e1_off == pytest.approx(0.85) and e0_off == pytest.approx(0.85)


def brute_force_power(n, p, e1, e0, alpha=0.001):
    crit = stats.chi2.isf(alpha, 1)
    pt, pc = p * e1, (1 - p) * e0
    pn = 1 - pt - pc
    total = 0.0
    for t, c in itertools.product(range(n + 1), repeat=2):
        if t + c > n:
            continue
        m = t + c
        if m == 0:
            continue
        z2 = (t - p * m) ** 2 / (m * p * (1 - p))
        if z2 >= crit:
            total += stats.multinomial.pmf([t, c, n - m], n, [pt, pc, pn])
    return total


def test_canary_exact_matches_brute_force_n40():
    mine = brute_force_power(40, 0.10, E1, E0)
    got = ar.exact_canary_power(40, 0.10, E1, E0)
    assert got["power"] == pytest.approx(mine, abs=1e-12, rel=0)
    assert 0 <= got["omitted_mass"] < 1e-12


@pytest.mark.parametrize(
    ("n", "p", "expected"),
    [(140_000, 0.01, 0.01605), (140_000, 0.10, 0.6346)],
)
def test_canary_power_stage_values(n, p, expected):
    got = ar.exact_canary_power(n, p, E1, E0)["power"]
    assert got == pytest.approx(expected, abs=6e-5)
    assert got == pytest.approx(spec_exact_power(n, p, E1, E0), abs=1e-9)


def test_canary_power_stage3_above_threshold():
    got = ar.exact_canary_power(280_000, 0.50, E1, E0)
    assert got["power"] > 0.9999
    assert got["power"] == pytest.approx(spec_exact_power(280_000, 0.50, E1, E0), abs=1e-9)


def test_canary_falsifier_not_triggered():
    assert ar.exact_canary_power(140_000, 0.10, E1, E0)["power"] < 0.80


def test_canary_statistic_and_zero_rule():
    t, m = np.array([3, 0, 5]), np.array([10, 0, 5])
    z2 = ar.canary_statistic(t, m, 0.1)
    assert z2[1] == 0
    assert z2[0] == pytest.approx((3 - 1.0) ** 2 / (10 * 0.1 * 0.9))
    assert z2[2] == pytest.approx((5 - 0.5) ** 2 / (5 * 0.1 * 0.9))


# ---- 7. test (a) ----------------------------------------------------------------------------
def _counts(n1_row, y1, y0, n_total=N_D):
    n1 = np.tile(np.asarray(n1_row), (y1.shape[0], 1))
    return ar.DayArmCounts(n1=n1, y1=y1, n0=n_total - n1, y0=y0)


def _draw_y(rng, n1, n0, reps=4):
    y1 = rng.binomial(n1, 0.033, size=(reps, len(n1)))
    y0 = rng.binomial(n0, 0.032, size=(reps, len(n0)))
    return y1, y0


def test_a_constant_fraction_equals_pooled(prm):
    rng = np.random.default_rng(11)
    n1 = np.full(DAYS, 6000)
    y1, y0 = _draw_y(rng, n1, N_D - n1)
    c = _counts(n1, y1, y0)
    pooled = ar.estimate_pooled(c)["abs"]
    dstd = ar.estimate_day_standardised(c, prm)["abs"]
    mine = y1.sum(1) / n1.sum() - y0.sum(1) / (N_D - n1).sum()
    assert np.allclose(pooled, mine, atol=1e-12, rtol=0)
    assert np.allclose(dstd, pooled, atol=1e-12, rtol=0)


def test_a_varying_fraction_differs(prm):
    rng = np.random.default_rng(12)
    n1 = np.repeat([200, 2000, 10000], [7, 7, 14])
    y1, y0 = _draw_y(rng, n1, N_D - n1)
    c = _counts(n1, y1, y0)
    pooled = ar.estimate_pooled(c)["abs"]
    dstd = ar.estimate_day_standardised(c, prm)["abs"]
    mine_d = np.mean(y1 / n1 - y0 / (N_D - n1), axis=1)
    assert np.allclose(dstd, mine_d, atol=1e-12, rtol=0)
    assert np.max(np.abs(pooled - dstd)) > 1e-6


# ---- 8. test (b) ----------------------------------------------------------------------------
def test_b_one_stage_5050_equals_case01_itt(prm):
    one = ar.with_changes(prm, stage_bounds=((1, DAYS),), stage_p=(0.5,), final_stage=0, kappa=0.0)
    rng = np.random.default_rng(5)
    n1 = np.full(DAYS, N_D // 2)
    y1 = rng.binomial(n1, 0.0345, size=(3, DAYS))
    y0 = rng.binomial(n1, 0.0320, size=(3, DAYS))
    c = _counts(n1, y1, y0)
    est = ar.estimate_all(c, one)
    for i in range(3):
        ref = difference_in_proportions(
            int(y1[i].sum()),
            int(n1.sum()),
            int(y0[i].sum()),
            int(n1.sum()),
            estimand="ITT",
            analysis_unit="product",
            population="assigned",
        )
        for name in ("final_stage", "pooled", "day_standardised"):
            assert est[name]["abs"][i] == pytest.approx(ref.absolute_difference, abs=1e-12)
            assert est[name]["rel"][i] == pytest.approx(ref.relative_lift, abs=1e-10)


# ---- 9. test (e) ----------------------------------------------------------------------------
@pytest.mark.parametrize("kappa", [0.2, -0.2, 1.0])
def test_e_probability_assertion_raises(prm, kappa):
    with pytest.raises(ValueError):
        ar.with_changes(prm, kappa=kappa)


def test_e_sweep_values_do_not_raise(prm):
    for kappa in (0.0, 0.0015625, 0.003125, 0.00625):
        ar.with_changes(prm, kappa=kappa)


def test_e_lift_pushing_probability_above_one_raises(prm):
    with pytest.raises(ValueError):
        ar.with_changes(prm, lift=40.0)


# ---- 10. reading rule -----------------------------------------------------------------------
@pytest.mark.slow
def test_reading_rule_s0(prm):
    rows, canary, _ = ar.run_operating_characteristics(
        prm,
        replicates=2000,
        canary_replicates=200,
        seed=20261102,
        kappa_sweep=[0.0, 0.0015625, 0.003125, 0.00625],
        s2_stage_lifts=[0.0, 0.015, 0.03],
    )
    s0 = [r for r in rows if r["scenario"] == "S0" and r["kappa"] == pytest.approx(KAPPA)]
    by = {r["estimator"]: r for r in s0}
    assert set(by) == {"pooled", "day_standardised", "final_stage"}
    for name in ("day_standardised", "final_stage"):
        r = by[name]
        assert abs(r["reject_zero"] - 0.05) <= 4 * r["mcse_reject_zero"], (name, r["reject_zero"])
    pooled = by["pooled"]
    ref_abs, _ = spec_pooled(KAPPA, (0.0, 0.0, 0.0))
    assert abs(pooled["mean_abs"] - ref_abs) <= 4 * pooled["mcse_bias_abs"]
    # the three exact canary powers carried in the table equal the independent ones
    for row in canary:
        mine = spec_exact_power(row["products"], row["routing_p"], E1, E0)
        assert row["exact_power"] == pytest.approx(mine, abs=1e-9)
