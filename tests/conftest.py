import faulthandler
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["BRAIN_FAKE"] = "1"   # tests never call Gemini or ElevenLabs

# A WebSocket receive that never arrives would hang pytest forever; dump stacks and exit instead.
faulthandler.dump_traceback_later(120, exit=True)

from backend import config  # noqa: E402
from backend.brain import gemini, interviewer, watcher  # noqa: E402


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch):
    monkeypatch.setattr(config, "BRAIN_FAKE", True)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(interviewer, "CALIBRATION_MIN_S", 0.0)
    monkeypatch.setattr(interviewer, "CLOSING_PAUSE_S", 0.0)
    monkeypatch.setattr(interviewer, "ANSWER_TIMEOUT_S", 10.0)
    monkeypatch.setattr(watcher, "INTERVAL_S", 0.05)
    monkeypatch.setattr(watcher, "COOLDOWN_S", 0.0)
    monkeypatch.setattr(gemini, "PLAN_TIMEOUT_S", 2.0)
    yield
