"""Unit-level resampling: the delete-one-unit jackknife and the cluster bootstrap.

Both functions take a frame at any grain (impressions, sessions, users), aggregate it to one
row of column sums per unit, and treat the unit as the independent observation. The unit is
whatever was randomised or, in the Case 02 harness, drawn: a user drawn twice is two units
(Case 02 README, Units and estimand).

A statistic is a function of column totals. It receives a DataFrame whose rows are sets of
totals (one row per leave-one-out set, per bootstrap resample, or a single row for the full
sample) and returns one value per row. Writing statistics this way makes the jackknife a single
vectorised pass: leave-one-unit-out totals are the full totals minus each unit's row, which is
the closed form for any statistic of totals, including a ratio of sums.

References: Efron and Tibshirani (1993), chapters 6 and 11 (bootstrap standard errors,
jackknife); Deng, Knoblich and Lu (2018) for the delta method these cross-check; Bakshy and
Eckles (2013) for resampling the unit, not the event, under dependent data.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from abforensics.results import BootstrapResult, JackknifeResult

Statistic = Callable[[pd.DataFrame], np.ndarray]

_CHUNK = 64  # bootstrap resamples per matrix product; bounds memory at large n


@dataclass(frozen=True)
class RatioOfSums:
    """Statistic sum(numerator) / sum(denominator), vectorised over rows of totals."""

    numerator: str
    denominator: str

    @property
    def name(self) -> str:
        return f"sum({self.numerator}) / sum({self.denominator})"

    def __call__(self, totals: pd.DataFrame) -> np.ndarray:
        den = totals[self.denominator].to_numpy(dtype=float)
        if np.any(den == 0):
            raise ValueError(f"denominator total {self.denominator!r} is zero in some row")
        return totals[self.numerator].to_numpy(dtype=float) / den


def _statistic_name(statistic: Statistic) -> str:
    return getattr(statistic, "name", getattr(statistic, "__name__", repr(statistic)))


def _require_finite(frame: pd.DataFrame, cols: list[str]) -> None:
    bad = {c: int((~np.isfinite(frame[c].to_numpy(dtype=float))).sum()) for c in cols}
    bad = {c: n for c, n in bad.items() if n}
    if bad:
        raise ValueError(
            f"non-finite values in {bad}; abforensics never drops missing values silently"
        )


def _unit_sums(
    frame: pd.DataFrame, unit_col: str, extra_keys: tuple[str, ...] = ()
) -> pd.DataFrame:
    if unit_col not in frame.columns:
        raise ValueError(f"unit column {unit_col!r} not in frame")
    if frame[unit_col].isna().any():
        raise ValueError(f"unit column {unit_col!r} has missing values")
    keys = [unit_col, *extra_keys]
    value_cols = [
        c
        for c in frame.columns
        if c not in keys
        and pd.api.types.is_numeric_dtype(frame[c])
        and not pd.api.types.is_bool_dtype(frame[c])
    ]
    if not value_cols:
        raise ValueError("frame has no numeric columns to total")
    _require_finite(frame, value_cols)
    sums = frame.groupby(keys, sort=False)[value_cols].sum()
    if len(sums) < 2:
        raise ValueError("at least two units are required")
    if sums.index.get_level_values(unit_col).duplicated().any():
        raise ValueError(f"a unit in {unit_col!r} belongs to more than one stratum")
    return sums


def _evaluate(statistic: Statistic, totals: pd.DataFrame) -> np.ndarray:
    out = np.asarray(statistic(totals), dtype=float).reshape(-1)
    if out.shape[0] != len(totals):
        raise ValueError("statistic must return one value per row of totals")
    return out


def jackknife_variance(
    frame: pd.DataFrame,
    unit_col: str,
    statistic: Statistic,
    *,
    estimand: str,
) -> JackknifeResult:
    """Delete-one-unit jackknife variance of a statistic of column totals.

    Var_J = (n - 1) / n * sum_i (theta_(i) - theta_bar)^2, where theta_(i) is the statistic on
    the totals with unit i removed. Leave-one-out totals are formed as full totals minus each
    unit's sums, so the cost is one pass over the units for any statistic of totals.

    Parameters
    ----------
    frame
        Rows at any grain; numeric columns are summed within ``unit_col``.
    unit_col
        The independent unit (``"user_id"``, or ``"draw_id"`` in the Case 02 harness).
    statistic
        Callable on a DataFrame of totals returning one value per row, e.g. ``RatioOfSums``.
    estimand
        The quantity the statistic targets, recorded on the result.
    """
    sums = _unit_sums(frame, unit_col)
    n = len(sums)
    total = sums.sum(axis=0)
    estimate = float(_evaluate(statistic, total.to_frame().T)[0])
    loo = total.to_numpy()[None, :] - sums.to_numpy()
    theta = _evaluate(statistic, pd.DataFrame(loo, columns=sums.columns))
    variance = float((n - 1) / n * np.sum((theta - theta.mean()) ** 2))
    warnings: list[str] = []
    if not np.all(np.isfinite(theta)):
        warnings.append("some leave-one-out statistics are not finite")
    return JackknifeResult(
        statistic_name=_statistic_name(statistic),
        estimand=estimand,
        unit_col=unit_col,
        n_units=n,
        estimate=estimate,
        variance=variance,
        standard_error=float(np.sqrt(variance)),
        warnings=tuple(warnings),
    )


def cluster_bootstrap(
    frame: pd.DataFrame,
    unit_col: str,
    statistic: Statistic,
    resamples: int,
    seed: int,
    *,
    estimand: str,
    interval: str = "percentile",
    alpha: float = 0.05,
    strata_col: str | None = None,
) -> BootstrapResult:
    """Bootstrap a statistic of column totals by resampling units with replacement.

    Each resample draws ``n`` units with replacement (within each stratum, keeping stratum
    sizes, when ``strata_col`` is given, e.g. the arm), so the unit's internal dependence is
    kept intact. ``interval="percentile"`` returns the alpha/2 and 1 - alpha/2 quantiles of the
    bootstrap distribution; ``interval="normal"`` returns estimate +/- z * bootstrap SE, for
    which 50 to 200 resamples are enough (Efron and Tibshirani 1993, chapter 6), where a
    percentile interval needs on the order of 1,000.
    """
    if resamples < 2:
        raise ValueError("resamples must be at least 2")
    if not 0 < alpha < 1:
        raise ValueError("alpha must lie in (0, 1)")
    if interval not in ("percentile", "normal"):
        raise ValueError("interval must be 'percentile' or 'normal'")
    extra = (strata_col,) if strata_col is not None else ()
    if strata_col is not None and strata_col not in frame.columns:
        raise ValueError(f"strata column {strata_col!r} not in frame")
    sums = _unit_sums(frame, unit_col, extra)
    values = sums.to_numpy(dtype=float)
    columns = sums.columns
    n = len(sums)
    estimate = float(
        _evaluate(statistic, pd.DataFrame(values.sum(axis=0)[None, :], columns=columns))[0]
    )

    if strata_col is None:
        groups = [np.arange(n)]
    else:
        labels = sums.index.get_level_values(strata_col)
        groups = [np.flatnonzero(labels == s) for s in pd.unique(labels)]
        if any(len(g) < 2 for g in groups):
            raise ValueError("every stratum needs at least two units")

    rng = np.random.default_rng(seed)
    dist = np.empty(resamples)
    done = 0
    while done < resamples:
        b = min(_CHUNK, resamples - done)
        totals = np.zeros((b, values.shape[1]))
        for g in groups:
            k = len(g)
            picks = rng.integers(0, k, size=(b, k))
            counts = np.stack([np.bincount(row, minlength=k) for row in picks])
            totals += counts @ values[g]
        dist[done : done + b] = _evaluate(statistic, pd.DataFrame(totals, columns=columns))
        done += b

    se = float(np.std(dist, ddof=1))
    if interval == "percentile":
        lower, upper = (float(q) for q in np.quantile(dist, [alpha / 2, 1 - alpha / 2]))
    else:
        z = float(stats.norm.ppf(1 - alpha / 2))
        lower, upper = estimate - z * se, estimate + z * se
    warnings: list[str] = []
    if interval == "percentile" and resamples < 1000:
        warnings.append(f"percentile interval from {resamples} resamples; about 1,000 are advised")
    if not np.all(np.isfinite(dist)):
        warnings.append("some bootstrap statistics are not finite")
    return BootstrapResult(
        statistic_name=_statistic_name(statistic),
        estimand=estimand,
        unit_col=unit_col,
        strata_col=strata_col,
        n_units=n,
        estimate=estimate,
        standard_error=se,
        lower=lower,
        upper=upper,
        interval=interval,
        alpha=alpha,
        resamples=resamples,
        seed=seed,
        distribution=dist,
        warnings=tuple(warnings),
    )
