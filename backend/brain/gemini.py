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
MIN_ATTEMPT_S = 1.0       # don't start another model attempt with less time than this left

T = TypeVar("T", bound=BaseModel)

_client = None
_no_thinking_level: set[str] = set()   # models that rejected thinking_level


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


def quality_models() -> list[str]:
    """plan_interview and final_report: the main model first, the fast one as backup."""
    return list(dict.fromkeys(m for m in (config.GEMINI_MODEL, config.GEMINI_FAST_MODEL) if m))


def fast_models() -> list[str]:
    """decide_next: latency matters most, so the fast model first."""
    return list(dict.fromkeys(m for m in (config.GEMINI_FAST_MODEL, config.GEMINI_MODEL) if m))


def _log(call: str, model: str, prompt: str, response: Optional[str], latency_ms: int,
         error: Optional[str] = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(), "call": call, "model": model,
        "latency_ms": latency_ms, "prompt": prompt, "response": response, "error": error,
    }
    try:
        path = config.LOG_DIR / f"gemini-{datetime.now():%Y%m%d}.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        log.exception("could not write gemini log")


def _config(model: str, system: str, schema: type[BaseModel], temperature: float):
    from google.genai import types
    kwargs = dict(
        system_instruction=system,
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=schema,
    )
    level = config.GEMINI_THINKING_LEVEL
    if model not in _no_thinking_level and level in ("minimal", "low", "medium", "high"):
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=types.ThinkingLevel(level.upper()))
    return types.GenerateContentConfig(**kwargs)


async def _attempt(call: str, model: str, system: str, prompt: str, schema: type[T],
                   temperature: float, timeout: float) -> T:
    """One request to one model. Raises on timeout, API error, or a response that doesn't fit the schema."""
    t0 = time.monotonic()
    text = None
    try:
        resp = await asyncio.wait_for(
            _get_client().aio.models.generate_content(
                model=model, contents=prompt, config=_config(model, system, schema, temperature)),
            timeout)
        text = resp.text
        if not text:
            raise ValueError("empty response")
        parsed = schema.model_validate_json(text)
        _log(call, model, prompt, text, int((time.monotonic() - t0) * 1000))
        return parsed
    except Exception as e:
        err = "timeout" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"
        _log(call, model, prompt, text, int((time.monotonic() - t0) * 1000), error=err)
        raise


async def generate(call: str, system: str, prompt: str, schema: type[T], temperature: float,
                   timeout: float, models: Optional[list[str]] = None) -> T:
    """Structured-output call with failover: if a model is overloaded (503), rate-limited (429), or
    errors, the next model gets whatever time is left. Raises if every attempt fails."""
    if not configured():
        raise GeminiUnavailable("GEMINI_API_KEY not set")
    deadline = time.monotonic() + timeout
    last: Optional[Exception] = None
    for model in models or quality_models():
        while True:   # at most twice per model: once more if it rejected thinking_level
            remaining = deadline - time.monotonic()
            if last is not None and remaining < MIN_ATTEMPT_S:   # the first attempt always runs
                raise last
            try:
                return await _attempt(call, model, system, prompt, schema, temperature, remaining)
            except Exception as e:
                last = e
                if model not in _no_thinking_level and "thinking" in str(e).lower():
                    log.warning("%s rejected thinking_level, retrying without it", model)
                    _no_thinking_level.add(model)
                    continue
                log.warning("%s on %s failed (%s), trying the next model", call, model, _short(e))
                break
    raise last or GeminiUnavailable("no Gemini model configured")


def _short(e: Exception) -> str:
    if isinstance(e, asyncio.TimeoutError):
        return "timeout"
    return f"{type(e).__name__}: {str(e)[:120]}"


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
        log.warning("plan_interview fell back to canned questions: %s", _short(e))
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
                           temperature=0.4, timeout=DECIDE_TIMEOUT_S, models=fast_models())
        return d.model_copy(update={
            "spoken_text": _clean_spoken(d.spoken_text),
            "answer_quality": max(1, min(5, d.answer_quality)),
        })
    except Exception as e:
        log.warning("decide_next fell back to 'next': %s", _short(e))
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
        log.warning("final_report fell back to numbers-only report: %s", _short(e))
        return fallback_report(s, items)
