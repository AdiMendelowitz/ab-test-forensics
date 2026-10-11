"""Case 02 sweeps of spec section 10 and ADR-07 items 5 and 6.

Rebuild every parametric point, share replicate seeds, and write aggregates only.
Runtime measurements run sequentially after the worker pool has finished.
"""

import gc
import json
import platform
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import scipy

from abforensics.metrics.ratio import ratio_of_sums_arrays
from abforensics.simulation import oc_case02 as oc
from abforensics.simulation import ranking_incident as ri
from abforensics.simulation.oc_case02 import bootstrap_relative_lift

MARKED_SIZES = (15143, 52500, 57600, 64100)
METHODS = ("delta", "jackknife", "bootstrap_normal", "bootstrap_percentile")


def _required(mapping, key, prefix=""):
    if key not in mapping:
        raise ValueError(f"missing key: {prefix}{key}")
    return mapping[key]


def _settings(config):
    operating = _required(config, "operating_characteristics")
    sweeps = _required(operating, "sweeps", "operating_characteristics.")
    for key in (
        "replicates",
        "time_limit_minutes",
        "parametric_n_per_arm",
        "heterogeneity_sigma",
        "tail_dispersion_r",
        "sample_size_per_arm",
        "heterogeneity_depth_off_point",
        "bootstrap",
        "tail_bootstrap",
        "wallclock_percentile_bootstrap",
    ):
        _required(sweeps, key, "sweeps.")
    depth = sweeps["heterogeneity_depth_off_point"]
    if _required(depth, "sigma", "sweeps.heterogeneity_depth_off_point.") != 0.0:
        raise ValueError("sweeps.heterogeneity_depth_off_point.sigma must be 0.0")
    _required(depth, "depth_term", "sweeps.heterogeneity_depth_off_point.")
    for block, keys in (
        ("bootstrap", ("scenarios", "replicates_per_size", "resamples", "interval")),
        ("tail_bootstrap", ("r", "replicates", "resamples", "interval")),
        ("wallclock_percentile_bootstrap", ("replicates_per_size", "resamples", "interval")),
    ):
        for key in keys:
            _required(sweeps[block], key, f"sweeps.{block}.")
    generator = _required(config, "parametric_generator")
    for key in (
        "population_users",
        "mean_inviews",
        "sd_inviews",
        "minimum_inviews",
        "old_shares",
        "old_share_concentration",
        "population_seed",
        "theta",
    ):
        _required(generator, key, "parametric_generator.")
    for key in ("a", "b", "sigma", "gamma"):
        _required(generator["theta"], key, "parametric_generator.theta.")
    return sweeps, generator


def _tasks(config, size_chunks):
    sweeps, _ = _settings(config)
    if size_chunks < 1:
        raise ValueError("size_chunks must be at least 1")
    indices = list(range(int(sweeps["replicates"])))
    tasks = []
    for index, sigma in enumerate(sweeps["heterogeneity_sigma"]):
        tasks.append(dict(kind="heterogeneity", point=index, sigma=float(sigma), depth_term="on"))
    tasks.append(
        dict(
            kind="heterogeneity",
            point=len(sweeps["heterogeneity_sigma"]),
            sigma=0.0,
            depth_term="off",
        )
    )
    for index, dispersion in enumerate(sweeps["tail_dispersion_r"]):
        tasks.append(dict(kind="tail", point=index, dispersion_r=float(dispersion)))
    for task in tasks:
        task.update(
            scenario="A", replicates=indices, total_draws=2 * int(sweeps["parametric_n_per_arm"])
        )
    for index, size in enumerate(sweeps["sample_size_per_arm"]):
        for scenario in ("A", "C"):
            for chunk in range(size_chunks):
                tasks.append(
                    dict(
                        kind="size",
                        point=index,
                        scenario=scenario,
                        size_per_arm=int(size),
                        total_draws=2 * int(size),
                        marked="yes" if size in MARKED_SIZES else "no",
                        above_population="yes" if size > 15143 else "no",
                        replicates=indices[chunk::size_chunks],
                    )
                )
    for order, task in enumerate(tasks):
        task["order"] = order
    return sorted(tasks, key=lambda task: -task["total_draws"] * len(task["replicates"]))


def sweep_tasks(config):
    """Pure task plan, largest workloads first with stable ties in sweep order."""
    return _tasks(config, 4)


