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
import time
from datetime import datetime, timezone
from typing import Optional

from backend import config, ws
from backend.brain import gemini, lines, material, voice
from backend.brain.history import as_baseline, as_question_stats, as_window_stats, normalize_history
from backend.brain.models import Decision
from backend.brain.report import fallback_report
from backend.brain.state import SessionState
from backend.brain.turns import (MAX_COACH_WORDS, MAX_FOLLOWUPS, MAX_REDIRECTS, MAX_TURN_WORDS,
                                 as_planned, asks_something, final_coaching, planned_repeat, spoken)
from backend.brain.watcher import watch_stress
from backend.data import queries as data
from backend.ws import broadcast

log = logging.getLogger("brain.interviewer")

ANSWER_TIMEOUT_S = 120.0     # a dropped tab can't hang the loop
CALIBRATION_MIN_S = 35.0     # ~10s Presage warm-up + ~25s of usable baseline readings
CALIBRATION_MAX_WAIT_S = 20.0  # after quick small talk, wait (quietly) at most this long for the minimum
CLOSING_PAUSE_S = 4.0        # minimum wait after the closing clip starts, before session_ended
CLOSING_SLACK_S = 1.0        # extra margin on top of the clip's own length
MAX_TRANSCRIPT_CHARS = 4000  # a runaway speech-to-text stream can't flood the prompt or the database
FOLLOW_UP_ACTIONS = ("follow_up", "ease_off", "push")
ADVANCING_ACTIONS = ("next", "coach")   # these move on to the next planned question


