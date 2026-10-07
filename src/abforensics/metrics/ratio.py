"""Ratio-of-sums metrics (clicks per impression) with unit-level uncertainty, and the naive
impression-level test kept so that Case 02 can show what it gets wrong.

A ratio of sums such as sum(clicks) / sum(in-views) per arm estimates the ratio of expected
totals, an impression-weighted rate. Its randomisation unit is the user, so the events inside
one user are dependent, and the variance must be computed at the user level. Three methods:

* ``delta`` (default): linearisation, Var(R) ~ [Var(C) - 2 R Cov(C, M) + R^2 Var(M)] / (n Mbar^2)
  per arm, from per-unit totals (Deng, Knoblich and Lu 2018). One pass.
* ``jackknife``: delete-one-unit jackknife from the closed-form leave-one-out totals
  (``inference.resampling.jackknife_variance``). One pass; equal to the delta method to first
  order.
* ``bootstrap``: cluster bootstrap of units (``inference.resampling.cluster_bootstrap``).
  The between-arm lift is a function of four totals, two per arm, so the frame is widened to
  arm-specific columns (the treatment columns are zero on control units and vice versa) and
  resampled within arm (``strata_col``), which keeps each arm's size fixed as randomisation
  did. Resampling the pooled units without strata would let arm sizes drift and mix the arms.
  Per-arm standard errors under this method come from the delta method; the bootstrap covers
  the between-arm contrasts.

``naive_event_rate`` treats every impression as an independent Bernoulli trial: the pooled
two-proportion test with n equal to the impression count. Under user-level heterogeneity its
interval is too narrow (spec case-02.md, section 5: 1.48 times on the base, Type I error 16.8%
at nominal 5%), and the result says so in its warnings.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence

import numpy as np
import pandas as pd
from scipy import stats

from abforensics.inference.proportions import difference_in_proportions
from abforensics.inference.resampling import cluster_bootstrap
from abforensics.results import ProportionDifferenceResult, RatioOfSumsResult

METHODS = ("delta", "jackknife", "bootstrap")

NAIVE_WARNING = (
    "the interval assumes impressions are independent Bernoulli trials; impressions are "
    "clustered within users, so under user-level heterogeneity it is too narrow and the "
    "test over-rejects. Use ratio_of_sums for inference; this result is the dashboard's view."
)


class _Contrast:
    """Between-arm contrast of two ratios of sums on arm-specific columns."""

    def __init__(self, cols: dict[str, str], kind: str) -> None:
        self.cols = cols
        self.kind = kind
        self.name = f"{kind} of sum({cols['num_t']})/sum({cols['den_t']}) vs control"

    def __call__(self, totals: pd.DataFrame) -> np.ndarray:
        c = self.cols
        r_t = totals[c["num_t"]].to_numpy(float) / totals[c["den_t"]].to_numpy(float)
        r_c = totals[c["num_c"]].to_numpy(float) / totals[c["den_c"]].to_numpy(float)
        return r_t / r_c - 1 if self.kind == "relative lift" else r_t - r_c


def _delta_se(c: np.ndarray, m: np.ndarray) -> float:
    n = len(c)
    r = c.sum() / m.sum()
    var = np.sum((c - r * m) ** 2) / ((n - 1) * n * m.mean() ** 2)
    return math.sqrt(max(float(var), 0.0))


def _jackknife_var(c: np.ndarray, m: np.ndarray, log: bool) -> float:
    n = len(c)
    loo_den = m.sum() - m
    if np.any(loo_den == 0):
        raise ValueError("denominator total 'denominator' is zero in some row")
    theta = (c.sum() - c) / loo_den
    if log:
        theta = np.log(theta)
    return float((n - 1) / n * np.sum((theta - theta.mean()) ** 2))


def ratio_of_sums_arrays(
    numerator_treatment,
    denominator_treatment,
    numerator_control,
    denominator_control,
    *,
    estimand: str,
    numerator: str = "numerator",
    denominator: str = "denominator",
    unit_col: str = "unit",
    alpha: float = 0.05,
    method: str = "delta",
    resamples: int = 1000,
    seed: int | None = None,
    interval: str = "percentile",
    warnings: Sequence[str] = (),
) -> RatioOfSumsResult:
    """Array kernel behind ``ratio_of_sums``: each element is one independent unit.

    A unit is a user, or a draw in the Case 02 harness. The harness calls this kernel directly with
    per-draw arrays; ``ratio_of_sums`` aggregates a frame to units and calls it.
    """
    c_t, m_t, c_c, m_c = (
        np.asarray(x, dtype=np.float64)
        for x in (
            numerator_treatment,
            denominator_treatment,
            numerator_control,
            denominator_control,
        )
    )
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}; got {method!r}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1); got {alpha}")
    for array in (c_t, m_t, c_c, m_c):
        if array.ndim != 1:
            raise ValueError("inputs must be 1-D")
    for array in (c_t, m_t, c_c, m_c):
        if not np.all(np.isfinite(array)):
            raise ValueError("inputs must be finite")
    for label, c, m in (("treatment", c_t, m_t), ("control", c_c, m_c)):
        if len(c) != len(m):
            raise ValueError(f"numerator and denominator lengths differ in arm {label}")
    for label, c in (("treatment", c_t), ("control", c_c)):
        if len(c) < 2:
            raise ValueError(f"arm {label!r} needs at least two units; found {len(c)}")
    for label, m in (("treatment", m_t), ("control", m_c)):
        if not m.sum() > 0:
            raise ValueError(f"denominator total is zero in arm {label!r}")
    warnings = list(warnings)
    treatment, control = "treatment", "control"
    r_t, r_c = c_t.sum() / m_t.sum(), c_c.sum() / m_c.sum()
    diff = r_t - r_c
    rel = r_t / r_c - 1 if r_c > 0 else math.nan
    z = float(stats.norm.ppf(1 - alpha / 2))

    if method in ("delta", "jackknife"):
        if method == "delta":
            se_t, se_c = _delta_se(c_t, m_t), _delta_se(c_c, m_c)
            var_log = (se_t / r_t) ** 2 + (se_c / r_c) ** 2 if r_t > 0 and r_c > 0 else math.nan
        else:
            se_t = math.sqrt(_jackknife_var(c_t, m_t, False))
            se_c = math.sqrt(_jackknife_var(c_c, m_c, False))
            var_log = (
                _jackknife_var(c_t, m_t, True) + _jackknife_var(c_c, m_c, True)
                if r_t > 0 and r_c > 0
                else math.nan
            )
        se = math.sqrt(se_t**2 + se_c**2)
        ci = (diff - z * se, diff + z * se)
        if math.isfinite(var_log):
            log_lift, se_log = math.log(r_t / r_c), math.sqrt(var_log)
            rel_ci = (math.exp(log_lift - z * se_log) - 1, math.exp(log_lift + z * se_log) - 1)
        else:
            rel_ci = (math.nan, math.nan)
            warnings.append("a ratio is zero; the relative lift interval is undefined.")
        boot_resamples, boot_seed = None, None
    else:
        if seed is None:
            raise ValueError("the bootstrap needs an explicit seed")
        if not r_c > 0:
            raise ValueError("the control ratio is zero; the relative lift is undefined")
        se_t, se_c = _delta_se(c_t, m_t), _delta_se(c_c, m_c)
        cols = {
            "num_t": f"{numerator}__t",
            "den_t": f"{denominator}__t",
            "num_c": f"{numerator}__c",
            "den_c": f"{denominator}__c",
        }
        wide = pd.concat(
            [
                pd.DataFrame(
                    {
                        unit_col: np.arange(len(c_t)),
                        "__arm": treatment,
                        cols["num_t"]: c_t,
                        cols["den_t"]: m_t,
                        cols["num_c"]: 0.0,
                        cols["den_c"]: 0.0,
                    }
                ),
                pd.DataFrame(
                    {
                        unit_col: np.arange(len(c_t), len(c_t) + len(c_c)),
                        "__arm": control,
                        cols["num_t"]: 0.0,
                        cols["den_t"]: 0.0,
                        cols["num_c"]: c_c,
                        cols["den_c"]: m_c,
                    }
                ),
            ],
            ignore_index=True,
        )
        common = dict(
            resamples=resamples,
            seed=seed,
            estimand=estimand,
            interval=interval,
            alpha=alpha,
            strata_col="__arm",
        )
        # Same seed and frame: both calls draw identical resamples, so the two distributions
        # are paired.
        b_rel = cluster_bootstrap(wide, unit_col, _Contrast(cols, "relative lift"), **common)
        b_abs = cluster_bootstrap(wide, unit_col, _Contrast(cols, "difference"), **common)
        se = b_abs.standard_error
        ci = (b_abs.lower, b_abs.upper)
        rel_ci = (b_rel.lower, b_rel.upper)
        warnings.extend(dict.fromkeys((*b_rel.warnings, *b_abs.warnings)))
        boot_resamples, boot_seed = resamples, seed

    p_value = float(2 * stats.norm.sf(abs(diff) / se)) if se > 0 else (1.0 if diff == 0 else 0.0)
    return RatioOfSumsResult(
        estimand=estimand,
        numerator=numerator,
        denominator=denominator,
        unit_col=unit_col,
        method=method,
        alpha=alpha,
        n_units_treatment=len(c_t),
        n_units_control=len(c_c),
        ratio_treatment=float(r_t),
        ratio_control=float(r_c),
        se_ratio_treatment=float(se_t),
        se_ratio_control=float(se_c),
        absolute_difference=float(diff),
        standard_error=float(se),
        confidence_interval=(float(ci[0]), float(ci[1])),
        relative_lift=float(rel),
        relative_confidence_interval=(float(rel_ci[0]), float(rel_ci[1])),
        p_value=p_value,
        resamples=boot_resamples,
        seed=boot_seed,
        warnings=tuple(warnings),
    )


def ratio_of_sums(
    frame: pd.DataFrame,
    numerator: str,
    denominator: str,
    unit_col: str,
    arm_col: str,
    *,
    estimand: str,
    treatment: str = "treatment",
    control: str = "control",
    alpha: float = 0.05,
    method: str = "delta",
    resamples: int = 1000,
    seed: int | None = None,
    interval: str = "percentile",
) -> RatioOfSumsResult:
    """Ratio of sums per arm and its treatment-minus-control contrasts, at the unit level.

    Parameters
    ----------
    frame
        Rows at any grain (impression, page view, user); ``numerator`` and ``denominator`` are
        summed within ``unit_col``. Every unit must sit in one arm.
    numerator, denominator
        Column names, e.g. ``"clicks"`` and ``"inviews"``.
    unit_col
        The independent unit: ``"user_id"``, or ``"draw_id"`` in the Case 02 harness, where a
        user drawn twice is two units.
    arm_col, treatment, control
        Arm column and the labels of the two arms compared.
    estimand
        The quantity targeted, recorded on the result.
    alpha
        Two-sided level.
    method
        ``"delta"`` (default), ``"jackknife"`` or ``"bootstrap"``.
    resamples, seed, interval
        Bootstrap only. ``seed`` is required for the bootstrap so that every interval can be
        reproduced; ``interval`` is ``"percentile"`` (about 1,000 resamples) or ``"normal"``
        (200 are enough for a standard error, Efron and Tibshirani 1993, chapter 6).

    Notes
    -----
    Delta-method intervals: the absolute difference is normal with Var = Var(R_t) + Var(R_c);
    the relative lift uses the log ratio, Var(log R) ~ Var(R) / R^2. The jackknife uses the
    same two forms with jackknife variances of R and of log R. The p-value tests a zero
    difference with a normal reference in all three methods.
    """
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}; got {method!r}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1); got {alpha}")
    for col in (numerator, denominator, unit_col, arm_col):
        if col not in frame.columns:
            raise ValueError(f"column {col!r} not in frame")
    if frame[unit_col].isna().any() or frame[arm_col].isna().any():
        raise ValueError("unit and arm columns must have no missing values")

    units = frame.groupby([unit_col, arm_col], sort=False)[[numerator, denominator]].sum()
    unit_ids = units.index.get_level_values(unit_col)
    if unit_ids.duplicated().any():
        raise ValueError(f"a unit in {unit_col!r} appears in more than one arm")
    arms = units.index.get_level_values(arm_col)
    parts = {}
    for label in (treatment, control):
        sub = units[arms == label]
        if len(sub) < 2:
            raise ValueError(f"arm {label!r} needs at least two units; found {len(sub)}")
        if sub[denominator].sum() <= 0:
            raise ValueError(f"denominator total is zero in arm {label!r}")
        parts[label] = sub
    other = set(pd.unique(arms)) - {treatment, control}
    warnings: list[str] = []
    if other:
        warnings.append(f"units in arms {sorted(map(str, other))} were ignored")

    c_t = parts[treatment][numerator].to_numpy(float)
    m_t = parts[treatment][denominator].to_numpy(float)
    c_c = parts[control][numerator].to_numpy(float)
    m_c = parts[control][denominator].to_numpy(float)
    return ratio_of_sums_arrays(
        c_t,
        m_t,
        c_c,
        m_c,
        estimand=estimand,
        numerator=numerator,
        denominator=denominator,
        unit_col=unit_col,
        alpha=alpha,
        method=method,
        resamples=resamples,
        seed=seed,
        interval=interval,
        warnings=warnings,
    )


def naive_event_rate(
    clicks_t: int,
    impressions_t: int,
    clicks_c: int,
    impressions_c: int,
    alpha: float = 0.05,
    *,
    estimand: str = "platform click-through rate, ratio of expected totals",
) -> ProportionDifferenceResult:
    """The dashboard's test: pooled two-proportion z-test with n = impressions per arm.

    Kept so that Case 02 can show its miscalibration; the result always carries
    ``NAIVE_WARNING``. The point estimate equals the ratio of sums; only the interval and the
    p-value are wrong when impressions are clustered within users.
    """
    result = difference_in_proportions(
        clicks_t,
        impressions_t,
        clicks_c,
        impressions_c,
        estimand=estimand,
        analysis_unit="impression",
        population="all impressions",
        alpha=alpha,
    )
    return dataclasses.replace(
        result,
        assumptions=(
            *result.assumptions[:1],
            "impressions are independent (violated when they cluster within users)",
            *result.assumptions[2:],
        ),
        warnings=(NAIVE_WARNING, *result.warnings),
    )
