"""``abf simulate|run|oc case01-rollout``: the staged-rollout extension of Case 01.

Outputs:

* ``abf simulate case01-rollout``: ``data/generated/case01-rollout/products.parquet`` and
  ``data/manifests/case01-rollout.json`` (seed, parameters, row counts, checksum).
* ``abf run case01-rollout``: ``results/case01/rollout/run_estimates.csv`` (the three estimators
  on the committed run, each against its own estimand) and ``run_canary.csv`` (the sample-ratio
  check per stage on the committed run's logged scans).
* ``abf oc case01-rollout``: ``results/case01/rollout/oc_rollout_scenarios.csv`` (S0 with the
  drift sweep, S1, S2), ``oc_rollout_canary.csv`` (S3 against the exact power),
  ``oc_rollout_s1_distribution.csv`` (percentiles of the S1 relative estimates, for the figure;
  the replicate-level rows go to the gitignored ``parts/``) and
  ``oc_rollout_meta.json``.

Every table is written with LF line endings on every platform.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from abforensics.simulation import assembly_rollout as ar

CASE = "case01-rollout"
CONFIG = Path("case_studies") / "01_lost_logs_found_lift" / "rollout" / "config.yaml"
RESULTS = Path("results") / "case01" / "rollout"


def load_rollout_config(root: Path) -> dict:
    with (root / CONFIG).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n", float_format="%.10g")


def _write_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def code_fingerprint() -> str:
    """SHA-256 of the rollout module and the Case 01 generator it reads (CRLF normalised)."""
    from abforensics.simulation import assembly_incident

    h = hashlib.sha256()
    for module in (ar, assembly_incident):
        h.update(inspect.getsource(module).replace("\r\n", "\n").encode("utf-8"))
    return h.hexdigest()


def _library_versions() -> dict[str, str]:
    import scipy

    return {"numpy": np.__version__, "scipy": scipy.__version__, "pandas": pd.__version__}


def cmd_simulate(root: Path, seed: int | None) -> int:
    config = load_rollout_config(root)
    prm = ar.validate_rollout_config(config)
    used_seed = int(config["seed"] if seed is None else seed)
    pop = ar.simulate_rollout_population(prm, used_seed)
    df = pd.DataFrame(pop)
    out = root / "data" / "generated" / CASE / "products.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    digest = hashlib.sha256(
        pd.util.hash_pandas_object(df.drop(columns=["p_control", "p_treatment"]), index=False)
        .to_numpy()
        .tobytes()
    ).hexdigest()
    manifest = {
        "case": CASE,
        "seed": used_seed,
        "config": config,
        "row_counts": {"products": int(len(df))},
        "content_sha256": digest,
        "truths": ar.truths(prm),
        "realised_control_rate": float(df["p_control"].mean()),
        "routed_to_new_station_by_stage": [
            int(df.loc[ar.stage_of_day(prm)[df["arrival_day"] - 1] == i, "arm"].sum())
            for i in range(prm.n_stages)
        ],
    }
    _write_json(manifest, root / "data" / "manifests" / f"{CASE}.json")
    print(
        f"{CASE}: seed={used_seed} wrote data/generated/{CASE}/products.parquet ({len(df):,} rows)"
    )
    print(f"manifest: data/manifests/{CASE}.json")
    return 0


def cmd_run(root: Path) -> int:
    config = load_rollout_config(root)
    prm = ar.validate_rollout_config(config)
    path = root / "data" / "generated" / CASE / "products.parquet"
    if not path.exists():
        raise SystemExit(f"{path} not found: run `abf simulate {CASE}` first")
    df = pd.read_parquet(path)
    pop = {c: df[c].to_numpy() for c in df.columns}
    counts = ar.counts_from_population(pop, prm)
    est = ar.estimate_all(counts, prm)
    tr = ar.truths(prm)
    rows = []
    for name in ar.ESTIMATORS:
        e = est[name]
        target = ar.TARGET[name]
        rows.append(
            {
                "estimator": name,
                "estimand": target,
                "truth_rel": tr[f"{target}_rel"],
                "estimate_rel": float(e["rel"][0]),
                "ci_rel_lo": float(e["rel_lo"][0]),
                "ci_rel_hi": float(e["rel_hi"][0]),
                "truth_abs": tr[f"{target}_abs"],
                "estimate_abs": float(e["abs"][0]),
                "ci_abs_lo": float(e["abs_lo"][0]),
                "ci_abs_hi": float(e["abs_hi"][0]),
                "ships": bool(e["rel_lo"][0] > prm.ship_floor),
            }
        )
    canary = []
    stage = ar.stage_of_day(prm)[df["arrival_day"].to_numpy() - 1]
    for i in range(prm.n_stages):
        s = df[stage == i]
        logged = s["logged_scan"].to_numpy()
        t = int((logged & (s["arm"].to_numpy() == 1)).sum())
        m = int(logged.sum())
        z2 = float(ar.canary_statistic(np.asarray([t]), np.asarray([m]), prm.stage_p[i])[0])
        canary.append(
            {
                "stage": i + 1,
                "routing_p": prm.stage_p[i],
                "logged_new_station": t,
                "logged_total": m,
                "z_squared": z2,
                "flags": bool(z2 >= ar.CHI2_CRIT_0001),
            }
        )
    out = root / RESULTS
    _write_csv(pd.DataFrame(rows), out / "run_estimates.csv")
    _write_csv(pd.DataFrame(canary), out / "run_canary.csv")
    print(pd.DataFrame(rows).to_string(index=False))
    print(pd.DataFrame(canary).to_string(index=False))
    print(f"{CASE}: wrote {RESULTS.as_posix()}/run_estimates.csv and run_canary.csv")
    return 0


def s1_distribution(reps: pd.DataFrame) -> pd.DataFrame:
    """Percentiles 0.5 to 99.5 (step 0.5) of each estimator's relative estimate."""
    q = np.round(np.arange(0.5, 100.0, 0.5), 1)
    rows = []
    for name in ar.ESTIMATORS:
        values = reps[name].to_numpy(dtype=float)
        values = values[~np.isnan(values)]
        for pct, value in zip(q, np.percentile(values, q), strict=True):
            rows.append({"estimator": name, "percentile": pct, "relative_estimate": value})
    return pd.DataFrame(rows)


