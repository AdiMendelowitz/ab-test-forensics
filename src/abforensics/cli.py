"""Command-line entry point: ``abf <command> <case>``.

Commands are added when the case that needs them has a written design spec.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

COMMANDS = ("simulate", "run", "oc")
CASES = {"case01": "01_lift_we_could_not_trust", "case02": "02_metric_that_told_two_stories"}


def repo_root() -> Path:
    """Locate the repository root by walking up from the current directory to pyproject.toml."""
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "case_studies").is_dir():
            return candidate
    raise SystemExit("abf must be run from inside the ab-test-forensics repository")


def load_config(root: Path, case: str) -> dict:
    path = root / "case_studies" / CASES[case] / "config.yaml"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def cmd_simulate(root: Path, case: str, seed: int | None, *, synthetic: bool = False) -> int:
    if synthetic and case != "case02":
        print("--synthetic is supported only by abf simulate case02", file=sys.stderr)
        return 2
    if case == "case02":
        return _cmd_simulate_case02(root, case, seed, synthetic=synthetic)
    from abforensics.simulation.checkout_incident import (
        simulate_checkout_experiment,
        write_case_data,
    )

    config = load_config(root, case)
    data = simulate_checkout_experiment(config, seed=seed)
    manifest = write_case_data(
        data,
        generated_dir=root / "data" / "generated" / case,
        manifest_path=root / "data" / "manifests" / f"{case}.json",
    )
    counts = ", ".join(f"{k}={v:,}" for k, v in manifest["row_counts"].items())
    print(f"{case}: seed={manifest['seed']} wrote data/generated/{case}/ ({counts})")
    print(f"manifest: data/manifests/{case}.json")
    return 0


def cmd_run(root: Path, case: str) -> int:
    """Execute the case notebook headless, in place, so the committed copy carries outputs."""
    import subprocess

    nb = root / "case_studies" / CASES[case] / "analysis.ipynb"
    if not nb.exists():
        raise SystemExit(f"notebook not found: {nb}")
    cmd = [
        sys.executable,
        "-m",
        "nbconvert",
        "--to",
        "notebook",
        "--execute",
        "--inplace",
        "--ExecutePreprocessor.timeout=900",
        str(nb),
    ]
    completed = subprocess.run(cmd, cwd=nb.parent, check=False)
    if completed.returncode == 0:
        normalise_line_endings(nb)
        print(f"{case}: executed {nb.relative_to(root)}")
    return completed.returncode


def normalise_line_endings(path: Path) -> None:
    """Rewrite ``path`` with LF line endings.

    nbconvert writes the notebook in text mode, so on Windows every line ends in CRLF. A notebook is
    JSON, whose strings escape newlines, so a raw CRLF can only be a line separator and the
    replacement leaves the content unchanged.
    """
    raw = path.read_bytes()
    if b"\r\n" in raw:
        path.write_bytes(raw.replace(b"\r\n", b"\n"))


OC_CONTRASTS = (
    (
        "C: exposed minus ITT, rejects zero",
        ("C", "exposed_complete"),
        ("C", "itt_assigned"),
        "rejected",
    ),
    (
        "C: exposed minus ITT, ship rule",
        ("C", "exposed_complete"),
        ("C", "itt_assigned"),
        "rule_ship",
    ),
    (
        "C: exposed (bound rescaled to the ITT lift) minus ITT, ship rule",
        ("C", "exposed_complete"),
        ("C", "itt_assigned"),
        "rule_ship_itt_scale",
    ),
    (
        "B minus A: dashboard rejects zero",
        ("B", "dashboard_day14"),
        ("A", "dashboard_day14"),
        "rejected",
    ),
    (
        "D minus C: dashboard ship rule",
        ("D", "dashboard_day14"),
        ("C", "dashboard_day14"),
        "rule_ship",
    ),
    (
        "D minus C: dashboard coverage",
        ("D", "dashboard_day14"),
        ("C", "dashboard_day14"),
        "covered",
    ),
    (
        "D: dashboard day 14 minus exposed complete, ship rule",
        ("D", "dashboard_day14"),
        ("D", "exposed_complete"),
        "rule_ship",
    ),
    (
        "D: segment-excluded minus ITT, rejects zero",
        ("D", "exposed_excluding_cell"),
        ("D", "itt_assigned"),
        "rejected",
    ),
    ("F minus C: exposed bias", ("F", "exposed_complete"), ("C", "exposed_complete"), "err"),
)


def _code_fingerprint() -> str:
    """SHA-256 of the source files whose behaviour determines a replicate's rows.

    Line endings are normalised, so a Windows checkout with CRLF gives the same hash.
    """
    import hashlib
    import inspect

    from abforensics import results
    from abforensics.diagnostics import srm
    from abforensics.inference import proportions
    from abforensics.simulation import checkout_incident, operating_characteristics

    h = hashlib.sha256()
    for module in (checkout_incident, operating_characteristics, proportions, srm, results):
        h.update(inspect.getsource(module).replace("\r\n", "\n").encode("utf-8"))
    return h.hexdigest()


def _read_sidecar(path: Path) -> dict | None:
    """The ``run`` record of a unit's sidecar, or None if it is missing or unreadable."""
    import json

    try:
        return dict(json.loads(path.read_text(encoding="utf-8")).get("run", {}))
    except (OSError, ValueError):
        return None


