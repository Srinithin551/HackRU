"""Every function Brain and Sensing call on Data (PLAN.md section 3), backed by Tiger Data.

The signatures are a promise to the team: same names, same parameters, same
return types. Do not rename or reshape them without telling everyone.

Each function runs against the in-memory fake_queries.py instead when
db.use_fake() is on: DATA_FAKE=1, no TIGER_DATABASE_URL, or under pytest unless
DATA_FAKE=0 (so nobody's tests write into the shared database).

Every vitals query filters on session_id and a time range, so it is an index
range scan on vitals_session_time (session_id, time DESC).
"""
import asyncio
import functools
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from backend.contracts import Baseline, QuestionStats, VitalsReading, WindowStats
from backend.data import db, fake_queries
from backend.data.metrics import MIN_CONFIDENCE, delta_pct, stress_score

log = logging.getLogger("data.queries")

FAKE = db.use_fake()
if FAKE:
    log.warning("Data is running IN MEMORY (fake_queries.py): nothing is saved to Tiger Data")

WARMUP_S = 10                 # Presage warm-up skipped at the start of calibration
MIN_BASELINE_SAMPLES = 10
DEFAULT_BASELINE = {"heart_rate": 75.0, "breathing_rate": 15.0, "focus": 0.75}   # population defaults
ACTIVE_CACHE_S = 2.0
BASELINE_MISS_TTL_S = 10.0    # before calibration ends, re-check the DB for a baseline at most this often

_baselines: dict[str, Baseline] = {}            # session_id -> latest baseline (in-memory cache)
_baseline_misses: dict[str, float] = {}         # session_id -> monotonic time we last found none
_active: tuple[float, str | None] = (0.0, None)  # (cache expiry, active session id)


def _contract(fn):
    """Route a PLAN.md function to fake_queries when FAKE is on."""
    @functools.wraps(fn)
    async def call(*args, **kwargs):
        if FAKE:
            return await getattr(fake_queries, fn.__name__)(*args, **kwargs)
        return await fn(*args, **kwargs)
    return call


def _utc(ts) -> datetime:
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _uuid(value) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _r(x, digits: int = 1):
    return round(x, digits) if x is not None else None


def _reset_caches() -> None:
    """For tests and scripts that switch sessions under the same process."""
    global _active
    _baselines.clear()
    _baseline_misses.clear()
    _active = (0.0, None)


# ---------------------------------------------------------------- sessions

@_contract
async def create_session(user_name: str, interview_type: str, role_title: str) -> str:
    global _active
    pool = await db.get_pool()
    sid = str(await pool.fetchval(
        "INSERT INTO sessions (user_name, interview_type, role_title) VALUES ($1, $2, $3) RETURNING id",
        user_name, interview_type, role_title))
    _active = (time.monotonic() + ACTIVE_CACHE_S, sid)     # the newest live session is the active one
    return sid


@_contract
async def get_active_session() -> str | None:
    """Most recent session with ended_at IS NULL started in the last 30 minutes. Cached 2 s."""
    global _active
    expires, sid = _active
    if time.monotonic() < expires:
        return sid
    pool = await db.get_pool()
    row = await pool.fetchval(
        "SELECT id FROM sessions WHERE ended_at IS NULL AND started_at > now() - interval '30 minutes' "
        "ORDER BY started_at DESC LIMIT 1")
    sid = str(row) if row else None
    _active = (time.monotonic() + ACTIVE_CACHE_S, sid)
    return sid


@_contract
async def end_session(session_id: str) -> None:
    global _active
    sid = _uuid(session_id)
    if sid is None:
        return
    pool = await db.get_pool()
    row = await pool.fetchrow("UPDATE sessions SET ended_at = now() WHERE id = $1 AND ended_at IS NULL "
                              "RETURNING started_at, ended_at", sid)
    _active = (0.0, None)
    if row is not None:
        # Materialize the whole session in vitals_5s now, so the replay is exact immediately
        # (incl. the stress backfilled after calibration) instead of after the next 30 s refresh.
        try:
            await pool.execute("CALL refresh_continuous_aggregate('vitals_5s', $1::timestamptz, $2::timestamptz)",
                               row["started_at"] - timedelta(seconds=5), row["ended_at"] + timedelta(seconds=5))
        except Exception:
            log.warning("end_session: vitals_5s refresh failed; the 30 s policy will catch up", exc_info=True)


# ---------------------------------------------------------------- vitals

INSERT_VITALS = """
INSERT INTO vitals (time, session_id, heart_rate, breathing_rate, focus, expression, confidence, stress)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""


async def _load_baseline(session_id: str) -> Baseline | None:
    sid = _uuid(session_id)
    if sid is None:
        return None
    pool = await db.get_pool()
    row = await pool.fetchrow(
        "SELECT baseline_hr, baseline_br, baseline_focus, baseline_n FROM sessions WHERE id = $1", sid)
    if row is None or row["baseline_hr"] is None:
        return None
    return Baseline(heart_rate=row["baseline_hr"], breathing_rate=row["baseline_br"],
                    focus=row["baseline_focus"], n_samples=row["baseline_n"] or 0)


async def _cached_baseline(session_id: str) -> Baseline | None:
    """Baseline from the in-memory cache, so insert_vitals doesn't hit the DB every second."""
    if session_id in _baselines:
        return _baselines[session_id]
    missed = _baseline_misses.get(session_id)
    if missed is not None and time.monotonic() - missed < BASELINE_MISS_TTL_S:
        return None
    b = await _load_baseline(session_id)
    if b is None:
        _baseline_misses[session_id] = time.monotonic()
    else:
        _baselines[session_id] = b
    return b


@_contract
async def insert_vitals(readings: list[VitalsReading]) -> list[dict]:
    """Store readings (stress computed per row) and return them as dicts for broadcasting.

    Readings without a session_id go to the active session; with no active session they're dropped.
    """
    active, rows, records = None, [], []
    for r in readings:
        sid = _uuid(r.session_id) if r.session_id else None
        if sid is None:
            active = active or await get_active_session()
            sid = _uuid(active) if active else None
        if sid is None:
            continue
        stress = _r(stress_score(r, await _cached_baseline(str(sid))))
        row = {"time": _utc(r.ts), "session_id": str(sid), "heart_rate": r.heart_rate,
               "breathing_rate": r.breathing_rate, "focus": r.focus, "expression": r.expression,
               "confidence": r.confidence, "stress": stress}
        rows.append(row)
        records.append((row["time"], sid, r.heart_rate, r.breathing_rate, r.focus, r.expression,
                        r.confidence, stress))
    if len(rows) < len(readings):
        log.debug("insert_vitals: dropped %d reading(s) with no active session", len(readings) - len(rows))
    if records:
        pool = await db.get_pool()
        await pool.executemany(INSERT_VITALS, records)
    return rows


BASELINE_SQL = """
SELECT count(*) AS n, avg(heart_rate) AS hr, avg(breathing_rate) AS br, avg(focus) AS focus
FROM vitals
WHERE session_id = $1
  AND confidence >= 0.5
  AND time <= now()
  AND time >= (SELECT greatest(s.started_at, (SELECT min(time) FROM vitals WHERE session_id = $1))
               FROM sessions s WHERE s.id = $1) + make_interval(secs => $2)
"""

BACKFILL_SQL = """
UPDATE vitals v SET stress = u.stress
FROM unnest($2::timestamptz[], $3::real[]) AS u(time, stress)
WHERE v.session_id = $1 AND v.time = u.time AND v.stress IS NULL
"""


