#!/usr/bin/env python
"""Chart 2: how the stress score responds to a heart-rate rise, one line per focus level.

Computed straight from backend/data/metrics.py (no database), so it always matches the
formula the backend uses. Re-run after tuning the constants:

    python scripts/viz/chart_stress_curve.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

import style

sys.path.insert(0, str(style.ROOT))
from backend.contracts import Baseline, VitalsReading  # noqa: E402
from backend.data import metrics  # noqa: E402

B = Baseline(heart_rate=72.0, breathing_rate=14.0, focus=0.8, n_samples=30)
FOCUS_LEVELS = [0.8, 0.65, 0.5, 0.3]           # baseline, then progressively distracted
TS = datetime(2026, 10, 10, tzinfo=timezone.utc)


def score(hr_delta_pct: float, focus: float) -> float:
    r = VitalsReading(ts=TS, heart_rate=B.heart_rate * (1 + hr_delta_pct / 100),
                      breathing_rate=B.breathing_rate, focus=focus, confidence=0.95)
    return metrics.stress_score(r, B)


def main() -> None:
    style.setup()
    x = np.linspace(-10, 40, 201)
    fig, ax = style.plt.subplots(figsize=(8, 4.6))
    fig.subplots_adjust(left=0.07, right=0.80, top=0.82, bottom=0.12)

    # threshold zones everyone uses: < 40 calm, 40-70 rising, > 70 high (labels carry meaning, not color)
    for y in (metrics.CALM_BELOW, metrics.HIGH_ABOVE):
        ax.axhline(y, color=style.AXIS, linewidth=1 * style.PX, zorder=1)
    for y, label in ((30, "calm"), (55, "rising"), (85, "high")):     # placed clear of the lines
        ax.text(-9.6, y, label, va="center", ha="left", fontsize=9, color=style.MUTED)

    for focus, color in zip(FOCUS_LEVELS, style.ORDINAL_BLUE):
        y = [score(v, focus) for v in x]
        name = f"focus {focus:.2f}" + (" (baseline)" if focus == B.focus else "")
        ax.plot(x, y, color=color, label=name, zorder=3)
        ax.text(40.8, y[-1], name, va="center", ha="left", fontsize=9, color=style.INK_2)

    # the anchor everyone should remember: +25% HR alone = 50
    ax.plot([25], [50], "o", markersize=8 * style.PX * 1.4, color=style.ORDINAL_BLUE[0],
            markeredgecolor=style.SURFACE, markeredgewidth=2 * style.PX, zorder=4)
    ax.annotate("+25% HR alone = 50\n(HR part maxes out here)", xy=(25, 50), xytext=(27, 30),
                fontsize=9, color=style.INK_2, arrowprops=dict(arrowstyle="-", color=style.MUTED,
                                                               linewidth=1 * style.PX))

    ax.set_xlim(-10, 40)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 20, 40, 60, 70, 80, 100])
    ax.set_xticks(range(-10, 41, 5))
    ax.xaxis.set_major_formatter(style.plt.FuncFormatter(lambda v, _: f"{v:+.0f}%" if v else "0%"))
    ax.set_xlabel("heart rate vs baseline")
    ax.set_ylabel("stress score")
    style.clean(ax)
    ax.legend(loc="upper left", bbox_to_anchor=(0.0, 1.12), ncol=4, fontsize=9, handlelength=1.6)
    style.title(fig, "Stress score vs heart-rate rise",
                "Breathing held at baseline (72 bpm, 14/min, focus 0.8). HR maxes out at +25%; "
                "a focus drop adds up to 20 more.")
    style.save(fig, "chart2_stress_response_curve.png")


if __name__ == "__main__":
    main()
