"""ElevenLabs path with a stubbed client: caching, dedupe, failures, filler rotation."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from backend import config
from backend.brain import lines, voice


@pytest.fixture
def eleven(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BRAIN_FAKE", False)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "voice1")
    monkeypatch.setattr(config, "AUDIO_DIR", tmp_path)
    calls = []

    def convert(voice_id, model_id, text, output_format):
        calls.append(text)
        if text == "explode":
            raise RuntimeError("quota exceeded")
        time.sleep(0.05)
        return iter([b"ID3", b"audio"])

    monkeypatch.setattr(voice, "_get_client", lambda: SimpleNamespace(text_to_speech=SimpleNamespace(convert=convert)))
    return calls


def test_tts_caches_and_dedupes(eleven):
    async def go():
        return await asyncio.gather(voice.tts("Hello there."), voice.tts("Hello  there."))
    a, b = asyncio.run(go())
    assert a == b == f"{config.PUBLIC_BASE_URL}/audio/{voice.key_for('Hello there.')}.mp3"
    assert eleven == ["Hello there."]                     # one request for two concurrent asks
    assert asyncio.run(voice.tts("Hello there.")) == a and len(eleven) == 1   # served from disk
    assert (config.AUDIO_DIR / f"{voice.key_for('Hello there.')}.mp3").read_bytes() == b"ID3audio"


def test_tts_failure_returns_none(eleven):
    assert asyncio.run(voice.tts("explode")) is None
    assert not list(config.AUDIO_DIR.glob("*.mp3"))


def test_cache_key_depends_on_voice(monkeypatch):
    monkeypatch.setattr(config, "BRAIN_FAKE", False)
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "a")
    k1 = voice.key_for("Hi")
    monkeypatch.setattr(config, "ELEVENLABS_VOICE_ID", "b")
    assert voice.key_for("Hi") != k1


def test_fillers_never_repeat_back_to_back():
    picks = [voice.next_filler() for _ in range(50)]
    assert all(a != b for a, b in zip(picks, picks[1:]))
    assert set(picks) <= set(lines.FILLERS)
