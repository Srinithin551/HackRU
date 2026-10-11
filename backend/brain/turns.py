"""Pure text helpers for the interviewer's spoken turns. Shared by the interview loop and fake mode."""

import re

MAX_TURN_WORDS = 70      # prompts ask for < 45; anything far longer is treated as a bad turn
MAX_COACH_WORDS = 110    # the coaching part alone (prompt asks for < 75)

# Per planned question. Defined here (not in interviewer.py) so the decide_next prompt quotes the same
# limits the loop enforces: prompts.py can't import interviewer.py without a circular import.
MAX_FOLLOWUPS = 1        # ease_off / push / follow_up
MAX_REDIRECTS = 1        # "didn't catch that" re-asks for garbled answers

_SENTENCE = re.compile(r"[^.!?]+[.!?]+[\"')\]]*\s*|[^.!?]+$")
_STEP_BACK_IN = re.compile(r"\bback (?:in|into|to)\b", re.IGNORECASE)


def spoken(text: str, limit: int = MAX_TURN_WORDS) -> str:
    """Gemini's text if it's usable as speech, else "" (callers then fall back)."""
    text = " ".join((text or "").split())
    return text if 0 < len(text.split()) <= limit else ""


def sentences(text: str) -> list[str]:
    """Split into sentences, keeping closing quotes/brackets with their sentence."""
    return [m.group(0).strip() for m in _SENTENCE.finditer(text or "") if m.group(0).strip()]


def final_coaching(text: str) -> str:
    """Coaching that ends the interview must stop at the model answer: cut any trailing question and
    the "okay, back in the interview" sentence (the closing line comes right after)."""
    parts = sentences(text)
    while len(parts) > 1 and (parts[-1].endswith("?") or _STEP_BACK_IN.search(parts[-1])):
        parts.pop()
    return " ".join(parts)


def split_coaching(text: str) -> tuple[str, str]:
    """Backstop for a coaching turn that arrived as one blob: split it into (coaching, question) at the
    "back in the interview" sentence. Without that marker it's all coaching and the planned question
    gets asked as written."""
    parts = sentences(text)
    for i in range(len(parts) - 1, -1, -1):
        if _STEP_BACK_IN.search(parts[i]):
            return " ".join(parts[: i + 1]), " ".join(parts[i + 1:])
    return " ".join(parts), ""
