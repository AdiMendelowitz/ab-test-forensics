"""Build the Case 01 staged-rollout notebook from its sections.

The notebook reads only the committed tables in results/case01/rollout/ and the rollout
config.yaml. It simulates nothing: `abf oc case01-rollout` and `abf run case01-rollout` write
those tables. It prints the numbers that the rollout README states and draws figures T1 to T3.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import nbformat as nbf

OUT = (
    Path(__file__).resolve().parents[1]
    / "case_studies"
    / "01_lost_logs_found_lift"
    / "rollout"
    / "analysis.ipynb"
)
cells: list = []


def md(text: str) -> None:
    cells.append(nbf.v4.new_markdown_cell(text.strip("\n")))


def code(text: str) -> None:
    cells.append(nbf.v4.new_code_cell(text.strip("\n")))


md("""
# Case 01, part III: reading a staged rollout

Everything here is simulated. The repaired new station is re-released through a staged
rollout, and the baseline yield drifts upwards while the share routed to it rises. This
notebook reads the committed operating-characteristics tables and the committed run, prints
the numbers the rollout README states, and draws figures T1 to T3. It runs no simulation.
""")

code("""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from abforensics.reporting import PALETTE, apply_style

apply_style()
ROOT = Path.cwd()
while not (ROOT / "pyproject.toml").exists():
    ROOT = ROOT.parent
CASE = ROOT / "case_studies" / "01_lost_logs_found_lift" / "rollout"
RES = ROOT / "results" / "case01" / "rollout"
FIG = CASE / "figures"
FIG.mkdir(exist_ok=True)
config = yaml.safe_load((CASE / "config.yaml").read_text(encoding="utf-8"))

scenarios = pd.read_csv(RES / "oc_rollout_scenarios.csv")
canary = pd.read_csv(RES / "oc_rollout_canary.csv")
s1 = pd.read_csv(RES / "oc_rollout_s1_distribution.csv")
run = pd.read_csv(RES / "run_estimates.csv")
run_canary = pd.read_csv(RES / "run_canary.csv")
meta = json.loads((RES / "oc_rollout_meta.json").read_text(encoding="utf-8"))
COLOURS = [PALETTE["control"], PALETTE["treatment"], PALETTE["series3"]]
pd.set_option("display.width", 160)
print(f"replicates {meta['replicates']}, canary replicates {meta['canary_replicates']}")
""")

md("""
## The schedule

Each product is routed to the new station with the probability of its arrival day's stage.
""")

code("""
print(canary[["stage", "first_day", "last_day", "routing_p", "products"]].to_string(index=False))
""")

md("""
## Operating characteristics

Rates are over the replicates of each scenario. S0 has no effect and is run at four drift
values; S1 and S2 run at the default drift. Each estimator is judged against its own estimand.
""")

code("""
cols = [
    "scenario", "kappa", "estimator", "estimand", "truth_rel", "mean_rel",
    "coverage_rel", "mcse_coverage_rel", "reject_zero", "ship_rate", "flag",
]
print(scenarios[cols].to_string(index=False))
print()
print("S1 sign disagreement (rate, MCSE):", meta["S1_sign_disagreement"])
print("pooled expectation at the default drift:", meta["pooled_expectation_default_kappa"])
print("day-standardised variance shares by stage:", meta["day_standardised_variance_shares"])
print("flags:", meta["flags"])
""")

md("""
## The canary

The ramp's scan-level sample-ratio check, run once at the end of each stage, against the Case 01
scan-logging defect.
""")

code("""
print(canary.to_string(index=False))
""")

md("""
## The committed run

One product-level rollout. It is one draw and is not the evidence for any claim above.
""")

code("""
print(run.to_string(index=False))
print()
print(run_canary.to_string(index=False))
""")

md("""
## Figure T1: the ramp timeline

The routing probability by day, beside the mean control yield implied by the configured drift.
""")

code("""
days = np.arange(1, config["days"] + 1)
p = np.zeros(len(days))
for stage in config["schedule"]:
    first, last = stage["days"]
    p[(days >= first) & (days <= last)] = stage["p"]
baseline = config["baseline_yield_rate"] * (1 + config["trend_kappa"] * (days - 14.5))

