"""Run the Case 02 scenario table of spec section 10 on the finite population.

Truths are computed before any replicate, and every rate is reported with its Monte Carlo standard
error.
"""

import json
import math
import platform
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import scipy

from abforensics.diagnostics.srm import srm_check
from abforensics.inference import difference_in_means
from abforensics.inference.resampling import cluster_bootstrap
from abforensics.metrics.ratio import _Contrast, naive_event_rate, ratio_of_sums_arrays
from abforensics.simulation import ranking_incident as ri
from abforensics.simulation.operating_characteristics import _mean, _rate

SCENARIOS = ("A", "B", "C", "D", "C_at_52500", "D_leaked_tier")
ESTIMATORS = (
    "naive_e2",
    "delta_e2",
    "jackknife_e2",
    "bootstrap_e2",
    "welch_e3",
    "welch_e1",
    "welch_guardrail",
)
ESTIMAND = {
    "naive_e2": "E2",
    "delta_e2": "E2",
    "jackknife_e2": "E2",
    "bootstrap_e2": "E2",
    "welch_e3": "E3",
    "welch_e1": "E1",
    "welch_guardrail": "guardrail",
}
TRUTH_KEY = {
    "E2": "expected_relative_lift_ctr",
    "E3": "expected_relative_lift_user_rate",
    "E1": "expected_relative_lift_clicks_per_user",
    "guardrail": "expected_relative_lift_feedback_per_user",
}


@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    prm: ri.RankingParameters
    tier: str
    total_draws: int


def scenario_specs(config) -> dict[str, ScenarioSpec]:
    if list(config["operating_characteristics"]["scenarios"]) != list(SCENARIOS):
        raise ValueError("operating_characteristics.scenarios must match SCENARIOS")
    total = int(config["design"]["total_draws"])
    specs = {s: ScenarioSpec(s, ri.parameters_from_config(config, s), "age", total) for s in "ABCD"}
    specs["C_at_52500"] = ScenarioSpec(
        "C_at_52500", specs["C"].prm, "age", int(config["design"]["contrast_total_draws"])
    )
    specs["D_leaked_tier"] = ScenarioSpec("D_leaked_tier", specs["D"].prm, "leaked", total)
    return specs


def replicate_seed(oc_seed: int, r: int) -> int:
    if not 0 <= r < 100_000:
        raise ValueError("replicate index must be in [0, 100000)")
    return int(oc_seed) * 100_000 + r


def guardrail_verdict(lo, hi, margin) -> str:
    if hi <= margin:
        return "pass"
    if lo > margin:
        return "breach"
    return "inconclusive"


def rule_row(e1_lo, e1_hi, guardrail, ship_margin) -> int:
    if guardrail == "breach":
        return 1
    if guardrail == "inconclusive":
        return 2
    if e1_hi < 0:
        return 3
    if e1_lo > ship_margin:
        return 4
    return 5


def bootstrap_relative_lift(c_t, m_t, c_c, m_c, seed, resamples):
    cols = {"num_t": "c_t", "den_t": "m_t", "num_c": "c_c", "den_c": "m_c"}
    n_t, n_c = len(c_t), len(c_c)
    wide = pd.DataFrame(
        {
            "draw": np.arange(n_t + n_c),
            "__arm": np.r_[np.repeat("treatment", n_t), np.repeat("control", n_c)],
            "c_t": np.r_[c_t, np.zeros(n_c)],
            "m_t": np.r_[m_t, np.zeros(n_c)],
            "c_c": np.r_[np.zeros(n_t), c_c],
            "m_c": np.r_[np.zeros(n_t), m_c],
        }
    )
    return cluster_bootstrap(
        wide,
        "draw",
        _Contrast(cols, "relative lift"),
        resamples,
        seed,
        estimand="E2",
        interval="normal",
        strata_col="__arm",
    )


