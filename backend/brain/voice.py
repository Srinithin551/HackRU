"""ElevenLabs text-to-speech with a disk cache keyed by voice + text.

`await tts(text)` returns the audio URL, or None if audio isn't available (the frontend still
shows the text). It never raises, so a TTS outage can't stall the interview.
"""

import asyncio
import hashlib
import logging
import random
import threading
from pathlib import Path
from typing import Optional

from backend import config
from backend.brain import fake, lines

log = logging.getLogger("brain.voice")

TTS_TIMEOUT_S = 12.0
OUTPUT_FORMAT = "mp3_44100_128"

_client = None
_concurrency = threading.BoundedSemaphore(3)   # free-tier ElevenLabs allows few parallel requests
_inflight: dict[str, asyncio.Task] = {}
_last_filler: Optional[str] = None
_warned_unconfigured = False


def key_for(text: str) -> str:
    voice = "fake" if config.BRAIN_FAKE else config.ELEVENLABS_VOICE_ID
    return hashlib.sha1(f"{voice}:{text}".encode()).hexdigest()[:16]


def audio_url(filename: str) -> str:
    return f"{config.PUBLIC_BASE_URL}/audio/{filename}"


def configured() -> bool:
    return bool(config.ELEVENLABS_API_KEY and config.ELEVENLABS_VOICE_ID)


def _get_client():
    global _client
    if _client is None:
        from elevenlabs.client import ElevenLabs
        _client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
    return _client


def synthesize_to_file(text: str, path: Path) -> None:
    """Blocking ElevenLabs call. Writes atomically so a half-written mp3 is never served."""
    with _concurrency:
        audio = _get_client().text_to_speech.convert(
            voice_id=config.ELEVENLABS_VOICE_ID,
            model_id=config.ELEVENLABS_MODEL,
            text=text,
            output_format=OUTPUT_FORMAT,
        )
        data = b"".join(audio)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)


async def tts(text: str) -> Optional[str]:
    global _warned_unconfigured
    text = " ".join((text or "").split())
    if not text:
        return None
    key = key_for(text)

    if config.BRAIN_FAKE:
        if (config.FAKE_AUDIO_DIR / f"{key}.mp3").exists():
            return audio_url(f"{key}.mp3")
        log.warning("fake mode: no pre-made clip for %r (run scripts/make_fake_audio.py)", text[:60])
        return None

    path = config.AUDIO_DIR / f"{key}.mp3"
    if path.exists():
        return audio_url(path.name)
    if not configured():
        if not _warned_unconfigured:
            log.warning("ElevenLabs not configured (ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID): questions will have no audio")
            _warned_unconfigured = True
        return None

    task = _inflight.get(key)
    if task is None:   # dedupe: concurrent requests for the same text share one ElevenLabs call
        task = asyncio.create_task(asyncio.to_thread(synthesize_to_file, text, path))
        _inflight[key] = task
        task.add_done_callback(lambda _t, k=key: _inflight.pop(k, None))
    try:
        await asyncio.wait_for(asyncio.shield(task), TTS_TIMEOUT_S)
        return audio_url(path.name)
    except asyncio.TimeoutError:
        log.error("TTS timed out after %.0fs for %r", TTS_TIMEOUT_S, text[:60])
    except Exception:
        log.exception("TTS failed for %r", text[:60])
    return None


def next_filler() -> str:
    global _last_filler
    choice = random.choice([f for f in lines.FILLERS if f != _last_filler] or lines.FILLERS)
    _last_filler = choice
    return choice


def startup_lines() -> list[str]:
    if config.BRAIN_FAKE:
        return fake.all_texts()
    return lines.fixed_lines()


async def warm_up() -> None:
    """Pre-generate the fixed lines (calibration, closing, fillers) so they play instantly."""
    texts = startup_lines()
    results = await asyncio.gather(*(tts(t) for t in texts))
    ready = sum(1 for r in results if r)
    log.info("voice warm-up: %d/%d fixed lines ready%s", ready, len(texts), " (fake mode)" if config.BRAIN_FAKE else "")
