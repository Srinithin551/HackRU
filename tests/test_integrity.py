"""Integrity watcher: only repeated look-aways while answering raise "suspected of cheating"."""

import asyncio

from backend.brain import integrity
from backend.brain.history import normalize_history
from backend.brain.report import fallback_report
from backend.brain.state import SessionMeta, SessionState


def answering(qid="q1") -> SessionState:
    s = SessionState("sid", SessionMeta("sid", "Ada", "Software Engineering", "Backend Engineer"))
    s.phase, s.current_qid = "listening", qid
    return s


def look(s: SessionState, at: float, qid="q1"):
    return asyncio.run(integrity.on_look_away(s, qid, now=at))


def test_one_or_two_glances_are_not_cheating():
    s = answering()
    assert look(s, 0.0) is None
    assert look(s, 10.0) is None
    assert s.integrity_flags == [] and s.look_away_total == 2


def test_repeated_look_aways_raise_a_suspicion():
    s = answering()
    look(s, 0.0)
    look(s, 10.0)
    flag = look(s, 20.0)
    assert flag is not None
    assert "suspect" in flag["message"].lower() and "cheating" in flag["message"].lower()
    assert flag["command"] and flag["look_aways"] == 3
    assert s.integrity_flags[0]["question_id"] == "q1"


def test_look_aways_spread_out_never_add_up():
    s = answering()
    for t in (0.0, 70.0, 140.0, 210.0):   # each one outside the previous one's window
        assert look(s, t) is None
    assert s.integrity_flags == []


def test_only_counts_while_answering_the_current_question():
    s = answering()
    s.phase = "asking"
    for t in (0.0, 1.0, 2.0):
        assert look(s, t) is None
    s.phase = "listening"
    for t in (3.0, 4.0, 5.0):
        assert look(s, t, qid="stale") is None
    assert s.look_away_total == 0


def test_a_second_suspicion_needs_a_new_run_and_never_repeats_its_words():
    s = answering()
    first = [look(s, t) for t in (0.0, 1.0, 2.0)][-1]
    assert look(s, 3.0) is None and look(s, 4.0) is None   # count started over
    second = look(s, 5.0)
    assert first and second and first["message"] != second["message"]
    assert second["look_aways"] == 6


def test_report_carries_the_suspicion():
    s = answering()
    clean = fallback_report(s, normalize_history([]))
    assert clean.suspected_cheating is False and clean.integrity_flags == []
    for t in (0.0, 1.0, 2.0):
        look(s, t)
    r = fallback_report(s, normalize_history([]))
    assert r.suspected_cheating is True and r.look_aways == 3
    assert r.integrity_flags[0].question_id == "q1"
