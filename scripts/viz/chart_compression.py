#!/usr/bin/env python
"""Chart 6: vitals size in the rowstore vs the columnstore, from scripts/viz/compression_demo.py.

One panel per dataset (each on its own scale): the real mock sessions, then the same plus
synthetic sessions at a realistic size.

    python scripts/viz/chart_compression.py      # reads docs/data/samples/compression.csv
"""
import pandas as pd

import style


def main() -> None:
    df = pd.read_csv(style.SAMPLES / "compression.csv")
    style.setup()
    fig, axes = style.plt.subplots(len(df), 1, figsize=(9, 1.5 + 1.35 * len(df)), squeeze=False,
                                   gridspec_kw={"hspace": 0.9})
    fig.subplots_adjust(left=0.17, right=0.93, top=0.74, bottom=0.12)
    for ax, (_, r) in zip(axes[:, 0], df.iterrows()):
        kib = [r["rowstore_bytes"] / 1024, r["columnstore_bytes"] / 1024]
        ax.set_xlim(0, kib[0] * 1.32)
        ax.set_ylim(-0.6, 1.6)
        style.hbars(ax, [1], [kib[0]], style.AXIS)
        style.hbars(ax, [0], [kib[1]], style.SERIES[0])
        ax.text(kib[0] + kib[0] * 0.015, 1, f"{kib[0]:,.0f} KiB", va="center", fontsize=9, color=style.INK_2)
        ax.text(kib[1] + kib[0] * 0.015, 0, f"{kib[1]:,.0f} KiB   ({r['ratio']}x smaller)", va="center",
                fontsize=9, color=style.INK)
        ax.set_yticks([1, 0], ["rowstore", "columnstore"], fontsize=9.5)
        ax.set_title(f"{r['dataset']}: {int(r['sessions'])} sessions, {int(r['rows']):,} readings",
                     loc="left", fontsize=10, pad=6)
        style.clean(ax)
        ax.grid(visible=False)
        ax.set_xticks([])
        ax.spines["bottom"].set_visible(False)
    style.title(fig, "Columnstore: vitals stored per session, column by column",
                "segmentby session_id, orderby time DESC. Rowstore = heap + indexes. Same query results after conversion.\n"
                "Synthetic sessions come from the mock_vitals simulator and exist only in the demo table.")
    style.save(fig, "chart6_compression.png")


if __name__ == "__main__":
    main()
