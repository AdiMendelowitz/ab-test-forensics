"""Missing values must not become zero during unit aggregation."""

import json
from pathlib import Path

import pandas as pd
import pytest

from abforensics.inference.resampling import _unit_sums


def test_nan_numerator_raises():
    frame = pd.DataFrame({"unit_id": ["a", "b"], "numerator": [1.0, float("nan")]})
    with pytest.raises(ValueError, match="non-finite values"):
        _unit_sums(frame, "unit_id")


def test_none_unit_key_raises():
    frame = pd.DataFrame({"unit_id": ["a", None], "numerator": [1.0, 2.0]})
    with pytest.raises(ValueError, match="missing values"):
        _unit_sums(frame, "unit_id")


def test_complete_rows_preserve_totals():
    frame = pd.DataFrame({"unit_id": ["a", "b"], "numerator": [1.0, 2.0]})
    expected = pd.DataFrame({"numerator": [1.0, 2.0]}, index=pd.Index(["a", "b"], name="unit_id"))
    pd.testing.assert_frame_equal(_unit_sums(frame, "unit_id"), expected)


def test_committed_case02_input_preserves_totals():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "data/manifests/case02.json").read_text(encoding="utf-8"))
    unit_col = manifest["config"]["design"]["analysis_unit"]
    users_path = root / "data/generated/case02" / manifest["files"]["users"]
    if not users_path.exists():
        pytest.skip("generated Case 02 users table is not present (untracked, regenerated locally)")
    frame = pd.read_parquet(users_path)
    assert len(frame) == manifest["row_counts"]["users"]
    expected = frame.set_index(unit_col).select_dtypes(include="number")
    pd.testing.assert_frame_equal(_unit_sums(frame, unit_col), expected)
