import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from abforensics import cli
from abforensics.simulation import oc_case02 as oc
from abforensics.simulation import oc_case02_sweeps as sw
from abforensics.simulation import ranking_incident as ri


@pytest.fixture
def committed_config():
    path = (
        Path(__file__).resolve().parents[2]
        / "case_studies/02_metric_that_told_two_stories/config.yaml"
    )
    return copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))


@pytest.fixture
def small_config(committed_config):
    config = copy.deepcopy(committed_config)
    sweeps = config["operating_characteristics"]["sweeps"]
    sweeps["replicates"] = 6
    sweeps["bootstrap"]["replicates_per_size"] = 2
    sweeps["bootstrap"]["resamples"] = 20
    sweeps["wallclock_percentile_bootstrap"]["replicates_per_size"] = 1
    sweeps["wallclock_percentile_bootstrap"]["resamples"] = 30
    sweeps["sample_size_per_arm"] = [400, 600]
    sweeps["heterogeneity_sigma"] = [0.0, 1.0]
    sweeps["tail_dispersion_r"] = [0.25, 4.0]
    sweeps["tail_bootstrap"]["replicates"] = 2
    sweeps["tail_bootstrap"]["resamples"] = 20
    sweeps["parametric_n_per_arm"] = 400
    config["parametric_generator"]["population_users"] = 400
    return config


@pytest.fixture
def base():
    return ri.make_synthetic_base(300, seed=3)


@pytest.mark.parametrize(
    "flag,value", [("--replicates", "5"), ("--sweep-replicates", "5"), ("--max-seconds", "10")]
)
def test_sweeps_rejects_replicate_and_time_overrides(flag, value):
    assert cli.main(["oc", "case02", "--sweeps", flag, value]) == 2


@pytest.mark.parametrize(
    "command,case", [("run", "case02"), ("simulate", "case02"), ("oc", "case01")]
)
def test_sweeps_rejects_other_commands_and_cases(command, case):
    assert cli.main([command, case, "--sweeps"]) == 2


def test_sweeps_passes_seed_jobs_and_flag(monkeypatch):
    seen = {}

    def recording(root, case, **kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "_cmd_oc_case02", recording)
    assert cli.main(["oc", "case02", "--sweeps", "--seed", "7", "--jobs", "2"]) == 0
    assert seen["sweeps"] is True
    assert seen["seed"] == 7
    assert seen["jobs"] == 2
    assert seen["replicates"] is None


def test_committed_task_plan_and_counts(committed_config):
    tasks = sw.sweep_tasks(committed_config)
    parametric = [task for task in tasks if task["kind"] != "size"]
    sizes = [task for task in tasks if task["kind"] == "size"]
    assert len(parametric) == 12
    assert len(sizes) == 64
    assert len(tasks) == 76
    assert {task["size_per_arm"] for task in sizes if task["marked"] == "yes"} == {
        15143,
        52500,
        57600,
        64100,
    }
    assert sw.expected_row_counts(committed_config) == {
        "oc_sweep_heterogeneity.csv": 42,
        "oc_sweep_tail.csv": 32,
        "oc_sweep_size.csv": 104,
        "oc_sweep_runtime.csv": 32,
    }
    costs = [task["total_draws"] * len(task["replicates"]) for task in tasks]
    assert costs == sorted(costs, reverse=True)
    for scenario in ("A", "C"):
        for size in committed_config["operating_characteristics"]["sweeps"]["sample_size_per_arm"]:
            chunks = [
                task
                for task in sizes
                if task["scenario"] == scenario and task["size_per_arm"] == size
            ]
            indices = [r for task in chunks for r in task["replicates"]]
            assert sorted(indices) == list(range(1000))


