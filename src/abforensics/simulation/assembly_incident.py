"""Case 01 data-generating process: a checkout experiment with an exposure-logging incident.

Everything here is simulated. The generator produces the six long-format tables described
in the Case 01 README (Data provenance), a ``truth`` table of per-user potential outcomes
written for audit (no analysis reads it; the operating-characteristic runner computes the
same quantities in memory from ``simulate_population``), and a manifest recording the seed,
every parameter and the realised row counts.

Three layers sit between assignment and the analysed population:

* Rendering. Every eligible user requests the checkout page. For a fixed share of them
  (``legacy_route_probability``), independently of arm and covariates, the page falls back to
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
version and day (the ``scan_log_health`` table); no user-level server-side render log
exists, so an analyst cannot re-trigger on server renders.

Random streams. ``simulate_population`` spawns one independent child stream per block
(covariates, arrival times, assignment salt, rendering, outcomes, render-event loss, order
timing), and every per-user quantity is a deterministic function of a fixed number of
uniforms per user (inverse-CDF sampling). For a fixed number of users, changing a parameter
therefore changes only the quantities that depend on it, user by user; every other draw is
identical, so two configs run with the same seed share common random numbers. Changing
``days`` or ``eligible_products_per_day`` changes the number of users and redraws every block
except the assignment hash. The event-level details added by ``simulate_assembly_experiment``
(payment-step loads, latencies, amounts, refunds) come from the parent generator and do not
share common random numbers across configs; no operating characteristic depends on them.

Two paths share one core:

* ``simulate_population`` returns NumPy arrays and is fast enough to call thousands of
  times for operating characteristics;
* ``simulate_assembly_experiment`` wraps it to produce the event tables and the manifest.
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

SUPPLY_LINES = ("ios", "android", "web")
SUPPLY_LINE_SHARES = (0.40, 0.40, 0.20)
BATCHES = ("8.1", "8.2", "8.3", "8.4", "8.5", "8.6")
BATCH_SHARES = (0.04, 0.08, 0.13, 0.20, 0.25, 0.30)
COUNTRIES = ("DE", "DK", "NL", "GB", "ES")
COUNTRY_SHARES = (0.30, 0.10, 0.20, 0.25, 0.15)
WEEKDAY_WEIGHTS = (1.00, 1.05, 1.05, 1.10, 1.15, 0.85, 0.80)  # Monday first

# Conversion model on the logit scale. Platform and app-version effects are fixed so that
# the incident's affected cell (Android, old versions) is the lowest-converting one, which
# is what makes the exposure-conditioned estimate biased upwards. The affected-cell offset
# is a default: config key ``older_batch_logit_offset`` overrides it for the sweep.
SUPPLY_LINE_LOGIT = {"ios": 0.30, "android": -0.30, "web": 0.0}
OLDER_BATCH_LOGIT = -0.30
PRE_FINISHED_LOGIT_PER_LOG1P = 0.25
# Pre-period orders: zero-inflated negative binomial. The structural-zero share below comes
# on top of the negative binomial's own zeros, P(NB = 0) = (0.8 / 2.8) ** 0.8 = 0.37, so the
# share of users with no pre-period order is 0.81 outside the affected cell and 0.87 inside.
ZERO_INFLATION = 0.70
NB_MEAN, NB_DISPERSION = 2.0, 0.8
# Users who have not updated the app are less engaged: more zeros, fewer orders when non-zero.
OLDER_BATCH_ZERO_INFLATION = 0.78
OLDER_BATCH_NB_MEAN = 1.6

LEGACY_ROUTE_PROBABILITY = 0.15  # default share of eligible users whose widget never renders
AFFECTS_ARM_VALUES = ("treatment", "control", "both")

PACKING_STEP_ERROR_RATE = 0.04  # per load of the payment step, identical by arm
EXTRA_LOADS_POISSON = 0.30  # payment-step loads per user: 1 + Poisson(0.30)
CYCLE_TIME_LOG_MEAN, CYCLE_TIME_LOG_SD = np.log(1200.0), 0.35  # ms
AMOUNT_LOG_MEAN, AMOUNT_LOG_SD = np.log(48.0), 0.60  # currency units
RECORDS_DELAY_MEAN_HOURS = 20.0  # eligibility to order: exponential, truncated to the window
RETURN_PROBABILITY = 0.05  # per order, identical by arm
RETURN_WINDOW_DAYS = 14  # refunds land uniformly within this many days of the order

HASH_SALT = 0x9E3779B97F4A7C15  # default for ``hash_assign`` when called directly

# Keys a case config may carry; anything else is rejected so that a misspelt optional key
# cannot silently fall back to its default.
CONFIG_KEYS = frozenset(
    {
        "seed",
        "days",
        "eligible_products_per_day",
        "allocation",
        "baseline_yield_rate",
        "true_relative_lift",
        "legacy_route_probability",
        "older_batch_logit_offset",
        "outcome_window_days",
        "decision_rule",
        "incident",
        "records_pipeline",
        "operating_characteristics",
    }
)
INCIDENT_KEYS = frozenset({"supply_line", "batch_below", "scan_loss_probability", "affects_arm"})

_SUPPLY_LINE_LOGIT_BY_INDEX = np.asarray([SUPPLY_LINE_LOGIT[p] for p in SUPPLY_LINES])
_BATCH_LABELS = np.asarray((*BATCHES, "web"))
_WEB_INDEX = SUPPLY_LINES.index("web")
_VERSION_PATTERN = re.compile(r"^\d+(\.\d+)*$")
_STREAMS = ("covariates", "arrival", "salt", "render", "outcome", "loss", "timing")


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in str(v).split("."))


def batch_below_check(versions: np.ndarray, threshold: str) -> np.ndarray:
    """Elementwise ``version < threshold`` for dotted version strings."""
    thr = version_tuple(threshold)
    return np.asarray([version_tuple(v) < thr if v != "web" else False for v in versions])


def hash_assign(product_ids: np.ndarray, salt: int = HASH_SALT) -> np.ndarray:
    """Deterministic 50/50 assignment from a 64-bit integer mix of the user id and a salt.

    Models hash-based bucketing: for a given salt the same user always lands in the same
    arm, and the split is balanced in expectation without any coin being flipped at request
    time. A new salt is a new experiment's bucketing. Returns 1 for treatment, 0 for control.
    """
    x = (product_ids.astype(np.uint64) + np.uint64(salt)) & np.uint64(0xFFFFFFFFFFFFFFFF)
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
    legacy_route_probability: float
    older_batch_logit_offset: float
    loss_probability: float
    affected_supply_line: str
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

    Unknown top-level or incident keys are rejected. Defaults: ``legacy_route_probability``
    0.15, ``older_batch_logit_offset`` -0.30.
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
    baseline = _as_number("baseline_yield_rate", config["baseline_yield_rate"])
    if not 0.0 < baseline < 1.0:
        raise ValueError(f"baseline_yield_rate must be in (0, 1); got {baseline!r}")
    lift = _as_number("true_relative_lift", config["true_relative_lift"])
    if lift <= -1.0:
        raise ValueError(f"true_relative_lift must be above -1; got {lift!r}")
    supply_line = incident["supply_line"]
    if supply_line not in SUPPLY_LINES or supply_line == "web":
        raise ValueError(
            f"incident.supply_line must be one of {SUPPLY_LINES[:_WEB_INDEX]} (web has no app "
            f"version); got {supply_line!r}"
        )
    threshold = incident["batch_below"]
    if not isinstance(threshold, str) or not _VERSION_PATTERN.match(threshold):
        raise ValueError(
            "incident.batch_below must be a quoted dotted version string such as "
            f'"8.4" (an unquoted 8.10 would parse as the number 8.1); got {threshold!r}'
        )
    affects = str(incident["affects_arm"])
    affected_arms(affects)
    window = _as_number("outcome_window_days", config["outcome_window_days"])
    if window <= 0:
        raise ValueError(f"outcome_window_days must be positive; got {window!r}")
    delay = config["records_pipeline"]["arrival_delay_hours"]
    d_min = _as_number("records_pipeline.arrival_delay_hours.min", delay["min"])
    d_max = _as_number("records_pipeline.arrival_delay_hours.max", delay["max"])
    if not 0.0 <= d_min <= d_max:
        raise ValueError(f"records_pipeline.arrival_delay_hours needs 0 <= min <= max; got {delay}")
    return DGPParameters(
        days=_as_count("days", config["days"]),
        per_day=_as_count("eligible_products_per_day", config["eligible_products_per_day"]),
        baseline=baseline,
        lift=lift,
        legacy_route_probability=_as_probability(
            "legacy_route_probability",
            config.get("legacy_route_probability", LEGACY_ROUTE_PROBABILITY),
            allow_one=False,
        ),
        older_batch_logit_offset=_as_number(
            "older_batch_logit_offset",
            config.get("older_batch_logit_offset", OLDER_BATCH_LOGIT),
        ),
        loss_probability=_as_probability(
            "incident.scan_loss_probability", incident["scan_loss_probability"]
        ),
        affected_supply_line=supply_line,
        version_threshold=threshold,
        affects_arm=affects,
        outcome_window_hours=window * 24.0,
        arrival_delay_min_hours=d_min,
        arrival_delay_max_hours=d_max,
    )


@dataclass(frozen=True)
class Population:
    """Arrays for one simulated experiment. All arrays are aligned on user index."""

    product_id: np.ndarray
    supply_line_idx: np.ndarray  # index into SUPPLY_LINES
    version_idx: np.ndarray  # index into BATCHES, len(BATCHES) for web
    country_idx: np.ndarray  # index into COUNTRIES
    pre_period_finished_28d: np.ndarray
    assigned_at: np.ndarray  # hours since experiment start
    arm: np.ndarray  # 1 treatment, 0 control
    p_control: np.ndarray
    p_treatment: np.ndarray  # equals p_control for users served the fallback
    y0: np.ndarray
    y1: np.ndarray
    reached_scanner: np.ndarray  # bool: the widget rendered (arm-independent)
    loss_uniform: np.ndarray  # uniform draw deciding render-event loss, per user
    scan_logged: np.ndarray  # bool: rendered and the render event survived the incident
    in_older_batch: np.ndarray  # bool: android below threshold (both arms)
    records_delay_hours: np.ndarray  # eligibility to order, if the user orders
    arrival_delay_hours: np.ndarray  # order to receipt by the batch pipeline
    realised_control_rate: float
    extras: Mapping[str, Any] = field(default_factory=dict)

    @property
    def supply_line(self) -> np.ndarray:
        return np.asarray(SUPPLY_LINES)[self.supply_line_idx]

    @property
    def batch(self) -> np.ndarray:
        return _BATCH_LABELS[self.version_idx]

    @property
    def country(self) -> np.ndarray:
        return np.asarray(COUNTRIES)[self.country_idx]

    @property
    def y(self) -> np.ndarray:
        return np.where(self.arm == 1, self.y1, self.y0)

    @property
    def n(self) -> int:
        return int(self.product_id.size)

    def received_by(self, hours: float) -> np.ndarray:
        """Converters whose order the pipeline has delivered by ``hours`` after the start."""
        received = self.assigned_at + self.records_delay_hours + self.arrival_delay_hours
        return (self.y == 1) & (received <= hours)

    def logged_under(self, loss_probability: float, affects_arm: str) -> np.ndarray:
        """Exposure mask the same population would have under another incident setting.

        Uses the population's own loss draws, so every setting is evaluated on the same
        users, assignment and outcomes (common random numbers across incident settings).
        """
        p = _as_probability("loss_probability", loss_probability)
        in_hit_arm = np.isin(self.arm, affected_arms(affects_arm))
        lost = self.reached_scanner & self.in_older_batch & in_hit_arm & (self.loss_uniform < p)
        return self.reached_scanner & ~lost

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


_PRE_FINISHED_CDF = {
    False: _zinb_cdf(ZERO_INFLATION, NB_MEAN, NB_DISPERSION),
    True: _zinb_cdf(OLDER_BATCH_ZERO_INFLATION, OLDER_BATCH_NB_MEAN, NB_DISPERSION),
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

    product_id = np.arange(1, n + 1, dtype=np.int64)
    cov = streams["covariates"].random((4, n))
    supply_line_idx = _categorical(cov[0], SUPPLY_LINE_SHARES)
    version_idx = _categorical(cov[1], BATCH_SHARES)
    version_idx = np.where(supply_line_idx == _WEB_INDEX, len(BATCHES), version_idx)
    country_idx = _categorical(cov[2], COUNTRY_SHARES)

    below = np.append(batch_below_check(np.asarray(BATCHES), prm.version_threshold), False)
    older_batch = (supply_line_idx == SUPPLY_LINES.index(prm.affected_supply_line)) & below[
        version_idx
    ]
    pre_finished = np.where(
        older_batch,
        np.searchsorted(_PRE_FINISHED_CDF[True], cov[3], side="right"),
        np.searchsorted(_PRE_FINISHED_CDF[False], cov[3], side="right"),
    ).astype(np.int64)

    # Arrival time: day drawn with weekday weights, hour uniform within the day.
    arrival = streams["arrival"].random((2, n))
    day_weights = np.asarray([WEEKDAY_WEIGHTS[d % 7] for d in range(prm.days)], dtype=float)
    day = _categorical(arrival[0], tuple(day_weights / day_weights.sum()))
    assigned_at = day * 24.0 + arrival[1] * 24.0

    salt = int(streams["salt"].integers(0, 2**63 - 1))
    arm = hash_assign(product_id, salt)

    # The linear predictor takes few distinct values (platform x cell x pre-period orders),
    # so the intercept is calibrated on a lookup table of those values weighted by their
    # counts rather than on n users.
    stride = int(pre_finished.max()) + 1
    key = (supply_line_idx * 2 + older_batch.astype(np.int64)) * stride + pre_finished
    counts = np.bincount(key, minlength=len(SUPPLY_LINES) * 2 * stride).astype(float)
    table_key = np.arange(counts.size)
    linear_table = (
        _SUPPLY_LINE_LOGIT_BY_INDEX[table_key // stride // 2]
        + prm.older_batch_logit_offset * ((table_key // stride) % 2)
        + PRE_FINISHED_LOGIT_PER_LOG1P * np.log1p(table_key % stride)
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

    reached_scanner = streams["render"].random(n) >= prm.legacy_route_probability
    p_treatment = np.where(reached_scanner, p_control * (1.0 + prm.lift), p_control)

    # Common random numbers: the same uniform decides both potential outcomes, so a
    # positive lift can only turn a 0 into a 1, never the reverse.
    u = streams["outcome"].random(n)
    y0 = (u < p_control).astype(np.int8)
    y1 = (u < p_treatment).astype(np.int8)

    loss_uniform = streams["loss"].random(n)
    in_hit_arm = np.isin(arm, affected_arms(prm.affects_arm))
    lost = reached_scanner & older_batch & in_hit_arm & (loss_uniform < prm.loss_probability)
    scan_logged = reached_scanner & ~lost

    # Order timing, drawn for every user (used only where the user orders): time from
    # eligibility to order, exponential truncated to the outcome window, and the batch
    # pipeline's delivery delay, uniform, identical in both arms.
    timing = streams["timing"].random((2, n))
    records_delay = np.minimum(
        -RECORDS_DELAY_MEAN_HOURS * np.log1p(-timing[0]), prm.outcome_window_hours - 1e-6
    )
    arrival_delay = prm.arrival_delay_min_hours + timing[1] * (
        prm.arrival_delay_max_hours - prm.arrival_delay_min_hours
    )

    return Population(
        product_id=product_id,
        supply_line_idx=supply_line_idx,
        version_idx=version_idx,
        country_idx=country_idx,
        pre_period_finished_28d=pre_finished.astype(np.int32),
        assigned_at=assigned_at,
        arm=arm,
        p_control=p_control,
        p_treatment=p_treatment,
        y0=y0,
        y1=y1,
        reached_scanner=reached_scanner,
        loss_uniform=loss_uniform,
        scan_logged=scan_logged,
        in_older_batch=older_batch,
        records_delay_hours=records_delay,
        arrival_delay_hours=arrival_delay,
        realised_control_rate=float(p_control.mean()),
        extras={
            "intercept": float(intercept),
            "logit_baseline": float(logit(prm.baseline)),
            "hash_salt": salt,
            "legacy_route_probability": prm.legacy_route_probability,
            "older_batch_logit_offset": prm.older_batch_logit_offset,
        },
    )


@dataclass(frozen=True)
class AssemblyExperimentData:
    products: pd.DataFrame
    assignments: pd.DataFrame
    scans: pd.DataFrame
    packing_runs: pd.DataFrame
    finished_products: pd.DataFrame
    scan_log_health: pd.DataFrame
    truth: pd.DataFrame
    manifest: dict[str, Any]

    def tables(self) -> dict[str, pd.DataFrame]:
        return {
            "products": self.products,
            "assignments": self.assignments,
            "scans": self.scans,
            "packing_runs": self.packing_runs,
            "finished_products": self.finished_products,
            "scan_log_health": self.scan_log_health,
            "truth": self.truth,
        }


def simulate_assembly_experiment(
    config: Mapping[str, Any], seed: int | None = None
) -> AssemblyExperimentData:
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
    supply_line, batch = pop.supply_line, pop.batch

    products = pd.DataFrame(
        {
            "product_id": pop.product_id,
            "supply_line": supply_line,
            "batch": batch,
            "country": pop.country,
            "pre_period_finished_28d": pop.pre_period_finished_28d,
        }
    )
    assignments = pd.DataFrame(
        {"product_id": pop.product_id, "arm": arm_label, "assigned_at": assigned_ts}
    )

    exposed = pop.scan_logged
    scans = pd.DataFrame(
        {
            "product_id": pop.product_id[exposed],
            "scanned_at": assigned_ts[exposed]
            + pd.to_timedelta(rng.random(exposed.sum()) * 5, "s"),
            "station_version": np.where(pop.arm[exposed] == 1, "v2", "v1"),
            "event_id": np.arange(1, exposed.sum() + 1, dtype=np.int64),
        }
    )

    # Payment-step loads: one or more per eligible user (every eligible user reaches the
    # checkout page), through the widget where it rendered and through the legacy fallback
    # where it did not. ``packing_step_error`` marks a load on which the payment step raised
    # a client error; a load is not a payment, so most loads end without an order. Latency
    # comes from the widget's timing event, so it exists only where the render event was
    # logged.
    n_loads = 1 + rng.poisson(EXTRA_LOADS_POISSON, size=n)
    load_product = np.repeat(pop.product_id, n_loads)
    load_idx = np.repeat(np.arange(n), n_loads)
    total_loads = int(n_loads.sum())
    load_offset_min = rng.random(total_loads) * 30.0
    latency = rng.lognormal(CYCLE_TIME_LOG_MEAN, CYCLE_TIME_LOG_SD, size=total_loads)
    latency = np.where(pop.scan_logged[load_idx], latency, np.nan)
    packing_runs = pd.DataFrame(
        {
            "product_id": load_product,
            "attempted_at": assigned_ts[load_idx] + pd.to_timedelta(load_offset_min, "m"),
            "packing_step_error": rng.random(total_loads) < PACKING_STEP_ERROR_RATE,
            "cycle_time_ms": np.round(latency, 1),
        }
    )

    # Orders: converters place one order within the outcome window; the batch pipeline
    # delivers it 0 to 36 hours later, identically in both arms (timing drawn in
    # ``simulate_population``). A fixed share of orders is refunded within 14 days,
    # identically in both arms; returned_at is NaT otherwise.
    y = pop.y.astype(bool)
    n_finished = int(y.sum())
    finished_at = assigned_ts[y] + pd.to_timedelta(pop.records_delay_hours[y], "h")
    received_at = finished_at + pd.to_timedelta(pop.arrival_delay_hours[y], "h")
    amount = np.round(rng.lognormal(AMOUNT_LOG_MEAN, AMOUNT_LOG_SD, size=n_finished), 2)
    refunded = rng.random(n_finished) < RETURN_PROBABILITY
    return_lag_h = rng.random(n_finished) * RETURN_WINDOW_DAYS * 24.0
    returned_at = (finished_at + pd.to_timedelta(return_lag_h, "h")).where(refunded)
    finished_products = pd.DataFrame(
        {
            "product_id": pop.product_id[y],
            "finished_product_id": np.arange(1, n_finished + 1, dtype=np.int64),
            "finished_at": finished_at,
            "received_at": received_at,
            "amount": amount,
            "returned_at": returned_at,
        }
    )

    # Telemetry health: a monitoring table, arms pooled, as an observability system would
    # publish it by comparing server-side widget renders with analytics render events.
    # Event loss rate = 1 - logged render events / rendered widgets.
    day = (pop.assigned_at // 24).astype(int)
    r = pop.reached_scanner
    health = (
        pd.DataFrame(
            {
                "supply_line": supply_line[r],
                "batch": batch[r],
                "day": t0.normalize() + pd.to_timedelta(day[r], "D"),
                "logged": pop.scan_logged[r].astype(int),
            }
        )
        .groupby(["supply_line", "batch", "day"], as_index=False)
        .agg(products=("logged", "size"), logged=("logged", "sum"))
    )
    health["event_loss_rate"] = 1.0 - health["logged"] / health["products"]
    scan_log_health = health[["supply_line", "batch", "day", "products", "event_loss_rate"]]

    truth = pd.DataFrame(
        {
            "product_id": pop.product_id,
            "p_control": pop.p_control,
            "p_treatment": pop.p_treatment,
            "y0": pop.y0,
            "y1": pop.y1,
            "reached_scanner": pop.reached_scanner,
            "scan_logged": pop.scan_logged,
            "in_older_batch": pop.in_older_batch,
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
        "expected_ate_absolute_reached_scanner": pop.expected_ate_absolute(pop.reached_scanner),
        "expected_relative_lift_itt": pop.expected_relative_lift(),
        "expected_relative_lift_reached_scanner": pop.expected_relative_lift(pop.reached_scanner),
        "realised": {
            "n_products": n,
            "control_rate_expected": pop.realised_control_rate,
            "sample_ate_absolute": float(pop.y1.mean() - pop.y0.mean()),
            "assigned_share_treatment": float(pop.arm.mean()),
            "older_batch_share": float(pop.in_older_batch.mean()),
            "reach_rate": float(pop.reached_scanner.mean()),
            "scan_loss_rate_in_older_batch_treatment": _loss_rate(pop),
            "calibrated_intercept": pop.extras["intercept"],
            "hash_salt": pop.extras["hash_salt"],
        },
        "row_counts": {
            "products": int(len(products)),
            "assignments": int(len(assignments)),
            "scans": int(len(scans)),
            "packing_runs": int(len(packing_runs)),
            "finished_products": int(len(finished_products)),
            "scan_log_health": int(len(scan_log_health)),
            "truth": int(len(truth)),
        },
    }
    return AssemblyExperimentData(
        products,
        assignments,
        scans,
        packing_runs,
        finished_products,
        scan_log_health,
        truth,
        manifest,
    )


def _loss_rate(pop: Population) -> float:
    """Share of rendered, affected-cell treatment users whose render event was lost."""
    mask = pop.in_older_batch & (pop.arm == 1) & pop.reached_scanner
    return float(1.0 - pop.scan_logged[mask].mean()) if mask.any() else 0.0


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
    data: AssemblyExperimentData, generated_dir: Path, manifest_path: Path
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
    manifest_path.write_text(
        json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest
