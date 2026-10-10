#!/usr/bin/env python3
"""
StressTest sensing client (Option B: local process on the demo laptop).

The SmartSpectra macOS app owns the webcam and appends metrics to a log file.
This client follows that file live, turns it into one VitalsReading per second
(see backend/contracts.py), and POSTs batches to /api/vitals while a session
is active.

Run (from the repo root):
    python3 sensing_client/client.py             # normal mode, talks to the backend
    python3 sensing_client/client.py --dry-run   # no backend, just prints readings

Env vars:
    BACKEND_URL   default http://localhost:8000
    SESSION_FILE  default ~/smartspectra_session.txt

Standard library only, no pip installs needed.
"""
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
SESSION_FILE = Path(os.environ.get("SESSION_FILE", "~/smartspectra_session.txt")).expanduser()

# Tuning knobs (adjust after real-world testing)
SMOOTH_WINDOW_S = 3.0   # rolling median window for heart rate and breathing rate
STALE_DECAY = 0.7       # confidence multiplier per second when repeating an old value
NO_FACE_AFTER_S = 3.0   # no new pulse reading for this long -> "no face" (nulls, confidence 0)
BR_HOLD_S = 15.0        # breathing rate updates slowly; keep the last value this long
SESSION_POLL_S = 2.0    # how often to ask the backend for the active session
MAX_BUFFER = 30         # readings kept if POSTs fail
HTTP_TIMEOUT_S = 2.0

PULSE_RE = re.compile(r"^Pulse rate: ([\d.]+) bpm \(([\d.]+)%\)")
BREATH_RE = re.compile(r"^Breathing rate: ([\d.]+) bpm \(([\d.]+)%\)")
EDA_RE = re.compile(r"^EDA Proxy level: (-?[\d.]+)")


def log(msg):
    print(f"{datetime.now().strftime('%H:%M:%S')}  {msg}", flush=True)


class LogFollower:
    """Reads new lines appended to the SmartSpectra log, like `tail -f`."""

    def __init__(self, path):
        self.path = path
        self.f = None
        self.inode = None
        self.partial = ""
        self.first_open = True

    def _open(self):
        try:
            self.f = self.path.open("r")
            self.inode = os.fstat(self.f.fileno()).st_ino
        except FileNotFoundError:
            self.f = None
            return
        if self.first_open:
            self.f.seek(0, os.SEEK_END)  # skip old sessions already in the file
        self.first_open = False
        self.partial = ""
        log(f"following {self.path}")

    def read_lines(self):
        if self.f is None:
            self._open()
            if self.f is None:
                self.first_open = False  # file appears later -> read it from the start
                return []
        try:
            st = self.path.stat()
            if st.st_ino != self.inode or st.st_size < self.f.tell():
                self.f.close()  # file was replaced or truncated: start over
                self.f = None
                self._open()
                if self.f is None:
                    return []
        except FileNotFoundError:
            return []
        chunk = self.f.read()
        if not chunk:
            return []
        lines = (self.partial + chunk).split("\n")
        self.partial = lines.pop()  # last piece may be an unfinished line
        return lines


