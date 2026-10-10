"""Pure-function tests: decision rules, coach flags, specs, prompts, report assembly."""

import pytest

from backend.brain import fake, lines, prompts
from backend.brain.history import normalize_history, stats_line
from backend.brain.interviewer import final_coaching, resolve_action, spoken
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

@pytest.mark.parametrize("action,text,followups,redirects,queue,expected", [
    ("next", "", 0, 0, 2, "next"),
    ("next", "", 0, 0, 0, "wrap_up"),                    # nothing left to ask
    ("ease_off", "Let me rephrase.", 0, 0, 2, "ease_off"),
    ("ease_off", "Let me rephrase.", 1, 0, 2, "next"),   # max one follow-up per planned question
    ("push", "", 0, 0, 2, "next"),                       # nothing to say → move on
    ("follow_up", "Example?", 1, 0, 0, "wrap_up"),
    ("wrap_up", "", 0, 0, 3, "next"),                    # can't wrap up with questions left
    ("wrap_up", "", 0, 0, 0, "wrap_up"),
    ("push", "Harder one.", 0, 0, 0, "push"),            # follow-up allowed on the last question
    ("redirect", "Back to the question?", 0, 0, 2, "redirect"),
    ("redirect", "Back to the question?", 1, 0, 2, "redirect"),   # separate allowance from follow-ups
    ("redirect", "Back to the question?", 0, 1, 2, "next"),       # max one redirect per planned question
    ("redirect", "Back to the question?", 0, 1, 0, "wrap_up"),
    ("redirect", "", 0, 0, 2, "next"),
    ("follow_up", " ".join(["word"] * 80), 0, 0, 2, "next"),     # far too long to be a real turn
    ("coach", "Stepping out: that would cost you. Say X. Back in: next?", 0, 0, 2, "coach"),
    ("coach", "Stepping out: that would cost you.", 1, 1, 2, "coach"),   # no allowance: coaching always moves on
    ("coach", "Stepping out: that would cost you.", 0, 0, 0, "coach"),   # last question: feedback, then closing
    ("coach", "", 0, 0, 2, "next"),
    ("coach", "", 0, 0, 0, "wrap_up"),
    ("coach", " ".join(["word"] * 120), 0, 0, 2, "coach"),      # coaching gets a longer allowance
    ("coach", " ".join(["word"] * 150), 0, 0, 2, "next"),
])
def test_resolve_action(action, text, followups, redirects, queue, expected):
    assert resolve_action(action, text, followups, redirects, queue) == expected


def test_spoken_rejects_empty_and_runaway_turns():
    assert spoken("  Fine.  Next one? ") == "Fine. Next one?"
    assert spoken("") == "" and spoken(" ".join(["w"] * 71)) == ""


# ---------------------------------------------------------------- fake decisions

def test_fake_decide_eases_off_on_spike():
    d = fake.decide("I think maybe you would use a cache or something like that for this", qstats(stress_peak=85))
    assert d.action == "ease_off" and d.spoken_text == fake.FAKE_LINES["ease_off"]


def test_fake_decide_pushes_when_calm_and_thorough():
    long = " ".join(["word"] * 30)
    assert fake.decide(long, qstats(stress_mean=20, stress_peak=30)).action == "push"


def test_fake_decide_follow_up_on_short_answer_and_next_otherwise():
    assert fake.decide("I would use a cache", qstats(stress_mean=50, stress_peak=60)).action == "follow_up"
    assert fake.decide(" ".join(["w"] * 20), qstats(stress_mean=50, stress_peak=60)).action == "next"
    assert fake.decide(" ".join(["w"] * 20), None).action == "next"


def test_fake_decide_coaches_wrong_answers_and_reasks_empty_ones():
    q = "Walk me through how you'd debug an API that suddenly got ten times slower."
    d = fake.decide("I really like pizza and playing basketball with my friends on weekends.", None, q, "main",
                    next_question="Next one?")
    assert d.action == "coach" and d.spoken_text == fake.coach_turn("Next one?")
    assert d.spoken_text.startswith("Let me step out of the interview") and d.spoken_text.endswith("Next one?")
    assert fake.decide("not sure", None, q, "main").action == "coach"                # very weak
    assert fake.decide("", None, q, "main").spoken_text == fake.FAKE_LINES["redirect"]
    assert fake.decide("I would debug the slower endpoints first by checking the traces.", None, q, "main").action == "next"
    assert not fake.off_topic(q, "pizza")                                              # too short to judge
    assert fake.decide("I really like pizza and basketball with friends on weekends.", None, q,
                       "follow_up").action == "next"                                   # follow-ups aren't checked


