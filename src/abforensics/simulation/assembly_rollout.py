"""Case 01 rollout: the repaired station re-released through a staged rollout.

Everything here is simulated. It tests claims C1 to
C3. The Case 01 generator (``assembly_incident``) is read, never modified: this module reuses its
constants (supply-line and batch shares, the yield model, the scan-loss incident) and adds a
time-varying routing probability, a linear drift in the baseline yield and three estimators.

Two layers:

* ``simulate_rollout_population`` draws one product-level rollout (560,000 products by default)
  for ``abf simulate case01-rollout`` and ``abf run case01-rollout``.
* ``sample_day_arm_counts`` draws the day-by-arm counts directly for the operating
  characteristics. Products are independent and identically distributed within a day, so the
  number of finished products among n products routed to an arm on day d is exactly
  Binomial(n, mean yield probability of that arm on that day), and the number routed to the new
  station is Binomial(N_d, p_d). Every estimator in this module reads only those counts, so the
  sampled distribution of every estimator is the one the product-level generator implies. The
  canary's logged-scan counts are drawn the same way, from the logged-scan probability of each arm.
  A unit test checks the two layers against each other.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import stats
from scipy.special import expit

from abforensics.simulation import assembly_incident as ai

Z95 = float(stats.norm.ppf(0.975))
RELATIVE_ZERO_RULE = 1e-9  # aggregate control mean at or below this: relative estimate unavailable
ESTIMATORS = ("pooled", "day_standardised", "final_stage")
TARGET = {"pooled": "E_ramp", "day_standardised": "E_ramp", "final_stage": "E_final"}


@dataclass(frozen=True)
class RolloutParameters:
    """Validated parameters of the rollout design (spec section 3)."""

    days: int
    per_day: int
    stage_bounds: tuple[tuple[int, int], ...]  # inclusive day ranges, 1-based
    stage_p: tuple[float, ...]
    baseline: float
    lift: float  # relative lift among products that reach the scanner
    legacy_route_probability: float
    kappa: float
    affected_share: float
    loss_probability: float
    defect_on: bool
    final_stage: int  # index of the stage whose window defines E_final
    ship_floor: float
    canary_alpha: float

    @property
    def n_stages(self) -> int:
        return len(self.stage_p)


def affected_share(supply_line: str, batch_below: str) -> float:
    """Share of products on ``supply_line`` below batch ``batch_below`` (the Case 01 segment)."""
    line_share = ai.SUPPLY_LINE_SHARES[ai.SUPPLY_LINES.index(supply_line)]
    below = ai.batch_below_check(np.asarray(ai.BATCHES), batch_below)
    return float(line_share * np.asarray(ai.BATCH_SHARES)[below].sum())


def validate_rollout_config(config: Mapping[str, Any]) -> RolloutParameters:
    """Read and check the rollout config; reject anything the specification does not allow."""
    days = int(config["days"])
    per_day = int(config["eligible_products_per_day"])
    schedule = config["schedule"]
    bounds = tuple((int(s["days"][0]), int(s["days"][1])) for s in schedule)
    stage_p = tuple(float(s["p"]) for s in schedule)
    expected_first = 1
    for first, last in bounds:
        if first != expected_first or last < first:
            raise ValueError(f"schedule must tile days 1..{days} without gaps; got {bounds}")
        expected_first = last + 1
    if expected_first != days + 1:
        raise ValueError(f"schedule must end on day {days}; got {bounds}")
    if not all(0.0 < p < 1.0 for p in stage_p):
        raise ValueError(f"routing probabilities must lie in (0, 1); got {stage_p}")
    incident = config["incident"]
    prm = RolloutParameters(
        days=days,
        per_day=per_day,
        stage_bounds=bounds,
        stage_p=stage_p,
        baseline=float(config["baseline_yield_rate"]),
        lift=float(config["true_relative_lift"]),
        legacy_route_probability=float(config["legacy_route_probability"]),
        kappa=float(config["trend_kappa"]),
        affected_share=affected_share(incident["supply_line"], str(incident["batch_below"])),
        loss_probability=float(incident["scan_loss_probability"]),
        defect_on=bool(incident["enabled"]),
        final_stage=len(stage_p) - 1,
        ship_floor=float(config["decision_rule"]["ship_if_lower_bound_above_relative"]),
        canary_alpha=float(config["canary"]["alpha"]),
    )
    if incident.get("affects_arm", "treatment") != "treatment":
        raise ValueError("the rollout canary is specified for a defect in the new station only")
    expected_rates(prm)  # probability assertion
    return prm


def with_changes(prm: RolloutParameters, **changes: Any) -> RolloutParameters:
    """A copy of ``prm`` with fields replaced; re-runs the probability assertion."""
    out = RolloutParameters(**{**prm.__dict__, **changes})
    expected_rates(out)
    return out


def day_index(prm: RolloutParameters) -> np.ndarray:
    return np.arange(1, prm.days + 1)


def routing_probability(prm: RolloutParameters) -> np.ndarray:
    """p_d for days 1..days (claim C1)."""
    p = np.empty(prm.days)
    for (first, last), value in zip(prm.stage_bounds, prm.stage_p, strict=True):
        p[first - 1 : last] = value
    return p


def stage_of_day(prm: RolloutParameters) -> np.ndarray:
    s = np.empty(prm.days, dtype=int)
    for i, (first, last) in enumerate(prm.stage_bounds):
        s[first - 1 : last] = i
    return s


def trend_factor(prm: RolloutParameters) -> np.ndarray:
    """g_d = 1 + kappa (d - midpoint): a linear drift centred on the middle of the rollout."""
    mid = (prm.days + 1) / 2
    return 1.0 + prm.kappa * (day_index(prm) - mid)


def expected_rates(
    prm: RolloutParameters, stage_lift: Sequence[float] | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Mean yield probability per day in the old (p0) and new (p1) station.

    ``stage_lift`` overrides the lift per stage (scenario S2); the lift applies only to products
    that reach the scanner, so the mean new-station probability is p0 (1 + (1 - legacy) lift).
    The probability assertion of the specification is enforced here: a violation raises.
    """
    p0 = prm.baseline * trend_factor(prm)
    lifts = (
        np.full(prm.days, prm.lift)
        if stage_lift is None
        else np.asarray(stage_lift, dtype=float)[stage_of_day(prm)]
    )
    p1 = p0 * (1.0 + (1.0 - prm.legacy_route_probability) * lifts)
    # The product-level yield probabilities spread around the mean (supply line, batch,
    # pre-period finishes); the largest one is checked in simulate_rollout_population. Here the
    # day means and the largest reached-scanner probability ratio are checked.
    worst = p0.max() * (1.0 + max(float(np.max(lifts)), 0.0))
    if not (p0.min() > 0.0 and p1.min() > 0.0 and worst < 1.0 and p1.max() < 1.0):
        raise ValueError(
            "rollout probabilities leave (0, 1): "
            f"min p0={p0.min():.6f}, min p1={p1.min():.6f}, max={max(worst, p1.max()):.6f}"
        )
    return p0, p1


