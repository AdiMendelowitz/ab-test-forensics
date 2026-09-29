"""Balance diagnostics: invariant metrics and exposure rates by segment.

Both functions compare arms on quantities that random assignment should have balanced.
A failure on the assigned population means assignment itself is suspect. A pass on the
assigned population together with a failure on the exposed (or analysed) population is
the signature of a post-assignment selection mechanism, which is case study 01.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
from scipy import stats

from abforensics.results import InvariantResult, SegmentExposureResult

_MIN_CELL = 5


def invariant_check(
    population: pd.DataFrame,
    *,
    arm_col: str,
    metric_cols: Sequence[str],
    level: str,
    treatment_arm: str,
    control_arm: str,
    alpha: float = 0.001,
) -> InvariantResult:
    """Test each metric for balance between two named arms on the given population.

    Numeric columns are compared with Welch's t-test on arm means; ``difference`` is
    treatment minus control. Categorical columns (object, string, category, bool) are
    compared with a chi-square test of independence between arm and category. Missing
    values are counted and reported, never silently dropped: for numeric metrics the test
    uses the non-missing values and the missing share is compared as a categorical metric
    of its own. Rows from arms other than the two named ones are excluded with a warning.

    Parameters
    ----------
    population
        One row per unit; must contain ``arm_col`` and every ``metric_cols`` entry.
    arm_col
        Column holding the arm label.
    metric_cols
        Pre-treatment attributes expected to be balanced.
    level
        Which population this is (``"assigned"``, ``"exposed"``), recorded on the result.
    treatment_arm, control_arm
        Arm labels. Explicit so that the sign of every difference is defined by the
        caller, never by the order in which arms happen to appear in the data.
    alpha
        Per-metric flag threshold. Defaults to 0.001 for the same reason as ``srm_check``.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1); got {alpha}")
    missing_cols = [c for c in (arm_col, *metric_cols) if c not in population.columns]
    if missing_cols:
        raise ValueError(f"columns not in population: {missing_cols}")
    if len(metric_cols) == 0:
        raise ValueError("metric_cols is empty")

    present = set(population[arm_col].dropna().unique())
    if {treatment_arm, control_arm} - present:
        raise ValueError(
            f"arms {treatment_arm!r}/{control_arm!r} not both present in population: {present}"
        )
    warnings: list[str] = []
    extra = present - {treatment_arm, control_arm}
    if extra:
        warnings.append(f"rows from other arms excluded from the comparison: {sorted(extra)}")
        population = population[population[arm_col].isin([treatment_arm, control_arm])]
    n_by_arm = population[arm_col].value_counts().to_dict()

    rows: list[dict] = []
    for col in metric_cols:
        series = population[col]
        n_missing = int(series.isna().sum())
        if n_missing:
            miss_row = _categorical_row(
                population[arm_col],
                series.isna().map({True: "missing", False: "present"}),
                f"{col}[missing]",
            )
            rows.append(miss_row)
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            x = series[population[arm_col] == treatment_arm].dropna().to_numpy(dtype=float)
            y = series[population[arm_col] == control_arm].dropna().to_numpy(dtype=float)
            stat, p = stats.ttest_ind(x, y, equal_var=False)
            rows.append(
                {
                    "metric": col,
                    "kind": "numeric",
                    "summary": {treatment_arm: float(x.mean()), control_arm: float(y.mean())},
                    "difference": float(x.mean() - y.mean()),
                    "statistic": float(stat),
                    "p_value": float(p),
                    "n_missing": n_missing,
                }
            )
        else:
            rows.append(_categorical_row(population[arm_col], series, col))
            row = rows[-1]
            if row.get("small_cells"):
                warnings.append(
                    f"{col}: {row['small_cells']} contingency cells below {_MIN_CELL}; "
                    "chi-square approximation is unreliable there."
                )

    table = pd.DataFrame(rows)
    table["flag"] = table["p_value"] < alpha
    flagged = tuple(table.loc[table["flag"], "metric"].tolist())
    warnings.append(
        f"{len(table)} tests at alpha={alpha}: expect up to {len(table) * alpha:.3f} false flags "
        "under perfect balance."
    )
    return InvariantResult(
        level=level,
        alpha=alpha,
        table=table,
        flagged=flagged,
        n_by_arm={str(k): int(v) for k, v in n_by_arm.items()},
        warnings=tuple(warnings),
    )


def _categorical_row(arm: pd.Series, values: pd.Series, name: str) -> dict:
    contingency = pd.crosstab(arm, values.astype("string").fillna("<NA>"))
    if contingency.shape[1] < 2:
        return {
            "metric": name,
            "kind": "categorical",
            "summary": contingency.to_dict(orient="index"),
            "difference": np.nan,
            "statistic": np.nan,
            "p_value": 1.0,
            "n_missing": int(values.isna().sum()),
        }
    chi2, p, _, expected = stats.chi2_contingency(contingency.to_numpy())
    shares = contingency.div(contingency.sum(axis=1), axis=0)
    # largest absolute share gap across categories between the first two arms
    if shares.shape[0] >= 2:
        gap = float((shares.iloc[0] - shares.iloc[1]).abs().max())
    else:
        gap = np.nan
    return {
        "metric": name,
        "kind": "categorical",
        "summary": shares.round(4).to_dict(orient="index"),
        "difference": gap,
        "statistic": float(chi2),
        "p_value": float(p),
        "n_missing": int(values.isna().sum()),
        "small_cells": int((expected < _MIN_CELL).sum()),
    }


