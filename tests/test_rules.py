"""Pure-function tests: decision rules, coach flags, specs, prompts, report assembly."""

import pytest

from backend.brain import fake, lines, prompts
from backend.brain.history import normalize_history, stats_line
from backend.brain.interviewer import resolve_action
from backend.brain.turns import (as_planned, asks_something, covers, final_coaching, planned_repeat,
                                 question_part, split_coaching, spoken, strip_step_markers,
                                 with_step_markers)
from backend.brain.models import QuestionFeedback, ReportDraft
from backend.brain.report import addressed, fallback_report, finalize_draft
from backend.brain.specs import fallback_questions, get_spec, normalize_type
from backend.brain.state import SessionMeta, SessionState
from backend.brain.watcher import VARIANTS, driver_for, flag_for, level_for, pick_variant
from backend.contracts import Baseline, QuestionStats, WindowStats


def qstats(qid="q1", **kw) -> QuestionStats:
    base = dict(question_id=qid, duration_s=20.0, n_samples=20, hr_mean=84.0, hr_peak=91.0, hr_delta_pct=17.0,
                br_mean=16.0, stress_mean=46.0, stress_peak=78.0, peak_offset_s=12.0)
    base.update(kw)
    return QuestionStats(**base)


def window(**kw) -> WindowStats:
    base = dict(seconds=10, n_samples=10, hr_mean=90.0, hr_delta_pct=20.0, br_mean=15.0, br_delta_pct=0.0,
                dominant_expression="frown", stress=55.0)
    base.update(kw)
    return WindowStats(**base)


def state(type_="Software Engineering") -> SessionState:
    s = SessionState("sid", SessionMeta("sid", "Ada", type_, "Backend Engineer"))
    s.baseline = Baseline(heart_rate=72, breathing_rate=14, n_samples=30)
    return s


# ---------------------------------------------------------------- resolve_action

