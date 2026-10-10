#!/usr/bin/env python
"""Chart 4: round-trip latency (p50 and p95) of every Data query against the 50 ms target,
with the network floor (SELECT 1) shaded and the server-side execution time listed per row.

    python scripts/viz/chart_latency.py            # reads docs/data/samples/bench_latency.csv
"""
import pandas as pd

import style

TARGET_MS = 50


def main() -> None:
    df = pd.read_csv(style.SAMPLES / "bench_latency.csv")
    floor = df[df["query"].str.startswith("network floor")].iloc[0]
    df = df[~df["query"].str.startswith("network floor")].iloc[::-1].reset_index(drop=True)   # hot path on top
    style.setup()
    fig, ax = style.plt.subplots(figsize=(10, 6.4))
    fig.subplots_adjust(left=0.30, right=0.84, top=0.82, bottom=0.09)
    p50_c, p95_c = style.ORDINAL_BLUE[2], style.ORDINAL_BLUE[0]

    ax.axvspan(0, floor["p50_ms"], color=style.WASH, zorder=0, linewidth=0)
    ax.text(floor["p50_ms"] / 2, len(df) - 0.35, f"network alone\n(SELECT 1 p50 {floor['p50_ms']:.0f} ms)",
            ha="center", va="bottom", fontsize=8.5, color=style.INK_2)
    ax.axvline(TARGET_MS, color=style.INK_2, linewidth=1 * style.PX, zorder=1)
    ax.text(TARGET_MS + 2, len(df) - 0.35, "target 50 ms", ha="left", va="bottom", fontsize=8.5, color=style.INK)

    y = range(len(df))
    ax.hlines(y, df["p50_ms"], df["p95_ms"], color=style.AXIS, linewidth=2 * style.PX, zorder=2)
    ax.plot(df["p95_ms"], y, "o", color=p95_c, markersize=7, markeredgecolor=style.SURFACE,
            markeredgewidth=2 * style.PX, label="p95 (slowest 5%)", zorder=3)
    ax.plot(df["p50_ms"], y, "o", color=p50_c, markersize=7, markeredgecolor=style.SURFACE,
            markeredgewidth=2 * style.PX, label="p50 (median)", zorder=4)
    xmax = max(df["p95_ms"].max(), TARGET_MS) * 1.08
    for i, r in df.iterrows():
        server = "1 row by PK" if pd.isna(r["server_exec_ms"]) else f"{r['server_exec_ms']:.2f} ms"
        ax.text(xmax * 1.02, i, server, va="center", ha="left", fontsize=8.5, color=style.INK_2)
    ax.text(xmax * 1.02, len(df) - 0.35, "server time", va="bottom", ha="left", fontsize=8.5, color=style.MUTED)

    ax.set_yticks(list(y), [f"{q}" for q in df["query"]], fontsize=9)
    ax.set_xlim(0, xmax)
    ax.set_ylim(-0.6, len(df) + 0.4)
    ax.set_xlabel("round trip from the dev laptop to Tiger Data (ms)")
    style.clean(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2, fontsize=9)
    style.title(fig, "Query latency vs the 50 ms target",
                f"{int(df['runs'].iloc[0])} runs each on a session with 10 min of data. Server execution is under a "
                "millisecond; the round trip is the network.")
    style.save(fig, "chart4_query_latency.png")


if __name__ == "__main__":
    main()
