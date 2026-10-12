"""
Global look of the paper figures: light, clear, quiet (after Tufte).

- Only left and bottom spines, thin and grey; small outward ticks; no grid by
  default (use ``faint_grid(ax)`` where reading values matters).
- One muted categorical palette, sampled from Crameri's scientific colour maps
  (perceptually uniform; https://www.fabiocrameri.ch/colourmaps/), avoiding
  batlow and other loud maps.
- Continuous data: ``CMAP`` (lapaz, light and sequential); signed data:
  ``CMAP_DIV`` (broc, soft and diverging).

    import style
    style.apply()
    ax.plot(x, y, color=style.C[0])
"""

from __future__ import annotations

import matplotlib as mpl
import numpy as np

try:
    import cmcrameri.cm as cmc
except ImportError as exc:               # pragma: no cover
    raise ImportError("paper figures need cmcrameri: pip install cmcrameri") from exc


def _pick(cmap, x):
    return mpl.colors.to_hex(cmap(x))


# muted categorical palette (all from Crameri maps)
C = [
    _pick(cmc.lapaz, 0.28),     # 0 slate blue
    _pick(cmc.lipari, 0.62),    # 1 terracotta
    _pick(cmc.navia, 0.58),     # 2 sea green
    _pick(cmc.bamako, 0.78),    # 3 ochre
    _pick(cmc.acton, 0.45),     # 4 plum
    _pick(cmc.oslo, 0.58),      # 5 grey blue
]
ACCENT = C[1]                   # highlights, markers to notice
INK = "#2b2b2b"                 # text and data in black-and-white panels
GREY = "#8a8a8a"                # reference lines, annotations
LIGHT = "#d9d9d9"               # shading
CMAP = cmc.lapaz_r              # sequential: light -> dark slate
CMAP_DIV = cmc.broc             # diverging


def tint(color, amount=0.82):
    """``color`` mixed with white (``amount`` = share of white): box fills."""
    r = np.array(mpl.colors.to_rgb(color))
    return mpl.colors.to_hex(r + (1 - r) * amount)


def apply():
    mpl.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 7.5, "axes.titlesize": 8, "axes.labelsize": 7.5,
        "legend.fontsize": 6.5, "xtick.labelsize": 6.5, "ytick.labelsize": 6.5,
        "text.color": INK, "axes.labelcolor": INK, "axes.titlecolor": INK,
        "axes.edgecolor": "#6b6b6b", "axes.linewidth": 0.5,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": False, "grid.color": "#e6e6e6", "grid.linewidth": 0.5,
        "axes.prop_cycle": mpl.cycler(color=C),
        "xtick.color": "#6b6b6b", "ytick.color": "#6b6b6b", "xtick.direction": "out", "ytick.direction": "out",
        "xtick.major.size": 2.5, "ytick.major.size": 2.5, "xtick.major.width": 0.5, "ytick.major.width": 0.5,
        "xtick.minor.size": 1.5, "ytick.minor.size": 1.5, "xtick.minor.width": 0.4, "ytick.minor.width": 0.4,
        "lines.linewidth": 0.8, "lines.markersize": 3, "patch.linewidth": 0.5,
        "legend.frameon": False, "legend.handlelength": 1.4,
        "image.cmap": "cmc.lapaz_r" if "cmc.lapaz_r" in mpl.colormaps else "cividis",
        "savefig.dpi": 300, "figure.dpi": 150,
    })


def faint_grid(ax, axis="both"):
    ax.grid(True, axis=axis, color="#ececec", lw=0.5, zorder=0)
    ax.set_axisbelow(True)


def label(ax, s, x=-0.12, y=1.04):
    ax.text(x, y, s, transform=ax.transAxes, fontsize=9, fontweight="bold", va="bottom", ha="right", color=INK)


def row_axes(fig, widths_in, height_in, left_in=0.55, gaps_in=0.6, bottom_in=0.45):
    """A row of axes with the SAME plot height (inches) and the given widths;
    ``gaps_in`` is one number or one per gap. Returns the axes."""
    W, H = fig.get_size_inches()
    gaps = [gaps_in] * (len(widths_in) - 1) if np.isscalar(gaps_in) else list(gaps_in)
    axes, x = [], left_in
    for k, w in enumerate(widths_in):
        axes.append(fig.add_axes([x / W, bottom_in / H, w / W, height_in / H]))
        x += w + (gaps[k] if k < len(gaps) else 0)
    return axes
