"""Normalizes Data's get_session_history() output and formats numbers for prompts and reports."""

from dataclasses import dataclass
from typing import Any, Optional

from backend.contracts import Baseline, QuestionStats, WindowStats


@dataclass
class HistoryItem:
    question_id: str
    idx: int
    kind: str
    text: str
    transcript: str
    stats: Optional[QuestionStats]
    label: str = ""   # "Warm-up", "Q2", "Q2 follow-up": matches "Question 2 of 5" on the live screen


def _coerce(model, raw: Any):
    """Accept the contract model, a dict, or a look-alike model. Returns None if it doesn't fit."""
    if raw is None or isinstance(raw, model):
        return raw
    try:
        return model.model_validate(raw if isinstance(raw, dict) else raw.model_dump())
    except Exception:
        return None


def as_question_stats(raw: Any) -> Optional[QuestionStats]:
    return _coerce(QuestionStats, raw)


def as_window_stats(raw: Any) -> Optional[WindowStats]:
    return _coerce(WindowStats, raw)


def as_baseline(raw: Any) -> Optional[Baseline]:
    return _coerce(Baseline, raw)


def normalize_history(history: list) -> list[HistoryItem]:
    """Accepts dicts (or models) shaped like {question_id?, idx, kind, text, transcript, stats}."""
    out = []
    number = 0   # planned-question number, as shown live ("Question 2 of 5"); follow-ups keep it
    for raw in history or []:
        item = raw if isinstance(raw, dict) else getattr(raw, "model_dump", lambda: {})()
        stats = as_question_stats(item.get("stats"))
        qid = item.get("question_id") or item.get("id") or (stats.question_id if stats else None)
        if not qid:
            continue
        kind = str(item.get("kind") or "main")
        if kind == "main":
            number += 1
        label = ("Warm-up" if kind == "calibration" else f"Q{max(number, 1)}"
                 + (" follow-up" if kind == "follow_up" else ""))
        out.append(HistoryItem(
            question_id=str(qid),
            idx=int(item.get("idx") or 0),
            kind=kind,
            text=str(item.get("text") or ""),
            transcript=str(item.get("transcript") or ""),
            stats=stats,
            label=label,
        ))
    return out


def num(x: Optional[float], digits: int = 0, suffix: str = "") -> str:
    if x is None:
        return "n/a"
    return f"{x:.{digits}f}{suffix}"


def pct(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return f"{round(x) or 0:+d}%"   # round first so -0.3 reads "+0%", not "-0%"


def stats_line(st: Optional[QuestionStats]) -> str:
    """One compact line of body data, e.g. for the decide_next and report prompts."""
    if st is None or st.n_samples == 0:
        return "no usable vitals (face not detected or sensor offline)"
    peak_at = f" at {st.peak_offset_s:.0f}s in" if st.peak_offset_s is not None else ""
    return (f"HR mean {num(st.hr_mean)} ({pct(st.hr_delta_pct)} vs baseline), peak {num(st.hr_peak)}, "
            f"BR mean {num(st.br_mean)}, stress mean {num(st.stress_mean)}, "
            f"peak {num(st.stress_peak)}{peak_at}, focus {num(st.focus_mean, 2)}, "
            f"answer length {st.duration_s:.0f}s")
