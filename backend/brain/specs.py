"""Interview types: question counts, difficulty curves, limits, and fallback questions.

The frontend sends human labels ("Software Engineering", "Quick Demo", ...). `get_spec()` maps any
of those (or short keys like "swe", "demo") onto one of the specs below.
"""

from dataclasses import dataclass

from backend.brain.models import PlannedQuestion


@dataclass(frozen=True)
class InterviewSpec:
    key: str
    label: str
    difficulties: tuple[int, ...]   # one per planned main question, in order
    max_turns: int                  # main + follow-up questions, excluding calibration
    max_seconds: int                # from the start of the session (calibration included)
    style: str                      # one line injected into the planning prompt

    @property
    def n_questions(self) -> int:
        return len(self.difficulties)


SPECS = {
    "swe": InterviewSpec(
        key="swe", label="Software Engineering", difficulties=(2, 3, 3, 4, 5),
        max_turns=7, max_seconds=480,
        style=("A spoken technical interview: past projects, debugging stories, system design trade-offs, "
               "and concepts explained out loud. Nothing that needs a whiteboard or writing code."),
    ),
    "behavioral": InterviewSpec(
        key="behavioral", label="Behavioral", difficulties=(2, 3, 3, 4, 5),
        max_turns=7, max_seconds=480,
        style=("A behavioral interview that invites STAR-style stories: teamwork, conflict, failure, "
               "ownership, pressure, and leadership."),
    ),
    "presentation": InterviewSpec(
        key="presentation", label="Class Presentation", difficulties=(2, 3, 3, 4, 5),
        max_turns=7, max_seconds=480,
        style=("The candidate just gave a class presentation on the topic below. You are the professor "
               "running Q&A: clarify, probe methodology and evidence, challenge assumptions, ask about "
               "limitations and what they would do next."),
    ),
    "demo": InterviewSpec(
        key="demo", label="Quick Demo", difficulties=(2, 4, 5),
        max_turns=5, max_seconds=240,
        style=("A short, punchy mock interview for a live demo. Start friendly, then get noticeably "
               "harder; the last question should put real pressure on the candidate."),
    ),
}

DEFAULT_ROLE = {
    "swe": "Software Engineer",
    "behavioral": "Software Engineer",
    "presentation": "a class project",
    "demo": "Software Engineer",
}


def normalize_type(interview_type: str) -> str:
    t = (interview_type or "").strip().lower()
    if t in SPECS:
        return t
    if "demo" in t or "quick" in t:
        return "demo"
    if "behav" in t:
        return "behavioral"
    if "present" in t or "class" in t:
        return "presentation"
    return "swe"


def get_spec(interview_type: str) -> InterviewSpec:
    return SPECS[normalize_type(interview_type)]


# Used when Gemini is unavailable or returns too few questions. Ordered easy → hard.
FALLBACK_BANK: dict[str, list[tuple[str, str]]] = {
    "swe": [
        ("Tell me about a project you built recently and the part you're proudest of.", "projects"),
        ("Walk me through how you'd debug an API that suddenly got ten times slower.", "debugging"),
        ("What's the difference between a process and a thread, and when would you pick each?", "fundamentals"),
        ("How would you design a URL shortener that handles a million requests a minute?", "system design"),
        ("Tell me about a technical decision you got wrong. What did it cost, and what would you do now?", "judgment"),
    ],
    "behavioral": [
        ("Tell me about a time you worked on a team project. What was your role?", "teamwork"),
        ("Describe a time you disagreed with a teammate. How did you handle it?", "conflict"),
        ("Tell me about a deadline you almost missed. What happened?", "pressure"),
        ("Describe a time you failed at something important. What did you learn?", "failure"),
        ("Tell me about a time you had to lead people who didn't want to follow you.", "leadership"),
    ],
    "presentation": [
        ("Can you summarize your main point in one sentence for someone who missed the talk?", "summary"),
        ("Where did your data come from, and why should we trust it?", "evidence"),
        ("What's the strongest argument against your conclusion?", "counterargument"),
        ("If you had another month, what would you change about your approach?", "methodology"),
        ("Your results could be explained by something else entirely. Convince me they aren't.", "challenge"),
    ],
    "demo": [
        ("Tell me about a project you're proud of, in about thirty seconds.", "projects"),
        ("Walk me through how you'd design a chat app that has to support a million users.", "system design"),
        ("Tell me about your biggest failure, and why I should still hire you.", "pressure"),
    ],
}


def fallback_questions(spec: InterviewSpec) -> list[PlannedQuestion]:
    bank = FALLBACK_BANK[spec.key]
    out = []
    for i, d in enumerate(spec.difficulties):
        text, topic = bank[min(i, len(bank) - 1)]
        out.append(PlannedQuestion(text=text, topic=topic, difficulty=d))
    return out
