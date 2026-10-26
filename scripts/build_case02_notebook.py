"""Build the Case 02 notebook from its fifteen outlined sections.

The analysis unit is draw_id: one draw is one eligible user. Cell 2 aggregates
the impressions table to per-draw counts and drops it. The guardrail verdict
function is defined in the notebook and labelled as such. No telemetry table
is read.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import nbformat as nbf

OUT = (
    Path(__file__).resolve().parents[1]
    / "case_studies"
    / "02_metric_that_told_two_stories"
    / "analysis.ipynb"
)
cells: list = []


def md(text: str) -> None:
    cells.append(nbf.v4.new_markdown_cell(text.strip("\n")))


def code(text: str) -> None:
    cells.append(nbf.v4.new_code_cell(text.strip("\n")))


md("""
# Case 02: The metric that told two stories

**Decision owner:** feed product lead. This is one look at one experiment; the
action space is ship to 100%, do not ship, or take no decision and investigate.
The +0.5% engagement floor is a product input: the minimum gain that justifies
the serving cost. Every bound is an end of a two-sided 95% interval (z = 1.96),
so a one-sided reading uses the 2.5% level.

| Order | Condition | Action |
|---|---|---|
| 1 | Negative-feedback guardrail breaches | Do not ship |
| 2 | Open decision blocker or must-pass guardrail inconclusive | No decision; investigate first |
| 3 | E1 upper relative-lift bound below zero | Do not ship: engagement loss |
| 4 | E1 lower relative-lift bound above +0.5% and guardrail passes | Ship |
| 5 | Otherwise | Do not ship: the engagement gain is not shown to exceed the +0.5% floor |

| Unit | Definition |
|---|---|
| Randomisation and analysis | draw_id, one draw is one eligible user |
| Eligibility | first logged page view in the split week |
| Exposure | in-view impression |
| Outcome | clicks from eligibility to the end of the split week |
| Analysis clock | 36 hours after the week closes |

**E1, decision estimand: clicks per eligible user.** E[C(1)] - E[C(0)], reported
as an absolute difference and as a relative lift. Every assigned user is in
the denominator. Estimate with a difference in user means, a Welch interval
and a delta-method log-ratio interval for lift.

**E2, dashboard estimand: platform-level click-through rate.** E[sum C] /
E[sum M] per arm: the ratio of expected totals, an impression-weighted rate.
Estimate with the ratio of arm sums and user-level uncertainty.

**E3: mean user-level click rate among users with at least one impression.**
E[C_i / M_i | M_i > 0]. Every user weighs the same. The treatment never removes
the last in-view, so the conditioning set is arm-independent by construction;
this is a simplification. Estimate with Welch on user rates.

E2 mixes a numerator effect with a denominator effect: removing impressions
that carry few clicks can raise the rate without raising clicks per user.
E3 weights light users equally with heavy users, while E2 weights impressions,
so their lifts can disagree and E1 still answers the engagement question.

**Data disclosure.** The control base is real: EB-NeRD small, train split
(manifest base.source). Treatment is simulated; negative feedback is simulated
in both arms. A population user_id identifies the user a draw came from;
repeated draws remain separate analysis units. No truth or telemetry table is
read. The case seed is the first seed from 20261002 satisfying the stated story
rule; 0.975 of 200 seeds showed it at 57,600 per arm. That share was measured
on the shipped generator; the operating-characteristics section's 0.961 story rate uses the harness's
with-replacement draws and 2,000 replicates.
""")

code("""
import json
import math
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from abforensics import event_arrival_lag, invariant_check, srm_check
from abforensics.inference.means import difference_in_means
from abforensics.metrics.ratio import naive_event_rate, ratio_of_sums

t0 = time.perf_counter()
ROOT = Path.cwd()
while not (ROOT / "pyproject.toml").exists():
    ROOT = ROOT.parent
CASE = ROOT / "case_studies" / "02_metric_that_told_two_stories"
DATA = ROOT / "data" / "generated" / "case02"
FIG = CASE / "figures"
FIG.mkdir(exist_ok=True)
config = yaml.safe_load((CASE / "config.yaml").read_text(encoding="utf-8"))
manifest = json.loads((ROOT / "data" / "manifests" / "case02.json").read_text(encoding="utf-8"))
UNIT = "draw_id"

