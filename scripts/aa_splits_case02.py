#!/usr/bin/env python3
"""Case 02 A/A splits of the real users, written to results/case02/aa_splits_small.json.

Source of the decision memo's "across 400 A/A splits of the real users the naive test rejects a
true null at nominal 5% in 16.8% of splits (MC SE 1.9 points)". The splits are not
re-implemented: this wrapper runs the archived, seeded script
results/case02/large_variant_feasibility/ebnerd_feasibility.py unchanged (its md5 and its seed and
split-count lines are checked first) on the EB-NeRD small variant, reads four lines of its output
and records them with the inputs' md5s. The rejection counts are recovered exactly from the
printed three-decimal rates, since S = 400 makes each count's rate unique at that precision. The
Monte Carlo SE of a rejection rate p over S splits is sqrt(p (1 - p) / S).
Usage: uv run python scripts/aa_splits_case02.py   (repository root; needs data/raw/ebnerd_small)
"""

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from importlib.metadata import version
from math import sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("results/case02/large_variant_feasibility/ebnerd_feasibility.py")
SCRIPT_MD5_PREFIX = "2053e27eb0cf"
DATA = Path("data/raw/ebnerd_small")
DATA_FILES = ("train/behaviors.parquet", "train/history.parquet", "articles.parquet")
OUT = Path("results/case02/aa_splits_small.json")
SEED_LINE = "rng = np.random.default_rng(20260929)"
SPLITS_LINE = "S = 400"
SEED = 20260929
SPLITS = 400
USERS_RE = re.compile(r"^users in split: ([\d,]+)$")
RATIO_RE = re.compile(
    r"^A/A splits \((\d+)\): delta/naive SE ratio median ([\d.]+) \(IQR ([\d.]+) to ([\d.]+)\)$"
)
REJECT_RE = re.compile(
    r"^naive test rejects ([\d.]+), delta-method ([\d.]+), E1 Welch ([\d.]+)"
    r"\s+\(MC SE about ([\d.]+) at 5%\)$"
)
VERDICT_RE = re.compile(r"^verdict on the headline: (.+)$")


def md5(path: Path) -> str:
    """Full md5 hex digest of a file."""
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def one(pattern: re.Pattern[str], lines: list[str]) -> re.Match[str]:
    """The single output line that matches a pattern; anything else stops the run."""
    hits = [m for m in (pattern.match(line) for line in lines) if m]
    if len(hits) != 1:
        raise SystemExit(f"{len(hits)} output lines match {pattern.pattern!r} (expected 1)")
    return hits[0]


def count_from_rate(text: str, splits: int) -> int:
    """The unique integer k with k / splits printed as text at three decimals."""
    found = [k for k in range(splits + 1) if f"{k / splits:.3f}" == text]
    if len(found) != 1:
        raise SystemExit(f"rate {text}: {len(found)} counts out of {splits} print as it")
    return found[0]


def main() -> int:
    """Run the archived script once and write the JSON summary."""
    source = (ROOT / SCRIPT).read_text(encoding="utf-8")
    script_md5 = md5(ROOT / SCRIPT)
    if not script_md5.startswith(SCRIPT_MD5_PREFIX):
        found = script_md5[:12]
        raise SystemExit(f"{SCRIPT.as_posix()} md5 {found} (expected {SCRIPT_MD5_PREFIX})")
    for line in (SEED_LINE, SPLITS_LINE):
        if source.splitlines().count(line) != 1:
            raise SystemExit(f"{SCRIPT.as_posix()}: line {line!r} not found exactly once")
    command = [sys.executable, SCRIPT.as_posix(), DATA.as_posix()]
    env = {**os.environ, "PYTHONUTF8": "1"}
    run = subprocess.run(
        command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8"
    )
    if run.returncode != 0:
        raise SystemExit(f"{SCRIPT.as_posix()} exited {run.returncode}: {run.stderr[-400:]}")
    lines = [line.rstrip() for line in run.stdout.splitlines()]
    users = int(one(USERS_RE, lines).group(1).replace(",", ""))
    ratio = one(RATIO_RE, lines)
    reject = one(REJECT_RE, lines)
    splits = int(ratio.group(1))
    if splits != SPLITS:
        raise SystemExit(f"output reports {splits} splits (expected {SPLITS})")
    names = ("naive", "delta_method", "e1_welch")
    counts = {name: count_from_rate(reject.group(i), splits) for i, name in enumerate(names, 1)}
    rate = {name: k / splits for name, k in counts.items()}
    summary = {
        "aa_splits": {
            "users": users,
            "users_per_arm_mean": users / 2,
            "splits": splits,
            "seed": SEED,
            "nominal_level": 0.05,
            "naive_rejections": counts["naive"],
            "naive_reject_rate": rate["naive"],
            "naive_reject_rate_mc_se": sqrt(rate["naive"] * (1 - rate["naive"]) / splits),
            "delta_method_rejections": counts["delta_method"],
            "delta_method_reject_rate": rate["delta_method"],
            "e1_welch_rejections": counts["e1_welch"],
            "e1_welch_reject_rate": rate["e1_welch"],
            "mc_se_at_nominal_printed": float(reject.group(4)),
            "se_ratio_median": float(ratio.group(2)),
            "se_ratio_q25": float(ratio.group(3)),
            "se_ratio_q75": float(ratio.group(4)),
        },
        "printed": {
            "naive_rate": reject.group(1),
            "delta_method_rate": reject.group(2),
            "e1_welch_rate": reject.group(3),
            "verdict": one(VERDICT_RE, lines).group(1),
        },
        "provenance": {
            "command": "uv run python scripts/aa_splits_case02.py",
            "inner_command": f"python {SCRIPT.as_posix()} {DATA.as_posix()}",
            "script": SCRIPT.as_posix(),
            "script_md5": script_md5,
            "data": {name: md5(ROOT / DATA / name) for name in DATA_FILES},
            "python": platform.python_version(),
            "numpy": version("numpy"),
            "pandas": version("pandas"),
            "scipy": version("scipy"),
        },
    }
    out = ROOT / OUT
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"wrote {OUT.as_posix()}; users {users}, splits {splits}, naive rejections "
        f"{counts['naive']} ({rate['naive']:.4f})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
