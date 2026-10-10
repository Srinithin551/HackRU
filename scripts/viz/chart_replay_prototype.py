#!/usr/bin/env python
"""Chart 3: a prototype of the replay chart, drawn from the replay JSON itself
(GET /api/sessions/{id}/replay, series from the vitals_5s continuous aggregate).

Shows Frontend the target: stress and heart rate on a shared time axis (two panels, since
they have different units), each answer window shaded and labeled, the session's peak called out.

    python scripts/viz/chart_replay_prototype.py frontend/src/mocks/replay_sample.json
"""
import argparse
import json
from pathlib import Path

import pandas as pd

import style


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("replay", type=Path)
    ap.add_argument("--out", default="chart3_replay_prototype.png")
    args = ap.parse_args()
    r = json.loads(args.replay.read_text(encoding="utf-8"))
    s = pd.DataFrame(r["series"])
    t0 = pd.Timestamp(r["session"]["started_at"])
    s["t"] = (pd.to_datetime(s["t"], utc=True) - t0).dt.total_seconds()
    base = r["session"]["baseline"]
    style.setup()

    fig, (ax_s, ax_h) = style.plt.subplots(2, 1, figsize=(10, 5.6), sharex=True,
                                            gridspec_kw={"height_ratios": [1.35, 1], "hspace": 0.32})
    fig.subplots_adjust(left=0.07, right=0.89, top=0.80, bottom=0.10)

    # answer windows, labeled like the replay page: Q{idx}, calibration as "Cal"
    for q in r["questions"]:
        if not q["answer_started_at"] or not q["answered_at"]:
            continue
        a = (pd.Timestamp(q["answer_started_at"]) - t0).total_seconds()
        b = (pd.Timestamp(q["answered_at"]) - t0).total_seconds()
        for ax in (ax_s, ax_h):
            ax.axvspan(a, b, color=style.WASH, zorder=0, linewidth=0)
        label = "Cal" if q["kind"] == "calibration" else f"Q{q['idx']}" + ("+" if q["kind"] == "follow_up" else "")
        ax_s.text((a + b) / 2, 1.02, label, transform=ax_s.get_xaxis_transform(), ha="center", va="bottom",
                  fontsize=8.5, color=style.INK_2)

    ax_s.fill_between(s["t"], s["stress"], color=style.SERIES[0], alpha=0.10, linewidth=0, step="post")
    ax_s.plot(s["t"], s["stress_peak"], color=style.ORDINAL_BLUE[0], drawstyle="steps-post", zorder=3,
              label="peak in the 5 s bucket (stress_peak)")
    ax_s.plot(s["t"], s["stress"], color=style.ORDINAL_BLUE[2], drawstyle="steps-post", zorder=3,
              label="mean in the 5 s bucket (stress)")
    for y, name in ((40, "rising"), (70, "high")):
        ax_s.axhline(y, color=style.AXIS, linewidth=1 * style.PX, zorder=1)
        ax_s.text(s["t"].max() + 8, y, f"{y} {name}", va="center", fontsize=8.5, color=style.INK_2)
    ax_s.set_ylim(0, 100)
    ax_s.set_yticks([0, 40, 70, 100])
    ax_s.set_title("Stress (0 to 100)", loc="left", fontsize=10, pad=16)
    ax_s.legend(loc="lower right", bbox_to_anchor=(1.0, 1.08), ncol=2, fontsize=8.5)

    # call out the single biggest moment, the way the report's "hardest moment" does:
    # the question with the highest stress_peak, dotted at its peak time (answer start + peak_offset_s)
    peak_q = max((q for q in r["questions"] if q["stats"]["stress_peak"] is not None),
                 key=lambda q: q["stats"]["stress_peak"], default=None)
    if peak_q is not None:
        st = peak_q["stats"]
        start = peak_q["answer_started_at"] or peak_q["answered_at"]
        x = (pd.Timestamp(start) - t0).total_seconds() + (st["peak_offset_s"] or 0)
        ax_s.plot([x], [st["stress_peak"]], "o", markersize=6, color=style.ORDINAL_BLUE[2],
                  markeredgecolor=style.SURFACE, markeredgewidth=2 * style.PX, zorder=4)
        lab = "Calibration" if peak_q["kind"] == "calibration" else f"Q{peak_q['idx']}"
        ax_s.annotate(f"hardest moment: {lab}, stress {st['stress_peak']:.0f}\n"
                      f"{st['peak_offset_s']:.0f} s in, answer HR {st['hr_delta_pct']:+.0f}%",
                      xy=(x, st["stress_peak"]), xytext=(12, -2), textcoords="offset points", fontsize=8.5,
                      color=style.INK_2, va="top", ha="left")

    ax_h.plot(s["t"], s["hr"], color=style.SERIES[0], drawstyle="steps-post", zorder=3)
    if base:
        ax_h.axhline(base["heart_rate"], color=style.MUTED, linewidth=1 * style.PX, zorder=2)
        ax_h.text(s["t"].max() + 8, base["heart_rate"], f"baseline {base['heart_rate']:.0f}", va="center",
                  fontsize=8.5, color=style.INK_2)
    ax_h.set_title("Heart rate (bpm)", loc="left", fontsize=10, pad=4)
    ax_h.set_xlabel("time into the session")
    ax_h.xaxis.set_major_formatter(style.plt.FuncFormatter(lambda v, _: f"{int(v) // 60}:{int(v) % 60:02d}"))
    ax_h.set_xlim(0, s["t"].max() + 5)
    for ax in (ax_s, ax_h):
        style.clean(ax)

    n_main = sum(q["kind"] != "calibration" for q in r["questions"])
    style.title(fig, "Replay chart prototype (target for the Frontend replay page)",
                f"{len(s)} points from vitals_5s via /api/sessions/{{id}}/replay, {n_main} questions. "
                "Shaded: answer windows (+ = follow-up). Source: frontend/src/mocks/replay_sample.json")
    style.save(fig, args.out)


if __name__ == "__main__":
    main()
