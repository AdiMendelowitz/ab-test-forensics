"""Calibration and validation of the Case 02 parametric generator.

The procedures return aggregates and never replace the stored generator parameters.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
from scipy.optimize import least_squares

from abforensics.simulation import ranking_incident as ri

TARGETS = ("E2", "E3", "old_rate", "se_ratio")
PARAMETERS = ("a", "b", "sigma", "gamma")
STARTS = ((-2.3, -0.1, 0.3, -0.9), (-2.0, -0.3, 0.6, -0.6))


def population_seeds(
    settings: Mapping[str, Any], phase: str, *, populations: int | None = None
) -> list[int]:
    """Calibration and validation use separate ranges of population seeds."""
    if phase not in ("calibration", "validation"):
        raise ValueError("phase must be calibration or validation")
    count = int(settings[f"{phase}_populations"] if populations is None else populations)
    if count < 2:
        raise ValueError("at least two populations are required")
    key = "calibration_seed" if phase == "calibration" else "validation_population_seed"
    first = int(settings[key])
    return list(range(first, first + count))


def calibrate_parametric(
    settings: Mapping[str, Any],
    *,
    starts: Sequence[Sequence[float]] = STARTS,
    populations: int | None = None,
    splits: int | None = None,
    observer: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """Fixed-draw least squares, checked against the frozen stored theta."""
    seeds = population_seeds(settings, "calibration", populations=populations)
    splits = int(settings["calibration_splits"] if splits is None else splits)
    draws = [ri.prepare_parametric_draws(settings, seed) for seed in seeds]
    target = np.array([settings["targets"][key] for key in TARGETS])
    tolerance = np.array([settings["tolerances"][key] for key in TARGETS])
    stored = np.array([settings["theta"][key] for key in PARAMETERS])

    def objective(theta):
        values = []
        for seed, draw in zip(seeds, draws, strict=True):
            counts = ri.parametric_counts(settings, prepared=draw, theta=theta)
            result = ri.parametric_statistics(counts, splits=splits, seed=seed)
            values.append([result[key] for key in TARGETS])
        residual = (np.mean(values, axis=0) - target) / tolerance
        if observer is not None:
            observer({"theta": list(theta), "residual": list(residual)})
        return residual

    fits = []
    for start in starts:
        fit = least_squares(
            objective,
            start,
            bounds=([-np.inf, -np.inf, 0, -np.inf], [np.inf] * 4),
            diff_step=0.02,
        )
        difference = float(np.max(np.abs(fit.x - stored)))
        fits.append(
            {
                "start": list(start),
                "fit": fit.x.tolist(),
                "cost": float(fit.cost),
                "success": bool(fit.success),
                "message": str(fit.message),
                "max_parameter_difference": difference,
                "pass": bool(fit.success and difference <= 0.01),
            }
        )
    return {"fits": fits, "pass": all(fit["pass"] for fit in fits)}


def validate_parametric(
    settings: Mapping[str, Any],
    *,
    populations: int | None = None,
    splits: int | None = None,
    observer: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """Independent populations, uncertainty gates and disclosed diagnostics."""
    seeds = population_seeds(settings, "validation", populations=populations)
    splits = int(settings["validation_splits"] if splits is None else splits)
    rows = []
    for i, seed in enumerate(seeds):
        draws = ri.prepare_parametric_draws(settings, seed)
        counts = ri.parametric_counts(settings, prepared=draws)
        values = ri.parametric_statistics(counts, splits=splits, seed=seed)
        off = ri.parametric_counts(settings, seed=seed, depth_off=True)
        values["depth_off"] = ri.parametric_statistics(off, splits=splits, seed=seed)["se_ratio"]
        negative = ri.parametric_counts(
            settings, prepared=draws, sigma=settings["theta"]["sigma"] + 0.02
        )
        values["negative"] = ri.parametric_statistics(negative, splits=splits, seed=seed)[
            "se_ratio"
        ]
        zero = ri.parametric_counts(settings, prepared=draws, sigma=0)
        values["sigma_zero"] = ri.parametric_statistics(zero, splits=splits, seed=seed)["se_ratio"]
        for q in (5, 25, 50, 90, 99):
            values[f"p{q}"] = float(np.percentile(counts.inviews, q))
        values["click_cv"] = float(np.std(counts.clicks, ddof=1) / np.mean(counts.clicks))
        values["decile_rates"] = [
            float(
                counts.clicks[counts.decile == k].sum() / counts.inviews[counts.decile == k].sum()
            )
            for k in range(10)
        ]
        values["decile_shares"] = [
            float(
                counts.old_inviews[counts.decile == k].sum()
                / counts.inviews[counts.decile == k].sum()
            )
            for k in range(10)
        ]
        values["top_old_share"] = float(
            counts.old_inviews[counts.decile == 9].sum() / counts.old_inviews.sum()
        )
        rows.append(values)
        if observer is not None:
            observer({"population": i, "values": values})
    gates = {
        key: ri.parametric_gate(
            [row[key] for row in rows], settings["targets"][key], settings["tolerances"][key]
        )
        for key in settings["targets"]
    }
    gates["old_share"] = ri.parametric_gate([row["old_share"] for row in rows], 1 / 3, 0.005)
    gates["depth_off"] = ri.parametric_gate([row["depth_off"] for row in rows], 1, 0.03)
    for q, target in ((50, 98), (90, 420), (99, 950)):
        gates[f"p{q}"] = ri.parametric_gate([row[f"p{q}"] for row in rows], target, target * 0.05)
    negative = ri.parametric_gate([row["negative"] for row in rows], 1.48, 0.03)
    diagnostics = {
        key: np.median([row[key] for row in rows], axis=0).tolist()
        for key in (
            "click_cv",
            "decile_rates",
            "decile_shares",
            "top_old_share",
            "p5",
            "p25",
            "sigma_zero",
        )
    }
    summary = {
        "gates": gates,
        "negative_control": negative,
        "diagnostics": diagnostics,
        "pass": all(gate["pass"] for gate in gates.values()) and not negative["pass"],
    }
    return {
        "populations": len(seeds),
        "splits": splits,
        "population_values": rows,
        "summary": summary,
    }
