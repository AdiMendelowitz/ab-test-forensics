"""Result objects returned by every public function in abforensics.

Design rules (see docs/design/case-01.md, section 11):

* every result names the estimand it targets, or states that it is a diagnostic with none;
* every result carries its assumptions and its warnings, so that nothing is hidden in a
  docstring the caller never reads;
* results are frozen: a result is evidence, not a mutable working object;
* no result contains a ship / no-ship decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SRMResult:
    """Outcome of a sample-ratio-mismatch check.

    This is a diagnostic hypothesis test on unit counts. It has no estimand. A small
    p-value is evidence that the observed counts are not compatible with the planned
    allocation; it is not proof of bias, and it says nothing about effect size.
    """

    observed_counts: Mapping[str, int]
    expected_counts: Mapping[str, float]
    expected_allocation: Mapping[str, float]
    chi2_stat: float
    p_value: float
    degrees_of_freedom: int
    alpha: float
    flag_srm: bool
    level: str  # what the counts are: "assignment", "exposure", "analysis", ...
    estimand: str = "none: diagnostic test on unit counts"
    assumptions: tuple[str, ...] = (
        "expected allocation is known and fixed for the whole period",
        "each count is a number of independent units at the stated level",
        "counts are complete for the level they describe",
    )
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return int(sum(self.observed_counts.values()))

    def summary(self) -> str:
        arms = ", ".join(
            f"{arm}={self.observed_counts[arm]:,} (expected {self.expected_counts[arm]:,.0f})"
            for arm in self.observed_counts
        )
        verdict = "MISMATCH" if self.flag_srm else "compatible"
        return (
            f"SRM check at {self.level} level: {verdict} with planned allocation. "
            f"{arms}; chi2={self.chi2_stat:.2f}, df={self.degrees_of_freedom}, "
            f"p={self.p_value:.3g}, alpha={self.alpha}."
        )


@dataclass(frozen=True)
class ProportionDifferenceResult:
    """Difference in two arm-level proportions with Wald uncertainty.

    ``estimand`` and ``analysis_unit`` are supplied by the caller; the function has no way
    to know whether the counts it was given are assigned users, exposed users or sessions,
    and refuses to guess.
    """

    estimand: str
    analysis_unit: str
    population: str  # e.g. "assigned", "exposed"
    rate_treatment: float
    rate_control: float
    n_treatment: int
    n_control: int
    absolute_difference: float
    standard_error: float
    confidence_interval: tuple[float, float]
    relative_lift: float
    relative_confidence_interval: tuple[float, float]
    p_value: float
    alpha: float
    assumptions: tuple[str, ...] = (
        "outcome is binary and observed once per unit",
        "units are independent (no clustering, no interference)",
        "arms were formed by random assignment of the units being compared",
        "sample sizes are large enough for the normal approximation",
    )
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        lo, hi = self.confidence_interval
        rlo, rhi = self.relative_confidence_interval
        return (
            f"{self.population} population, {self.analysis_unit} level: "
            f"treatment {self.rate_treatment:.4%} vs control {self.rate_control:.4%}; "
            f"difference {self.absolute_difference:+.4%} "
            f"[{lo:+.4%}, {hi:+.4%}], relative {self.relative_lift:+.2%} "
            f"[{rlo:+.2%}, {rhi:+.2%}], p={self.p_value:.3g}."
        )


@dataclass(frozen=True)
class InvariantResult:
    """Per-metric balance checks between arms on a stated population.

    ``table`` has one row per metric: kind (numeric or categorical), the per-arm summary,
    the test statistic, p-value and flag. ``flagged`` lists the metrics whose p-value fell
    below ``alpha``. This is a diagnostic; it has no estimand.
    """

    level: str
    alpha: float
    table: Any  # pandas.DataFrame; typed loosely to keep results.py free of pandas
    flagged: tuple[str, ...]
    n_by_arm: Mapping[str, int]
    estimand: str = "none: diagnostic balance checks"
    assumptions: tuple[str, ...] = (
        "each metric is measured before, or independently of, treatment",
        "units are independent within and across arms",
        "alpha applies per metric; with k metrics the family-wise rate is up to k * alpha",
    )
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def any_flagged(self) -> bool:
        return len(self.flagged) > 0


@dataclass(frozen=True)
class SegmentExposureResult:
    """Exposure rate per arm within each segment, with the treatment-minus-control gap."""

    treatment_arm: str
    control_arm: str
    segment_cols: tuple[str, ...]
    table: Any  # pandas.DataFrame
    overall_gap: float
    flagged_segments: tuple[str, ...]
    alpha: float
    estimand: str = "none: diagnostic on exposure logging"
    assumptions: tuple[str, ...] = (
        "every assigned unit should produce an exposure event with probability independent of arm",
        "segment columns are pre-treatment attributes",
    )
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ArrivalLagResult:
    """Distribution of event arrival lag by arm and the share unavailable at analysis time."""

    table: Any  # pandas.DataFrame, one row per arm
    ks_statistic: float
    ks_p_value: float
    analysis_time: Any
    differential: bool
    alpha: float
    estimand: str = "none: diagnostic on outcome-event completeness"
    assumptions: tuple[str, ...] = (
        "received_time is the moment the event became available to analysis",
        "events not yet received are absent from the analysed outcome table",
    )
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class JackknifeResult:
    """Delete-one-unit jackknife variance of a statistic of column totals.

    Carries no interval and no decision: it is a variance estimate for the named estimand,
    at the stated unit.
    """

    statistic_name: str
    estimand: str
    unit_col: str
    n_units: int
    estimate: float
    variance: float
    standard_error: float
    assumptions: tuple[str, ...] = (
        "units are independent; rows within a unit may be dependent",
        "the statistic is a smooth function of column totals (the jackknife fails for "
        "non-smooth statistics such as the median)",
    )
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        return (
            f"Jackknife over {self.n_units:,} {self.unit_col} units: {self.statistic_name} = "
            f"{self.estimate:.6g}, SE {self.standard_error:.4g}."
        )


@dataclass(frozen=True)
class BootstrapResult:
    """Cluster bootstrap of a statistic of column totals, resampling whole units."""

    statistic_name: str
    estimand: str
    unit_col: str
    strata_col: str | None
    n_units: int
    estimate: float
    standard_error: float
    lower: float
    upper: float
    interval: str  # "percentile" or "normal"
    alpha: float
    resamples: int
    seed: int
    distribution: Any = field(repr=False, compare=False)
    assumptions: tuple[str, ...] = (
        "units are independent; rows within a unit may be dependent",
        "resampling units with replacement mimics the sampling of units from the population",
    )
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        return (
            f"Cluster bootstrap over {self.n_units:,} {self.unit_col} units, {self.resamples:,} "
            f"resamples (seed {self.seed}): {self.statistic_name} = {self.estimate:.6g}, "
            f"{self.interval} {1 - self.alpha:.0%} interval [{self.lower:.6g}, {self.upper:.6g}]."
        )


@dataclass(frozen=True)
class RatioOfSumsResult:
    """A ratio of sums per arm (e.g. clicks per impression) and its between-arm contrasts.

    The estimand is the ratio of expected totals, E[sum numerator] / E[sum denominator], per
    arm: an impression-weighted rate, not the mean of per-unit rates. Uncertainty is computed
    at the unit level named in ``unit_col``, never at the grain of the denominator's events.
    """

    estimand: str
    numerator: str
    denominator: str
    unit_col: str
    method: str  # "delta", "jackknife" or "bootstrap"
    alpha: float
    n_units_treatment: int
    n_units_control: int
    ratio_treatment: float
    ratio_control: float
    se_ratio_treatment: float
    se_ratio_control: float
    absolute_difference: float
    standard_error: float
    confidence_interval: tuple[float, float]
    relative_lift: float
    relative_confidence_interval: tuple[float, float]
    p_value: float
    resamples: int | None = None
    seed: int | None = None
    assumptions: tuple[str, ...] = (
        "units are independent; events within a unit may be dependent",
        "arms were formed by random assignment of the units",
        "the ratio is a smooth function of totals and n is large enough for the normal "
        "approximation (delta and jackknife) or for the bootstrap distribution",
    )
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        lo, hi = self.confidence_interval
        rlo, rhi = self.relative_confidence_interval
        return (
            f"sum({self.numerator}) / sum({self.denominator}) by {self.unit_col}, "
            f"{self.method}: treatment {self.ratio_treatment:.5g} vs control "
            f"{self.ratio_control:.5g}; difference {self.absolute_difference:+.5g} "
            f"[{lo:+.5g}, {hi:+.5g}], relative {self.relative_lift:+.2%} "
            f"[{rlo:+.2%}, {rhi:+.2%}], p={self.p_value:.3g}."
        )


@dataclass(frozen=True)
class MeanDifferenceResult:
    """Difference in two arm means (Welch) and the relative lift (delta method, log ratio)."""

    estimand: str
    analysis_unit: str
    mean_treatment: float
    mean_control: float
    sd_treatment: float
    sd_control: float
    n_treatment: int
    n_control: int
    absolute_difference: float
    standard_error: float
    degrees_of_freedom: float
    confidence_interval: tuple[float, float]
    relative_lift: float
    relative_confidence_interval: tuple[float, float]
    p_value: float
    alpha: float
    assumptions: tuple[str, ...] = (
        "units are independent within and across arms",
        "the outcome has finite variance; with heavy tails the normal approximation needs large n",
        "arms were formed by random assignment of the units being compared",
    )
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        lo, hi = self.confidence_interval
        rlo, rhi = self.relative_confidence_interval
        return (
            f"{self.analysis_unit} means: treatment {self.mean_treatment:.5g} vs control "
            f"{self.mean_control:.5g}; difference {self.absolute_difference:+.5g} "
            f"[{lo:+.5g}, {hi:+.5g}] (Welch, df {self.degrees_of_freedom:,.0f}), relative "
            f"{self.relative_lift:+.2%} [{rlo:+.2%}, {rhi:+.2%}], p={self.p_value:.3g}."
        )
