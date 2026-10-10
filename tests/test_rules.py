"""Pure-function tests: decision rules, coach flags, specs, prompts, report assembly."""

import pytest

from backend.brain import fake, prompts
from backend.brain.history import normalize_history, stats_line
from backend.brain.interviewer import resolve_action
from backend.brain.models import QuestionFeedback, ReportDraft
from backend.brain.report import fallback_report, finalize_draft
from backend.brain.specs import fallback_questions, get_spec, normalize_type
from backend.brain.state import SessionMeta, SessionState
from backend.brain.watcher import flag_message, level_for
from backend.contracts import Baseline, QuestionStats, WindowStats


def qstats(qid="q1", **kw) -> QuestionStats:
    base = dict(question_id=qid, duration_s=20.0, n_samples=20, hr_mean=84.0, hr_peak=91.0, hr_delta_pct=17.0,
                br_mean=16.0, focus_mean=0.62, stress_mean=46.0, stress_peak=78.0, peak_offset_s=12.0)
    base.update(kw)
    return QuestionStats(**base)


def window(**kw) -> WindowStats:
    base = dict(seconds=10, n_samples=10, hr_mean=90.0, hr_delta_pct=20.0, br_mean=15.0, br_delta_pct=0.0,
                focus_mean=0.8, dominant_expression="frown", stress=55.0)
    base.update(kw)
    return WindowStats(**base)


def state(type_="Software Engineering") -> SessionState:
    s = SessionState("sid", SessionMeta("sid", "Ada", type_, "Backend Engineer"))
    s.baseline = Baseline(heart_rate=72, breathing_rate=14, focus=0.8, n_samples=30)
    return s


# ---------------------------------------------------------------- resolve_action

@pytest.mark.parametrize("action,text,used,queue,expected", [
    ("next", "", 0, 2, "next"),
    ("next", "", 0, 0, "wrap_up"),                  # nothing left to ask
    ("ease_off", "Let me rephrase.", 0, 2, "ease_off"),
    ("ease_off", "Let me rephrase.", 1, 2, "next"),  # max one follow-up per planned question
    ("push", "", 0, 2, "next"),                      # nothing to say → move on
    ("follow_up", "Example?", 1, 0, "wrap_up"),
    ("wrap_up", "", 0, 3, "next"),                   # can't wrap up with questions left
    ("wrap_up", "", 0, 0, "wrap_up"),
    ("push", "Harder one.", 0, 0, "push"),           # follow-up allowed on the last question
])
def test_resolve_action(action, text, used, queue, expected):
    assert resolve_action(action, text, used, queue) == expected


# ---------------------------------------------------------------- fake decisions

def test_fake_decide_eases_off_on_spike():
    d = fake.decide("I think maybe you would use a cache or something like that for this", qstats(stress_peak=85))
    assert d.action == "ease_off" and d.spoken_text == fake.FAKE_LINES["ease_off"]


def test_fake_decide_pushes_when_calm_and_thorough():
    long = " ".join(["word"] * 30)
    assert fake.decide(long, qstats(stress_mean=20, stress_peak=30)).action == "push"


def test_fake_decide_follow_up_on_short_answer_and_next_otherwise():
    assert fake.decide("not sure", qstats(stress_mean=50, stress_peak=60)).action == "follow_up"
    assert fake.decide(" ".join(["w"] * 20), qstats(stress_mean=50, stress_peak=60)).action == "next"
    assert fake.decide(" ".join(["w"] * 20), None).action == "next"


def test_fake_texts_cover_everything_spoken():
    texts = fake.all_texts()
    assert all(q.text in texts for q in fake.FAKE_QUESTIONS)
    assert all(t in texts for t in fake.FAKE_LINES.values())
    assert len(texts) == len(set(texts))


# ---------------------------------------------------------------- watcher

def test_levels_match_plan_thresholds():
    assert level_for(None) is None
    assert level_for(40) is None
    assert level_for(40.1) == "rising"
    assert level_for(70) == "rising"
    assert level_for(70.1) == "high"


def test_flag_message_names_the_driver():
    assert "Heart rate up 20%" in flag_message(window(), "rising")
    assert "Breathing" in flag_message(window(hr_delta_pct=2, br_delta_pct=35), "high")
    assert "focus" in flag_message(window(hr_delta_pct=0, focus_mean=0.3), "high", baseline_focus=0.8).lower()
    assert "Stress at 55" in flag_message(window(hr_delta_pct=None, br_delta_pct=None, focus_mean=None), "rising")


# ---------------------------------------------------------------- specs

@pytest.mark.parametrize("label,key", [
    ("Software Engineering", "swe"), ("Behavioral", "behavioral"), ("Class Presentation", "presentation"),
    # the slugs the frontend actually sends (frontend/src/types.ts INTERVIEW_TYPES)
    ("software_engineering", "swe"), ("class_presentation", "presentation"),
    ("Quick Demo", "demo"), ("demo", "demo"), ("", "swe"), ("something else", "swe"),
])
def test_normalize_type(label, key):
    assert normalize_type(label) == key


