"""Difference in two arm-level proportions.

The estimator is the difference in arm-level user proportions. The interval is the
unpooled Wald interval on the absolute difference; the p-value is the two-sided pooled
z-test of equality. The relative lift gets its own interval from the delta method on the
log ratio, because "lift" is what product decisions are phrased in and the Wald interval
on the absolute difference does not translate to it directly.

Reference: Kohavi, Tang, Xu (2020), chapter 17, for the two-sample test on proportions
and the practical-significance framing; Newcombe (1998) for why the Wald interval is
acceptable at large n and rare outcomes but should not be used for small counts.
"""

from __future__ import annotations

import math

from scipy import stats

from abforensics.results import ProportionDifferenceResult

_MIN_EXPECTED = 10.0


def difference_in_proportions(
    successes_treatment: int,
    n_treatment: int,
    successes_control: int,
    n_control: int,
    *,
    estimand: str,
    analysis_unit: str,
    population: str,
    alpha: float = 0.05,
) -> ProportionDifferenceResult:
    """Estimate treatment minus control success rate with a (1 - alpha) Wald interval.

    Parameters
    ----------
    successes_treatment, n_treatment, successes_control, n_control
        Counts of units with the outcome and total units, per arm, at the analysis unit.
    estimand
        The causal quantity the caller intends this estimate to target, in words. It is
        recorded on the result, not used in the computation, so that the estimate can never
        be reported without its estimand.
    analysis_unit
        The unit each count refers to (``"user"``, ``"session"``). Recorded, not inferred.
    population
        Which population the counts come from (``"assigned"``, ``"exposed"``). Recorded so
        that two estimates from different populations cannot be confused.
    alpha
        Two-sided significance level for the interval and the flag.

    Raises
    ------
    ValueError
        On impossible counts (negative, successes above totals), an empty arm, or an alpha
        outside (0, 1).
    """
    for name, value in (
        ("successes_treatment", successes_treatment),
        ("n_treatment", n_treatment),
        ("successes_control", successes_control),
        ("n_control", n_control),
    ):
        if value < 0 or value != int(value):
            raise ValueError(f"{name} must be a non-negative integer; got {value}")
    if n_treatment == 0 or n_control == 0:
        raise ValueError("both arms must contain at least one unit")
    if successes_treatment > n_treatment or successes_control > n_control:
        raise ValueError("successes cannot exceed the number of units in an arm")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1); got {alpha}")

    p_t = successes_treatment / n_treatment
    p_c = successes_control / n_control
    diff = p_t - p_c
    se_unpooled = math.sqrt(p_t * (1 - p_t) / n_treatment + p_c * (1 - p_c) / n_control)
    z = stats.norm.ppf(1 - alpha / 2)
    ci = (diff - z * se_unpooled, diff + z * se_unpooled)

    p_pooled = (successes_treatment + successes_control) / (n_treatment + n_control)
    se_pooled = math.sqrt(p_pooled * (1 - p_pooled) * (1 / n_treatment + 1 / n_control))
    p_value = 2 * stats.norm.sf(abs(diff) / se_pooled) if se_pooled > 0 else 1.0

    warnings: list[str] = []
    expected_min = min(
        successes_treatment,
        n_treatment - successes_treatment,
        successes_control,
        n_control - successes_control,
    )
    if expected_min < _MIN_EXPECTED:
        warnings.append(
            f"a cell has fewer than {_MIN_EXPECTED:.0f} units; the Wald interval is "
            "unreliable here, use an exact or score interval."
        )

    if p_t > 0 and p_c > 0:
        rel = p_t / p_c - 1
        se_log_ratio = math.sqrt((1 - p_t) / (n_treatment * p_t) + (1 - p_c) / (n_control * p_c))
        log_ratio = math.log(p_t / p_c)
        rel_ci = (
            math.exp(log_ratio - z * se_log_ratio) - 1,
            math.exp(log_ratio + z * se_log_ratio) - 1,
        )
    else:
        rel = math.nan
        rel_ci = (math.nan, math.nan)
        warnings.append("a rate is zero; relative lift is undefined.")

    return ProportionDifferenceResult(
        estimand=estimand,
        analysis_unit=analysis_unit,
        population=population,
        rate_treatment=p_t,
        rate_control=p_c,
        n_treatment=int(n_treatment),
        n_control=int(n_control),
        absolute_difference=diff,
        standard_error=se_unpooled,
        confidence_interval=ci,
        relative_lift=rel,
        relative_confidence_interval=rel_ci,
        p_value=float(p_value),
        alpha=alpha,
        warnings=tuple(warnings),
    )
