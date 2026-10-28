"""Check CHANGELOG bullets against commits on the first-parent history."""

from __future__ import annotations

import argparse
import re
import subprocess
from datetime import datetime
from pathlib import Path

HEADING = re.compile(r"^## (\d{1,2} [A-Z][a-z]+ \d{4})$")
TAG = re.compile(r"\(((?:[0-9a-f]{7,12})(?:, [0-9a-f]{7,12})*)\)\.?$")
NO_COMMIT = re.compile(r"\(no matching commit\)\.?$")


def git_lines(*args: str) -> list[str]:
    """Return lines from one read-only Git log call."""
    result = subprocess.run(["git", "log", *args], capture_output=True, text=True, check=True)
    return result.stdout.splitlines()


def parse_bullets(path: Path) -> tuple[list[tuple[int, str, str | None, list[str]]], list[str]]:
    """Collect bullets with their section dates and any format problems."""
    bullets = []
    problems = []
    day = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        heading = HEADING.fullmatch(line)
        if heading:
            day = datetime.strptime(heading.group(1), "%d %B %Y").date().isoformat()
        if not line.startswith("- "):
            continue
        if day is None:
            problems.append(f"CHANGELOG.md:{number}: bullet outside a section")
        tag = TAG.search(line)
        if tag:
            hashes = tag.group(1).split(", ")
        elif NO_COMMIT.search(line):
            hashes = []
        else:
            hashes = []
            problems.append(f"CHANGELOG.md:{number}: bullet without hash")
        bullets.append((number, line, day, hashes))
    return bullets, problems


def check_hashes(bullets: list[tuple[int, str, str | None, list[str]]], verbose: bool) -> list[str]:
    """Check each tag against the reachable first-parent commits."""
    full_hashes = git_lines("--first-parent", "--pretty=format:%H", "HEAD")
    subjects = {}
    if verbose:
        for record in git_lines("--first-parent", "--pretty=format:%H%x09%s", "HEAD"):
            full_hash, subject = record.split("\t", 1)
            subjects[full_hash] = subject
    problems = []
    for number, _line, _day, hashes in bullets:
        if verbose and not hashes:
            print(f"CHANGELOG.md:{number}: no matching commit")
        for short_hash in hashes:
            matches = [full_hash for full_hash in full_hashes if full_hash.startswith(short_hash)]
            if not matches:
                problems.append(f"CHANGELOG.md:{number}: hash {short_hash} not reachable from HEAD")
            elif verbose:
                for full_hash in matches:
                    print(f"CHANGELOG.md:{number}: {short_hash} {subjects[full_hash]}")
    return problems


def check_coverage(
    bullets: list[tuple[int, str, str | None, list[str]]], since: str
) -> tuple[int, list[str]]:
    """Find first-parent commits absent from their committer day's section."""
    records = git_lines(
        "--first-parent",
        f"--since={since}",
        "--date=short",
        "--pretty=format:%h%x09%cd%x09%s",
        "HEAD",
    )
    problems = []
    for record in records:
        short_hash, day, subject = record.split("\t", 2)
        covered = any(
            section_day == day
            and any(short_hash.startswith(tag) or tag.startswith(short_hash) for tag in tags)
            for _number, _line, section_day, tags in bullets
        )
        if not covered:
            problems.append(
                f"CHANGELOG.md:0: commit {short_hash} ({day}) {subject} "
                "is in no bullet of its day's section"
            )
    return len(records), problems


def main() -> int:
    """Run requested checks and print one final summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default="CHANGELOG.md")
    parser.add_argument("--since", default="2026-09-30")
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    options = parser.parse_args()
    bullets, problems = parse_bullets(Path(options.file))
    problems.extend(check_hashes(bullets, options.verbose))
    count = 0
    if options.coverage:
        count, coverage_problems = check_coverage(bullets, options.since)
        problems.extend(coverage_problems)
    for problem in problems:
        print(problem)
    print(
        f"changelog check: {len(bullets)} bullets, {count} commits since {options.since}, "
        f"{len(problems)} problems"
    )
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