def test_fallback_questions_follow_difficulty_curve():
    for key in ("swe", "behavioral", "presentation", "demo"):
        spec = get_spec(key)
        qs = fallback_questions(spec)
        assert [q.difficulty for q in qs] == list(spec.difficulties)
        assert all(len(q.text.split()) <= 30 for q in qs)
    assert get_spec("Quick Demo").n_questions == 3


# ---------------------------------------------------------------- prompts

def test_decide_context_has_every_section():
    s = state()
    s.current_planned = fallback_questions(s.spec)[1]
    s.turns, s.actions = 2, ["next", "push"]
    ctx = prompts.decide_context(s, "main", "Why?", "x" * 900, qstats(), window())
    for needle in ("ROLE: Backend Engineer", "TURN: 2/7", "BASELINE: HR 72 bpm", "difficulty 3",
                   "HR mean 84 (+17% vs baseline)", "peak 78 at 12s in", "RIGHT NOW (last 10s): stress 55",
                   "expression frown", "REMAINING PLANNED QUESTIONS: 0", "PREVIOUS DECISIONS: next, push"):
        assert needle in ctx, needle
    assert "x" * 601 not in ctx   # transcript truncated


def test_stats_line_handles_missing_vitals():
    assert "no usable vitals" in stats_line(None)
    assert "no usable vitals" in stats_line(qstats(n_samples=0))


# ---------------------------------------------------------------- history + report

def history():
    return [
        {"question_id": "c", "idx": 0, "kind": "calibration", "text": "How's it going?", "transcript": "Good",
         "stats": qstats("c", stress_mean=None, stress_peak=None).model_dump()},
        {"idx": 1, "kind": "main", "text": "Q one", "transcript": "short answer",
         "stats": qstats("q1", stress_mean=30, stress_peak=45)},
        {"question_id": "q2", "idx": 2, "kind": "follow_up", "text": "Q two", "transcript": " ".join(["w"] * 40),
         "stats": qstats("q2", stress_mean=60, stress_peak=88, peak_offset_s=9)},
    ]


def test_normalize_history_accepts_dicts_and_models():
    items = normalize_history(history())
    assert [it.question_id for it in items] == ["c", "q1", "q2"]   # q1 id recovered from stats
    assert [it.label for it in items] == ["Calibration", "Q1", "Q2"]


def test_fallback_report_cites_numbers_and_skips_calibration():
    s = state()
    s.flags = [{"ts": "t", "question_id": "q2", "level": "high", "stress": 80.0, "message": "m"}]
    r = fallback_report(s, normalize_history(history()))
    assert r.composure_score == 55                      # 100 - mean(30, 60)
    assert [f.question_id for f in r.per_question] == ["q1", "q2"]
    assert all(any(ch.isdigit() for ch in f.body_signal) for f in r.per_question)
    assert "Q2" in r.hardest_moment and "88" in r.hardest_moment
    assert len(r.top_tips) == 3
    assert r.coach_flags[0].question_id == "q2"
    assert r.generated_by == "fallback"


def test_fallback_report_without_vitals():
    items = normalize_history([{"question_id": "q1", "idx": 1, "kind": "main", "text": "Q", "transcript": "",
                                "stats": qstats(n_samples=0, stress_mean=None, stress_peak=None)}])
    r = fallback_report(state(), items)
    assert r.composure_score == 50 and "No usable vitals" in r.per_question[0].body_signal


def test_finalize_draft_repairs_gemini_output():
    items = normalize_history(history())
    draft = ReportDraft(
        composure_score=140,
        summary="Solid overall.",
        hardest_moment="Q2 was tough.",                            # no numbers → replaced
        per_question=[
            QuestionFeedback(question_id="q2", what_happened="ok", body_signal="you seemed tense", advice="a"),
            QuestionFeedback(question_id="bogus", what_happened="x", body_signal="1", advice="x"),
        ],
        top_tips=["one", "  "],
    )
    r = finalize_draft(state(), items, draft)
    assert r.composure_score == 70                                  # 55 from stats + at most 15
    assert [f.question_id for f in r.per_question] == ["q1", "q2"]  # bogus dropped, q1 filled in, order kept
    assert any(ch.isdigit() for ch in r.per_question[1].body_signal)
    assert "88" in r.hardest_moment
    assert len(r.top_tips) == 3 and r.top_tips[0] == "one"
    assert r.generated_by == "gemini"


def test_finalize_draft_without_vitals_does_not_claim_calm():
    items = normalize_history([{"question_id": "q1", "idx": 1, "kind": "main", "text": "Q", "transcript": "A",
                                "stats": qstats(n_samples=0, stress_mean=None, stress_peak=None)}])
    draft = ReportDraft(composure_score=100, summary="s", hardest_moment="none", top_tips=["a", "b", "c"],
                        per_question=[QuestionFeedback(question_id="q1", what_happened="w",
                                                       body_signal="No usable vitals.", advice="a")])
    assert finalize_draft(state(), items, draft).composure_score == 50
