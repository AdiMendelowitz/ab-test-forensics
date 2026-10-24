"""Case 02: scenario A naive-to-delta decomposition from the replicate cache."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

REPO = Path(__file__).resolve().parents[2]
REPLICATES = REPO / "results" / "case02" / "oc_replicates.parquet"
OC_META = REPO / "results" / "case02" / "oc_meta.json"
OC_SCENARIOS = REPO / "results" / "case02" / "oc_scenarios.csv"
Z = float(stats.norm.ppf(0.975))

pytestmark = pytest.mark.skipif(
    not REPLICATES.exists(), reason="replicate cache absent: run `abf oc case02`"
)


def _scenario_a() -> pd.DataFrame:
    reps = pd.read_parquet(REPLICATES)
    rows = reps[(reps["scenario"] == "A") & reps["estimator"].isin(["naive_e2", "delta_e2"])]
    return rows.pivot(
        index="replicate",
        columns="estimator",
        values=["estimate", "ci_low", "ci_high", "p_value"],
    )


def test_d17_scenario_a_decomposition() -> None:
    meta = json.loads(OC_META.read_text(encoding="utf-8"))
    assert (meta["oc_seed"], meta["replicates"]) == (20261003, 2000)
    wide = _scenario_a()
    assert len(wide) == 2000
    naive, delta = "naive_e2", "delta_e2"
    assert (wide["estimate"][naive] == wide["estimate"][delta]).all()
    width_naive = (wide["ci_high"][naive] - wide["ci_low"][naive]).to_numpy()
    width_delta = (wide["ci_high"][delta] - wide["ci_low"][delta]).to_numpy()
    ratio = width_delta / width_naive
    z_naive = stats.norm.isf(wide["p_value"][naive].to_numpy() / 2)
    z_delta = stats.norm.isf(wide["p_value"][delta].to_numpy() / 2)
    assert np.max(np.abs(ratio - z_naive / z_delta)) < 0.01
    median = float(np.median(ratio))
    assert 1.465 < median < 1.48
    table = pd.read_csv(OC_SCENARIOS)
    published = table.loc[(table["scenario"] == "A") & (table["estimator"] == naive), "rejection"]
    rejections = int((wide["p_value"][naive] < 0.05).sum())
    assert rejections == round(float(published.iloc[0]) * 2000) == 330
    assert int((z_delta * median > Z).sum()) == 326
    assert 0.182 < 2 * stats.norm.sf(Z / median) < 0.184
    signed = np.sign(wide["estimate"][delta].to_numpy()) * z_delta
    assert 0.96 < float(np.std(signed, ddof=1)) < 0.985
