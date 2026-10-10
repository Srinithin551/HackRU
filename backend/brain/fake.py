"""BRAIN_FAKE=1: canned questions, rule-based decisions, no Gemini or ElevenLabs calls.

The decision rules read Data's real stats, so pressing `s` in mock_vitals.py during an answer still
makes the interviewer visibly ease off. Every line spoken in fake mode has a pre-made clip in
backend/static/fake_audio/ (regenerate with scripts/make_fake_audio.py).
"""

import re
from typing import Optional

from backend.brain import lines
from backend.brain.models import Decision, PlannedQuestion
from backend.brain.specs import InterviewSpec
from backend.brain.turns import final_coaching
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
    "redirect": "Sorry, I didn't catch that. Could you say it again?",
    "ease_off": "That's okay, let me put it more simply. Just walk me through the very first step you'd take.",
    "push": "Let's go one level deeper. What would break first if the load grew ten times?",
    "follow_up": "Can you give me one specific example of that?",
}

# Coaching, said outside the interview frame, then the next question in the same clip. Every combination
# is pre-recorded, so a coaching turn is still a single clip.
FAKE_COACH = ("Let me step out of the interview for a second. In a real interview, an answer like that would cost "
              "you, because it doesn't answer what was asked. A strong answer leads with your main point and backs "
              "it with one concrete example. Okay, back in the interview.")


def coach_turn(next_question: Optional[str]) -> str:
    return f"{FAKE_COACH} {next_question}" if next_question else FAKE_COACH


def plan(spec: InterviewSpec) -> list[PlannedQuestion]:
    n = min(spec.n_questions, len(FAKE_QUESTIONS))
    return [q.model_copy() for q in FAKE_QUESTIONS[:n]]


# Words too common to tell whether an answer is about the question.
_STOPWORDS = {
    "about", "would", "could", "should", "there", "their", "which", "these", "those", "where", "while",
    "think", "really", "going", "thing", "things", "something", "because", "maybe", "other", "first",
    "through", "after", "before", "being", "doing", "still", "might", "every", "never", "always",
}


def _content_stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 5 and w not in _STOPWORDS}


def off_topic(question: str, transcript: str) -> bool:
    """Crude offline stand-in for Gemini's judgment: a real answer (8+ words) that shares no content
    words with the question. Only used in fake mode."""
    if len(transcript.split()) < 8:
        return False
    q = _content_stems(question)
    return bool(q) and not (q & _content_stems(transcript))


def decide(transcript: str, stats: Optional[QuestionStats], question: str = "", kind: str = "main",
           next_question: Optional[str] = None) -> Decision:
    """Same order and thresholds as the Gemini system prompt, with word count standing in for answer quality.
    Only main questions are checked for off-topic answers (fake follow-up lines are too generic)."""
    words = len((transcript or "").split())
    quality = 1 if words < 5 else 2 if words < 15 else 3 if words < 40 else 4
    peak = stats.stress_peak if stats else None
    mean = stats.stress_mean if stats else None

    if words == 0:
        action = "redirect"
    elif words < 5 or (kind == "main" and off_topic(question, transcript)):
        action, quality = "coach", 1
    elif peak is not None and peak > 70:
        action = "ease_off"
    elif mean is not None and mean < 40 and words >= 25:
        action = "push"
    elif words < 8:
        action = "follow_up"
    else:
        action = "next"
    if action == "coach":
        coaching, text = FAKE_COACH, next_question or ""
    else:
        coaching, text = "", FAKE_LINES.get(action, "")
    return Decision(
        action=action,
        spoken_text=text,
        coaching=coaching,
        answer_quality=quality,
        rationale=f"fake rules: words={words} stress_peak={peak} stress_mean={mean}",
    )


def all_texts() -> list[str]:
    """Every line fake mode can speak. scripts/make_fake_audio.py renders exactly these."""
    texts = lines.fixed_lines()
    texts += [q.text for q in FAKE_QUESTIONS]
    texts += list(FAKE_LINES.values())
    texts += [coach_turn(q.text) for q in FAKE_QUESTIONS[1:]]             # coaching + next question, one clip
    texts += [f"{final_coaching(FAKE_COACH)} {lines.CLOSING_LINE}"]         # coaching on the last answer + closing
    return list(dict.fromkeys(texts))