def evaluate_replicate(
    base,
    g,
    spec,
    r,
    oc_seed,
    *,
    ship_margin,
    guardrail_margin,
    reread_margin,
    bootstrap,
    resamples,
    seed_override=None,
) -> list[dict]:
    seed = replicate_seed(oc_seed, r) if seed_override is None else int(seed_override)
    dr = ri.simulate_draws(base.with_tier(spec.tier), spec.prm, seed, spec.total_draws, g)
    t = dr.arm == 1
    c, m, f = (np.asarray(a, dtype=np.float64) for a in (dr.clicks, dr.inviews, dr.feedback))
    results = {}
    results["naive_e2"] = naive_event_rate(
        int(c[t].sum()),
        int(m[t].sum()),
        int(c[~t].sum()),
        int(m[~t].sum()),
        alpha=0.05,
        estimand="E2",
    )
    for method in ("delta", "jackknife"):
        results[f"{method}_e2"] = ratio_of_sums_arrays(
            c[t],
            m[t],
            c[~t],
            m[~t],
            estimand="E2",
            numerator="clicks",
            denominator="inviews",
            unit_col="draw",
            alpha=0.05,
            method=method,
        )
    if bootstrap:
        results["bootstrap_e2"] = bootstrap_relative_lift(c[t], m[t], c[~t], m[~t], seed, resamples)
    results["welch_e3"] = difference_in_means(
        c[t] / m[t], c[~t] / m[~t], alpha=0.05, estimand="E3", analysis_unit="draw"
    )
    results["welch_e1"] = difference_in_means(
        c[t], c[~t], alpha=0.05, estimand="E1", analysis_unit="draw"
    )
    results["welch_guardrail"] = difference_in_means(
        f[t], f[~t], alpha=0.05, estimand="guardrail", analysis_unit="draw"
    )
    srm_p = srm_check(
        {"treatment": int(t.sum()), "control": int((~t).sum())},
        {"control": 0.5, "treatment": 0.5},
        level="assignment",
    ).p_value
    guardrail, e1 = results["welch_guardrail"], results["welch_e1"]
    gv5 = guardrail_verdict(*guardrail.relative_confidence_interval, guardrail_margin)
    gv3 = guardrail_verdict(*guardrail.relative_confidence_interval, reread_margin)
    row = rule_row(*e1.relative_confidence_interval, gv5, ship_margin)
    rows = []
    for estimator, res in results.items():
        if estimator == "bootstrap_e2":
            estimate, lo, hi, p = res.estimate, res.lower, res.upper, math.nan
        else:
            estimate = res.relative_lift
            lo, hi = res.relative_confidence_interval
            p = res.p_value
        rows.append(
            dict(
                scenario=spec.name,
                replicate=r,
                estimator=estimator,
                estimand=ESTIMAND[estimator],
                estimate=estimate,
                ci_low=lo,
                ci_high=hi,
                p_value=p,
                n_treatment=int(t.sum()),
                n_control=int((~t).sum()),
                srm_p=srm_p,
                guardrail_verdict_5=gv5,
                guardrail_verdict_3=gv3,
                rule_row=row,
            )
        )
    return rows


_WORKER = {}


def _init_worker(raw_dir: str):
    _WORKER["base"] = ri.load_base(Path(raw_dir))


def _run_chunk(args):
    base_or_none, config, oc_seed, replicates_chunk, specs, settings = args
    base = base_or_none if base_or_none is not None else _WORKER["base"]
    g = ri.guardrail_multipliers(base, specs["A"].prm)
    rows = []
    for r in replicates_chunk:
        for scenario in SCENARIOS:
            rows.extend(
                evaluate_replicate(
                    base,
                    g,
                    specs[scenario],
                    r,
                    oc_seed,
                    ship_margin=settings["ship_margin"],
                    guardrail_margin=settings["guardrail_margin"],
                    reread_margin=settings["reread_margin"],
                    resamples=settings["resamples"],
                    bootstrap=scenario in settings["bootstrap_scenarios"]
                    and r < settings["bootstrap_replicates"],
                )
            )
    return rows


