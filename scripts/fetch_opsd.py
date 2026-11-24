"""Fetch and slice the German load series of Open Power System Data for Case 03.

    uv run python scripts/fetch_opsd.py --year 2019 --source PATH_OR_URL
        [--out data/raw/opsd] [--manifest data/manifests/case03.json]
        [--timestamp-column utc_timestamp] [--load-column DE_load_actual_entsoe_transparency]

Package: Open Power System Data, Data Package Time series, version 2020-10-06,
DOI 10.25832/time_series/2020-10-06, file time_series_60min_singleindex.csv. The download address is
not stored here: the author supplies it from the package page https://doi.org/10.25832/time_series/2020-10-06
as --source (an http(s) or file URL, fetched with urllib, or a path to a copy of the file).

The script computes the sha256 of the whole file, keeps the rows whose UTC timestamp falls in the
requested calendar year, and asserts on that slice (any failure prints every count, ends with
"FETCH RESULT: REFUSED <names of the failed checks>", exits 2 and writes nothing):

    columns         the timestamp column and the load column both exist in the header (if not, the
                    header names that look like timestamps are printed)
    timestamps      every timestamp in the slice parses
    rows            8,760 rows (8,784 in a leap year)
    unique          no timestamp appears twice
    ordered         every row is exactly one hour after the one before
    span            the first row is the year's first hour and the last row is the year's last hour
    load numeric    no missing and no non-numeric load value (empty, text, nan or inf)
    load positive   every load value is greater than zero

On success it writes the slice (two columns, LF line endings, the file's own value text) to --out
and a manifest to --manifest. The default --out is data/raw/opsd, which .gitignore covers through
data/raw/. The UTC column name utc_timestamp is the default of --timestamp-column on the authority
of an external review; it has not been read from the file by the author, so it is an option, not a
certainty. The last line is

    FETCH RESULT: DONE rows=8760 sha256=<first 12 of the sha256 of the downloaded file>

Test hook: --expected-rows N replaces the calendar row count with N (the first N hours of the year).
Any other value than the calendar one is recorded in the manifest as expected_rows_override.
Exit codes: 0 done, 2 refused. Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

PACKAGE = "Open Power System Data, Data Package Time series"
VERSION = "2020-10-06"
DOI = "10.25832/time_series/2020-10-06"
FILE_NAME = "time_series_60min_singleindex.csv"
LOAD_COLUMN = "DE_load_actual_entsoe_transparency"
TIMESTAMP_COLUMN = "utc_timestamp"
# Assembled from the fields the Case 03 design spec (section 6) quotes: Chicago author-date, author,
# title, version, DOI, and the note on primary data. The year is the year of the version date. The
# exact wording on the package page has not been compared with this string.
ATTRIBUTION = (
    f"Open Power System Data. {VERSION[:4]}. Data Package Time series. Version {VERSION}. "
    f"https://doi.org/{DOI}. Primary data from various sources."
)
HOUR = timedelta(hours=1)
TIMESTAMPISH = re.compile(r"time|utc|cet|cest|date", re.IGNORECASE)


class RefusedError(Exception):
    """A failed check: the run ends with FETCH RESULT: REFUSED and exit 2."""


def say(*parts: object) -> None:
    print(*parts)
    sys.stdout.flush()


def calendar_rows(year: int) -> int:
    return (datetime(year + 1, 1, 1, tzinfo=UTC) - datetime(year, 1, 1, tzinfo=UTC)) // HOUR


def parse_timestamp(text: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def stamp_text(stamp: datetime) -> str:
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_to_file(source: str, target: Path) -> str:
    """Copy or download the source to target and return the sha256 of its bytes."""
    digest = hashlib.sha256()
    try:
        if "://" in source:
            say(f"download: {source}")
            handle = urllib.request.urlopen(source, timeout=120)
        else:
            say(f"copy: {source}")
            handle = open(source, "rb")
        with handle, open(target, "wb") as out:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
    except (OSError, urllib.error.URLError, ValueError) as error:
        raise RefusedError(f"cannot read source {source!r}: {error}") from error
    return digest.hexdigest()


def read_slice(path: Path, year: int, ts_col: str, load_col: str) -> tuple[list[str], list[tuple]]:
    """Header names and the rows of the year as (datetime, timestamp text, load text)."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise RefusedError("the source file is empty") from None
        header = [name.lstrip("﻿") for name in header]
        say(f"header: {len(header)} columns")
        missing = [c for c in (ts_col, load_col) if c not in header]
        if missing:
            looks = [name for name in header if TIMESTAMPISH.search(name)]
            say(f"columns missing from the header: {missing}")
            say(f"header names that look like timestamps: {looks}")
            raise RefusedError(f"columns: missing {missing}")
        ts_at, load_at = header.index(ts_col), header.index(load_col)
        rows: list[tuple] = []
        unparsed = 0
        for record in reader:
            text = record[ts_at] if ts_at < len(record) else ""
            stamp = parse_timestamp(text)
            if stamp is None:
                unparsed += 1
                continue
            if stamp.year == year:
                rows.append((stamp, text, record[load_at] if load_at < len(record) else ""))
    say(f"rows read in {year}: {len(rows)}; timestamps that do not parse (any year): {unparsed}")
    return header, rows


