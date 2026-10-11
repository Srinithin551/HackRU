"""Start the whole Osiris pipeline with one command, and watch that every link is alive.

    webcam → Presage (hidden sensor, sensing_client/presage_node) → backend (/api/vitals) → Tiger Data
           → Brain (Gemini + ElevenLabs) → Chrome window (audio, live dashboard, report)

The only window you see is the app in Chrome, which opens by itself. Presage runs in the background
with no window, and starts measuring as soon as this script does (pre-warm), so the pulse is already
stable when the first question lands. The camera light is therefore on while the app is open; pass
--no-prewarm to the sensor for the old behaviour (camera on only during an interview).

    python scripts/run_demo.py                   # everything, real Gemini + ElevenLabs + Presage
    python scripts/run_demo.py --fake            # BRAIN_FAKE=1: no Gemini/ElevenLabs calls
    python scripts/run_demo.py --simulate-vitals # artificial Presage vitals, no camera (testing)
    python scripts/run_demo.py --no-open         # don't open the Chrome window
    python scripts/run_demo.py --no-preview      # the page doesn't show the webcam (Presage still uses it)
    python scripts/run_demo.py --no-sensing      # no Presage at all (e.g. with scripts/mock_vitals.py)
    python scripts/run_demo.py --legacy-presage  # old way: SmartSpectra app window + log file + client.py

Every run starts empty: the Tiger Data tables are dropped and recreated (scripts/init_db.py --fresh) and the
generated voice clips are deleted, so nothing from an earlier run can leak into this one (--keep-data skips it).
If Presage crashes (its engine can abort on a camera hiccup), only Presage is restarted: the interview keeps
going. Everything printed here is also saved to backend/logs/run-<date>-<time>.log.

    python scripts/run_demo.py --keep-data       # don't empty Tiger Data / the voice cache this time

Needs Node.js 20+ (the Presage SDK is installed into sensing_client/presage_node on first run) and
PRESAGE_API_KEY in .env (plus optional PRESAGE_BACKUP_API_KEYS, used when a key runs out of credits). The first time, macOS asks whether your terminal app may use the camera: allow it.
Ctrl+C stops everything this script started. Standard library only.
"""

import argparse
import json
import os
import shutil
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
AUDIO_DIR = ROOT / "backend" / "static" / "audio"   # generated voice clips (the committed fake ones live elsewhere)
LOG_DIR = ROOT / "backend" / "logs"
KEEP_RUN_LOGS = 10
SENSOR_RESTART_S = [2, 5, 10, 20]   # waits before restarting a crashed Presage sensor
SENSOR_STABLE_S = 60                # a sensor that ran this long has its restart backoff reset

_log_file = None
_log_lock = threading.Lock()


def out(line: str) -> None:
    """Print a line and append it to this run's log file."""
    print(line, flush=True)
    if _log_file is not None:
        with _log_lock:
            _log_file.write(line + "\n")
            _log_file.flush()


def open_run_log() -> Path:
    """backend/logs/run-<date>-<time>.log for this run; only the newest KEEP_RUN_LOGS are kept."""
    global _log_file
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for old in sorted(LOG_DIR.glob("run-*.log"))[:-(KEEP_RUN_LOGS - 1)]:
        old.unlink(missing_ok=True)
    path = LOG_DIR / f"run-{time.strftime('%Y%m%d-%H%M%S')}.log"
    _log_file = path.open("w", encoding="utf-8")
    return path
DB_CONTAINER = "stresstest-tigerdb"
SENSOR_DIR = ROOT / "sensing_client" / "presage_node"
CHROME_APPS = (Path("/Applications/Google Chrome.app"), Path.home() / "Applications" / "Google Chrome.app")


def say(msg: str) -> None:
    out(f"[demo] {msg}")


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
OUR_COMMANDS = ("uvicorn backend.main:app", "sensing_client/client.py", "presage_node/sensor.mjs", "vite")


def _cmd_and_cwd(pid: int) -> tuple[str, str]:
    cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    cwd = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
    cwd = next((line[1:] for line in cwd.splitlines() if line.startswith("n")), "")
    return cmd, cwd


def _ours(cmd: str, cwd: str) -> bool:
    return any(c in cmd for c in OUR_COMMANDS) and cwd.startswith(str(ROOT))