def summarise(rows: pd.DataFrame, truths: dict[str, dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = rows.copy()
    rows["true_effect"] = [
        truths[s][TRUTH_KEY[e]] for s, e in zip(rows.scenario, rows.estimand, strict=True)
    ]
    rows["covers"] = (rows.ci_low <= rows.true_effect) & (rows.true_effect <= rows.ci_high)
    rows["reject"] = np.where(
        rows.estimator == "bootstrap_e2",
        (rows.ci_low > 0) | (rows.ci_high < 0),
        rows.p_value < 0.05,
    )

    def add(d, name, values, rate=True):
        stats = _rate(values) if rate else _mean(values)
        for suffix, value in stats.items():
            d[name + suffix] = value

    scenarios, rules = [], []
    for scenario in SCENARIOS:
        for estimator in ESTIMATORS:
            group = rows[(rows.scenario == scenario) & (rows.estimator == estimator)]
            if group.empty:
                continue
            estimand = ESTIMAND[estimator]
            d = dict(
                scenario=scenario,
                estimator=estimator,
                estimand=estimand,
                B=len(group),
                true_effect=truths[scenario][TRUTH_KEY[estimand]],
            )
            add(d, "bias", group.estimate - group.true_effect, rate=False)
            add(d, "coverage", group.covers)
            add(d, "rejection", group.reject)
            scenarios.append(d)
        group = rows[(rows.scenario == scenario) & (rows.estimator == "welch_e1")]
        d = dict(scenario=scenario, B=len(group))
        for k in range(1, 6):
            stats = _rate(group.rule_row == k)
            d[f"row{k}"], d[f"row{k}_mc_se"] = stats[""], stats["_mc_se"]
        for name, values in (
            ("guardrail_pass_5", group.guardrail_verdict_5 == "pass"),
            ("guardrail_pass_3", group.guardrail_verdict_3 == "pass"),
            ("srm_flag", group.srm_p < 0.001),
        ):
            stats = _rate(values)
            d[name], d[name + "_mc_se"] = stats[""], stats["_mc_se"]
        rules.append(d)
    return pd.DataFrame(scenarios), pd.DataFrame(rules)


def story_holds(replicate_rows: pd.DataFrame) -> bool:
    naive = replicate_rows[replicate_rows.estimator == "naive_e2"].iloc[0]
    delta = replicate_rows[replicate_rows.estimator == "delta_e2"].iloc[0]
    return bool(
        naive.p_value < 0.05
        and delta.p_value < 0.05
        and delta.rule_row == 5
        and delta.guardrail_verdict_5 == "pass"
        and delta.srm_p >= 0.001
    )


def run_oc_case02(
    config, base, *, replicates, oc_seed, workers, out_dir, raw_dir=None, chunks=None
) -> dict:
    specs = scenario_specs(config)
    oc, rule = config["operating_characteristics"], config["decision_rule"]
    settings = dict(
        bootstrap_scenarios=oc["bootstrap"]["scenarios"],
        bootstrap_replicates=oc["bootstrap"]["replicates"],
        resamples=oc["bootstrap"]["resamples"],
        ship_margin=rule["ship_if_lower_bound_above_relative"],
        guardrail_margin=rule["guardrails"]["negative_feedback_max_increase_relative"],
        reread_margin=rule["sensitivity_reread_margin_relative"],
    )
    if workers > 1 and raw_dir is None:
        raise ValueError("raw_dir is required for multiple workers")
    start = time.perf_counter()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    g = ri.guardrail_multipliers(base, specs["A"].prm)
    truths, truth_rows = {}, []
    for spec in specs.values():
        truths[spec.name] = ri.scenario_truths(
            ri.potential_outcomes(base.with_tier(spec.tier), spec.prm, g)
        )
        truth_rows.append(
            dict(
                scenario=spec.name,
                tier=spec.tier,
                delta=spec.prm.delta,
                tau=spec.prm.tau,
                total_draws=spec.total_draws,
                **truths[spec.name],
            )
        )
    pd.DataFrame(truth_rows).to_csv(out_dir / "oc_truths.csv", index=False, lineterminator="\n")
    k = chunks or max(1, 4 * workers)
    replicate_chunks = [list(range(replicates))[i::k] for i in range(k)]
    args = [
        (base if workers == 1 else None, config, oc_seed, chunk, specs, settings)
        for chunk in replicate_chunks
    ]
    if workers == 1:
        parts = list(map(_run_chunk, args))
    else:
        with ProcessPoolExecutor(
            max_workers=workers, initializer=_init_worker, initargs=(str(raw_dir),)
        ) as executor:
            parts = list(executor.map(_run_chunk, args))
    rows = pd.DataFrame([row for part in parts for row in part]).sort_values(
        ["scenario", "replicate", "estimator"], ignore_index=True
    )
    rows.to_parquet(out_dir / "oc_replicates.parquet", index=False)
    scenarios, rules = summarise(rows, truths)
    scenarios.to_csv(out_dir / "oc_scenarios.csv", index=False, lineterminator="\n")
    rules.to_csv(out_dir / "oc_rule.csv", index=False, lineterminator="\n")
    story = _rate(
        pd.Series(
            [story_holds(group) for _, group in rows[rows.scenario == "D"].groupby("replicate")]
        )
    )
    joint = _rate(rows[(rows.scenario == "C") & (rows.estimator == "welch_e1")].rule_row == 4)
    wall = time.perf_counter() - start
    limit = oc["time_limit_minutes"]
    meta = dict(
        case="case02",
        oc_seed=oc_seed,
        replicates=replicates,
        workers=workers,
        chunks=k,
        bootstrap=settings,
        scenarios=list(SCENARIOS),
        wall_clock_seconds=wall,
        time_limit_minutes=limit,
        within_time_limit=wall <= 60 * limit,
        story_rate_D=dict(rate=story[""], mc_se=story["_mc_se"]),
        joint_ship_rate_check=dict(
            rate=joint[""],
            mc_se=joint["_mc_se"],
            target=0.80,
            tolerance=0.025,
            within_tolerance=abs(joint[""] - 0.80) <= 0.025,
        ),
        python=platform.python_version(),
        numpy=np.__version__,
        pandas=pd.__version__,
        scipy=scipy.__version__,
    )
    if "raw_md5" in base.summary:
        meta["raw_md5"] = base.summary["raw_md5"]
    (out_dir / "oc_meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return meta
