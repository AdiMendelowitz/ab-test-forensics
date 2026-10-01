"""Difference in two arm means: Welch interval and a delta-method interval on the log ratio.

The Welch interval does not assume equal variances. The relative lift gets its own interval
from the delta method on log(mean_t / mean_c), with Var(log mean) ~ s^2 / (n mean^2), because
product decisions are phrased as lifts and the absolute interval does not translate to one.

References: Welch (1947) for the unequal-variance t interval; Kohavi, Tang and Xu (2020),
chapter 18, for the delta method on a relative lift; Deng, Knoblich and Lu (2018).
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

from abforensics.results import MeanDifferenceResult


def difference_in_means(
    y_t: np.ndarray,
    y_c: np.ndarray,
    alpha: float = 0.05,
    *,
    estimand: str,
    analysis_unit: str = "user",
) -> MeanDifferenceResult:
    """Treatment minus control mean with a (1 - alpha) Welch interval and a log-ratio lift.

    Parameters
    ----------
    y_t, y_c
        One outcome value per unit in each arm (e.g. clicks per eligible user, zeros included).
    alpha
        Two-sided level for both intervals.
    estimand
        The quantity targeted, recorded on the result.
    analysis_unit
        The unit each value belongs to; recorded, not inferred.
    """
    y_t = np.asarray(y_t, dtype=float).ravel()
    y_c = np.asarray(y_c, dtype=float).ravel()
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1); got {alpha}")
    if len(y_t) < 2 or len(y_c) < 2:
        raise ValueError("each arm needs at least two units")
    if not (np.all(np.isfinite(y_t)) and np.all(np.isfinite(y_c))):
        raise ValueError("outcomes must be finite; drop or impute missing values first")

    n_t, n_c = len(y_t), len(y_c)
    m_t, m_c = float(y_t.mean()), float(y_c.mean())
    v_t, v_c = float(y_t.var(ddof=1)), float(y_c.var(ddof=1))
    a, b = v_t / n_t, v_c / n_c
    se = math.sqrt(a + b)
    diff = m_t - m_c
    warnings: list[str] = []
    if se == 0:
        df = float(n_t + n_c - 2)
        p_value = 1.0 if diff == 0 else 0.0
        ci = (diff, diff)
        warnings.append("both arms have zero variance; the interval is degenerate.")
    else:
        df = (a + b) ** 2 / ((a * a / (n_t - 1) if a else 0.0) + (b * b / (n_c - 1) if b else 0.0))
        t = float(stats.t.ppf(1 - alpha / 2, df))
        ci = (diff - t * se, diff + t * se)
        p_value = float(2 * stats.t.sf(abs(diff) / se, df))

    if m_t > 0 and m_c > 0:
        z = float(stats.norm.ppf(1 - alpha / 2))
        log_ratio = math.log(m_t / m_c)
        se_log = math.sqrt(v_t / (n_t * m_t**2) + v_c / (n_c * m_c**2))
        rel = m_t / m_c - 1
        rel_ci = (math.exp(log_ratio - z * se_log) - 1, math.exp(log_ratio + z * se_log) - 1)
    else:
        rel, rel_ci = math.nan, (math.nan, math.nan)
        warnings.append("an arm mean is not positive; the log-ratio lift is undefined.")

    return MeanDifferenceResult(
        estimand=estimand,
        analysis_unit=analysis_unit,
        mean_treatment=m_t,
        mean_control=m_c,
        sd_treatment=math.sqrt(v_t),
        sd_control=math.sqrt(v_c),
        n_treatment=n_t,
        n_control=n_c,
        absolute_difference=diff,
        standard_error=se,
        degrees_of_freedom=float(df),
        confidence_interval=ci,
        relative_lift=rel,
        relative_confidence_interval=rel_ci,
        p_value=p_value,
        alpha=alpha,
        warnings=tuple(warnings),
    )
