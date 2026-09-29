"""Case 01 data-generating process: a checkout experiment with an exposure-logging incident.

Everything here is simulated. The generator produces the six long-format tables described
in docs/design/case-01.md, section 6, a ``truth`` table of per-user potential outcomes
written for audit (no analysis reads it; the operating-characteristic runner computes the
same quantities in memory from ``simulate_population``), and a manifest recording the seed,
every parameter and the realised row counts.

Three layers sit between assignment and the analysed population:

* Rendering. Every eligible user requests the checkout page. For a fixed share of them
  (``non_render_probability``), independently of arm and covariates, the page falls back to
  the server-rendered legacy checkout and neither widget renders. They can still load the
  payment step and order through the fallback, but they cannot be affected by the widget,
  so their treatment potential outcome equals their control one. The exposed population is
  therefore a genuine subset of the assigned one with its own valid, exposure-conditioned
  estimand, and the intent-to-treat effect is the rendered effect diluted by the render rate.
* The incident (section 7 of the spec). Among rendered users on the affected platform below
  the version threshold, the analytics emitter loses the render event with a fixed
  probability in the arm(s) named by ``affects_arm``. The loss is drawn at random within the
  cell, independently of the user's outcome and latency. Assignment is unaffected and only
  the logged exposure changes.
* The latency guardrail is recorded by the widget's own timing event, so it is missing
  wherever the render event is missing, and for fallback users.

Server-side renders are known to the platform only as aggregate counts per platform, app
version and day (the ``telemetry_health`` table); no user-level server-side render log
exists, so an analyst cannot re-trigger on server renders.

Random streams. ``simulate_population`` spawns one independent child stream per block
(covariates, arrival times, assignment salt, rendering, outcomes, render-event loss, order
timing), and every per-user quantity is a deterministic function of a fixed number of
uniforms per user (inverse-CDF sampling). For a fixed number of users, changing a parameter
therefore changes only the quantities that depend on it, user by user; every other draw is
identical, so two configs run with the same seed share common random numbers. Changing
``days`` or ``eligible_users_per_day`` changes the number of users and redraws every block
except the assignment hash. The event-level details added by ``simulate_checkout_experiment``
(payment-step loads, latencies, amounts, refunds) come from the parent generator and do not
share common random numbers across configs; no operating characteristic depends on them.

Two paths share one core:

* ``simulate_population`` returns NumPy arrays and is fast enough to call thousands of
  times for operating characteristics;
* ``simulate_checkout_experiment`` wraps it to produce the event tables and the manifest.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from scipy.optimize import brentq
from scipy.special import expit, logit

from abforensics import __version__

PLATFORMS = ("ios", "android", "web")
PLATFORM_SHARES = (0.40, 0.40, 0.20)
APP_VERSIONS = ("8.1", "8.2", "8.3", "8.4", "8.5", "8.6")
APP_VERSION_SHARES = (0.04, 0.08, 0.13, 0.20, 0.25, 0.30)
COUNTRIES = ("DE", "DK", "NL", "GB", "ES")
COUNTRY_SHARES = (0.30, 0.10, 0.20, 0.25, 0.15)
WEEKDAY_WEIGHTS = (1.00, 1.05, 1.05, 1.10, 1.15, 0.85, 0.80)  # Monday first

# Conversion model on the logit scale. Platform and app-version effects are fixed so that
# the incident's affected cell (Android, old versions) is the lowest-converting one, which
# is what makes the exposure-conditioned estimate biased upwards. The affected-cell offset
# is a default: config key ``affected_cell_logit_offset`` overrides it for the sweep.
PLATFORM_LOGIT = {"ios": 0.30, "android": -0.30, "web": 0.0}
OLD_ANDROID_LOGIT = -0.30
PRE_ORDERS_LOGIT_PER_LOG1P = 0.25
# Pre-period orders: zero-inflated negative binomial. The structural-zero share below comes
# on top of the negative binomial's own zeros, P(NB = 0) = (0.8 / 2.8) ** 0.8 = 0.37, so the
# share of users with no pre-period order is 0.81 outside the affected cell and 0.87 inside.
ZERO_INFLATION = 0.70
NB_MEAN, NB_DISPERSION = 2.0, 0.8
# Users who have not updated the app are less engaged: more zeros, fewer orders when non-zero.
OLD_ANDROID_ZERO_INFLATION = 0.78
OLD_ANDROID_NB_MEAN = 1.6

NON_RENDER_PROBABILITY = 0.15  # default share of eligible users whose widget never renders
AFFECTS_ARM_VALUES = ("treatment", "control", "both")

PAYMENT_STEP_ERROR_RATE = 0.04  # per load of the payment step, identical by arm
EXTRA_LOADS_POISSON = 0.30  # payment-step loads per user: 1 + Poisson(0.30)
LATENCY_LOG_MEAN, LATENCY_LOG_SD = np.log(1200.0), 0.35  # ms
AMOUNT_LOG_MEAN, AMOUNT_LOG_SD = np.log(48.0), 0.60  # currency units
ORDER_DELAY_MEAN_HOURS = 20.0  # eligibility to order: exponential, truncated to the window
REFUND_PROBABILITY = 0.05  # per order, identical by arm
REFUND_WINDOW_DAYS = 14  # refunds land uniformly within this many days of the order

HASH_SALT = 0x9E3779B97F4A7C15  # default for ``hash_assign`` when called directly

# Keys a case config may carry; anything else is rejected so that a misspelt optional key
# cannot silently fall back to its default.
CONFIG_KEYS = frozenset(
    {
        "seed",
        "days",
        "eligible_users_per_day",
        "allocation",
        "baseline_order_rate",
        "true_relative_lift",
        "non_render_probability",
        "affected_cell_logit_offset",
        "outcome_window_days",
        "decision_rule",
        "incident",
        "order_pipeline",
        "operating_characteristics",
    }
)
INCIDENT_KEYS = frozenset(
    {"platform", "app_version_below", "exposure_loss_probability", "affects_arm"}
)

_PLATFORM_LOGIT_BY_INDEX = np.asarray([PLATFORM_LOGIT[p] for p in PLATFORMS])
_APP_VERSION_LABELS = np.asarray((*APP_VERSIONS, "web"))
_WEB_INDEX = PLATFORMS.index("web")
_VERSION_PATTERN = re.compile(r"^\d+(\.\d+)*$")
_STREAMS = ("covariates", "arrival", "salt", "render", "outcome", "loss", "timing")


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in str(v).split("."))


def version_below(versions: np.ndarray, threshold: str) -> np.ndarray:
    """Elementwise ``version < threshold`` for dotted version strings."""
    thr = version_tuple(threshold)
    return np.asarray([version_tuple(v) < thr if v != "web" else False for v in versions])


def hash_assign(user_ids: np.ndarray, salt: int = HASH_SALT) -> np.ndarray:
    """Deterministic 50/50 assignment from a 64-bit integer mix of the user id and a salt.

    Models hash-based bucketing: for a given salt the same user always lands in the same
    arm, and the split is balanced in expectation without any coin being flipped at request
    time. A new salt is a new experiment's bucketing. Returns 1 for treatment, 0 for control.
    """
    x = (user_ids.astype(np.uint64) + np.uint64(salt)) & np.uint64(0xFFFFFFFFFFFFFFFF)
    x ^= x >> np.uint64(30)
    x = (x * np.uint64(0xBF58476D1CE4E5B9)) & np.uint64(0xFFFFFFFFFFFFFFFF)
    x ^= x >> np.uint64(27)
    x = (x * np.uint64(0x94D049BB133111EB)) & np.uint64(0xFFFFFFFFFFFFFFFF)
    x ^= x >> np.uint64(31)
    return (x & np.uint64(1)).astype(np.int8)


def affected_arms(value: str) -> tuple[int, ...]:
    """Map the config's ``affects_arm`` to arm codes, rejecting anything unrecognised."""
    mapping = {"treatment": (1,), "control": (0,), "both": (0, 1)}
    if value not in mapping:
        raise ValueError(f"incident.affects_arm must be one of {AFFECTS_ARM_VALUES}; got {value!r}")
    return mapping[value]


