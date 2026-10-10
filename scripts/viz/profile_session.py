#!/usr/bin/env python
"""Profile one session's 1 Hz vitals stream: ranges, nulls, confidence, gaps.

Input is mock_vitals.py --stdout NDJSON (column `ts`) or an export_session.py
vitals CSV (column `time`, plus `stress`). Writes a CSV copy of the stream
(for NDJSON input) and a markdown profile.

    python scripts/viz/profile_session.py stream.ndjson --csv docs/data/samples/x.csv --out docs/data/profile_x.md
    python scripts/viz/profile_session.py docs/data/samples/<sid>_vitals.csv --out docs/data/profile_y.md
"""
import argparse
from pathlib import Path

import pandas as pd

NUMERIC = ["heart_rate", "breathing_rate", "focus", "confidence", "stress"]


def load(path: Path) -> pd.DataFrame:
    df = pd.read_json(path, lines=True) if path.suffix == ".ndjson" else pd.read_csv(path)
    df = df.rename(columns={"ts": "time"})
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    return df.sort_values("time").reset_index(drop=True)


def runs(mask: pd.Series, t: pd.Series) -> str:
    """'t=20 s for 19 s, ...' for each run of consecutive True rows."""
    out, start = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            out.append(f"t={t.iloc[start]:.0f} s for {i - start} rows")
            start = None
    return ", ".join(out) or "none"


def profile(df: pd.DataFrame, title: str, source: str) -> str:
    t = (df["time"] - df["time"].iloc[0]).dt.total_seconds()
    dt = df["time"].diff().dt.total_seconds().dropna()
    by_second = df["time"].dt.floor("s")
    span = t.iloc[-1]
    lines = [f"# {title}", "", f"Source: `{source}`", "",
             "## Shape and cadence", "",
             "| Metric | Value |", "|---|---|",
             f"| Rows | {len(df)} |",
             f"| Time span | {span:.1f} s ({df['time'].iloc[0]:%H:%M:%S} to {df['time'].iloc[-1]:%H:%M:%S} UTC) |",
             f"| Expected rows at 1 Hz | {int(round(span)) + 1} |",
             f"| Interval between readings (min / median / max) | {dt.min():.3f} / {dt.median():.3f} / {dt.max():.3f} s |",
             f"| Gaps > 1.5 s (missing seconds) | {(dt > 1.5).sum()} |",
             f"| Readings < 0.5 s apart | {(dt < 0.5).sum()} |",
             f"| Two readings in the same wall-clock second | {by_second.duplicated().sum()} |",
             f"| Duplicate timestamps | {df['time'].duplicated().sum()} |", ""]
    if "session_id" in df:
        lines[-1:-1] = [f"| Sessions | {df['session_id'].nunique()} |"]

    lines += ["## Columns", "",
              "| Column | Nulls | Null % | Min | p5 | Mean | p95 | Max | Std |",
              "|---|---|---|---|---|---|---|---|---|"]
    for c in [c for c in NUMERIC if c in df]:
        s = df[c]
        if s.notna().any():
            q = s.quantile([0.05, 0.95])
            lines.append(f"| `{c}` | {s.isna().sum()} | {100 * s.isna().mean():.1f}% | {s.min():.2f} | "
                         f"{q[0.05]:.2f} | {s.mean():.2f} | {q[0.95]:.2f} | {s.max():.2f} | {s.std():.2f} |")
        else:
            lines.append(f"| `{c}` | {s.isna().sum()} | 100% | | | | | | |")
    if "expression" in df:
        lines.append(f"| `expression` | {df['expression'].isna().sum()} | "
                     f"{100 * df['expression'].isna().mean():.1f}% | | | | | | |")

    conf = df["confidence"].round(2).value_counts().sort_index()
    lines += ["", "## Confidence distribution", "",
              "| Confidence | Rows | % | Used in stress math (>= 0.5) |", "|---|---|---|---|"]
    lines += [f"| {v:.2f} | {n} | {100 * n / len(df):.1f}% | {'yes' if v >= 0.5 else 'no'} |"
              for v, n in conf.items()]
    lines += ["", f"Rows dropped from all averages (confidence < 0.5): **{(df['confidence'] < 0.5).sum()}**", ""]

    expr = df["expression"].fillna("(null)").value_counts()
    lines += ["## Expression distribution", "", "| Expression | Rows |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in expr.items()]

    lost = runs(df["confidence"] < 0.5, t)
    frown = runs(df["expression"].eq("frown"), t)
    distracted = runs(df["focus"].lt(0.4) & df["confidence"].ge(0.5), t)
    lines += ["", "## Events visible in the stream", "",
              "| Pattern | Runs (seconds from start) |", "|---|---|",
              f"| Face lost (confidence < 0.5) | {lost} |",
              f"| Frown (stress spike) | {frown} |",
              f"| Focus < 0.4 with a face (distraction) | {distracted} |"]
    if "stress" in df and df["stress"].notna().any():
        lines += [f"| Stress > 70 | {runs(df['stress'].gt(70), t)} |",
                  f"| Stress null while confidence >= 0.5 | {int((df['stress'].isna() & df['confidence'].ge(0.5)).sum())} rows |"]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="markdown profile to write")
    ap.add_argument("--csv", type=Path, help="also write the stream as CSV (for NDJSON input)")
    ap.add_argument("--title", default="Mock session profile")
    ap.add_argument("--source", help="source label shown in the report (default: the input path)")
    args = ap.parse_args()
    df = load(args.input)
    source = args.source or args.input.as_posix()
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        df.assign(time=df["time"].map(lambda x: x.isoformat())).to_csv(args.csv, index=False)
        source = args.csv.as_posix()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(profile(df, args.title, source), encoding="utf-8")
    print(f"wrote {args.out}" + (f" and {args.csv}" if args.csv else ""))


if __name__ == "__main__":
    main()