@_contract
async def compute_baseline(session_id: str) -> Baseline:
    """Calm baseline from calibration: readings with confidence >= 0.5 from session start (after
    the first WARMUP_S seconds of readings) until now. Fewer than 10 samples -> population defaults.
    Stored on sessions, cached, and stress is backfilled for the rows that came in before it."""
    sid = _uuid(session_id)
    pool = await db.get_pool()
    row = await pool.fetchrow(BASELINE_SQL, sid, float(WARMUP_S))
    n = row["n"] if row else 0
    if n < MIN_BASELINE_SAMPLES:
        log.warning("baseline: only %d good samples for %s, using population defaults", n, session_id)
        b = Baseline(**DEFAULT_BASELINE, n_samples=0)
    else:
        b = Baseline(heart_rate=row["hr"] or DEFAULT_BASELINE["heart_rate"],
                     breathing_rate=row["br"] or DEFAULT_BASELINE["breathing_rate"],
                     focus=row["focus"] if row["focus"] is not None else DEFAULT_BASELINE["focus"],
                     n_samples=n)
    await pool.execute(
        "UPDATE sessions SET baseline_hr = $2, baseline_br = $3, baseline_focus = $4, baseline_n = $5 "
        "WHERE id = $1", sid, b.heart_rate, b.breathing_rate, b.focus, b.n_samples)
    _baselines[str(sid)] = b               # cache first, so rows inserted from now on get stress
    _baseline_misses.pop(str(sid), None)

    # Backfill: the calibration rows were stored with stress NULL (no baseline yet).
    pending = await pool.fetch(
        "SELECT time, heart_rate, breathing_rate, focus, confidence FROM vitals "
        "WHERE session_id = $1 AND stress IS NULL AND confidence >= 0.5", sid)
    times, scores = [], []
    for p in pending:
        s = stress_score(VitalsReading(ts=p["time"], heart_rate=p["heart_rate"],
                                       breathing_rate=p["breathing_rate"], focus=p["focus"],
                                       confidence=p["confidence"]), b)
        if s is not None:
            times.append(p["time"])
            scores.append(round(s, 1))
    if times:
        await pool.execute(BACKFILL_SQL, sid, times, scores)
    log.info("baseline for %s: HR %.1f BR %.1f focus %.2f from %d samples; backfilled %d rows",
             session_id, b.heart_rate, b.breathing_rate, b.focus, b.n_samples, len(times))
    return b


@_contract
async def get_baseline(session_id: str) -> Baseline | None:
    if str(session_id) in _baselines:
        return _baselines[str(session_id)]
    b = await _load_baseline(session_id)
    if b is not None:
        _baselines[str(session_id)] = b
    return b


WINDOW_SQL = """
SELECT count(*) AS n, avg(heart_rate) AS hr, avg(breathing_rate) AS br, avg(focus) AS focus,
       avg(stress) AS stress, mode() WITHIN GROUP (ORDER BY expression) AS expression
FROM vitals
WHERE session_id = $1 AND time > now() - make_interval(secs => $2) AND confidence >= 0.5
"""


@_contract
async def get_window_stats(session_id: str, seconds: int = 15) -> WindowStats:
    """'How is the person right now': averages over the last `seconds`, deltas vs the cached baseline."""
    sid = _uuid(session_id)
    row = None
    if sid is not None:
        pool = await db.get_pool()
        row = await pool.fetchrow(WINDOW_SQL, sid, float(seconds))
    if row is None or row["n"] == 0:
        return WindowStats(seconds=seconds, n_samples=0, hr_mean=None, hr_delta_pct=None, br_mean=None,
                           br_delta_pct=None, focus_mean=None, dominant_expression=None, stress=None)
    b = await _cached_baseline(str(sid))
    return WindowStats(
        seconds=seconds, n_samples=row["n"],
        hr_mean=_r(row["hr"]), hr_delta_pct=_r(delta_pct(row["hr"], b.heart_rate if b else None)),
        br_mean=_r(row["br"]), br_delta_pct=_r(delta_pct(row["br"], b.breathing_rate if b else None)),
        focus_mean=_r(row["focus"], 3), dominant_expression=row["expression"], stress=_r(row["stress"]))


