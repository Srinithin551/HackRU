"""Per-session state for the interview loop, plus the in-memory registry of sessions."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from backend.brain.models import PlannedQuestion
from backend.brain.specs import DEFAULT_ROLE, InterviewSpec, get_spec
from backend.contracts import Baseline, WindowStats


@dataclass
class SessionMeta:
    """What the setup page sent. Kept here because Data has no get_session() query."""
    session_id: str
    user_name: str
    interview_type: str   # raw label from the frontend
    role_title: str

    @property
    def spec(self) -> InterviewSpec:
        return get_spec(self.interview_type)

    @property
    def role(self) -> str:
        return self.role_title.strip() or DEFAULT_ROLE[self.spec.key]


@dataclass
class SessionState:
    """Everything the running loop knows about one session. Mutated only by the loop's own task (and
    end_requested by routes._on_client_event), so no locking is needed."""
    session_id: str
    meta: SessionMeta

    phase: str = "idle"           # calibrating | asking | listening | thinking | done
    idx: int = 0                  # DB idx of the latest question (0 = calibration)
    turns: int = 0                # main + follow-up questions asked
    planned_number: int = 0       # 1-based number of the current planned question ("Q 2 of 5")
    total_planned: int = 0
    queue: list[PlannedQuestion] = field(default_factory=list)
    current_planned: Optional[PlannedQuestion] = None
    followups_on_current: int = 0    # ease_off / push / follow_up used on the current planned question
    redirects_on_current: int = 0    # redirects (off-topic answers) used on the current planned question

    current_qid: Optional[str] = None
    current_text: str = ""
    current_kind: str = ""

    baseline: Optional[Baseline] = None
    live_window: Optional[WindowStats] = None   # latest watcher reading, decide_next sees it
    flags: list[dict] = field(default_factory=list)        # CoachFlag dicts
    adaptations: list[dict] = field(default_factory=list)  # Adaptation dicts
    actions: list[str] = field(default_factory=list)       # every resolved action, in order
    exchanges: list[dict] = field(default_factory=list)    # {kind, text, transcript} for every answered turn
    parting_words: str = ""                                # Gemini's reaction to the last answer, said before the closing line
    report: Optional[dict] = None                          # the final report, also served from here if the DB save fails

    end_requested: bool = False
    started: float = field(default_factory=time.monotonic)
    finished_at: Optional[float] = None   # time.monotonic() when session_ended was sent

    @property
    def spec(self) -> InterviewSpec:
        return self.meta.spec

    def elapsed(self) -> float:
        return time.monotonic() - self.started


# session_id -> data. In process memory only: a backend restart forgets the running loops (Data's
# tables keep the questions, vitals and saved reports, and routes.start_session can rebuild a meta from
# them). Finished sessions are dropped by prune_finished(), FINISHED_TTL_S after they end.
metas: dict[str, SessionMeta] = {}
states: dict[str, SessionState] = {}
tasks: dict[str, asyncio.Task] = {}

# Long enough to reopen or reload a report (served from memory if the DB save failed), short enough
# that a backend left running all weekend doesn't keep every session's transcript in RAM.
FINISHED_TTL_S = 3600.0


def prune_finished(now: Optional[float] = None) -> list[str]:
    """Forget sessions whose loop finished more than FINISHED_TTL_S ago. Returns the pruned ids, so
    the caller can also clear the WebSocket hub's per-session state."""
    now = time.monotonic() if now is None else now
    gone = [sid for sid, s in states.items()
            if s.finished_at is not None and now - s.finished_at > FINISHED_TTL_S
            and (sid not in tasks or tasks[sid].done())]
    for sid in gone:
        states.pop(sid, None)
        metas.pop(sid, None)
        tasks.pop(sid, None)
    return gone
