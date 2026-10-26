import subprocess
import sys
from pathlib import Path


def test_public_reader_check_and_allow_list(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts" / "public_reader_check.py"
    git = [
        "git",
        "-c",
        "user.name=Reader Test",
        "-c",
        "user.email=reader@example.invalid",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run([*git, "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "clean.md").write_text("By AdiMendelowitz.\n", encoding="utf-8")
    (tmp_path / "private.md").write_text("see notes/plan.md\n", encoding="utf-8")
    subprocess.run(
        [*git, "add", "clean.md", "private.md"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        [*git, "commit", "-m", "Test fixture"], cwd=tmp_path, check=True, capture_output=True
    )
    result = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 1
    assert result.stdout.splitlines() == ["private.md:1: notes/"]
    allow = tmp_path / "allow.txt"
    allow.write_text("private.md:notes/\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(script), "--allow", str(allow)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "public-reader check: 0 hits in 2 files"
