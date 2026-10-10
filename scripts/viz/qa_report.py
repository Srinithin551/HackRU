#!/usr/bin/env python
"""Data QA for one exported session: is every stored number what the contract says it is?

    python scripts/viz/qa_report.py checkpoint_phase4 [--replay frontend/src/mocks/replay_sample.json]

Reads docs/data/samples/<name>_*.csv (scripts/export_session.py) and the mock_vitals
--events-out CSV, writes docs/data/qa_<name>.md, exits 1 if any check FAILs.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import style

SPIKE_S, DISTRACT_S, FACE_S = 26, 8, 5        # mock_vitals.py event lengths
MOCK_BASE = {"baseline_hr": 72.0, "baseline_br": 14.0, "baseline_focus": 0.8}

REPLAY_SHAPE = {
    "session": {"id", "user_name", "started_at", "ended_at", "baseline"},
    "baseline": {"heart_rate", "breathing_rate", "focus"},
    "series": {"t", "hr", "br", "focus", "stress", "stress_peak"},
    "question": {"id", "idx", "kind", "text", "transcript", "answer_started_at", "answered_at", "stats"},
    "stats": {"question_id", "duration_s", "n_samples", "hr_mean", "hr_peak", "hr_delta_pct", "br_mean",
              "focus_mean", "stress_mean", "stress_peak", "peak_offset_s"},
}


def label_rows(v: pd.DataFrame, ev: pd.DataFrame | None) -> pd.Series:
    """calm | spike | distraction | face_lost | settle (spike tail / just after an event)."""
    lab = pd.Series("calm", index=v.index)
    if ev is None:
        return lab
    for _, e in ev.iterrows():
        dt = (v["time"] - e["time"]).dt.total_seconds()
        if e["key"] == "s":
            lab[(dt >= 0) & (dt < SPIKE_S + 4)] = "settle"
            lab[(dt >= 3) & (dt <= 16)] = "spike"          # envelope >= 0.5: ramp top, peak, early decay
        elif e["key"] == "d":
            lab[(dt >= 0) & (dt < DISTRACT_S)] = "distraction"
        elif e["key"] == "f":
            lab[(dt >= 0) & (dt < FACE_S)] = "face_lost"
    lab[v["confidence"] < 0.5] = "face_lost"
    return lab


def check_replay(path: Path) -> list[tuple[str, bool, str]]:
    r = json.loads(path.read_text(encoding="utf-8"))
    out = []
    ok = set(r) == {"session", "series", "questions"}
    out.append(("replay: top-level keys session, series, questions", ok, ", ".join(sorted(r))))
    s = r.get("session", {})
    out.append(("replay: session keys", set(s) == REPLAY_SHAPE["session"], ", ".join(sorted(s))))
    b = s.get("baseline") or {}
    out.append(("replay: session.baseline keys", set(b) == REPLAY_SHAPE["baseline"], ", ".join(sorted(b))))
    series = r.get("series", [])
    bad = [p for p in series if set(p) != REPLAY_SHAPE["series"]]
    out.append(("replay: every series point has t, hr, br, focus, stress, stress_peak",
                bool(series) and not bad, f"{len(series)} points, {len(bad)} malformed"))
    ts = [p["t"] for p in series]
    out.append(("replay: series ordered by time, 5 s apart, ISO 'Z' timestamps",
                ts == sorted(ts) and all(t.endswith("Z") for t in ts) and
                all((pd.Timestamp(b2) - pd.Timestamp(a)).total_seconds() % 5 == 0 for a, b2 in zip(ts, ts[1:])),
                f"{ts[0]} .. {ts[-1]}" if ts else "empty"))
    qs = r.get("questions", [])
    badq = [q for q in qs if set(q) != REPLAY_SHAPE["question"] or set(q["stats"]) != REPLAY_SHAPE["stats"]]
    out.append(("replay: every question has the agreed keys and full QuestionStats",
                bool(qs) and not badq, f"{len(qs)} questions, {len(badq)} malformed"))
    out.append(("replay: questions ordered by idx", [q["idx"] for q in qs] == sorted(q["idx"] for q in qs),
                str([q["idx"] for q in qs])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("--replay", type=Path, help="also check a replay JSON against the agreed shape")
    args = ap.parse_args()
    S = style.SAMPLES
    v = pd.read_csv(S / f"{args.name}_vitals.csv")
    v["time"] = pd.to_datetime(v["time"], utc=True, format="ISO8601")
    session = pd.read_csv(S / f"{args.name}_session.csv").iloc[0]
    v5 = pd.read_csv(S / f"{args.name}_vitals_5s.csv")
    v5["bucket"] = pd.to_datetime(v5["bucket"], utc=True, format="ISO8601")
    ev_path = S / f"{args.name}_events.csv"
    ev = pd.read_csv(ev_path) if ev_path.exists() else None
    if ev is not None:
        ev["time"] = pd.to_datetime(ev["time"], utc=True, format="ISO8601")
        ev = ev[ev["time"].between(v["time"].min(), v["time"].max())]   # events after the session ended had no effect
    v["label"] = label_rows(v, ev)
    good = v["confidence"] >= 0.5

    checks: list[tuple[str, bool, str]] = []
    calm = v.loc[(v["label"] == "calm") & good, "stress"]
    checks.append(("calm rows: stress ~ 0 (mean < 10, p95 < 25)",
                   calm.mean() < 10 and calm.quantile(0.95) < 25,
                   f"n={len(calm)}, mean {calm.mean():.1f}, p95 {calm.quantile(0.95):.1f}, max {calm.max():.1f}"))
    if ev is not None:
        for _, e in ev[ev["key"] == "s"].iterrows():
            dt = (v["time"] - e["time"]).dt.total_seconds()
            win = v.loc[(dt >= 0) & (dt <= 16) & good]
            before = v.loc[(dt >= -10) & (dt < 0) & good, "stress"]
            peak = win["stress"].max()
            at = (win.loc[win["stress"].idxmax(), "time"] - e["time"]).total_seconds() if len(win) else np.nan
            checks.append((f"s at {e['time']:%H:%M:%S}: stress jumps past 70 ('high'), peak ~6 s in",
                           peak > 70 and 3 <= at <= 10,
                           f"10 s before: mean {before.mean():.1f} -> peak {peak:.1f} at +{at:.0f} s"))
    lost = v[~good]
    checks.append(("face-lost rows (confidence < 0.5): stress is NULL",
                   lost["stress"].isna().all(), f"{len(lost)} rows, {lost['stress'].notna().sum()} non-null"))
    checks.append(("face-lost rows: vitals are NULL",
                   lost[["heart_rate", "breathing_rate", "focus"]].isna().all().all(), f"{len(lost)} rows"))
    checks.append(("rows with a face all have stress (backfill worked)",
                   v.loc[good, "stress"].notna().all(), f"{v.loc[good, 'stress'].isna().sum()} NULL of {good.sum()}"))
    checks.append(("stress within 0..100", v["stress"].dropna().between(0, 100).all(),
                   f"min {v['stress'].min():.1f}, max {v['stress'].max():.1f}"))
    base_ok = (pd.notna(session["baseline_hr"]) and session["baseline_n"] >= 10 and
               all(abs(session[k] - MOCK_BASE[k]) <= tol for k, tol in
                   (("baseline_hr", 2.0), ("baseline_br", 1.0), ("baseline_focus", 0.05))))
    checks.append(("baseline close to the mock's true calm values (72 bpm, 14/min, 0.80) from >= 10 samples",
                   bool(base_ok), f"HR {session['baseline_hr']:.1f}, BR {session['baseline_br']:.1f}, "
                                  f"focus {session['baseline_focus']:.3f}, n={int(session['baseline_n'])}"))
    dups = v["time"].duplicated().sum()
    checks.append(("no duplicate timestamps", dups == 0, f"{dups} duplicates in {len(v)} rows"))
    gaps = v["time"].diff().dt.total_seconds().dropna()
    checks.append(("1 Hz stream: no gaps > 1.5 s, no two readings < 0.5 s apart",
                   (gaps > 1.5).sum() == 0 and (gaps < 0.5).sum() == 0,
                   f"interval min {gaps.min():.2f} / median {gaps.median():.2f} / max {gaps.max():.2f} s"))

    # vitals_5s must equal a 5 s rollup of the raw rows (continuous aggregate correctness)
    raw5 = (v.assign(bucket=v["time"].dt.floor("5s")).groupby("bucket")
            .agg(stress=("stress", "mean"), stress_peak=("stress", "max"), hr=("heart_rate", "mean")).reset_index())
    m = raw5.merge(v5, on="bucket", suffixes=("_raw", "_cagg"), how="outer", indicator=True)
    both = m[m["_merge"] == "both"]
    diff = max((both["stress_raw"] - both["stress_cagg"]).abs().max(skipna=True) or 0,
               (both["hr_raw"] - both["hr_cagg"]).abs().max(skipna=True) or 0)
    checks.append(("vitals_5s matches a 5 s rollup of raw vitals (same buckets, same values)",
                   (m["_merge"] != "both").sum() == 0 and diff < 0.05,
                   f"{len(v5)} buckets, {(m['_merge'] != 'both').sum()} unmatched, max abs diff {diff:.3f}"))
    if args.replay:
        checks += check_replay(args.replay)

    by_label = v.groupby("label")["stress"].agg(["count", "mean", "max"]).round(1)
    lines = [f"# Data QA: `{args.name}`", "",
             f"Session `{session['id']}`: {len(v)} readings, {len(v5)} rows in `vitals_5s`. "
             f"Generated by `scripts/viz/qa_report.py`.", "",
             "| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {c} | {'PASS' if ok else '**FAIL**'} | {d} |" for c, ok, d in checks]
    lines += ["", "## Stress by period", "",
              "Periods come from the `mock_vitals.py --events-out` log. `spike` = 3 to 16 s after an `s` press "
              "(ramp top, peak, early decay); `settle` = the rest of the 26 s spike plus 4 s.", "",
              "| Period | Rows | Mean stress | Max stress |", "|---|---|---|---|"]
    lines += [f"| {k} | {int(r['count'])} | {r['mean']} | {r['max']} |" for k, r in by_label.iterrows()]
    lines += ["", "Why spikes peak near 85 and not 50: `+25% HR ≈ 50` is the heart-rate-only case "
              "(unit test `test_hr_plus_25pct_is_about_50`). The mock's `s` also raises breathing 30% and drops "
              "focus to 0.5, which adds 0.3×0.75 + 0.2×0.6 = 0.345, so the peak is 50 + 34.5 = 84.5."]
    out = style.ROOT / "docs" / "data" / f"qa_{args.name}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(style.ROOT).as_posix()}")
    failed = [c for c, ok, _ in checks if not ok]
    for c, ok, d in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {c}: {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