users = pd.read_parquet(DATA / "users.parquet")
assignments = pd.read_parquet(DATA / "assignments.parquet")
impressions = pd.read_parquet(DATA / "impressions.parquet", columns=[UNIT])
clicks = pd.read_parquet(DATA / "clicks.parquet", columns=[UNIT, "clicked_at", "received_at"])
feedback = pd.read_parquet(DATA / "feedback.parquet", columns=[UNIT])
counts = {"users": len(users), "assignments": len(assignments), "impressions": len(impressions),
          "clicks": len(clicks), "feedback": len(feedback)}
for name, n in counts.items():
    assert n == manifest["row_counts"][name], f"{name}: {n} rows against the manifest"

draws = users.merge(assignments, on=UNIT, validate="one_to_one")
draws["inviews"] = draws[UNIT].map(impressions[UNIT].value_counts()).fillna(0).astype(int)
draws["clicks"] = draws[UNIT].map(clicks[UNIT].value_counts()).fillna(0).astype(int)
draws["feedback"] = draws[UNIT].map(feedback[UNIT].value_counts()).fillna(0).astype(int)
del impressions
draws["user_rate"] = draws["clicks"] / draws["inviews"]
assert len(draws) == manifest["realised"]["total_draws"] == 115200, "eligible users"
assert draws["inviews"].min() >= 1, "every eligible user has at least one in-view"
assert int(draws["inviews"].sum()) == 19149136, "in-view total"
assert int(draws["clicks"].sum()) == 1775722, "click total"
assert int(draws["feedback"].sum()) == 156770, "feedback total"
is_t = draws["arm"].eq("treatment").to_numpy()
is_c = draws["arm"].eq("control").to_numpy()
print("rows read:", {k: f"{v:,}" for k, v in counts.items()})
print(f"eligible users (draws): {len(draws):,}; treatment {int(is_t.sum()):,}, control {int(is_c.sum()):,}")
print(f"elapsed {time.perf_counter() - t0:.1f} s")

def show(label, r):
    lo, hi = (float(x) for x in r.relative_confidence_interval)
    print(f"{label}: relative lift {r.relative_lift:+.5f}, 95% interval ({lo:+.5f}, {hi:+.5f}), p = {r.p_value:.4g}")
    print(f"ABF_RESULT {label} {float(r.relative_lift)!r} {lo!r} {hi!r}")


def level(label, control, se_control):
    print(f"ABF_LEVEL {label} {float(control)!r} {float(se_control)!r}")
""")

md("""
## 1. Assignment SRM

Start with assignment counts against the pre-declared allocation, before reading
effects. The independent unit here is the draw, one eligible user.
""")
code("""
print("base source:", manifest["base"]["source"])
print("case seed:", manifest["seed"])
print("raw-file MD5 provenance:", manifest["raw_md5"])
n_c = int(is_c.sum())
n_t = int(is_t.sum())
srm = srm_check({"control": n_c, "treatment": n_t}, config["design"]["allocation"], level="assignment")
assert n_c == manifest["realised"]["draws_control"] == 57381, "control assigned count"
assert n_t == manifest["realised"]["draws_treatment"] == 57819, "treatment assigned count"
assert not srm.flag_srm, "assignment SRM at alpha 0.001"
print(srm.summary())
print(f"assignment flagged: {srm.flag_srm}")
print(f"elapsed {time.perf_counter() - t0:.1f} s")
""")

md("""
## 2. Invariants on the assigned population

Test subscriber share, device-type shares and pre-period 21-day clicks on all
assigned users. The pre-period click covariate is also the input a Phase 2
CUPED case would use. The threshold is 0.001 per metric.
""")
code("""
device = pd.get_dummies(draws["device_type"], prefix="device").astype(float)
assert len(device.columns) == draws["device_type"].nunique(), "device indicator count"
draws_with_dummies = pd.concat([draws[["arm", "is_subscriber", "pre_period_clicks_21d"]].copy(), device], axis=1)
draws_with_dummies["is_subscriber"] = draws_with_dummies["is_subscriber"].astype(float)
invariants = invariant_check(draws_with_dummies, arm_col="arm",
    metric_cols=["is_subscriber", "pre_period_clicks_21d", *device.columns],
    level="assignment", treatment_arm="treatment", control_arm="control")
