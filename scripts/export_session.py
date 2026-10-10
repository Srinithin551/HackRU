#!/usr/bin/env python
"""Export one session from Tiger Data to CSV under docs/data/samples/.

    python scripts/export_session.py <session_id>
    python scripts/export_session.py latest --name checkpoint_phase4

Writes <name>_vitals.csv (raw 1 Hz rows), <name>_questions.csv (one row per question,
with its QuestionStats), <name>_vitals_5s.csv (the replay series) and <name>_session.csv
(baseline). <name> defaults to session_<first 8 chars of the id>.
"""
import argparse
import asyncio
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.data import db, queries  # noqa: E402

OUT = ROOT / "docs" / "data" / "samples"


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()})


async def export(session_arg: str, name: str | None) -> int:
    queries.FAKE = False
    pool = await db.init_pool()
    try:
        if session_arg == "latest":
            sid = await pool.fetchval("SELECT id FROM sessions WHERE user_name IS DISTINCT FROM 'pytest' "
                                      "ORDER BY started_at DESC LIMIT 1")
        else:
            sid = queries._uuid(session_arg)
        session = await pool.fetchrow(
            "SELECT id, user_name, interview_type, role_title, started_at, ended_at, baseline_hr, "
            "baseline_br, baseline_focus, baseline_n FROM sessions WHERE id = $1", sid) if sid else None
        if session is None:
            print(f"no session {session_arg!r}", file=sys.stderr)
            return 1
        name = name or f"session_{str(sid)[:8]}"
        OUT.mkdir(parents=True, exist_ok=True)

        vitals = await pool.fetch(
            "SELECT time, heart_rate, breathing_rate, focus, expression, confidence, stress "
            "FROM vitals WHERE session_id = $1 ORDER BY time", sid)
        series = await pool.fetch(
            "SELECT bucket, hr, br, focus, stress, stress_peak FROM vitals_5s WHERE session_id = $1 ORDER BY bucket", sid)
        history = await queries.get_session_history(str(sid))
        qrows = await pool.fetch(
            "SELECT id, created_at, answer_started_at, answered_at FROM questions WHERE session_id = $1", sid)
        times = {str(r["id"]): r for r in qrows}
        questions = []
        for h in history:
            t = times[h["question_id"]]
            questions.append({"question_id": h["question_id"], "idx": h["idx"], "kind": h["kind"],
                              "text": h["text"], "transcript": h["transcript"],
                              "created_at": t["created_at"], "answer_started_at": t["answer_started_at"],
                              "answered_at": t["answered_at"]}
                             | {k: v for k, v in h["stats"].model_dump().items() if k != "question_id"})

        files = {f"{name}_session.csv": [dict(session) | {"id": str(session["id"])}],
                 f"{name}_vitals.csv": [dict(r) for r in vitals],
                 f"{name}_questions.csv": questions,
                 f"{name}_vitals_5s.csv": [dict(r) for r in series]}
        for fname, rows in files.items():
            write_csv(OUT / fname, rows)
            print(f"wrote {(OUT / fname).relative_to(ROOT).as_posix()} ({len(rows)} rows)")
        return 0
    finally:
        await db.close_pool()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_id", help="a session UUID, or 'latest'")
    ap.add_argument("--name", help="file name prefix (default session_<id prefix>)")
    args = ap.parse_args()
    return asyncio.run(export(args.session_id, args.name))


if __name__ == "__main__":
    sys.exit(main())
