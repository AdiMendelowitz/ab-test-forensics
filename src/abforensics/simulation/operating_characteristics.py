"""Operating characteristics of the Case 01 estimators by repeated simulation.

Every rate is reported with its Monte Carlo standard error, sqrt(r (1 - r) / B), and a
Clopper-Pearson 95% interval (which stays informative at 0% and 100%, where the standard
error is zero); every mean is reported with sd / sqrt(B). Four estimators are applied to
each replicate of the generator, all through the package's ``difference_in_proportions``,
so the interval whose coverage is measured is the one the case's decision rule reads (delta
method on the log ratio, exponentiated for the relative lift):

* ``dashboard_day14``: what the dashboard shows on day 14, users with a logged render
  event and orders received by the end of day 14;
* ``exposed_complete``: the same population on complete data (every order counted);
* ``exposed_excluding_cell``: logged users outside the affected segment in both arms, a
  pre-treatment exclusion, on complete data;
* ``itt_assigned``: the pre-registered analysis, all assigned users on complete data.

The three exposure-based estimators target the relative lift among rendered users (for the
segment-excluded one, among rendered users outside the segment); with a constant relative
effect these all equal the configured lift, whatever the weighting by segment or timing.
The ITT estimator targets the relative lift among all eligible users, the decision
estimand. Targets are computed per replicate from the potential-outcome probabilities, never
from the logged users. Each estimator is scored against its own target (``bias``,
``coverage``) and against the decision estimand (``bias_vs_itt``, ``coverage_vs_itt``).
``rule_ship`` applies the pre-declared rule (lower relative bound above the margin) to each
estimator's own estimate, as a dashboard would; ``rule_ship_itt_scale`` first rescales an
exposure-based lower bound to the ITT scale by the replicate's dilution factor, separating
the change of scale from the gain in power. These are operating characteristics of the
rule in simulation; the package still never emits a decision about real data.

Seeding and common random numbers. One ``SeedSequence`` per run is spawned into B children;
replicate i uses child i in every scenario and sweep point, and the generator draws each
block from its own stream. Scenarios that differ only in the incident (A and B; C, D, F and
the loss sweep) are evaluated on the same simulated population, and scenarios that differ
only in the true lift share every draw except the treatment outcomes. Rows are therefore
not independent confirmations of each other, and differences between them are reported
with paired Monte Carlo standard errors (``paired_contrasts``).
"""

from __future__ import annotations

import copy
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from abforensics.diagnostics.srm import srm_check
from abforensics.inference.proportions import difference_in_proportions
from abforensics.simulation.checkout_incident import affected_arms, simulate_population

ESTIMATORS = ("dashboard_day14", "exposed_complete", "exposed_excluding_cell", "itt_assigned")
EXPOSURE_ESTIMATORS = ESTIMATORS[:3]
_ALLOCATION = {"control": 0.5, "treatment": 0.5}
LOSS_KEY = "incident.exposure_loss_probability"
# Keys a sweep may vary. Anything else raises, so a misspelt key cannot silently produce a
# flat sweep.
SWEEPABLE = frozenset(
    {
        LOSS_KEY,
        "affected_cell_logit_offset",
        "non_render_probability",
        "baseline_order_rate",
        "true_relative_lift",
    }
)


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    null: bool  # true lift 0; otherwise the scenario lift
    loss: bool  # incident on (config loss probability) or off (0)
    affects_arm: str = "treatment"


SCENARIOS: dict[str, Scenario] = {
    s.key: s
    for s in (
        Scenario("A", "null, no incident", null=True, loss=False),
        Scenario("B", "null, incident", null=True, loss=True),
        Scenario("C", "effect, no incident", null=False, loss=False),
        Scenario("D", "effect, incident", null=False, loss=True),
        Scenario("F", "effect, loss in both arms", null=False, loss=True, affects_arm="both"),
    )
}


@dataclass(frozen=True)
class Variant:
    """One incident setting evaluated on a shared population."""

    name: str
    loss_probability: float
    affects_arm: str