# ---------------------------------------------------------------- questions

@_contract
async def insert_question(session_id: str, idx: int, kind: str, text: str) -> str:
    pool = await db.get_pool()
    return str(await pool.fetchval(
        "INSERT INTO questions (session_id, idx, kind, text) VALUES ($1, $2, $3, $4) RETURNING id",
        _uuid(session_id), idx, kind, text))


@_contract
async def mark_answer_started(question_id: str, ts: datetime) -> None:
    pool = await db.get_pool()
    await pool.execute("UPDATE questions SET answer_started_at = $2 WHERE id = $1",
                       _uuid(question_id), _utc(ts))


@_contract
async def mark_answered(question_id: str, ts: datetime, transcript: str) -> None:
    pool = await db.get_pool()
    await pool.execute("UPDATE questions SET answered_at = $2, transcript = $3 WHERE id = $1",
                       _uuid(question_id), _utc(ts), transcript)


# The time-range join between question events and the vitals hypertable (data.md), written as a
# LATERAL subquery so a question with no usable vitals still returns one row (n = 0) instead of none.
# Answer window: answer_started_at (or created_at if the answer never started) to answered_at (or now).
QUESTION_STATS_SQL = """
SELECT q.id, q.session_id, q.idx, q.kind, q.text, q.transcript,
       q.created_at, q.answer_started_at, q.answered_at,
       COALESCE(q.answer_started_at, q.created_at) AS t_start,
       COALESCE(q.answered_at, now())              AS t_end,
       st.*
FROM questions q
LEFT JOIN LATERAL (
    SELECT count(*)            AS n,
           avg(v.heart_rate)   AS hr_mean,
           max(v.heart_rate)   AS hr_peak,
           avg(v.breathing_rate) AS br_mean,
           avg(v.focus)        AS focus_mean,
           avg(v.stress)       AS stress_mean,
           max(v.stress)       AS stress_peak,
           (array_agg(v.time ORDER BY v.stress DESC NULLS LAST))[1] AS peak_time
    FROM vitals v
    WHERE v.session_id = q.session_id
      AND v.time BETWEEN COALESCE(q.answer_started_at, q.created_at) AND COALESCE(q.answered_at, now())
      AND v.confidence >= 0.5
) st ON true
"""


def _question_stats(row, b: Baseline | None) -> QuestionStats:
    start, end = row["t_start"], row["t_end"]
    peak = row["peak_time"] if row["stress_peak"] is not None else None
    return QuestionStats(
        question_id=str(row["id"]),
        duration_s=round(max(0.0, (end - start).total_seconds()), 1),
        n_samples=row["n"] or 0,
        hr_mean=_r(row["hr_mean"]), hr_peak=_r(row["hr_peak"]),
        hr_delta_pct=_r(delta_pct(row["hr_mean"], b.heart_rate if b else None)),
        br_mean=_r(row["br_mean"]), focus_mean=_r(row["focus_mean"], 3),
        stress_mean=_r(row["stress_mean"]), stress_peak=_r(row["stress_peak"]),
        peak_offset_s=round((peak - start).total_seconds(), 1) if peak else None)


def _empty_stats(question_id) -> QuestionStats:
    return QuestionStats(question_id=str(question_id), duration_s=0.0, n_samples=0, hr_mean=None,
                         hr_peak=None, hr_delta_pct=None, br_mean=None, focus_mean=None,
                         stress_mean=None, stress_peak=None, peak_offset_s=None)


@_contract
async def get_question_stats(question_id: str) -> QuestionStats:
    """'How did they react to this question': vitals during the answer window."""
    qid = _uuid(question_id)
    row = None
    if qid is not None:
        pool = await db.get_pool()
        row = await pool.fetchrow(QUESTION_STATS_SQL + "WHERE q.id = $1", qid)
    if row is None:
        log.warning("get_question_stats: unknown question %s", question_id)
        return _empty_stats(question_id)
    return _question_stats(row, await get_baseline(str(row["session_id"])))