assert not invariants.flagged, "no assigned-population invariant flagged at alpha 0.001"
table = invariants.table.copy()
table.index.name = None
assert not set(table.columns) & {"user_id", "article_id", "draw_id"}, "invariant table column names"
print(table.to_string())
print(f"elapsed {time.perf_counter() - t0:.1f} s")
""")

md("""
## 3. Outcome completeness and two clocks

Enrolment and outcomes share the split week. Analysis waits until 36 hours
after the first midnight following the last assignment, so the maximum event
delay has elapsed. A late cohort has a shorter outcome window: low clicks in
that cohort must be read with its enrolment date.
""")
code("""
last_assignment = assignments["assigned_at"].max()
first_assignment = assignments["assigned_at"].min()
week_end = last_assignment.normalize() + pd.Timedelta(days=1)
analysis_time = week_end + pd.Timedelta(hours=config["design"]["arrival_delay_hours"]["max"])
arrival = event_arrival_lag(clicks, assignments, event_time_col="clicked_at",
    received_time_col="received_at", analysis_time=analysis_time, user_col=UNIT, arm_col="arm")
assert arrival.table["share_unavailable_at_analysis"].eq(0.0).all(), "late click share zero in both arms"
assert not arrival.differential, "no differential arrival delay at alpha 0.001"
print(f"week closes: {week_end}; analysis clock: {analysis_time}")
table = arrival.table.copy()
table.index.name = None
assert not set(table.columns) & {"user_id", "article_id", "draw_id"}, "arrival table column names"
print(table.to_string())
print(f"arrival KS p = {arrival.ks_p_value:.6g}; differential: {arrival.differential}")
draws["enrolment_day"] = draws["assigned_at"].dt.date
assert draws["enrolment_day"].nunique() == (last_assignment.date() - first_assignment.date()).days + 1, "enrolment calendar date count"
assert last_assignment - first_assignment < pd.Timedelta(days=7), "enrolment span under seven days"
daily = draws.groupby(["enrolment_day", "arm"]).agg(draws=("clicks", "size"), mean_clicks=("clicks", "mean"))
table = daily.unstack("arm")
table.columns = [f"{metric}_{arm}" for metric, arm in table.columns]
table.insert(0, "date", table.index)
table.index.name = None
assert len(table) == draws["enrolment_day"].nunique(), "one enrolment table row per date"
assert not set(table.columns) & {"user_id", "article_id", "draw_id"}, "enrolment table column names"
print(table.to_string())
print(f"calendar dates: {len(table)}; assignment span: {last_assignment - first_assignment}")
print(f"elapsed {time.perf_counter() - t0:.1f} s")
""")

md("""
## 4. Impressions per eligible user

The denominator is an outcome of the treatment, so inspect it directly. Activity
deciles come from pre-period clicks; ranking with a fixed tie order makes ten
equal groups. Figure 1 shows treatment-minus-control gaps with Welch intervals.
""")
code("""
draws["decile"] = pd.qcut(draws["pre_period_clicks_21d"].rank(method="first"), 10, labels=range(1, 11)).astype(int)
assert draws["decile"].value_counts().eq(11520).all(), "ten deciles of 11,520 eligible users"
inviews = difference_in_means(draws.loc[is_t, "inviews"].to_numpy(), draws.loc[is_c, "inviews"].to_numpy(),
    estimand="in-views per eligible user", analysis_unit="draw")
assert inviews.relative_confidence_interval[1] < 0, "in-view relative interval entirely below zero"
print(f"in-views absolute difference {inviews.absolute_difference:+.6f}; Welch interval {inviews.confidence_interval}")
show("inviews_per_user", inviews)
decile_inviews = []
for decile in range(1, 11):
    subset = draws[draws["decile"].eq(decile)]
    r = difference_in_means(subset.loc[subset["arm"].eq("treatment"), "inviews"].to_numpy(),
        subset.loc[subset["arm"].eq("control"), "inviews"].to_numpy(),
        estimand="in-views per eligible user in activity decile", analysis_unit="draw")
    decile_inviews.append({"decile": decile, "control": r.mean_control, "treatment": r.mean_treatment,
        "gap": r.absolute_difference, "lower": r.confidence_interval[0], "upper": r.confidence_interval[1]})