@dataclass(frozen=True)
class DGPParameters:
    """The validated parameters ``simulate_population`` reads from a config."""

    days: int
    per_day: int
    baseline: float
    lift: float
    non_render_probability: float
    affected_cell_logit_offset: float
    loss_probability: float
    affected_platform: str
    version_threshold: str
    affects_arm: str
    outcome_window_hours: float
    arrival_delay_min_hours: float
    arrival_delay_max_hours: float


def _as_number(name: str, value: Any) -> float:
    if isinstance(value, bool | str) or not isinstance(value, int | float | np.number):
        raise ValueError(f"{name} must be a number; got {type(value).__name__} {value!r}")
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"{name} must be finite; got {value!r}")
    return v


def _as_count(name: str, value: Any) -> int:
    v = _as_number(name, value)
    if not v.is_integer() or v < 1:
        raise ValueError(f"{name} must be a positive integer; got {value!r}")
    return int(v)


def _as_probability(name: str, value: Any, *, allow_one: bool = True) -> float:
    v = _as_number(name, value)
    upper_ok = v <= 1.0 if allow_one else v < 1.0
    if not (v >= 0.0 and upper_ok):
        bound = "[0, 1]" if allow_one else "[0, 1)"
        raise ValueError(f"{name} must be in {bound}; got {value!r}")
    return v


