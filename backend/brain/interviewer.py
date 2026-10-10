"""The interview loop: calibration → planned questions with adaptive follow-ups → closing → report.

The interviewer holds a conversation rather than reading a list: Gemini writes every turn after the
small talk (a short reaction to what the candidate said, then the next question). A wrong, off-topic,
or inappropriate answer gets immediate coaching, said outside the interview frame (what would happen
in a real interview, what to say instead), before the next question.

Exactly one clip plays per turn and the next one is only sent after the candidate answers, so the
interviewer's voice can never overlap itself.

One `run_session` task per session, started by POST /api/sessions/{id}/start. It talks to the
browser only through ws.broadcast() / ws.inbox and to the database only through data.queries.
If anything inside crashes, the session still ends cleanly with a numbers-only report.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from backend import ws
from backend.brain import gemini, lines, voice
from backend.brain.history import as_baseline, as_question_stats, as_window_stats, normalize_history
from backend.brain.models import Decision
from backend.brain.report import fallback_report
from backend.brain.state import SessionState
from backend.brain.turns import MAX_COACH_WORDS, MAX_TURN_WORDS, final_coaching, spoken
from backend.brain.watcher import watch_stress
from backend.data import queries as data
from backend.ws import broadcast

log = logging.getLogger("brain.interviewer")

ANSWER_TIMEOUT_S = 120.0     # a dropped tab can't hang the loop
CALIBRATION_MIN_S = 35.0     # ~10s Presage warm-up + ~25s of usable baseline readings
CLOSING_PAUSE_S = 4.0        # minimum wait after the closing clip starts, before session_ended
CLOSING_SLACK_S = 1.0        # extra margin on top of the clip's own length
MAX_FOLLOWUPS = 1            # ease_off / push / follow_up, per planned question
MAX_REDIRECTS = 1            # "didn't catch that" re-asks for garbled answers, per planned question
FOLLOW_UP_ACTIONS = ("follow_up", "ease_off", "push")
ADVANCING_ACTIONS = ("next", "coach")   # these move on to the next planned question


class EndRequested(Exception):
    """The user clicked end, or the browser went away."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def set_state(s: SessionState, state: str) -> None:
    if s.phase == state:
        return
    s.phase = state
    await broadcast(s.session_id, "state", {"state": state})


# ---------------------------------------------------------------- speaking

async def ask(s: SessionState, qid: str, text: str, kind: str, coaching: str = "") -> None:
    """tts → `question` event → listening. Coaching on the previous answer, if any, is spoken first in the
    same clip (one voice, no gap) but sent as its own field so the screen can show the question cleanly."""
    s.current_qid, s.current_text, s.current_kind = qid, text, kind
    calibrating = kind == "calibration"   # calibration stays "calibrating" so the overlay doesn't flicker
    if not calibrating:
        await set_state(s, "asking")
    audio_url = await voice.tts(f"{coaching} {text}" if coaching else text)
    await broadcast(s.session_id, "question", {
        "question_id": qid,
        "index": s.planned_number,       # 0 during calibration, then 1..total (follow-ups keep their parent's number)
        "total": s.total_planned,
        "kind": kind,
        "text": text,                    # the question (with a short lead-in); never the coaching
        "coaching": coaching or None,    # stepped-out feedback on the previous answer, spoken before the question
        "audio_url": audio_url,
    })
    if not calibrating:
        await set_state(s, "listening")


async def say(s: SessionState, text: str) -> float:
    """Speech that isn't a question (the closing line), sent as a `filler` event so the question on
    screen doesn't change. Only sent after the last answer, so it can't overlap a question clip.
    Returns how long the clip plays, in seconds (0 if there's no audio)."""
    url = await voice.tts(text)
    if url:
        await broadcast(s.session_id, "filler", {"audio_url": url})
    return voice.clip_seconds(url)


# ---------------------------------------------------------------- listening

