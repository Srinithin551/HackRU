"""The three Gemini calls. Each one has a timeout and a fallback, so none of them can stall a live session.

Every prompt and response is appended to backend/logs/gemini-YYYYMMDD.jsonl for 3 AM debugging.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional, TypeVar

from pydantic import BaseModel

from backend import config
from backend.brain import fake, prompts
from backend.brain.history import HistoryItem
from backend.brain.models import Decision, InterviewPlan, PlannedQuestion, Report, ReportDraft
from backend.brain.report import fallback_report, finalize_draft
from backend.brain.specs import InterviewSpec, fallback_questions
from backend.brain.state import SessionState
from backend.contracts import QuestionStats, WindowStats

log = logging.getLogger("brain.gemini")

PLAN_TIMEOUT_S = 20.0     # runs during the ~40s calibration, so it has room
DECIDE_TIMEOUT_S = 6.0    # brain.md: fall back to "next" past 6s
REPORT_TIMEOUT_S = 45.0

T = TypeVar("T", bound=BaseModel)

_client = None
_thinking_supported = True


class GeminiUnavailable(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.GEMINI_API_KEY)


def _get_client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def _log(call: str, prompt: str, response: Optional[str], latency_ms: int, error: Optional[str] = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(), "call": call, "model": config.GEMINI_MODEL,
        "latency_ms": latency_ms, "prompt": prompt, "response": response, "error": error,
    }
    try:
        path = config.LOG_DIR / f"gemini-{datetime.now():%Y%m%d}.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        log.exception("could not write gemini log")


def _config(system: str, schema: type[BaseModel], temperature: float):
    from google.genai import types
    kwargs = dict(
        system_instruction=system,
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=schema,
    )
    level = config.GEMINI_THINKING_LEVEL
    if _thinking_supported and level in ("minimal", "low", "medium", "high"):
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=types.ThinkingLevel(level.upper()))
    return types.GenerateContentConfig(**kwargs)


async def generate(call: str, system: str, prompt: str, schema: type[T], temperature: float, timeout: float) -> T:
    """One structured-output call. Raises on timeout, API error, or a response that doesn't fit the schema."""
    global _thinking_supported
    if not configured():
        raise GeminiUnavailable("GEMINI_API_KEY not set")

    t0 = time.monotonic()
    text = None
    try:
        try:
            resp = await asyncio.wait_for(
                _get_client().aio.models.generate_content(
                    model=config.GEMINI_MODEL, contents=prompt,
                    config=_config(system, schema, temperature)),
                timeout)
        except Exception as e:
            # Older or different models reject thinking_level; drop it once and retry for the rest of the run.
            if _thinking_supported and "thinking" in str(e).lower():
                log.warning("model rejected thinking_level, retrying without it: %s", e)
                _thinking_supported = False
                remaining = max(1.0, timeout - (time.monotonic() - t0))
                resp = await asyncio.wait_for(
                    _get_client().aio.models.generate_content(
                        model=config.GEMINI_MODEL, contents=prompt,
                        config=_config(system, schema, temperature)),
                    remaining)
            else:
                raise
        text = resp.text
        if not text:
            raise ValueError("empty response")
        parsed = schema.model_validate_json(text)
        _log(call, prompt, text, int((time.monotonic() - t0) * 1000))
        return parsed
    except Exception as e:
        err = "timeout" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"
        _log(call, prompt, text, int((time.monotonic() - t0) * 1000), error=err)
        raise


def _clean_spoken(text: str) -> str:
    """Strip anything a TTS voice would read out awkwardly."""
    text = " ".join((text or "").replace("*", "").replace("#", "").replace("`", "").split())
    return text.strip().strip('"').strip()


# ---------------------------------------------------------------- call 1

async def plan_interview(spec: InterviewSpec, role: str) -> list[PlannedQuestion]:
    """Never raises. Falls back to the canned bank for this interview type."""
    if config.BRAIN_FAKE:
        return fake.plan(spec)
    try:
        plan = await generate("plan_interview", prompts.PLAN_SYSTEM, prompts.plan_prompt(spec, role),
                              InterviewPlan, temperature=0.8, timeout=PLAN_TIMEOUT_S)
        questions = []
        for q in plan.questions:
            text = _clean_spoken(q.text)
            if text:
                questions.append(PlannedQuestion(text=text, topic=q.topic.strip() or "general", difficulty=q.difficulty))
        questions = questions[:spec.n_questions]
    except Exception as e:
        log.warning("plan_interview fell back to canned questions: %s", e)
        questions = []

    # Pad if Gemini returned too few, and pin difficulties to the planned curve.
    backup = fallback_questions(spec)
    while len(questions) < spec.n_questions:
        questions.append(backup[len(questions)])
    for q, d in zip(questions, spec.difficulties):
        q.difficulty = d
    return questions


# ---------------------------------------------------------------- call 2

async def decide_next(s: SessionState, kind: str, question: str, transcript: str,
                      stats: Optional[QuestionStats], window: Optional[WindowStats]) -> Decision:
    """Never raises. Falls back to `next` (with the queued question) on timeout or error."""
    if config.BRAIN_FAKE:
        return fake.decide(transcript, stats)
    context = prompts.decide_context(s, kind, question, transcript, stats, window)
    try:
        d = await generate("decide_next", prompts.DECIDE_SYSTEM, context, Decision,
                           temperature=0.4, timeout=DECIDE_TIMEOUT_S)
        return d.model_copy(update={
            "spoken_text": _clean_spoken(d.spoken_text),
            "answer_quality": max(1, min(5, d.answer_quality)),
        })
    except Exception as e:
        log.warning("decide_next fell back to 'next': %s", e)
        return Decision(action="next", spoken_text="", answer_quality=3, rationale=f"fallback: {type(e).__name__}")


# ---------------------------------------------------------------- call 3

async def final_report(s: SessionState, items: list[HistoryItem]) -> Report:
    """Never raises. Falls back to a report built from the numbers alone."""
    if config.BRAIN_FAKE:
        return fallback_report(s, items, generated_by="fake")
    if not any(it.kind != "calibration" for it in items):
        return fallback_report(s, items)
    try:
        draft = await generate("final_report", prompts.REPORT_SYSTEM, prompts.report_context(s, items),
                               ReportDraft, temperature=0.5, timeout=REPORT_TIMEOUT_S)
        return finalize_draft(s, items, draft)
    except Exception as e:
        log.warning("final_report fell back to numbers-only report: %s", e)
        return fallback_report(s, items)
