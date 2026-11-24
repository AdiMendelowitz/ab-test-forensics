"""Tests for scripts/fetch_opsd.py on synthetic fixtures: no network, no real OPSD file."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
# Beside this file when rehearsed in a drafts folder, else scripts/ of the repository (tests/unit/).
SCRIPT = next(
    p for p in (HERE.with_name("fetch_opsd.py"), HERE.parents[2] / "scripts" / "fetch_opsd.py")
    if p.is_file()
)  # fmt: skip
N = 48
HEADER = ["utc_timestamp", "cet_cest_timestamp", "DE_load_actual_entsoe_transparency", "DE_solar"]


def stamp(year: int, hour: int) -> str:
    moment = datetime(year, 1, 1, tzinfo=UTC) + timedelta(hours=hour)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_csv(
    path: Path, loads: list[str] | None = None, hours: list[int] | None = None, header=None
) -> Path:
    """A year-2019 fixture of N hours, with two rows before it (2018) and two after it (2020)."""
    header = header or HEADER
    hours = list(range(N)) if hours is None else hours
    loads = loads if loads is not None else [str(40000 + h) for h in range(len(hours))]
    lines = [",".join(header)]
    for hour in (-2, -1):
        lines.append(f"{stamp(2019, hour)},x,50000.0,1.0")
    lines += [f"{stamp(2019, h)},x,{v},1.0" for h, v in zip(hours, loads, strict=True)]
    for hour in (8760, 8761):
        lines.append(f"{stamp(2019, hour)},x,60000.0,1.0")
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return path


def run(root: Path, *args: str) -> tuple[int, list[str]]:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=root,
        capture_output=True,
        check=False,
    )
    assert done.stderr == b""
    return done.returncode, done.stdout.decode("utf-8").splitlines()


def fetch(root: Path, source: str, *extra: str, hook: bool = True) -> tuple[int, list[str]]:
    args = ["--year", "2019", "--source", source, "--out", "out", "--manifest", "man/case03.json"]
    return run(root, *args, *(["--expected-rows", str(N)] if hook else []), *extra)


def nothing_written(root: Path) -> bool:
    return not (root / "out").exists() and not (root / "man").exists()


def test_clean_year_writes_slice_and_manifest(tmp_path: Path) -> None:
    source = make_csv(tmp_path / "time_series_60min_singleindex.csv")
    file_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    code, lines = fetch(tmp_path, str(source))
    assert code == 0
    assert lines[-1] == f"FETCH RESULT: DONE rows=48 sha256={file_sha[:12]}"
    slice_path = tmp_path / "out" / "opsd_de_load_2019.csv"
    data = slice_path.read_bytes().decode("utf-8").split("\n")
    assert data[0] == "utc_timestamp,DE_load_actual_entsoe_transparency"
    assert data[1] == "2019-01-01T00:00:00Z,40000"
    assert data[-2] == "2019-01-02T23:00:00Z,40047"
    assert len(data) == N + 2 and data[-1] == ""
    manifest = json.loads((tmp_path / "man" / "case03.json").read_text(encoding="utf-8"))
    assert manifest["package_version"] == "2020-10-06"
    assert manifest["doi"] == "10.25832/time_series/2020-10-06"
    assert manifest["source"] == str(source)
    assert manifest["source_sha256"] == file_sha
    assert manifest["column_names"] == ["utc_timestamp", "DE_load_actual_entsoe_transparency"]
    assert (manifest["year"], manifest["row_count"], manifest["missing_count"]) == (2019, 48, 0)
    assert manifest["first_timestamp"] == "2019-01-01T00:00:00Z"
    assert manifest["last_timestamp"] == "2019-01-02T23:00:00Z"
    assert manifest["slice_sha256"] == hashlib.sha256(slice_path.read_bytes()).hexdigest()
    assert "https://doi.org/10.25832/time_series/2020-10-06" in manifest["attribution"]
    assert manifest["expected_rows_override"] == 48


def test_file_url_goes_through_urllib(tmp_path: Path) -> None:
    source = make_csv(tmp_path / "time_series_60min_singleindex.csv")
    file_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    code, lines = fetch(tmp_path, source.as_uri())
    assert code == 0
    assert lines[-1] == f"FETCH RESULT: DONE rows=48 sha256={file_sha[:12]}"


def test_timestamp_column_is_an_option(tmp_path: Path) -> None:
    header = ["utc_stamp", "cet_cest_timestamp", HEADER[2], "DE_solar"]
    source = make_csv(tmp_path / "f.csv", header=header)
    file_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    code, lines = fetch(tmp_path, str(source), "--timestamp-column", "utc_stamp")
    assert code == 0
    assert lines[-1] == f"FETCH RESULT: DONE rows=48 sha256={file_sha[:12]}"


def test_missing_hour_refused(tmp_path: Path) -> None:
    hours = [h for h in range(N) if h != 10]
    source = make_csv(tmp_path / "f.csv", hours=hours)
    code, lines = fetch(tmp_path, str(source))
    assert code == 2
    assert "count rows = 47" in lines and "count missing_hours = 1" in lines
    assert lines[-1] == "FETCH RESULT: REFUSED checks failed: rows, ordered"
    assert nothing_written(tmp_path)


def test_duplicated_hour_refused(tmp_path: Path) -> None:
    hours = list(range(N))
    hours[11] = 10
    source = make_csv(tmp_path / "f.csv", hours=hours)
    code, lines = fetch(tmp_path, str(source))
    assert code == 2
    assert "count duplicate_timestamps = 1" in lines
    assert lines[-1] == "FETCH RESULT: REFUSED checks failed: unique, ordered"
    assert nothing_written(tmp_path)


@pytest.mark.parametrize(
    ("bad", "count_line", "failed"),
    [
        ("abc", "count non_numeric_load = 1", "load numeric"),
        ("nan", "count non_numeric_load = 1", "load numeric"),
        ("", "count empty_load = 1", "load numeric"),
        ("0", "count non_positive_load = 1", "load positive"),
        ("-5.5", "count non_positive_load = 1", "load positive"),
    ],
)
def test_bad_load_value_refused(tmp_path: Path, bad: str, count_line: str, failed: str) -> None:
    loads = [str(40000 + h) for h in range(N)]
    loads[5] = bad
    source = make_csv(tmp_path / "f.csv", loads=loads)
    code, lines = fetch(tmp_path, str(source))
    assert code == 2
    assert count_line in lines
    assert lines[-1] == f"FETCH RESULT: REFUSED checks failed: {failed}"
    assert nothing_written(tmp_path)


def test_missing_column_refused_and_timestamp_like_names_printed(tmp_path: Path) -> None:
    header = ["not_utc", "cet_cest_timestamp", HEADER[2], "DE_solar"]
    source = make_csv(tmp_path / "f.csv", header=header)
    code, lines = fetch(tmp_path, str(source))
    assert code == 2
    assert "header names that look like timestamps: ['not_utc', 'cet_cest_timestamp']" in lines
    assert lines[-1] == "FETCH RESULT: REFUSED columns: missing ['utc_timestamp']"
    assert nothing_written(tmp_path)


def test_default_expects_a_full_year(tmp_path: Path) -> None:
    source = make_csv(tmp_path / "f.csv")
    code, lines = fetch(tmp_path, str(source), hook=False)
    assert code == 2
    assert "count expected_rows = 8760" in lines
    assert lines[-1] == "FETCH RESULT: REFUSED checks failed: rows, ordered, span"
    assert nothing_written(tmp_path)


def test_source_is_required(tmp_path: Path) -> None:
    code, lines = run(tmp_path, "--year", "2019")
    assert code == 2
    assert lines[-1].startswith("FETCH RESULT: REFUSED --source is required")


def test_unreadable_source_refused(tmp_path: Path) -> None:
    code, lines = fetch(tmp_path, str(tmp_path / "absent.csv"))
    assert code == 2
    assert lines[-1].startswith("FETCH RESULT: REFUSED cannot read source")
    assert nothing_written(tmp_path)


def test_calendar_row_counts() -> None:
    spec = importlib.util.spec_from_file_location("fetch_opsd_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_opsd_under_test"] = module
    spec.loader.exec_module(module)
    assert (module.calendar_rows(2019), module.calendar_rows(2020)) == (8760, 8784)
