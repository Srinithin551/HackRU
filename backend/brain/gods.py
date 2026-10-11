"""Which god speaks each line, so every god has their own voice (config.GOD_VOICES).

Mirrors frontend/src/lib/constants.ts (PRESIDING_GOD, PANTHEON, godForQuestion): keep the names and order in
sync, or a question will be voiced by a different god than the one shown on screen.
"""

PRESIDING = "Osiras"   # presides: the warm-up small talk and the closing line
PANTHEON = ("Athena", "Apollo", "Hephaestus", "Hera", "Hermes")   # take the planned questions in turn


def god_for_question(index: int) -> str:
    """The god asking question `index`: 0 is the warm-up (Osiras), then 1..n cycles through the pantheon.
    A follow-up keeps its parent's index, so it's asked by the same god. Any coaching on the previous answer
    is spoken in the same clip, so it comes from this god too."""
    if index <= 0:
        return PRESIDING
    return PANTHEON[(index - 1) % len(PANTHEON)]