def scenario_config(
    config: Mapping[str, Any],
    *,
    lift: float,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Deep copy of ``config`` with the true lift set and ``overrides`` applied.

    ``overrides`` keys must be in ``SWEEPABLE``; ``incident.``-prefixed keys set incident
    fields. The caller's mapping is never mutated.
    """
    cfg = copy.deepcopy(dict(config))
    cfg["true_relative_lift"] = float(lift)
    for key, value in (overrides or {}).items():
        if key not in SWEEPABLE:
            raise ValueError(f"cannot vary {key!r}; sweepable keys are {sorted(SWEEPABLE)}")
        if key.startswith("incident."):
            cfg["incident"][key.removeprefix("incident.")] = value
        else:
            cfg[key] = value
    return cfg


class _LossCounts:
    """Per-arm counts of users whose render event can be lost, sorted by their loss draw.

    Evaluating a loss probability p then costs one ``searchsorted`` per arm: the lost users
    are the candidates with a draw below p, and cumulative sums give their conversions.
    """

    def __init__(self, loss_uniform: np.ndarray, candidates: np.ndarray, outcomes: dict) -> None:
        order = np.argsort(loss_uniform[candidates], kind="stable")
        self.u = loss_uniform[candidates][order]
        self.cum = {
            k: np.concatenate(([0], np.cumsum(v[candidates][order]))) for k, v in outcomes.items()
        }

    def lost(self, p: float) -> tuple[int, dict[str, int]]:
        k = int(np.searchsorted(self.u, p, side="left"))
        return k, {name: int(c[k]) for name, c in self.cum.items()}


def _one_replicate(
    config: Mapping[str, Any], seed: np.random.SeedSequence, variants: Sequence[Variant]
) -> list[dict]:
    pop = simulate_population(config, np.random.default_rng(seed))
    y = pop.y.astype(np.int64)
    y14 = pop.received_by(float(config["days"]) * 24.0).astype(np.int64)
    margin = float(config.get("decision_rule", {}).get("ship_if_lower_bound_above_relative", 0.01))
    outcomes = {"y": y, "y14": y14}
    arms = {1: pop.arm == 1, 0: pop.arm == 0}
    srm_assign = srm_check(
        {"treatment": int(arms[1].sum()), "control": int(arms[0].sum())},
        _ALLOCATION,
        level="assignment",
    )
    rendered, cell = pop.rendered, pop.in_affected_cell
    truth_rendered = pop.expected_relative_lift(rendered)
    truth_excl = pop.expected_relative_lift(rendered & ~cell)
    truth_itt = pop.expected_relative_lift()
    dilution = float(pop.p_control[rendered].sum() / pop.p_control.sum())

    # Loss-independent counts per arm.
    base = {}
    for a, in_arm in arms.items():
        r = in_arm & rendered
        rx = r & ~cell
        base[a] = {
            "n_all": int(in_arm.sum()),
            "y_all": int(y[in_arm].sum()),
            "n_r": int(r.sum()),
            "y_r": int(y[r].sum()),
            "y14_r": int(y14[r].sum()),
            "n_rx": int(rx.sum()),
            "y_rx": int(y[rx].sum()),
            "loss": _LossCounts(pop.loss_uniform, r & cell, outcomes),
        }

    def estimate(name: str, s_t: int, n_t: int, s_c: int, n_c: int) -> Any:
        return difference_in_proportions(
            s_t, n_t, s_c, n_c, estimand=name, analysis_unit="user", population=name
        )

    b1, b0 = base[1], base[0]
    itt = estimate("itt_assigned", b1["y_all"], b1["n_all"], b0["y_all"], b0["n_all"])
    excl = estimate("exposed_excluding_cell", b1["y_rx"], b1["n_rx"], b0["y_rx"], b0["n_rx"])
    no_loss = (0, {"y": 0, "y14": 0})
    rows: list[dict] = []
    for v in variants:
        hit = affected_arms(v.affects_arm)
        logged = {}
        for a in (0, 1):
            k, lost = base[a]["loss"].lost(v.loss_probability) if a in hit else no_loss
            logged[a] = {
                "n": base[a]["n_r"] - k,
                "y": base[a]["y_r"] - lost["y"],
                "y14": base[a]["y14_r"] - lost["y14"],
            }
        l1, l0 = logged[1], logged[0]
        srm_expose = srm_check(
            {"treatment": l1["n"], "control": l0["n"]}, _ALLOCATION, level="exposure"
        )
        results = {
            "dashboard_day14": (
                estimate("dashboard_day14", l1["y14"], l1["n"], l0["y14"], l0["n"]),
                truth_rendered,
            ),
            "exposed_complete": (
                estimate("exposed_complete", l1["y"], l1["n"], l0["y"], l0["n"]),
                truth_rendered,
            ),
            "exposed_excluding_cell": (excl, truth_excl),
            "itt_assigned": (itt, truth_itt),
        }
        for name in ESTIMATORS:
            res, truth = results[name]
            lo, hi = res.relative_confidence_interval
            rows.append(
                {
                    "variant": v.name,
                    "estimator": name,
                    "true_effect": truth,
                    "true_effect_itt": truth_itt,
                    "estimate": res.relative_lift,
                    "ci_low": lo,
                    "ci_high": hi,
                    "p_value": res.p_value,
                    "rule_margin": margin,
                    "dilution": dilution if name in EXPOSURE_ESTIMATORS else 1.0,
                    "n_analysed": res.n_treatment + res.n_control,
                    "assignment_srm_p": srm_assign.p_value,
                    "exposure_srm_p": srm_expose.p_value,
                }
            )
    return rows


def _run_chunk(
    args: tuple[Mapping[str, Any], Sequence[np.random.SeedSequence], Sequence[Variant]],
) -> list[dict]:
    config, seeds, variants = args
    out = []
    for s in seeds:
        for row in _one_replicate(config, s, variants):
            row["replicate"] = int(s.spawn_key[-1])
            out.append(row)
    return out


def simulate_replicates(
    config: Mapping[str, Any],
    variants: Sequence[Variant],
    replicates: int,
    seed: int,
    *,
    executor: Executor | None = None,
    chunks: int = 1,
) -> pd.DataFrame:
    """Long table: one row per replicate x variant x estimator.

    Deterministic for a given seed whatever the executor or number of chunks: replicate i
    always uses child i of ``SeedSequence(seed)``.
    """
    if replicates < 2:
        raise ValueError("replicates must be at least 2 for a Monte Carlo standard error")
    if not variants:
        raise ValueError("at least one variant is required")
    children = np.random.SeedSequence(seed).spawn(replicates)
    k = max(1, min(chunks, replicates))
    parts = [(config, children[i::k], tuple(variants)) for i in range(k)]
    if executor is None:
        rows = [r for part in map(_run_chunk, parts) for r in part]
    else:
        rows = [r for part in executor.map(_run_chunk, parts) for r in part]
    return pd.DataFrame(rows).sort_values(["variant", "replicate", "estimator"], ignore_index=True)


def _rate(x: pd.Series) -> dict[str, float]:
    """Rate, Monte Carlo SE and Clopper-Pearson 95% interval of a boolean series (NaN when
    the series is empty)."""
    b, k = int(x.size), int(x.sum())
    if b == 0:
        return {"": math.nan, "_mc_se": math.nan, "_ci_low": math.nan, "_ci_high": math.nan}
    r = k / b
    lo = 0.0 if k == 0 else float(stats.beta.ppf(0.025, k, b - k + 1))
    hi = 1.0 if k == b else float(stats.beta.ppf(0.975, k + 1, b - k))
    return {"": r, "_mc_se": math.sqrt(r * (1.0 - r) / b), "_ci_low": lo, "_ci_high": hi}


def _mean(x: pd.Series) -> dict[str, float]:
    if x.size < 2:
        return {"": float(x.mean()) if x.size else math.nan, "_mc_se": math.nan}
    return {"": float(x.mean()), "_mc_se": float(x.std(ddof=1) / math.sqrt(x.size))}


def _replicate_flags(reps: pd.DataFrame) -> pd.DataFrame:
    """Per-replicate indicator columns derived from the stored estimates and intervals."""
    r = reps.copy()
    r["covered"] = (r["ci_low"] <= r["true_effect"]) & (r["true_effect"] <= r["ci_high"])
    r["covered_itt"] = (r["ci_low"] <= r["true_effect_itt"]) & (
        r["true_effect_itt"] <= r["ci_high"]
    )
    r["rejected"] = r["p_value"] < 0.05
    r["rejected_positive"] = r["rejected"] & (r["estimate"] > 0)
    r["rule_ship"] = r["ci_low"] > r["rule_margin"]
    r["rule_ship_itt_scale"] = r["ci_low"] * r["dilution"] > r["rule_margin"]
    r["err"] = r["estimate"] - r["true_effect"]
    r["err_itt"] = r["estimate"] - r["true_effect_itt"]
    for level in ("assignment", "exposure"):
        for alpha in (0.001, 0.05):
            r[f"{level}_srm_{alpha:g}"] = r[f"{level}_srm_p"] < alpha
    return r


_RATE_COLUMNS = (
    ("covered", "coverage"),
    ("covered_itt", "coverage_vs_itt"),
    ("rejected", "rejection_rate"),
    ("rejected_positive", "positive_rejection_rate"),
    ("rule_ship", "rule_ship_rate"),
    ("rule_ship_itt_scale", "rule_ship_rate_itt_scale"),
    ("assignment_srm_0.001", "assignment_srm_rate_0.001"),
    ("assignment_srm_0.05", "assignment_srm_rate_0.05"),
    ("exposure_srm_0.001", "exposure_srm_rate_0.001"),
    ("exposure_srm_0.05", "exposure_srm_rate_0.05"),
)


def summarise(reps: pd.DataFrame) -> pd.DataFrame:
    """Per variant x estimator operating characteristics with Monte Carlo uncertainty.

    Replicates whose estimate is undefined (a zero rate in an arm) are counted in
    ``n_undefined`` and excluded from every statistic, so all statistics share one
    denominator, ``B``; with fewer than two defined estimates the Monte Carlo standard
    errors are NaN.
    """
    flags = _replicate_flags(reps)
    rows = []
    for (variant, name), g_all in flags.groupby(["variant", "estimator"], sort=False):
        g = g_all[g_all["estimate"].notna()]
        row: dict[str, Any] = {
            "variant": variant,
            "estimator": name,
            "B": len(g),
            "n_undefined": len(g_all) - len(g),
            "true_effect": g["true_effect"].mean(),
            "true_effect_itt": g["true_effect_itt"].mean(),
            "mean_n_analysed": g["n_analysed"].mean(),
        }
        for col, label in (
            ("estimate", "mean_estimate"),
            ("err", "bias"),
            ("err_itt", "bias_vs_itt"),
        ):
            for suffix, value in _mean(g[col]).items():
                row[label + suffix] = value
        for col, label in _RATE_COLUMNS:
            for suffix, value in _rate(g[col]).items():
                row[label + suffix] = value
        rows.append(row)
    return pd.DataFrame(rows)


def paired_contrasts(
    reps: pd.DataFrame,
    contrasts: Iterable[tuple[str, tuple[str, str], tuple[str, str], str]],
) -> pd.DataFrame:
    """Differences between two (variant, estimator) cells over the same replicates.

    Each contrast is ``(label, (variant_a, estimator_a), (variant_b, estimator_b), metric)``
    with ``metric`` a per-replicate column of ``_replicate_flags`` (``rejected``,
    ``rule_ship``, ``rule_ship_itt_scale``, ``covered``, ``err``, ...). The Monte Carlo SE
    is sd(a - b) / sqrt(B) on the replicates where both estimates are defined; common random
    numbers make it smaller than the unpaired SE, which is reported beside it.
    """
    flags = _replicate_flags(reps).set_index(["variant", "estimator", "replicate"]).sort_index()
    rows = []
    for label, (va, ea), (vb, eb), metric in contrasts:
        a = flags.loc[(va, ea)]
        b = flags.loc[(vb, eb)]
        ok = a["estimate"].notna() & b["estimate"].notna()
        xa = a.loc[ok, metric].astype(float)
        xb = b.loc[ok, metric].astype(float)
        d = xa - xb
        rows.append(
            {
                "contrast": label,
                "metric": metric,
                "a": f"{va}/{ea}",
                "b": f"{vb}/{eb}",
                "B": int(ok.sum()),
                "a_value": float(xa.mean()),
                "b_value": float(xb.mean()),
                "difference": float(d.mean()),
                "paired_mc_se": float(d.std(ddof=1) / math.sqrt(d.size)),
                "unpaired_mc_se": float(
                    math.sqrt(xa.var(ddof=1) / d.size + xb.var(ddof=1) / d.size)
                ),
            }
        )
    return pd.DataFrame(rows)


def case_story(reps: pd.DataFrame, variant: str = "D", srm_alpha: float = 0.001) -> pd.DataFrame:
    """How often one replicate shows the case's story, and what such replicates look like.

    The story (the seed-selection rule in config.yaml): the day-14 dashboard clears the ship
    rule, the complete-data ITT interval lands in the continue zone (lower bound at or below
    the margin, upper bound at or above zero), and the assignment SRM check passes. Returns
    one row: the story rate with its Monte Carlo SE and Clopper-Pearson interval, the
    conditional rate of the ITT continue zone given a day-14 ship, and the mean estimates
    among story replicates beside their means over all replicates.
    """
    sub = reps[reps["variant"] == variant]
    wide = sub.pivot(
        index="replicate", columns="estimator", values=["estimate", "ci_low", "ci_high"]
    )
    first = sub.groupby("replicate").first()
    margin = float(first["rule_margin"].iloc[0])
    dash_ship = wide[("ci_low", "dashboard_day14")] > margin
    itt_extend = (wide[("ci_low", "itt_assigned")] <= margin) & (
        wide[("ci_high", "itt_assigned")] >= 0
    )
    srm_pass = first["assignment_srm_p"] >= srm_alpha
    story = dash_ship & itt_extend & srm_pass
    rate = _rate(story)
    est = wide["estimate"]
    gap = est["exposed_complete"] - est["itt_assigned"]
    row = {
        "variant": variant,
        "B": int(story.size),
        "story_rate": rate[""],
        "story_rate_mc_se": rate["_mc_se"],
        "story_rate_ci_low": rate["_ci_low"],
        "story_rate_ci_high": rate["_ci_high"],
        "dashboard_ship_rate": float(dash_ship.mean()),
        "itt_extend_given_dashboard_ship": float(itt_extend[dash_ship].mean()),
        "mean_dashboard_day14_story": float(est.loc[story, "dashboard_day14"].mean()),
        "mean_dashboard_day14_all": float(est["dashboard_day14"].mean()),
        "mean_itt_story": float(est.loc[story, "itt_assigned"].mean()),
        "mean_itt_all": float(est["itt_assigned"].mean()),
        "mean_gap_exposed_minus_itt_story": float(gap[story].mean()),
        "mean_gap_exposed_minus_itt_all": float(gap.mean()),
    }
    return pd.DataFrame([row])


def default_jobs() -> int:
    """Worker processes: the CPUs this process may use, capped at 61 on Windows."""
    count = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    return max(1, min(count, 61 if os.name == "nt" else 1024))


def scenario_variants(
    config: Mapping[str, Any], keys: Sequence[str]
) -> list[tuple[bool, list[Variant]]]:
    """Group scenarios by population (null or effect) so each population is drawn once."""
    loss = float(config["incident"]["exposure_loss_probability"])
    grouped: dict[bool, list[Variant]] = {}
    for key in keys:
        s = SCENARIOS[key]
        grouped.setdefault(s.null, []).append(Variant(key, loss if s.loss else 0.0, s.affects_arm))
    return list(grouped.items())


def _estimator_rank(estimator: pd.Series) -> pd.Series:
    return estimator.map({e: i for i, e in enumerate(ESTIMATORS)})


def scenario_summary(
    replicates: pd.DataFrame, lift: float, loss: float, order: Sequence[str] | None = None
) -> pd.DataFrame:
    """``summarise`` for scenario replicates, with each scenario's settings as columns."""
    reps = replicates[replicates["variant"].isin(list(SCENARIOS))]
    summary = summarise(reps).rename(columns={"variant": "scenario"})
    spec = summary["scenario"].map(SCENARIOS.__getitem__)
    summary.insert(1, "description", spec.map(lambda s: s.label))
    summary.insert(2, "tau_rendered", spec.map(lambda s: 0.0 if s.null else lift))
    summary.insert(3, "loss_probability", spec.map(lambda s: loss if s.loss else 0.0))
    summary.insert(4, "affects_arm", spec.map(lambda s: s.affects_arm))
    rank = {k: i for i, k in enumerate(order or SCENARIOS)}
    summary["_rank"] = summary["scenario"].map(rank) * 10 + _estimator_rank(summary["estimator"])
    return summary.sort_values("_rank").drop(columns="_rank").reset_index(drop=True)


def loss_variant(p: float, affects_arm: str) -> Variant:
    """The loss-sweep variant for probability ``p``; its name encodes the probability."""
    return Variant(f"loss={float(p)!r}", float(p), affects_arm)


def loss_summary(replicates: pd.DataFrame) -> pd.DataFrame:
    """``summarise`` for the loss-sweep variants of a replicate table, one row per value."""
    reps = replicates[replicates["variant"].str.startswith("loss=")]
    summary = summarise(reps)
    summary.insert(0, "parameter", LOSS_KEY)
    summary.insert(1, "value", summary.pop("variant").str.removeprefix("loss=").map(float))
    summary["_rank"] = _estimator_rank(summary["estimator"])
    return summary.sort_values(["value", "_rank"]).drop(columns="_rank").reset_index(drop=True)


def run_scenarios(
    config: Mapping[str, Any],
    B: int,  # noqa: N803  (B is the conventional name for the number of replicates)
    seed: int,
    *,
    lift: float | None = None,
    scenarios: Sequence[str] = ("A", "B", "C", "D", "F"),
    executor: Executor | None = None,
    chunks: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Scenarios A to D and F (spec section 10). Returns (summary, replicates).

    ``lift`` is the true relative lift among rendered users for the effect scenarios; it
    defaults to the config's ``true_relative_lift``. The summary has one row per scenario x
    estimator; the long replicate table feeds ``paired_contrasts`` and ``case_story``.
    """
    unknown = set(scenarios) - set(SCENARIOS)
    if unknown:
        raise ValueError(f"unknown scenarios {sorted(unknown)}; known {sorted(SCENARIOS)}")
    effect = float(config["true_relative_lift"] if lift is None else lift)
    reps = []
    for null, variants in scenario_variants(config, scenarios):
        cfg = scenario_config(config, lift=0.0 if null else effect)
        reps.append(simulate_replicates(cfg, variants, B, seed, executor=executor, chunks=chunks))
    replicates = pd.concat(reps, ignore_index=True)
    loss = float(config["incident"]["exposure_loss_probability"])
    summary = scenario_summary(replicates, effect, loss, order=scenarios)
    return summary, replicates


def run_sweep(
    config: Mapping[str, Any],
    parameter: str,
    values: Iterable[float],
    B: int,  # noqa: N803
    seed: int,
    *,
    lift: float | None = None,
    executor: Executor | None = None,
    chunks: int = 1,
) -> pd.DataFrame:
    """Scenario D (effect, incident on) at each value of one parameter in ``SWEEPABLE``.

    A sweep over the loss probability is evaluated on one population per replicate (the
    loss only changes which render events are logged); any other parameter redraws the
    population at each value from the same seeds.
    """
    if parameter not in SWEEPABLE:
        raise ValueError(f"cannot sweep {parameter!r}; sweepable keys are {sorted(SWEEPABLE)}")
    values = [float(v) for v in values]
    if not values:
        raise ValueError("values is empty")
    effect = float(config["true_relative_lift"] if lift is None else lift)
    affects = str(config["incident"]["affects_arm"])
    if parameter == LOSS_KEY:
        cfg = scenario_config(config, lift=effect)
        variants = [loss_variant(v, affects) for v in values]
        reps = simulate_replicates(cfg, variants, B, seed, executor=executor, chunks=chunks)
        return loss_summary(reps)
    frames = []
    loss = float(config["incident"]["exposure_loss_probability"])
    for value in values:
        cfg = scenario_config(config, lift=effect, overrides={parameter: value})
        reps = simulate_replicates(
            cfg, [Variant("D", loss, affects)], B, seed, executor=executor, chunks=chunks
        )
        summary = summarise(reps).drop(columns="variant")
        summary.insert(0, "parameter", parameter)
        summary.insert(1, "value", value)
        frames.append(summary)
    return pd.concat(frames, ignore_index=True)
