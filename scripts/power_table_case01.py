#!/usr/bin/env python3
"""Analytic power table for Case 01, written to results/case01/power_table.csv.

Design inputs (all committed): control rate 0.032 (case_studies/01_lost_logs_found_lift/config.yaml
`baseline_yield_rate`), relative lift 0.06 and alpha 0.05 two-sided (design record, Power
paragraph), ship margin +1.0% (config.yaml `decision_rule.ship_if_lower_bound_above_relative`).
The interval is the delta-method interval on the log ratio of two independent proportions, the one
the ship rule reads:
  se(n) = sqrt((1 - p0) / (n p0) + (1 - p1) / (n p1)),  p1 = p0 (1 + lift)
  power_vs_zero   = Phi(log(1 + lift) / se - z)
  power_vs_margin = Phi((log(1 + lift) - log(1 + margin)) / se - z)
The rows at 132,000 and 136,000 reproduce the design record (78.9% and 80%), which checks the
formula.
Usage: python scripts/power_table_case01.py   (run from the repository root; stdlib only)
"""

import re
from math import log, sqrt
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "case_studies" / "01_lost_logs_found_lift" / "config.yaml"
OUT = ROOT / "results" / "case01" / "power_table.csv"
LIFT = 0.06
ALPHA = 0.05
SIZES = (100000, 120000, 132000, 136000, 140000, 160000, 180000, 197000, 200000)


def config_value(key: str) -> float:
    text = CONFIG.read_text(encoding="utf-8")
    return float(re.search(rf"^\s*{key}:\s*([0-9.]+)", text, re.M).group(1))


def main() -> None:
    p0 = config_value("baseline_yield_rate")
    margin = config_value("ship_if_lower_bound_above_relative")
    p1 = p0 * (1 + LIFT)
    nd = NormalDist()
    z = nd.inv_cdf(1 - ALPHA / 2)
    lines = ["n_per_arm,power_vs_zero,power_vs_ship_margin"]
    for n in SIZES:
        se = sqrt((1 - p0) / (n * p0) + (1 - p1) / (n * p1))
        w0 = nd.cdf(log(1 + LIFT) / se - z)
        w1 = nd.cdf((log(1 + LIFT) - log(1 + margin)) / se - z)
        lines.append(f"{n},{w0:.4f},{w1:.4f}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
