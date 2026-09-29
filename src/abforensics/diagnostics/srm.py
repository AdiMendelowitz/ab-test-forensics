"""Sample ratio mismatch (SRM) diagnostic.

An SRM check asks one question: are the observed unit counts per arm compatible with
the allocation the experiment was configured with? It is a chi-square goodness-of-fit
test on counts. It is run at a stated *level* of the unit hierarchy (assignment,
exposure, analysis) because a failure at one level and a pass at another is itself the
finding: see case study 01.

References:
    Fabijan et al. (2019). Diagnosing Sample Ratio Mismatch in Online Controlled
    Experiments: A Taxonomy and Rules of Thumb for Practitioners. KDD.
    Kohavi, Tang, Xu (2020). Trustworthy Online Controlled Experiments, chapter 21.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from scipy import stats

from abforensics.results import SRMResult

_ALLOCATION_TOLERANCE = 1e-9
_MIN_EXPECTED_COUNT = 5.0


def srm_check(
    observed_counts: Mapping[str, int],
    expected_allocation: Mapping[str, float],
    *,
    level: str,
    alpha: float = 0.001,
) -> SRMResult:
    """Chi-square goodness-of-fit test of observed arm counts against planned allocation.

    Parameters
    ----------
    observed_counts
        Number of units observed per arm at the stated level, e.g.
        ``{"control": 50123, "treatment": 48911}``. Every arm in ``expected_allocation``
        must be present; extra arms are an error, not silently dropped.
    expected_allocation
        Planned share of units per arm, e.g. ``{"control": 0.5, "treatment": 0.5}``.
        Must sum to one. There is deliberately no default: an SRM check against an
        assumed 50/50 split is a common way to miss a misconfigured experiment.
    level
        Which unit counts these are: ``"assignment"``, ``"exposure"``, ``"analysis"``
        or any label meaningful to the case. Recorded on the result so that a reader
        can see at which level the check was run.
    alpha
        Significance threshold for ``flag_srm``. The default of 0.001 follows the
        practice of large experimentation platforms, where SRM alerts fire on many
        experiments per day and a 0.05 threshold would flag 1 in 20 healthy ones.

    Returns
    -------
    SRMResult
        Statistic, p-value, expected counts and a flag. The result carries a warning
        when any expected count is below five, where the chi-square approximation is
        unreliable.

    Raises
    ------
    ValueError
        On negative counts, allocations not summing to one, arms missing from either
        mapping, an empty total, or an alpha outside (0, 1).
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1); got {alpha}")
    if not observed_counts:
        raise ValueError("observed_counts is empty")

    obs_arms = set(observed_counts)
    alloc_arms = set(expected_allocation)
    if obs_arms != alloc_arms:
        raise ValueError(
            "arms in observed_counts and expected_allocation differ: "
            f"observed={sorted(obs_arms)}, allocation={sorted(alloc_arms)}"
        )

    arms = list(expected_allocation)  # preserve the caller's order
    shares = np.asarray([float(expected_allocation[a]) for a in arms])
    counts = np.asarray([observed_counts[a] for a in arms], dtype=float)

    if np.any(shares <= 0):
        raise ValueError("every expected allocation share must be positive")
    if abs(shares.sum() - 1.0) > _ALLOCATION_TOLERANCE:
        raise ValueError(f"expected_allocation must sum to 1; got {shares.sum():.12f}")
    if np.any(counts < 0):
        raise ValueError("observed counts must be non-negative")
    if np.any(counts != np.floor(counts)):
        raise ValueError("observed counts must be integers")

    total = counts.sum()
    if total == 0:
        raise ValueError("total observed count is zero; nothing to test")

    expected = shares * total
    warnings: list[str] = [
        "SRM is evidence that observed counts and planned allocation disagree; "
        "it is not proof of bias and carries no effect-size interpretation.",
    ]
    if np.any(expected < _MIN_EXPECTED_COUNT):
        warnings.append(
            f"at least one expected count is below {_MIN_EXPECTED_COUNT:.0f}; the chi-square "
            "approximation is unreliable at this sample size."
        )

    chi2_stat, p_value = stats.chisquare(f_obs=counts, f_exp=expected)
    dof = len(arms) - 1

    return SRMResult(
        observed_counts={a: int(observed_counts[a]) for a in arms},
        expected_counts={a: float(e) for a, e in zip(arms, expected, strict=True)},
        expected_allocation={a: float(expected_allocation[a]) for a in arms},
        chi2_stat=float(chi2_stat),
        p_value=float(p_value),
        degrees_of_freedom=dof,
        alpha=alpha,
        flag_srm=bool(p_value < alpha),
        level=level,
        warnings=tuple(warnings),
    )