fig, ax = plt.subplots(figsize=(8, 4))
ax.step(days, 100 * p, where="mid", color=COLOURS[0], label="routed to the new station")
ax.set_xlabel("day of the rollout")
ax.set_ylabel("share routed to the new station (%)")
ax2 = ax.twinx()
ax2.plot(days, 100 * baseline, color=COLOURS[1], linestyle="--", label="mean control yield")
ax2.set_ylabel("mean control yield (%)")
handles = ax.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
ax.legend(handles=handles, loc="upper left", frameon=False)
fig.tight_layout()
fig.savefig(FIG / "t1_ramp_timeline.png", metadata={"Software": None})
""")

md("""
## Figure T2: three reads of the same rollouts (S1)

Each curve is the distribution of one estimator's relative estimate across the S1 replicates,
drawn from the committed percentiles. The markers show the committed run, which is one draw.
""")

code("""
labels = {
    "pooled": "pooled contrast",
    "day_standardised": "day-standardised",
    "final_stage": "final stage",
}
floor = config["decision_rule"]["ship_if_lower_bound_above_relative"]
truth = float(run.loc[run["estimator"] == "pooled", "truth_rel"].iloc[0])

fig, ax = plt.subplots(figsize=(8, 4.5))
for i, (name, label) in enumerate(labels.items()):
    part = s1[s1["estimator"] == name].sort_values("percentile")
    x = 100 * part["relative_estimate"].to_numpy()
    y = part["percentile"].to_numpy()
    ax.plot(x, y, color=COLOURS[i], label=label)
    est = 100 * float(run.loc[run["estimator"] == name, "estimate_rel"].iloc[0])
    ax.plot([est], [np.interp(est, x, y)], marker="o", color=COLOURS[i], linestyle="none")
refs = (
    (0.0, ":", "no effect"),
    (100 * floor, "--", "ship floor"),
    (100 * truth, "-", "true effect"),
)
for value, style, name in refs:
    ax.axvline(value, color=PALETTE["ink2"], linestyle=style, linewidth=1, label=name)
ax.set_xlabel("relative estimate (%)")
ax.set_ylabel("percentile across S1 replicates")
ax.legend(loc=4, frameon=False)
fig.tight_layout()
fig.savefig(FIG / "t2_s1_estimates.png", metadata={"Software": None})
""")

md("""
## Figure T3: canary power by stage

Exact power of one check at the end of each stage, with the simulated power and two Monte Carlo
standard errors. The dashed line marks 80%.
""")

code("""
fig, ax = plt.subplots(figsize=(6, 4))
x = np.arange(len(canary))
ax.bar(x, 100 * canary["exact_power"], color=COLOURS[0], label="exact power")
ax.errorbar(
    x, 100 * canary["simulated_power"], yerr=200 * canary["mcse"],
    fmt="o", color=COLOURS[1], label="simulated power",
)
ax.axhline(80, color=PALETTE["ink2"], linestyle="--", linewidth=1)
ticks = [f"stage {s}: {100 * r:g}%" for s, r in zip(canary["stage"], canary["routing_p"])]
ax.set_xticks(x, ticks)
ax.set_ylabel("probability the check flags (%)")
ax.set_ylim(0, 105)
ax.legend(loc="upper left", frameon=False)
fig.tight_layout()
fig.savefig(FIG / "t3_canary_power.png", metadata={"Software": None})
""")

BANNED = re.compile(r"\b(correct|incorrect|right|wrong)\b", re.IGNORECASE)
for i, cell in enumerate(cells, 1):
    hit = BANNED.search(cell["source"])
    if hit:
        raise SystemExit(f"banned word {hit.group(0)!r} in nbformat cell {i}")

nb = nbf.v4.new_notebook(cells=cells)
nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
nb.metadata["language_info"] = {"name": "python"}
parser = argparse.ArgumentParser(description="Build the Case 01 rollout notebook.")
parser.add_argument("--out", type=Path, default=OUT, help="path of the notebook to write")
OUT = parser.parse_args().out
OUT.write_bytes(nbf.writes(nb).encode("utf-8"))
print(f"wrote {OUT} with {len(cells)} cells")