table = pd.DataFrame(decile_inviews)
table.index.name = None
assert not set(table.columns) & {"user_id", "article_id", "draw_id"}, "in-view decile table column names"
print(table.to_string())
plt.figure(figsize=(8, 4))
plt.errorbar(table["decile"], table["gap"], yerr=np.array([table["gap"] - table["lower"], table["upper"] - table["gap"]]),
    fmt="o", color="#28678a", capsize=3)
plt.axhline(0, color="0.5", linewidth=1)
plt.xlabel("Pre-period activity decile")
plt.ylabel("In-views per eligible user: treatment minus control")
plt.title("The denominator falls across activity deciles")
plt.xticks(range(1, 11))
plt.savefig(FIG / "fig1_inviews_by_decile.png", dpi=120, bbox_inches="tight")
plt.close()
assert (FIG / "fig1_inviews_by_decile.png").stat().st_size > 0, "figure 1 non-empty"
print(f"elapsed {time.perf_counter() - t0:.1f} s")
""")

md("""
## 5. What the dashboard reads

The dashboard reads E2, the ratio of arm sums, and treats 19.1 million
impressions as independent trials. A user's impressions share that user's
propensities: inference must therefore respect the randomised draw, one
eligible user (Deng, Knoblich and Lu, 2018). Delta, jackknife and user bootstrap
intervals are expected to agree and to be about 1.5 times as wide as the
dashboard interval. The next cell measures that comparison on this draw.
""")

code("""
clicks_t = int(draws.loc[is_t, "clicks"].sum())
clicks_c = int(draws.loc[is_c, "clicks"].sum())
inviews_t = int(draws.loc[is_t, "inviews"].sum())
inviews_c = int(draws.loc[is_c, "inviews"].sum())
naive = naive_event_rate(clicks_t, inviews_t, clicks_c, inviews_c)
delta = ratio_of_sums(draws, "clicks", "inviews", unit_col=UNIT, arm_col="arm",
    estimand="platform click-through rate, ratio of expected totals", method="delta")
jack = ratio_of_sums(draws, "clicks", "inviews", unit_col=UNIT, arm_col="arm",
    estimand="platform click-through rate, ratio of expected totals", method="jackknife")
boot = ratio_of_sums(draws, "clicks", "inviews", unit_col=UNIT, arm_col="arm",
    estimand="platform click-through rate, ratio of expected totals", method="bootstrap",
    resamples=1000, seed=config["seed"], interval="percentile")
same_lifts = [delta.relative_lift, jack.relative_lift, boot.relative_lift]
assert max(same_lifts) - min(same_lifts) < 1e-12, "three E2 point estimates identical"
assert abs(naive.relative_lift - delta.relative_lift) < 1e-9, "naive E2 point estimate equals ratio of sums"
assert abs(jack.standard_error / delta.standard_error - 1) < 0.01, "jackknife to delta SE agreement"
assert abs(boot.standard_error / delta.standard_error - 1) < 0.10, "bootstrap to delta SE agreement"
assert naive.warnings, "naive estimate carries a warning"
assert naive.p_value < 0.05 and delta.p_value < 0.05, "naive and delta E2 reject at 0.05"
delta_width = delta.relative_confidence_interval[1] - delta.relative_confidence_interval[0]
naive_width = naive.relative_confidence_interval[1] - naive.relative_confidence_interval[0]
width_ratio = delta_width / naive_width
assert 1.3 <= width_ratio <= 1.7, "delta to naive relative interval width ratio inside [1.3, 1.7]"
print(f"delta / naive relative interval width: {width_ratio:.2f}")
print(f"ABF_WIDTH_RATIO {float(width_ratio)!r}")
e2_rows = []
for label, r in (("e2_naive", naive), ("e2_delta", delta), ("e2_jackknife", jack), ("e2_bootstrap", boot)):
    show(label, r)
    lower, upper = r.relative_confidence_interval
    e2_rows.append({"estimator": label, "relative_lift": r.relative_lift, "lower": lower, "upper": upper,
        "width": upper - lower, "width_ratio_to_delta": (upper - lower) / delta_width})
level("e2_delta", delta.ratio_control, delta.se_ratio_control)
table = pd.DataFrame(e2_rows)
table.index.name = None
assert not set(table.columns) & {"user_id", "article_id", "draw_id"}, "E2 comparison table column names"
print(table.to_string())
for warning in naive.warnings:
    print(warning)
print(f"elapsed {time.perf_counter() - t0:.1f} s")
""")

md("""
## 6. One estimate, four intervals