def logged_scan_probabilities(prm: RolloutParameters, defect_on: bool | None = None) -> tuple:
    """Probability that a product has a logged scan, new station (e1) and old station (e0)."""
    on = prm.defect_on if defect_on is None else defect_on
    reach = 1.0 - prm.legacy_route_probability
    e1 = reach * (1.0 - prm.affected_share * prm.loss_probability) if on else reach
    return e1, reach


# ---------------------------------------------------------------- truths and expectations


def truths(prm: RolloutParameters, stage_lift: Sequence[float] | None = None) -> dict[str, float]:
    """E_ramp and E_final on the absolute and relative scale, from potential-outcome means."""
    p0, p1 = expected_rates(prm, stage_lift)
    w = np.full(prm.days, prm.per_day, dtype=float)
    final = stage_of_day(prm) == prm.final_stage
    out = {}
    for name, mask in (("E_ramp", np.ones(prm.days, bool)), ("E_final", final)):
        a = float(np.average(p1[mask], weights=w[mask]))
        b = float(np.average(p0[mask], weights=w[mask]))
        out[f"{name}_abs"] = a - b
        out[f"{name}_rel"] = a / b - 1.0
    return out


def pooled_expectation(
    prm: RolloutParameters, stage_lift: Sequence[float] | None = None
) -> dict[str, float]:
    """Ratio of expected totals for the pooled contrast (its analytic reference, spec section 4)."""
    p0, p1 = expected_rates(prm, stage_lift)
    pd_ = routing_probability(prm)
    n1 = prm.per_day * pd_
    n0 = prm.per_day * (1.0 - pd_)
    m1 = float((n1 * p1).sum() / n1.sum())
    m0 = float((n0 * p0).sum() / n0.sum())
    return {"abs": m1 - m0, "rel": m1 / m0 - 1.0}


