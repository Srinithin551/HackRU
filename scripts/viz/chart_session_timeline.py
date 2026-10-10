#!/usr/bin/env python
"""Chart 1: one session over time. HR, breathing, focus and stress as four panels on a shared
time axis (different units never share a y-axis), mock_vitals events shaded and labeled,
question answer windows in a strip on top.

Reads the CSVs written by scripts/export_session.py (+ the --events-out CSV of mock_vitals.py):

    python scripts/viz/chart_session_timeline.py checkpoint_phase4
"""
import argparse

import pandas as pd

import style

SPANS = {"s": ("spike", 26), "d": ("distraction", 8), "f": ("face lost", 5)}   # seconds, from mock_vitals.py
PANELS = [("heart_rate", "Heart rate", "bpm", "baseline_hr"),
          ("breathing_rate", "Breathing", "breaths/min", "baseline_br"),
          ("focus", "Focus", "0 to 1", "baseline_focus"),
          ("stress", "Stress", "0 to 100", None)]


def load(name: str):
    read = lambda suffix, **kw: pd.read_csv(style.SAMPLES / f"{name}_{suffix}.csv", **kw)
    vitals = read("vitals")
    vitals["time"] = pd.to_datetime(vitals["time"], utc=True, format="ISO8601")
    session = read("session").iloc[0]
    t0 = vitals["time"].iloc[0]
    vitals["t"] = (vitals["time"] - t0).dt.total_seconds()
    # readings below 0.7 confidence aren't stored: put a NaN row in each gap so lines break there
    gap_rows = vitals.loc[vitals["t"].diff() > 1.5, ["t"]].assign(t=lambda d: d["t"] - 1.0)
    vitals = pd.concat([vitals, gap_rows]).sort_values("t").reset_index(drop=True)
    try:
        q = read("questions")
        for c in ("created_at", "answer_started_at", "answered_at"):
            q[c] = pd.to_datetime(q[c], utc=True, format="ISO8601")
        q["t_start"] = ((q["answer_started_at"].fillna(q["created_at"])) - t0).dt.total_seconds()
        q["t_end"] = (q["answered_at"] - t0).dt.total_seconds()
    except FileNotFoundError:
        q = None
    try:
        ev = read("events")
        ev["t"] = (pd.to_datetime(ev["time"], utc=True, format="ISO8601") - t0).dt.total_seconds()
        ev = ev[ev["t"].between(0, vitals["t"].max())]      # events after the session ended had no effect
    except FileNotFoundError:
        ev = None
    return vitals, session, q, ev


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="sample prefix in docs/data/samples (e.g. checkpoint_phase4)")
    ap.add_argument("--out", default="chart1_session_timeline.png")
    args = ap.parse_args()
    vitals, session, q, ev = load(args.name)
    style.setup()

    fig, axes = style.plt.subplots(5, 1, figsize=(10, 8.2), sharex=True,
                                   gridspec_kw={"height_ratios": [0.32, 1, 1, 1, 1.25], "hspace": 0.28})
    fig.subplots_adjust(left=0.085, right=0.88, top=0.88, bottom=0.07)
    strip, panels = axes[0], axes[1:]
    t_max = vitals["t"].max()

    # top strip: answer windows (Cal, Q1, Q2 follow-up ... = Data's idx, as on the replay chart)
    # and the mock_vitals event names, so nothing sits on top of the data
    strip.set_ylim(0, 1)
    strip.axis("off")
    if q is not None:
        for _, row in q.iterrows():
            if pd.isna(row["t_end"]):
                continue
            label = "Cal" if row["kind"] == "calibration" else                 f"Q{row['idx']}" + (" follow-up" if row["kind"] == "follow_up" else "")
            strip.barh(0.28, row["t_end"] - row["t_start"], left=row["t_start"], height=0.36,
                       color=style.AXIS, edgecolor=style.SURFACE, linewidth=2 * style.PX)
            strip.text((row["t_start"] + row["t_end"]) / 2, 0.28, label, ha="center", va="center",
                       fontsize=7.5, color=style.INK)
        strip.text(-0.01, 0.28, "answers", transform=strip.get_yaxis_transform(), ha="right", va="center",
                   fontsize=8, color=style.MUTED)
    if ev is not None:
        strip.text(-0.01, 0.78, "events", transform=strip.get_yaxis_transform(), ha="right", va="center",
                   fontsize=8, color=style.MUTED)
        for _, e in ev.iterrows():
            name, dur = SPANS[e["key"]]
            for ax in panels:
                ax.axvspan(e["t"], min(e["t"] + dur, t_max), color=style.WASH, zorder=0, linewidth=0)
            strip.axvspan(e["t"], min(e["t"] + dur, t_max), ymin=0.58, ymax=1.0, color=style.WASH, linewidth=0)
            strip.text(e["t"] + 0.6, 0.78, f"{e['key']}: {name}", fontsize=8, color=style.INK_2, va="center")

    for ax, (col, label, unit, base_col) in zip(panels, PANELS):
        ax.plot(vitals["t"], vitals[col], color=style.SERIES[0], zorder=3)
        style.clean(ax)
        ax.set_title(f"{label} ({unit})", loc="left", fontsize=10, pad=3)
        if base_col and pd.notna(session[base_col]):
            b = float(session[base_col])
            ax.axhline(b, color=style.MUTED, linewidth=1 * style.PX, zorder=2)
            ax.text(t_max + 2, b, f"baseline {b:.2f}" if col == "focus" else f"baseline {b:.1f}",
                    va="center", fontsize=8.5, color=style.INK_2)
    stress_ax = panels[-1]
    stress_ax.fill_between(vitals["t"], vitals["stress"], color=style.SERIES[0], alpha=0.10, linewidth=0)
    stress_ax.set_ylim(0, 100)
    stress_ax.set_yticks([0, 40, 70, 100])
    for y, name in ((40, "rising"), (70, "high")):
        stress_ax.axhline(y, color=style.AXIS, linewidth=1 * style.PX, zorder=1)
        stress_ax.text(t_max + 2, y, f"{y} {name}", va="center", fontsize=8.5, color=style.INK_2)
    stress_ax.set_xlabel("seconds since first reading")
    stress_ax.set_xlim(0, t_max)

    n = int(vitals["time"].notna().sum())
    sid = str(session["id"])[:8]
    style.title(fig, "One mock session: vitals and stress over time",
                f"Session {sid} on Tiger Data, {n} stored readings at 1 Hz. Readings under 0.7 confidence aren't stored "
                "(gaps).\n"
                "Shaded: mock_vitals events. Stress is relative to the calibration baseline.")
    style.save(fig, args.out)


if __name__ == "__main__":
    main()
