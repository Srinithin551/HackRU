"""Chart notes: one per (question, variable), always complete, never blocking the verdict."""

import pytest

from backend.brain.history import HistoryItem
from backend.brain.insights import VARIABLES, fallback_notes
from backend.contracts import QuestionStats


def item(qid="q1", idx=1, **stats_kw) -> HistoryItem:
    base = dict(question_id=qid, duration_s=24.0, n_samples=24, hr_mean=88.0, hr_peak=97.0,
                hr_delta_pct=18.0, br_mean=17.0, focus_mean=None, stress_mean=52.0,
                stress_peak=74.0, peak_offset_s=11.0)
    base.update(stats_kw)
    return HistoryItem(question_id=qid, idx=idx, kind="main", text="Tell me about a project.",
                       transcript="I built a thing.", stats=QuestionStats(**base))


def test_fallback_covers_every_variable_for_every_answered_question():
    notes = fallback_notes([item("q1", 1), item("q2", 2)])
    for qid in ("q1", "q2"):
        got = {n.variable for n in notes if n.question_id == qid}
        assert got == set(VARIABLES)


def test_fallback_notes_cite_real_numbers():
    heart = next(n for n in fallback_notes([item()]) if n.variable == "heart_rate")
    assert "88" in heart.detail and "97" in heart.detail
    composure = next(n for n in fallback_notes([item()]) if n.variable == "composure")
    assert "74" in composure.detail and "11s" in composure.detail


def test_questions_without_vitals_get_no_notes_rather_than_invented_ones():
    assert fallback_notes([item(n_samples=0)]) == []


def test_variables_missing_from_the_data_are_skipped():
    notes = fallback_notes([item(br_mean=None, stress_mean=None, stress_peak=None)])
    assert {n.variable for n in notes} == {"heart_rate"}


@pytest.mark.parametrize("field", ["headline", "detail"])
def test_every_note_is_populated(field):
    assert all(getattr(n, field).strip() for n in fallback_notes([item()]))