async def wait_answer(s: SessionState, qid: str) -> str:
    """Consume answer_started / answer_done for this question from the inbox. Returns the transcript.

    Raises EndRequested if the user ends the session (or the tab is gone when the timeout hits).
    """
    inbox = ws.get_inbox(s.session_id)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ANSWER_TIMEOUT_S
    started = False

    while True:
        if s.end_requested:
            await data.mark_answered(qid, _utcnow(), "")
            raise EndRequested()
        remaining = deadline - loop.time()
        if remaining <= 0:
            await data.mark_answered(qid, _utcnow(), "")
            if not ws.connections.get(s.session_id):
                log.warning("session %s: answer timed out and no client is connected, ending", s.session_id)
                s.end_requested = True
                raise EndRequested()
            log.warning("session %s: answer to %s timed out, moving on", s.session_id, qid)
            return ""
        try:
            msg = await asyncio.wait_for(inbox.get(), min(remaining, 1.0))
        except asyncio.TimeoutError:
            continue

        type_, d = msg.get("type"), msg.get("data") or {}
        if type_ == "end_session":
            s.end_requested = True
            continue
        if d.get("question_id") not in (None, qid):
            continue   # stale event for an earlier question
        if type_ == "answer_started" and not started:
            started = True
            await data.mark_answer_started(qid, _utcnow())
            deadline = loop.time() + ANSWER_TIMEOUT_S   # full answer time from when they start talking
        elif type_ == "answer_done":
            transcript = " ".join(str(d.get("transcript") or "").split())
            await data.mark_answered(qid, _utcnow(), transcript)
            return transcript


# ---------------------------------------------------------------- deciding

def word_limit(action: str) -> int:
    return MAX_COACH_WORDS if action == "coach" else MAX_TURN_WORDS


def resolve_action(action: str, text: str, followups_on_current: int, redirects_on_current: int,
                   queue_len: int) -> str:
    """Enforce the rules Gemini is asked to follow, so a bad decision can't derail the session.
    `text` is what the action needs to say: the coaching for "coach", the spoken turn otherwise."""
    if not spoken(text, word_limit(action)) and action not in ("next", "wrap_up"):
        return "next" if queue_len else "wrap_up"   # nothing usable to say: move on with the planned text
    if action in FOLLOW_UP_ACTIONS and followups_on_current >= MAX_FOLLOWUPS:
        action = "next"
    elif action == "redirect" and redirects_on_current >= MAX_REDIRECTS:
        action = "next"
    if action == "wrap_up" and queue_len > 0:
        action = "next"
    if action == "next" and queue_len == 0:
        action = "wrap_up"
    return action   # "coach" with nothing left to ask stays "coach": its feedback becomes the last turn


def out_of_budget(s: SessionState) -> bool:
    return s.turns >= s.spec.max_turns or s.elapsed() > s.spec.max_seconds


def record_adaptation(s: SessionState, qid: str, decision: Decision, action: str) -> None:
    s.actions.append(action)
    s.adaptations.append({
        "question_id": qid, "action": action,
        "answer_quality": decision.answer_quality, "rationale": decision.rationale,
        "coaching": decision.coaching if action == "coach" == decision.action else "",
    })
    log.info("session %s: decision %s → %s (quality %d) %s", s.session_id, decision.action, action,
             decision.answer_quality, decision.rationale)


# ---------------------------------------------------------------- phases

async def calibrate(s: SessionState) -> None:
    """Small talk while Presage warms up and Data collects the calm baseline. The opener is pre-recorded
    so the session starts instantly; the second turn (only if the baseline needs more time) is written
    by Gemini so it reacts to what they said and asks about their major or field."""
    await set_state(s, "calibrating")
    line, fallback = lines.calibration_lines(s.spec.key)
    for turn in range(2):
        qid = str(await data.insert_question(s.session_id, 0, "calibration", line))
        await ask(s, qid, line, "calibration")
        transcript = await wait_answer(s, qid)
        s.exchanges.append({"kind": "calibration", "text": line, "transcript": transcript})
        if turn == 1 or s.elapsed() >= CALIBRATION_MIN_S:
            break
        line = spoken(await gemini.small_talk_follow_up(s) or "") or fallback


def _next_planned(s: SessionState):
    q = s.queue.pop(0)
    s.current_planned = q
    s.planned_number += 1
    s.followups_on_current = s.redirects_on_current = 0
    return q


