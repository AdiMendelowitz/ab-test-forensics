"""Unit tests for jackknife_variance and cluster_bootstrap (spec case-02.md, sections 11 and 14)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from abforensics.inference import RatioOfSums, cluster_bootstrap, jackknife_variance

EST = "impression-weighted click-through rate, ratio of expected totals"
CTR = RatioOfSums("clicks", "inviews")


def _events(n_users: int, seed: int, rows_per_user: int = 3) -> pd.DataFrame:
    """Impression-grain frame with heterogeneous user propensities and heavy-tailed volume."""
    rng = np.random.default_rng(seed)
    inviews = rng.negative_binomial(2, 0.05, size=(n_users, rows_per_user)) + 1
    p = 1 / (1 + np.exp(-rng.normal(-2.3, 0.6, size=n_users)))
    clicks = rng.binomial(inviews, p[:, None])
    return pd.DataFrame({
        "draw_id": np.repeat(np.arange(n_users), rows_per_user),
        "inviews": inviews.ravel(),
        "clicks": clicks.ravel(),
    })


def _brute_force_jackknife(frame: pd.DataFrame, unit_col: str) -> float:
    units = frame[unit_col].unique()
    thetas = []
    for u in units:
        rest = frame[frame[unit_col] != u]
        thetas.append(rest["clicks"].sum() / rest["inviews"].sum())
    thetas = np.asarray(thetas)
    n = len(units)
    return float((n - 1) / n * np.sum((thetas - thetas.mean()) ** 2))


def test_closed_form_jackknife_equals_general_delete_one() -> None:
    frame = _events(12, seed=1)
    r = jackknife_variance(frame, "draw_id", CTR, estimand=EST)
    assert r.n_units == 12
    assert r.estimate == pytest.approx(frame["clicks"].sum() / frame["inviews"].sum())
    assert r.variance == pytest.approx(_brute_force_jackknife(frame, "draw_id"), rel=1e-10)


def test_jackknife_of_a_mean_is_s2_over_n() -> None:
    rng = np.random.default_rng(2)
    y = rng.normal(size=50)
    frame = pd.DataFrame({"draw_id": np.arange(50), "y": y, "one": 1.0})
    r = jackknife_variance(frame, "draw_id", RatioOfSums("y", "one"), estimand="mean of y")
    assert r.variance == pytest.approx(y.var(ddof=1) / 50, rel=1e-10)


def test_delta_method_matches_jackknife_to_first_order() -> None:
    frame = _events(5_000, seed=3)
    users = frame.groupby("draw_id")[["clicks", "inviews"]].sum()
    c, m = users["clicks"].to_numpy(float), users["inviews"].to_numpy(float)
    n = len(users)
    r_hat = c.sum() / m.sum()
    cov = np.cov(c, m, ddof=1)
    delta_var = (cov[0, 0] - 2 * r_hat * cov[0, 1] + r_hat**2 * cov[1, 1]) / (n * m.mean() ** 2)
    jk = jackknife_variance(frame, "draw_id", CTR, estimand=EST)
    assert jk.variance == pytest.approx(delta_var, rel=0.02)


def test_duplicated_draws_are_separate_units() -> None:
    frame = _events(5, seed=4, rows_per_user=1)
    dup = pd.concat([frame, frame.assign(draw_id=frame["draw_id"] + 5)], ignore_index=True)
    r = jackknife_variance(dup, "draw_id", CTR, estimand=EST)
    assert r.n_units == 10


def test_bootstrap_is_reproducible_and_records_its_budget() -> None:
    frame = _events(300, seed=5)
    a = cluster_bootstrap(frame, "draw_id", CTR, resamples=200, seed=11, estimand=EST, interval="normal")
    b = cluster_bootstrap(frame, "draw_id", CTR, resamples=200, seed=11, estimand=EST, interval="normal")
    assert a.standard_error == b.standard_error
    assert (a.resamples, a.seed, a.interval) == (200, 11, "normal")
    assert a.lower < a.estimate < a.upper
    assert a.upper - a.estimate == pytest.approx(a.estimate - a.lower)


def test_bootstrap_se_agrees_with_jackknife() -> None:
    frame = _events(2_000, seed=6)
    boot = cluster_bootstrap(frame, "draw_id", CTR, resamples=1_000, seed=7, estimand=EST)
    jk = jackknife_variance(frame, "draw_id", CTR, estimand=EST)
    assert boot.standard_error == pytest.approx(jk.standard_error, rel=0.10)
    assert boot.lower < boot.estimate < boot.upper
    assert not boot.warnings


def test_percentile_interval_below_1000_resamples_warns() -> None:
    frame = _events(100, seed=8)
    r = cluster_bootstrap(frame, "draw_id", CTR, resamples=200, seed=1, estimand=EST)
    assert any("1,000" in w for w in r.warnings)


def test_stratified_bootstrap_keeps_stratum_sizes() -> None:
    frame = _events(30, seed=9)
    frame["arm"] = np.where(frame["draw_id"] < 10, "treatment", "control")
    frame["t_units"] = np.where(frame["arm"] == "treatment", 1.0 / 3, 0.0)  # 3 rows per draw
    frame["one"] = 1.0 / 3  # each draw totals 1 on both columns
    r = cluster_bootstrap(
        frame, "draw_id", RatioOfSums("t_units", "one"), resamples=100, seed=2,
        estimand="share of draws in treatment", strata_col="arm",
    )
    assert np.allclose(r.distribution, 10 / 30)


def test_input_errors() -> None:
    frame = _events(10, seed=10)
    with pytest.raises(ValueError, match="unit column"):
        jackknife_variance(frame, "user_id", CTR, estimand=EST)
    with pytest.raises(ValueError, match="two units"):
        jackknife_variance(frame[frame["draw_id"] == 0], "draw_id", CTR, estimand=EST)
    with pytest.raises(ValueError, match="resamples"):
        cluster_bootstrap(frame, "draw_id", CTR, resamples=1, seed=0, estimand=EST)
    with pytest.raises(ValueError, match="alpha"):
        cluster_bootstrap(frame, "draw_id", CTR, resamples=10, seed=0, estimand=EST, alpha=1.5)
    with pytest.raises(ValueError, match="interval"):
        cluster_bootstrap(frame, "draw_id", CTR, resamples=10, seed=0, estimand=EST, interval="bca")
