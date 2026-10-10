"""In-memory version of queries.py, used when DATA_FAKE=1, under pytest, or with no TIGER_DATABASE_URL.

Started as Brain's day-one stub. Same signatures as PLAN.md section 3, and it computes real
stats from whatever vitals were inserted, so mock vitals produce real stress numbers.
Nothing persists across restarts. queries.py routes calls here; don't import this directly.
"""
import logging
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.contracts import Baseline, QuestionStats, VitalsReading, WindowStats
from backend.data.metrics import MIN_CONFIDENCE, delta_pct, stress_score

log = logging.getLogger("data.fake")

_sessions: dict[str, dict] = {}
_questions: dict[str, dict] = {}
_vitals: dict[str, list[dict]] = defaultdict(list)

WARMUP_S = 10
MIN_BASELINE_SAMPLES = 10
DEFAULT_BASELINE = Baseline(heart_rate=75.0, breathing_rate=15.0, focus=0.75, n_samples=0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _r(x, digits=1):
    return round(x, digits) if x is not None else None


def _stress(row: dict, b: Optional[Baseline]) -> Optional[float]:
    s = stress_score(VitalsReading(ts=row["time"], heart_rate=row["heart_rate"],
                                   breathing_rate=row["breathing_rate"], focus=row["focus"],
                                   confidence=row["confidence"]), b)
    return _r(s)


# ---------------------------------------------------------------- sessions

async def create_session(user_name, interview_type, role_title) -> str:
    sid = str(uuid.uuid4())
    _sessions[sid] = {
        "id": sid, "user_name": user_name, "interview_type": interview_type,
        "role_title": role_title, "started_at": _now(), "ended_at": None,
        "baseline": None, "report": None,
    }
    return sid


async def get_active_session() -> Optional[str]:
    cutoff = _now() - timedelta(minutes=30)
    live = [s for s in _sessions.values() if s["ended_at"] is None and s["started_at"] > cutoff]
    return max(live, key=lambda s: s["started_at"])["id"] if live else None


async def end_session(session_id) -> None:
    if session_id in _sessions:
        _sessions[session_id]["ended_at"] = _now()


# ---------------------------------------------------------------- vitals

async def insert_vitals(readings: list[VitalsReading]) -> list[dict]:
    active = None
    rows = []
    for r in readings:
        sid = r.session_id
        if not sid:
            active = active or await get_active_session()
            sid = active
        if not sid or sid not in _sessions:
            continue
        row = {
            "time": _utc(r.ts), "session_id": sid, "heart_rate": r.heart_rate,
            "breathing_rate": r.breathing_rate, "focus": r.focus,
            "expression": r.expression, "confidence": r.confidence,
        }
        row["stress"] = _stress(row, _sessions[sid]["baseline"])
        _vitals[sid].append(row)
        rows.append(row)
    return rows


async def compute_baseline(session_id) -> Baseline:
    s = _sessions[session_id]
    first = min((v["time"] for v in _vitals[session_id]), default=s["started_at"])
    start = max(s["started_at"], first) + timedelta(seconds=WARMUP_S)
    good = [v for v in _vitals[session_id] if v["time"] >= start and v["confidence"] >= MIN_CONFIDENCE]
    if len(good) < MIN_BASELINE_SAMPLES:
        log.warning("baseline: only %d samples for %s, using population defaults", len(good), session_id)
        b = DEFAULT_BASELINE
    else:
        b = Baseline(
            heart_rate=_mean(v["heart_rate"] for v in good) or DEFAULT_BASELINE.heart_rate,
            breathing_rate=_mean(v["breathing_rate"] for v in good) or DEFAULT_BASELINE.breathing_rate,
            focus=_mean(v["focus"] for v in good) or DEFAULT_BASELINE.focus,
            n_samples=len(good),
        )
    s["baseline"] = b
    for v in _vitals[session_id]:   # backfill so the chart isn't empty at the start
        if v["stress"] is None:
            v["stress"] = _stress(v, b)
    return b


async def get_baseline(session_id) -> Optional[Baseline]:
    s = _sessions.get(session_id)
    return s["baseline"] if s else None


async def get_window_stats(session_id, seconds=15) -> WindowStats:
    cutoff = _now() - timedelta(seconds=seconds)
    rows = [v for v in _vitals.get(session_id, []) if v["time"] > cutoff and v["confidence"] >= MIN_CONFIDENCE]
    b = await get_baseline(session_id)
    hr = _mean(v["heart_rate"] for v in rows)
    br = _mean(v["breathing_rate"] for v in rows)
    exprs = Counter(v["expression"] for v in rows if v["expression"])
    return WindowStats(
        seconds=seconds, n_samples=len(rows),
        hr_mean=_r(hr), hr_delta_pct=_r(delta_pct(hr, b.heart_rate if b else None)),
        br_mean=_r(br), br_delta_pct=_r(delta_pct(br, b.breathing_rate if b else None)),
        focus_mean=_r(_mean(v["focus"] for v in rows), 3),
        dominant_expression=exprs.most_common(1)[0][0] if exprs else None,
        stress=_r(_mean(v["stress"] for v in rows)),
    )


# ---------------------------------------------------------------- questions

async def insert_question(session_id, idx, kind, text) -> str:
    qid = str(uuid.uuid4())
    _questions[qid] = {
        "id": qid, "session_id": session_id, "idx": idx, "kind": kind, "text": text,
        "transcript": None, "created_at": _now(), "answer_started_at": None, "answered_at": None,
    }
    return qid


async def mark_answer_started(question_id, ts) -> None:
    if question_id in _questions:
        _questions[question_id]["answer_started_at"] = _utc(ts)


async def mark_answered(question_id, ts, transcript) -> None:
    if question_id in _questions:
        _questions[question_id]["answered_at"] = _utc(ts)
        _questions[question_id]["transcript"] = transcript


async def get_question_stats(question_id) -> QuestionStats:
    q = _questions.get(question_id)
    if q is None:
        log.warning("get_question_stats: unknown question %s", question_id)
        return QuestionStats(question_id=str(question_id), duration_s=0.0, n_samples=0, hr_mean=None,
                             hr_peak=None, hr_delta_pct=None, br_mean=None, focus_mean=None,
                             stress_mean=None, stress_peak=None, peak_offset_s=None)
    start = q["answer_started_at"] or q["created_at"]
    end = q["answered_at"] or _now()
    rows = [v for v in _vitals.get(q["session_id"], [])
            if start <= v["time"] <= end and v["confidence"] >= MIN_CONFIDENCE]
    b = await get_baseline(q["session_id"])
    hr = _mean(v["heart_rate"] for v in rows)
    hrs = [v["heart_rate"] for v in rows if v["heart_rate"] is not None]
    stressed = [v for v in rows if v["stress"] is not None]
    peak = max(stressed, key=lambda v: v["stress"]) if stressed else None
    return QuestionStats(
        question_id=question_id,
        duration_s=round((end - start).total_seconds(), 1),
        n_samples=len(rows),
        hr_mean=_r(hr), hr_peak=max(hrs) if hrs else None,
        hr_delta_pct=_r(delta_pct(hr, b.heart_rate if b else None)),
        br_mean=_r(_mean(v["breathing_rate"] for v in rows)),
        focus_mean=_r(_mean(v["focus"] for v in rows), 3),
        stress_mean=_r(_mean(v["stress"] for v in rows)),
        stress_peak=peak["stress"] if peak else None,
        peak_offset_s=round((peak["time"] - start).total_seconds(), 1) if peak else None,
    )


async def get_session_history(session_id) -> list[dict]:
    qs = sorted((q for q in _questions.values() if q["session_id"] == session_id),
                key=lambda q: (q["idx"], q["created_at"]))
    return [
        {"question_id": q["id"], "idx": q["idx"], "kind": q["kind"], "text": q["text"],
         "transcript": q["transcript"], "stats": await get_question_stats(q["id"])}
        for q in qs
    ]


# ---------------------------------------------------------------- report

async def save_report(session_id, report: dict) -> None:
    if session_id in _sessions:
        _sessions[session_id]["report"] = report


async def get_report(session_id) -> Optional[dict]:
    s = _sessions.get(session_id)
    return s["report"] if s else None


# ---------------------------------------------------------------- replay

def _iso(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def get_replay(session_id) -> Optional[dict]:
    """Same shape as queries.get_replay, bucketed in memory like time_bucket('5 seconds')."""
    s = _sessions.get(session_id)
    if s is None:
        return None
    buckets: dict[datetime, list[dict]] = defaultdict(list)
    for v in _vitals.get(session_id, []):
        ts = v["time"]
        buckets[ts - timedelta(seconds=ts.timestamp() % 5)].append(v)
    series = []
    for bucket in sorted(buckets):
        rows = buckets[bucket]
        stresses = [v["stress"] for v in rows if v["stress"] is not None]
        series.append({"t": _iso(bucket), "hr": _r(_mean(v["heart_rate"] for v in rows)),
                       "br": _r(_mean(v["breathing_rate"] for v in rows)),
                       "focus": _r(_mean(v["focus"] for v in rows), 3), "stress": _r(_mean(stresses)),
                       "stress_peak": max(stresses) if stresses else None})
    b = s["baseline"]
    qs = sorted((q for q in _questions.values() if q["session_id"] == session_id),
                key=lambda q: (q["idx"], q["created_at"]))
    return {
        "session": {"id": session_id, "user_name": s.get("user_name"), "started_at": _iso(s["started_at"]),
                    "ended_at": _iso(s["ended_at"]),
                    "baseline": None if b is None else {"heart_rate": _r(b.heart_rate),
                                                        "breathing_rate": _r(b.breathing_rate),
                                                        "focus": _r(b.focus, 3)}},
        "series": series,
        "questions": [{"id": q["id"], "idx": q["idx"], "kind": q["kind"], "text": q["text"],
                       "transcript": q["transcript"], "answer_started_at": _iso(q["answer_started_at"]),
                       "answered_at": _iso(q["answered_at"]),
                       "stats": (await get_question_stats(q["id"])).model_dump()} for q in qs],
    }
