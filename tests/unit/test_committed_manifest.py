"""The committed manifest must describe the committed config, for every case that has one
(spec case-02.md, section 14)."""

import json
from pathlib import Path

import pytest

from abforensics.cli import load_config

ROOT = Path(__file__).resolve().parents[2]
CASES = ("case01", "case02")


@pytest.mark.parametrize("case", CASES)
def test_manifest_config_matches_config_yaml(case):
    manifest_path = ROOT / "data" / "manifests" / f"{case}.json"
    if not manifest_path.exists():
        pytest.skip(f"no committed manifest for {case}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = load_config(ROOT, case)
    assert manifest["config"] == config, f"rerun `abf simulate {case}` after editing config.yaml"
    assert manifest["seed"] == config["seed"]
