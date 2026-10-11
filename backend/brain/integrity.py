"""Integrity watcher: a candidate who keeps looking away mid-answer is suspected of cheating.

The browser (frontend/src/hooks/useLookAway.ts) tracks head pose and eye direction on the camera locally
and sends a `look_away` client event each time the candidate has looked away from the screen for
LOOK_AWAY_MIN_MS. One glance is thinking; several in a short stretch reads as reading answers off
something. So a single look-away does nothing, and only REPEATS look-aways within WINDOW_S while
answering raise an `integrity_flag` ("I suspect you of cheating"), shown in the same interrupt box as
the coach flags. Nothing is ever spoken: the mic is open during an answer.

After a flag the count starts over, so a second flag needs another REPEATS look-aways.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from backend.brain.state import SessionState
from backend.ws import broadcast

log = logging.getLogger("brain.integrity")

REPEATS = 3          # look-aways within WINDOW_S that make a suspicion
WINDOW_S = 60.0

# (message, command). `message` may use {n} (look-aways in this stretch) and {total} (this session).
# Every phrasing makes the claim; `pick` never reuses one while an unused one remains, because the
# frontend drops a text it has already shown.
VARIANTS: list[tuple[str, str]] = [
    ("You have looked away from me {n} times in under a minute. I suspect you of cheating.",
     "keep your eyes on the screen and answer from memory"),
    ("Your eyes keep leaving the stand. {total} times now. The court suspects you of cheating.",
     "look at me and put away whatever you are reading"),
    ("Again you look away. I suspect you are reading your answers, and it will be noted.",
     "face the camera for the rest of this answer"),
    ("{total} glances off screen. Suspicion of cheating is now on the record.",
     "keep your gaze here until the interview ends"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pick(used: set[str]) -> tuple[str, str]:
    """The first phrasing not used yet this session (the first one once all are used)."""
    for message, command in VARIANTS:
        if message not in used:
            return message, command
    return VARIANTS[0]


async def on_look_away(s: SessionState, question_id: Optional[str], now: Optional[float] = None) -> Optional[dict]:
    """Record one look-away. Returns the integrity flag it raised, if this one made a suspicion.

    Only counts while the candidate is answering the current question: looking away while the
    interviewer talks or thinks is ordinary, and a stale event for an earlier question is dropped.
    """
    if s.phase != "listening" or question_id != s.current_qid:
        return None
    now = time.monotonic() if now is None else now
    s.look_away_total += 1
    s.look_aways = [t for t in s.look_aways if now - t <= WINDOW_S] + [now]
    if len(s.look_aways) < REPEATS:
        return None

    n = len(s.look_aways)
    s.look_aways = []   # start over: a second suspicion needs another run of look-aways
    template, command = pick(s.used_flag_texts)
    s.used_flag_texts.add(template)
    message = template.format(n=n, total=s.look_away_total)
    flag = {"ts": now_iso(), "message": message, "command": command, "look_aways": s.look_away_total}
    await broadcast(s.session_id, "integrity_flag", flag)
    s.integrity_flags.append({**flag, "question_id": s.current_qid})
    log.info("session %s: suspected cheating (%d look-aways)", s.session_id, s.look_away_total)
    return flag
