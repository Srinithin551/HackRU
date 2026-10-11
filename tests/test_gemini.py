"""The real-mode Gemini path with a stubbed client: parsing, cleanup, timeouts, retries, logging."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend import config
from backend.brain import gemini, lines
from backend.brain.history import normalize_history
from backend.brain.specs import fallback_questions, get_spec
from backend.brain.state import SessionMeta, SessionState
from backend.contracts import QuestionStats


class FakeModels:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.models = []

    async def generate_content(self, model, contents, config):
        self.calls.append(config)
        self.models.append(model)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, float):
            await asyncio.sleep(reply)
            reply = {}
        return SimpleNamespace(text=json.dumps(reply))


@pytest.fixture
def fake_gemini(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BRAIN_FAKE", False)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    monkeypatch.setattr(config, "GEMINI_MODEL", "main-model")
    monkeypatch.setattr(config, "GEMINI_FAST_MODEL", "fast-model")
    monkeypatch.setattr(config, "GEMINI_THINKING_LEVEL", "minimal")
    monkeypatch.setattr(gemini, "_no_thinking_level", set())
    monkeypatch.setattr(gemini, "_resting_until", {})

    def install(*replies):
        models = FakeModels(replies)
        monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(aio=SimpleNamespace(models=models)))
        return models
    return install


def state():
    return SessionState("sid", SessionMeta("sid", "Ada", "Software Engineering", "Backend Engineer"))


def test_plan_cleans_pads_and_pins_difficulty(fake_gemini):
    fake_gemini({"questions": [
        {"text": '"**Tell me** about   your `favorite` project."', "topic": "projects", "difficulty": 5},
        {"text": "   ", "topic": "", "difficulty": 1},
    ]})
    qs = asyncio.run(gemini.plan_interview(get_spec("swe"), "Backend Engineer"))
    assert qs[0].text == "Tell me about your favorite project."
    assert len(qs) == 5 and [q.difficulty for q in qs] == [2, 3, 3, 4, 5]


def test_decide_parses_and_clamps(fake_gemini):
    fake_gemini({"action": "push", "spoken_text": "Okay. What breaks first at ten times the load?",
                 "answer_quality": 9, "rationale": "calm and strong", "coaching": ""})
    d = asyncio.run(gemini.decide_next(state(), "main", "Q", "answer", None, None))
    assert d.action == "push" and d.answer_quality == 5


def test_decide_times_out_to_next(fake_gemini, monkeypatch):
    monkeypatch.setattr(gemini, "DECIDE_TIMEOUT_S", 0.05)
    fake_gemini(1.0)
    d = asyncio.run(gemini.decide_next(state(), "main", "Q", "answer", None, None))
    assert d.action == "next" and d.rationale.startswith("fallback")
    log = next(config.LOG_DIR.glob("gemini-*.jsonl")).read_text()
    assert '"error": "timeout"' in log


def test_bad_json_falls_back(fake_gemini):
    fake_gemini({"action": "dance", "spoken_text": "", "answer_quality": 3, "rationale": "", "coaching": ""})
    assert asyncio.run(gemini.decide_next(state(), "main", "Q", "a", None, None)).action == "next"


def test_thinking_level_rejected_retries_without_it(fake_gemini):
    models = fake_gemini(RuntimeError("400 INVALID_ARGUMENT: thinking_level is not supported for this model"),
                         {"action": "next", "spoken_text": "", "answer_quality": 3, "rationale": "ok", "coaching": ""})
    d = asyncio.run(gemini.decide_next(state(), "main", "Q", "a", None, None))
    assert d.rationale == "ok"
    assert models.models == ["fast-model", "fast-model"]   # same model, retried without thinking_level
    assert models.calls[0].thinking_config is not None and models.calls[1].thinking_config is None
    assert gemini._no_thinking_level == {"fast-model"}


def test_overloaded_model_fails_over_to_the_other(fake_gemini):
    models = fake_gemini(RuntimeError("503 UNAVAILABLE: model is experiencing high demand"),
                         {"action": "ease_off", "spoken_text": "Let's slow down.", "answer_quality": 2,
                          "rationale": "stressed", "coaching": ""})
    d = asyncio.run(gemini.decide_next(state(), "main", "Q", "a", None, None))
    assert d.action == "ease_off"
    assert models.models == ["fast-model", "main-model"]   # decide_next tries the fast model first


def test_plan_and_report_prefer_the_main_model(fake_gemini):
    models = fake_gemini(RuntimeError("429 RESOURCE_EXHAUSTED"), {"questions": []})
    asyncio.run(gemini.plan_interview(get_spec("demo"), "Engineer"))
    assert models.models == ["main-model", "fast-model"]


def test_every_model_failing_falls_back(fake_gemini):
    fake_gemini(RuntimeError("503"), RuntimeError("503"))
    d = asyncio.run(gemini.decide_next(state(), "main", "Q", "a", None, None))
    assert d.action == "next" and d.rationale.startswith("fallback")


def test_final_report_from_gemini(fake_gemini):
    stats = QuestionStats(question_id="q1", duration_s=20, n_samples=20, hr_mean=84, hr_peak=91, hr_delta_pct=17,
                          br_mean=16, stress_mean=46, stress_peak=78, peak_offset_s=12)
    items = normalize_history([{"question_id": "q1", "idx": 1, "kind": "main", "text": "Q", "transcript": "A",
                                "stats": stats}])
    fake_gemini({"composure_score": 61, "summary": "Steady.", "hardest_moment": "Q1: stress hit 78 at 12s.",
                 "per_question": [{"question_id": "q1", "what_happened": "Fine.",
                                   "body_signal": "HR +17%, stress peak 78.", "advice": "Pause first."}],
                 "top_tips": ["a", "b", "c"]})
    r = asyncio.run(gemini.final_report(state(), items))
    assert r.generated_by == "gemini" and r.composure_score == 61 and r.top_tips == ["a", "b", "c"]


@pytest.mark.parametrize("raw,want", [
    ("Got it. I see. Cutting mismatches that much is a real result. Next?", "Cutting mismatches that much is a real result. Next?"),
    ("Okay, got it! Let's switch gears.", "Let's switch gears."),
    ("Makes sense, thanks for sharing. So how would you shard it?", "So how would you shard it?"),
    ("Alright, I understand \u2014 so what's the tradeoff?", "So what's the tradeoff?"),
    ("I see what you mean, but that's not the question.", "I see what you mean, but that's not the question."),
    ("Interestingly, caching helps here.", "Interestingly, caching helps here."),
    ("Got it.", "Got it."),   # never strip a turn down to nothing
])
def test_stock_openers_are_stripped(raw, want):
    assert gemini.strip_stock_openers(raw) == want


def test_decide_turn_has_stock_openers_removed(fake_gemini):
    fake_gemini({"action": "next", "spoken_text": "Got it, makes sense. Let's switch gears. Why Go?",
                 "answer_quality": 4, "rationale": "solid", "coaching": ""})
    d = asyncio.run(gemini.decide_next(state(), "main", "Q", "a", None, None))
    assert d.spoken_text == "Let's switch gears. Why Go?"


def test_small_talk_uses_the_fast_model_and_cleans_the_turn(fake_gemini):
    models = fake_gemini({"spoken_text": "Got it. Turtles are underrated. What made you pick computer science?"})
    line = asyncio.run(gemini.small_talk_follow_up(state()))
    assert line == "Turtles are underrated. What made you pick computer science?"
    assert models.models == ["fast-model"]


def test_small_talk_failure_returns_none(fake_gemini):
    fake_gemini(RuntimeError("503"), RuntimeError("503"))
    assert asyncio.run(gemini.small_talk_follow_up(state())) is None


def with_next_question(s):
    s.queue = fallback_questions(s.spec)[:1]
    return s


def test_coach_decision_keeps_coaching_separate_from_the_question(fake_gemini):
    fake_gemini({"action": "coach", "coaching": "Let me step out for a second. That answer would cost you. Okay, back in.",
                 "spoken_text": "How would you shard it?", "answer_quality": 1, "rationale": "wrong"})
    d = asyncio.run(gemini.decide_next(with_next_question(state()), "main", "Q", "pizza", None, None))
    # Gemini's own step out / back in lines are swapped for the rotating ones
    assert d.coaching == f"{lines.STEP_OUT_LINES[0]} That answer would cost you. {lines.STEP_IN_LINES[0]}"
    assert d.spoken_text == "How would you shard it?"


def test_coach_blob_in_spoken_text_is_split(fake_gemini):
    fake_gemini({"action": "coach", "coaching": "",
                 "spoken_text": "Let me step out for a second. That would cost you. Okay, back in the interview. How would you shard it?",
                 "answer_quality": 1, "rationale": "wrong"})
    d = asyncio.run(gemini.decide_next(with_next_question(state()), "main", "Q", "pizza", None, None))
    assert d.coaching == f"{lines.STEP_OUT_LINES[0]} That would cost you. {lines.STEP_IN_LINES[0]}"
    assert d.spoken_text == "How would you shard it?"


def test_coaching_repeated_in_spoken_text_is_said_once(fake_gemini):
    """Seen live 4 times: the whole coaching in both fields, which would play it twice in one clip."""
    coaching = ("Let me step out of the interview for a second. Telling the interviewer to shut up ends the interview. "
                "You should have explained failover to a standby. Okay, back in the interview.")
    fake_gemini({"action": "coach", "coaching": coaching, "spoken_text": f"{coaching} How do you recover the cluster?",
                 "answer_quality": 1, "rationale": "rude"})
    d = asyncio.run(gemini.decide_next(with_next_question(state()), "main", "Q", "shut up", None, None))
    assert d.spoken_text == "How do you recover the cluster?"
    assert d.coaching.count("failover") == 1


def test_reworded_copy_of_the_coaching_is_dropped_too(fake_gemini):
    fake_gemini({"action": "coach",
                 "coaching": "Using a graph doesn't reduce memory. You should have described streaming in chunks.",
                 "spoken_text": "Let me step out of the interview for a second. A tree won't fix a memory spike. Stream "
                                "the records lazily instead. Okay, back in the interview. How would you design an O(1) set?",
                 "answer_quality": 2, "rationale": "vague"})
    d = asyncio.run(gemini.decide_next(with_next_question(state()), "main", "Q", "a graph", None, None))
    assert d.spoken_text == "How would you design an O(1) set?"


def test_step_lines_rotate_and_skip_stepping_back_in_on_the_last_question(fake_gemini):
    s = with_next_question(state())
    s.actions = ["next", "coach", "coach"]   # two coaching turns already
    fake_gemini({"action": "coach", "coaching": "That would cost you. Say X instead.", "spoken_text": "Next?",
                 "answer_quality": 1, "rationale": "wrong"},
                {"action": "coach", "coaching": "That would cost you. Say X instead.", "spoken_text": "",
                 "answer_quality": 1, "rationale": "wrong"})
    d = asyncio.run(gemini.decide_next(s, "main", "Q", "pizza", None, None))
    assert d.coaching == f"{lines.STEP_OUT_LINES[2]} That would cost you. Say X instead. {lines.STEP_IN_LINES[2]}"
    s.queue = []   # the last question: no step back in, the closing line follows
    d = asyncio.run(gemini.decide_next(s, "main", "Q", "pizza", None, None))
    assert d.coaching == f"{lines.STEP_OUT_LINES[2]} That would cost you. Say X instead."


def test_coaching_is_dropped_for_other_actions(fake_gemini):
    fake_gemini({"action": "next", "coaching": "stray", "spoken_text": "Why Go?", "answer_quality": 4, "rationale": "ok"})
    assert asyncio.run(gemini.decide_next(state(), "main", "Q", "a", None, None)).coaching == ""


def test_daily_quota_model_is_skipped_until_it_rests(fake_gemini):
    daily = RuntimeError("429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    models = fake_gemini(daily, {"questions": []}, {"questions": []})
    asyncio.run(gemini.plan_interview(get_spec("demo"), "Engineer"))
    asyncio.run(gemini.plan_interview(get_spec("demo"), "Engineer"))
    assert models.models == ["main-model", "fast-model", "fast-model"]   # no second wasted call on main-model


def test_rest_durations():
    assert gemini._rest_for(RuntimeError("429 RESOURCE_EXHAUSTED ...PerDay...")) == gemini.DAILY_QUOTA_REST_S
    assert gemini._rest_for(RuntimeError("429 RESOURCE_EXHAUSTED. Please retry in 11.5s.")) == 12.5
    assert gemini._rest_for(RuntimeError("503 UNAVAILABLE high demand")) == gemini.OVERLOAD_REST_S
    assert gemini._rest_for(ValueError("bad json")) is None


def test_all_models_resting_falls_back_immediately(fake_gemini, monkeypatch):
    import time as _time
    models = fake_gemini()
    monkeypatch.setattr(gemini, "_resting_until", {"main-model": _time.monotonic() + 99, "fast-model": _time.monotonic() + 99})
    d = asyncio.run(gemini.decide_next(state(), "main", "Q", "a", None, None))
    assert d.action == "next" and models.models == []                 # no network call at all


def test_let_us_becomes_lets(fake_gemini):
    fake_gemini({"action": "next", "spoken_text": "Let us move to the next topic. Why Go?", "answer_quality": 4,
                 "rationale": "ok", "coaching": ""})
    d = asyncio.run(gemini.decide_next(state(), "main", "Q", "a", None, None))
    assert d.spoken_text == "Let's move to the next topic. Why Go?"
    assert gemini._LET_US.sub(r"\1et's ", "Please let us know.") == "Please let us know."   # left alone