def check_slice(rows: list[tuple], year: int, expected: int) -> tuple[list[str], dict]:
    """Run the assertions; return (failed check names, counts)."""
    stamps = [r[0] for r in rows]
    first_hour = datetime(year, 1, 1, tzinfo=UTC)
    wanted = {first_hour + i * HOUR for i in range(expected)}
    unique = len(set(stamps))
    off_hour = sum(1 for s in stamps if s.minute or s.second or s.microsecond)
    unordered = sum(1 for a, b in zip(stamps, stamps[1:], strict=False) if b - a != HOUR)
    non_numeric = empty = nonpositive = 0
    for _, _, text in rows:
        if text.strip() == "":
            empty += 1
            continue
        try:
            value = float(text)
        except ValueError:
            non_numeric += 1
            continue
        if not math.isfinite(value):
            non_numeric += 1
        elif value <= 0:
            nonpositive += 1
    counts = {
        "rows": len(rows),
        "expected_rows": expected,
        "unique_timestamps": unique,
        "duplicate_timestamps": len(rows) - unique,
        "missing_hours": len(wanted - set(stamps)),
        "unexpected_hours": len(set(stamps) - wanted),
        "off_hour_timestamps": off_hour,
        "steps_not_one_hour": unordered,
        "empty_load": empty,
        "non_numeric_load": non_numeric,
        "non_positive_load": nonpositive,
    }
    failed = []
    if len(rows) != expected:
        failed.append("rows")
    if unique != len(rows):
        failed.append("unique")
    if unordered or off_hour or counts["missing_hours"] or counts["unexpected_hours"]:
        failed.append("ordered")
    if not rows or stamps[0] != first_hour or stamps[-1] != first_hour + (expected - 1) * HOUR:
        failed.append("span")
    if empty or non_numeric:
        failed.append("load numeric")
    if nonpositive:
        failed.append("load positive")
    return failed, counts


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(options: argparse.Namespace) -> None:
    if not options.source:
        raise RefusedError(
            "--source is required: a path or URL of "
            f"{FILE_NAME} (the author supplies it from https://doi.org/{DOI})"
        )
    year = options.year
    expected = options.expected_rows if options.expected_rows is not None else calendar_rows(year)
    if expected != calendar_rows(year):
        say(f"TEST HOOK --expected-rows {expected} (calendar value {calendar_rows(year)})")
    base = Path(str(options.source).replace("\\", "/")).name
    if "://" not in options.source and base != FILE_NAME:
        say(f"WARN source file name is {base!r}, the package file is {FILE_NAME!r}")
    with tempfile.TemporaryDirectory() as scratch:
        local = Path(scratch) / FILE_NAME
        file_sha = fetch_to_file(options.source, local)
        say(f"source sha256 {file_sha}")
        header, rows = read_slice(local, year, options.timestamp_column, options.load_column)
    failed, counts = check_slice(rows, year, expected)
    for name, value in counts.items():
        say(f"count {name} = {value}")
    if failed:
        say(f"checks failed: {failed}")
        raise RefusedError(f"checks failed: {', '.join(failed)}")
    say("checks passed: columns, rows, unique, ordered, span, load numeric, load positive")
    out_dir = Path(options.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    slice_path = out_dir / f"opsd_de_load_{year}.csv"
    say(("overwriting " if slice_path.exists() else "writing ") + slice_path.as_posix())
    with open(slice_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([options.timestamp_column, options.load_column])
        writer.writerows((text, load) for _, text, load in rows)
    slice_sha = sha256_of(slice_path)
    manifest = {
        "package": PACKAGE,
        "package_version": VERSION,
        "doi": DOI,
        "source": options.source,
        "source_file_name": FILE_NAME,
        "source_sha256": file_sha,
        "timestamp_column": options.timestamp_column,
        "load_column": options.load_column,
        "column_names": [options.timestamp_column, options.load_column],
        "source_column_count": len(header),
        "year": year,
        "row_count": len(rows),
        "first_timestamp": stamp_text(rows[0][0]),
        "last_timestamp": stamp_text(rows[-1][0]),
        "missing_count": 0,
        "slice_file": slice_path.as_posix(),
        "slice_sha256": slice_sha,
        "attribution": ATTRIBUTION,
        "expected_rows_override": expected if expected != calendar_rows(year) else None,
    }
    manifest_path = Path(options.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    say(f"slice sha256 {slice_sha}")
    say(f"wrote {manifest_path.as_posix()}")
    say(f"FETCH RESULT: DONE rows={len(rows)} sha256={file_sha[:12]}")


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        say(f"FETCH RESULT: REFUSED arguments: {message}")
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
    parser = Parser(description="Fetch and slice the OPSD German load series for one UTC year.")
    parser.add_argument("--year", type=int, required=True, help="calendar year in UTC, e.g. 2019")
    parser.add_argument("--source", help="path, or http(s)/file URL, of the package file")
    parser.add_argument("--out", default="data/raw/opsd", help="directory for the slice")
    parser.add_argument("--manifest", default="data/manifests/case03.json")
    parser.add_argument("--timestamp-column", default=TIMESTAMP_COLUMN)
    parser.add_argument("--load-column", default=LOAD_COLUMN)
    parser.add_argument("--expected-rows", type=int, help="test hook: replaces the calendar count")
    options = parser.parse_args(argv)
    try:
        run(options)
    except RefusedError as refusal:
        say(f"FETCH RESULT: REFUSED {refusal}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