async def interview(s: SessionState) -> None:
    """Main loop. Returns when the plan is exhausted, the budget runs out, or the user ends."""
    q = _next_planned(s)
    next_text = spoken(await gemini.open_interview(s, q) or "") or q.text   # bridge from the small talk
    kind, coaching = "main", ""

    while True:
        s.idx += 1
        s.turns += 1
        qid = str(await data.insert_question(s.session_id, s.idx, kind, next_text))
        await ask(s, qid, next_text, kind, coaching)

        transcript = await wait_answer(s, qid)
        s.exchanges.append({"kind": kind, "text": next_text, "transcript": transcript})
        await set_state(s, "thinking")       # no filler clip: one voice at a time, nothing scripted
        if s.end_requested or out_of_budget(s):
            return

        stats = as_question_stats(await data.get_question_stats(qid))
        try:
            window = as_window_stats(await data.get_window_stats(s.session_id, 15))
        except Exception:
            log.exception("get_window_stats failed, using the watcher's last window")
            window = s.live_window
        decision = await gemini.decide_next(s, kind, next_text, transcript, stats, window)
        needed = decision.coaching if decision.action == "coach" else decision.spoken_text
        action = resolve_action(decision.action, needed, s.followups_on_current, s.redirects_on_current, len(s.queue))
        record_adaptation(s, qid, decision, action)
        # Gemini's wording only fits if its action survived the rules; otherwise use the planned text.
        kept = action == decision.action
        turn = spoken(decision.spoken_text) if kept else ""
        coaching = spoken(decision.coaching, MAX_COACH_WORDS) if kept and action == "coach" else ""

        if s.end_requested or action == "wrap_up" or (action == "coach" and not s.queue):
            s.parting_words = final_coaching(coaching) if action == "coach" else turn   # said before the closing line
            return
        if action in ADVANCING_ACTIONS:
            q = _next_planned(s)
            next_text, kind = turn or q.text, "main"
        else:
            if action == "redirect":
                s.redirects_on_current += 1
            else:
                s.followups_on_current += 1
            next_text, kind = turn, "follow_up"


async def build_and_save_report(s: SessionState) -> dict:
    items = normalize_history(await data.get_session_history(s.session_id))
    report = await gemini.final_report(s, items)
    payload = report.model_dump()
    await data.save_report(s.session_id, payload)
    return payload


async def finish(s: SessionState) -> None:
    await data.end_session(s.session_id)
    await set_state(s, "done")
    await broadcast(s.session_id, "session_ended", {
        "report_url": f"/api/sessions/{s.session_id}/report",
        "replay_url": f"/api/sessions/{s.session_id}/replay",
    })


async def _run(s: SessionState) -> None:
    plan_task = asyncio.create_task(gemini.plan_interview(s.spec, s.meta.role))   # plans during calibration
    watcher: Optional[asyncio.Task] = None
    try:
        try:
            await calibrate(s)
        except EndRequested:
            pass
        try:
            s.baseline = as_baseline(await data.compute_baseline(s.session_id))
        except Exception:
            log.exception("compute_baseline failed for %s", s.session_id)

        if not s.end_requested:
            await set_state(s, "thinking")         # while the bridge into the first question is written
            plan = await plan_task
            s.queue = list(plan)
            s.total_planned = len(plan)
            watcher = asyncio.create_task(watch_stress(s))   # coach flags from here on
            try:
                await interview(s)
            except EndRequested:
                pass
    finally:
        if watcher:
            watcher.cancel()
        plan_task.cancel()

    # Wrap up: closing line plays while the report is written.
    await set_state(s, "thinking")
    report_task = asyncio.create_task(build_and_save_report(s))
    if not s.end_requested:
        # One clip, so the reaction to the last answer and the closing line can't overlap in the browser.
        closing = f"{s.parting_words} {lines.CLOSING_LINE}" if s.parting_words else lines.CLOSING_LINE
        # The frontend leaves the page on session_ended, so wait until the clip has finished playing.
        seconds = await say(s, closing)
        await asyncio.sleep(max(CLOSING_PAUSE_S, seconds + CLOSING_SLACK_S) if seconds else CLOSING_PAUSE_S)
    await report_task
    await finish(s)


async def run_session(s: SessionState) -> None:
    log.info("session %s: starting (%s, role=%r)", s.session_id, s.spec.label, s.meta.role)
    try:
        await _run(s)
        log.info("session %s: finished after %d turns in %.0fs", s.session_id, s.turns, s.elapsed())
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("session %s: interview loop crashed, ending with a fallback report", s.session_id)
        await _emergency_finish(s)


async def _emergency_finish(s: SessionState) -> None:
    try:
        items = normalize_history(await data.get_session_history(s.session_id))
    except Exception:
        log.exception("get_session_history failed during emergency finish")
        items = []
    try:
        await data.save_report(s.session_id, fallback_report(s, items).model_dump())
    except Exception:
        log.exception("save_report failed during emergency finish")
    try:
        await finish(s)
    except Exception:
        log.exception("could not finish session %s cleanly", s.session_id)
