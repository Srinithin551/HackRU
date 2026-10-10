"""Report assembly: validates Gemini's draft and builds a numbers-only fallback when Gemini is unavailable.

Whatever happens, the frontend gets a complete Report (see models.Report) with real numbers in it.
"""

import re
from typing import Optional

from backend.brain.history import HistoryItem, num, pct
from backend.brain.models import QuestionFeedback, Report, ReportDraft
from backend.brain.state import SessionState
from backend.contracts import QuestionStats

_DIGIT = re.compile(r"(?<![Qq\d])\d")   # a real number, not a "Q2" label
NO_VITALS_SCORE = 50     # composure can't be measured without vitals; don't let "no data" read as "calm"
COMPOSURE_LEEWAY = 15    # Gemini may adjust the stress-based score for recovery, but only this far


def _answered(items: list[HistoryItem]) -> list[HistoryItem]:
    return [it for it in items if it.kind != "calibration"]


def _has_vitals(st: Optional[QuestionStats]) -> bool:
    return st is not None and st.n_samples > 0 and st.stress_mean is not None


def composure_from_stats(items: list[HistoryItem]) -> Optional[int]:
    means = [it.stats.stress_mean for it in _answered(items) if _has_vitals(it.stats)]
    if not means:
        return None
    return int(round(max(0.0, min(100.0, 100 - sum(means) / len(means)))))


def hardest(items: list[HistoryItem]) -> Optional[HistoryItem]:
    candidates = [it for it in _answered(items) if it.stats and it.stats.stress_peak is not None]
    return max(candidates, key=lambda it: it.stats.stress_peak) if candidates else None


def hardest_moment_text(items: list[HistoryItem]) -> str:
    it = hardest(items)
    if it is None:
        return "No stress spike could be measured: the camera didn't get a usable signal during your answers."
    st = it.stats
    when = f" {st.peak_offset_s:.0f} seconds into your answer" if st.peak_offset_s is not None else ""
    hr = (f", with heart rate peaking at {num(st.hr_peak)} bpm ({pct(st.hr_delta_pct)} vs your baseline on average)"
          if st.hr_peak is not None else "")
    return f'{it.label}, "{it.text}": stress peaked at {num(st.stress_peak)}{when}{hr}.'


def body_signal_text(st: Optional[QuestionStats]) -> str:
    if not _has_vitals(st):
        return "No usable vitals during this answer (face not detected or sensor offline)."
    parts = []
    if st.hr_mean is not None:
        parts.append(f"Heart rate averaged {num(st.hr_mean)} bpm ({pct(st.hr_delta_pct)} vs baseline)"
                     + (f", peaking at {num(st.hr_peak)}" if st.hr_peak is not None else ""))
    peak_at = f" {st.peak_offset_s:.0f}s in" if st.peak_offset_s is not None else ""
    parts.append(f"stress averaged {num(st.stress_mean)} and peaked at {num(st.stress_peak)}{peak_at}")
    if st.focus_mean is not None:
        parts.append(f"focus {num(st.focus_mean, 2)}")
    text = ", ".join(parts)
    return text[0].upper() + text[1:] + "."


def _advice(st: Optional[QuestionStats], transcript: str) -> str:
    words = len(transcript.split())
    if words < 15:
        return "Give a fuller answer: state your point, back it with one concrete example, then land a one-line conclusion."
    if _has_vitals(st) and (st.stress_peak or 0) > 70:
        return "When a question lands hard, pause for two seconds and restate it in your own words before answering."
    if _has_vitals(st) and (st.stress_mean or 0) > 40:
        return "Slow your pace slightly and finish sentences before starting the next thought."
    if _has_vitals(st):
        return "Good composure here. Push for more specifics: numbers, names, and outcomes make answers memorable."
    return "Push for more specifics: numbers, names, and outcomes make answers memorable."


OFF_TOPIC_ADVICE = "Answer the question that was asked: restate it in a few words first, then give your answer."


def _what_happened(it: HistoryItem, off_topic: bool = False) -> str:
    words = len(it.transcript.split())
    if words == 0:
        return "No answer was captured for this question."
    if off_topic:
        return "Your answer didn't address the question, so the interviewer steered you back to it."
    secs = round(it.stats.duration_s) if it.stats and it.stats.duration_s else 0
    dur = f" in about {secs} second{'s' if secs != 1 else ''}" if secs else ""
    return f"You answered with about {words} words{dur}."


