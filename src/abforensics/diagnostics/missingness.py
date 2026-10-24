"""Outcome-event completeness: arrival lag and the share of events not yet available.

A batch pipeline delivers outcome events some time after they happen. At analysis time
the most recent events are missing from the outcome table. This is harmless when it is
identical across arms and the analysis window is truncated accordingly; it is a bias when
it differs by arm. ``event_arrival_lag`` measures both.
"""

from __future__ import annotations

import pandas as pd
from scipy import stats

from abforensics.results import ArrivalLagResult


def event_arrival_lag(
    events: pd.DataFrame,
    assignments: pd.DataFrame,
    *,
    event_time_col: str,
    received_time_col: str,
    analysis_time: pd.Timestamp,
    user_col: str = "user_id",
    arm_col: str = "arm",
    alpha: float = 0.001,
) -> ArrivalLagResult:
    """Summarise ``received - event`` lag per arm and the share received after ``analysis_time``.

    Returns one row per arm with the event count, mean, median, p90 and maximum lag in
    hours, and the share of events whose ``received_time_col`` is later than
    ``analysis_time`` (unavailable to an analysis run then). An event with a missing
    receipt time is counted as unavailable and reported in a warning; an event with a
    missing event time is reported in a warning and excluded from the lag summaries. A
    two-sample Kolmogorov-Smirnov test compares the lag distributions of the two arms; a
    small p-value marks the lag as differential.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1); got {alpha}")
    for col in (event_time_col, received_time_col, user_col):
        if col not in events.columns:
            raise ValueError(f"column {col!r} not in events")
    joined = events[[user_col, event_time_col, received_time_col]].merge(
        assignments[[user_col, arm_col]], on=user_col, how="inner"
    )
    dropped = len(events) - len(joined)
    warnings: list[str] = []
    if dropped:
        warnings.append(f"{dropped} events belong to users not in assignments and were excluded.")
    missing_received = int(joined[received_time_col].isna().sum())
    missing_event = int(joined[event_time_col].isna().sum())
    if missing_received:
        warnings.append(
            f"{missing_received} events have no receipt time; they are counted as unavailable "
            "at the analysis time and excluded from the lag summaries."
        )
    if missing_event:
        warnings.append(
            f"{missing_event} events have no event time; they are excluded from the lag summaries."
        )
    lag_h = (joined[received_time_col] - joined[event_time_col]).dt.total_seconds() / 3600.0
    if (lag_h < 0).any():
        warnings.append(f"{int((lag_h < 0).sum())} events were received before they happened.")
    late = (joined[received_time_col] > analysis_time) | joined[received_time_col].isna()
    joined = joined.assign(lag_h=lag_h, late=late)

    table = (
        joined.groupby(arm_col)
        .agg(
            n_events=("lag_h", "size"),
            mean_lag_h=("lag_h", "mean"),
            median_lag_h=("lag_h", "median"),
            p90_lag_h=("lag_h", lambda s: s.quantile(0.9)),
            max_lag_h=("lag_h", "max"),
            share_unavailable_at_analysis=("late", "mean"),
        )
        .reset_index()
    )
    arms = table[arm_col].tolist()
    if len(arms) == 2:
        a = joined.loc[joined[arm_col] == arms[0], "lag_h"].dropna().to_numpy()
        b = joined.loc[joined[arm_col] == arms[1], "lag_h"].dropna().to_numpy()
        ks_stat, ks_p = stats.ks_2samp(a, b)
    else:
        ks_stat, ks_p = float("nan"), float("nan")
        warnings.append(f"KS comparison needs exactly two arms; found {len(arms)}.")
    return ArrivalLagResult(
        table=table,
        ks_statistic=float(ks_stat),
        ks_p_value=float(ks_p),
        analysis_time=analysis_time,
        differential=bool(ks_p < alpha) if ks_p == ks_p else False,
        alpha=alpha,
        warnings=tuple(warnings),
    )