def expected_row_counts(config) -> dict[str, int]:
    """Expected aggregate rows from the sweep grids and bootstrap placement."""
    sweeps, _ = _settings(config)
    ordinary = len(oc.ESTIMATORS) - 1
    tail_bootstrap = sum(r in sweeps["tail_bootstrap"]["r"] for r in sweeps["tail_dispersion_r"])
    return {
        "oc_sweep_heterogeneity.csv": (len(sweeps["heterogeneity_sigma"]) + 1) * ordinary,
        "oc_sweep_tail.csv": len(sweeps["tail_dispersion_r"]) * ordinary + tail_bootstrap,
        "oc_sweep_size.csv": len(sweeps["sample_size_per_arm"]) * (2 * ordinary + 1),
        "oc_sweep_runtime.csv": len(sweeps["sample_size_per_arm"]) * len(METHODS),
    }


def _run_task(args):
    task, config, base_or_none, oc_seed, settings = args
    sweeps, generator = _settings(config)
    if task["kind"] == "size":
        base = base_or_none if base_or_none is not None else oc._WORKER["base"]
    else:
        overrides = (
            {"depth_off": True}
            if task["kind"] == "heterogeneity" and task["depth_term"] == "off"
            else {"sigma": task["sigma"]}
            if task["kind"] == "heterogeneity"
            else {"r": task["dispersion_r"]}
        )
        base = ri.make_parametric_base(generator, **overrides)
    prm = oc.scenario_specs(config)[task["scenario"]].prm
    spec = oc.ScenarioSpec(task["scenario"], prm, "age", task["total_draws"])
    g = ri.guardrail_multipliers(base, prm)
    truths = (
        None
        if task["kind"] == "size"
        else {"A": ri.scenario_truths(ri.potential_outcomes(base.with_tier("age"), prm, g))}
    )
    bootstrap_limit = 0
    resamples = sweeps["bootstrap"]["resamples"]
    if task["kind"] == "size" and task["scenario"] == "A":
        bootstrap_limit = sweeps["bootstrap"]["replicates_per_size"]
    elif task["kind"] == "tail" and task["dispersion_r"] in sweeps["tail_bootstrap"]["r"]:
        bootstrap_limit = sweeps["tail_bootstrap"]["replicates"]
        resamples = sweeps["tail_bootstrap"]["resamples"]
    rows = []
    for r in task["replicates"]:
        rows.extend(
            oc.evaluate_replicate(
                base,
                g,
                spec,
                r,
                oc_seed,
                **settings,
                bootstrap=r < bootstrap_limit,
                resamples=resamples,
            )
        )
    return task, rows, truths


def time_methods(c_t, m_t, c_c, m_c, seed, *, normal_resamples, percentile_resamples):
    """Seconds for one call of each method on one replicate's per-draw arrays."""
    seconds = {}
    for method in ("delta", "jackknife"):
        gc.collect()
        start = time.perf_counter()
        ratio_of_sums_arrays(
            c_t,
            m_t,
            c_c,
            m_c,
            estimand="E2",
            numerator="clicks",
            denominator="inviews",
            unit_col="draw",
            alpha=0.05,
            method=method,
        )
        seconds[method] = time.perf_counter() - start
    for name, resamples, interval in (
        ("bootstrap_normal", normal_resamples, "normal"),
        ("bootstrap_percentile", percentile_resamples, "percentile"),
    ):
        gc.collect()
        start = time.perf_counter()
        bootstrap_relative_lift(c_t, m_t, c_c, m_c, seed, resamples, interval=interval)
        seconds[name] = time.perf_counter() - start
    return seconds


def _runtime_table(base, prm, g, sweeps, oc_seed):
    sizes = sorted(sweeps["sample_size_per_arm"])
    budgets = dict(
        normal_resamples=sweeps["bootstrap"]["resamples"],
        percentile_resamples=sweeps["wallclock_percentile_bootstrap"]["resamples"],
    )

    def measure(size, r):
        seed = oc.replicate_seed(oc_seed, r)
        dr = ri.simulate_draws(base.with_tier("age"), prm, seed, 2 * size, g)
        c, m = (np.asarray(a, dtype=np.float64) for a in (dr.clicks, dr.inviews))
        t = dr.arm == 1
        return time_methods(c[t], m[t], c[~t], m[~t], seed, **budgets)

    measure(sizes[0], 0)
    replicates = int(sweeps["wallclock_percentile_bootstrap"]["replicates_per_size"])
    rows = []
    for size in sizes:
        measurements = [measure(size, r) for r in range(replicates)]
        for method in METHODS:
            seconds = [measurement[method] for measurement in measurements]
            rows.append(
                dict(
                    source="real_base_resampling",
                    size_per_arm=int(size),
                    total_draws=2 * int(size),
                    marked="yes" if size in MARKED_SIZES else "no",
                    method=method,
                    resamples=budgets["normal_resamples"]
                    if method == "bootstrap_normal"
                    else budgets["percentile_resamples"]
                    if method == "bootstrap_percentile"
                    else 0,
                    replicates=replicates,
                    seconds=";".join(str(value) for value in seconds),
                    median_seconds=float(np.median(seconds)),
                )
            )
    return pd.DataFrame(rows)


