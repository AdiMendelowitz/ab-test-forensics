"""Coverage of the relative-lift interval at Case 01's own regime.

The decision rule reads the lower bound of ``relative_confidence_interval`` from
``difference_in_proportions`` (delta method on the log ratio). This test draws arm-level
binomial counts at p_control = 0.032, a true relative lift of 3% and 140,000 users per arm,
and checks that the 95% interval covers the true lift within four Monte Carlo standard
errors of nominal. At B = 20,000 the MC SE of a 95% coverage rate is
sqrt(0.95 x 0.05 / 20,000) = 0.0015, so the tolerance is +/- 0.0062.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from abforensics.inference.proportions import difference_in_proportions

P_CONTROL = 0.032
LIFT = 0.03
N_PER_ARM = 140_000
B = 20_000


def relative_interval_coverage(
    p_control: float, lift: float, n_per_arm: int, replicates: int, seed: int
) -> tuple[float, float]:
    """Monte Carlo coverage of the relative interval and its standard error."""
    rng = np.random.default_rng(seed)
    p_treatment = p_control * (1 + lift)
    s_t = rng.binomial(n_per_arm, p_treatment, size=replicates)
    s_c = rng.binomial(n_per_arm, p_control, size=replicates)
    covered = 0
    for st, sc in zip(s_t, s_c, strict=True):
        res = difference_in_proportions(
            int(st),
            n_per_arm,
            int(sc),
            n_per_arm,
            estimand="relative lift",
            analysis_unit="user",
            population="assigned",
        )
        lo, hi = res.relative_confidence_interval
        covered += lo <= lift <= hi
    rate = covered / replicates
    return rate, math.sqrt(rate * (1 - rate) / replicates)


@pytest.mark.slow
def test_relative_interval_covers_at_the_case_regime() -> None:
    rate, se = relative_interval_coverage(P_CONTROL, LIFT, N_PER_ARM, B, seed=20260929)
    nominal_se = math.sqrt(0.95 * 0.05 / B)
    assert abs(rate - 0.95) <= 4 * nominal_se, f"coverage {rate:.4f} (MC SE {se:.4f}, B={B})"


def test_relative_interval_coverage_helper_runs() -> None:
    rate, se = relative_interval_coverage(P_CONTROL, LIFT, N_PER_ARM, 50, seed=1)
    assert 0.0 <= rate <= 1.0 and se >= 0.0
