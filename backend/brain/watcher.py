"""Stress watcher: every few seconds while the user is answering, read Data's live window and raise
on-screen coach flags. Templates only (no Gemini) so flags appear within a second of the reading.
Coach flags are on screen only; the interviewer never interrupts with voice.
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
    if stress is None:
        return None
    return "high" if stress > HIGH else "rising" if stress > RISING else None


def flag_message(w: WindowStats, level: str, baseline_focus: float = 0.75) -> str:
    """Name whichever signal is driving the stress, using the same scale as the stress formula."""
    hr = w.hr_delta_pct or 0.0
    br = w.br_delta_pct or 0.0
    focus_drop = (baseline_focus - w.focus_mean) if w.focus_mean is not None else 0.0
    drivers = {"hr": hr / 25, "br": br / 40, "focus": focus_drop / 0.5}
    driver = max(drivers, key=drivers.get)
    if drivers[driver] <= 0:
        driver = None
    high = level == "high"

    if driver == "hr":
        return (f"Heart rate up {hr:.0f}% from your baseline. Pause, breathe, slow down." if high
                else f"Heart rate up {hr:.0f}% from your baseline. Slow down a little.")
    if driver == "br":
        return (f"Breathing {br:.0f}% faster than baseline. Take one slow breath." if high
                else f"Breathing is picking up ({br:+.0f}%). Exhale slowly.")
    if driver == "focus":
        return ("Focus is dropping. Look back at the camera and reset." if high
                else "Eyes drifting. Bring your focus back to the question.")
    return f"Stress at {w.stress:.0f}. Slow down and take a breath."


async def check_once(s: SessionState, last_flag_at: float) -> float:
    """One watcher tick. Returns the (possibly updated) time of the last flag."""
    w = as_window_stats(await data.get_window_stats(s.session_id, WINDOW_S))
    if w is None:
        return last_flag_at
    s.live_window = w
    level = level_for(w.stress)
    if level is None or w.n_samples < MIN_SAMPLES or time.monotonic() - last_flag_at < COOLDOWN_S:
        return last_flag_at
    flag = {"ts": now_iso(), "level": level, "message": flag_message(w, level, s.baseline.focus if s.baseline else 0.75)}
    await broadcast(s.session_id, "coach_flag", flag)
    s.flags.append({**flag, "question_id": s.current_qid, "stress": round(w.stress, 1)})
    return time.monotonic()


async def watch_stress(s: SessionState) -> None:
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
