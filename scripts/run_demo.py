"""Start the whole StressTest pipeline with one command, and watch that every link is alive.

    webcam → Presage (SmartSpectra app) → sensing_client → backend (/api/vitals) → Tiger Data
           → Brain (Gemini + ElevenLabs) → browser (audio, live dashboard, report)

    python scripts/run_demo.py                 # everything, real Gemini + ElevenLabs
    python scripts/run_demo.py --fake          # BRAIN_FAKE=1: no Gemini/ElevenLabs calls
    python scripts/run_demo.py --no-preview    # don't open the webcam in the browser (SmartSpectra keeps it)
    python scripts/run_demo.py --no-sensing    # skip the Presage client (e.g. with scripts/mock_vitals.py)
    python scripts/run_demo.py --presage       # also launch the Presage SmartSpectra app (press Start in it)

Before running: start the SmartSpectra app (Presage) and press Start, so it writes to SESSION_FILE
(default ~/smartspectra_session.txt), or pass --presage to launch it (one-time build:
sensing_client/presage_macos/setup_presage.sh). Ctrl+C stops everything this script started.
Standard library only.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"
DB_CONTAINER = "stresstest-tigerdb"


def say(msg: str) -> None:
    print(f"[demo] {msg}", flush=True)


def read_env() -> dict:
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def get_json(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def ensure_local_db(url: str) -> None:
    """If TIGER_DATABASE_URL points at the local TimescaleDB container, make sure it's running."""
    host = urlparse(url).hostname or ""
    if host not in ("127.0.0.1", "localhost"):
        say("Tiger Data: cloud service (from TIGER_DATABASE_URL)")
        return
    try:
        running = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", DB_CONTAINER],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        sys.exit("[demo] Docker isn't available. Start Docker Desktop, or set TIGER_DATABASE_URL to a cloud service.")
    if running != "true":
        say(f"starting local TimescaleDB container '{DB_CONTAINER}'…")
        subprocess.run(["docker", "start", DB_CONTAINER], check=True, capture_output=True)
        for _ in range(30):
            ok = subprocess.run(["docker", "exec", DB_CONTAINER, "pg_isready", "-U", "postgres", "-q"]).returncode == 0
            if ok:
                break
            time.sleep(1)
    say("Tiger Data: local TimescaleDB container is up")


# Commands this launcher starts. A process on one of our ports matching these, running from this repo,
# is a leftover from an earlier run (e.g. its terminal was closed) and is safe to stop.
OUR_COMMANDS = ("uvicorn backend.main:app", "sensing_client/client.py", "vite")


def _cmd_and_cwd(pid: int) -> tuple[str, str]:
    cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    cwd = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
    cwd = next((line[1:] for line in cwd.splitlines() if line.startswith("n")), "")
    return cmd, cwd


def _ours(cmd: str, cwd: str) -> bool:
    return any(c in cmd for c in OUR_COMMANDS) and cwd.startswith(str(ROOT))


def _is_sensing_client(pid: int, cmd: str) -> bool:
    """Python actually running sensing_client/client.py, not just a command line mentioning it (a shell,
    an editor, a grep). The executable must be Python and the script must be its first argument."""
    exe = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    if "python" not in exe.rsplit("/", 1)[-1].lower():
        return False
    args = cmd.split()
    script = next((a for a in args[1:] if not a.startswith("-")), "")
    return script.endswith("sensing_client/client.py")


