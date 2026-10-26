"""Check tracked text files for references unavailable to public readers."""

import argparse
import subprocess
from pathlib import Path

EXTENSIONS = {".md", ".py", ".yaml", ".yml", ".toml", ".json", ".ipynb", ".txt", ".cfg", ".ps1"}
PATTERNS = (
    "notes/",
    "notes\\",
    "docs/design",
    "C:\\Users",
    "Users/adime",
    "Users\\adime",
    "handoff-v",
    "codex-report",
    "plan-2026-",
    "retro",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow", type=Path, default=Path("scripts/public_reader_allow.txt"))
    args = parser.parse_args()
    allowed = set()
    if args.allow.exists():
        for line in args.allow.read_text(encoding="utf-8").splitlines():
            if line:
                path, pattern = line.split(":", 1)
                allowed.add((path, pattern.casefold()))

    tracked = subprocess.run(
        ["git", "ls-files", "-z"], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.split("\0")
    hits = 0
    files = 0
    for relative in tracked:
        path = Path(relative)
        if (
            not relative
            or relative in {".gitignore", "scripts/public_reader_check.py"}
            or path.resolve() == args.allow.resolve()
            or path.suffix.lower() not in EXTENSIONS
        ):
            continue
        files += 1
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            folded = line.casefold()
            for pattern in PATTERNS:
                if pattern.casefold() in folded and (relative, pattern.casefold()) not in allowed:
                    print(f"{relative}:{number}: {pattern}")
                    hits += 1
    if hits:
        return 1
    print(f"public-reader check: 0 hits in {files} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