class VitalsState:
    """Keeps recent Presage values and builds one smoothed reading on demand."""

    def __init__(self):
        self.pulse = deque()   # (arrival_time, bpm, confidence 0-1)
        self.breath = deque()  # (arrival_time, bpm, confidence 0-1)
        self.eda = None        # (arrival_time, level)

    def ingest(self, line, now):
        line = line.strip()
        if m := PULSE_RE.match(line):
            self.pulse.append((now, float(m.group(1)), float(m.group(2)) / 100))
        elif m := BREATH_RE.match(line):
            self.breath.append((now, float(m.group(1)), float(m.group(2)) / 100))
        elif m := EDA_RE.match(line):
            self.eda = (now, float(m.group(1)))

    def latest_eda(self, now):
        if self.eda and now - self.eda[0] <= NO_FACE_AFTER_S:
            return self.eda[1]
        return None

    def reading(self, now, session_id):
        keep = max(BR_HOLD_S, SMOOTH_WINDOW_S, NO_FACE_AFTER_S)
        for q in (self.pulse, self.breath):
            while q and now - q[0][0] > keep:
                q.popleft()

        r = {
            "session_id": session_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "heart_rate": None,
            "breathing_rate": None,
            "focus": None,        # not provided by the Presage C++ SDK
            "expression": None,   # not provided by the Presage C++ SDK
            "confidence": 0.0,
        }

        # No fresh pulse -> treat as no face: all metrics null, confidence 0.
        if not self.pulse or now - self.pulse[-1][0] > NO_FACE_AFTER_S:
            return r

        window = [p[1] for p in self.pulse if now - p[0] <= SMOOTH_WINDOW_S]
        r["heart_rate"] = round(statistics.median(window), 1)

        # Confidence from Presage's pulse confidence; decays if we're repeating an old value.
        age = now - self.pulse[-1][0]
        conf = self.pulse[-1][2] * (STALE_DECAY ** int(age))
        r["confidence"] = round(max(0.0, min(1.0, conf)), 2)

        bwin = [b[1] for b in self.breath if now - b[0] <= SMOOTH_WINDOW_S]
        if bwin:
            r["breathing_rate"] = round(statistics.median(bwin), 1)
        elif self.breath and now - self.breath[-1][0] <= BR_HOLD_S:
            r["breathing_rate"] = round(self.breath[-1][1], 1)
        return r


def http_json(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BACKEND_URL + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def get_active_session():
    result = http_json("GET", "/api/sessions/active")
    if isinstance(result, dict):
        return result.get("session_id")
    if isinstance(result, str):
        return result
    return None


def fmt(value, spec):
    return format(value, spec) if value is not None else "  --"


def print_debug(r, eda, status):
    face = "" if r["heart_rate"] is not None else "  NO FACE"
    print(f"{datetime.now().strftime('%H:%M:%S')}  "
          f"HR {fmt(r['heart_rate'], '5.1f')}  BR {fmt(r['breathing_rate'], '4.1f')}  "
          f"conf {r['confidence']:.2f}  EDA {fmt(eda, '+.3f')}{face}  | {status}", flush=True)


def main():
    dry_run = "--dry-run" in sys.argv
    follower = LogFollower(SESSION_FILE)
    vitals = VitalsState()
    buffer = []
    session_id = "dry-run" if dry_run else None
    backend_ok = True
    next_poll = 0.0
    next_tick = time.monotonic()

    log(f"sensing client started ({'DRY RUN' if dry_run else BACKEND_URL})")
    log("start the SmartSpectra app and press Start; Ctrl+C to quit")

    while True:
        try:
            now = time.monotonic()
            for line in follower.read_lines():
                vitals.ingest(line, now)

            if not dry_run and now >= next_poll:
                next_poll = now + SESSION_POLL_S
                try:
                    new_id = get_active_session()
                    if not backend_ok:
                        log("backend reachable again")
                    backend_ok = True
                except Exception as e:
                    new_id = None
                    if backend_ok:
                        log(f"backend unreachable ({e.__class__.__name__}); retrying")
                    backend_ok = False
                if new_id != session_id:
                    log(f"session started: {new_id}" if new_id else "session ended")
                    if new_id is None:
                        buffer.clear()
                    session_id = new_id

            reading = vitals.reading(now, session_id)
            status = "waiting for session"
            if dry_run:
                status = "dry run"
            elif session_id:
                buffer.append(reading)
                del buffer[:-MAX_BUFFER]
                try:
                    http_json("POST", "/api/vitals", {"readings": buffer})
                    status = f"sent {len(buffer)}"
                    buffer.clear()
                except Exception as e:
                    status = f"POST failed ({e.__class__.__name__}), buffered {len(buffer)}"
            print_debug(reading, vitals.latest_eda(now), status)
        except Exception as e:
            log(f"unexpected error: {e!r}; continuing")

        next_tick += 1.0
        delay = next_tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_tick = time.monotonic()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped")