def exposure_rate_by_segment(
    assignments: pd.DataFrame,
    exposures: pd.DataFrame,
    users: pd.DataFrame,
    *,
    segment_cols: Sequence[str],
    treatment_arm: str,
    control_arm: str,
    user_col: str = "user_id",
    arm_col: str = "arm",
    alpha: float = 0.001,
) -> SegmentExposureResult:
    """Share of assigned units with at least one exposure event, per arm and segment.

    Joins ``assignments`` to ``users`` on ``user_col`` (inner; unmatched rows are counted
    and reported in a warning, never dropped silently), flags each user as exposed if they
    appear in ``exposures`` at least once, and compares the exposure rate between the two
    named arms within every segment with a two-proportion z-test. The overall row is
    included so that the reader sees where the aggregate gap comes from.

    The arm names are explicit parameters because the function cannot know which label is
    the treatment, and the gap's sign matters.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1); got {alpha}")
    if not segment_cols:
        raise ValueError("segment_cols is empty")
    for col in segment_cols:
        if col not in users.columns:
            raise ValueError(f"segment column {col!r} not in users")
    present_arms = set(assignments[arm_col].unique())
    if {treatment_arm, control_arm} - present_arms:
        raise ValueError(
            f"arms {treatment_arm!r}/{control_arm!r} not both present in assignments: "
            f"{present_arms}"
        )

    warnings: list[str] = []
    joined = assignments[[user_col, arm_col]].merge(
        users[[user_col, *segment_cols]], on=user_col, how="left"
    )
    unmatched = int(joined[list(segment_cols)].isna().all(axis=1).sum())
    if unmatched:
        warnings.append(
            f"{unmatched} assigned users have no row in users; they are kept with NA segments."
        )
    exposed_ids = set(exposures[user_col].unique())
    joined["exposed"] = joined[user_col].isin(exposed_ids)
    stray = len(exposed_ids - set(assignments[user_col]))
    if stray:
        warnings.append(
            f"{stray} exposed users are not in assignments; ignored, but this is itself a finding."
        )

    two_arms = joined[joined[arm_col].isin([treatment_arm, control_arm])]
    grouped = (
        two_arms.groupby([*segment_cols, arm_col], dropna=False, observed=True)["exposed"]
        .agg(n_assigned="size", n_exposed="sum")
        .reset_index()
    )
    wide = grouped.pivot_table(
        index=list(segment_cols), columns=arm_col, values=["n_assigned", "n_exposed"], fill_value=0
    )
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    wide = wide.reset_index()
    overall = {c: "<all>" for c in segment_cols}
    for arm in (treatment_arm, control_arm):
        sub = two_arms[two_arms[arm_col] == arm]
        overall[f"n_assigned_{arm}"] = int(len(sub))
        overall[f"n_exposed_{arm}"] = int(sub["exposed"].sum())
    table = pd.concat([pd.DataFrame([overall]), wide], ignore_index=True)

    for arm in (treatment_arm, control_arm):
        table[f"rate_{arm}"] = table[f"n_exposed_{arm}"] / table[f"n_assigned_{arm}"].replace(
            0, np.nan
        )
    table["gap"] = table[f"rate_{treatment_arm}"] - table[f"rate_{control_arm}"]
    table["p_value"] = [
        _two_proportion_p(
            row[f"n_exposed_{treatment_arm}"],
            row[f"n_assigned_{treatment_arm}"],
            row[f"n_exposed_{control_arm}"],
            row[f"n_assigned_{control_arm}"],
        )
        for _, row in table.iterrows()
    ]
    table["flag"] = table["p_value"] < alpha
    labels = table[list(segment_cols)].astype(str).agg(" / ".join, axis=1)
    flagged = tuple(labels[table["flag"]].tolist())
    overall_gap = float(table.loc[0, "gap"])
    return SegmentExposureResult(
        treatment_arm=treatment_arm,
        control_arm=control_arm,
        segment_cols=tuple(segment_cols),
        table=table,
        overall_gap=overall_gap,
        flagged_segments=flagged,
        alpha=alpha,
        warnings=tuple(warnings),
    )


def _two_proportion_p(x1: float, n1: float, x2: float, n2: float) -> float:
    if n1 == 0 or n2 == 0:
        return float("nan")
    p = (x1 + x2) / (n1 + n2)
    se = np.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (x1 / n1 - x2 / n2) / se
    return float(2 * stats.norm.sf(abs(z)))
