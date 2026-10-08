"""abforensics: reproducible case studies in how online experiments produce the wrong decision.

Public API is deliberately small. Each function performs one statistical or diagnostic
task, never infers the experimental unit, never chooses a variance estimator silently,
never drops missing rows silently, never returns only a p-value, and never emits a
ship / no-ship decision.
"""

from abforensics.diagnostics.invariants import exposure_rate_by_segment, invariant_check
from abforensics.diagnostics.missingness import event_arrival_lag
from abforensics.diagnostics.srm import srm_check
from abforensics.inference.means import difference_in_means
from abforensics.inference.proportions import difference_in_proportions
from abforensics.inference.resampling import cluster_bootstrap, jackknife_variance
from abforensics.metrics.ratio import naive_event_rate, ratio_of_sums
from abforensics.results import (
    ArrivalLagResult,
    BootstrapResult,
    InvariantResult,
    JackknifeResult,
    MeanDifferenceResult,
    ProportionDifferenceResult,
    RatioOfSumsResult,
    SegmentExposureResult,
    SRMResult,
)

__all__ = [
    "ArrivalLagResult",
    "BootstrapResult",
    "InvariantResult",
    "JackknifeResult",
    "MeanDifferenceResult",
    "ProportionDifferenceResult",
    "RatioOfSumsResult",
    "SRMResult",
    "SegmentExposureResult",
    "cluster_bootstrap",
    "difference_in_means",
    "difference_in_proportions",
    "event_arrival_lag",
    "exposure_rate_by_segment",
    "invariant_check",
    "jackknife_variance",
    "naive_event_rate",
    "ratio_of_sums",
    "srm_check",
]
__version__ = "0.1.0"
