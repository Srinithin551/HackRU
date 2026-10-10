"""Fixed interviewer lines. Pre-generated and cached at startup so they play instantly."""

# Calibration: an easy, standard opener. While the user talks, Presage warms up and Data collects the
# calm baseline. The answer also gives Gemini something to build the first real question on.
CALIBRATION_LINE = "Thanks for joining me today. To start, tell me a little about yourself."

# Asked only if the first answer was too short to give a usable baseline. Keyed by interview type
# (specs.SPECS keys). Kept generic (no role name) so they can be pre-recorded.
CALIBRATION_EXTRA_LINES = {
    "swe": "What got you interested in this kind of work?",
    "behavioral": "What got you interested in this kind of work?",
    "demo": "What got you interested in this kind of work?",
    "presentation": "What made you choose this topic?",
}

CLOSING_LINE = "That's everything I wanted to cover. Thanks for your time. Give me a moment to put your feedback together."

# Played the instant an answer ends, to mask Gemini latency. Rotated so none repeats back to back.
FILLERS = [
    "Okay.",
    "Got it.",
    "Mm, alright.",
    "Okay, thanks.",
    "Alright, let's keep going.",
]


def calibration_lines(spec_key: str) -> tuple[str, str]:
    return CALIBRATION_LINE, CALIBRATION_EXTRA_LINES.get(spec_key, CALIBRATION_EXTRA_LINES["swe"])


def fixed_lines() -> list[str]:
    """Every pre-recorded line, deduplicated, in a stable order."""
    texts = [CALIBRATION_LINE, *CALIBRATION_EXTRA_LINES.values(), CLOSING_LINE, *FILLERS]
    return list(dict.fromkeys(texts))
