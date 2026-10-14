"""Case 02 notebook: cmd_run wiring, spec section 14's banned-word rule, the two-minute line."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from abforensics import cli

REPO = Path(__file__).resolve().parents[2]
NB = REPO / "case_studies" / "02_metric_that_told_two_stories" / "analysis.ipynb"
BANNED = re.compile(r"\b(correct|incorrect|right|wrong)\b", re.IGNORECASE)
TWO_MINUTE = (
    "Users were randomised and impressions were counted. The interval was 1.5 times too narrow, "
    "and fixing it did not change the decision, because clicks per user did not rise and the "
    "rate rose as impressions per user fell."
)


def test_run_case02_executes_the_notebook_in_place(monkeypatch) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cli.cmd_run(REPO, "case02") == 0
    ((cmd, kwargs),) = calls
    assert cmd[-1] == str(NB)
    assert "--execute" in cmd and "--inplace" in cmd
    assert kwargs["cwd"] == NB.parent


def _cells() -> list[dict]:
    return json.loads(NB.read_text(encoding="utf-8"))["cells"]


def test_no_cell_calls_an_estimand_by_a_banned_word() -> None:
    hits = []
    for cell in _cells():
        source = "".join(cell["source"])
        match = BANNED.search(source)
        if match:
            hits.append(match.group(0))
    assert hits == []


def test_last_code_cell_prints_the_two_minute_version() -> None:
    last = [c for c in _cells() if c["cell_type"] == "code"][-1]
    streams = [o for o in last.get("outputs", []) if o.get("output_type") == "stream"]
    assert streams, "the committed notebook carries no outputs: run `abf run case02`"
    text = "".join("".join(o["text"]) for o in streams)
    assert text.splitlines()[0] == TWO_MINUTE
