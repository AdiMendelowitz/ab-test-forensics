"""Every CSV writer in the repository pins LF line endings, and no committed result CSV holds a CR.

A static companion to test_line_endings.py, which runs some writers on small inputs: this file
reads the source of every writer instead, so a new writer without lineterminator="\\n" fails here
before any run. Scope: src/, scripts/, the code cells of case_studies/*/analysis.ipynb, and
results/**/*.csv on disk.
"""

import ast
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WRITER_ATTRS = {"to_csv", "writer", "DictWriter"}


def _is_csv_writer(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in WRITER_ATTRS:
        return False
    if func.attr == "to_csv":
        return True
    return isinstance(func.value, ast.Name) and func.value.id == "csv"


def _pins_lf(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "lineterminator":
            return isinstance(kw.value, ast.Constant) and kw.value.value == "\n"
    return False


def _cell_source(cell: dict) -> str:
    src = cell.get("source", "")
    text = "".join(src) if isinstance(src, list) else str(src)
    kept = []
    for line in text.splitlines():
        body = line.lstrip()
        kept.append(line[: len(line) - len(body)] + "pass" if body.startswith(("%", "!")) else line)
    return "\n".join(kept)


def _sources() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for folder in ("src", "scripts"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            found.append((path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")))
    for nb in sorted((ROOT / "case_studies").glob("*/analysis.ipynb")):
        cells = json.loads(nb.read_text(encoding="utf-8")).get("cells", [])
        for i, cell in enumerate(cells):
            if cell.get("cell_type") == "code":
                found.append((f"{nb.relative_to(ROOT).as_posix()}#cell{i}", _cell_source(cell)))
    return found


def test_every_csv_writer_pins_lf():
    writers, offenders = 0, []
    for name, text in _sources():
        tree = ast.parse(text, filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_csv_writer(node):
                writers += 1
                if not _pins_lf(node):
                    offenders.append(f"{name}:{node.lineno}")
    assert writers >= 7, f"only {writers} CSV writer calls found: the scan lost its scope"
    assert not offenders, f"CSV writers without lineterminator='\\n': {offenders}"


def _committed_result_csvs() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "results"], cwd=ROOT, capture_output=True, check=True
        ).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError):
        return sorted((ROOT / "results").rglob("*.csv"))
    return sorted(ROOT / name for name in out.split("\0") if name.endswith(".csv"))


def test_committed_result_csvs_have_no_cr():
    csvs = _committed_result_csvs()
    assert csvs, "no CSV under results/: the scan lost its scope"
    offenders = [p.relative_to(ROOT).as_posix() for p in csvs if b"\r" in p.read_bytes()]
    assert not offenders, f"carriage returns in {offenders}"
