#!/usr/bin/env python3
"""Analytic power tables for Case 01, written to results/case01/.

Default mode writes results/case01/power_table.csv, the design-time table. Design inputs (all
committed): control rate 0.032 (case_studies/01_lost_logs_found_lift/config.yaml
`baseline_yield_rate`), relative lift 0.06 and alpha 0.05 two-sided (design record, Power
paragraph), ship margin +1.0% (config.yaml `decision_rule.ship_if_lower_bound_above_relative`).
The interval is the delta-method interval on the log ratio of two independent proportions, the one
the ship rule reads:
  se(n) = sqrt((1 - p0) / (n p0) + (1 - p1) / (n p1)),  p1 = p0 (1 + lift)
  power_vs_zero   = Phi(log(1 + lift) / se - z)
  power_vs_margin = Phi((log(1 + lift) - log(1 + margin)) / se - z)
The rows at 132,000 and 136,000 reproduce the design record (78.9% and 80%), which checks the
formula.

--itt writes results/case01/power_itt_case01.csv with the same formula at the simulated
intent-to-treat lift (true_effect_itt of scenario D, estimator itt_routed, in
results/case01/oc_scenarios.csv) on a 25,000 grid from 1,000,000 to 3,000,000 per arm, and prints
the smallest size at which the ship rule has 80% power: the source of the decision memo's "about
2 million products per arm". One final look; the interim look is not modelled.
Usage: python scripts/power_table_case01.py [--itt]   (run from the repository root; stdlib only)
"""

import argparse
import csv
import re
import sys
from math import log, sqrt
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "case_studies" / "01_lost_logs_found_lift" / "config.yaml"
SCENARIOS = ROOT / "results" / "case01" / "oc_scenarios.csv"
OUT = ROOT / "results" / "case01" / "power_table.csv"
OUT_ITT = ROOT / "results" / "case01" / "power_itt_case01.csv"
LIFT = 0.06
ALPHA = 0.05
TARGET = 0.80
SIZES = (100000, 120000, 132000, 136000, 140000, 160000, 180000, 197000, 200000)
SIZES_ITT = tuple(range(1_000_000, 3_000_001, 25_000))


def config_value(key: str) -> float:
    """Read one numeric key from config.yaml; exactly one match is required."""
    text = CONFIG.read_text(encoding="utf-8")
    found = re.findall(rf"^\s*{key}:\s*([0-9.]+)", text, re.M)
    if len(found) != 1:
        raise SystemExit(f"config key {key}: {len(found)} matches (expected 1)")
    return float(found[0])


def itt_lift() -> float:
    """Read true_effect_itt of scenario D, estimator itt_routed."""
    with open(SCENARIOS, encoding="utf-8", newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["scenario"] == "D" and row["estimator"] == "itt_routed"
        ]
    if len(rows) != 1:
        raise SystemExit(f"oc_scenarios.csv rows for D/itt_routed: {len(rows)} (expected 1)")
    return float(rows[0]["true_effect_itt"])


def powers(n: int, p0: float, lift: float, margin: float) -> tuple[float, float]:
    """Return (power_vs_zero, power_vs_margin) at n products per arm."""
    nd = NormalDist()
    z = nd.inv_cdf(1 - ALPHA / 2)
    p1 = p0 * (1 + lift)
    se = sqrt((1 - p0) / (n * p0) + (1 - p1) / (n * p1))
    w0 = nd.cdf(log(1 + lift) / se - z)
    w1 = nd.cdf((log(1 + lift) - log(1 + margin)) / se - z)
    return w0, w1


def main() -> int:
    """Write the design table, or with --itt the table at the simulated intent-to-treat lift."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--itt", action="store_true", help="write power_itt_case01.csv at the simulated ITT lift"
    )
    options = parser.parse_args()
    p0 = config_value("baseline_yield_rate")
    margin = config_value("ship_if_lower_bound_above_relative")
    if not options.itt:
        lines = ["n_per_arm,power_vs_zero,power_vs_ship_margin"]
        for n in SIZES:
            w0, w1 = powers(n, p0, LIFT, margin)
            lines.append(f"{n},{w0:.4f},{w1:.4f}")
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {OUT.relative_to(ROOT).as_posix()}")
        return 0
    lift = itt_lift()
    lines = ["n_per_arm,lift,p0,margin,power_vs_zero,power_vs_ship_margin"]
    first = None
    for n in SIZES_ITT:
        w0, w1 = powers(n, p0, lift, margin)
        lines.append(f"{n},{lift},{p0},{margin},{w0:.4f},{w1:.4f}")
        if first is None and w1 >= TARGET:
            first = n
    OUT_ITT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT_ITT.relative_to(ROOT).as_posix()}; smallest n per arm at 80%: {first}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