def test_sweep_outputs_are_chunk_invariant(small_config, base, tmp_path):
    expected = sw.expected_row_counts(small_config)
    folders = [tmp_path / str(chunks) for chunks in (1, 3)]
    for chunks, folder in zip((1, 3), folders, strict=True):
        meta = sw.run_sweeps_case02(
            small_config,
            base,
            oc_seed=20261003,
            workers=1,
            size_chunks=chunks,
            out_dir=folder,
            fig_path=folder / "figure.png",
        )
        assert meta["row_counts"] == expected
        for name, count in expected.items():
            assert (folder / name).exists()
            assert len(pd.read_csv(folder / name)) == count
            assert b"\r\n" not in (folder / name).read_bytes()
        raw_meta = (folder / "oc_sweep_meta.json").read_bytes()
        assert b"\r\n" not in raw_meta
        assert raw_meta.endswith(b"\n")
        assert json.loads(raw_meta)["size_chunks"] == chunks
        assert (folder / "figure.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    for kind in ("heterogeneity", "tail", "size"):
        name = f"oc_sweep_{kind}.csv"
        pd.testing.assert_frame_equal(
            pd.read_csv(folders[0] / name), pd.read_csv(folders[1] / name)
        )
        assert (folders[0] / name).read_bytes() == (folders[1] / name).read_bytes()
    tables = {
        kind: pd.read_csv(folders[0] / f"oc_sweep_{kind}.csv")
        for kind in ("heterogeneity", "tail", "size", "runtime")
    }
    for kind, table in tables.items():
        source = "simulated" if kind in ("heterogeneity", "tail") else "real_base_resampling"
        assert set(table.source) == {source}
    depth = tables["heterogeneity"].query("depth_term == 'off'")
    assert len(depth) == 6
    assert (depth.sigma == 0.0).all()
    assert (tables["heterogeneity"].iloc[-6:].depth_term == "off").all()
    assert "bootstrap_e2" not in set(tables["heterogeneity"].estimator)
    tail_bootstrap = tables["tail"].query("estimator == 'bootstrap_e2'")
    assert set(tail_bootstrap.dispersion_r) == {0.25}
    assert (tail_bootstrap.scenario == "A").all()
    size_bootstrap = tables["size"].query("estimator == 'bootstrap_e2'")
    assert set(size_bootstrap.size_per_arm) == {400, 600}
    assert (size_bootstrap.scenario == "A").all()
    assert (size_bootstrap.B == 2).all()
    assert set(tables["runtime"].method) == set(sw.METHODS)
    assert (tables["runtime"].replicates == 1).all()
    for _, point in tables["size"].groupby(["size_per_arm", "scenario"]):
        assert point.ship_rate.nunique() == 1
        assert point.ship_rate_mc_se.nunique() == 1


def test_sweeps_preserves_core_outputs(small_config, base, tmp_path):
    sentinels = {
        name: f"sentinel for {name}\r\n".encode()
        for name in (
            "oc_scenarios.csv",
            "oc_rule.csv",
            "oc_truths.csv",
            "oc_meta.json",
            "oc_replicates.parquet",
        )
    }
    for name, content in sentinels.items():
        (tmp_path / name).write_bytes(content)
    sw.run_sweeps_case02(small_config, base, oc_seed=20261003, workers=1, out_dir=tmp_path)
    for name, content in sentinels.items():
        assert (tmp_path / name).read_bytes() == content


def test_core_defaults_and_scenario_subset(small_config, base):
    c_t, m_t = np.array([1.0, 3.0, 5.0, 2.0]), np.array([10.0, 12.0, 20.0, 10.0])
    c_c, m_c = np.array([2.0, 1.0, 4.0, 3.0]), np.array([12.0, 10.0, 18.0, 14.0])
    default = oc.bootstrap_relative_lift(c_t, m_t, c_c, m_c, 7, 20)
    normal = oc.bootstrap_relative_lift(c_t, m_t, c_c, m_c, 7, 20, interval="normal")
    assert default == normal
    np.testing.assert_array_equal(default.distribution, normal.distribution)
    rows, truths = [], {}
    for name in ("A", "B"):
        original = oc.scenario_specs(small_config)[name]
        spec = oc.ScenarioSpec(name, original.prm, "age", 800)
        g = ri.guardrail_multipliers(base, spec.prm)
        truths[name] = ri.scenario_truths(ri.potential_outcomes(base.with_tier("age"), spec.prm, g))
        for r in range(2):
            rows.extend(
                oc.evaluate_replicate(
                    base,
                    g,
                    spec,
                    r,
                    20261003,
                    ship_margin=0.005,
                    guardrail_margin=0.05,
                    reread_margin=0.03,
                    bootstrap=False,
                    resamples=20,
                )
            )
    rows = pd.DataFrame(rows)
    all_summaries = oc.summarise(rows, truths)
    a_summaries = oc.summarise(rows, truths, scenario_names=["A"])
    for all_frame, a_frame in zip(all_summaries, a_summaries, strict=True):
        pd.testing.assert_frame_equal(
            all_frame[all_frame.scenario == "A"].reset_index(drop=True), a_frame
        )


def test_missing_replicates_names_key(small_config, base, tmp_path):
    del small_config["operating_characteristics"]["sweeps"]["replicates"]
    with pytest.raises(ValueError, match="replicates"):
        sw.run_sweeps_case02(small_config, base, oc_seed=7, workers=1, out_dir=tmp_path)
