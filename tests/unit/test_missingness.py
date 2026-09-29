"""Unit tests for event_arrival_lag."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from abforensics import event_arrival_lag


def _events(n: int = 20_000, seed: int = 0, treatment_extra_lag_h: float = 0.0):
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp("2026-03-02T00:00:00Z")
    assignments = pd.DataFrame(
        {"user_id": np.arange(n), "arm": rng.choice(["control", "treatment"], size=n)}
    )
    event_time = t0 + pd.to_timedelta(rng.random(n) * 14 * 24, "h")
    lag = rng.uniform(0, 36, size=n) + np.where(
        assignments.arm == "treatment", treatment_extra_lag_h, 0.0
    )
    events = pd.DataFrame(
        {
            "user_id": np.arange(n),
            "ordered_at": event_time,
            "received_at": event_time + pd.to_timedelta(lag, "h"),
        }
    )
    return events, assignments, t0


def test_non_differential_lag_is_not_flagged() -> None:
    events, assignments, t0 = _events()
    r = event_arrival_lag(
        events,
        assignments,
        event_time_col="ordered_at",
        received_time_col="received_at",
        analysis_time=t0 + pd.Timedelta(days=14),
    )
    assert set(r.table["arm"]) == {"control", "treatment"}
    assert not r.differential
    assert r.ks_p_value > 0.001
    assert (r.table["max_lag_h"] <= 36).all()
    assert r.table["mean_lag_h"].between(17, 19).all()
    # Events in the last 36 hours of a 14-day window are partly unavailable: ~1.5/14 * 0.5
    assert r.table["share_unavailable_at_analysis"].between(0.02, 0.08).all()


def test_differential_lag_is_flagged() -> None:
    events, assignments, t0 = _events(treatment_extra_lag_h=6.0)
    r = event_arrival_lag(
        events,
        assignments,
        event_time_col="ordered_at",
        received_time_col="received_at",
        analysis_time=t0 + pd.Timedelta(days=14),
    )
    assert r.differential
    t = r.table.set_index("arm")
    assert t.loc["treatment", "mean_lag_h"] - t.loc["control", "mean_lag_h"] == pytest.approx(
        6.0, abs=1.0
    )


def test_unassigned_and_negative_lags_are_warned() -> None:
    events, assignments, t0 = _events(n=1_000)
    events.loc[0, "received_at"] = events.loc[0, "ordered_at"] - pd.Timedelta(hours=1)
    assignments = assignments.iloc[:-5]
    r = event_arrival_lag(
        events,
        assignments,
        event_time_col="ordered_at",
        received_time_col="received_at",
        analysis_time=t0 + pd.Timedelta(days=14),
    )
    assert any("5 events" in w for w in r.warnings)
    assert any("received before" in w for w in r.warnings)


def test_missing_column_raises() -> None:
    events, assignments, t0 = _events(n=100)
    with pytest.raises(ValueError, match="not in events"):
        event_arrival_lag(
            events,
            assignments,
            event_time_col="nope",
            received_time_col="received_at",
            analysis_time=t0,
        )
