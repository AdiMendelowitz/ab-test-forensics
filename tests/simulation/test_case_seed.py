"""The case seed was chosen by a stated rule; this test re-applies the rule.

Rule (config.yaml): the first seed from 20260928 upwards whose single run shows the case's
story. The day-14 dashboard (exposed users, orders received by day 14) clears the ship rule;
the complete-data intent-to-treat interval lands in the continue zone (it reaches below the
+1.0% margin and above zero); the assignment-level SRM check passes.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from abforensics import difference_in_proportions, srm_check
from abforensics.simulation.checkout_incident import simulate_checkout_experiment

CONFIG = Path(__file__).resolve().parents[2] / "case_studies/01_lift_we_could_not_trust/config.yaml"
FIRST_CANDIDATE = 20260928


def shows_the_case_story(config: dict, seed: int) -> bool:
    data = simulate_checkout_experiment(config, seed=seed)
    a, e = data.assignments, data.exposures
    t0 = a["assigned_at"].min().floor("D")
    window = pd.Timedelta(days=config["outcome_window_days"])
    delay = pd.Timedelta(hours=config["order_pipeline"]["arrival_delay_hours"]["max"])
    dashboard_time = t0 + pd.Timedelta(days=config["days"])
    analysis_time = dashboard_time + window + delay
    margin = config["decision_rule"]["ship_if_lower_bound_above_relative"]
    o = data.orders.merge(a[["user_id", "assigned_at"]], on="user_id")
    o = o[o["ordered_at"] < o["assigned_at"] + window]

    def relative_interval(pop: pd.DataFrame, clock: pd.Timestamp) -> tuple[float, float]:
        converters = set(o.loc[o["received_at"] <= clock, "user_id"])
        counts = {
            arm: (int(g["user_id"].isin(converters).sum()), len(g)) for arm, g in pop.groupby("arm")
        }
        res = difference_in_proportions(
            *counts["treatment"],
            *counts["control"],
            estimand="x",
            analysis_unit="user",
            population="x",
        )
        return res.relative_confidence_interval

    dash_low, _ = relative_interval(a[a["user_id"].isin(e["user_id"])], dashboard_time)
    itt_low, itt_high = relative_interval(a, analysis_time)
    srm = srm_check(a["arm"].value_counts().to_dict(), config["allocation"], level="assignment")
    return dash_low > margin and itt_low <= margin and itt_high >= 0 and not srm.flag_srm


@pytest.mark.slow
def test_case_seed_is_the_first_that_meets_the_stated_rule() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    chosen = int(config["seed"])
    assert chosen >= FIRST_CANDIDATE
    for seed in range(FIRST_CANDIDATE, chosen):
        assert not shows_the_case_story(config, seed), seed
    assert shows_the_case_story(config, chosen)
