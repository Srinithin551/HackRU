"""The Gemini calls. Each one has a timeout and a fallback, so none of them can stall a live session.

Every prompt and response is appended to backend/logs/gemini-YYYYMMDD.jsonl for 3 AM debugging.
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional, TypeVar

from pydantic import BaseModel

from backend import config
from backend.brain import fake, prompts
from backend.brain.history import HistoryItem
from backend.brain.material import Material
from backend.brain.models import Decision, DecisionReply, InterviewPlan, OpeningLine, PlannedQuestion, Report, ReportDraft
from backend.brain.report import fallback_report, finalize_draft
from backend.brain.specs import InterviewSpec, fallback_questions
from backend.brain.state import SessionState
from backend.brain.turns import question_part, split_coaching, with_step_markers
from backend.contracts import QuestionStats, WindowStats

log = logging.getLogger("brain.gemini")

PLAN_TIMEOUT_S = 20.0     # runs during the ~40s calibration, so it has room
PLAN_WITH_FILE_TIMEOUT_S = 30.0   # a whole PDF to read first
DECIDE_TIMEOUT_S = 6.0    # brain.md: fall back to "next" past 6s
OPEN_TIMEOUT_S = 5.0      # the small-talk → first-question bridge; falls back to the planned text
SMALL_TALK_TIMEOUT_S = 4.0  # the second small-talk turn; falls back to a fixed line
REPORT_TIMEOUT_S = 45.0
MIN_ATTEMPT_S = 1.0       # don't start another model attempt with less time than this left

T = TypeVar("T", bound=BaseModel)

_client = None
_no_thinking_level: set[str] = set()   # models that rejected thinking_level
_resting_until: dict[str, float] = {}  # model -> monotonic time it may be tried again (quota/overload)

DAILY_QUOTA_REST_S = 3600.0   # a per-day quota is gone for hours; re-check hourly instead of every call
OVERLOAD_REST_S = 20.0        # 503 "high demand" is usually brief


def _rest_for(e: Exception) -> Optional[float]:
    """How long to skip a model after this error, or None if it's not a quota/overload error."""
    msg = str(e)
    if "RESOURCE_EXHAUSTED" in msg or " 429" in f" {msg}":
        if "PerDay" in msg:
            return DAILY_QUOTA_REST_S
        m = re.search(r"retry in ([\d.]+)s", msg)
        return float(m.group(1)) + 1 if m else 60.0
    if "UNAVAILABLE" in msg or " 503" in f" {msg}":
        return OVERLOAD_REST_S
    return None


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
                   temperature: float, timeout: float, attachments: Optional[list] = None) -> T:
    """One request to one model. Raises on timeout, API error, or a response that doesn't fit the schema.
    `attachments` (e.g. a PDF Part) go in front of the prompt."""
    t0 = time.monotonic()
    text = None
    try:
        resp = await asyncio.wait_for(
            _get_client().aio.models.generate_content(
                model=model, contents=[*attachments, prompt] if attachments else prompt,
                config=_config(model, system, schema, temperature)),
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
                   timeout: float, models: Optional[list[str]] = None, attachments: Optional[list] = None) -> T:
    """Structured-output call with failover: if a model is overloaded (503), rate-limited (429), or
    errors, the next model gets whatever time is left. Raises if every attempt fails."""
    if not configured():
        raise GeminiUnavailable("GEMINI_API_KEY not set")
    deadline = time.monotonic() + timeout
    last: Optional[Exception] = None
    candidates = models or quality_models()
    available = [m for m in candidates if _resting_until.get(m, 0) <= time.monotonic()]
    if not available:
        raise GeminiUnavailable(f"every model is resting after quota/overload errors: {', '.join(candidates)}")
    for model in available:
        while True:   # at most twice per model: once more if it rejected thinking_level
            remaining = deadline - time.monotonic()
            if last is not None and remaining < MIN_ATTEMPT_S:   # the first attempt always runs
                raise last
            try:
                return await _attempt(call, model, system, prompt, schema, temperature, remaining, attachments)
            except Exception as e:
                last = e
                if model not in _no_thinking_level and "thinking" in str(e).lower():
                    log.warning("%s rejected thinking_level, retrying without it", model)
                    _no_thinking_level.add(model)
                    continue
                rest = _rest_for(e)
                if rest:
                    _resting_until[model] = time.monotonic() + rest
                    log.warning("%s: %s is rate-limited or overloaded, skipping it for %.0fs", call, model, rest)
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


# Backstop for the prompt's banned-phrase list: stock acknowledgments at the start of a turn.
_STOCK_OPENER = re.compile(
    r"^(?:okay|ok|alright|all right|so|well)?[,\s]*"
    r"(?:got it|i see|i understand|makes sense|that makes sense|interesting|great answer|great question|"
    r"excellent|awesome|absolutely|fair enough|thanks for sharing|thank you for sharing|thank you for that|"
    r"i appreciate that|that's a good point)\s*[.,!:;\u2014-]+\s*",   # only when it stands alone as a clause
    re.IGNORECASE,
)