Figure 2 compares relative-lift intervals on one axis. The three user-level
intervals agree; the dashboard interval is narrower by the measured ratio.
The decision does not depend on which of the three user-level intervals is read.
""")
code("""
assert all(r.relative_confidence_interval[0] <= r.relative_lift <= r.relative_confidence_interval[1]
    for r in (delta, jack, boot)), "three user-level E2 intervals contain their estimate"
plt.figure(figsize=(8, 4))
for y, (label, r) in enumerate((("Dashboard: impression-level", naive), ("User delta", delta),
        ("User jackknife", jack), ("User bootstrap", boot))):
    lo, hi = r.relative_confidence_interval
    plt.errorbar(r.relative_lift, y, xerr=np.array([[r.relative_lift - lo], [hi - r.relative_lift]]),
        fmt="o", color="#b14d42" if y == 0 else "#28678a", capsize=4)
plt.yticks(range(4), ["Dashboard: impression-level", "User delta", "User jackknife", "User bootstrap"])
plt.axvline(0, color="0.5", linewidth=1)
plt.xlabel("Relative lift in platform click-through rate")
plt.title(f"One E2 estimate; delta interval {width_ratio:.2f} times wider than dashboard")
plt.savefig(FIG / "fig2_one_estimate_four_intervals.png", dpi=120, bbox_inches="tight")
plt.close()
assert (FIG / "fig2_one_estimate_four_intervals.png").stat().st_size > 0, "figure 2 non-empty"
print(f"elapsed {time.perf_counter() - t0:.1f} s")
""")

md("""
## 7. Three estimands against the engagement rule

E1 counts clicks per eligible user, E2 weights impressions, and E3 weights
users' rates equally. Only E1 is read against the product lead's +0.5% floor.
An E1 interval including zero and failing to clear that floor leads to row 5;
an upper bound above the floor does not establish a gain.
""")
code("""
e1 = difference_in_means(draws.loc[is_t, "clicks"].to_numpy(), draws.loc[is_c, "clicks"].to_numpy(),
    estimand="clicks per eligible user", analysis_unit="draw")
e3 = difference_in_means(draws.loc[is_t, "user_rate"].to_numpy(), draws.loc[is_c, "user_rate"].to_numpy(),
    estimand="mean user-level click rate among users with at least one in-view", analysis_unit="draw")
floor = config["decision_rule"]["ship_if_lower_bound_above_relative"]
assert delta.relative_confidence_interval[0] > 0, "E2 lower bound above zero"
assert e3.relative_confidence_interval[0] > 0, "E3 lower bound above zero"
assert e1.relative_confidence_interval[1] >= 0, "E1 upper bound at or above zero"
assert e1.relative_confidence_interval[0] <= floor, "E1 lower bound at or below engagement floor"
show("e1", e1)
level("e1", e1.mean_control, e1.sd_control / math.sqrt(e1.n_control))
show("e3", e3)
level("e3", e3.mean_control, e3.sd_control / math.sqrt(e3.n_control))
table = pd.DataFrame([
    {"estimand": label, "control_level": control, "relative_lift": r.relative_lift,
     "lower": r.relative_confidence_interval[0], "upper": r.relative_confidence_interval[1]}
    for label, control, r in (("E1: clicks per user", e1.mean_control, e1),
        ("E2: ratio of totals", delta.ratio_control, delta), ("E3: mean user rate", e3.mean_control, e3))])
table.index.name = None
assert not set(table.columns) & {"user_id", "article_id", "draw_id"}, "three-estimand table column names"
print(table.to_string())
print(f"engagement floor: {floor:+.3%}")
plt.figure(figsize=(8, 4))
for y, r in enumerate((e1, delta, e3)):
    lo, hi = r.relative_confidence_interval
    plt.errorbar(r.relative_lift, y, xerr=np.array([[r.relative_lift - lo], [hi - r.relative_lift]]),
        fmt="o", color="#28678a", capsize=4)