def validate_config(config: Mapping[str, Any]) -> DGPParameters:
    """Parse and validate the keys the generator reads; raise ``ValueError`` naming the key.

    Unknown top-level or incident keys are rejected. Defaults: ``non_render_probability``
    0.15, ``affected_cell_logit_offset`` -0.30.
    """
    unknown = set(config) - CONFIG_KEYS
    if unknown:
        raise ValueError(f"unknown config keys {sorted(unknown)}; known {sorted(CONFIG_KEYS)}")
    allocation = config.get("allocation", {"control": 0.5, "treatment": 0.5})
    if dict(allocation) != {"control": 0.5, "treatment": 0.5}:
        raise ValueError(
            f"allocation must be {{control: 0.5, treatment: 0.5}}: the hash assignment is 50/50; "
            f"got {allocation!r}"
        )
    incident = config["incident"]
    unknown = set(incident) - INCIDENT_KEYS
    if unknown:
        raise ValueError(f"unknown incident keys {sorted(unknown)}; known {sorted(INCIDENT_KEYS)}")
    baseline = _as_number("baseline_order_rate", config["baseline_order_rate"])
    if not 0.0 < baseline < 1.0:
        raise ValueError(f"baseline_order_rate must be in (0, 1); got {baseline!r}")
    lift = _as_number("true_relative_lift", config["true_relative_lift"])
    if lift <= -1.0:
        raise ValueError(f"true_relative_lift must be above -1; got {lift!r}")
    platform = incident["platform"]
    if platform not in PLATFORMS or platform == "web":
        raise ValueError(
            f"incident.platform must be one of {PLATFORMS[:_WEB_INDEX]} (web has no app "
            f"version); got {platform!r}"
        )
    threshold = incident["app_version_below"]
    if not isinstance(threshold, str) or not _VERSION_PATTERN.match(threshold):
        raise ValueError(
            "incident.app_version_below must be a quoted dotted version string such as "
            f'"8.4" (an unquoted 8.10 would parse as the number 8.1); got {threshold!r}'
        )
    affects = str(incident["affects_arm"])
    affected_arms(affects)
    window = _as_number("outcome_window_days", config["outcome_window_days"])
    if window <= 0:
        raise ValueError(f"outcome_window_days must be positive; got {window!r}")
    delay = config["order_pipeline"]["arrival_delay_hours"]
    d_min = _as_number("order_pipeline.arrival_delay_hours.min", delay["min"])
    d_max = _as_number("order_pipeline.arrival_delay_hours.max", delay["max"])
    if not 0.0 <= d_min <= d_max:
        raise ValueError(f"order_pipeline.arrival_delay_hours needs 0 <= min <= max; got {delay}")
    return DGPParameters(
        days=_as_count("days", config["days"]),
        per_day=_as_count("eligible_users_per_day", config["eligible_users_per_day"]),
        baseline=baseline,
        lift=lift,
        non_render_probability=_as_probability(
            "non_render_probability",
            config.get("non_render_probability", NON_RENDER_PROBABILITY),
            allow_one=False,
        ),
        affected_cell_logit_offset=_as_number(
            "affected_cell_logit_offset",
            config.get("affected_cell_logit_offset", OLD_ANDROID_LOGIT),
        ),
        loss_probability=_as_probability(
            "incident.exposure_loss_probability", incident["exposure_loss_probability"]
        ),
        affected_platform=platform,
        version_threshold=threshold,
        affects_arm=affects,
        outcome_window_hours=window * 24.0,
        arrival_delay_min_hours=d_min,
        arrival_delay_max_hours=d_max,
    )