def free_ports(ports: list[int]) -> None:
    """Stop leftover StressTest processes (on our ports, plus orphaned sensing clients); refuse to touch
    anything else."""
    pids = set()
    for port in ports:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True).stdout
        for pid in map(int, out.split()):
            cmd, cwd = _cmd_and_cwd(pid)
            if not _ours(cmd, cwd):
                sys.exit(f"[demo] port {port} is in use by another program (pid {pid}: {cmd[:100]}).\n"
                         f"[demo] stop it (kill {pid}) or pick another port with --port / --frontend-port")
            pids.add(pid)
    out = subprocess.run(["pgrep", "-f", "sensing_client/client.py"], capture_output=True, text=True).stdout
    for pid in map(int, out.split()):
        if pid in (os.getpid(), os.getppid()):
            continue
        cmd, cwd = _cmd_and_cwd(pid)
        if _is_sensing_client(pid, cmd) and cwd.startswith(str(ROOT)):
            pids.add(pid)
    for pid in pids:
        args = _cmd_and_cwd(pid)[0].split()
        what = " ".join(a for a in args[1:] if not a.startswith("/"))[:70] or args[0]   # drop interpreter paths
        say(f"stopping a leftover StressTest process from an earlier run (pid {pid}: {what})")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + 8
    while pids and time.time() < deadline:
        pids = {pid for pid in pids if _alive(pid)}
        time.sleep(0.3)
    for pid in pids:
        os.kill(pid, signal.SIGKILL)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def pipe(proc: subprocess.Popen, name: str) -> None:
    for line in iter(proc.stdout.readline, ""):
        print(f"[{name}] {line.rstrip()}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fake", action="store_true", help="BRAIN_FAKE=1 (no Gemini/ElevenLabs calls)")
    ap.add_argument("--no-sensing", action="store_true", help="don't start the Presage sensing client")
    ap.add_argument("--no-frontend", action="store_true", help="don't start the Vite dev server")
    ap.add_argument("--no-preview", action="store_true", help="browser doesn't open the webcam (SmartSpectra owns it)")
    ap.add_argument("--presage", action="store_true",
                    help="launch the Presage SmartSpectra app (built by sensing_client/presage_macos/setup_presage.sh)")
    ap.add_argument("--port", type=int, default=8000, help="backend port")
    ap.add_argument("--frontend-port", type=int, default=5173, help="frontend port")
    args = ap.parse_args()

    env = read_env()
    if not VENV_PY.exists():
        sys.exit("[demo] .venv missing: python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt")
    if not args.fake and not (env.get("GEMINI_API_KEY") and env.get("ELEVENLABS_API_KEY") and env.get("ELEVENLABS_VOICE_ID")):
        say("WARNING: Gemini/ElevenLabs keys missing in .env: questions fall back to canned text with no voice")
    if env.get("TIGER_DATABASE_URL"):
        ensure_local_db(env["TIGER_DATABASE_URL"])
    else:
        say("WARNING: no TIGER_DATABASE_URL: Data runs in memory and nothing is saved")

    session_file = Path(os.environ.get("SESSION_FILE", "~/smartspectra_session.txt")).expanduser()
    if args.presage:
        r = subprocess.run([str(ROOT / "sensing_client" / "presage_macos" / "start_presage.sh")],
                           capture_output=True, text=True, env={**os.environ, "SESSION_FILE": str(session_file)})
        for line in (r.stdout + r.stderr).splitlines():
            print(line, flush=True)
        if r.returncode != 0:
            sys.exit("[demo] couldn't launch Presage (see above)")
    if not args.no_sensing and not session_file.exists():
        say(f"NOTE: {session_file} doesn't exist yet: start the SmartSpectra app and press Start")

    free_ports([args.port] + ([] if args.no_frontend else [args.frontend_port]))
    backend_url = f"http://127.0.0.1:{args.port}"
    procs: list[tuple[str, subprocess.Popen]] = []

    def start(name: str, cmd: list[str], cwd: Path = ROOT, extra_env: dict | None = None) -> None:
        p = subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **(extra_env or {})}, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        threading.Thread(target=pipe, args=(p, name), daemon=True).start()
        procs.append((name, p))

    def stop_all(*_):
        say("stopping…")
        for _, p in reversed(procs):
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
        for _, p in procs:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
        sys.exit(0)

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    signal.signal(signal.SIGHUP, stop_all)   # terminal window closed: don't leave orphaned servers behind

    # No --reload: a code reload mid-interview would drop the session.
    start("backend", [str(VENV_PY), "-m", "uvicorn", "backend.main:app", "--port", str(args.port)],
          extra_env={"BRAIN_FAKE": "1"} if args.fake else {"BRAIN_FAKE": env.get("BRAIN_FAKE", "0")})
    backend = procs[0][1]
    for _ in range(60):
        if backend.poll() is not None:   # it died (e.g. couldn't bind the port): don't trust any /health
            say(f"backend failed to start (exit code {backend.returncode}); see the [backend] lines above")
            stop_all()
        try:
            health = get_json(f"{backend_url}/health")
            break
        except Exception:
            time.sleep(0.5)
    else:
        stop_all()
    say(f"backend up: brain={'FAKE' if health['fake'] else 'real'} gemini={health['gemini']} "
        f"elevenlabs={health['elevenlabs']} data={health.get('data_store')} routers={health['routers']}")

    if not args.no_sensing:
        start("presage", [sys.executable, "sensing_client/client.py"],
              extra_env={"BACKEND_URL": backend_url, "SESSION_FILE": str(session_file)})
    if not args.no_frontend:
        fe_env = {"VITE_PROXY_TARGET": backend_url}
        if args.no_preview:
            fe_env["VITE_CAMERA_PREVIEW"] = "0"
        start("frontend", ["npm", "run", "dev", "--", "--port", str(args.frontend_port), "--strictPort"],
              cwd=ROOT / "frontend", extra_env=fe_env)
        say(f"open http://localhost:{args.frontend_port} in Chrome (Chrome is needed for speech-to-text)")

    # Watch the links that fail silently: Presage feeding vitals, and the backend itself.
    was = None
    while True:
        time.sleep(5)
        for name, p in procs:
            if p.poll() is not None:
                say(f"{name} exited with code {p.returncode}; stopping the rest")
                stop_all()
        try:
            sensor = get_json(f"{backend_url}/health")["sensor"]
        except Exception:
            say("WARNING: backend /health not answering")
            continue
        now = ("signal" if sensor.get("signal") else "no-face") if sensor["live"] else "off"
        if now != was:
            say({"signal": "Presage vitals: LIVE (heart rate coming through)",
                 "no-face": "Presage vitals: readings arriving but NO FACE / no heart rate: is the SmartSpectra "
                            "app running, and is your face in view?",
                 "off": "Presage vitals: not arriving (starts once a session is active)"}[now])
            was = now


if __name__ == "__main__":
    main()
