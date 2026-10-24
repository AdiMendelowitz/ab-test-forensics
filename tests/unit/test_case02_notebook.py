"""Case 02 notebook: cmd_run wiring, the no-verdict-words rule, the two-minute line."""

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
    "Users were randomised and impressions were counted. The user-level interval was about 1.5 "
    "times as wide as the dashboard interval, and the correction did not change the decision: "
    "clicks per user were not shown to rise, while the rate rose as impressions per user fell."
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


def _stdout_lines() -> list[str]:
    lines: list[str] = []
    for cell in _cells():
        for out in cell.get("outputs", []):
            if out.get("output_type") == "stream":
                lines.extend("".join(out["text"]).splitlines())
    return lines


def _two_minute_problems(lines: list[str]) -> list[str]:
    """Where the printed two-minute sentence disagrees with the printed e1 and width ratio."""
    e1 = [line.split() for line in lines if line.startswith("ABF_RESULT e1 ")]
    ratio = [line.split() for line in lines if line.startswith("ABF_WIDTH_RATIO ")]
    sentence = [line for line in lines if line.startswith("Users were randomised")]
    counts = (len(e1), len(ratio), len(sentence))
    if counts != (1, 1, 1):
        return [f"expected one e1, one width-ratio and one sentence line, found {counts}"]
    problems: list[str] = []
    clicks = "were not shown to rise" if float(e1[0][3]) <= 0 else "rose"
    if f"clicks per user {clicks}, while" not in sentence[0]:
        problems.append(f"clicks clause disagrees with the e1 lower bound {e1[0][3]}")
    times = f"about {float(ratio[0][1]):.1f} times as wide"
    if times not in sentence[0]:
        problems.append(f"ratio clause disagrees with ABF_WIDTH_RATIO {ratio[0][1]}")
    return problems


def test_two_minute_sentence_agrees_with_stored_e1_and_width_ratio() -> None:
    assert _two_minute_problems(_stdout_lines()) == []


def test_two_minute_check_rejects_a_sentence_that_contradicts_e1() -> None:
    lines = [line.replace("were not shown to rise", "rose") for line in _stdout_lines()]
    assert any(p.startswith("clicks clause") for p in _two_minute_problems(lines))


def test_two_minute_check_rejects_a_sentence_that_contradicts_the_width_ratio() -> None:
    lines = [line.replace("about 1.5 times", "about 1.4 times") for line in _stdout_lines()]
    assert any(p.startswith("ratio clause") for p in _two_minute_problems(lines))
