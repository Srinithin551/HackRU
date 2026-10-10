"""Fixed interviewer lines. Pre-generated and cached at startup so they play instantly."""

# Calibration small talk. While the user chats, Presage warms up and Data collects the baseline.
CALIBRATION_LINE = "Hey, thanks for coming in. Before we get started, how's HackRU going so far?"
# Asked only if the first calibration answer was too short to give a usable baseline.
CALIBRATION_EXTRA_LINE = "Nice. And what have you been working on this weekend?"

CLOSING_LINE = "That's everything I wanted to cover. Thanks for your time. Give me a moment to put your feedback together."

# Played the instant an answer ends, to mask Gemini latency. Rotated so none repeats back to back.
FILLERS = [
    "Okay.",
    "Got it.",
    "Mm, alright.",
    "Okay, thanks.",
    "Alright, let's keep going.",
]
