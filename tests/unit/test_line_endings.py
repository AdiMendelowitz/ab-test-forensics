"""Regenerated Case 02 outputs are written with LF line endings on every platform (retro action
A11). `.gitattributes` normalises on add; the working copy must already match, so that
`git add` prints no CRLF warning and a diff against the index is empty."""

import copy
from pathlib import Path

import pytest
import yaml

from abforensics.simulation import ranking_incident as ri
from abforensics.simulation import oc_case02 as oc

ROOT = Path(__file__).resolve().parents[2]
GEN_CONFIG = {
    "seed": None,
    "base": {"raw_dir": "data/raw/ebnerd_small"},
    "design": {"total_draws": 6000, "allocation": {"control": 0.5, "treatment": 0.5},
               "arrival_delay_hours": {"min": 0, "max": 36}},
    "treatment": {"suppression_delta": 0.15, "click_lift_tau": 0.025, "never_remove_last_inview": True},
    "guardrail_simulation": {"per_impression_rate": 0.005, "low_quality_multiplier": 3.0,
                             "heterogeneity_shape": 2.0, "population_seed": 20261001},
}


@pytest.fixture(scope="module")
def base() -> ri.RankingBase:
    return ri.make_synthetic_base(300, seed=3)


def _has_crlf(path: Path) -> bool:
    return b"\r\n" in path.read_bytes()


def test_manifest_is_written_with_lf(base, tmp_path):
    data = ri.simulate_ranking_experiment(GEN_CONFIG, seed=11, base=base)
    manifest_path = tmp_path / "manifests" / "case02.json"
    (tmp_path / "generated").mkdir()
    manifest_path.parent.mkdir(parents=True)
    ri.write_case_data(data, tmp_path / "generated", manifest_path)
    assert not _has_crlf(manifest_path), "manifest carries CRLF: write_case_data must pass newline='\\n'"


def test_oc_tables_are_written_with_lf(base, tmp_path):
    path = ROOT / "case_studies/02_metric_that_told_two_stories/config.yaml"
    config = copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))
    config["design"]["total_draws"] = 4000
    config["design"]["contrast_total_draws"] = 3000
    config["operating_characteristics"]["bootstrap"]["replicates"] = 3
    config["operating_characteristics"]["bootstrap"]["resamples"] = 20
    oc.run_oc_case02(config, base, replicates=2, oc_seed=20261003, workers=1, out_dir=tmp_path)
    offenders = [name for name in ("oc_truths.csv", "oc_scenarios.csv", "oc_rule.csv", "oc_meta.json")
                 if _has_crlf(tmp_path / name)]
    assert not offenders, f"CRLF in {offenders}: pin lineterminator='\\n' and newline='\\n' in oc_case02"