plt.axvline(0, color="0.5", linewidth=1)
plt.plot([floor, floor], [-0.3, 0.3], color="#b14d42", label="E1 engagement floor")
plt.yticks(range(3), ["E1: clicks per user", "E2: ratio of totals", "E3: mean user rate"])
plt.xlabel("Relative lift")
plt.title("The rates rise; the engagement floor is not cleared")
plt.legend(loc="upper left")
plt.savefig(FIG / "fig3_three_estimands.png", dpi=120, bbox_inches="tight")
plt.close()
assert (FIG / "fig3_three_estimands.png").stat().st_size > 0, "figure 3 non-empty"
print(f"elapsed {time.perf_counter() - t0:.1f} s")
""")

md("""
## 8. Negative feedback: +5% guardrail and +3% re-read

Negative feedback is simulated in both arms: 0.5% per in-view outside the old
tier, a Gamma(2, mean 1) multiplier per population user, and three times the
rate on old-tier in-views. A real signal is a Phase 2 item. The verdict
function below is defined here, outside the package, using the pre-declared
bound convention. A narrower margin needs more precision: at no effect the
harness passes +5% with probability 0.992 and +3% with 0.7525 (the operating-characteristics section
reports MC SEs). An inconclusive verdict must not be read as a pass (Schultzberg,
Ankargren and Frånberg, 2024).
""")
code('''
def guardrail_verdict(lower, upper, margin):
    """Defined here, outside the package: the pre-declared verdict rule."""
    if upper <= margin:
        return "pass"
    if lower > margin:
        return "breach"
    return "inconclusive"


assert guardrail_verdict(-0.02, 0.01, 0.05) == "pass"
assert guardrail_verdict(0.06, 0.09, 0.05) == "breach"
assert guardrail_verdict(0.01, 0.07, 0.05) == "inconclusive"
guardrail = difference_in_means(draws.loc[is_t, "feedback"].to_numpy(), draws.loc[is_c, "feedback"].to_numpy(),
    estimand="negative-feedback actions per eligible user", analysis_unit="draw")
show("guardrail", guardrail)
guard_margin = config["decision_rule"]["guardrails"]["negative_feedback_max_increase_relative"]
reread_margin = config["decision_rule"]["sensitivity_reread_margin_relative"]
guard_lower, guard_upper = guardrail.relative_confidence_interval
guard_verdict = guardrail_verdict(guard_lower, guard_upper, guard_margin)
reread_verdict = guardrail_verdict(guard_lower, guard_upper, reread_margin)
assert guard_verdict == "pass", "guardrail pass at +5% margin"
assert reread_verdict == "pass", "guardrail pass at +3% margin"
assert guard_upper < 0.03, "guardrail upper bound below +3%"
print(f"negative-feedback verdict at {guard_margin:+.0%}: {guard_verdict}")
print(f"negative-feedback re-read at {reread_margin:+.0%}: {reread_verdict}")
print("feedback simulation:", config["guardrail_simulation"])
print(f"elapsed {time.perf_counter() - t0:.1f} s")
''')

md("""
## 9. Clicks per user by activity decile

The heaviest activity deciles carry more old-tier in-views: the mechanism
check measured shares 0.167 in the lightest decile and 0.385 in the heaviest.
Read where clicks were gained and lost from the decile gaps and Welch
intervals. Count-weighted decile means must recover the overall E1 means.
""")
code("""
decile_clicks = []
for decile in range(1, 11):
    subset = draws[draws["decile"].eq(decile)]
    t_values = subset.loc[subset["arm"].eq("treatment"), "clicks"].to_numpy()
    c_values = subset.loc[subset["arm"].eq("control"), "clicks"].to_numpy()
    r = difference_in_means(t_values, c_values, estimand="clicks per eligible user in activity decile", analysis_unit="draw")
    decile_clicks.append({"decile": decile, "control": r.mean_control, "treatment": r.mean_treatment,
        "n_control": len(c_values), "n_treatment": len(t_values), "gap": r.absolute_difference,
        "lower": r.confidence_interval[0], "upper": r.confidence_interval[1]})
table = pd.DataFrame(decile_clicks)
assert len(table) == 10, "ten click-decile table rows"
recovered_c = float((table["control"] * table["n_control"]).sum() / table["n_control"].sum())
recovered_t = float((table["treatment"] * table["n_treatment"]).sum() / table["n_treatment"].sum())
assert abs(recovered_c - e1.mean_control) < 1e-9, "weighted control decile mean recovers E1"
assert abs(recovered_t - e1.mean_treatment) < 1e-9, "weighted treatment decile mean recovers E1"
assert abs((recovered_t - recovered_c) - e1.absolute_difference) < 1e-9, "weighted decile difference recovers E1"
table.index.name = None
assert not set(table.columns) & {"user_id", "article_id", "draw_id"}, "click-decile table column names"
print(table.to_string())
print(f"elapsed {time.perf_counter() - t0:.1f} s")
""")

md("""
## 10. Operating characteristics