def _library_versions() -> dict[str, str]:
    import numpy
    import pandas
    import scipy

    return {"numpy": numpy.__version__, "scipy": scipy.__version__, "pandas": pandas.__version__}


def cmd_oc(
    root: Path,
    case: str,
    *,
    replicates: int | None = None,
    sweep_replicates: int | None = None,
    seed: int | None = None,
    jobs: int | None = None,
    max_seconds: float | None = None,
) -> int:
    """Operating characteristics: scenarios A to D and F, loss sweep, conversion-deficit sweep.

    Work is split into units, one per simulated population group: the null scenarios (A,
    B), the effect scenarios together with the loss sweep (C, D, F and every loss value, all
    on one population per replicate), and one unit per conversion-deficit value other than
    the configured offset, which reuses scenario D's first replicates. Each unit's
    replicate-level rows are cached under ``results/<case>/parts/`` in a file named by a
    hash of everything that determines them: the validated generator parameters (less the
    incident's loss settings, which the variants carry), the ship margin, the variants, B,
    the seed, the package and library versions, and the source of the simulation and
    estimation modules. A changed input produces a new unit, and an unrelated
    config edit does not force a recompute. An interrupted run resumes; ``--max-seconds``
    is checked between units, after at least one unit has been computed in the invocation, so
    every invocation makes progress and a run can overshoot the budget by one unit.
    """
    if case == "case02":
        return _cmd_oc_case02(
            root,
            case,
            replicates=replicates,
            sweep_replicates=sweep_replicates,
            seed=seed,
            jobs=jobs,
            max_seconds=max_seconds,
        )
    import hashlib
    import json
    import os
    import platform
    import time
    from concurrent.futures import ProcessPoolExecutor
    from dataclasses import asdict

    import pandas as pd

    from abforensics import __version__
    from abforensics.simulation.checkout_incident import validate_config
    from abforensics.simulation.operating_characteristics import (
        SCENARIOS,
        Variant,
        case_story,
        default_jobs,
        loss_summary,
        loss_variant,
        paired_contrasts,
        scenario_config,
        scenario_summary,
        scenario_variants,
        simulate_replicates,
        summarise,
    )

    config = load_config(root, case)
    oc = config["operating_characteristics"]
    b = int(replicates if replicates is not None else oc["replicates"])
    b_sweep = int(sweep_replicates if sweep_replicates is not None else oc["sweep_replicates"])
    seed = int(seed if seed is not None else oc["seed"])
    lift = float(oc.get("scenario_lift", config["true_relative_lift"]))
    n_jobs = int(jobs if jobs is not None else default_jobs())
    if min(b, b_sweep) < 2 or n_jobs < 1:
        raise SystemExit("replicates must be at least 2 and jobs at least 1")
    out = root / "results" / case
    parts = out / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    fingerprint = _code_fingerprint()
    versions = _library_versions()
    loss = float(config["incident"]["exposure_loss_probability"])
    affects = str(config["incident"]["affects_arm"])
    base_offset = float(validate_config(config).affected_cell_logit_offset)

    # Units: (name, config for the population, variants, B).
    units: list[tuple[str, dict, list, int]] = []
    loss_values = [float(v) for v in oc["loss_probability_sweep"]]
    for null, variants in scenario_variants(config, tuple(SCENARIOS)):
        if null:
            units.append(("scenarios_null", scenario_config(config, lift=0.0), variants, b))
        else:
            variants = variants + [loss_variant(v, affects) for v in loss_values]
            units.append(("scenarios_effect", scenario_config(config, lift=lift), variants, b))
    offset_values = [float(v) for v in oc["affected_cell_logit_offset_sweep"]]
    for v in offset_values:
        if v == base_offset and b_sweep <= b and affects == SCENARIOS["D"].affects_arm:
            continue  # identical to scenario D's first b_sweep replicates; reused below
        cfg = scenario_config(config, lift=lift, overrides={"affected_cell_logit_offset": v})
        units.append((f"offset_{v!r}", cfg, [Variant("D", loss, affects)], b_sweep))

    started = time.perf_counter()
    manifest: dict[str, dict] = {}
    frames: dict[str, pd.DataFrame] = {}
    computed = 0  # units computed in this invocation; at least one runs before any stop
    executor = ProcessPoolExecutor(max_workers=n_jobs) if n_jobs > 1 else None
    try:
        for i, (name, cfg, variants, bb) in enumerate(units, start=1):
            dgp = asdict(validate_config(cfg))
            dgp.pop("loss_probability")
            dgp.pop("affects_arm")
            spec = {
                "unit": name,
                "dgp": dgp,
                "ship_margin": cfg.get("decision_rule", {}).get(
                    "ship_if_lower_bound_above_relative", 0.01
                ),
                "variants": [vars(v) for v in variants],
                "replicates": bb,
                "seed": seed,
                "abforensics_version": __version__,
                "library_versions": versions,
                "code_sha256": fingerprint,
            }
            key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode("utf-8")).hexdigest()
            path = parts / f"{name}_{key[:16]}.parquet"
            sidecar = path.with_suffix(".json")
            cached = _read_sidecar(sidecar) if path.exists() else None
            if cached is not None:
                frames[name] = pd.read_parquet(path)
                manifest[name] = {"file": path.name, "replicates": bb, "key": key, **cached}
                continue
            elapsed = time.perf_counter() - started
            if max_seconds is not None and computed > 0 and elapsed > max_seconds:
                print(f"{case}: stopped after {max_seconds:g} s at unit {i}/{len(units)}; rerun")
                return 3
            t0 = time.perf_counter()
            reps = simulate_replicates(cfg, variants, bb, seed, executor=executor, chunks=n_jobs)
            run = {
                "seconds": round(time.perf_counter() - t0, 1),
                "jobs": n_jobs,
                "machine": platform.platform(),
                "python": platform.python_version(),
            }
            # Parquet first, then its sidecar: a unit counts as cached only when both exist
            # and the sidecar parses, so an interrupted write is recomputed. Temporary names
            # carry the process id, so concurrent runs do not collide.
            tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
            reps.to_parquet(tmp, index=False)
            os.replace(tmp, path)
            tmp_side = sidecar.with_name(f"{sidecar.stem}.{os.getpid()}.json.tmp")
            tmp_side.write_text(
                json.dumps({**spec, "run": run}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp_side, sidecar)
            frames[name] = reps
            computed += 1
            manifest[name] = {"file": path.name, "replicates": bb, "key": key, **run}
            print(
                f"{case}: {name} B={bb:,} in {run['seconds']:.0f} s ({i}/{len(units)})", flush=True
            )
    finally:
        if executor is not None:
            executor.shutdown()

    scen_reps = pd.concat([frames["scenarios_null"], frames["scenarios_effect"]], ignore_index=True)
    scen = scenario_summary(scen_reps, lift, loss)
    contrasts = paired_contrasts(scen_reps, OC_CONTRASTS)
    story = case_story(frames["scenarios_effect"], "D")
    loss_table = loss_summary(frames["scenarios_effect"])
    deficit = []
    for v in offset_values:
        name = f"offset_{v!r}"
        if name in frames:
            reps = frames[name]
        else:
            eff = frames["scenarios_effect"]
            reps = eff[(eff["variant"] == "D") & (eff["replicate"] < b_sweep)]
        s = summarise(reps).drop(columns="variant")
        s.insert(0, "parameter", "affected_cell_logit_offset")
        s.insert(1, "value", v)
        deficit.append(s)
    deficit_table = pd.concat(deficit, ignore_index=True)

    for fname, frame in (
        ("oc_scenarios", scen),
        ("oc_contrasts", contrasts),
        ("oc_story", story),
        ("oc_loss_sweep", loss_table),
        ("oc_deficit_sweep", deficit_table),
    ):
        frame.to_csv(out / f"{fname}.csv", index=False, float_format="%.10g", lineterminator="\n")
        print(f"{case}: wrote results/{case}/{fname}.csv")
    fig_path = (
        root / "case_studies" / CASES[case] / "figures" / "fig4_operating_characteristics.png"
    )
    plot_operating_characteristics(loss_table, fig_path, b, lift)
    meta = {
        "case": case,
        "seed": seed,
        "replicates_scenarios_and_loss_sweep": b,
        "replicates_deficit_sweep": b_sweep,
        "scenario_lift_rendered": lift,
        "abforensics_version": __version__,
        "library_versions": versions,
        # The environment that assembled these tables; each unit records where it was computed.
        "summary_python": platform.python_version(),
        "summary_machine": platform.platform(),
        "code_sha256": fingerprint,
        "units": manifest,
        "total_unit_seconds": round(sum(u.get("seconds", 0.0) for u in manifest.values()), 1),
    }
    (out / "oc_meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"{case}: figure {fig_path.relative_to(root)}")
    return 0


def _cmd_simulate_case02(root, case, seed, *, synthetic=False):
    from abforensics.simulation import ranking_incident as ri

    config = load_config(root, case)
    if seed is None and config.get("seed") is None:
        print("abf simulate case02: the case seed is not set yet; pass --seed", file=sys.stderr)
        return 2
    base = (
        ri.make_parametric_base(config["parametric_generator"])
        if synthetic
        else ri.load_base(root / config["base"]["raw_dir"])
    )
    data = ri.simulate_ranking_experiment(config, seed=seed, base=base)
    generated = root / "data" / "generated" / ("case02_synthetic" if synthetic else case)
    manifest_path = (
        generated / "manifest.json" if synthetic else root / "data" / "manifests" / f"{case}.json"
    )
    label = "simulated " if synthetic else ""
    if synthetic:
        for frame in data.tables().values():
            frame["source"] = "simulated"
        data.manifest.update(
            {
                "source": "simulated",
                "population_seed": int(config["parametric_generator"]["population_seed"]),
                "skipped_scenarios": ["D_leaked_tier"],
                "skipped_scenarios_reason": (
                    "simulated population has no article totals for a leaked tier"
                ),
            }
        )
    manifest = ri.write_case_data(data, generated, manifest_path)
    counts = ", ".join(f"{k}={v:,}" for k, v in manifest["row_counts"].items())
    print(f"{case}: {label}seed={manifest['seed']} wrote {generated.relative_to(root)}/ ({counts})")
    print(f"{label}manifest: {manifest_path.relative_to(root)}")
    return 0


def _cmd_oc_case02(root, case, *, replicates, sweep_replicates, seed, jobs, max_seconds):
    t0 = time.perf_counter()
    from abforensics.simulation import ranking_incident as ri
    from abforensics.simulation.oc_case02 import run_oc_case02

    if sweep_replicates is not None or max_seconds is not None:
        print(
            "abf oc case02: --sweep-replicates and --max-seconds are not supported yet",
            file=sys.stderr,
        )
        return 2
    config = load_config(root, case)
    oc = config["operating_characteristics"]
    raw_dir = root / config["base"]["raw_dir"]
    out_dir = root / "results" / "case02"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = run_oc_case02(
        config,
        ri.load_base(raw_dir),
        replicates=int(replicates or oc["replicates"]),
        oc_seed=int(seed if seed is not None else oc["seed"]),
        workers=int(jobs or oc["workers"]),
        out_dir=out_dir,
        raw_dir=raw_dir,
    )
    total = time.perf_counter() - t0
    limit = oc["time_limit_minutes"]
    verdict = "PASS" if total <= 60 * limit else "FAIL"
    print(
        f"abf oc case02: B={meta['replicates']}, workers={meta['workers']}, "
        f"command wall-clock {total} s ({total / 60} min), "
        f"harness {meta['wall_clock_seconds']} s, against {limit} min: {verdict}"
    )
    for name in ("oc_truths.csv", "oc_scenarios.csv", "oc_rule.csv", "oc_meta.json"):
        print(out_dir / name)
    return 0


def plot_operating_characteristics(loss, path: Path, b: int, lift: float) -> None:  # noqa: ANN001
    """Three panels against the loss probability: relative bias, coverage, SRM detection."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from abforensics.reporting.style import PALETTE, apply_style

    apply_style()
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.9))
    shown = {
        "dashboard_day14": (PALETTE["treatment"], "Dashboard (exposed, day 14)"),
        "exposed_excluding_cell": (PALETTE["series3"], "Exposed, affected segment excluded"),
        "itt_assigned": (PALETTE["control"], "ITT (assigned)"),
    }
    for est, (colour, label) in shown.items():
        g = loss[loss["estimator"] == est]
        x = g["value"].to_numpy()
        for ax, col in zip(axes[:2], ("bias", "coverage"), strict=True):
            y = g[col].to_numpy() * 100
            se = g[f"{col}_mc_se"].to_numpy() * 100
            ax.fill_between(x, y - 1.96 * se, y + 1.96 * se, color=colour, alpha=0.18, lw=0)
            ax.plot(x, y, marker="o", color=colour, label=label)
    dash = loss[loss["estimator"] == "dashboard_day14"]
    x = dash["value"].to_numpy()
    for alpha, style in ((0.001, "-"), (0.05, "--")):
        col = f"exposure_srm_rate_{alpha:g}"
        y = dash[col].to_numpy() * 100
        lo = dash[f"{col}_ci_low"].to_numpy() * 100
        hi = dash[f"{col}_ci_high"].to_numpy() * 100
        axes[2].fill_between(x, lo, hi, color=PALETTE["ink2"], alpha=0.15, lw=0)
        axes[2].plot(x, y, style, marker="o", color=PALETTE["ink2"], label=f"alpha = {alpha:g}")
    axes[0].axhline(0, color=PALETTE["threshold"], lw=1)
    axes[1].axhline(95, color=PALETTE["threshold"], lw=1)
    axes[0].set_title("Bias of the relative lift (points)")
    axes[1].set_title("Coverage of the 95% interval (%)")
    axes[2].set_title("Exposure SRM detected (%)")
    for ax in axes:
        ax.set_xlabel("Render-event loss probability in the affected cell")
    axes[0].legend(loc="upper left")
    axes[2].legend(loc="lower right")
    axes[2].set_ylim(-3, 103)
    fig.suptitle(
        f"Case 01 operating characteristics: true lift {lift:.1%} among rendered users, "
        f"B = {b:,} per point; bands are 95% Monte Carlo intervals, each estimator scored "
        "against its own target",
        x=0.01,
        ha="left",
        fontsize=10,
        color=PALETTE["ink2"],
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def _int_at_least(minimum: int):  # noqa: ANN202
    def parse(text: str) -> int:
        value = int(text)
        if value < minimum:
            raise argparse.ArgumentTypeError(f"must be at least {minimum}; got {value}")
        return value

    return parse


def _positive_seconds(text: str) -> float:
    value = float(text)
    if not value > 0:  # also rejects NaN
        raise argparse.ArgumentTypeError(f"must be a positive number of seconds; got {text}")
    return value


_at_least_two = _int_at_least(2)
_at_least_one = _int_at_least(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="abf",
        description="Regenerate data, execute a case notebook, or run operating characteristics.",
    )
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("case", choices=sorted(CASES))
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="simulate case02: generate simulated data without the raw download",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="simulate: override the case seed; oc: override operating_characteristics.seed",
    )
    parser.add_argument(
        "--replicates", type=_at_least_two, default=None, help="oc: B per scenario and loss point"
    )
    parser.add_argument(
        "--sweep-replicates",
        type=_at_least_two,
        default=None,
        help="oc: B per conversion-deficit sweep point",
    )
    parser.add_argument("--jobs", type=_at_least_one, default=None, help="oc: worker processes")
    parser.add_argument(
        "--max-seconds",
        type=_positive_seconds,
        default=None,
        help="oc: stop starting new units after this many seconds; a rerun resumes",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.synthetic and (args.command != "simulate" or args.case != "case02"):
        print("--synthetic is supported only by abf simulate case02", file=sys.stderr)
        return 2
    root = repo_root()
    if args.command == "simulate":
        return cmd_simulate(root, args.case, args.seed, synthetic=args.synthetic)
    if args.command == "run":
        return cmd_run(root, args.case)
    if args.command == "oc":
        return cmd_oc(
            root,
            args.case,
            replicates=args.replicates,
            sweep_replicates=args.sweep_replicates,
            seed=args.seed,
            jobs=args.jobs,
            max_seconds=args.max_seconds,
        )
    print(f"abf {args.command} {args.case}: unknown command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