def _json_value(value):
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def run_sweeps_case02(
    config, base, *, oc_seed, workers, out_dir, raw_dir=None, fig_path=None, size_chunks=None
) -> dict:
    """Run one uninterrupted sweep; never cache, resume, or stop at the time limit."""
    start = time.perf_counter()
    sweeps, generator = _settings(config)
    if workers > 1 and raw_dir is None:
        raise ValueError("raw_dir is required for multiple workers")
    if workers < 1:
        raise ValueError("workers must be at least 1")
    chunks = 4 if size_chunks is None else size_chunks
    tasks = _tasks(config, chunks)
    specs = oc.scenario_specs(config)
    rule = config["decision_rule"]
    settings = dict(
        ship_margin=rule["ship_if_lower_bound_above_relative"],
        guardrail_margin=rule["guardrails"]["negative_feedback_max_increase_relative"],
        reread_margin=rule["sensitivity_reread_margin_relative"],
    )
    truths = {}
    for scenario in ("A", "C"):
        prm = specs[scenario].prm
        g = ri.guardrail_multipliers(base, prm)
        truths[scenario] = ri.scenario_truths(ri.potential_outcomes(base.with_tier("age"), prm, g))
    args = [(task, config, base if workers == 1 else None, oc_seed, settings) for task in tasks]
    parallel_start = time.perf_counter()
    if workers == 1:
        parts = list(map(_run_task, args))
    else:
        with ProcessPoolExecutor(
            max_workers=workers, initializer=oc._init_worker, initargs=(str(raw_dir),)
        ) as executor:
            parts = list(executor.map(_run_task, args))
    parallel_seconds = time.perf_counter() - parallel_start
    groups = {}
    for task, rows, point_truths in sorted(parts, key=lambda part: part[0]["order"]):
        key = task["kind"], task["point"], task["scenario"]
        if key not in groups:
            groups[key] = (task, [], point_truths)
        groups[key][1].extend(rows)
    tables = {kind: [] for kind in ("heterogeneity", "tail", "size")}
    for task, rows, point_truths in groups.values():
        frame = pd.DataFrame(rows).sort_values(["replicate", "estimator"], ignore_index=True)
        summary, rules = oc.summarise(
            frame,
            truths if point_truths is None else point_truths,
            scenario_names=[task["scenario"]],
        )
        if task["kind"] == "heterogeneity":
            prefix = dict(source="simulated", sigma=task["sigma"], depth_term=task["depth_term"])
        elif task["kind"] == "tail":
            prefix = dict(source="simulated", dispersion_r=task["dispersion_r"])
        else:
            prefix = dict(
                source="real_base_resampling",
                size_per_arm=task["size_per_arm"],
                total_draws=task["total_draws"],
                marked=task["marked"],
                above_population=task["above_population"],
            )
            summary["ship_rate"] = rules.iloc[0]["row4"]
            summary["ship_rate_mc_se"] = rules.iloc[0]["row4_mc_se"]
        for index, (key, value) in enumerate(prefix.items()):
            summary.insert(index, key, value)
        tables[task["kind"]].append(summary)
    tables = {kind: pd.concat(frames, ignore_index=True) for kind, frames in tables.items()}
    runtime_start = time.perf_counter()
    prm = specs["A"].prm
    tables["runtime"] = _runtime_table(
        base, prm, ri.guardrail_multipliers(base, prm), sweeps, oc_seed
    )
    runtime_seconds = time.perf_counter() - runtime_start
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    row_counts = {}
    for kind, table in tables.items():
        name = f"oc_sweep_{kind}.csv"
        table.to_csv(out_dir / name, index=False, lineterminator="\n")
        row_counts[name] = len(pd.read_csv(out_dir / name))
    expected = expected_row_counts(config)
    for name, count in expected.items():
        if row_counts[name] != count:
            raise ValueError(f"{name}: expected {count} rows, wrote {row_counts[name]}")
    if fig_path is not None:
        plot_case02_sweeps(
            tables["heterogeneity"],
            tables["tail"],
            tables["runtime"],
            fig_path,
            sweeps["replicates"],
        )
    wall = time.perf_counter() - start
    limit = sweeps["time_limit_minutes"]
    meta = dict(
        case="case02",
        command="abf oc case02 --sweeps",
        oc_seed=int(oc_seed),
        replicates=int(sweeps["replicates"]),
        workers=int(workers),
        tasks=len(tasks),
        size_chunks=int(chunks),
        sweeps_config=sweeps,
        theta=generator["theta"],
        marked_sizes=[int(size) for size in sweeps["sample_size_per_arm"] if size in MARKED_SIZES],
        row_counts=row_counts,
        parallel_seconds=parallel_seconds,
        runtime_seconds=runtime_seconds,
        wall_clock_seconds=wall,
        time_limit_minutes=limit,
        within_time_limit=wall <= 60 * limit,
        python=platform.python_version(),
        numpy=np.__version__,
        pandas=pd.__version__,
        scipy=scipy.__version__,
    )
    if "raw_md5" in base.summary:
        meta["raw_md5"] = base.summary["raw_md5"]
    meta = _json_value(meta)
    (out_dir / "oc_sweep_meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return meta


def plot_case02_sweeps(heterogeneity, tail, runtime, path, replicates):
    """Three panels separating simulated error sweeps from real-base runtime."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from abforensics.reporting.style import PALETTE, apply_style

    apply_style()
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.3))
    for axis, table, column, title in (
        (
            axes[0],
            heterogeneity[heterogeneity.depth_term == "on"],
            "sigma",
            "Naive Type I error against\nuser heterogeneity (simulated)",
        ),
        (
            axes[1],
            tail,
            "dispersion_r",
            "Naive Type I error against\ntail heaviness (simulated)",
        ),
    ):
        for estimator, colour, label in (
            ("naive_e2", PALETTE["treatment"], "Naive"),
            ("delta_e2", PALETTE["control"], "Delta"),
        ):
            group = table[table.estimator == estimator]
            x = group[column].to_numpy(dtype=float)
            y = 100 * group.rejection.to_numpy(dtype=float)
            band = 100 * 1.96 * group.rejection_mc_se.to_numpy(dtype=float)
            axis.plot(x, y, color=colour, label=label)
            axis.fill_between(x, y - band, y + band, color=colour, alpha=0.15)
            if column == "sigma":
                depth_off = heterogeneity[
                    (heterogeneity.depth_term == "off") & (heterogeneity.estimator == estimator)
                ]
                axis.scatter(
                    np.zeros(len(depth_off)),
                    100 * depth_off.rejection,
                    s=110 if estimator == "naive_e2" else 36,
                    facecolors="none",
                    edgecolors=colour,
                    linewidths=1.5,
                    zorder=3,
                    label="Depth term off (both methods)" if estimator == "naive_e2" else None,
                )
        axis.axhline(5, color=PALETTE["threshold"], linestyle="--", label="Nominal 5%")
        axis.set_title(title, fontsize=10)
        axis.set_ylabel("Type I error (%)")
        axis.legend()
    axes[0].set_xlabel("User heterogeneity sigma")
    axes[1].set_xlabel("Negative-binomial dispersion r (log scale)")
    axes[1].set_xscale("log")
    dispersions = sorted(float(v) for v in tail.dispersion_r.unique())
    axes[1].set_xticks(dispersions)
    axes[1].set_xticklabels([f"{v:g}" for v in dispersions])
    axes[1].minorticks_off()
    for method, colour in zip(METHODS, ("control", "treatment", "series3", "ink2"), strict=True):
        group = runtime[runtime.method == method]
        axes[2].plot(group.size_per_arm, group.median_seconds, color=PALETTE[colour], label=method)
    axes[2].axvline(57600, color=PALETTE["threshold"], linestyle=":", label="57,600 (design size)")
    axes[2].set_xscale("log")
    axes[2].set_yscale("log")
    axes[2].set_xticks([10_000, 25_000, 57_600, 100_000, 200_000])
    axes[2].set_xticklabels(["10k", "25k", "57.6k", "100k", "200k"])
    axes[2].minorticks_off()
    axes[2].set_title("Wall-clock time against\nsample size (real base, resampling)", fontsize=10)
    axes[2].set_xlabel("Sample size per arm (log scale)")
    axes[2].set_ylabel(
        f"Median wall-clock time (seconds, {int(runtime.replicates.iloc[0])} replicates)"
    )
    axes[2].set_ylim(top=1000)
    axes[2].legend(loc="upper left", ncol=2, fontsize=8, framealpha=1)
    fig.suptitle(f"Case 02 sweeps, B = {replicates} per point")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
