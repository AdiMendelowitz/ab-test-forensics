import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "changelog_check.py"


@pytest.fixture
def scratch_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    for key, value in [("user.name", "Changelog Test"), ("user.email", "test@example.invalid")]:
        subprocess.run(["git", "config", key, value], cwd=tmp_path, check=True)
    hashes = []
    for day in ("2026-10-01", "2026-10-02"):
        marker = tmp_path / f"marker-{day}.txt"
        marker.write_text(day + "\n", encoding="utf-8")
        subprocess.run(["git", "add", marker.name], cwd=tmp_path, check=True)
        environment = os.environ.copy()
        environment["GIT_AUTHOR_DATE"] = f"{day}T12:00:00+00:00"
        environment["GIT_COMMITTER_DATE"] = f"{day}T12:00:00+00:00"
        subprocess.run(
            ["git", "commit", "-m", f"Commit {day}"],
            cwd=tmp_path,
            env=environment,
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )
        hashes.append(result.stdout.strip())
    return tmp_path, hashes


def run_check(repo, content, *options):
    changelog = repo / "CHANGELOG.md"
    changelog.write_text(content, encoding="utf-8", newline="\n")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--file", str(changelog), *options],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_complete_file_passes(scratch_repo):
    repo, hashes = scratch_repo
    content = (
        f"## 2 October 2026\n- Second ({hashes[1]}).\n## 1 October 2026\n- First ({hashes[0]}).\n"
    )
    result = run_check(repo, content, "--coverage")
    assert result.returncode == 0
    assert result.stdout.endswith("2 commits since 2026-09-30, 0 problems\n")


def test_bullet_without_hash_reports_line(scratch_repo):
    repo, _hashes = scratch_repo
    result = run_check(repo, "## 2 October 2026\n- Untagged.\n")
    assert result.returncode == 1
    assert "CHANGELOG.md:2: bullet without hash\n" in result.stdout


def test_unreachable_hash_reports_problem(scratch_repo):
    repo, _hashes = scratch_repo
    result = run_check(repo, "## 2 October 2026\n- Unknown (abcdef1).\n")
    assert result.returncode == 1
    assert "CHANGELOG.md:2: hash abcdef1 not reachable from HEAD\n" in result.stdout


def test_missing_day_requires_coverage_option(scratch_repo):
    repo, hashes = scratch_repo
    content = f"## 2 October 2026\n- Second ({hashes[1]}).\n"
    without = run_check(repo, content)
    with_coverage = run_check(repo, content, "--coverage")
    assert without.returncode == 0
    assert with_coverage.returncode == 1
    assert f"commit {hashes[0]} (2026-10-01)" in with_coverage.stdout


def test_no_matching_commit_is_accepted(scratch_repo):
    repo, _hashes = scratch_repo
    result = run_check(repo, "## 2 October 2026\n- Early work (no matching commit).\n")
    assert result.returncode == 0
    assert result.stdout.endswith("0 problems\n")


def test_since_after_both_commits_requires_none(scratch_repo):
    repo, _hashes = scratch_repo
    result = run_check(
        repo,
        "## 2 October 2026\n- Early work (no matching commit).\n",
        "--coverage",
        "--since",
        "2026-10-03",
    )
    assert result.returncode == 0
    assert result.stdout.endswith("0 commits since 2026-10-03, 0 problems\n")