def day_standardised_variance_shares(prm: RolloutParameters) -> np.ndarray:
    """Share of the day-standardised estimator's variance by stage, at the baseline rate."""
    b = prm.baseline
    pd_ = routing_probability(prm)
    w = 1.0 / prm.days
    v = w**2 * (b * (1 - b) / (prm.per_day * pd_) + b * (1 - b) / (prm.per_day * (1 - pd_)))
    s = stage_of_day(prm)
    return np.asarray([v[s == i].sum() for i in range(prm.n_stages)]) / v.sum()


# ---------------------------------------------------------------- canary (claim C3)

CHI2_CRIT_0001 = float(stats.chi2.ppf(1.0 - 0.001, 1))


def canary_statistic(t: np.ndarray, m: np.ndarray, p: float) -> np.ndarray:
    """Z^2 of the scan-level sample-ratio check; m = 0 gives 0 (no flag)."""
    t = np.asarray(t, dtype=float)
    m = np.asarray(m, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        z2 = (t - p * m) ** 2 / (m * p * (1.0 - p))
    return np.where(m > 0, z2, 0.0)


def exact_canary_power(n: int, p: float, e1: float, e0: float, alpha: float = 0.001) -> dict:
    """Exact probability that the stage's sample-ratio check flags (spec section 6).

    M = T + C ~ Binomial(n, q), q = p e1 + (1 - p) e0; T | M = m ~ Binomial(m, theta),
    theta = p e1 / q. The sum over m is truncated at 12 SD of M; the omitted mass is returned.
    """
    crit = float(stats.chi2.ppf(1.0 - alpha, 1))
    q = p * e1 + (1.0 - p) * e0
    theta = p * e1 / q
    mu, sd = n * q, math.sqrt(n * q * (1.0 - q))
    m = np.arange(max(1, int(mu - 12 * sd)), min(n, int(mu + 12 * sd)) + 1)
    pm = stats.binom.pmf(m, n, q)
    half = np.sqrt(crit * m * p * (1.0 - p))
    upper = np.ceil(p * m + half - 1e-12)
    lower = np.floor(p * m - half + 1e-12)
    flag = stats.binom.sf(upper - 1, m, theta) + stats.binom.cdf(lower, m, theta)
    omitted = 1.0 - float(pm.sum()) - float(stats.binom.pmf(0, n, q))
    return {"power": float((pm * flag).sum()), "omitted_mass": max(omitted, 0.0), "crit": crit}


def brute_force_canary_power(n: int, p: float, e1: float, e0: float, alpha: float = 0.001):
    """Full trinomial enumeration (small n only), the unit-test reference for the exact power."""
    crit = float(stats.chi2.ppf(1.0 - alpha, 1))
    a, b = p * e1, (1.0 - p) * e0
    c = 1.0 - a - b
    total = 0.0
    for t in range(n + 1):
        for k in range(n - t + 1):
            prob = math.exp(
                math.lgamma(n + 1)
                - math.lgamma(t + 1)
                - math.lgamma(k + 1)
                - math.lgamma(n - t - k + 1)
                + (t * math.log(a) if t else 0.0)
                + (k * math.log(b) if k else 0.0)
                + ((n - t - k) * math.log(c) if n - t - k else 0.0)
            )
            m = t + k
            if m > 0 and (t - p * m) ** 2 / (m * p * (1.0 - p)) >= crit:
                total += prob
    return total


# ---------------------------------------------------------------- sampling (OC layer)


@dataclass(frozen=True)
class DayArmCounts:
    """Counts per replicate (rows) and day (columns)."""

    n1: np.ndarray
    y1: np.ndarray
    n0: np.ndarray
    y0: np.ndarray


def sample_day_arm_counts(
    prm: RolloutParameters,
    b: int,
    rng: np.random.Generator,
    stage_lift: Sequence[float] | None = None,
) -> DayArmCounts:
    """Draw B replicates of the day-by-arm routed and finished counts (exact, see module doc)."""
    p0, p1 = expected_rates(prm, stage_lift)
    pd_ = routing_probability(prm)
    n1 = rng.binomial(prm.per_day, np.broadcast_to(pd_, (b, prm.days)))
    n0 = prm.per_day - n1
    y1 = rng.binomial(n1, np.broadcast_to(p1, (b, prm.days)))
    y0 = rng.binomial(n0, np.broadcast_to(p0, (b, prm.days)))
    return DayArmCounts(n1=n1, y1=y1, n0=n0, y0=y0)


def sample_canary_counts(
    prm: RolloutParameters, b: int, rng: np.random.Generator, defect_on: bool = True
) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Per stage: (T, M), the new station's logged scans and all logged scans, B replicates."""
    e1, e0 = logged_scan_probabilities(prm, defect_on)
    out = {}
    for i, (first, last) in enumerate(prm.stage_bounds):
        n = prm.per_day * (last - first + 1)
        n1 = rng.binomial(n, prm.stage_p[i], size=b)
        t = rng.binomial(n1, e1)
        c = rng.binomial(n - n1, e0)
        out[i] = (t, t + c)
    return out


# ---------------------------------------------------------------- estimators (claim C2)


def _ratio_interval(a, b, var_a, var_b):
    """Relative lift a/b - 1 with a delta-method interval on the log ratio (arms independent)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    ok = (a > 0) & (b > RELATIVE_ZERO_RULE)
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(ok, a / b - 1.0, np.nan)
        se = np.sqrt(var_a / a**2 + var_b / b**2)
        log_r = np.log(a / b)
        lo = np.where(ok, np.exp(log_r - Z95 * se) - 1.0, np.nan)
        hi = np.where(ok, np.exp(log_r + Z95 * se) - 1.0, np.nan)
    return rel, lo, hi


def _two_proportion(n1, y1, n0, y0) -> dict[str, np.ndarray]:
    """Unpooled Wald difference and delta-method relative lift, vectorised (as Case 01 ITT)."""
    n1 = np.asarray(n1, dtype=float)
    n0 = np.asarray(n0, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        r1 = np.asarray(y1) / n1
        r0 = np.asarray(y0) / n0
        se = np.sqrt(r1 * (1 - r1) / n1 + r0 * (1 - r0) / n0)
        rel, lo_r, hi_r = _ratio_interval(r1, r0, r1 * (1 - r1) / n1, r0 * (1 - r0) / n0)
    diff = r1 - r0
    return {
        "abs": diff,
        "abs_lo": diff - Z95 * se,
        "abs_hi": diff + Z95 * se,
        "rel": rel,
        "rel_lo": lo_r,
        "rel_hi": hi_r,
    }


def estimate_pooled(c: DayArmCounts) -> dict[str, np.ndarray]:
    """All new-station products against all old-station products over the whole rollout."""
    return _two_proportion(c.n1.sum(-1), c.y1.sum(-1), c.n0.sum(-1), c.y0.sum(-1))


def estimate_final_stage(c: DayArmCounts, prm: RolloutParameters) -> dict[str, np.ndarray]:
    """The Case 01 ITT estimator on the final stage's window."""
    m = stage_of_day(prm) == prm.final_stage
    return _two_proportion(
        c.n1[..., m].sum(-1), c.y1[..., m].sum(-1), c.n0[..., m].sum(-1), c.y0[..., m].sum(-1)
    )


def estimate_day_standardised(c: DayArmCounts, prm: RolloutParameters) -> dict[str, np.ndarray]:
    """tau = sum_d w_d (Ybar_1d - Ybar_0d), w_d = N_d / sum_j N_j.

    Unavailable for a replicate in which any arm-day is empty.
    """
    n1 = c.n1.astype(float)
    n0 = c.n0.astype(float)
    w = np.full(prm.days, 1.0 / prm.days)  # N_d is the same every day
    with np.errstate(divide="ignore", invalid="ignore"):
        r1 = c.y1 / n1
        r0 = c.y0 / n0
        v1 = r1 * (1 - r1) / n1
        v0 = r0 * (1 - r0) / n0
    empty = (c.n1 == 0).any(-1) | (c.n0 == 0).any(-1)
    a = (w * r1).sum(-1)
    b = (w * r0).sum(-1)
    var_a = (w**2 * v1).sum(-1)
    var_b = (w**2 * v0).sum(-1)
    tau = a - b
    se = np.sqrt(var_a + var_b)
    rel, lo_r, hi_r = _ratio_interval(a, b, var_a, var_b)
    nan = np.where(empty, np.nan, 1.0)
    return {
        "abs": tau * nan,
        "abs_lo": (tau - Z95 * se) * nan,
        "abs_hi": (tau + Z95 * se) * nan,
        "rel": rel * nan,
        "rel_lo": lo_r * nan,
        "rel_hi": hi_r * nan,
    }


def estimate_all(c: DayArmCounts, prm: RolloutParameters) -> dict[str, dict[str, np.ndarray]]:
    return {
        "pooled": estimate_pooled(c),
        "day_standardised": estimate_day_standardised(c, prm),
        "final_stage": estimate_final_stage(c, prm),
    }


# ---------------------------------------------------------------- operating characteristics


def _rate(x: np.ndarray) -> tuple[float, float, int]:
    """Mean of a boolean over the available replicates, its MCSE and the count used."""
    x = x[~np.isnan(x)] if x.dtype.kind == "f" else x
    k = int(x.size)
    p = float(np.mean(x)) if k else math.nan
    return p, math.sqrt(p * (1 - p) / k) if k else math.nan, k


def summarise(
    est: dict[str, dict[str, np.ndarray]],
    truth: dict[str, float],
    pooled_ref: dict[str, float],
    prm: RolloutParameters,
    scenario: str,
    kappa: float,
) -> list[dict[str, Any]]:
    """One row per estimator: bias, coverage, rejection and decision rates, each with its MCSE."""
    rows = []
    for name in ESTIMATORS:
        e = est[name]
        target = TARGET[name]
        t_rel, t_abs = truth[f"{target}_rel"], truth[f"{target}_abs"]
        avail = ~np.isnan(e["rel"])
        rel = e["rel"][avail]
        absd = e["abs"][avail]
        b = int(avail.sum())
        cover_rel = (e["rel_lo"][avail] <= t_rel) & (t_rel <= e["rel_hi"][avail])
        cover_abs = (e["abs_lo"][avail] <= t_abs) & (t_abs <= e["abs_hi"][avail])
        rejects = (e["rel_lo"][avail] > 0) | (e["rel_hi"][avail] < 0)
        ships = e["rel_lo"][avail] > prm.ship_floor
        ref_rel = pooled_ref["rel"] if name == "pooled" else t_rel
        ref_abs = pooled_ref["abs"] if name == "pooled" else t_abs
        row = {
            "scenario": scenario,
            "kappa": kappa,
            "estimator": name,
            "estimand": target,
            "replicates": b,
            "truth_rel": t_rel,
            "truth_abs": t_abs,
            "reference_rel": ref_rel,
            "reference_abs": ref_abs,
            "mean_rel": float(rel.mean()),
            "mean_abs": float(absd.mean()),
            "bias_rel": float(rel.mean() - t_rel),
            "mcse_bias_rel": float(rel.std(ddof=1) / math.sqrt(b)),
            "bias_abs": float(absd.mean() - t_abs),
            "mcse_bias_abs": float(absd.std(ddof=1) / math.sqrt(b)),
        }
        for key, arr in (
            ("coverage_rel", cover_rel),
            ("coverage_abs", cover_abs),
            ("reject_zero", rejects),
            ("ship_rate", ships),
        ):
            p, se, _ = _rate(arr)
            row[key] = p
            row[f"mcse_{key}"] = se
        # Reading rule (spec section 5): each estimate against its own reference.
        # Applied on both scales: the relative estimators are ratios, so their bias and coverage
        # can differ from the absolute ones (spec v1.2 section 5).
        flags = []
        for scale in ("abs", "rel"):
            mcse = row[f"mcse_bias_{scale}"]
            if not (b > 1 and mcse > 0):
                flags.append(f"mean_{scale}: not judged (b={b}, mcse={mcse})")
                continue
            dev = (row[f"mean_{scale}"] - row[f"reference_{scale}"]) / mcse
            if abs(dev) > 3:
                flags.append(f"mean_{scale} {dev:+.1f} MCSE from reference")
        if name != "pooled" and b > 1:
            # Rates are judged with the MCSE at the nominal value, so a rate of exactly 0 or 1
            # (whose own MCSE is 0) is still judged.
            mcse_nominal = math.sqrt(0.95 * 0.05 / b)
            for scale in ("abs", "rel"):
                dev_cov = (row[f"coverage_{scale}"] - 0.95) / mcse_nominal
                if abs(dev_cov) > 3:
                    flags.append(f"coverage_{scale} {dev_cov:+.1f} MCSE from 0.95")
            if t_abs == 0.0:
                dev_t1 = (row["reject_zero"] - 0.05) / mcse_nominal
                if abs(dev_t1) > 3:
                    flags.append(f"type_I {dev_t1:+.1f} MCSE from 0.05")
        row["flag"] = "; ".join(flags)
        rows.append(row)
    return rows


def run_operating_characteristics(
    prm: RolloutParameters,
    *,
    replicates: int,
    canary_replicates: int,
    seed: int,
    kappa_sweep: Sequence[float],
    s2_stage_lifts: Sequence[float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Scenarios S0 (with the kappa sweep), S1, S2 and the canary S3.

    Each scenario and sweep point draws from its own child of ``SeedSequence(seed)``, keyed by a
    fixed index, so adding a sweep point never changes another point's draws.
    """
    root = np.random.SeedSequence(seed)
    children = root.spawn(3 + len(kappa_sweep) + 1)
    rows: list[dict[str, Any]] = []
    extra: dict[str, Any] = {}

    for k_idx, kappa in enumerate(kappa_sweep):
        p = with_changes(prm, kappa=float(kappa), lift=0.0)
        rng = np.random.default_rng(children[3 + k_idx])
        c = sample_day_arm_counts(p, replicates, rng)
        est = estimate_all(c, p)
        rows += summarise(est, truths(p), pooled_expectation(p), p, "S0", float(kappa))

    rng = np.random.default_rng(children[0])
    c = sample_day_arm_counts(prm, replicates, rng)
    est = estimate_all(c, prm)
    rows += summarise(est, truths(prm), pooled_expectation(prm), prm, "S1", prm.kappa)
    pooled, std = est["pooled"]["rel"], est["day_standardised"]["rel"]
    both = ~np.isnan(pooled) & ~np.isnan(std)
    extra["S1_sign_disagreement"] = _rate(np.sign(pooled[both]) != np.sign(std[both]))[:2]
    # Replicate-level relative estimates of S1, for the distribution figure (spec section 3.6 and
    # figure T2): the committed single run is one draw from these.
    extra["S1_replicates"] = {name: est[name]["rel"] for name in ESTIMATORS}

    rng = np.random.default_rng(children[1])
    c = sample_day_arm_counts(prm, replicates, rng, stage_lift=s2_stage_lifts)
    est = estimate_all(c, prm)
    tr = truths(prm, s2_stage_lifts)
    rows += summarise(est, tr, pooled_expectation(prm, s2_stage_lifts), prm, "S2", prm.kappa)
    extra["S2_E_ramp_minus_E_final_rel"] = tr["E_ramp_rel"] - tr["E_final_rel"]

    rng = np.random.default_rng(children[2])
    canary_rows = []
    e1, e0 = logged_scan_probabilities(prm, True)
    sim = sample_canary_counts(prm, canary_replicates, rng, defect_on=True)
    for i, (first, last) in enumerate(prm.stage_bounds):
        n = prm.per_day * (last - first + 1)
        t, m = sim[i]
        flags = canary_statistic(t, m, prm.stage_p[i]) >= CHI2_CRIT_0001
        p_sim, se, _ = _rate(flags)
        ex = exact_canary_power(n, prm.stage_p[i], e1, e0, prm.canary_alpha)
        canary_rows.append(
            {
                "stage": i + 1,
                "first_day": first,
                "last_day": last,
                "routing_p": prm.stage_p[i],
                "products": n,
                "e1": e1,
                "e0": e0,
                "exact_power": ex["power"],
                "omitted_mass": ex["omitted_mass"],
                "simulated_power": p_sim,
                "mcse": se,
                "replicates": canary_replicates,
                "flag": _canary_flag(p_sim, ex["power"], canary_replicates),
            }
        )
    return rows, canary_rows, extra


def _canary_flag(p_sim: float, p_exact: float, b: int) -> str:
    """Reading rule against the exact power, with the MCSE taken at the reference value so a
    simulated rate of exactly 0 or 1 is still judged (its own MCSE would be 0)."""
    se = math.sqrt(max(p_exact * (1.0 - p_exact), 1.0 / b**2) / b)
    dev = (p_sim - p_exact) / se
    return "" if abs(dev) <= 3 else f"simulated {dev:+.1f} MCSE from exact"


# ---------------------------------------------------------------- product-level generator


def _hash_uniform(product_ids: np.ndarray, salt: int) -> np.ndarray:
    """A uniform in [0, 1) per product from the same 64-bit mix as ``hash_assign``.

    Sticky: the same product and salt always give the same value, so assignment does not change
    when a product reappears within its outcome window.
    """
    x = (product_ids.astype(np.uint64) + np.uint64(salt)) & np.uint64(0xFFFFFFFFFFFFFFFF)
    x ^= x >> np.uint64(30)
    x = (x * np.uint64(0xBF58476D1CE4E5B9)) & np.uint64(0xFFFFFFFFFFFFFFFF)
    x ^= x >> np.uint64(27)
    x = (x * np.uint64(0x94D049BB133111EB)) & np.uint64(0xFFFFFFFFFFFFFFFF)
    x ^= x >> np.uint64(31)
    return (x >> np.uint64(11)).astype(np.float64) * (1.0 / 9007199254740992.0)


def simulate_rollout_population(
    prm: RolloutParameters, seed: int, stage_lift: Sequence[float] | None = None
) -> dict[str, np.ndarray]:
    """One product-level rollout: day, routing, reaching the scanner, yield, logged scan.

    The yield model is the Case 01 one (supply line, older batch, pre-period finishes, calibrated
    to ``baseline_yield_rate`` on the realised covariates), multiplied by the day's drift g_d.
    """
    n = prm.days * prm.per_day
    root = np.random.SeedSequence(seed)
    s_cov, s_salt, s_reach, s_out, s_loss = (np.random.default_rng(c) for c in root.spawn(5))
    product_id = np.arange(1, n + 1, dtype=np.int64)
    day = np.repeat(day_index(prm), prm.per_day)
    cov = s_cov.random((3, n))
    line = ai._categorical(cov[0], ai.SUPPLY_LINE_SHARES)
    batch = ai._categorical(cov[1], ai.BATCH_SHARES)
    batch = np.where(line == ai.SUPPLY_LINES.index("C"), len(ai.BATCHES), batch)
    below = np.append(ai.batch_below_check(np.asarray(ai.BATCHES), "4"), False)
    older = (line == ai.SUPPLY_LINES.index("A")) & below[batch]
    pre = np.where(
        older,
        np.searchsorted(ai._PRE_FINISHED_CDF[True], cov[2], side="right"),
        np.searchsorted(ai._PRE_FINISHED_CDF[False], cov[2], side="right"),
    )
    linear = (
        np.asarray([ai.SUPPLY_LINE_LOGIT[s] for s in ai.SUPPLY_LINES])[line]
        + ai.OLDER_BATCH_LOGIT * older
        + ai.PRE_FINISHED_LOGIT_PER_LOG1P * np.log1p(pre)
    )
    values, inverse, counts = np.unique(linear, return_inverse=True, return_counts=True)
    intercept = ai._calibrate_intercept(values, counts.astype(float), prm.baseline)
    p_base = expit(intercept + values)[inverse]
    g = trend_factor(prm)[day - 1]
    p0 = p_base * g
    lifts = (
        np.full(prm.days, prm.lift)
        if stage_lift is None
        else np.asarray(stage_lift, dtype=float)[stage_of_day(prm)]
    )
    reached = s_reach.random(n) >= prm.legacy_route_probability
    p1 = np.where(reached, p0 * (1.0 + lifts[day - 1]), p0)
    if not (p0.min() > 0 and p1.max() < 1):
        raise ValueError(f"a product probability leaves (0, 1): max p1 = {p1.max():.6f}")
    salt = int(s_salt.integers(0, 2**63 - 1))
    arm = (_hash_uniform(product_id, salt) < routing_probability(prm)[day - 1]).astype(np.int8)
    u = s_out.random(n)
    y = np.where(arm == 1, u < p1, u < p0).astype(np.int8)
    lost = prm.defect_on & reached & older & (arm == 1) & (s_loss.random(n) < prm.loss_probability)
    return {
        "product_id": product_id,
        "arrival_day": day.astype(np.int16),
        "supply_line": np.asarray(ai.SUPPLY_LINES)[line],
        "older_batch": older,
        "arm": arm,
        "reached_scanner": reached,
        "logged_scan": reached & ~lost,
        "finished": y,
        "p_control": p0,
        "p_treatment": p1,
    }


def counts_from_population(pop: Mapping[str, np.ndarray], prm: RolloutParameters) -> DayArmCounts:
    """Day-by-arm counts (one replicate) from a product-level rollout."""
    d = pop["arrival_day"].astype(int) - 1
    arm = pop["arm"].astype(int)
    y = pop["finished"].astype(int)
    n1 = np.bincount(d[arm == 1], minlength=prm.days)
    n0 = np.bincount(d[arm == 0], minlength=prm.days)
    y1 = np.bincount(d[arm == 1], weights=y[arm == 1], minlength=prm.days).astype(int)
    y0 = np.bincount(d[arm == 0], weights=y[arm == 0], minlength=prm.days).astype(int)
    return DayArmCounts(n1=n1[None], y1=y1[None], n0=n0[None], y0=y0[None])
