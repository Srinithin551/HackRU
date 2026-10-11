"""Render every line BRAIN_FAKE=1 can speak into backend/static/fake_audio/*.mp3 (committed to git).

    python scripts/make_fake_audio.py                 # ElevenLabs if keys are set, else macOS `say`
    python scripts/make_fake_audio.py --engine say    # force placeholder voice (needs ffmpeg)
    python scripts/make_fake_audio.py --force         # re-render clips that already exist

Run it again whenever you change a line in backend/brain/lines.py or backend/brain/fake.py.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402

config.BRAIN_FAKE = True   # clip names use the fake-mode key

from backend.brain import fake, voice  # noqa: E402


def render_say(text: str, out: Path, say_voice: str) -> None:
    if not shutil.which("say") or not shutil.which("ffmpeg"):
        sys.exit("the 'say' engine needs macOS `say` and ffmpeg (brew install ffmpeg)")
    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / "clip.aiff"
        cmd = ["say", "-o", str(aiff)] + (["-v", say_voice] if say_voice else []) + [text]
        subprocess.run(cmd, check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff),
                        "-codec:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", str(out)], check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=["auto", "elevenlabs", "say"], default="auto")
    ap.add_argument("--voice", default="Samantha", help="macOS voice for --engine say")
    ap.add_argument("--force", action="store_true", help="re-render existing clips")
    args = ap.parse_args()

    engine = args.engine
    if engine == "auto":
        engine = "elevenlabs" if voice.configured() else "say"
    if engine == "elevenlabs" and not voice.configured():
        sys.exit("ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID must be set in .env")

    out_dir = config.FAKE_AUDIO_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for text in fake.all_texts():
        name = f"{voice.key_for(text)}.mp3"
        manifest[name] = text
        path = out_dir / name
        if path.exists() and not args.force:
            print(f"  have  {name}  {text}")
            continue
        if engine == "elevenlabs":
            voice.synthesize_to_file(text, path)
        else:
            render_say(text, path, args.voice)
        print(f"  made  {name}  {text}")

    stale = [p for p in out_dir.glob("*.mp3") if p.name not in manifest]
    for p in stale:
        p.unlink()
        print(f"  removed stale {p.name}")
    (out_dir / "manifest.json").write_text(json.dumps({"engine": engine, "clips": manifest}, indent=2) + "\n")
    print(f"{len(manifest)} clips in {out_dir} (engine: {engine})")


if __name__ == "__main__":
    main()
