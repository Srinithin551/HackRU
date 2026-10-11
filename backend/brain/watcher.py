"""Stress watcher: every few seconds while the user is answering, read Data's live window and raise
on-screen coach flags ("interrupts"). Templates only (no Gemini) so flags appear within a second of
the reading. Coach flags are on screen only; the interviewer never interrupts with voice.

Each flag carries two parts:
  message  what the interviewer (Judge) observed, citing the number that triggered it
  command  the single thing the candidate must do, rendered by the frontend as "Please <command>."

The frontend makes the candidate acknowledge the command before the interview moves on, so a flag that
repeated itself word for word would read as a bug. Every (driver, level) therefore has several
phrasings and `pick_variant` never reuses one while an unused one remains.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from backend.brain.history import as_window_stats
from backend.brain.state import SessionState
from backend.contracts import WindowStats
from backend.data import queries as data
from backend.ws import broadcast

log = logging.getLogger("brain.watcher")

INTERVAL_S = 3.0
WINDOW_S = 10
COOLDOWN_S = 12.0
MIN_SAMPLES = 3          # don't flag off one or two readings
RISING, HIGH = 40, 70    # PLAN.md thresholds: < 40 calm, 40 to 70 rising, > 70 high


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def level_for(stress: Optional[float]) -> Optional[str]:
    """'high' (> 70), 'rising' (40 to 70), or None for calm (< 40) / no reading. 40 itself is rising:
    the same boundaries as metrics.stress_level and the frontend's stressLevel()."""
    if stress is None:
        return None
    return "high" if stress > HIGH else "rising" if stress >= RISING else None


def driver_for(w: WindowStats) -> Optional[str]:
    """Which signal is driving the stress, on the same scale as the stress formula."""
    hr = w.hr_delta_pct or 0.0
    br = w.br_delta_pct or 0.0
    drivers = {"hr": hr / 25, "br": br / 40}
    driver = max(drivers, key=drivers.get)
    return driver if drivers[driver] > 0 else None


# (message, command) per driver and level. `message` may use {hr}, {br} and {stress} (already
# rounded). `command` is phrased to follow the word "Please".
VARIANTS: dict[tuple[Optional[str], str], list[tuple[str, str]]] = {
    ("hr", "rising"): [
        ("Your pulse climbs {hr}% above your own calm. I have noticed.",
         "slow your pace and finish the sentence you started"),
        ("{hr}% over your baseline. Your heart answers faster than your mouth.",
         "take one slow breath before your next point"),
        ("The blood quickens, {hr}% beyond where you began.",
         "unclench your jaw and speak a little more slowly"),
    ],
    ("hr", "high"): [
        ("Your heart races {hr}% past your baseline. Steady yourself before the court.",
         "stop, breathe out slowly, then continue"),
        ("A pulse {hr}% above your calm. This stand has rattled steadier witnesses.",
         "pause for two full seconds before you say another word"),
        ("{hr}% over baseline and still climbing. I am watching closely.",
         "plant both feet, breathe out, and begin that thought again"),
    ],
    ("br", "rising"): [
        ("Your breath shortens, {br}% quicker than your calm.",
         "let one slow breath out before continuing"),
        ("Breathing {br}% faster than when you entered.",
         "breathe low into your chest, not high in your throat"),
        ("The air moves {br}% quicker through you than it should.",
         "finish this sentence, then take one deliberate breath"),
    ],
    ("br", "high"): [
        ("Your breathing runs {br}% above your calm. You are rushing.",
         "stop speaking and take one long breath out"),
        ("{br}% faster breath than your baseline. The whole courtroom can hear it.",
         "breathe out fully, then answer in shorter sentences"),
        ("Your breath has outrun your thinking, {br}% above calm.",
         "pause, exhale, and give us only your strongest point"),
    ],
    (None, "rising"): [
        ("Your composure sits at {stress} out of a hundred.",
         "slow down and take one steady breath"),
        ("I read {stress} on you. Not yet alarming, but rising.",
         "settle your shoulders and keep your pace even"),
    ],
    (None, "high"): [
        ("Your composure has broken to {stress} out of a hundred.",
         "stop, breathe, and begin that answer again"),
        ("{stress} out of a hundred. The pressure of the stand has got to you.",
         "take one slow breath and speak more slowly than feels natural"),
    ],
}


def pick_variant(driver: Optional[str], level: str, used: set[str]) -> tuple[str, str]:
    """A phrasing for this (driver, level) that hasn't been used yet in this session.

    Falls back to the (None, level) pool, then to the first phrasing, so a long session can repeat
    only after genuinely exhausting every variant.
    """
    pools = [VARIANTS.get((driver, level), []), VARIANTS.get((None, level), [])]
    for pool in pools:
        for message, command in pool:
            if message not in used:
                return message, command
    flat = [v for pool in pools for v in pool]
    return flat[0] if flat else ("I am watching you closely.", "take one steady breath")


def flag_for(w: WindowStats, level: str, used: set[str]) -> tuple[str, str]:
    """(message, command) for this reading, with the numbers filled in."""
    driver = driver_for(w)
    template, command = pick_variant(driver, level, used)
    used.add(template)
    message = template.format(
        hr=f"{w.hr_delta_pct:.0f}" if w.hr_delta_pct is not None else "—",
        br=f"{w.br_delta_pct:.0f}" if w.br_delta_pct is not None else "—",
        stress=f"{w.stress:.0f}" if w.stress is not None else "—",
    )
    return message, command


async def check_once(s: SessionState, last_flag_at: float) -> float:
    """One watcher tick. Returns the (possibly updated) time of the last flag."""
    w = as_window_stats(await data.get_window_stats(s.session_id, WINDOW_S))
    if w is None:
        return last_flag_at
    s.live_window = w
    level = level_for(w.stress)
    if level is None or w.n_samples < MIN_SAMPLES or time.monotonic() - last_flag_at < COOLDOWN_S:
        return last_flag_at
    message, command = flag_for(w, level, s.used_flag_texts)
    flag = {"ts": now_iso(), "level": level, "message": message, "command": command}
    await broadcast(s.session_id, "coach_flag", flag)
    s.flags.append({**flag, "question_id": s.current_qid, "stress": round(w.stress, 1)})
    return time.monotonic()


async def watch_stress(s: SessionState) -> None:
    """Runs until interviewer._run cancels it. Only checks while the user is answering ("listening"),
    so flags never pop up while the interviewer is talking or thinking."""
    last_flag_at = float("-inf")
    while True:
        await asyncio.sleep(INTERVAL_S)
        if s.phase != "listening":
            continue
        try:
            last_flag_at = await check_once(s, last_flag_at)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("watcher tick failed for %s", s.session_id)
