import subprocess
import sys
from pathlib import Path

import nbformat


def test_builder_matches_committed_rollout_notebook(tmp_path):
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "build_case01_rollout_notebook.py"
    output = tmp_path / "analysis.ipynb"
    subprocess.run([sys.executable, str(script), "--out", str(output)], check=True)
    actual = nbformat.read(output, as_version=4)
    committed = nbformat.read(
        root / "case_studies" / "01_lost_logs_found_lift" / "rollout" / "analysis.ipynb",
        as_version=4,
    )
    assert len(actual.cells) == len(committed.cells)
    for actual_cell, committed_cell in zip(actual.cells, committed.cells, strict=True):
        assert actual_cell.cell_type == committed_cell.cell_type
        assert actual_cell.source == committed_cell.source
