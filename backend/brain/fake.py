"""BRAIN_FAKE=1: canned questions, rule-based decisions, no Gemini or ElevenLabs calls.

The decision rules read Data's real stats, so pressing `s` in mock_vitals.py during an answer still
makes the interviewer visibly ease off. Every line spoken in fake mode has a pre-made clip in
backend/static/fake_audio/ (regenerate with scripts/make_fake_audio.py).
"""

from typing import Optional

from backend.brain import lines
from backend.brain.models import Decision, PlannedQuestion
from backend.brain.specs import InterviewSpec
from backend.contracts import QuestionStats

FAKE_QUESTIONS = [
    PlannedQuestion(text="Tell me about a project you built recently and the part you're proudest of.",
                    topic="projects", difficulty=2),
    PlannedQuestion(text="Walk me through how you'd debug an API that suddenly got ten times slower.",
                    topic="debugging", difficulty=3),
    PlannedQuestion(text="How would you design a chat app that has to support a million users?",
                    topic="system design", difficulty=4),
    PlannedQuestion(text="Tell me about your biggest failure, and why I should still hire you.",
                    topic="pressure", difficulty=5),
]

FAKE_LINES = {
    "ease_off": "That's okay, let me put it more simply. Just walk me through the very first step you'd take.",
    "push": "Okay. Let's go one level deeper. What would break first if the load grew ten times?",
    "follow_up": "Can you give me one specific example of that?",
}


def plan(spec: InterviewSpec) -> list[PlannedQuestion]:
    n = min(spec.n_questions, len(FAKE_QUESTIONS))
    return [q.model_copy() for q in FAKE_QUESTIONS[:n]]


def decide(transcript: str, stats: Optional[QuestionStats]) -> Decision:
    """Same thresholds as the Gemini system prompt, with word count standing in for answer quality."""
    words = len((transcript or "").split())
    quality = 1 if words < 5 else 2 if words < 15 else 3 if words < 40 else 4
    peak = stats.stress_peak if stats else None
    mean = stats.stress_mean if stats else None

    if peak is not None and peak > 70:
        action = "ease_off"
    elif mean is not None and mean < 40 and words >= 25:
        action = "push"
    elif words < 8:
        action = "follow_up"
    else:
        action = "next"
    return Decision(
        action=action,
        spoken_text=FAKE_LINES.get(action, ""),
        answer_quality=quality,
        rationale=f"fake rules: words={words} stress_peak={peak} stress_mean={mean}",
    )


def all_texts() -> list[str]:
    """Every line fake mode can speak. scripts/make_fake_audio.py renders exactly these."""
    texts = [lines.CALIBRATION_LINE, lines.CALIBRATION_EXTRA_LINE, lines.CLOSING_LINE, *lines.FILLERS]
    texts += [q.text for q in FAKE_QUESTIONS]
    texts += list(FAKE_LINES.values())
    return list(dict.fromkeys(texts))
