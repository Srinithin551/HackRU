"""The real-mode Gemini path with a stubbed client: parsing, cleanup, timeouts, retries, logging."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend import config
from backend.brain import gemini
from backend.brain.history import normalize_history
from backend.brain.specs import get_spec
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
                 "answer_quality": 9, "rationale": "calm and strong"})
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
    fake_gemini({"action": "dance", "spoken_text": "", "answer_quality": 3, "rationale": ""})
    assert asyncio.run(gemini.decide_next(state(), "main", "Q", "a", None, None)).action == "next"


def test_thinking_level_rejected_retries_without_it(fake_gemini):
    models = fake_gemini(RuntimeError("400 INVALID_ARGUMENT: thinking_level is not supported for this model"),
                         {"action": "next", "spoken_text": "", "answer_quality": 3, "rationale": "ok"})
    d = asyncio.run(gemini.decide_next(state(), "main", "Q", "a", None, None))
    assert d.rationale == "ok"
    assert models.models == ["fast-model", "fast-model"]   # same model, retried without thinking_level
    assert models.calls[0].thinking_config is not None and models.calls[1].thinking_config is None
    assert gemini._no_thinking_level == {"fast-model"}


def test_overloaded_model_fails_over_to_the_other(fake_gemini):
    models = fake_gemini(RuntimeError("503 UNAVAILABLE: model is experiencing high demand"),
                         {"action": "ease_off", "spoken_text": "Let's slow down.", "answer_quality": 2,
                          "rationale": "stressed"})
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
                          br_mean=16, focus_mean=0.6, stress_mean=46, stress_peak=78, peak_offset_s=12)
    items = normalize_history([{"question_id": "q1", "idx": 1, "kind": "main", "text": "Q", "transcript": "A",
                                "stats": stats}])
    fake_gemini({"composure_score": 61, "summary": "Steady.", "hardest_moment": "Q1: stress hit 78 at 12s.",
                 "per_question": [{"question_id": "q1", "what_happened": "Fine.",
                                   "body_signal": "HR +17%, stress peak 78.", "advice": "Pause first."}],
                 "top_tips": ["a", "b", "c"]})
    r = asyncio.run(gemini.final_report(state(), items))
    assert r.generated_by == "gemini" and r.composure_score == 61 and r.top_tips == ["a", "b", "c"]