async def _question_rows(session_id: str):
    sid = _uuid(session_id)
    if sid is None:
        return [], None
    pool = await db.get_pool()
    rows = await pool.fetch(QUESTION_STATS_SQL + "WHERE q.session_id = $1 ORDER BY q.idx, q.created_at", sid)
    return rows, await get_baseline(str(sid))


@_contract
async def get_session_history(session_id: str) -> list[dict]:
    """[{question_id, idx, kind, text, transcript, stats: QuestionStats}] in order, for Gemini."""
    rows, b = await _question_rows(session_id)
    return [{"question_id": str(r["id"]), "idx": r["idx"], "kind": r["kind"], "text": r["text"],
             "transcript": r["transcript"], "stats": _question_stats(r, b)} for r in rows]


# ---------------------------------------------------------------- report

@_contract
async def save_report(session_id: str, report: dict) -> None:
    pool = await db.get_pool()
    await pool.execute("UPDATE sessions SET report = $2 WHERE id = $1", _uuid(session_id), report)


@_contract
async def get_report(session_id: str) -> dict | None:
    sid = _uuid(session_id)
    if sid is None:
        return None
    pool = await db.get_pool()
    return await pool.fetchval("SELECT report FROM sessions WHERE id = $1", sid)


# ---------------------------------------------------------------- replay (GET /api/sessions/{id}/replay)

REPLAY_SESSION_SQL = """
SELECT id, user_name, started_at, ended_at, baseline_hr, baseline_br, baseline_focus, baseline_n
FROM sessions WHERE id = $1
"""
# 5-second rollups from the continuous aggregate (real-time, so the newest rows are included)
REPLAY_SERIES_SQL = """
SELECT bucket, hr, br, focus, stress, stress_peak FROM vitals_5s WHERE session_id = $1 ORDER BY bucket
"""


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def get_replay(session_id: str) -> dict | None:
    """The replay chart payload (data.md shape): session + baseline, vitals_5s series, questions + stats.
    None if the session doesn't exist."""
    if FAKE:
        return await fake_queries.get_replay(session_id)
    sid = _uuid(session_id)
    if sid is None:
        return None
    pool = await db.get_pool()
    s, series, qrows = await asyncio.gather(          # three queries in parallel: one round trip
        pool.fetchrow(REPLAY_SESSION_SQL, sid),
        pool.fetch(REPLAY_SERIES_SQL, sid),
        pool.fetch(QUESTION_STATS_SQL + "WHERE q.session_id = $1 ORDER BY q.idx, q.created_at", sid))
    if s is None:
        return None
    b = None
    if s["baseline_hr"] is not None:
        b = Baseline(heart_rate=s["baseline_hr"], breathing_rate=s["baseline_br"],
                     focus=s["baseline_focus"], n_samples=s["baseline_n"] or 0)
    return {
        "session": {"id": str(s["id"]), "user_name": s["user_name"], "started_at": _iso(s["started_at"]),
                    "ended_at": _iso(s["ended_at"]),
                    "baseline": None if b is None else {"heart_rate": _r(b.heart_rate),
                                                        "breathing_rate": _r(b.breathing_rate),
                                                        "focus": _r(b.focus, 3)}},
        "series": [{"t": _iso(p["bucket"]), "hr": _r(p["hr"]), "br": _r(p["br"]), "focus": _r(p["focus"], 3),
                    "stress": _r(p["stress"]), "stress_peak": _r(p["stress_peak"])} for p in series],
        "questions": [{"id": str(q["id"]), "idx": q["idx"], "kind": q["kind"], "text": q["text"],
                       "transcript": q["transcript"], "answer_started_at": _iso(q["answer_started_at"]),
                       "answered_at": _iso(q["answered_at"]), "stats": _question_stats(q, b).model_dump()}
                      for q in qrows],
    }