@dataclass(frozen=True)
class Population:
    """Arrays for one simulated experiment. All arrays are aligned on user index."""

    user_id: np.ndarray
    platform_idx: np.ndarray  # index into PLATFORMS
    version_idx: np.ndarray  # index into APP_VERSIONS, len(APP_VERSIONS) for web
    country_idx: np.ndarray  # index into COUNTRIES
    pre_period_orders_28d: np.ndarray
    assigned_at: np.ndarray  # hours since experiment start
    arm: np.ndarray  # 1 treatment, 0 control
    p_control: np.ndarray
    p_treatment: np.ndarray  # equals p_control for users served the fallback
    y0: np.ndarray
    y1: np.ndarray
    rendered: np.ndarray  # bool: the widget rendered (arm-independent)
    loss_uniform: np.ndarray  # uniform draw deciding render-event loss, per user
    exposure_logged: np.ndarray  # bool: rendered and the render event survived the incident
    in_affected_cell: np.ndarray  # bool: android below threshold (both arms)
    order_delay_hours: np.ndarray  # eligibility to order, if the user orders
    arrival_delay_hours: np.ndarray  # order to receipt by the batch pipeline
    realised_control_rate: float
    extras: Mapping[str, Any] = field(default_factory=dict)

    @property
    def platform(self) -> np.ndarray:
        return np.asarray(PLATFORMS)[self.platform_idx]

    @property
    def app_version(self) -> np.ndarray:
        return _APP_VERSION_LABELS[self.version_idx]

    @property
    def country(self) -> np.ndarray:
        return np.asarray(COUNTRIES)[self.country_idx]

    @property
    def y(self) -> np.ndarray:
        return np.where(self.arm == 1, self.y1, self.y0)

    @property
    def n(self) -> int:
        return int(self.user_id.size)

    def received_by(self, hours: float) -> np.ndarray:
        """Converters whose order the pipeline has delivered by ``hours`` after the start."""
        received = self.assigned_at + self.order_delay_hours + self.arrival_delay_hours
        return (self.y == 1) & (received <= hours)

    def logged_under(self, loss_probability: float, affects_arm: str) -> np.ndarray:
        """Exposure mask the same population would have under another incident setting.

        Uses the population's own loss draws, so every setting is evaluated on the same
        users, assignment and outcomes (common random numbers across incident settings).
        """
        p = _as_probability("loss_probability", loss_probability)
        in_hit_arm = np.isin(self.arm, affected_arms(affects_arm))
        lost = self.rendered & self.in_affected_cell & in_hit_arm & (self.loss_uniform < p)
        return self.rendered & ~lost

    def expected_ate_absolute(self, mask: np.ndarray | None = None) -> float:
        """Mean of p_treatment - p_control over the population (or a boolean subset)."""
        d = self.p_treatment - self.p_control
        return float(d.mean() if mask is None else d[mask].mean())

    def expected_relative_lift(self, mask: np.ndarray | None = None) -> float:
        """E[p_treatment] / E[p_control] - 1 over the population (or a boolean subset)."""
        if mask is None:
            pt, pc = self.p_treatment, self.p_control
        else:
            pt, pc = self.p_treatment[mask], self.p_control[mask]
        return float(pt.mean() / pc.mean() - 1.0)


