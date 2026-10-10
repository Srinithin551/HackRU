"""Shared chart style for docs/data/charts (light mode, static PNG).

Colors are the validated reference palette from the dataviz method: categorical slots in
fixed order, an ordinal blue ramp for ordered series, recessive gray chrome, text in ink
tokens (never the series color). One y-axis per panel; different units get their own panel.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CHARTS = ROOT / "docs" / "data" / "charts"
SAMPLES = ROOT / "docs" / "data" / "samples"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
WASH = "#f0efec"          # neutral band (events, zones)

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
ORDINAL_BLUE = ["#86b6ef", "#5598e7", "#256abf", "#104281"]   # validated --ordinal, light
CRITICAL = "#d03b3b"      # status: reserved for "over target" with a text label

DPI = 100                 # 1 inch = 100 px; saved at 2x for sharpness
PX = 72 / DPI             # points per px: linewidth=2*PX is a 2 px line


def setup() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"], "font.size": 10,
        "text.color": INK, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK_2, "ytick.labelcolor": INK_2,
        "axes.edgecolor": AXIS, "axes.linewidth": 1 * PX, "axes.grid": True, "grid.color": GRID,
        "grid.linewidth": 1 * PX, "grid.linestyle": "-", "axes.axisbelow": True,
        "lines.linewidth": 2 * PX, "lines.solid_capstyle": "round", "lines.solid_joinstyle": "round",
        "legend.frameon": False, "legend.labelcolor": INK_2, "axes.titlesize": 11,
        "axes.titleweight": "semibold", "axes.titlelocation": "left",
    })


def clean(ax, left_spine: bool = False) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_visible(left_spine)
    ax.tick_params(length=0)
    ax.grid(axis="x", visible=False)


def title(fig, text: str, subtitle: str | None = None) -> None:
    fig.text(0.012, 0.985, text, ha="left", va="top", fontsize=13, fontweight="semibold", color=INK)
    if subtitle:
        fig.text(0.012, 0.935, subtitle, ha="left", va="top", fontsize=10, color=INK_2)


def save(fig, name: str) -> Path:
    CHARTS.mkdir(parents=True, exist_ok=True)
    path = CHARTS / name
    fig.savefig(path, dpi=DPI * 2)
    plt.close(fig)
    print(f"wrote {path.relative_to(ROOT).as_posix()}")
    return path


def hbars(ax, ys, widths, color, max_px: float = 24, radius_px: float = 4, zorder: int = 3) -> None:
    """Horizontal bars from x=0: at most `max_px` thick, 4 px rounded data end, square at the baseline.
    Call after the axis limits are final (sizes are converted from pixels to data units)."""
    from matplotlib.patches import FancyBboxPatch, Rectangle
    bbox = ax.get_window_extent()
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    px_x, px_y = (x1 - x0) / bbox.width, abs(y1 - y0) / bbox.height
    h = min(0.62, max_px * px_y)
    r = radius_px * px_x
    for y, w in zip(ys, widths):
        if not w or w <= 0:
            continue
        ax.add_patch(FancyBboxPatch((0, y - h / 2), w, h, boxstyle=f"round,pad=0,rounding_size={min(r, w)}",
                                    mutation_aspect=px_y / px_x, facecolor=color, edgecolor="none", zorder=zorder))
        ax.add_patch(Rectangle((0, y - h / 2), min(r, w), h, facecolor=color, edgecolor="none", zorder=zorder))
