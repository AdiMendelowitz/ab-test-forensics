"""abforensics: reproducible case studies in how online experiments produce the wrong decision.

Public API is deliberately small. Each function performs one statistical or diagnostic
task, never infers the experimental unit, never chooses a variance estimator silently,
never drops missing rows silently, never returns only a p-value, and never emits a
ship / no-ship decision.
"""

from abforensics.diagnostics.invariants import exposure_rate_by_segment, invariant_check
from abforensics.diagnostics.missingness import event_arrival_lag
from abforensics.diagnostics.srm import srm_check
from abforensics.inference.proportions import difference_in_proportions
from abforensics.results import (
    ArrivalLagResult,
    InvariantResult,
    ProportionDifferenceResult,
    SegmentExposureResult,
    SRMResult,
)

__all__ = [
    "ArrivalLagResult",
    "InvariantResult",
    "ProportionDifferenceResult",
    "SRMResult",
    "SegmentExposureResult",
    "difference_in_proportions",
    "event_arrival_lag",
    "exposure_rate_by_segment",
    "invariant_check",
    "srm_check",
]
__version__ = "0.1.0"
