"""Pure text helpers for the interviewer's spoken turns. Shared by the interview loop and fake mode."""

import re
from typing import Optional, Sequence

from backend.brain.lines import STEP_IN_LINES, STEP_OUT_LINES

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


# ---------------------------------------------------------------- stepping out of the interview and back in

# Gemini's own "let me step out…" opener at the very start of the coaching (with only filler words before it).
_STEP_OUT_LEAD = re.compile(
    r"^(?:(?:okay|ok|alright|all right|so|right|now|first|real quick)[,.]?\s+)?"
    r"(?:(?:let me|let's|i'll|i'm going to|i want to|i need to|i'd like to|i have to)\s+)?"
    r"(?:quickly\s+|briefly\s+)?"
    r"(?:step(?:ping)?\s+(?:out|outside|off|away|aside)|(?:go(?:ing)?\s+)?off the record|stop the clock"
    r"|paus(?:e|ing)\s+(?:the|our|this)\s+interview|paus(?:e|ing)\s+(?:here|for a (?:second|moment)))"
    r"[^.!?:]{0,50}[.!?:—]+\s*",
    re.IGNORECASE,
)
# Gemini's own "okay, back in the interview" closer: a short last sentence, so a model answer that happens to
# say "back to" ("…then route traffic back to the primary.") is never cut.
_STEP_IN_TAIL = re.compile(r"\bback (?:in|into|to|on)\b|\bresum(?:e|ing)\b|\bpick(?:ing)? (?:it|things) back up\b"
                           r"|\bon to the next\b|\bcontinue\b|\bmoving on\b", re.IGNORECASE)
_STEP_IN_MAX_WORDS = 8


def _is_step_in(sentence: str) -> bool:
    return len(sentence.split()) <= _STEP_IN_MAX_WORDS and not sentence.endswith("?") \
        and bool(_STEP_IN_TAIL.search(sentence))


def strip_step_markers(coaching: str) -> str:
    """The feedback alone: drop a step-out opener and step-back-in closer that Gemini wrote anyway."""
    body = _STEP_OUT_LEAD.sub("", " ".join((coaching or "").split()), count=1)
    parts = sentences(body)
    while parts and _is_step_in(parts[-1]):
        parts.pop()
    body = " ".join(parts)
    return body[:1].upper() + body[1:]


def with_step_markers(coaching: str, n: int, step_in: bool) -> str:
    """Coaching as spoken: a step-out line, the feedback, then (if another question follows) a step-back-in
    line. `n` is how many coaching turns this session has had, so each one gets the next pair of lines.
    Returns "" when there's no feedback left, so the caller falls back as before."""
    body = strip_step_markers(coaching)
    if not body:
        return ""
    out = f"{STEP_OUT_LINES[n % len(STEP_OUT_LINES)]} {body}"
    return f"{out} {STEP_IN_LINES[n % len(STEP_IN_LINES)]}" if step_in else out


def _norm(sentence: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", sentence.lower()))


def question_part(text: str, coaching: str) -> str:
    """A coaching turn's spoken_text must be only the next question. Gemini sometimes repeats the coaching
    there too, which would play it twice in one clip: drop the repeated sentences, and if a reworded copy
    is left, keep only what follows its "back in the interview". A copy with no clear end leaves "", so
    the planned question is asked as written."""
    parts = sentences(text)
    for i in range(len(parts) - 1, -1, -1):   # find the copy's end first: its "back in" may match the coaching's
        if _is_step_in(parts[i]):
            parts = parts[i + 1:]
            break
    coached = {_norm(p) for p in sentences(coaching)}
    parts = [p for p in parts if _norm(p) not in coached]
    if parts and _STEP_OUT_LEAD.match(parts[0]):
        return ""
    return " ".join(parts)


# ---------------------------------------------------------------- does a turn ask the planned question?

# Too common, or too often swapped when a question is reworded ("tell me" ↔ "walk me through"), to say
# whether two questions are the same one.
_STOPWORDS = {
    "about", "would", "could", "should", "there", "their", "which", "these", "those", "where", "while",
    "think", "really", "going", "thing", "things", "something", "because", "maybe", "other", "first",
    "through", "after", "before", "being", "doing", "still", "might", "every", "never", "always", "what",
    "when", "with", "your", "have", "that", "this", "from", "them", "they", "into", "then", "than", "just",
    "does", "make", "tell", "walk", "describe", "explain", "give", "share", "talk", "like", "some", "more",
    "most", "much", "many", "very", "also", "only", "even", "over",
}
# Fraction of a planned question's content words a turn must contain to count as asking it. Measured on the
# 2026-10-10/11 logs: every bridge and every reworded planned question scored >= 0.5; turns that asked
# something else, or nothing, scored <= 0.18; and every follow-up that was secretly the next planned
# question scored >= 0.5 while real follow-ups scored below it.
SAME_QUESTION = 0.5


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) >= 4 and w not in _STOPWORDS}


def covers(turn: str, planned: str) -> bool:
    """True if `turn` asks the `planned` question (possibly reworded, with a reaction in front)."""
    want = _stems(planned)
    return not want or len(want & _stems(turn)) / len(want) >= SAME_QUESTION


def as_planned(turn: str, planned: str) -> str:
    """Gemini's wording of a planned question, unless it asks something else or nothing at all; then the
    planned question as written."""
    return turn if turn and covers(turn, planned) else planned


def planned_repeat(turn: str, planned: Sequence[str]) -> Optional[int]:
    """Index of the planned question this follow-up actually asks, or None. A follow-up that is really a
    later planned question would ask it twice: once now, then again on its own turn."""
    for i, text in enumerate(planned):
        if len(_stems(text)) >= 2 and covers(turn, text):
            return i
    return None


# A question mark, or a request ("walk me through…", "take a guess…"): the candidate has something to answer.
_ASKS = re.compile(
    r"\?|\b(?:walk|talk|take|run) me through\b|\b(?:tell|give|show|convince|help) me\b"
    r"|\b(?:describe|explain|share|name|list|compare|imagine|picture|suppose|defend|justify|summari[sz]e|sketch"
    r"|outline|define|estimate|elaborate|expand|guess|pick|choose|try|repeat|say (?:it|that) again)\b",
    re.IGNORECASE,
)


def asks_something(text: str) -> bool:
    return bool(_ASKS.search(text or ""))
