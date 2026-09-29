"""The committed manifest must describe the committed config."""

import json
from pathlib import Path

import pytest

from abforensics.cli import load_config

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "manifests" / "case01.json"


@pytest.mark.skipif(not MANIFEST.exists(), reason="no committed manifest")
def test_manifest_config_matches_config_yaml():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    config = load_config(ROOT, "case01")
    assert manifest["config"] == config, "rerun `abf simulate case01` after editing config.yaml"
    assert manifest["seed"] == config["seed"]
