#!/usr/bin/env python
"""Chart 5 (demo slide): across all sessions, which question type spikes stress most?

Runs queries.get_stress_by_question_type() (the questions <-> vitals time-range join over every
answered question), saves the result to docs/data/samples/stress_by_question_type.csv, and draws
the average stress peak per question kind and per interview type.

    python scripts/viz/chart_question_types.py
"""
import asyncio
import csv
import sys

import pandas as pd

import style

sys.path.insert(0, str(style.ROOT))
from backend.data import db, queries  # noqa: E402

KIND_LABEL = {"calibration": "Calibration small talk", "main": "Main question", "follow_up": "Follow-up"}


async def fetch() -> list[dict]:
    queries.FAKE = False
    await db.init_pool()
    try:
        return await queries.get_stress_by_question_type()
    finally:
        await db.close_pool()


def panel(ax, df: pd.DataFrame, labels: list[str], title: str) -> None:
    ys = list(range(len(df)))[::-1]
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.6, len(df) - 0.4)
    for x, name in ((40, "rising"), (70, "high")):
        ax.axvline(x, color=style.AXIS, linewidth=1 * style.PX, zorder=1)
        ax.text(x + 1, len(df) - 0.45, name, fontsize=8, color=style.MUTED, va="bottom")
    style.hbars(ax, ys, df["stress_peak"], style.SERIES[0])
    for y, (_, r) in zip(ys, df.iterrows()):
        ax.text(r["stress_peak"] + 1.5, y, f"{r['stress_peak']:.0f}   ({int(r['high_peaks'])} of {int(r['questions'])} went high)",
                va="center", fontsize=9, color=style.INK_2)
    ax.set_yticks(ys, labels, fontsize=9.5)
    ax.set_title(title, loc="left", fontsize=10, pad=14)
    style.clean(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)


def main() -> None:
    rows = asyncio.run(fetch())
    out = style.SAMPLES / "stress_by_question_type.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    df = pd.DataFrame(rows)
    by_kind = df[df["interview_type"].isna() & df["kind"].notna()].sort_values("stress_peak", ascending=False)
    by_type = df[df["kind"].isna() & df["interview_type"].notna()].sort_values("stress_peak", ascending=False)
    style.setup()
    fig, (a1, a2) = style.plt.subplots(2, 1, figsize=(9, 5.8),
                                       gridspec_kw={"height_ratios": [len(by_kind), max(1, len(by_type))], "hspace": 0.55})
    fig.subplots_adjust(left=0.24, right=0.95, top=0.80, bottom=0.10)
    panel(a1, by_kind, [KIND_LABEL.get(k, k) for k in by_kind["kind"]], "By question kind: average stress peak during the answer")
    panel(a2, by_type, list(by_type["interview_type"]), "By interview type (all questions)")
    a2.set_xlabel("average of each answer's peak stress (0 to 100)")
    top = by_kind.iloc[0]
    style.title(fig, f"{KIND_LABEL.get(top['kind'], top['kind'])}s spike stress most",
                f"{int(by_kind['questions'].sum())} answers across {len(by_type)} interview types on Tiger Data, one time-range join.\n"
                "Mock sessions: mock_vitals --auto spikes every other question, so re-run this on real sessions.")
    style.save(fig, "chart5_question_type_stress.png")
    print(f"wrote {out.relative_to(style.ROOT).as_posix()}")


if __name__ == "__main__":
    main()