def test_fake_texts_cover_everything_spoken():
    texts = fake.all_texts()
    assert all(q.text in texts for q in fake.FAKE_QUESTIONS)
    assert all(t in texts for t in fake.FAKE_LINES.values())
    assert all(t in texts for t in lines.fixed_lines())
    assert all(fake.coach_turn(q.text) in texts for q in fake.FAKE_QUESTIONS[1:])
    assert fake.coach_turn(None) in texts
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
    s.queue = fallback_questions(s.spec)[2:3]
    s.redirects_on_current = 1
    s.exchanges = [
        {"kind": "calibration", "text": "Tell me about yourself.", "transcript": "I like distributed systems."},
        {"kind": "main", "text": "Q1?", "transcript": "An answer about caching."},
        {"kind": "main", "text": "Why?", "transcript": "x" * 900},          # the answer being judged
    ]
    ctx = prompts.decide_context(s, "main", "Why?", "x" * 900, qstats(), window())
    for needle in ("ROLE: Backend Engineer", "TURN: 2/7", "BASELINE: HR 72 bpm", "difficulty 3",
                   "HR mean 84 (+17% vs baseline)", "peak 78 at 12s in", "RIGHT NOW (last 10s): stress 55",
                   "expression frown", "PREVIOUS DECISIONS: next, push", "REDIRECTS USED ON THIS QUESTION: 1/1",
                   'CANDIDATE: "I like distributed systems."', 'CANDIDATE: "An answer about caching."',
                   "REMAINING PLANNED QUESTIONS (including the next one): 1",
                   f'NEXT PLANNED QUESTION: "{s.queue[0].text}"'):
        assert needle in ctx, needle
    assert "x" * 601 not in ctx   # transcript truncated
    assert 'INTERVIEWER: "Why?"' not in ctx   # the judged answer isn't repeated in the history block


def test_open_context_includes_small_talk_and_first_question():
    s = state("Class Presentation")
    s.exchanges = [{"kind": "calibration", "text": "Tell me about yourself.", "transcript": "I study ecology."}]
    first = fallback_questions(s.spec)[0]
    ctx = prompts.open_context(s, first)
    assert "PRESENTATION TOPIC: Backend Engineer" in ctx and 'CANDIDATE: "I study ecology."' in ctx
    assert f'FIRST QUESTION: "{first.text}"' in ctx


def test_next_planned_line_when_queue_empty():
    s = state()
    s.current_planned = fallback_questions(s.spec)[4]
    assert "NEXT PLANNED QUESTION: none" in prompts.decide_context(s, "main", "Q", "A", None, None)


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


def test_fallback_report_describes_coached_and_unclear_answers():
    s = state()
    s.adaptations = [{"question_id": "q1", "action": "coach", "answer_quality": 1, "rationale": "wrong"},
                     {"question_id": "q2", "action": "redirect", "answer_quality": 1, "rationale": "garbled"}]
    r = fallback_report(s, normalize_history(history()))
    q1, q2 = (next(f for f in r.per_question if f.question_id == q) for q in ("q1", "q2"))
    assert "missed what the question was asking" in q1.what_happened and q1.advice.startswith("Use the live coaching")
    assert "didn't come through clearly" in q2.what_happened
    assert r.top_tips[0].startswith("Use the live coaching")


def test_prompts_carry_the_voice_style():
    for system in (prompts.DECIDE_SYSTEM, prompts.OPEN_SYSTEM):
        assert '"got it", "I see", "I understand"' in system
        assert "never \"let us\"" in system
    assert "step out" in prompts.DECIDE_SYSTEM.lower() and "real interview" in prompts.DECIDE_SYSTEM


@pytest.mark.parametrize("raw,want", [
    # what Gemini actually produced live on the last question: re-asked after the step back in
    ("Let me step out of the interview for a second. In a real interview, restarting the server is a red flag. "
     "Instead, you should propose a circuit breaker or tuning the eviction policy. Okay, back in the interview. "
     "How would you handle that cache thrashing without horizontal scaling?",
     "Let me step out of the interview for a second. In a real interview, restarting the server is a red flag. "
     "Instead, you should propose a circuit breaker or tuning the eviction policy."),
    ("Stepping out: that answer would cost you. Lead with the tradeoff. Back to it.",
     "Stepping out: that answer would cost you. Lead with the tradeoff."),
    ("Stepping out: that answer would cost you. Lead with the tradeoff.",
     "Stepping out: that answer would cost you. Lead with the tradeoff."),
    ("Why would you say that?", "Why would you say that?"),   # never cut a turn down to nothing
])
def test_final_coaching_ends_at_the_model_answer(raw, want):
    assert final_coaching(raw) == want


def test_small_talk_context_names_what_they_are_interviewing_for():
    s = state()
    s.exchanges = [{"kind": "calibration", "text": lines.CALIBRATION_LINE, "transcript": "I'm a CS major at Rutgers."}]
    ctx = prompts.small_talk_context(s)
    assert "INTERVIEWING FOR: Backend Engineer" in ctx and 'CANDIDATE: "I\'m a CS major at Rutgers."' in ctx
    assert "PRESENTATION TOPIC:" in prompts.small_talk_context(state("Class Presentation"))
    assert "major" in prompts.SMALL_TALK_SYSTEM and '"got it"' in prompts.SMALL_TALK_SYSTEM
