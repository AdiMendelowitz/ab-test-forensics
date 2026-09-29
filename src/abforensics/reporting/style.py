"""Palette and matplotlib defaults.

Categorical hues are assigned in fixed order and never cycled: control is always slot 1
(blue), treatment always slot 2 (orange). Sequential encodings use one hue, light to dark.
Text and axes use ink tokens, never a series colour.
"""

from __future__ import annotations

import matplotlib as mpl
from matplotlib.colors import LinearSegmentedColormap

PALETTE = {
    "control": "#2a78d6",
    "treatment": "#eb6834",
    "series3": "#1baf7a",
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "muted": "#8a8985",
    "grid": "#e6e5e1",
    "surface": "#fcfcfb",
    "threshold": "#52514e",
}

SEQUENTIAL_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
BLUE_CMAP = LinearSegmentedColormap.from_list("abf_blue", SEQUENTIAL_BLUE)


def apply_style() -> None:
    mpl.rcParams.update(
        {
            "figure.facecolor": PALETTE["surface"],
            "axes.facecolor": PALETTE["surface"],
            "axes.edgecolor": PALETTE["grid"],
            "axes.labelcolor": PALETTE["ink2"],
            "axes.titlecolor": PALETTE["ink"],
            "axes.titlesize": 12,
            "axes.titleweight": "semibold",
            "axes.titlelocation": "left",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": PALETTE["grid"],
            "grid.linewidth": 0.8,
            "axes.axisbelow": True,
            "xtick.color": PALETTE["ink2"],
            "ytick.color": PALETTE["ink2"],
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.frameon": False,
            "legend.fontsize": 9,
            "lines.linewidth": 2,
            "lines.markersize": 7,
            "font.size": 10,
            "figure.dpi": 120,
            "savefig.dpi": 160,
            "savefig.bbox": "tight",
        }
    )
