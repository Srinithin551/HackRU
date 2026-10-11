"""ElevenLabs path with a stubbed client: caching, dedupe, failures, filler rotation."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from backend import config
from backend.brain import voice


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


def test_cache_key_depends_on_model(monkeypatch):
    monkeypatch.setattr(config, "BRAIN_FAKE", False)
    monkeypatch.setattr(config, "ELEVENLABS_MODEL", "m1")
    k1 = voice.key_for("Hi")
    monkeypatch.setattr(config, "ELEVENLABS_MODEL", "m2")
    assert voice.key_for("Hi") != k1


def test_prune_cache_removes_only_old_generated_clips(tmp_path):
    import os
    import time
    old, fresh = config.AUDIO_DIR / "old.mp3", config.AUDIO_DIR / "fresh.mp3"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    ancient = time.time() - 30 * 86400
    os.utime(old, (ancient, ancient))
    assert voice.prune_cache(14) == 1
    assert not old.exists() and fresh.exists()


def test_clip_seconds_from_file_size(monkeypatch, tmp_path):
    monkeypatch.undo()   # conftest stubs clip_seconds out; test the real one
    monkeypatch.setattr(config, "AUDIO_DIR", tmp_path)
    (tmp_path / "abc.mp3").write_bytes(b"x" * 16_000 * 7)       # 7 s at 128 kbps
    assert voice.clip_seconds("/audio/abc.mp3") == 7.0
    assert voice.clip_seconds("/audio/missing.mp3") == 0.0
    assert voice.clip_seconds(None) == 0.0


def test_each_god_speaks_in_their_own_voice(eleven, monkeypatch):
    """Questions go to ElevenLabs in the voice of the god the page shows asking them; the presiding god
    (warm-up, closing) has his own; ELEVENLABS_GOD_VOICES=0 puts everyone back on ELEVENLABS_VOICE_ID."""
    from backend.brain import gods
    used = []
    monkeypatch.setattr(voice, "synthesize_to_file",
                        lambda text, path, voice_id=None: (used.append(voice_id), path.write_bytes(b"ID3")))
    everyone = [gods.PRESIDING, *gods.PANTHEON]
    for god in everyone:
        assert asyncio.run(voice.tts("Speak, mortal.", god))
    assert used == [config.GOD_VOICES[g] for g in everyone]
    assert len(set(used)) == len(everyone)   # nobody shares a voice
    assert gods.god_for_question(0) == "Osiras" and gods.god_for_question(1) == "Athena"
    assert gods.god_for_question(6) == "Athena"   # 1..n cycles through the pantheon

    monkeypatch.setattr(config, "GOD_VOICES_ON", False)
    assert voice.voice_for("Hera") == "voice1"


def test_gods_match_the_frontend():
    """backend/brain/gods.py must name the gods in the same order as frontend/src/lib/constants.ts, or a
    question would be voiced by a different god than the one on screen."""
    import re
    from pathlib import Path
    from backend.brain import gods
    src = (Path(__file__).resolve().parent.parent / "frontend/src/lib/constants.ts").read_text()
    presiding = re.search(r"INTERVIEWER_NAME = '([^']+)'", src).group(1)
    pantheon_block = re.search(r"PANTHEON: readonly God\[\] = \[(.*?)\]", src, re.S).group(1)
    assert presiding == gods.PRESIDING
    assert tuple(re.findall(r"name: '([^']+)'", pantheon_block)) == gods.PANTHEON