def cmd_oc(root: Path, replicates: int | None, seed: int | None) -> int:
    config = load_rollout_config(root)
    prm = ar.validate_rollout_config(config)
    oc = config["operating_characteristics"]
    b = int(oc["replicates"] if replicates is None else replicates)
    used_seed = int(oc["seed"] if seed is None else seed)
    started = time.perf_counter()
    rows, canary, extra = ar.run_operating_characteristics(
        prm,
        replicates=b,
        canary_replicates=int(oc["canary_replicates"]),
        seed=used_seed,
        kappa_sweep=[float(k) for k in oc["kappa_sweep"]],
        s2_stage_lifts=[float(x) for x in oc["s2_stage_lifts"]],
    )
    seconds = time.perf_counter() - started
    out = root / RESULTS
    scen = pd.DataFrame(rows)
    can = pd.DataFrame(canary)
    _write_csv(scen, out / "oc_rollout_scenarios.csv")
    _write_csv(can, out / "oc_rollout_canary.csv")
    # Replicate-level rows are never tracked (the repository's data guard allows at most 1,000
    # rows per tracked table): they go to the gitignored parts/ folder, and the tracked table is
    # their distribution, the 0.5th to 99.5th percentiles in steps of 0.5 (199 rows per estimator).
    reps = pd.DataFrame(extra.pop("S1_replicates"))
    reps.insert(0, "replicate", np.arange(1, len(reps) + 1))
    _write_csv(
        reps.add_suffix("_rel").rename(columns={"replicate_rel": "replicate"}),
        out / "parts" / "oc_rollout_s1_replicates.csv",
    )
    _write_csv(s1_distribution(reps), out / "oc_rollout_s1_distribution.csv")
    meta = {
        "case": CASE,
        "seed": used_seed,
        "replicates": b,
        "canary_replicates": int(oc["canary_replicates"]),
        "scenarios": ["S0", "S1", "S2", "S3"],
        "kappa_sweep": oc["kappa_sweep"],
        "s2_stage_lifts": oc["s2_stage_lifts"],
        "code_sha256": code_fingerprint(),
        "library_versions": _library_versions(),
        "python": platform.python_version(),
        "machine": f"{platform.system()}-{platform.release()}",
        "seconds": round(seconds, 2),
        "pooled_expectation_default_kappa": ar.pooled_expectation(ar.with_changes(prm, lift=0.0)),
        "day_standardised_variance_shares": ar.day_standardised_variance_shares(prm).tolist(),
        "S1_sign_disagreement": list(extra["S1_sign_disagreement"]),
        "S2_E_ramp_minus_E_final_rel": extra["S2_E_ramp_minus_E_final_rel"],
        "flags": [
            f"{r['scenario']} kappa={r['kappa']} {r['estimator']}: {r['flag']}"
            for r in rows
            if r["flag"]
        ]
        + [f"S3 stage {r['stage']}: {r['flag']}" for r in canary if r["flag"]],
    }
    _write_json(meta, out / "oc_rollout_meta.json")
    print(f"{CASE}: B={b} seed={used_seed} in {seconds:.1f} s; flags: {len(meta['flags'])}")
    for f in meta["flags"]:
        print(f"  FLAG {f}")
    print(
        f"{CASE}: wrote {RESULTS.as_posix()}/oc_rollout_scenarios.csv, oc_rollout_canary.csv, "
        "oc_rollout_meta.json"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(2)
