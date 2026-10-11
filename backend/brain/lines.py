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

# Coaching steps outside the interview and back in. Gemini writes only the feedback; the code wraps it in
# these (turns.with_step_markers), taking the next pair each time, so no session hears the same line twice
# before all of them have been used. When Gemini wrote these itself, it used the prompt's example word for
# word on every coaching turn. They are spoken inside the coaching clip, not pre-recorded on their own.
STEP_OUT_LINES = (
    "Let me step out of the interview for a second.",
    "Let me step off the bench for a moment.",
    "Off the record for a second.",
    "Let me pause the interview here.",
    "Quick aside before we go on.",
    "Let me stop the clock for a moment.",
)
STEP_IN_LINES = (
    "Okay, back in the interview.",
    "Back on the record.",
    "Alright, let's pick it back up.",
    "Now, back to the questions.",
    "Let's continue.",
    "Okay, on to the next one.",
)

GREETING = "Thanks for joining me today."   # how both openers begin


def calibration_lines(spec_key: str, ask_for_material: bool = False, name: str = "") -> tuple[str, str]:
    """(opener, fallback second turn). A presentation with no imported file opens by asking about its content.
    With a `name`, the opener greets them by it ("Thanks for joining me today, Ada."); that clip is made
    for the session instead of pre-recorded."""
    opener = PRESENTATION_MATERIAL_LINE if ask_for_material else CALIBRATION_LINE
    if name:
        opener = opener.replace(GREETING, f"{GREETING[:-1]}, {name}.", 1)
    return opener, CALIBRATION_EXTRA_LINES.get(spec_key, CALIBRATION_EXTRA_LINES["swe"])


def fixed_lines() -> list[str]:
    """Every pre-recorded line, deduplicated, in a stable order."""
    texts = [CALIBRATION_LINE, PRESENTATION_MATERIAL_LINE, *CALIBRATION_EXTRA_LINES.values(), CLOSING_LINE]
    return list(dict.fromkeys(texts))
