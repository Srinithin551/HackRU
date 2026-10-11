"""ElevenLabs text-to-speech with a disk cache keyed by voice + text.

`await tts(text)` returns the audio URL, or None if audio isn't available (the frontend still
shows the text). It never raises, so a TTS outage can't stall the interview.
"""

import asyncio
import hashlib
import logging
import threading
import time
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
_warned_unconfigured = False


def key_for(text: str) -> str:
    """Cache file name for a line: sha1 of voice + model + text, so changing ELEVENLABS_VOICE_ID or
    ELEVENLABS_MODEL renders fresh clips instead of replaying old ones. Fake mode keys on "fake" + text
    only, which is how the committed clips in static/fake_audio/ are named."""
    if config.BRAIN_FAKE:
        return hashlib.sha1(f"fake:{text}".encode()).hexdigest()[:16]
    return hashlib.sha1(f"{config.ELEVENLABS_VOICE_ID}:{config.ELEVENLABS_MODEL}:{text}".encode()).hexdigest()[:16]


CACHE_MAX_AGE_DAYS = 14   # generated clips unused this long are deleted at startup (static/audio/ only)


def prune_cache(max_age_days: float = CACHE_MAX_AGE_DAYS) -> int:
    """Delete generated clips not touched for `max_age_days`. Every question Gemini writes is a new clip,
    so without this backend/static/audio/ only grows. Never touches the committed fake-mode clips.
    Returns how many files were removed."""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for path in config.AUDIO_DIR.glob("*.mp3"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


BYTES_PER_SECOND = 128_000 / 8   # OUTPUT_FORMAT is constant-bitrate 128 kbps


def clip_seconds(url: Optional[str]) -> float:
    """How long a clip we served will play, from its file size (exact enough at a constant bitrate).
    0 if unknown. Used so the session doesn't end while the closing clip is still talking."""
    if not url:
        return 0.0
    name = url.rsplit("/", 1)[-1]
    for d in (config.AUDIO_DIR, config.FAKE_AUDIO_DIR):
        path = d / name
        if path.is_file():
            return path.stat().st_size / BYTES_PER_SECOND
    return 0.0


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
    """URL of an mp3 for `text` in the interviewer's one voice (ELEVENLABS_VOICE_ID), or None. Checked in
    order: fake-mode clip, disk cache, then ElevenLabs (shared with any in-flight request for the same text,
    capped at TTS_TIMEOUT_S). On timeout the request keeps running in its thread, so a later call may find
    the file cached."""
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
        try:
            path.touch()   # mark it as used, so prune_cache keeps clips that still get played
        except OSError:
            pass
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


def startup_lines() -> list[str]:
    if config.BRAIN_FAKE:
        return fake.all_texts()
    return lines.fixed_lines()


async def warm_up() -> None:
    """Pre-generate the fixed lines (calibration, closing) so they play instantly. Old generated clips
    are pruned first (prune_cache)."""
    removed = prune_cache()
    if removed:
        log.info("voice cache: removed %d clip(s) unused for %d+ days", removed, CACHE_MAX_AGE_DAYS)
    texts = startup_lines()
    results = await asyncio.gather(*(tts(t) for t in texts))
    ready = sum(1 for r in results if r)
    log.info("voice warm-up: %d/%d fixed lines ready%s", ready, len(texts), " (fake mode)" if config.BRAIN_FAKE else "")
