"""Fixed interviewer lines. Pre-generated and cached at startup so they play instantly.

There are deliberately no filler clips ("Okay.", "Got it."): they sounded scripted, and a filler still
playing when the next question arrived made two clips overlap. The brief pause while Gemini thinks
reads as an interviewer considering the answer.
"""

# Calibration: an easy, standard opener. While the user talks, Presage warms up and Data collects the
# calm baseline. The answer also gives Gemini something to build the first real question on.
CALIBRATION_LINE = "Thanks for joining me today. To start, tell me a little about yourself."

# A Class Presentation with no imported file opens with this instead: the questions are then planned from
# what the candidate says the presentation covers (backend/brain/material.py).
PRESENTATION_MATERIAL_LINE = ("Thanks for joining me today. Before we begin, walk me through your presentation: "
                              "what is it about, and what material does it cover?")

# The second small-talk turn (asked only if the first answer was too short for a usable baseline) is
# written live by Gemini so it can react to what they said. These are the fallbacks if Gemini is
# unavailable (and fake mode). Keyed by interview type (specs.SPECS keys).
CALIBRATION_EXTRA_LINES = {
    "swe": "What got you into your field in the first place?",
    "behavioral": "What got you into your field in the first place?",
    "demo": "What got you into your field in the first place?",
    "presentation": "What made you choose this topic?",
}

CLOSING_LINE = "That's everything I wanted to cover. Thanks for your time. Give me a moment to put your feedback together."

def calibration_lines(spec_key: str, ask_for_material: bool = False) -> tuple[str, str]:
    """(opener, fallback second turn). A presentation with no imported file opens by asking about its content."""
    opener = PRESENTATION_MATERIAL_LINE if ask_for_material else CALIBRATION_LINE
    return opener, CALIBRATION_EXTRA_LINES.get(spec_key, CALIBRATION_EXTRA_LINES["swe"])


def fixed_lines() -> list[str]:
    """Every pre-recorded line, deduplicated, in a stable order."""
    texts = [CALIBRATION_LINE, PRESENTATION_MATERIAL_LINE, *CALIBRATION_EXTRA_LINES.values(), CLOSING_LINE]
    return list(dict.fromkeys(texts))