class EndRequested(Exception):
    """The user clicked end, or the browser went away."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def set_state(s: SessionState, state: str) -> None:
    """Change phase and broadcast it. Repeats are swallowed so the frontend never sees a duplicate."""
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
            transcript = " ".join(str(d.get("transcript") or "").split())[:MAX_TRANSCRIPT_CHARS]
            await data.mark_answered(qid, _utcnow(), transcript)
            # A tab that reconnects from now on must not be re-sent this question: it would replay the
            # audio and reopen the mic for a question that's already answered.
            ws.clear_replay(s.session_id, "question")
            return transcript


# ---------------------------------------------------------------- deciding

def word_limit(action: str) -> int:
    """Longest usable turn for an action: coaching gets more room than an ordinary turn."""
    return MAX_COACH_WORDS if action == "coach" else MAX_TURN_WORDS


def resolve_action(action: str, text: str, followups_on_current: int, redirects_on_current: int,
                   queue_len: int, turns_left: int = 99, answered: bool = True) -> str:
    """Enforce the rules Gemini is asked to follow, so a bad decision can't derail the session.
    `text` is what the action needs to say: the coaching for "coach", the spoken turn otherwise.
    `turns_left`: turns remaining in the budget; a follow-up must leave room for every planned question.
    `answered`: False when nothing was captured (silence is never coached: it's usually the mic)."""
    if not answered and action == "coach":
        action = "next"
    if not spoken(text, word_limit(action)) and action not in ("next", "wrap_up"):
        return "next" if queue_len else "wrap_up"   # nothing usable to say: move on with the planned text
    if action in (*FOLLOW_UP_ACTIONS, "redirect") and not asks_something(text):
        action = "next"   # a follow-up that asks nothing leaves them nothing to answer: move on instead
    if action in FOLLOW_UP_ACTIONS and followups_on_current >= MAX_FOLLOWUPS:
        action = "next"
    elif action == "redirect" and redirects_on_current >= MAX_REDIRECTS:
        action = "next"
    if action in (*FOLLOW_UP_ACTIONS, "redirect") and turns_left <= queue_len:
        action = "next"   # no room: a follow-up now would cost a planned question later
    if action == "wrap_up" and queue_len > 0:
        action = "next"
    if action == "next" and queue_len == 0:
        action = "wrap_up"
    return action   # "coach" with nothing left to ask stays "coach": its feedback becomes the last turn


def out_of_budget(s: SessionState) -> bool:
    """True once the spec's turn or time limit is hit. Checked after each answer, so the question in
    progress always finishes; a session can overrun max_seconds by up to one answer."""
    return s.turns >= s.spec.max_turns or s.elapsed() > s.spec.max_seconds


def record_adaptation(s: SessionState, qid: str, decision: Decision, action: str) -> None:
    """Keep the resolved action for the report and for the next decide_next prompt."""
    s.actions.append(action)
    # Coaching is only recorded if Gemini chose "coach" and the rules kept it (it was actually spoken).
    spoken_coaching = decision.coaching if action == "coach" and decision.action == "coach" else ""
    s.adaptations.append({
        "question_id": qid, "action": action,
        "answer_quality": decision.answer_quality, "rationale": decision.rationale,
        "coaching": spoken_coaching,
    })
    log.info("session %s: decision %s → %s (quality %d) %s", s.session_id, decision.action, action,
             decision.answer_quality, decision.rationale)


# ---------------------------------------------------------------- phases

def spoken_name(meta) -> str:
    """The first name Judge greets them with. None in fake mode, whose clips are pre-recorded word for word."""
    return "" if config.BRAIN_FAKE else meta.first_name


def opening_lines(meta) -> tuple[str, str]:
    """(opener, fallback second small-talk turn) for this session."""
    return lines.calibration_lines(meta.spec.key, meta.asks_for_material, spoken_name(meta))


async def calibrate(s: SessionState) -> None:
    """Small talk while Presage warms up and Data collects the calm baseline. The opener is pre-recorded
    so the session starts instantly; the second turn (only if the baseline needs more time) is written
    by Gemini so it reacts to what they said and asks about their major or field.

    If both answers were quick, calibration would end before Presage has a usable baseline (Data then
    falls back to population averages, so stress is measured against a stranger). So it then waits,
    still "calibrating", until CALIBRATION_MIN_S, but never more than CALIBRATION_MAX_WAIT_S. The live
    page's warm-up ring keeps counting down meanwhile.
    """
    await set_state(s, "calibrating")
    asks_for_material = s.meta.asks_for_material
    line, fallback = opening_lines(s.meta)   # the opener greets them by name
    for turn in range(2):
        qid = str(await data.insert_question(s.session_id, 0, "calibration", line))
        await ask(s, qid, line, "calibration")
        transcript = await wait_answer(s, qid)
        s.exchanges.append({"kind": "calibration", "text": line, "transcript": transcript})
        if turn == 0 and asks_for_material:
            # A presentation with no imported file: plan the questions from what they say it covers.
            s.meta.material = material.from_speech(transcript)
            start_planning(s)
        if turn == 1 or s.elapsed() >= CALIBRATION_MIN_S:
            break
        line = spoken(await gemini.small_talk_follow_up(s) or "") or fallback
    await _top_up_calibration(s)


def start_planning(s: SessionState) -> None:
    """Plan the questions with Gemini in the background (from the imported material, if any). Awaited once
    calibration is over; started only once."""
    if s.plan_task is None:
        s.plan_task = asyncio.create_task(gemini.plan_interview(s.spec, s.meta.role, s.meta.material))


async def _top_up_calibration(s: SessionState) -> None:
    """Wait out the rest of CALIBRATION_MIN_S (capped), checking for end_session every 0.25 s."""
    wait = min(CALIBRATION_MAX_WAIT_S, CALIBRATION_MIN_S - s.elapsed())
    if wait <= 0:
        return
    log.info("session %s: small talk was quick, holding calibration %.0fs more for the baseline", s.session_id, wait)
    loop = asyncio.get_running_loop()
    until = loop.time() + wait
    while loop.time() < until and not s.end_requested:
        await asyncio.sleep(min(0.25, until - loop.time()))


def _next_planned(s: SessionState):
    """Pop the next planned question and reset the per-question allowances."""
    q = s.queue.pop(0)
    s.current_planned = q
    s.planned_number += 1
    s.followups_on_current = s.redirects_on_current = 0
    return q


async def interview(s: SessionState) -> None:
    """Main loop. Returns when the plan is exhausted, the budget runs out, or the user ends."""
    q = _next_planned(s)
    next_text = as_planned(spoken(await gemini.open_interview(s, q) or ""), q.text)   # bridge from the small talk
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

        # Both reads at once: on Tiger Cloud each is a ~60 ms network round trip.
        raw_stats, raw_window = await asyncio.gather(
            data.get_question_stats(qid), data.get_window_stats(s.session_id, 15), return_exceptions=True)
        # A failed stats read must not end the interview: decide without body data for this one turn
        # (decide_next treats None as "no usable vitals"). The report re-reads the stats at the end.
        if isinstance(raw_stats, BaseException):
            log.error("get_question_stats failed (%s), deciding without body data", raw_stats)
            stats = None
        else:
            stats = as_question_stats(raw_stats)
        if isinstance(raw_window, BaseException):
            log.error("get_window_stats failed (%s), using the watcher's last window", raw_window)
            window = s.live_window
        else:
            window = as_window_stats(raw_window)
        decision = await gemini.decide_next(s, kind, next_text, transcript, stats, window)
        needed = decision.coaching if decision.action == "coach" else decision.spoken_text
        action = resolve_action(decision.action, needed, s.followups_on_current, s.redirects_on_current,
                                len(s.queue), turns_left=s.spec.max_turns - s.turns, answered=bool(transcript))
        # A follow-up that is really a planned question would ask it twice (now, then on its own turn).
        # If it's the next one, ask it now as that question, in Gemini's words; if it's a later one, skip
        # the follow-up and ask the next planned question, so the difficulty order holds.
        early = (planned_repeat(decision.spoken_text, [p.text for p in s.queue])
                 if action in FOLLOW_UP_ACTIONS and action == decision.action else None)
        if early is not None:
            action = "next"
        record_adaptation(s, qid, decision, action)
        # Gemini's wording only fits if its action survived the rules; otherwise use the planned text.
        # (Silence turned from coach into next keeps its spoken_text: that's already the next question.)
        kept = (action == decision.action or early == 0
                or (decision.action == "coach" and action == "next" and not transcript))
        turn = spoken(decision.spoken_text) if kept else ""
        coaching = spoken(decision.coaching, MAX_COACH_WORDS) if kept and action == "coach" else ""

        if s.end_requested or action == "wrap_up" or (action == "coach" and not s.queue):
            s.parting_words = final_coaching(coaching) if action == "coach" else turn   # said before the closing line
            return
        if action in ADVANCING_ACTIONS:
            q = _next_planned(s)
            # Moving on must ask the planned question: not a lead-in with no question, not a new question.
            next_text, kind = as_planned(turn, q.text), "main"
        else:
            if action == "redirect":
                s.redirects_on_current += 1
            else:
                s.followups_on_current += 1
            next_text, kind = turn, "follow_up"


async def build_and_save_report(s: SessionState) -> dict:
    """Pull every question + its vitals stats from Data, write the report, save it. Returns the dict."""
    items =normalize_history(await data.get_session_history(s.session_id))
    report = await gemini.final_report(s, items)
    s.report = report.model_dump()   # kept in memory too: GET /report still works if the save fails
    try:
        await data.save_report(s.session_id, s.report)
    except Exception:
        log.exception("save_report failed for %s; serving the report from memory", s.session_id)
    return s.report


async def finish(s: SessionState) -> None:
    """End the session. The browser always gets session_ended, even if the database is unreachable."""
    try:
        await data.end_session(s.session_id)
    except Exception:
        log.exception("end_session failed for %s", s.session_id)
    s.finished_at = time.monotonic()   # state.prune_finished() frees it a while after this
    await set_state(s, "done")
    await broadcast(s.session_id, "session_ended", {
        "report_url": f"/api/sessions/{s.session_id}/report",
        "replay_url": f"/api/sessions/{s.session_id}/replay",
    })


async def _run(s: SessionState) -> None:
    """The whole session in order:

    1. Start planning questions with Gemini in the background (it has all of calibration to finish). For a
       presentation with no imported file, planning starts after the first answer instead, which says what
       the presentation covers.
    2. Small talk (calibration), then compute the baseline from those readings.
    3. Start the stress watcher (coach flags) and run the question loop.
    4. The closing line plays while the report is written, then finish() sends session_ended.

    end_session at any point skips straight to step 4, without the closing line.
    """
    if not s.meta.asks_for_material:
        start_planning(s)   # plans during calibration
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
            start_planning(s)                      # no-op unless calibration ended before it could start
            plan = await s.plan_task
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
        if s.plan_task is not None:
            s.plan_task.cancel()

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
    """Entry point of the background task routes.start_session creates. Never raises except on cancel."""
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
    """Best-effort cleanup after a crash: numbers-only report, then session_ended. Each step is guarded
    on its own so one failure (e.g. the database is down) doesn't skip the rest."""
    try:
        items = normalize_history(await data.get_session_history(s.session_id))
    except Exception:
        log.exception("get_session_history failed during emergency finish")
        items = []
    if s.report is None:
        s.report = fallback_report(s, items).model_dump()
    try:
        await data.save_report(s.session_id, s.report)
    except Exception:
        log.exception("save_report failed during emergency finish")
    try:
        await finish(s)
    except Exception:
        log.exception("could not finish session %s cleanly", s.session_id)