def _calibrate_intercept(
    linear_values: np.ndarray, weights: np.ndarray, target_rate: float
) -> float:
    """Intercept b such that the weighted mean of expit(b + x) equals target_rate."""
    w = weights / weights.sum()

    def gap(b: float) -> float:
        return float(np.dot(w, expit(b + linear_values))) - target_rate

    return brentq(gap, -40.0, 40.0, xtol=1e-12)


def _categorical(u: np.ndarray, shares: tuple[float, ...]) -> np.ndarray:
    """Inverse-CDF draw of category indices from one uniform per user."""
    cdf = np.cumsum(shares)
    cdf[-1] = 1.0
    return np.searchsorted(cdf, u, side="right")


def _zinb_cdf(zero_inflation: float, mean: float, dispersion: float) -> np.ndarray:
    """CDF of the zero-inflated negative binomial, tabulated to the 1 - 1e-13 quantile of the
    negative binomial; the remaining tail mass (below 1e-13) is lumped into the last value."""
    nb = stats.nbinom(dispersion, dispersion / (dispersion + mean))
    upper = int(nb.ppf(1.0 - 1e-13)) + 1
    k = np.arange(upper + 1)
    pmf = (1.0 - zero_inflation) * nb.pmf(k)
    pmf[0] += zero_inflation
    cdf = np.cumsum(pmf)
    cdf[-1] = 1.0
    return cdf


_PRE_ORDERS_CDF = {
    False: _zinb_cdf(ZERO_INFLATION, NB_MEAN, NB_DISPERSION),
    True: _zinb_cdf(OLD_ANDROID_ZERO_INFLATION, OLD_ANDROID_NB_MEAN, NB_DISPERSION),
}


