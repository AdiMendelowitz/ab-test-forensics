"""The download-free path stays isolated and labels every output simulated."""

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from abforensics import cli
from abforensics.simulation import ranking_incident as ri

REPO = Path(__file__).resolve().parents[2]


def test_parser_accepts_synthetic():
    args = cli.build_parser().parse_args(["simulate", "case02", "--synthetic"])
    assert args.synthetic


@pytest.mark.parametrize(
    "command,case",
    [
        ("simulate", "case01"),
        ("run", "case01"),
        ("run", "case02"),
        ("oc", "case01"),
        ("oc", "case02"),
    ],
)
def test_other_commands_and_cases_reject_synthetic(command, case, monkeypatch, capsys):
    def forbidden():
        pytest.fail("invalid synthetic dispatch touched the repository")

    monkeypatch.setattr(cli, "repo_root", forbidden)
    assert cli.main([command, case, "--synthetic"]) == 2
    assert "only" in capsys.readouterr().err


def test_synthetic_runs_without_raw_data_and_preserves_core_files(tmp_path, monkeypatch):
    config = cli.load_config(REPO, "case02")
    config["parametric_generator"]["population_users"] = 100
    config["design"]["total_draws"] = 100
    config_path = tmp_path / "case_studies/02_metric_that_told_two_stories/config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert not (tmp_path / config["base"]["raw_dir"]).exists()
    core = [
        "data/manifests/case02.json",
        "results/case02/oc_meta.json",
        "results/case02/oc_scenarios.csv",
        "results/case02/oc_rule.csv",
        "results/case02/oc_truths.csv",
    ]
    before = {}
    for name in core:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        before[name] = (REPO / name).read_bytes()
        path.write_bytes(before[name])

    def no_raw(*args, **kwargs):
        pytest.fail("synthetic path called load_base")

    monkeypatch.setattr(ri, "load_base", no_raw)
    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)
    assert cli.main(["simulate", "case02", "--synthetic"]) == 0
    out = tmp_path / "data/generated/case02_synthetic"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["source"] == "simulated"
    assert manifest["population_seed"] == 20263011
    assert manifest["seed"] == config["seed"]
    assert manifest["base"]["layout"] == "placeholder"
    assert manifest["skipped_scenarios"] == ["D_leaked_tier"]
    assert manifest["config"]["parametric_generator"] == config["parametric_generator"]
    assert manifest["raw_md5"] == {}
    for name, filename in manifest["files"].items():
        frame = pd.read_parquet(out / filename)
        assert len(frame) == manifest["row_counts"][name]
        assert set(frame["source"]) == {"simulated"}
    for name in core:
        assert (tmp_path / name).read_bytes() == before[name]
    assert not (tmp_path / "data/generated/case02").exists()
    assert cli.main(["simulate", "case02", "--synthetic"]) == 0
    repeated = json.loads((out / "manifest.json").read_text())
    assert repeated["content_sha256"] == manifest["content_sha256"]
    assert cli.main(["simulate", "case02", "--synthetic", "--seed", "20261003"]) == 0
    changed = json.loads((out / "manifest.json").read_text())
    assert changed["seed"] == 20261003
    assert changed["population_seed"] == manifest["population_seed"]
    assert changed["base"] == manifest["base"]
