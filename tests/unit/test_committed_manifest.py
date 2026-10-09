"""The committed manifest must describe the committed config, for every case that has one
(spec case-02.md, section 14)."""

import json
from pathlib import Path

import pytest

from abforensics.cli import load_config

ROOT = Path(__file__).resolve().parents[2]
CASES = ("case01", "case02")


def _real_config(config, case):
    # The parametric block is for the isolated synthetic path (ADR-07, item 4).
    if case == "case02":
        return {key: value for key, value in config.items() if key != "parametric_generator"}
    return config


def test_real_config_excludes_only_case02_parametric_settings():
    config = load_config(ROOT, "case02")
    expected = {key: value for key, value in config.items() if key != "parametric_generator"}
    assert _real_config(config, "case02") == expected
    changed = {**config, "seed": None}
    assert _real_config(changed, "case02") != expected
    assert _real_config(config, "case01") == config


@pytest.mark.parametrize("case", CASES)
def test_manifest_config_matches_config_yaml(case):
    manifest_path = ROOT / "data" / "manifests" / f"{case}.json"
    if not manifest_path.exists():
        pytest.skip(f"no committed manifest for {case}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = load_config(ROOT, case)
    assert _real_config(manifest["config"], case) == _real_config(config, case), (
        f"rerun `abf simulate {case}` after editing the real-run config"
    )
    assert manifest["seed"] == config["seed"]