def strip_stock_openers(text: str) -> str:
    """Remove scripted openers like "Got it. I see." so a turn starts with substance."""
    out = text
    while True:
        trimmed = _STOCK_OPENER.sub("", out, count=1).lstrip()
        if trimmed == out or not trimmed:
            break
        out = trimmed
    return out[:1].upper() + out[1:] if out else text


# "Let us move on" → "Let's move on" (the prompt asks for contractions; this catches the slips).
_LET_US = re.compile(r"\b([Ll])et us (?=(?:move|switch|shift|turn|talk|dig|dive|look|go|get|start|begin|focus|"
                     r"explore|discuss|circle|pivot|step|try|come|zoom|jump|head|continue|wrap|change|consider)\b)")


def _clean_turn(text: str) -> str:
    return _LET_US.sub(r"\1et's ", strip_stock_openers(_clean_spoken(text)))


# ---------------------------------------------------------------- call 1

async def plan_interview(spec: InterviewSpec, role: str, material: Optional[Material] = None) -> list[PlannedQuestion]:
    """Never raises. Falls back to the canned bank for this interview type. With `material` (an imported
    file, or a presentation's spoken description) the questions are drawn from it; a PDF is attached as-is."""
    if config.BRAIN_FAKE:
        return fake.plan(spec)
    attachments = None
    if material is not None and material.pdf is not None:
        from google.genai import types   # imported lazily, like the client
        attachments = [types.Part.from_bytes(data=material.pdf, mime_type="application/pdf")]
    try:
        plan = await generate("plan_interview", prompts.PLAN_SYSTEM, prompts.plan_prompt(spec, role, material),
                              InterviewPlan, temperature=0.8,
                              timeout=PLAN_WITH_FILE_TIMEOUT_S if attachments else PLAN_TIMEOUT_S,
                              attachments=attachments)
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
        return fake.decide(transcript, stats, question=question, kind=kind,
                           next_question=s.queue[0].text if s.queue else None)
    context = prompts.decide_context(s, kind, question, transcript, stats, window)
    try:
        d = await generate("decide_next", prompts.DECIDE_SYSTEM, context, DecisionReply,
                           temperature=0.4, timeout=DECIDE_TIMEOUT_S, models=fast_models())
        text, coaching = _clean_turn(d.spoken_text), _LET_US.sub(r"\1et's ", _clean_spoken(d.coaching))
        if d.action == "coach":
            if coaching:
                text = question_part(text, coaching)    # never say the coaching twice in one clip
            else:
                coaching, text = split_coaching(text)   # it put everything in spoken_text anyway
            # The step out / step back in lines come from a rotating set, not from Gemini (which repeated
            # one wording every time). No step back in when no question follows.
            coaching = with_step_markers(coaching, s.actions.count("coach"), step_in=bool(s.queue))
        return Decision(action=d.action, spoken_text=text, coaching=coaching if d.action == "coach" else "",
                        answer_quality=max(1, min(5, d.answer_quality)), rationale=d.rationale)
    except Exception as e:
        log.warning("decide_next fell back to 'next': %s", _short(e))
        return Decision(action="next", spoken_text="", answer_quality=3, rationale=f"fallback: {type(e).__name__}")


async def small_talk_follow_up(s: SessionState) -> Optional[str]:
    """The second small-talk turn: reacts to the first answer, then asks about their major or field.
    None means "use the fixed fallback line" (fake mode, no key, or Gemini failed). Never raises."""
    if config.BRAIN_FAKE or not configured():
        return None
    try:
        line = await generate("small_talk", prompts.SMALL_TALK_SYSTEM, prompts.small_talk_context(s), OpeningLine,
                              temperature=0.7, timeout=SMALL_TALK_TIMEOUT_S, models=fast_models())
        return _clean_turn(line.spoken_text) or None
    except Exception as e:
        log.warning("small_talk_follow_up fell back to the fixed line: %s", _short(e))
        return None


async def open_interview(s: SessionState, first: PlannedQuestion) -> Optional[str]:
    """The turn that carries the small talk into the first real question. None means "just ask the
    planned question as written" (fake mode, no key, or Gemini failed). Never raises."""
    if config.BRAIN_FAKE or not configured():
        return None
    try:
        line = await generate("open_interview", prompts.OPEN_SYSTEM, prompts.open_context(s, first), OpeningLine,
                              temperature=0.6, timeout=OPEN_TIMEOUT_S, models=fast_models())
        return _clean_turn(line.spoken_text) or None
    except Exception as e:
        log.warning("open_interview fell back to the planned question: %s", _short(e))
        return None


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
