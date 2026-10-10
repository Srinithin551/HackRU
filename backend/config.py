"""Environment config. Everything reads settings from here, never from os.environ directly."""

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent

# .env at the repo root is the documented spot; backend/.env also works.
load_dotenv(REPO_ROOT / ".env")
load_dotenv(BACKEND_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(int(default))).strip().lower() in ("1", "true", "yes", "on")


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Two models, because free-tier rate limits are per model: the main one plans and writes the report,
# the fast one makes the live decide_next call. Each backs the other up on 503/429 errors.
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-3.6-flash"
GEMINI_FAST_MODEL = os.getenv("GEMINI_FAST_MODEL") or "gemini-3.5-flash-lite"
GEMINI_THINKING_LEVEL = (os.getenv("GEMINI_THINKING_LEVEL") or "minimal").strip().lower()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL") or "eleven_flash_v2_5"

PRESAGE_API_KEY = os.getenv("PRESAGE_API_KEY", "")
TIGER_DATABASE_URL = os.getenv("TIGER_DATABASE_URL", "")

BRAIN_FAKE = _bool("BRAIN_FAKE")
# Prefix for audio URLs. Blank (default) = relative "/audio/x.mp3", which the frontend resolves
# against its own origin (the Vite dev server proxies /audio to this backend).
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]

STATIC_DIR = BACKEND_DIR / "static"
AUDIO_DIR = STATIC_DIR / "audio"            # generated mp3s, served at /audio
FAKE_AUDIO_DIR = STATIC_DIR / "fake_audio"  # committed clips for BRAIN_FAKE=1
LOG_DIR = BACKEND_DIR / "logs"

for _d in (AUDIO_DIR, FAKE_AUDIO_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)