This draw is read alongside the committed harness results. Scenario A measures
null rejection and guardrail passes; scenario C measures the full ship rule
at 57,600 per arm against the target 0.80 +/- 0.025. Bootstrap rejection uses
500 replicates. The harness's scenario D story rate is distinct from the
shipped-generator seed search disclosed in the data disclosure above. Monte Carlo SE quantifies
the uncertainty of each rate, not the uncertainty of this experiment's lift.
""")
code("""
oc_dir = ROOT / "results" / "case02"
oc_rule = pd.read_csv(oc_dir / "oc_rule.csv")
oc_scenarios = pd.read_csv(oc_dir / "oc_scenarios.csv")
oc_meta = json.loads((oc_dir / "oc_meta.json").read_text(encoding="utf-8"))
assert oc_meta["replicates"] == 2000, "OC replicate count"
oc_rows = []
for estimator, expected_rate, expected_se in (
    ("naive_e2", 0.165, 0.0083), ("delta_e2", 0.05, 0.0049),
    ("jackknife_e2", 0.05, 0.0049), ("bootstrap_e2", 0.046, 0.0094),
    ("welch_e1", 0.0595, 0.0053), ("welch_e3", 0.0495, 0.0049),
    ("welch_guardrail", 0.0595, 0.0053)):
    row = oc_scenarios.loc[oc_scenarios["scenario"].eq("A") & oc_scenarios["estimator"].eq(estimator)].iloc[0]
    assert math.isclose(float(row["rejection"]), expected_rate, rel_tol=1e-9), f"scenario A {estimator} rejection rate"
    assert math.isclose(float(row["rejection_mc_se"]), expected_se, abs_tol=5e-5), f"scenario A {estimator} rejection MC SE"
    if estimator == "bootstrap_e2":
        assert int(row["B"]) == 500, "bootstrap OC replicate count"
    oc_rows.append({"quantity": f"A {estimator} rejection", "rate": row["rejection"], "MC_SE": row["rejection_mc_se"], "B": int(row["B"])})
a_rule = oc_rule.loc[oc_rule["scenario"].eq("A")].iloc[0]
for label, expected_rate, expected_se in (("guardrail_pass_5", 0.992, 0.0020), ("guardrail_pass_3", 0.7525, 0.0096)):
    assert math.isclose(float(a_rule[label]), expected_rate, rel_tol=1e-9), f"scenario A {label} rate"
    assert math.isclose(float(a_rule[label + "_mc_se"]), expected_se, abs_tol=5e-5), f"scenario A {label} MC SE"
    oc_rows.append({"quantity": f"A {label}", "rate": a_rule[label], "MC_SE": a_rule[label + "_mc_se"], "B": int(a_rule["B"])})
c_rule = oc_rule.loc[oc_rule["scenario"].eq("C")].iloc[0]
assert math.isclose(float(c_rule["row4"]), 0.814, rel_tol=1e-9), "scenario C row 4 ship rate"
assert math.isclose(float(c_rule["row4_mc_se"]), 0.0087, abs_tol=5e-5), "scenario C row 4 MC SE"
oc_rows.append({"quantity": "C row4", "rate": c_rule["row4"], "MC_SE": c_rule["row4_mc_se"], "B": int(c_rule["B"])})
assert math.isclose(oc_meta["story_rate_D"]["rate"], 0.961, rel_tol=1e-9), "scenario D story rate"
assert math.isclose(oc_meta["story_rate_D"]["mc_se"], 0.0043, abs_tol=5e-5), "scenario D story MC SE"
oc_rows.append({"quantity": "D story", "rate": oc_meta["story_rate_D"]["rate"], "MC_SE": oc_meta["story_rate_D"]["mc_se"], "B": oc_meta["replicates"]})
table = pd.DataFrame(oc_rows)
table.index.name = None
assert not set(table.columns) & {"user_id", "article_id", "draw_id"}, "OC table column names"
print(table.to_string())
print("scenario C joint ship-rate target: 0.80 +/- 0.025")
print(f"OC replicates: {oc_meta['replicates']}; OC seed: {oc_meta['oc_seed']}; wall-clock seconds: {oc_meta['wall_clock_seconds']}")
print(f"elapsed {time.perf_counter() - t0:.1f} s")
""")

md("""
## 11. Decision memo

