"""Tracked-data licence guard: Case 02 README, Data provenance."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IDENTIFIERS = {"user_id", "article_id", "draw_id"}
ROW_LIMIT = 1000


class _HTMLTables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.stack.append({"rows": [], "row": None, "cell": None})
        elif self.stack:
            table = self.stack[-1]
            if tag == "tr":
                table["row"] = []
            elif tag in {"td", "th"} and table["row"] is not None:
                table["cell"] = [tag, ""]

    def handle_data(self, data):
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"][1] += data

    def handle_endtag(self, tag):
        if not self.stack:
            return
        table = self.stack[-1]
        if tag in {"td", "th"} and table["cell"] is not None:
            table["row"].append(table["cell"])
            table["cell"] = None
        elif tag == "tr" and table["row"] is not None:
            table["rows"].append(table["row"])
            table["row"] = None
        elif tag == "table":
            rows = self.stack.pop()["rows"]
            columns = {text.strip() for row in rows for kind, text in row if kind == "th"}
            if rows:
                columns.update(text.strip() for _, text in rows[0])
            count = sum(any(kind == "td" for kind, _ in row) for row in rows)
            self.tables.append((columns, count))


def _markdown_tables(text):
    lines = text.splitlines()
    for i in range(1, len(lines)):
        cells = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
        if "|" not in lines[i] or not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        if "|" not in lines[i - 1]:
            continue
        columns = {c.strip().strip("` ") for c in lines[i - 1].strip().strip("|").split("|")}
        count = 0
        for line in lines[i + 1 :]:
            if "|" not in line or not line.strip():
                break
            count += 1
        yield columns, count


def _json_columns(value):
    if isinstance(value, dict):
        yield from value
        for child in value.values():
            yield from _json_columns(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_columns(child)


def _text_tables(text):
    yield from _markdown_tables(text)
    parser = _HTMLTables()
    parser.feed(text)
    yield from parser.tables


def _allowed_large_table(relative):
    return (
        relative == Path("data/manifests/case02.json")
        or (relative.parent == Path("results/case02") and relative.suffix == ".csv")
        or "figures" in relative.parts[:-1]
    )


def check_tracked_files(root, tracked):
    """Return only file/identifier or file/row-count pairs, never data rows."""
    hits = []
    for name in tracked:
        relative = Path(name)
        try:
            text = (root / relative).read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            continue
        if "\0" in text:
            continue
        columns = set()
        tables = []
        excluded = relative.parts[0] in {"src", "tests", "docs"} or relative.suffix == ".py"
        if relative.suffix == ".ipynb":
            notebook = json.loads(text)
            for cell in notebook.get("cells", []):
                for output in cell.get("outputs", []):
                    representations = []
                    if "text" in output:
                        representations.append(output["text"])
                    for mime, value in output.get("data", {}).items():
                        if mime.startswith("text/"):
                            representations.append(value)
                    for value in representations:
                        output_text = "".join(value) if isinstance(value, list) else value
                        columns.update(
                            re.findall(r"\b(?:user_id|article_id|draw_id)\b", output_text)
                        )
                        tables.extend(_text_tables(output_text))
        else:
            if relative.suffix in {".csv", ".tsv"}:
                rows = list(
                    csv.reader(
                        io.StringIO(text), delimiter="\t" if relative.suffix == ".tsv" else ","
                    )
                )
                if rows:
                    tables.append((set(rows[0]), sum(bool(row) for row in rows[1:])))
            else:
                tables.extend(_text_tables(text))
            if relative.suffix == ".json":
                columns.update(_json_columns(json.loads(text)))
            columns.update(
                re.findall(r"(?m)^\s*[\"']?(user_id|article_id|draw_id)[\"']?\s*[:=]", text)
            )
            if relative.suffix == ".txt":
                for delimiter in (",", "\t"):
                    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
                    if len(rows) > 1 and len(rows[0]) > 1:
                        if all(len(row) == len(rows[0]) for row in rows[1:] if row):
                            columns.update(cell.strip() for cell in rows[0])
        for table_columns, count in tables:
            columns.update(table_columns)
            if count > ROW_LIMIT and not _allowed_large_table(relative):
                hits.append((relative.as_posix(), count))
        if not excluded:
            hits.extend(
                (relative.as_posix(), identifier) for identifier in sorted(columns & IDENTIFIERS)
            )
    return sorted(set(hits), key=lambda hit: (hit[0], str(hit[1])))


_SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "build",
    "dist",
    ".ipynb_checkpoints",
    "parts",
    "__MACOSX",
}
_SKIP_RELATIVE = {"notes", "data/generated", "data/raw"}


def _walk(root):
    """Every file under root except the untracked roots of .gitignore: a superset of
    git ls-files."""
    found = []
    for here, dirs, files in os.walk(root):
        rel_here = Path(here).relative_to(root).as_posix()
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _SKIP_DIR_NAMES
            and not d.endswith(".egg-info")
            and (d if rel_here == "." else f"{rel_here}/{d}") not in _SKIP_RELATIVE
        )
        for name in files:
            found.append((Path(here) / name).relative_to(root).as_posix())
    return found


def tracked_files(root):
    """git ls-files when a repository is present; otherwise the walk, so a clean clone without
    .git runs the same guard."""
    if (root / ".git").exists():
        return subprocess.run(
            ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.splitlines()
    return _walk(root)


def test_tracked_files_contain_no_per_user_data():
    assert not (hits := check_tracked_files(REPO, tracked_files(REPO))), hits


def test_fallback_walk_lists_files_without_git(tmp_path):
    (tmp_path / "keep.csv").write_text("count\n1\n", encoding="utf-8", newline="\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "skip.csv").write_text("user_id\n1\n", encoding="utf-8", newline="\n")
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "skip2.csv").write_text(
        "user_id\n1\n", encoding="utf-8", newline="\n"
    )
    assert tracked_files(tmp_path) == ["keep.csv"]


def test_walk_is_a_superset_of_git_ls_files():
    tracked = set(
        subprocess.run(
            ["git", "ls-files"], cwd=REPO, check=True, capture_output=True, text=True
        ).stdout.splitlines()
    )
    missing = sorted(tracked - set(_walk(REPO)))
    assert not missing, missing


@pytest.mark.parametrize("identifier", sorted(IDENTIFIERS))
def test_forbidden_csv_header_is_caught(tmp_path, identifier):
    path = tmp_path / "table.csv"
    path.write_text(f"{identifier},count\n1,2\n", encoding="utf-8", newline="\n")
    assert check_tracked_files(tmp_path, [path.name]) == [(path.name, identifier)]


@pytest.mark.parametrize("rows", [1000, 1001])
def test_csv_row_limit(tmp_path, rows):
    path = tmp_path / "table.csv"
    path.write_text("count\n" + "1\n" * rows, encoding="utf-8", newline="\n")
    expected = [(path.name, rows)] if rows > ROW_LIMIT else []
    assert check_tracked_files(tmp_path, [path.name]) == expected


def test_notebook_source_is_excluded_but_outputs_are_checked(tmp_path):
    path = tmp_path / "analysis.ipynb"
    notebook = {"cells": [{"source": "user_id", "outputs": []}]}
    path.write_text(json.dumps(notebook), encoding="utf-8", newline="\n")
    assert check_tracked_files(tmp_path, [path.name]) == []
    notebook["cells"][0]["outputs"] = [
        {"data": {"text/html": "<table><tr><th>draw_id</th></tr><tr><td>1</td></tr></table>"}}
    ]
    path.write_text(json.dumps(notebook), encoding="utf-8", newline="\n")
    assert check_tracked_files(tmp_path, [path.name]) == [(path.name, "draw_id")]


@pytest.mark.parametrize("kind", ["markdown", "html"])
def test_other_table_row_limits(tmp_path, kind):
    if kind == "markdown":
        text = "| count |\n| --- |\n" + "| 1 |\n" * 1001
        path = tmp_path / "table.md"
    else:
        text = "<table><tr><th>count</th></tr>" + "<tr><td>1</td></tr>" * 1001 + "</table>"
        text = json.dumps({"cells": [{"outputs": [{"data": {"text/html": text}}]}]})
        path = tmp_path / "analysis.ipynb"
    path.write_text(text, encoding="utf-8", newline="\n")
    assert check_tracked_files(tmp_path, [path.name]) == [(path.name, 1001)]


@pytest.mark.parametrize("name", ["results/case02/table.csv", "figures/table.csv"])
def test_row_allowlist_does_not_allow_identifiers(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.write_text("article_id\n" + "1\n" * 1001, encoding="utf-8", newline="\n")
    assert check_tracked_files(tmp_path, [name]) == [(name, "article_id")]


@pytest.mark.parametrize(
    "name,text",
    [
        ("table.tsv", "user_id\tcount\n1\t2\n"),
        ("table.txt", "count,user_id\n2,1\n"),
        ("table.json", '{"rows": [{"user_id": 1}]}'),
        ("table.md", "| `user_id` | count |\n| --- | --- |\n| 1 | 2 |\n"),
    ],
)
def test_identifier_in_other_text_formats(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8", newline="\n")
    assert check_tracked_files(tmp_path, [name]) == [(name, "user_id")]


def test_html_table_without_header_counts_every_row(tmp_path):
    html = "<table>" + "<tr><td>1</td></tr>" * 1001 + "</table>"
    path = tmp_path / "analysis.ipynb"
    path.write_text(
        json.dumps({"cells": [{"outputs": [{"data": {"text/html": html}}]}]}),
        encoding="utf-8",
        newline="\n",
    )
    assert check_tracked_files(tmp_path, [path.name]) == [(path.name, 1001)]


def test_identifier_in_configuration_comment_is_not_a_column(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "analysis_unit: draw_id  # streams keyed by (seed, draw_id, use)\n",
        encoding="utf-8",
        newline="\n",
    )
    assert check_tracked_files(tmp_path, [path.name]) == []