def simulate_population(config: Mapping[str, Any], rng: np.random.Generator) -> Population:
    """Draw one experiment's population, potential outcomes, incident flags and order timing.

    ``config`` is the parsed ``config.yaml`` of the case; ``validate_config`` lists the keys
    it reads and rejects invalid values. See the module docstring for the random streams.
    """
    prm = validate_config(config)
    n = prm.days * prm.per_day
    # Child streams come from a fresh SeedSequence built from draws of ``rng``: the result
    # depends only on the generator's state and is independent of how often its own
    # SeedSequence has been spawned before (``Generator.spawn`` mutates that counter).
    # Passing the same generator twice gives two different populations.
    root = np.random.SeedSequence(rng.integers(0, 2**63 - 1, size=4).tolist())
    streams = {
        name: np.random.default_rng(child)
        for name, child in zip(_STREAMS, root.spawn(len(_STREAMS)), strict=True)
    }

    user_id = np.arange(1, n + 1, dtype=np.int64)
    cov = streams["covariates"].random((4, n))
    platform_idx = _categorical(cov[0], PLATFORM_SHARES)
    version_idx = _categorical(cov[1], APP_VERSION_SHARES)
    version_idx = np.where(platform_idx == _WEB_INDEX, len(APP_VERSIONS), version_idx)
    country_idx = _categorical(cov[2], COUNTRY_SHARES)

    below = np.append(version_below(np.asarray(APP_VERSIONS), prm.version_threshold), False)
    old_android = (platform_idx == PLATFORMS.index(prm.affected_platform)) & below[version_idx]
    pre_orders = np.where(
        old_android,
        np.searchsorted(_PRE_ORDERS_CDF[True], cov[3], side="right"),
        np.searchsorted(_PRE_ORDERS_CDF[False], cov[3], side="right"),
    ).astype(np.int64)

    # Arrival time: day drawn with weekday weights, hour uniform within the day.
    arrival = streams["arrival"].random((2, n))
    day_weights = np.asarray([WEEKDAY_WEIGHTS[d % 7] for d in range(prm.days)], dtype=float)
    day = _categorical(arrival[0], tuple(day_weights / day_weights.sum()))
    assigned_at = day * 24.0 + arrival[1] * 24.0

    salt = int(streams["salt"].integers(0, 2**63 - 1))
    arm = hash_assign(user_id, salt)

    # The linear predictor takes few distinct values (platform x cell x pre-period orders),
    # so the intercept is calibrated on a lookup table of those values weighted by their
    # counts rather than on n users.
    stride = int(pre_orders.max()) + 1
    key = (platform_idx * 2 + old_android.astype(np.int64)) * stride + pre_orders
    counts = np.bincount(key, minlength=len(PLATFORMS) * 2 * stride).astype(float)
    table_key = np.arange(counts.size)
    linear_table = (
        _PLATFORM_LOGIT_BY_INDEX[table_key // stride // 2]
        + prm.affected_cell_logit_offset * ((table_key // stride) % 2)
        + PRE_ORDERS_LOGIT_PER_LOG1P * np.log1p(table_key % stride)
    )
    used = counts > 0
    intercept = _calibrate_intercept(linear_table[used], counts[used], prm.baseline)
    p_control = expit(intercept + linear_table)[key]
    if float(p_control.max()) * (1.0 + prm.lift) > 1.0:
        raise ValueError(
            f"true_relative_lift {prm.lift!r} would push a treatment probability above 1 "
            f"(max control probability {float(p_control.max()):.4f}); the realised effect "
            "would differ from the configured one"
        )

    rendered = streams["render"].random(n) >= prm.non_render_probability
    p_treatment = np.where(rendered, p_control * (1.0 + prm.lift), p_control)

    # Common random numbers: the same uniform decides both potential outcomes, so a
    # positive lift can only turn a 0 into a 1, never the reverse.
    u = streams["outcome"].random(n)
    y0 = (u < p_control).astype(np.int8)
    y1 = (u < p_treatment).astype(np.int8)

    loss_uniform = streams["loss"].random(n)
    in_hit_arm = np.isin(arm, affected_arms(prm.affects_arm))
    lost = rendered & old_android & in_hit_arm & (loss_uniform < prm.loss_probability)
    exposure_logged = rendered & ~lost

    # Order timing, drawn for every user (used only where the user orders): time from
    # eligibility to order, exponential truncated to the outcome window, and the batch
    # pipeline's delivery delay, uniform, identical in both arms.
    timing = streams["timing"].random((2, n))
    order_delay = np.minimum(
        -ORDER_DELAY_MEAN_HOURS * np.log1p(-timing[0]), prm.outcome_window_hours - 1e-6
    )
    arrival_delay = prm.arrival_delay_min_hours + timing[1] * (
        prm.arrival_delay_max_hours - prm.arrival_delay_min_hours
    )

    return Population(
        user_id=user_id,
        platform_idx=platform_idx,
        version_idx=version_idx,
        country_idx=country_idx,
        pre_period_orders_28d=pre_orders.astype(np.int32),
        assigned_at=assigned_at,
        arm=arm,
        p_control=p_control,
        p_treatment=p_treatment,
        y0=y0,
        y1=y1,
        rendered=rendered,
        loss_uniform=loss_uniform,
        exposure_logged=exposure_logged,
        in_affected_cell=old_android,
        order_delay_hours=order_delay,
        arrival_delay_hours=arrival_delay,
        realised_control_rate=float(p_control.mean()),
        extras={
            "intercept": float(intercept),
            "logit_baseline": float(logit(prm.baseline)),
            "hash_salt": salt,
            "non_render_probability": prm.non_render_probability,
            "affected_cell_logit_offset": prm.affected_cell_logit_offset,
        },
    )


@dataclass(frozen=True)
class CheckoutExperimentData:
    users: pd.DataFrame
    assignments: pd.DataFrame
    exposures: pd.DataFrame
    checkout_attempts: pd.DataFrame
    orders: pd.DataFrame
    telemetry_health: pd.DataFrame
    truth: pd.DataFrame
    manifest: dict[str, Any]

    def tables(self) -> dict[str, pd.DataFrame]:
        return {
            "users": self.users,
            "assignments": self.assignments,
            "exposures": self.exposures,
            "checkout_attempts": self.checkout_attempts,
            "orders": self.orders,
            "telemetry_health": self.telemetry_health,
            "truth": self.truth,
        }


def simulate_checkout_experiment(
    config: Mapping[str, Any], seed: int | None = None
) -> CheckoutExperimentData:
    """Generate the Case 01 event tables from ``config`` (the case's config.yaml).

    The experiment clock starts at ``2026-03-02T00:00:00`` (a Monday) so that weekday
    effects line up with calendar dates in the tables. Timestamps are UTC.
    """
    seed = int(config["seed"] if seed is None else seed)
    rng = np.random.default_rng(seed)
    pop = simulate_population(config, rng)
    n = pop.n
    t0 = pd.Timestamp("2026-03-02T00:00:00Z")
    hours = pd.to_timedelta(pop.assigned_at, unit="h")
    assigned_ts = t0 + hours
    arm_label = np.where(pop.arm == 1, "treatment", "control")
    platform, app_version = pop.platform, pop.app_version

    users = pd.DataFrame(
        {
            "user_id": pop.user_id,
            "platform": platform,
            "app_version": app_version,
            "country": pop.country,
            "pre_period_orders_28d": pop.pre_period_orders_28d,
        }
    )
    assignments = pd.DataFrame(
        {"user_id": pop.user_id, "arm": arm_label, "assigned_at": assigned_ts}
    )

    exposed = pop.exposure_logged
    exposures = pd.DataFrame(
        {
            "user_id": pop.user_id[exposed],
            "exposed_at": assigned_ts[exposed]
            + pd.to_timedelta(rng.random(exposed.sum()) * 5, "s"),
            "widget_version": np.where(pop.arm[exposed] == 1, "v2", "v1"),
            "event_id": np.arange(1, exposed.sum() + 1, dtype=np.int64),
        }
    )

    # Payment-step loads: one or more per eligible user (every eligible user reaches the
    # checkout page), through the widget where it rendered and through the legacy fallback
    # where it did not. ``payment_step_error`` marks a load on which the payment step raised
    # a client error; a load is not a payment, so most loads end without an order. Latency
    # comes from the widget's timing event, so it exists only where the render event was
    # logged.
    n_loads = 1 + rng.poisson(EXTRA_LOADS_POISSON, size=n)
    load_user = np.repeat(pop.user_id, n_loads)
    load_idx = np.repeat(np.arange(n), n_loads)
    total_loads = int(n_loads.sum())
    load_offset_min = rng.random(total_loads) * 30.0
    latency = rng.lognormal(LATENCY_LOG_MEAN, LATENCY_LOG_SD, size=total_loads)
    latency = np.where(pop.exposure_logged[load_idx], latency, np.nan)
    checkout_attempts = pd.DataFrame(
        {
            "user_id": load_user,
            "attempted_at": assigned_ts[load_idx] + pd.to_timedelta(load_offset_min, "m"),
            "payment_step_error": rng.random(total_loads) < PAYMENT_STEP_ERROR_RATE,
            "latency_ms": np.round(latency, 1),
        }
    )

    # Orders: converters place one order within the outcome window; the batch pipeline
    # delivers it 0 to 36 hours later, identically in both arms (timing drawn in
    # ``simulate_population``). A fixed share of orders is refunded within 14 days,
    # identically in both arms; refunded_at is NaT otherwise.
    y = pop.y.astype(bool)
    n_orders = int(y.sum())
    ordered_at = assigned_ts[y] + pd.to_timedelta(pop.order_delay_hours[y], "h")
    received_at = ordered_at + pd.to_timedelta(pop.arrival_delay_hours[y], "h")
    amount = np.round(rng.lognormal(AMOUNT_LOG_MEAN, AMOUNT_LOG_SD, size=n_orders), 2)
    refunded = rng.random(n_orders) < REFUND_PROBABILITY
    refund_lag_h = rng.random(n_orders) * REFUND_WINDOW_DAYS * 24.0
    refunded_at = (ordered_at + pd.to_timedelta(refund_lag_h, "h")).where(refunded)
    orders = pd.DataFrame(
        {
            "user_id": pop.user_id[y],
            "order_id": np.arange(1, n_orders + 1, dtype=np.int64),
            "ordered_at": ordered_at,
            "received_at": received_at,
            "amount": amount,
            "refunded_at": refunded_at,
        }
    )

    # Telemetry health: a monitoring table, arms pooled, as an observability system would
    # publish it by comparing server-side widget renders with analytics render events.
    # Event loss rate = 1 - logged render events / rendered widgets.
    day = (pop.assigned_at // 24).astype(int)
    r = pop.rendered
    health = (
        pd.DataFrame(
            {
                "platform": platform[r],
                "app_version": app_version[r],
                "day": t0.normalize() + pd.to_timedelta(day[r], "D"),
                "logged": pop.exposure_logged[r].astype(int),
            }
        )
        .groupby(["platform", "app_version", "day"], as_index=False)
        .agg(users=("logged", "size"), logged=("logged", "sum"))
    )
    health["event_loss_rate"] = 1.0 - health["logged"] / health["users"]
    telemetry_health = health[["platform", "app_version", "day", "users", "event_loss_rate"]]

    truth = pd.DataFrame(
        {
            "user_id": pop.user_id,
            "p_control": pop.p_control,
            "p_treatment": pop.p_treatment,
            "y0": pop.y0,
            "y1": pop.y1,
            "rendered": pop.rendered,
            "exposure_logged": pop.exposure_logged,
            "in_affected_cell": pop.in_affected_cell,
        }
    )

    manifest: dict[str, Any] = {
        "case": "case01",
        "abforensics_version": __version__,
        "seed": seed,
        "config": _jsonable(config),
        "true_relative_lift": float(config["true_relative_lift"]),
        "true_relative_lift_applies_to": "users whose widget renders",
        "expected_ate_absolute": pop.expected_ate_absolute(),
        "expected_ate_absolute_rendered": pop.expected_ate_absolute(pop.rendered),
        "expected_relative_lift_itt": pop.expected_relative_lift(),
        "expected_relative_lift_rendered": pop.expected_relative_lift(pop.rendered),
        "realised": {
            "n_users": n,
            "control_rate_expected": pop.realised_control_rate,
            "sample_ate_absolute": float(pop.y1.mean() - pop.y0.mean()),
            "assigned_share_treatment": float(pop.arm.mean()),
            "affected_cell_share": float(pop.in_affected_cell.mean()),
            "render_rate": float(pop.rendered.mean()),
            "exposure_loss_rate_in_affected_treatment": _loss_rate(pop),
            "calibrated_intercept": pop.extras["intercept"],
            "hash_salt": pop.extras["hash_salt"],
        },
        "row_counts": {
            "users": int(len(users)),
            "assignments": int(len(assignments)),
            "exposures": int(len(exposures)),
            "checkout_attempts": int(len(checkout_attempts)),
            "orders": int(len(orders)),
            "telemetry_health": int(len(telemetry_health)),
            "truth": int(len(truth)),
        },
    }
    return CheckoutExperimentData(
        users, assignments, exposures, checkout_attempts, orders, telemetry_health, truth, manifest
    )


def _loss_rate(pop: Population) -> float:
    """Share of rendered, affected-cell treatment users whose render event was lost."""
    mask = pop.in_affected_cell & (pop.arm == 1) & pop.rendered
    return float(1.0 - pop.exposure_logged[mask].mean()) if mask.any() else 0.0


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


def content_sha256(frame: pd.DataFrame) -> str:
    """SHA-256 of the table's row hashes: a checksum of table content, independent of the
    Parquet encoding.

    Parquet encodings differ across pyarrow versions and platforms, so a file checksum
    would fail to reproduce on another machine even when every value is identical.
    """
    row_hashes = pd.util.hash_pandas_object(frame, index=False).to_numpy()
    return hashlib.sha256(row_hashes.tobytes()).hexdigest()


def write_case_data(
    data: CheckoutExperimentData, generated_dir: Path, manifest_path: Path
) -> dict[str, Any]:
    """Write the tables as Parquet and the manifest as JSON with per-table content checksums."""
    generated_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    checksums: dict[str, str] = {}
    for name, frame in data.tables().items():
        frame.to_parquet(generated_dir / f"{name}.parquet", index=False)
        checksums[name] = content_sha256(frame)
    manifest = {
        **data.manifest,
        "files": {k: f"{k}.parquet" for k in checksums},
        "content_sha256": checksums,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    return manifest