def _is_sensing_client(pid: int, cmd: str) -> bool:
    """Python running sensing_client/client.py or Node running the Presage sensor, not just a command line
    mentioning them (a shell, an editor, a grep). The executable must be Python or Node and the script
    must be its first argument."""
    exe = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    exe = exe.rsplit("/", 1)[-1].lower()
    args = cmd.split()
    script = next((a for a in args[1:] if not a.startswith("-")), "")
    if "python" in exe:
        return script.endswith("sensing_client/client.py")
    return exe == "node" and script.endswith("presage_node/sensor.mjs")


def free_ports(ports: list[int]) -> None:
    """Stop leftover app processes (on our ports, plus orphaned sensing clients); refuse to touch
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
    out = subprocess.run(["pgrep", "-f", "sensing_client/client.py|presage_node/sensor.mjs"],
                         capture_output=True, text=True).stdout
    for pid in map(int, out.split()):
        if pid in (os.getpid(), os.getppid()):
            continue
        cmd, cwd = _cmd_and_cwd(pid)
        if _is_sensing_client(pid, cmd) and cwd.startswith(str(ROOT)):
            pids.add(pid)
    for pid in pids:
        args = _cmd_and_cwd(pid)[0].split()
        what = " ".join(a for a in args[1:] if not a.startswith("/"))[:70] or args[0]   # drop interpreter paths
        say(f"stopping a leftover app process from an earlier run (pid {pid}: {what})")
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
        out(f"[{name}] {line.rstrip()}")


def fresh_start(env: dict) -> None:
    """Every run starts empty: drop and recreate the Tiger Data tables (current schema + columnstore, via
    scripts/init_db.py --fresh) and delete the generated voice clips. The backend regenerates the few fixed
    lines at startup; everything else is made per interview anyway."""
    clips = [p for pattern in ("*.mp3", "*.part") for p in AUDIO_DIR.glob(pattern)]
    for p in clips:
        p.unlink(missing_ok=True)
    say(f"fresh start: deleted {len(clips)} generated voice clip(s)")
    if not env.get("TIGER_DATABASE_URL"):
        return
    try:
        r = subprocess.run([str(VENV_PY), "scripts/init_db.py", "--fresh", "--columnstore"], cwd=ROOT,
                           capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        say("WARNING: resetting Tiger Data took over 3 minutes; continuing with whatever is there")
        return
    failed = [line.strip() for line in r.stdout.splitlines() if line.strip().startswith("FAIL")]
    if r.returncode != 0 or failed:
        say("WARNING: couldn't reset Tiger Data; continuing with whatever is there:")
        for line in (failed or (r.stdout + r.stderr).strip().splitlines()[-6:]):
            out(f"[demo]   {line}")
    else:
        say("fresh start: Tiger Data tables dropped and recreated empty (schema, indexes, 5 s rollup, columnstore)")


def describe_exit(code: int) -> str:
    """'exit code 1' or 'signal SIGABRT' (negative return codes are signals)."""
    if code < 0:
        try:
            return f"signal {signal.Signals(-code).name}"
        except ValueError:
            return f"signal {-code}"
    return f"exit code {code}"


def ensure_sensor() -> str:
    """Node.js plus Presage's Node SDK for the hidden sensor (installed on first run). Returns node's path."""
    node = shutil.which("node")
    if not node:
        sys.exit("[demo] Node.js 20+ is needed for the Presage sensor: install it from nodejs.org")
    major = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip().lstrip("v").split(".")[0]
    if not major.isdigit() or int(major) < 20:
        sys.exit(f"[demo] the Presage sensor needs Node.js 20 or newer (found {major or 'unknown'})")
    if not (SENSOR_DIR / "node_modules" / "@smartspectra" / "node-sdk").exists():
        say("installing Presage's Node SDK into sensing_client/presage_node (one time, about 400 MB)…")
        if subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=SENSOR_DIR).returncode != 0:
            sys.exit("[demo] npm couldn't install the Presage SDK (see above)")
    return node


def wait_for(url: str, proc: subprocess.Popen, timeout: float = 60) -> bool:
    """True once `url` answers; False if `proc` dies or it takes too long."""
    deadline = time.time() + timeout
    while time.time() < deadline and proc.poll() is None:
        try:
            urllib.request.urlopen(url, timeout=2).close()
            return True
        except Exception:
            time.sleep(0.5)
    return False


def open_chrome(url: str) -> None:
    """The app in its own Chrome window (app mode: no tabs or address bar). Chrome is needed for
    speech-to-text; without it, the default browser opens instead."""
    app = next((a for a in CHROME_APPS if a.exists()), None)
    if app is None:
        say(f"Google Chrome isn't installed: opening {url} in your default browser (speech-to-text needs Chrome)")
        subprocess.run(["open", url])
        return
    # -n hands the flags to Chrome even when it's already running (it opens a window in the open profile).
    subprocess.run(["open", "-na", str(app), "--args", f"--app={url}", "--window-size=1440,900"])
    say(f"opened the app in a Chrome window ({url})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fake", action="store_true", help="BRAIN_FAKE=1 (no Gemini/ElevenLabs calls)")
    ap.add_argument("--simulate-vitals", action="store_true",
                    help="the Presage sensor sends artificial vitals instead of using the camera (testing)")
    ap.add_argument("--no-sensing", action="store_true", help="don't start the Presage sensor")
    ap.add_argument("--no-frontend", action="store_true", help="don't start the Vite dev server")
    ap.add_argument("--no-open", action="store_true", help="don't open the Chrome window")
    ap.add_argument("--no-preview", action="store_true", help="the page doesn't show the webcam (Presage still uses it)")
    ap.add_argument("--legacy-presage", action="store_true",
                    help="old setup: launch the SmartSpectra app window (sensing_client/presage_macos) and "
                         "sensing_client/client.py instead of the hidden sensor")
    ap.add_argument("--presage", action="store_true", help=argparse.SUPPRESS)   # old flag: Presage is now the default
    ap.add_argument("--keep-data", action="store_true",
                    help="don't empty Tiger Data and the voice cache at startup (every run starts empty otherwise)")
    ap.add_argument("--port", type=int, default=8000, help="backend port")
    ap.add_argument("--frontend-port", type=int, default=5173, help="frontend port")
    args = ap.parse_args()

    env = read_env()
    say(f"logging this run to {open_run_log().relative_to(ROOT)}")
    if not VENV_PY.exists():
        sys.exit("[demo] .venv missing: python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt")
    if not args.fake and not (env.get("GEMINI_API_KEY") and env.get("ELEVENLABS_API_KEY") and env.get("ELEVENLABS_VOICE_ID")):
        say("WARNING: Gemini/ElevenLabs keys missing in .env: questions fall back to canned text with no voice")
    if env.get("TIGER_DATABASE_URL"):
        ensure_local_db(env["TIGER_DATABASE_URL"])
    else:
        say("WARNING: no TIGER_DATABASE_URL: Data runs in memory and nothing is saved")
    if args.keep_data:
        say("--keep-data: keeping Tiger Data and the voice cache from earlier runs")
    if args.presage:
        say("NOTE: --presage isn't needed any more: Presage now runs hidden by default")

    sensing = not args.no_sensing
    node = None
    session_file = Path(os.environ.get("SESSION_FILE", "~/smartspectra_session.txt")).expanduser()
    if sensing and args.legacy_presage:
        r = subprocess.run([str(ROOT / "sensing_client" / "presage_macos" / "start_presage.sh")],
                           capture_output=True, text=True, env={**os.environ, "SESSION_FILE": str(session_file)})
        for line in (r.stdout + r.stderr).splitlines():
            out(line)
        if r.returncode != 0:
            sys.exit("[demo] couldn't launch Presage (see above)")
    elif sensing:
        node = ensure_sensor()
        keys = {k for k in [env.get("PRESAGE_API_KEY", "")] + env.get("PRESAGE_BACKUP_API_KEYS", "").replace(",", " ").split()
                if k.strip()}
        if args.simulate_vitals:
            say("Presage: SIMULATED vitals (no camera); the live page says so")
        elif not keys:
            say("WARNING: no Presage API key in .env (PRESAGE_API_KEY): no real vitals (the live page shows the error)")
        else:
            say(f"Presage: {len(keys)} API key(s) in .env; when one runs out of credits, the next takes over")

    free_ports([args.port] + ([] if args.no_frontend else [args.frontend_port]))
    if not args.keep_data:
        fresh_start(env)
    backend_url = f"http://127.0.0.1:{args.port}"
    procs: list[tuple[str, subprocess.Popen]] = []

    def start(name: str, cmd: list[str], cwd: Path = ROOT, extra_env: dict | None = None) -> subprocess.Popen:
        p = subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **(extra_env or {})}, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        threading.Thread(target=pipe, args=(p, name), daemon=True).start()
        procs.append((name, p))
        return p

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

    sensor_cmd: list[str] | None = None
    sensor_env: dict = {}
    if sensing and args.legacy_presage:
        start("presage", [sys.executable, "sensing_client/client.py"],
              extra_env={"BACKEND_URL": backend_url, "SESSION_FILE": str(session_file)})
    elif sensing:
        sensor_cmd = [node, "sensing_client/presage_node/sensor.mjs"] + (["--simulate"] if args.simulate_vitals else [])
        sensor_env = {"BACKEND_URL": backend_url}
        if args.no_preview or args.no_frontend:   # the page won't open the camera: don't wait for it
            sensor_env["PRESAGE_WAIT_FOR_PAGE"] = "0"
        start("presage", sensor_cmd, extra_env=sensor_env)
    sensor_started = time.monotonic()
    sensor_restarts = 0
    sensor_restart_at: float | None = None
    if not args.no_frontend:
        fe_env = {"VITE_PROXY_TARGET": backend_url}
        if args.no_preview:
            fe_env["VITE_CAMERA_PREVIEW"] = "0"
        frontend = start("frontend", ["npm", "run", "dev", "--", "--port", str(args.frontend_port), "--strictPort"],
                         cwd=ROOT / "frontend", extra_env=fe_env)
        url = f"http://localhost:{args.frontend_port}"
        if args.no_open:
            say(f"open {url} in Chrome (Chrome is needed for speech-to-text)")
        elif wait_for(url, frontend):
            open_chrome(url)
        else:
            say(f"the frontend didn't come up; when it does, open {url} in Chrome")

    # Watch the links that fail silently: Presage feeding vitals, and the backend itself.
    # A crashed Presage sensor is restarted on its own: it must never take the interview down with it.
    was = None
    presage_was = None
    next_health = time.monotonic() + 5
    while True:
        time.sleep(1)
        for entry in list(procs):
            name, p = entry
            if p.poll() is None:
                continue
            if name == "presage" and sensor_cmd is not None:
                procs.remove(entry)
                if time.monotonic() - sensor_started > SENSOR_STABLE_S:
                    sensor_restarts = 0
                wait = SENSOR_RESTART_S[min(sensor_restarts, len(SENSOR_RESTART_S) - 1)]
                sensor_restarts += 1
                sensor_restart_at = time.monotonic() + wait
                say(f"Presage crashed ({describe_exit(p.returncode)}); restarting it in {wait} s. The backend and "
                    "the page keep running: the interview goes on and vitals resume once Presage is back.")
                continue
            say(f"{name} stopped ({describe_exit(p.returncode)}); stopping the rest")
            stop_all()
        if sensor_restart_at is not None and time.monotonic() >= sensor_restart_at:
            sensor_restart_at = None
            sensor_started = time.monotonic()
            say("restarting Presage")
            start("presage", sensor_cmd, extra_env=sensor_env)
        if time.monotonic() < next_health:
            continue
        next_health = time.monotonic() + 5
        try:
            sensor = get_json(f"{backend_url}/health")["sensor"]
        except Exception:
            say("WARNING: backend /health not answering")
            continue
        presage = sensor.get("presage") or {}
        if presage.get("state") == "error" and presage.get("message") != presage_was:
            say(f"Presage ERROR: {presage['message']}")
        presage_was = presage.get("message") if presage.get("state") == "error" else None
        now = ("signal" if sensor.get("signal") else "no-face") if sensor["live"] else "off"
        if now != was:
            say({"signal": "Presage vitals: LIVE (heart rate coming through)",
                 "no-face": "Presage vitals: arriving, but no heart rate yet (Presage needs 10-20 s of a still "
                            "face; sit facing a light, face and chest in view)",
                 "off": "Presage vitals: not arriving (they're only sent while an interview is on)"}[now])
            was = now


if __name__ == "__main__":
    main()