@pytest.mark.parametrize("action,text,followups,redirects,queue,expected", [
    ("next", "", 0, 0, 2, "next"),
    ("next", "", 0, 0, 0, "wrap_up"),                    # nothing left to ask
    ("ease_off", "Let me rephrase: what's the first step?", 0, 0, 2, "ease_off"),
    ("ease_off", "Let me rephrase: what's the first step?", 1, 0, 2, "next"),   # max one follow-up per planned question
    ("push", "", 0, 0, 2, "next"),                       # nothing to say → move on
    ("follow_up", "Example?", 1, 0, 0, "wrap_up"),
    ("wrap_up", "", 0, 0, 3, "next"),                    # can't wrap up with questions left
    ("wrap_up", "", 0, 0, 0, "wrap_up"),
    ("push", "Harder one: what breaks first?", 0, 0, 0, "push"),   # follow-up allowed on the last question
    ("push", "Take a guess at what breaks first.", 0, 0, 2, "push"),   # a request counts as asking
    ("follow_up", "Python's flexibility is hard to beat.", 0, 0, 2, "next"),   # asks nothing: move on
    ("redirect", "Let us circle back to database connections.", 0, 0, 2, "next"),
    ("ease_off", "No worries at all.", 0, 0, 0, "wrap_up"),
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
    ("coach", " ".join(["word"] * 100), 0, 0, 2, "coach"),      # coaching gets a longer allowance
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
    assert d.action == "coach" and d.coaching == fake.FAKE_COACH and d.spoken_text == "Next one?"
    assert d.coaching.startswith("Let me step out of the interview")
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
    assert f"{final_coaching(fake.FAKE_COACH)} {lines.CLOSING_LINE}" in texts
    assert len(texts) == len(set(texts))


# ---------------------------------------------------------------- watcher

def test_levels_match_plan_thresholds():
    assert level_for(None) is None
    assert level_for(39.9) is None
    assert level_for(40) == "rising"      # PLAN.md: 40 to 70 is rising, same as metrics.stress_level
    assert level_for(40.1) == "rising"
    assert level_for(70) == "rising"
    assert level_for(70.1) == "high"


def test_driver_for_names_the_strongest_signal():
    assert driver_for(window()) == "hr"
    assert driver_for(window(hr_delta_pct=2, br_delta_pct=35)) == "br"
    # Nothing above baseline: no driver, so the generic pool is used.
    assert driver_for(window(hr_delta_pct=None, br_delta_pct=None)) is None


def test_flag_cites_the_number_that_triggered_it():
    message, command = flag_for(window(), "rising", set())
    assert "20%" in message and command
    message, _ = flag_for(window(hr_delta_pct=2, br_delta_pct=35), "high", set())
    assert "35%" in message
    message, _ = flag_for(window(hr_delta_pct=None, br_delta_pct=None), "rising", set())
    assert "55" in message


def test_flags_never_repeat_while_an_unused_phrasing_remains():
    """The frontend makes the user acknowledge each interrupt, so a repeated one reads as a bug."""
    used: set[str] = set()
    w = window()
    seen = [flag_for(w, "rising", used)[0] for _ in range(len(VARIANTS[("hr", "rising")]))]
    assert len(set(seen)) == len(seen)


def test_flag_variants_fall_back_to_the_generic_pool_once_exhausted():
    used = {m for m, _ in VARIANTS[("hr", "high")]}
    message, command = pick_variant("hr", "high", used)
    assert (message, command) in VARIANTS[(None, "high")]


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
    assert [it.label for it in items] == ["Warm-up", "Q1", "Q1 follow-up"]   # matches the live screen


def test_fallback_report_cites_numbers_and_skips_calibration():
    s = state()
    s.flags = [{"ts": "t", "question_id": "q2", "level": "high", "stress": 80.0, "message": "m"}]
    r = fallback_report(s, normalize_history(history()))
    assert r.composure_score == 55                      # 100 - mean(30, 60)
    assert [f.question_id for f in r.per_question] == ["q1", "q2"]
    assert all(any(ch.isdigit() for ch in f.body_signal) for f in r.per_question)
    assert "Q1 follow-up" in r.hardest_moment and "88" in r.hardest_moment
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


@pytest.mark.parametrize("raw,coaching,question", [
    ("Let me step out. That would cost you. Okay, back in the interview. How would you shard it?",
     "Let me step out. That would cost you. Okay, back in the interview.", "How would you shard it?"),
    ("Let me step out. That would cost you. Say X instead.", "Let me step out. That would cost you. Say X instead.", ""),
])
def test_split_coaching(raw, coaching, question):
    assert split_coaching(raw) == (coaching, question)


def test_reports_say_whether_composure_was_measured():
    measured = fallback_report(state(), normalize_history(history()))
    assert measured.composure_measured is True
    no_vitals = normalize_history([{"question_id": "q1", "idx": 1, "kind": "main", "text": "Q", "transcript": "A",
                                    "stats": qstats(n_samples=0, stress_mean=None, stress_peak=None)}])
    assert fallback_report(state(), no_vitals).composure_measured is False
    draft = ReportDraft(composure_score=90, summary="s", hardest_moment="none", top_tips=["a", "b", "c"],
                        per_question=[])
    assert finalize_draft(state(), no_vitals, draft).composure_measured is False
    assert finalize_draft(state(), normalize_history(history()), draft).composure_measured is True


def test_pct_never_prints_negative_zero():
    from backend.brain.history import pct
    assert [pct(-0.3), pct(0.4), pct(22.4), pct(-7.6), pct(None)] == ["+0%", "+0%", "+22%", "-8%", "n/a"]


def test_labels_follow_the_live_numbering():
    items = normalize_history([
        {"question_id": "c", "idx": 0, "kind": "calibration", "text": "", "transcript": "", "stats": None},
        {"question_id": "a", "idx": 1, "kind": "main", "text": "", "transcript": "", "stats": None},
        {"question_id": "b", "idx": 2, "kind": "follow_up", "text": "", "transcript": "", "stats": None},
        {"question_id": "d", "idx": 3, "kind": "main", "text": "", "transcript": "", "stats": None},
        {"question_id": "e", "idx": 4, "kind": "follow_up", "text": "", "transcript": "", "stats": None},
    ])
    assert [it.label for it in items] == ["Warm-up", "Q1", "Q1 follow-up", "Q2", "Q2 follow-up"]


def test_calm_session_has_no_spike_in_hardest_moment():
    calm = normalize_history([{"question_id": "q1", "idx": 1, "kind": "main", "text": "Q", "transcript": "A",
                               "stats": qstats("q1", stress_mean=2, stress_peak=3)}])
    assert fallback_report(state(), calm).hardest_moment.startswith("No real stress spike")
    draft = ReportDraft(composure_score=97, summary="s", top_tips=["a", "b", "c"], per_question=[],
                        hardest_moment="Your single biggest stress spike happened during Q1, stress 3.")
    assert finalize_draft(state(), calm, draft).hardest_moment.startswith("No real stress spike")


@pytest.mark.parametrize("action,turns_left,queue,answered,expected", [
    ("push", 3, 2, True, "push"),          # room for the follow-up and both planned questions
    ("push", 2, 2, True, "next"),          # a follow-up now would cost a planned question
    ("follow_up", 1, 0, True, "follow_up"),
    ("redirect", 2, 2, True, "next"),
    ("coach", 1, 2, True, "coach"),        # coaching moves on, so it never costs a question
    ("coach", 5, 2, False, "next"),        # silence is never coached
])
def test_resolve_respects_turn_budget_and_silence(action, turns_left, queue, answered, expected):
    assert resolve_action(action, "Some words to say here?", 0, 0, queue, turns_left=turns_left,
                          answered=answered) == expected


# ---------------------------------------------------------------- turn checks (real turns from the 2026-10-10/11 logs)

PLANNED = "Can you walk me through a particularly tricky bug you faced recently and how you figured out what was wrong?"


@pytest.mark.parametrize("turn,planned,same", [
    # reworded, with a reaction in front: still the planned question
    ("Python's familiarity is a strong starting point. Can you walk me through a tricky bug you ran into recently "
     "and how you figured out what was causing it?", PLANNED, True),
    ("Makes sense. Bigger picture now: how would you design a chat app for a million users?",
     "Walk me through how you'd design a chat app that has to support a million users.", True),
    ("Love that you build APIs. Speaking of which, what project are you proudest of?",
     "Tell me about a project you're proud of, in about thirty seconds.", True),
    # a lead-in that asks nothing, and a question that isn't the planned one
    ("Let's tackle our last scenario together.",
     "How would you redesign a distributed database during a live split-brain outage without data loss?", False),
    ("Let's move on to the next question. How do you approach designing a scalable API that needs to handle sudden "
     "traffic spikes?", "How do you handle microservice failures when you need to maintain data consistency "
     "across systems?", False),
])
def test_covers_tells_a_reworded_planned_question_from_another_question(turn, planned, same):
    assert covers(turn, planned) is same
    assert as_planned(turn, planned) == (turn if same else planned)


def test_as_planned_with_no_turn_is_the_planned_text():
    assert as_planned("", PLANNED) == PLANNED


def test_planned_repeat_finds_a_follow_up_that_is_really_a_planned_question():
    queue = ["How would you design a distributed rate limiter for a high-traffic API?",
             "When would you choose a hash table over a balanced search tree despite worst-case collisions?"]
    push = "Good breakdown of chaining and open addressing. Let's step it up. How would you design a distributed rate limiter for a high-traffic API?"
    assert planned_repeat(push, queue) == 0
    assert planned_repeat("Fixed memory versus pointers hits the core. When would you pick a hash table over a "
                          "balanced search tree despite worst-case collisions?", queue) == 1
    real = "Constant time lookups are the draw. Can you give me a situation where you'd pick a balanced tree instead?"
    assert planned_repeat(real, queue) is None
    assert planned_repeat(push, []) is None


@pytest.mark.parametrize("text,asks", [
    ("Which approach did you pick?", True),
    ("Take a guess at what might trigger that worst-case performance.", True),
    ("That's okay, let me put it more simply. Just walk me through the very first step you'd take.", True),
    ("Let's tackle our last scenario together.", False),
    ("Python's flexibility is hard to beat.", False),
])
def test_asks_something(text, asks):
    assert asks_something(text) is asks


@pytest.mark.parametrize("raw,body", [
    ("Let me step out of the interview for a second. That would cost you. Say X. Okay, back in the interview.",
     "That would cost you. Say X."),
    ("Stepping out for a second: refusing to answer ends things. Own one failure.",
     "Refusing to answer ends things. Own one failure."),
    ("Okay, let me pause the interview here. That would cost you. Back to it.", "That would cost you."),
    # never cut real feedback that merely mentions these words
    ("Pausing that long would worry an interviewer. Fail over, then route traffic back to the primary.",
     "Pausing that long would worry an interviewer. Fail over, then route traffic back to the primary."),
])
def test_strip_step_markers(raw, body):
    assert strip_step_markers(raw) == body


def test_with_step_markers_rotates_and_is_empty_without_feedback():
    assert with_step_markers("Say X.", 0, step_in=True) == f"{lines.STEP_OUT_LINES[0]} Say X. {lines.STEP_IN_LINES[0]}"
    assert with_step_markers("Say X.", 1, step_in=False) == f"{lines.STEP_OUT_LINES[1]} Say X."
    n = len(lines.STEP_OUT_LINES)
    assert with_step_markers("Say X.", n, step_in=False) == with_step_markers("Say X.", 0, step_in=False)
    assert with_step_markers("Let me step out of the interview for a second. Okay, back in.", 0, step_in=True) == ""
    assert len(set(lines.STEP_OUT_LINES)) == len(lines.STEP_OUT_LINES) == len(lines.STEP_IN_LINES)


@pytest.mark.parametrize("text,coaching,question", [
    ("That costs you. Say X. How would you shard it?", "That costs you. Say X.", "How would you shard it?"),
    ("Let me step out. Reworded copy. Okay, back in the interview. How would you shard it?", "Original.",
     "How would you shard it?"),
    ("Let me step out of the interview for a second. Reworded copy with no end.", "Original.", ""),
    # seen live: a reworded copy that shares its step out / back in sentences with the coaching
    ("Let me step out of the interview for a second. Reworded. Okay, back in the interview. How would you shard it?",
     "Let me step out of the interview for a second. Original. Okay, back in the interview.", "How would you shard it?"),
    ("How would you shard it?", "That costs you.", "How would you shard it?"),
])
def test_question_part(text, coaching, question):
    assert question_part(text, coaching) == question


def test_decide_context_lists_later_planned_questions():
    s = state()
    s.current_planned = fallback_questions(s.spec)[0]
    s.queue = fallback_questions(s.spec)[1:4]
    ctx = prompts.decide_context(s, "main", "Q", "A", None, None)
    assert f'NEXT PLANNED QUESTION: "{s.queue[0].text}"' in ctx
    later = next(line for line in ctx.splitlines() if line.startswith("LATER PLANNED QUESTIONS"))
    assert s.queue[1].text in later and s.queue[2].text in later and s.queue[0].text not in later
    s.queue = s.queue[:1]
    assert "never ask one as a follow-up): none" in prompts.decide_context(s, "main", "Q", "A", None, None)


def test_decide_prompt_judges_only_this_answer_and_forgives_speech_to_text():
    d = prompts.DECIDE_SYSTEM
    flat = " ".join(d.split())   # the prompt wraps lines
    assert "Earlier turns are context" in flat and '"cash" for "cache"' in flat
    assert "Asking you to repeat or clarify the question is never a reason to coach" in flat
    assert "Never use the NEXT PLANNED QUESTION or a LATER PLANNED QUESTION as your follow-up" in flat
    # the examples Gemini copied word for word are gone; the code adds the step out / back in lines
    for copied in ("Let me step out of the interview for a second.", "Okay, back in the interview.",
                   "Let's switch gears.", "Let's dig in."):
        assert copied not in d and copied not in prompts.OPEN_SYSTEM


# ---------------------------------------------------------------- the candidate's name

@pytest.mark.parametrize("typed,first", [("Ada Lovelace", "Ada"), ("  ada  ", "ada"), ("Ada,", "Ada"), ("", ""), ("   ", "")])
def test_first_name_is_the_first_word_typed(typed, first):
    assert SessionMeta("sid", typed, "Quick Demo", "").first_name == first


def test_openers_greet_them_by_name():
    assert lines.calibration_lines("swe", False, "Ada")[0] == \
        "Thanks for joining me today, Ada. To start, tell me a little about yourself."
    assert lines.calibration_lines("presentation", True, "Ada")[0].startswith(
        "Thanks for joining me today, Ada. Before we begin, walk me through your presentation")
    assert lines.calibration_lines("swe", False, "")[0] == lines.CALIBRATION_LINE
    assert lines.calibration_lines("swe", False, "Ada")[1] == lines.CALIBRATION_EXTRA_LINES["swe"]


def test_bridge_and_report_prompts_know_the_first_name():
    s = state()   # user_name "Ada"
    s.exchanges = [{"kind": "calibration", "text": "Tell me about yourself.", "transcript": "I build APIs."}]
    assert "FIRST NAME: Ada" in prompts.open_context(s, fallback_questions(s.spec)[0])
    assert "FIRST NAME is given, address them by it once" in " ".join(prompts.OPEN_SYSTEM.split())
    assert "FIRST NAME: Ada" in prompts.report_context(s, normalize_history(history()))
    assert "open with it" in prompts.REPORT_SYSTEM
    blank = SessionState("sid", SessionMeta("sid", "", "Software Engineering", "Backend Engineer"))
    assert "FIRST NAME: not given" in prompts.open_context(blank, fallback_questions(blank.spec)[0])


@pytest.mark.parametrize("summary,want", [
    ("You held steady until Q3.", "Ada, you held steady until Q3."),
    ("Your heart rate rose on Q2.", "Ada, your heart rate rose on Q2."),
    ("Ada, you held steady.", "Ada, you held steady."),        # already addressed: never twice
    ("Q2 was the hardest moment.", "Q2 was the hardest moment."),   # not "Ada, q2 was…"
])
def test_summary_is_addressed_to_them(summary, want):
    assert addressed(summary, "Ada") == want
    assert addressed(summary, "") == summary


def test_reports_carry_the_name_typed_on_the_setup_page():
    s = state()   # user_name "Ada"
    items = normalize_history(history())
    r = fallback_report(s, items)
    assert r.candidate_name == "Ada" and r.summary.startswith("Ada, you answered")
    draft = ReportDraft(composure_score=60, summary="You stayed calm until Q2.", hardest_moment="Q2 at 78.",
                        per_question=[], top_tips=["a", "b", "c"])
    r = finalize_draft(s, items, draft)
    assert r.candidate_name == "Ada" and r.summary == "Ada, you stayed calm until Q2."
