"""Unit tests for invariant_check and exposure_rate_by_segment."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from abforensics import exposure_rate_by_segment, invariant_check

ARMS = {"level": "assigned", "treatment_arm": "treatment", "control_arm": "control"}
EXPOSED = {**ARMS, "level": "exposed"}


def balanced_population(n: int = 20_000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "user_id": np.arange(n),
            "arm": rng.choice(["control", "treatment"], size=n),
            "platform": rng.choice(["ios", "android", "web"], size=n, p=[0.4, 0.4, 0.2]),
            "pre_orders": rng.poisson(0.6, size=n),
            "is_new": rng.random(n) < 0.3,
        }
    )


def test_balanced_population_is_not_flagged() -> None:
    pop = balanced_population()
    r = invariant_check(
        pop, arm_col="arm", metric_cols=["platform", "pre_orders", "is_new"], **ARMS
    )
    assert r.level == "assigned"
    assert set(r.table["metric"]) == {"platform", "pre_orders", "is_new"}
    assert r.table.set_index("metric").loc["platform", "kind"] == "categorical"
    assert r.table.set_index("metric").loc["pre_orders", "kind"] == "numeric"
    assert r.table.set_index("metric").loc["is_new", "kind"] == "categorical"
    assert not r.any_flagged
    assert r.n_by_arm["control"] + r.n_by_arm["treatment"] == len(pop)


def test_imbalanced_categorical_and_numeric_are_flagged() -> None:
    pop = balanced_population()
    treat = pop["arm"] == "treatment"
    # Remove 40% of treatment android users: platform mix shifts; and add 1 to their pre_orders.
    drop = (
        treat & (pop["platform"] == "android") & (np.random.default_rng(1).random(len(pop)) < 0.4)
    )
    pop = pop[~drop].copy()
    pop.loc[pop["arm"] == "treatment", "pre_orders"] += 1
    r = invariant_check(
        pop,
        arm_col="arm",
        metric_cols=["platform", "pre_orders"],
        level="exposed",
        treatment_arm="treatment",
        control_arm="control",
    )
    assert set(r.flagged) == {"platform", "pre_orders"}
    row = r.table.set_index("metric").loc["pre_orders"]
    assert row["difference"] == pytest.approx(
        pop.loc[pop.arm == "treatment", "pre_orders"].mean()
        - pop.loc[pop.arm == "control", "pre_orders"].mean()
    )


def test_missing_values_are_reported_not_dropped() -> None:
    pop = balanced_population(n=5_000)
    pop.loc[pop.index[:100], "pre_orders"] = np.nan
    r = invariant_check(
        pop,
        arm_col="arm",
        metric_cols=["pre_orders"],
        level="assigned",
        treatment_arm="treatment",
        control_arm="control",
    )
    metrics = set(r.table["metric"])
    assert metrics == {"pre_orders", "pre_orders[missing]"}
    assert r.table.set_index("metric").loc["pre_orders", "n_missing"] == 100


def test_multiplicity_warning_present() -> None:
    r = invariant_check(balanced_population(2_000), arm_col="arm", metric_cols=["platform"], **ARMS)
    assert any("false flags" in w for w in r.warnings)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"arm_col": "nope", "metric_cols": ["platform"], "level": "a"}, "not in population"),
        ({"arm_col": "arm", "metric_cols": [], "level": "a"}, "empty"),
        ({"arm_col": "arm", "metric_cols": ["platform"], "level": "a", "alpha": 2}, "alpha"),
        (
            {"arm_col": "arm", "metric_cols": ["platform"], "level": "a", "treatment_arm": "x"},
            "not both present",
        ),
    ],
)
def test_invariant_invalid_inputs(kwargs, match) -> None:
    kwargs = {"treatment_arm": "treatment", "control_arm": "control", **kwargs}
    with pytest.raises(ValueError, match=match):
        invariant_check(balanced_population(500), **kwargs)


def test_third_arm_is_excluded_with_warning() -> None:
    pop = balanced_population(3_000)
    pop.loc[pop.index[:500], "arm"] = "t2"
    r = invariant_check(
        pop,
        arm_col="arm",
        metric_cols=["pre_orders"],
        level="assigned",
        treatment_arm="treatment",
        control_arm="control",
    )
    assert set(r.n_by_arm) == {"treatment", "control"}
    assert any("t2" in w for w in r.warnings)


def _experiment(n: int = 40_000, loss: float = 0.35, seed: int = 0):
    rng = np.random.default_rng(seed)
    users = pd.DataFrame(
        {
            "user_id": np.arange(n),
            "platform": rng.choice(["ios", "android", "web"], size=n, p=[0.4, 0.4, 0.2]),
            "old": rng.random(n) < 0.25,
        }
    )
    assignments = pd.DataFrame(
        {"user_id": users.user_id, "arm": rng.choice(["control", "treatment"], size=n)}
    )
    affected = (users.platform == "android") & users.old & (assignments.arm == "treatment")
    logged = ~(affected & (rng.random(n) < loss))
    exposures = pd.DataFrame(
        {"user_id": users.user_id[logged], "event_id": np.arange(int(logged.sum()))}
    )
    return users, assignments, exposures


def test_exposure_gap_is_localised_to_the_affected_segment() -> None:
    users, assignments, exposures = _experiment()
    r = exposure_rate_by_segment(
        assignments,
        exposures,
        users,
        segment_cols=["platform", "old"],
        treatment_arm="treatment",
        control_arm="control",
    )
    t = r.table.set_index(["platform", "old"])
    assert t.loc[("<all>", "<all>"), "flag"]
    assert t.loc[("android", True), "flag"]
    assert t.loc[("android", True), "gap"] == pytest.approx(-0.35, abs=0.04)
    for key in [("android", False), ("ios", True), ("ios", False), ("web", True), ("web", False)]:
        assert t.loc[key, "gap"] == 0.0
        assert not t.loc[key, "flag"]
    assert r.flagged_segments == ("<all> / <all>", "android / True")
    assert r.overall_gap < 0


def test_no_incident_no_flags() -> None:
    users, assignments, exposures = _experiment(loss=0.0)
    r = exposure_rate_by_segment(
        assignments,
        exposures,
        users,
        segment_cols=["platform"],
        treatment_arm="treatment",
        control_arm="control",
    )
    assert not r.flagged_segments
    assert (r.table["gap"] == 0).all()


def test_unmatched_and_stray_users_are_warned_about() -> None:
    users, assignments, exposures = _experiment(n=2_000, loss=0.0)
    users = users.iloc[:-10]
    exposures = pd.concat([exposures, pd.DataFrame({"user_id": [10_000], "event_id": [-1]})])
    r = exposure_rate_by_segment(
        assignments,
        exposures,
        users,
        segment_cols=["platform"],
        treatment_arm="treatment",
        control_arm="control",
    )
    assert any("10 assigned users" in w for w in r.warnings)
    assert any("1 exposed users are not in assignments" in w for w in r.warnings)


def test_exposure_invalid_inputs() -> None:
    users, assignments, exposures = _experiment(n=500, loss=0.0)
    with pytest.raises(ValueError, match="not both present"):
        exposure_rate_by_segment(
            assignments,
            exposures,
            users,
            segment_cols=["platform"],
            treatment_arm="x",
            control_arm="control",
        )
    with pytest.raises(ValueError, match="not in users"):
        exposure_rate_by_segment(
            assignments,
            exposures,
            users,
            segment_cols=["nope"],
            treatment_arm="treatment",
            control_arm="control",
        )
    with pytest.raises(ValueError, match="empty"):
        exposure_rate_by_segment(
            assignments,
            exposures,
            users,
            segment_cols=[],
            treatment_arm="treatment",
            control_arm="control",
        )