Apply the pre-declared rows in order. The post-treatment denominator blocks
reading E2 and E3 as engagement estimates; E1 is available, so it does not
block the experiment. The decision below concerns an engagement gain. An
efficiency proposal would need its own decision rule.
""")
code("""
blockers = {
    "Assignment issue": bool(srm.flag_srm or invariants.flagged),
    "Exposure measurement issue": False,
    "Outcome logging issue": bool(arrival.table["share_unavailable_at_analysis"].gt(0).any()),
    "Differential missingness": bool(arrival.differential),
    "Guardrail breach or unmeasurable": guard_verdict != "pass",
    "Duration insufficient for delayed outcomes": bool(analysis_time < week_end + pd.Timedelta(hours=config["design"]["arrival_delay_hours"]["max"]))}
row_conditions = [guard_verdict == "breach", any(blockers.values()) or guard_verdict == "inconclusive",
    e1.relative_confidence_interval[1] < 0,
    e1.relative_confidence_interval[0] > floor and guard_verdict == "pass", True]
actions = ["Do not ship", "No decision; investigate first", "Do not ship: engagement loss", "Ship",
    "Do not ship: the engagement gain is not shown to exceed the +0.5% floor"]
decision_row = next(i for i, condition in enumerate(row_conditions, 1) if condition)
action = actions[decision_row - 1]
assert decision_row == 5, "decision row 5 fires"
assert not any(row_conditions[:4]), "decision rows 1 to 4 are false"
assert action == "Do not ship: the engagement gain is not shown to exceed the +0.5% floor", "decision action matches rule row 5"
print(f"Decision row {decision_row}: {action}")
for label, blocked in blockers.items():
    print(f"{label}: {'yes' if blocked else 'no'}")
print("Metric denominator is a post-treatment quantity: yes, for E2 and E3; E1 is available")
print("Recommendation: do not ship as an engagement gain; reframe the change as an efficiency proposal.")
print(f"elapsed {time.perf_counter() - t0:.1f} s")
""")

code("""
e1_lower = e1.relative_confidence_interval[0]
assert inviews.relative_confidence_interval[1] < 0, "impressions per user fell"
assert delta.relative_confidence_interval[0] > 0, "the click-through rate rose"
assert decision_row == 5, "the decision reads E1, so the E2 interval does not change it"
clicks_clause = "were not shown to rise" if e1_lower <= 0 else "rose"
TWO_MINUTE = ("Users were randomised and impressions were counted. "
              f"The user-level interval was about {width_ratio:.1f} times as wide as the dashboard interval, "
              f"and the correction did not change the decision: clicks per user {clicks_clause}, "
              "while the rate rose as impressions per user fell.")
EXPECTED_TWO_MINUTE = "Users were randomised and impressions were counted. The user-level interval was about 1.5 times as wide as the dashboard interval, and the correction did not change the decision: clicks per user were not shown to rise, while the rate rose as impressions per user fell."
assert TWO_MINUTE == EXPECTED_TWO_MINUTE, "two-minute version, computed from e1 and width_ratio, against the README's two-minute sentence"
print(TWO_MINUTE)
print(f"On this draw the user-level interval was {width_ratio:.2f} times as wide as the dashboard's interval.")
print(f"elapsed {time.perf_counter() - t0:.1f} s")
""")

BANNED = re.compile(r"\b(correct|incorrect|right|wrong)\b", re.IGNORECASE)
for i, cell in enumerate(cells, 1):
    hit = BANNED.search(cell["source"])
    if hit:
        raise SystemExit(f"banned word {hit.group(0)!r} in nbformat cell {i}")

nb = nbf.v4.new_notebook(cells=cells)
nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
nb.metadata["language_info"] = {"name": "python"}
parser = argparse.ArgumentParser(description="Build the Case 02 notebook from its sections.")
parser.add_argument("--out", type=Path, default=OUT, help="path of the notebook to write")
OUT = parser.parse_args().out
OUT.write_text(nbf.writes(nb), encoding="utf-8")
print(f"wrote {OUT} with {len(cells)} cells")
