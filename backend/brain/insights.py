"""Chart notes: what each measured variable did during each question, in words.

The replay chart shows heart rate and composure over time with a band per question. Hovering a band
should explain that stretch rather than just restating the number, so this asks Gemini for one short
note per (question, variable) and the frontend shows it in the tooltip.

Served by GET /api/sessions/{id}/insights, generated on first request rather than during the
interview: the verdict must not wait on another model call. Results are cached per session, and if
Gemini is unavailable the notes are built from the numbers alone — the same contract as the report
(never raises, always complete).
"""

import asyncio
import logging
from typing import Literal, Optional

from pydantic import BaseModel

from backend import config
from backend.brain import gemini, prompts
from backend.brain.history import HistoryItem, normalize_history, num, pct
from backend.brain.state import metas, states
from backend.contracts import QuestionStats
from backend.data import queries as data

log = logging.getLogger("brain.insights")

INSIGHTS_TIMEOUT_S = 30.0

Variable = Literal["heart_rate", "breathing", "composure"]
VARIABLES: tuple[Variable, ...] = ("heart_rate", "breathing", "composure")

VARIABLE_LABEL: dict[str, str] = {
    "heart_rate": "Heart rate",
    "breathing": "Breathing",
    "composure": "Composure",
}


class RangeNote(BaseModel):
    question_id: str
    variable: Variable
    headline: str   # at most ~6 words, shown in bold in the tooltip
    detail: str     # 1-2 sentences citing a number from the data


class ChartNotes(BaseModel):
    notes: list[RangeNote]


# session_id -> notes, so hovering the chart doesn't re-ask Gemini on every render.
_cache: dict[str, list[dict]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _lock(session_id: str) -> asyncio.Lock:
    return _locks.setdefault(session_id, asyncio.Lock())


# ---------------------------------------------------------------- fallback (no Gemini)

def _trend(value: Optional[float], rising: float, high: float) -> str:
    if value is None:
        return "no reading"
    if value > high:
        return "well above"
    if value > rising:
        return "above"
    return "steady at"


def fallback_notes(items: list[HistoryItem]) -> list[RangeNote]:
    """Notes built from the numbers alone. Honest and specific, just not written by Gemini."""
    out: list[RangeNote] = []
    for it in items:
        st = it.stats
        if st is None or st.n_samples == 0:
            continue
        hr_word = _trend(st.hr_delta_pct, 8, 20)
        out.append(RangeNote(
            question_id=it.question_id, variable="heart_rate",
            headline=f"{num(st.hr_mean)} bpm average",
            detail=(f"Your heart rate averaged {num(st.hr_mean)} bpm here, {hr_word} your baseline "
                    f"({pct(st.hr_delta_pct)}), peaking at {num(st.hr_peak)}."),
        ))
        if st.br_mean is not None:
            out.append(RangeNote(
                question_id=it.question_id, variable="breathing",
                headline=f"{num(st.br_mean)} breaths a minute",
                detail=f"You averaged {num(st.br_mean)} breaths a minute while answering this one.",
            ))
        if st.stress_mean is not None:
            when = f" about {st.peak_offset_s:.0f}s in" if st.peak_offset_s is not None else ""
            out.append(RangeNote(
                question_id=it.question_id, variable="composure",
                headline=f"Peaked at {num(st.stress_peak)}",
                detail=(f"Stress averaged {num(st.stress_mean)} and peaked at {num(st.stress_peak)}{when}, "
                        f"on the 0-100 scale where over 70 is high."),
            ))
    return out


# ---------------------------------------------------------------- Gemini

def _context(items: list[HistoryItem], baseline_line: str) -> str:
    lines = [f"BASELINE: {baseline_line}", ""]
    for it in items:
        st = it.stats
        if st is None or st.n_samples == 0:
            continue
        lines.append(f"{it.label} question_id={it.question_id}")
        lines.append(f'  QUESTION: "{it.text}"')
        lines.append(f"  HEART RATE: mean {num(st.hr_mean)} bpm ({pct(st.hr_delta_pct)} vs baseline), peak {num(st.hr_peak)}")
        lines.append(f"  BREATHING: mean {num(st.br_mean)} per minute")
        lines.append(f"  COMPOSURE: stress mean {num(st.stress_mean)}, peak {num(st.stress_peak)}"
                     + (f" at {st.peak_offset_s:.0f}s into the answer" if st.peak_offset_s is not None else ""))
        lines.append(f"  ANSWER LENGTH: {st.duration_s:.0f}s")
    return "\n".join(lines)


INSIGHTS_SYSTEM = """You are explaining a mock-interview replay chart to the candidate who just sat it.

For every question listed, write one note for each of the three variables: heart_rate, breathing, composure.

Rules:
- Use only numbers that appear in the data. Never invent one.
- headline: at most 6 words, no final full stop, e.g. "Climbed 22% over baseline".
- detail: 1-2 sentences saying what the number means for this question, in plain English, addressed
  as "you". Say what it suggests about how the answer went, not just the number again.
- composure is a 0-100 stress score against the candidate's own calm baseline: under 40 calm,
  40 to 70 rising, over 70 high. Higher means less composed.
- Use the exact question_id given. No markdown."""


def _valid(notes: list[RangeNote], items: list[HistoryItem]) -> list[RangeNote]:
    """Drop anything addressed to a question that isn't in this session."""
    known = {it.question_id for it in items}
    return [n for n in notes if n.question_id in known and n.headline.strip() and n.detail.strip()]


async def build_notes(session_id: str) -> list[dict]:
    """Chart notes for a session. Never raises: falls back to numbers-only notes."""
    history = await data.get_session_history(session_id)
    items = [it for it in normalize_history(history) if it.kind != "calibration"]
    if not items:
        return []

    state = states.get(session_id)
    baseline_line = prompts._baseline_line(state.baseline if state else None)
    fallback = fallback_notes(items)

    if config.BRAIN_FAKE or not gemini.configured():
        return [n.model_dump() for n in fallback]
    try:
        written = await gemini.generate(
            "chart_insights", INSIGHTS_SYSTEM, _context(items, baseline_line), ChartNotes,
            temperature=0.5, timeout=INSIGHTS_TIMEOUT_S)
        notes = _valid(written.notes, items)
        # Anything Gemini skipped keeps its numbers-only note, so every band always has all three.
        have = {(n.question_id, n.variable) for n in notes}
        notes += [n for n in fallback if (n.question_id, n.variable) not in have]
        return [n.model_dump() for n in notes]
    except Exception as e:
        log.warning("chart insights fell back to numbers-only notes: %s", gemini._short(e))
        return [n.model_dump() for n in fallback]


async def get_notes(session_id: str) -> Optional[list[dict]]:
    """Cached chart notes, or None if the session is unknown."""
    if session_id in _cache:
        return _cache[session_id]
    async with _lock(session_id):
        if session_id in _cache:   # another request generated them while we waited
            return _cache[session_id]
        if session_id not in states and session_id not in metas:
            # Not a session this process ran; it may still exist in the database.
            if await data.get_report(session_id) is None:
                return None
        notes = await build_notes(session_id)
        _cache[session_id] = notes
        return notes