def fallback_feedback(it: HistoryItem, off_topic: bool = False) -> QuestionFeedback:
    return QuestionFeedback(
        question_id=it.question_id,
        what_happened=_what_happened(it, off_topic),
        body_signal=body_signal_text(it.stats),
        advice=OFF_TOPIC_ADVICE if off_topic else _advice(it.stats, it.transcript),
    )


def redirected_ids(s: SessionState) -> set[str]:
    """Questions whose answer the interviewer judged off-topic."""
    return {a["question_id"] for a in s.adaptations if a["action"] == "redirect"}


def fallback_tips(items: list[HistoryItem], off_topic_ids: frozenset = frozenset()) -> list[str]:
    tips = []
    if any(it.question_id in off_topic_ids for it in items):
        tips.append(OFF_TOPIC_ADVICE)
    h = hardest(items)
    if h is not None and (h.stats.stress_peak or 0) > 70:
        tips.append(f"Your biggest spike came on {h.label}. Practice that question type out loud until it feels routine.")
    if any(len(it.transcript.split()) < 15 for it in _answered(items)):
        tips.append("Some answers were very short. Use a simple structure: point, example, result.")
    tips += [
        "Before answering a hard question, take one slow breath. It buys you time and steadies your heart rate.",
        "Keep your eyes on the camera when you're thinking; looking away reads as uncertainty.",
        "End each answer with a one-sentence takeaway so the interviewer knows you're done.",
    ]
    return tips[:3]


def fallback_report(s: SessionState, items: list[HistoryItem], generated_by: str = "fallback") -> Report:
    answered = _answered(items)
    off_topic = redirected_ids(s)
    score = composure_from_stats(items)
    if score is None:
        summary = (f"You answered {len(answered)} question(s). The camera didn't capture enough usable vitals "
                   f"to measure your stress, so this report focuses on your answers.")
        score = NO_VITALS_SCORE
    else:
        level = "calm" if score >= 60 else "visibly stressed at times" if score >= 30 else "under heavy stress"
        h = hardest(items)
        worst = f" {h.label} was where your body reacted most." if h is not None else ""
        summary = (f"You answered {len(answered)} question(s) and stayed {level} overall, "
                   f"for a composure score of {score}.{worst}")
    return Report(
        composure_score=score,
        summary=summary,
        hardest_moment=hardest_moment_text(items),
        per_question=[fallback_feedback(it, it.question_id in off_topic) for it in answered],
        top_tips=fallback_tips(items, frozenset(off_topic)),
        coach_flags=s.flags,
        adaptations=s.adaptations,
        generated_by=generated_by,
    )


def finalize_draft(s: SessionState, items: list[HistoryItem], draft: ReportDraft) -> Report:
    """Make Gemini's draft safe to serve: valid ids, every question covered, numbers present, 3 tips."""
    answered = _answered(items)
    off_topic = redirected_ids(s)
    by_id = {fb.question_id: fb for fb in draft.per_question}
    per_question = []
    for it in answered:
        fb = by_id.get(it.question_id)
        if fb is None:
            fb = fallback_feedback(it, it.question_id in off_topic)
        elif not _DIGIT.search(fb.body_signal):
            fb = fb.model_copy(update={"body_signal": body_signal_text(it.stats)})
        per_question.append(fb)

    tips = [t.strip() for t in draft.top_tips if t and t.strip()][:3]
    for extra in fallback_tips(items, frozenset(off_topic)):
        if len(tips) >= 3:
            break
        if extra not in tips:
            tips.append(extra)

    base = composure_from_stats(items)
    if base is None:
        score = NO_VITALS_SCORE
    else:
        score = max(base - COMPOSURE_LEEWAY, min(base + COMPOSURE_LEEWAY, int(draft.composure_score)))
        score = max(0, min(100, score))
    hardest_text = draft.hardest_moment.strip()
    if not _DIGIT.search(hardest_text):
        hardest_text = hardest_moment_text(items)

    return Report(
        composure_score=score,
        summary=draft.summary.strip() or fallback_report(s, items).summary,
        hardest_moment=hardest_text,
        per_question=per_question,
        top_tips=tips,
        coach_flags=s.flags,
        adaptations=s.adaptations,
        generated_by="gemini",
    )
