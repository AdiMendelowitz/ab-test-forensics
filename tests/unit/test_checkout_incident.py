"""Unit tests for the Case 01 generator.

The generator is a data-generating process with known truth, so the tests check that the
truth it claims (in the manifest and the ``truth`` table) matches what it produced, and
that the incident touches exactly the cell the spec says it touches.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from abforensics.simulation.checkout_incident import (
    CheckoutExperimentData,
    content_sha256,
    hash_assign,
    simulate_checkout_experiment,
    simulate_population,
    version_below,
    write_case_data,
)

SMALL_CONFIG = {
    "seed": 7,
    "days": 7,
    "eligible_users_per_day": 4_000,
    "allocation": {"control": 0.5, "treatment": 0.5},
    "baseline_order_rate": 0.032,
    "true_relative_lift": 0.03,
    "outcome_window_days": 7,
    "non_render_probability": 0.15,
    "incident": {
        "platform": "android",
        "app_version_below": "8.4",
        "exposure_loss_probability": 0.35,
        "affects_arm": "treatment",
    },
    "order_pipeline": {"arrival_delay_hours": {"min": 0, "max": 36}},
}


def _with_incident(**changes: object) -> dict:
    return {**SMALL_CONFIG, "incident": {**SMALL_CONFIG["incident"], **changes}}


@pytest.fixture(scope="module")
def data() -> CheckoutExperimentData:
    return simulate_checkout_experiment(SMALL_CONFIG)


def test_version_below() -> None:
    versions = np.asarray(["8.1", "8.3", "8.4", "8.10", "web"])
    assert version_below(versions, "8.4").tolist() == [True, True, False, False, False]


def test_hash_assign_is_deterministic_and_balanced() -> None:
    ids = np.arange(1, 200_001, dtype=np.int64)
    a = hash_assign(ids)
    assert np.array_equal(a, hash_assign(ids))
    assert set(np.unique(a).tolist()) == {0, 1}
    # Binomial(200000, 0.5) has SD ~ 224; allow 4 SD around 0.5.
    assert abs(a.mean() - 0.5) < 4 * 0.5 / math.sqrt(200_000)


def test_assignment_is_rerandomised_across_seeds() -> None:
    a = simulate_population(SMALL_CONFIG, np.random.default_rng(1))
    b = simulate_population(SMALL_CONFIG, np.random.default_rng(2))
    assert a.extras["hash_salt"] != b.extras["hash_salt"]
    # Two independent 50/50 bucketings agree on about half the users.
    agree = float((a.arm == b.arm).mean())
    assert abs(agree - 0.5) < 4 * 0.5 / math.sqrt(a.n)


def test_same_seed_same_tables() -> None:
    a = simulate_checkout_experiment(SMALL_CONFIG)
    b = simulate_checkout_experiment(SMALL_CONFIG)
    for name, frame in a.tables().items():
        pd.testing.assert_frame_equal(frame, b.tables()[name])
    assert a.manifest["realised"] == b.manifest["realised"]


def test_different_seed_different_tables() -> None:
    a = simulate_checkout_experiment(SMALL_CONFIG, seed=1)
    b = simulate_checkout_experiment(SMALL_CONFIG, seed=2)
    assert not a.orders.equals(b.orders)
    assert not a.assignments["arm"].equals(b.assignments["arm"])


def test_row_counts_and_keys(data: CheckoutExperimentData) -> None:
    n = SMALL_CONFIG["days"] * SMALL_CONFIG["eligible_users_per_day"]
    assert len(data.users) == n == len(data.assignments) == len(data.truth)
    assert data.users["user_id"].is_unique
    assert data.exposures["user_id"].is_unique  # one render event per user in v1
    assert data.exposures["event_id"].is_unique
    assert data.orders["order_id"].is_unique
    assert data.orders["user_id"].is_unique  # one order per converter in v1
    assert set(data.exposures["user_id"]).issubset(set(data.users["user_id"]))
    assert set(data.orders["user_id"]).issubset(set(data.users["user_id"]))
    assert data.manifest["row_counts"] == {k: len(v) for k, v in data.tables().items()}


def test_exposed_is_a_strict_subset_in_both_arms(data: CheckoutExperimentData) -> None:
    assigned = data.assignments["arm"].value_counts()
    n = len(data.assignments)
    assert abs(assigned["treatment"] / n - 0.5) < 4 * 0.5 / math.sqrt(n)

    exposed = data.exposures.merge(data.assignments, on="user_id")["arm"].value_counts()
    assert exposed["control"] < assigned["control"]
    assert exposed["treatment"] < assigned["treatment"]
    # Control loses only the arm-independent non-render share; treatment loses more.
    ctrl_rate = exposed["control"] / assigned["control"]
    se = math.sqrt(0.85 * 0.15 / assigned["control"])
    assert abs(ctrl_rate - 0.85) < 4 * se
    assert exposed["treatment"] / assigned["treatment"] < ctrl_rate


def test_render_is_arm_independent(data: CheckoutExperimentData) -> None:
    merged = data.truth.merge(data.assignments, on="user_id")
    rate = merged.groupby("arm")["rendered"].mean()
    n_arm = merged["arm"].value_counts()
    se = math.sqrt(0.85 * 0.15 * (1 / n_arm["treatment"] + 1 / n_arm["control"]))
    assert abs(rate["treatment"] - rate["control"]) < 4 * se


def test_incident_hits_exactly_the_affected_cell(data: CheckoutExperimentData) -> None:
    merged = data.truth.merge(data.users, on="user_id").merge(data.assignments, on="user_id")
    old_android = (merged["platform"] == "android") & version_below(
        merged["app_version"].to_numpy(), "8.4"
    )
    assert (merged["in_affected_cell"] == old_android).all()

    hit = old_android & (merged["arm"] == "treatment")
    others = merged[~hit]
    assert (others["exposure_logged"] == others["rendered"]).all()
    target = merged[hit & merged["rendered"]]
    loss = 1 - target["exposure_logged"].mean()
    se = math.sqrt(0.35 * 0.65 / len(target))
    assert abs(loss - 0.35) < 4 * se
    assert data.manifest["realised"]["exposure_loss_rate_in_affected_treatment"] == pytest.approx(
        loss
    )
    assert not (merged["exposure_logged"] & ~merged["rendered"]).any()


def test_affects_arm_both_hits_both_arms() -> None:
    pop = simulate_population(_with_incident(affects_arm="both"), np.random.default_rng(3))
    for arm in (0, 1):
        mask = pop.in_affected_cell & pop.rendered & (pop.arm == arm)
        loss = 1 - pop.exposure_logged[mask].mean()
        assert abs(loss - 0.35) < 4 * math.sqrt(0.35 * 0.65 / mask.sum())


def test_affects_arm_control_spares_treatment() -> None:
    pop = simulate_population(_with_incident(affects_arm="control"), np.random.default_rng(4))
    treat = pop.arm == 1
    assert (pop.exposure_logged[treat] == pop.rendered[treat]).all()


@pytest.mark.parametrize("bad", ["Treatment", "neither", "", "1"])
def test_affects_arm_rejects_unknown_values(bad: str) -> None:
    with pytest.raises(ValueError, match="affects_arm"):
        simulate_population(_with_incident(affects_arm=bad), np.random.default_rng(0))


def test_affected_cell_converts_least_and_buys_least_before(data: CheckoutExperimentData) -> None:
    merged = data.truth.merge(data.users, on="user_id")
    in_cell, out_cell = merged["in_affected_cell"], ~merged["in_affected_cell"]
    assert merged.loc[in_cell, "p_control"].mean() < merged.loc[out_cell, "p_control"].mean()
    assert (
        merged.loc[in_cell, "pre_period_orders_28d"].mean()
        < merged.loc[out_cell, "pre_period_orders_28d"].mean()
    )


def _cell_gap(p) -> float:  # noqa: ANN001
    return p.p_control[~p.in_affected_cell].mean() - p.p_control[p.in_affected_cell].mean()


def test_affected_cell_offset_is_configurable() -> None:
    flat = simulate_population(
        {**SMALL_CONFIG, "affected_cell_logit_offset": 0.0}, np.random.default_rng(5)
    )
    deep = simulate_population(
        {**SMALL_CONFIG, "affected_cell_logit_offset": -0.6}, np.random.default_rng(5)
    )
    assert _cell_gap(deep) > _cell_gap(flat)


_DRAWN = (
    "arm",
    "pre_period_orders_28d",
    "assigned_at",
    "rendered",
    "loss_uniform",
    "order_delay_hours",
    "arrival_delay_hours",
)


@pytest.mark.parametrize(
    "change",
    [
        {"affected_cell_logit_offset": -0.6},
        {"true_relative_lift": 0.0},
        {"baseline_order_rate": 0.05},
        {"incident": {**SMALL_CONFIG["incident"], "exposure_loss_probability": 0.9}},
        {"incident": {**SMALL_CONFIG["incident"], "affects_arm": "both"}},
    ],
)
def test_common_random_numbers_across_parameters(change: dict) -> None:
    base = simulate_population(SMALL_CONFIG, np.random.default_rng(9))
    other = simulate_population({**SMALL_CONFIG, **change}, np.random.default_rng(9))
    for name in _DRAWN:
        assert np.array_equal(getattr(base, name), getattr(other, name)), name


def test_a_lift_change_moves_only_the_treatment_outcomes() -> None:
    base = simulate_population(SMALL_CONFIG, np.random.default_rng(9))
    other = simulate_population(
        {**SMALL_CONFIG, "true_relative_lift": 0.10}, np.random.default_rng(9)
    )
    for name in ("p_control", "y0", "exposure_logged"):
        assert np.array_equal(getattr(base, name), getattr(other, name)), name
    assert not np.array_equal(base.y1, other.y1)


def test_per_user_draws_survive_a_moved_affected_cell() -> None:
    # Moving the version threshold changes cell membership and the pre-period distribution
    # only for users whose membership changes; everyone else keeps every draw.
    base = simulate_population(SMALL_CONFIG, np.random.default_rng(9))
    other = simulate_population(_with_incident(app_version_below="8.2"), np.random.default_rng(9))
    for name in ("arm", "assigned_at", "rendered", "loss_uniform", "platform_idx"):
        assert np.array_equal(getattr(base, name), getattr(other, name)), name
    same_cell = base.in_affected_cell == other.in_affected_cell
    assert not same_cell.all()
    assert np.array_equal(
        base.pre_period_orders_28d[same_cell], other.pre_period_orders_28d[same_cell]
    )


def test_received_by_matches_the_order_tables(data: CheckoutExperimentData) -> None:
    pop = simulate_population(SMALL_CONFIG, np.random.default_rng(SMALL_CONFIG["seed"]))
    t0 = data.assignments["assigned_at"].min().floor("D")
    for hours in (24.0, 7 * 24.0, 10 * 24.0):
        clock = t0 + pd.Timedelta(hours=hours)
        expected = set(data.orders.loc[data.orders["received_at"] <= clock, "user_id"])
        assert set(pop.user_id[pop.received_by(hours)]) == expected


def test_logged_under_reproduces_the_configured_exposure() -> None:
    pop = simulate_population(SMALL_CONFIG, np.random.default_rng(2))
    assert np.array_equal(pop.logged_under(0.35, "treatment"), pop.exposure_logged)
    assert np.array_equal(pop.logged_under(0.0, "treatment"), pop.rendered)
    other = simulate_population(
        _with_incident(exposure_loss_probability=0.1, affects_arm="both"),
        np.random.default_rng(2),
    )
    assert np.array_equal(pop.logged_under(0.1, "both"), other.exposure_logged)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"days": 0}, "days"),
        ({"days": 1.5}, "days"),
        ({"eligible_users_per_day": -3}, "eligible_users_per_day"),
        ({"baseline_order_rate": 0.0}, "baseline_order_rate"),
        ({"baseline_order_rate": 1.0}, "baseline_order_rate"),
        ({"true_relative_lift": -1.0}, "true_relative_lift"),
        ({"true_relative_lift": 40.0}, "true_relative_lift"),
        ({"non_render_probability": 1.0}, "non_render_probability"),
        ({"non_render_probability": -0.1}, "non_render_probability"),
        ({"non_render_probabilty": 0.9}, "unknown config keys"),
        ({"true_relative_lift": float("nan")}, "true_relative_lift"),
        ({"true_relative_lift": True}, "true_relative_lift"),
        ({"days": "1.0x"}, "days"),
        ({"days": "14"}, "days"),
        ({"baseline_order_rate": "0.032"}, "baseline_order_rate"),
        ({"allocation": {"control": 0.6, "treatment": 0.4}}, "allocation"),
        ({"outcome_window_days": 0}, "outcome_window_days"),
        ({"order_pipeline": {"arrival_delay_hours": {"min": 5, "max": 1}}}, "arrival_delay"),
    ],
)
def test_invalid_config_values_are_rejected(change: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        simulate_population({**SMALL_CONFIG, **change}, np.random.default_rng(0))


@pytest.mark.parametrize(
    ("incident_change", "message"),
    [
        ({"platform": "web"}, "incident.platform"),
        ({"platform": "windows"}, "incident.platform"),
        ({"app_version_below": 8.1}, "app_version_below"),
        ({"app_version_below": "8.x"}, "app_version_below"),
        ({"exposure_loss_probability": 1.5}, "exposure_loss_probability"),
        ({"exposure_loss_probability": True}, "exposure_loss_probability"),
        ({"loss_probabilty": 0.2}, "unknown incident keys"),
    ],
)
def test_invalid_incident_values_are_rejected(incident_change: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        simulate_population(_with_incident(**incident_change), np.random.default_rng(0))


def test_pre_period_zero_share_matches_the_documented_mixture() -> None:
    pop = simulate_population(SMALL_CONFIG, np.random.default_rng(1))
    for in_cell, zi, mean in ((False, 0.70, 2.0), (True, 0.78, 1.6)):
        expected = zi + (1 - zi) * (0.8 / (0.8 + mean)) ** 0.8  # 0.81 outside, 0.87 inside
        mask = pop.in_affected_cell == in_cell
        zero = (pop.pre_period_orders_28d[mask] == 0).mean()
        assert abs(zero - expected) < 4 * math.sqrt(expected * (1 - expected) / mask.sum())


def test_truth_matches_the_configured_effect(data: CheckoutExperimentData) -> None:
    t = data.truth
    ratio = t["p_treatment"] / t["p_control"]
    assert np.allclose(ratio[t["rendered"]], 1.03)
    assert np.allclose(ratio[~t["rendered"]], 1.0)  # no widget, no effect
    assert t["p_control"].mean() == pytest.approx(0.032, abs=1e-9)  # calibrated intercept
    assert (t["y1"] >= t["y0"]).all()  # common random numbers, positive lift
    assert (t.loc[~t["rendered"], "y1"] == t.loc[~t["rendered"], "y0"]).all()
    m = data.manifest
    assert m["realised"]["sample_ate_absolute"] == pytest.approx(t["y1"].mean() - t["y0"].mean())
    assert m["expected_ate_absolute"] == pytest.approx((t["p_treatment"] - t["p_control"]).mean())
    assert m["expected_relative_lift_rendered"] == pytest.approx(0.03)
    # Dilution: the ITT effect is the rendered effect scaled by the rendered share of E[p0].
    r = t["rendered"]
    share = t.loc[r, "p_control"].sum() / t["p_control"].sum()
    assert m["expected_relative_lift_itt"] == pytest.approx(0.03 * share)


def test_orders_match_realised_outcomes(data: CheckoutExperimentData) -> None:
    merged = data.truth.merge(data.assignments, on="user_id")
    y = np.where(merged["arm"] == "treatment", merged["y1"], merged["y0"])
    assert len(data.orders) == int(y.sum())
    converters = set(merged.loc[y == 1, "user_id"])
    assert set(data.orders["user_id"]) == converters


def test_orders_within_window_and_delayed_non_differentially(data: CheckoutExperimentData) -> None:
    o = data.orders.merge(data.assignments, on="user_id")
    lag = (o["ordered_at"] - o["assigned_at"]).dt.total_seconds() / 3600
    assert (lag >= 0).all() and (lag < 7 * 24).all()
    arrival = (o["received_at"] - o["ordered_at"]).dt.total_seconds() / 3600
    assert (arrival >= 0).all() and (arrival <= 36).all()
    by_arm = arrival.groupby(o["arm"]).mean()
    assert abs(by_arm["treatment"] - by_arm["control"]) < 2.0  # uniform(0, 36): SD ~10.4 h


def test_refunds_within_window_at_configured_rate(data: CheckoutExperimentData) -> None:
    o = data.orders
    assert str(o["refunded_at"].dtype).startswith("datetime64")
    refunded = o["refunded_at"].notna()
    assert abs(refunded.mean() - 0.05) < 4 * math.sqrt(0.05 * 0.95 / len(o))
    lag_days = (o.loc[refunded, "refunded_at"] - o.loc[refunded, "ordered_at"]).dt.total_seconds()
    assert (lag_days >= 0).all() and (lag_days / 86_400 <= 14).all()


def test_every_eligible_user_has_an_attempt(data: CheckoutExperimentData) -> None:
    # Fallback users (no widget render) still attempt payment through the legacy checkout.
    assert set(data.checkout_attempts["user_id"]) == set(data.users["user_id"])


def test_non_rendered_users_can_order_without_an_effect(data: CheckoutExperimentData) -> None:
    t = data.truth
    assert (t.loc[~t["rendered"], "p_treatment"] == t.loc[~t["rendered"], "p_control"]).all()
    ordered = set(data.orders["user_id"])
    assert t.loc[~t["rendered"], "user_id"].isin(ordered).any()


def test_latency_missing_exactly_where_exposure_is_missing(data: CheckoutExperimentData) -> None:
    attempts = data.checkout_attempts.merge(
        data.truth[["user_id", "exposure_logged"]], on="user_id"
    )
    assert attempts.loc[attempts["exposure_logged"], "latency_ms"].notna().all()
    assert attempts.loc[~attempts["exposure_logged"], "latency_ms"].isna().all()


def test_telemetry_health_flags_only_the_affected_cell(data: CheckoutExperimentData) -> None:
    h = data.telemetry_health
    affected = (h["platform"] == "android") & version_below(h["app_version"].to_numpy(), "8.4")
    assert (h.loc[~affected, "event_loss_rate"] == 0).all()
    # Pooled over arms the affected cell loses ~0.35 * 0.5 = 17.5% of rendered events.
    assert 0.10 < h.loc[affected, "event_loss_rate"].mean() < 0.25


@pytest.mark.slow
def test_population_path_is_fast_enough_for_operating_characteristics() -> None:
    import time

    cfg = {**SMALL_CONFIG, "days": 14, "eligible_users_per_day": 20_000}
    rng = np.random.default_rng(0)
    simulate_population(cfg, rng)  # warm-up
    start = time.perf_counter()
    pop = simulate_population(cfg, rng)
    elapsed = time.perf_counter() - start
    assert pop.n == 280_000
    assert elapsed < 1.0  # generous ceiling for a laptop; the OC budget assumes ~0.1 s


def test_write_case_data_round_trips(tmp_path) -> None:
    data = simulate_checkout_experiment({**SMALL_CONFIG, "days": 2, "eligible_users_per_day": 500})
    manifest = write_case_data(data, tmp_path / "gen", tmp_path / "manifests" / "case01.json")
    assert set(manifest["content_sha256"]) == set(data.tables())
    for name in ("users", "orders"):
        frame = pd.read_parquet(tmp_path / "gen" / f"{name}.parquet")
        pd.testing.assert_frame_equal(frame, data.tables()[name])
        assert content_sha256(frame) == manifest["content_sha256"][name]
    assert (tmp_path / "manifests" / "case01.json").exists()
