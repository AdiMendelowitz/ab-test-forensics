import copy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from abforensics.simulation import oc_case02 as oc
from abforensics.simulation import ranking_incident as ri


@pytest.fixture
def config():
    path = (
        Path(__file__).resolve().parents[2]
        / "case_studies/02_metric_that_told_two_stories/config.yaml"
    )
    config = copy.deepcopy(yaml.safe_load(path.read_text(encoding="utf-8")))
    config["design"]["total_draws"] = 4000
    config["design"]["contrast_total_draws"] = 3000
    config["operating_characteristics"]["bootstrap"]["replicates"] = 3
    config["operating_characteristics"]["bootstrap"]["resamples"] = 20
    return config


@pytest.fixture
def base():
    return ri.make_synthetic_base(300, seed=3)


def test_guardrail_verdict_and_rule_row():
    for lo, hi, verdict in (
        (-0.1, 0.05, "pass"),
        (0.051, 0.2, "breach"),
        (0.0, 0.06, "inconclusive"),
    ):
        assert oc.guardrail_verdict(lo, hi, 0.05) == verdict
    assert oc.rule_row(0.006, 0.02, "breach", 0.005) == 1
    assert oc.rule_row(0.006, 0.02, "inconclusive", 0.005) == 2
    for lo, hi, row in ((-0.02, -0.001, 3), (0.006, 0.02, 4), (0.004, 0.02, 5), (-0.01, 0.01, 5)):
        assert oc.rule_row(lo, hi, "pass", 0.005) == row


def test_run_is_chunk_invariant(config, base, tmp_path):
    for chunks in (1, 3):
        oc.run_oc_case02(
            config,
            base,
            replicates=4,
            oc_seed=20261003,
            workers=1,
            out_dir=tmp_path / str(chunks),
            chunks=chunks,
        )
    pd.testing.assert_frame_equal(
        pd.read_parquet(tmp_path / "1/oc_replicates.parquet"),
        pd.read_parquet(tmp_path / "3/oc_replicates.parquet"),
    )


def test_bootstrap_called_once_per_replicate_in_a_and_d(config, base, tmp_path, monkeypatch):
    original = oc.cluster_bootstrap
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(oc, "cluster_bootstrap", counting)
    oc.run_oc_case02(config, base, replicates=4, oc_seed=20261003, workers=1, out_dir=tmp_path)
    assert len(calls) == 2 * 3


def test_truths_written_before_replicates(config, base, tmp_path, monkeypatch):
    original = oc.evaluate_replicate

    def checking(*args, **kwargs):
        assert (tmp_path / "oc_truths.csv").exists()
        return original(*args, **kwargs)

    monkeypatch.setattr(oc, "evaluate_replicate", checking)
    oc.run_oc_case02(config, base, replicates=2, oc_seed=20261003, workers=1, out_dir=tmp_path)


def test_common_random_numbers_across_scenarios(config, base, tmp_path):
    oc.run_oc_case02(config, base, replicates=3, oc_seed=20261003, workers=1, out_dir=tmp_path)
    rows = pd.read_parquet(tmp_path / "oc_replicates.parquet")
    for _, group in rows[rows.scenario.isin(["A", "B", "C", "D", "D_leaked_tier"])].groupby(
        "replicate"
    ):
        assert group.n_treatment.nunique() == 1
        assert group.n_control.nunique() == 1


def test_leaked_tier_view(base):
    assert np.any(base.with_tier("leaked").old_inviews != base.old_inviews)
    assert base.with_tier("age") is base
    counts = np.diff(base.offsets)
    iv = pd.DataFrame(
        dict(
            user_id=np.repeat(base.user_id, counts),
            impression_id=np.arange(len(base.iv_old)),
            impression_time=base.iv_time,
            position=np.zeros(len(base.iv_old), dtype=int),
            article_id=base.iv_article_id,
            age_hours=base.iv_age_hours,
            click=base.iv_click,
        )
    )
    users = pd.DataFrame(
        dict(
            user_id=base.user_id,
            device_type=base.device_type,
            is_subscriber=base.is_subscriber,
            pre_period_clicks_21d=base.pre_period_clicks_21d,
        )
    )
    rebuilt = ri.build_base(iv, users)
    np.testing.assert_array_equal(rebuilt.leaked_old_inviews, rebuilt.old_inviews)


def test_story_holds():
    frame = pd.DataFrame(
        [
            dict(estimator=e, p_value=0.01, rule_row=5, guardrail_verdict_5="pass", srm_p=0.1)
            for e in ("naive_e2", "delta_e2")
        ]
    )
    assert oc.story_holds(frame)
    for estimator, col, value in (
        ("naive_e2", "p_value", 0.05),
        ("delta_e2", "p_value", 0.05),
        ("delta_e2", "rule_row", 4),
        ("delta_e2", "guardrail_verdict_5", "inconclusive"),
        ("delta_e2", "srm_p", 0.0001),
    ):
        changed = frame.copy()
        changed.loc[changed.estimator == estimator, col] = value
        assert not oc.story_holds(changed)


def test_outputs_carry_no_id_columns(config, base, tmp_path):
    oc.run_oc_case02(config, base, replicates=2, oc_seed=20261003, workers=1, out_dir=tmp_path)
    for path in tmp_path.rglob("*"):
        if path.suffix in (".csv", ".parquet"):
            frame = pd.read_csv(path) if path.suffix == ".csv" else pd.read_parquet(path)
            assert not {"user_id", "article_id", "draw_id"}.intersection(frame.columns)
