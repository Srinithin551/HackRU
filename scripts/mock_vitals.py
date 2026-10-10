#!/usr/bin/env python
"""Fake Presage: posts one VitalsReading per second to POST /api/vitals.

No session_id is sent, so the backend files readings under the active session.

Keys while running:   s = stress spike   d = distraction   f = face lost   q = quit

    python scripts/mock_vitals.py                                  # post to localhost:8000
    python scripts/mock_vitals.py --auto                           # spike ~10 s into every other question
    python scripts/mock_vitals.py --events "20:s,60:d,100:f" --duration 180
    python scripts/mock_vitals.py --stdout --duration 30           # print NDJSON instead of posting
    python scripts/mock_vitals.py --stdout --fast --duration 600   # 10 min of data instantly

Behavior (docs/roles/data.md):
    baseline   HR 72 +/- 2, BR 14 +/- 1, focus 0.8 +/- 0.05, neutral, confidence 0.95
    s          HR ramps to +25% over 6 s (BR +30%, focus -> 0.5, frown), decays back over 20 s
    d          focus drops to 0.3 for 8 s, HR unchanged
    f          5 s of nulls with confidence 0

Standard library only, except --auto which uses `websockets` (ships with uvicorn[standard]).
"""
import argparse
import json
import os
import queue
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone

BASE_HR, BASE_BR, BASE_FOCUS, CONFIDENCE = 72.0, 14.0, 0.8, 0.95
NOISE_HR, NOISE_BR, NOISE_FOCUS = 2.0, 1.0, 0.05
SPIKE_RAMP_S, SPIKE_DECAY_S = 6, 20
SPIKE_HR, SPIKE_BR, SPIKE_FOCUS = 0.25, 0.30, 0.5     # +25% HR, +30% BR, focus -> 0.5
DISTRACT_S, DISTRACT_FOCUS = 8, 0.3
FACE_LOST_S = 5
EVENTS = {"s": "SPIKE", "d": "DISTRACTION", "f": "FACE LOST"}

AUTO_POLL_S = 2          # how often --auto checks /api/sessions/active
AUTO_SPIKE_DELAY_S = 10  # spike this long after a question is asked
AUTO_FALLBACK_EVERY_S = 60


class VitalsSimulator:
    """Pure function of (tick, triggered events): readings are reproducible with a seeded rng."""

    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.spikes: list[int] = []        # tick each spike started
        self.distract_until = -1
        self.face_lost_until = -1

    def trigger(self, event: str, tick: int) -> None:
        if event == "s":
            self.spikes.append(tick)
        elif event == "d":
            self.distract_until = tick + DISTRACT_S
        elif event == "f":
            self.face_lost_until = tick + FACE_LOST_S

    def spike_level(self, tick: int) -> float:
        """0..1 envelope: linear ramp over SPIKE_RAMP_S, linear decay over SPIKE_DECAY_S."""
        level = 0.0
        for start in self.spikes:
            dt = tick - start
            if 0 <= dt <= SPIKE_RAMP_S:
                level = max(level, dt / SPIKE_RAMP_S)
            elif SPIKE_RAMP_S < dt < SPIKE_RAMP_S + SPIKE_DECAY_S:
                level = max(level, 1 - (dt - SPIKE_RAMP_S) / SPIKE_DECAY_S)
        self.spikes = [s for s in self.spikes if tick - s < SPIKE_RAMP_S + SPIKE_DECAY_S]
        return level

    def _noise(self, amount: float) -> float:
        return max(-amount, min(amount, self.rng.gauss(0, amount / 2)))

    def reading(self, tick: int, ts: datetime) -> dict:
        r = {"ts": ts.isoformat().replace("+00:00", "Z")}
        if tick < self.face_lost_until:
            return r | {"heart_rate": None, "breathing_rate": None, "focus": None,
                        "expression": None, "confidence": 0.0}
        e = self.spike_level(tick)
        hr = BASE_HR * (1 + SPIKE_HR * e) + self._noise(NOISE_HR)
        br = BASE_BR * (1 + SPIKE_BR * e) + self._noise(NOISE_BR)
        focus = BASE_FOCUS - (BASE_FOCUS - SPIKE_FOCUS) * e + self._noise(NOISE_FOCUS)
        if tick < self.distract_until:
            focus = DISTRACT_FOCUS + self._noise(NOISE_FOCUS)
        return r | {"heart_rate": round(hr, 1), "breathing_rate": round(br, 1),
                    "focus": round(min(1.0, max(0.0, focus)), 3),
                    "expression": "frown" if e > 0.25 else "neutral", "confidence": CONFIDENCE}

    def label(self, tick: int) -> str:
        tags = []
        if tick < self.face_lost_until:
            tags.append("FACE LOST")
        if tick < self.distract_until:
            tags.append("DISTRACTED")
        if any(0 <= tick - s < SPIKE_RAMP_S + SPIKE_DECAY_S for s in self.spikes):
            tags.append("SPIKE")
        return " ".join(f"[{t}]" for t in tags)


def parse_events(spec: str) -> dict[int, list[str]]:
    """'20:s,60:d,100:f' -> {20: ['s'], 60: ['d'], 100: ['f']}"""
    events: dict[int, list[str]] = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        sec, _, key = part.partition(":")
        if not sec.strip().isdigit() or key.strip() not in EVENTS:
            raise argparse.ArgumentTypeError(f"bad event {part!r}: use SECONDS:KEY with KEY in s, d, f")
        events.setdefault(int(sec), []).append(key.strip())
    return events


class Keys:
    """Single key presses without Enter: msvcrt on Windows, termios/tty cbreak elsewhere."""

    def __enter__(self):
        self._restore = None
        if os.name != "nt" and sys.stdin.isatty():
            import termios
            import tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            self._restore = lambda: termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return self

    def __exit__(self, *exc):
        if self._restore:
            self._restore()

    def poll(self) -> list[str]:
        keys = []
        if os.name == "nt":
            import msvcrt
            while msvcrt.kbhit():
                keys.append(msvcrt.getwch().lower())
        elif self._restore:
            import select
            while select.select([sys.stdin], [], [], 0)[0]:
                ch = sys.stdin.read(1)
                if not ch:
                    break
                keys.append(ch.lower())
        return keys


class Poster:
    """POSTs a VitalsBatch. On failure keeps up to 30 readings and resends them with the next one."""

    def __init__(self, base_url: str):
        self.url = base_url.rstrip("/") + "/api/vitals"
        self.buffer: deque[dict] = deque(maxlen=30)
        self.sent = self.stored = self.failed = 0

    def send(self, reading: dict) -> str:
        self.buffer.append(reading)
        body = json.dumps({"readings": list(self.buffer)}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                stored = json.load(resp).get("stored")
        except urllib.error.HTTPError as exc:
            self.failed += 1
            detail = exc.read().decode(errors="replace")[:200]
            return f"POST {exc.code}: {detail} (buffered {len(self.buffer)})"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.failed += 1
            reason = getattr(exc, "reason", exc)
            return f"POST failed: {reason} (buffered {len(self.buffer)})"
        self.sent += len(self.buffer)
        self.stored += stored or 0
        self.buffer.clear()
        return f"stored {stored}"


def get_json(url: str, timeout: float = 1.5):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


class AutoSpiker:
    """--auto: poll /api/sessions/active; spike ~10 s into every other question.

    Question timing comes from the `question` events on /ws/{session_id} (PLAN.md section 3).
    If the WebSocket can't be used, fall back to one spike every 60 s.
    """

    def __init__(self, base_url: str, log):
        self.base_url = base_url.rstrip("/")
        self.log = log
        self.session_id: str | None = None
        self.due: queue.Queue[float] = queue.Queue()   # monotonic times a spike should fire
        self._pending: list[float] = []
        self._stop = threading.Event()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                body = get_json(f"{self.base_url}/api/sessions/active")
                sid = body.get("session_id") if isinstance(body, dict) else None
            except (urllib.error.URLError, OSError, ValueError) as exc:
                self.log(f"[auto] /api/sessions/active failed: {getattr(exc, 'reason', exc)}")
                sid = self.session_id          # keep last known state on a blip
            if sid != self.session_id:
                self.session_id = sid
                self.log(f"[auto] active session: {sid or 'none'}")
                if sid:
                    threading.Thread(target=self._watch_questions, args=(sid,), daemon=True).start()
            self._stop.wait(AUTO_POLL_S)

    def _watch_questions(self, sid: str):
        ws_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://") + f"/ws/{sid}"
        try:
            from websockets.sync.client import connect
            with connect(ws_url, open_timeout=3) as ws:
                self.log(f"[auto] listening for questions on {ws_url}")
                n_questions = 0
                while self.session_id == sid and not self._stop.is_set():
                    try:
                        msg = json.loads(ws.recv(timeout=AUTO_POLL_S))
                    except TimeoutError:
                        continue
                    if msg.get("type") != "question" or msg.get("data", {}).get("kind") == "calibration":
                        continue
                    n_questions += 1
                    if n_questions % 2 == 1:       # Q1, Q3, Q5...
                        self.log(f"[auto] question {n_questions}: spike in {AUTO_SPIKE_DELAY_S} s")
                        self.due.put(time.monotonic() + AUTO_SPIKE_DELAY_S)
                    else:
                        self.log(f"[auto] question {n_questions}: staying calm")
            return
        except Exception as exc:    # no websockets package, refused, closed: fall back to a timer
            if self.session_id != sid or self._stop.is_set():
                return
            self.log(f"[auto] can't watch questions ({type(exc).__name__}: {exc}); "
                     f"spiking every {AUTO_FALLBACK_EVERY_S} s instead")
        next_spike = time.monotonic() + AUTO_FALLBACK_EVERY_S
        while self.session_id == sid and not self._stop.is_set():
            if time.monotonic() >= next_spike:
                self.due.put(time.monotonic())
                next_spike += AUTO_FALLBACK_EVERY_S
            self._stop.wait(0.5)

    def spikes_due(self) -> int:
        while not self.due.empty():
            self._pending.append(self.due.get())
        now = time.monotonic()
        fired = [t for t in self._pending if t <= now]
        self._pending = [t for t in self._pending if t > now]
        return len(fired)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000", help="backend base URL (default %(default)s)")
    ap.add_argument("--duration", type=int, default=None, help="stop after this many seconds (default: run forever)")
    ap.add_argument("--events", type=parse_events, default={}, metavar='"20:s,60:d,100:f"',
                    help="fire s/d/f at fixed seconds since start (for when you can't press keys)")
    ap.add_argument("--auto", action="store_true",
                    help="only send while a session is active; spike ~10 s into every other question")
    ap.add_argument("--stdout", action="store_true", help="print readings as NDJSON instead of posting")
    ap.add_argument("--fast", action="store_true", help="with --stdout: no sleeping, timestamps 1 s apart")
    ap.add_argument("--seed", type=int, default=None, help="random seed for reproducible noise")
    args = ap.parse_args(argv)
    if args.fast and not args.stdout:
        ap.error("--fast only works with --stdout")
    if args.auto and args.stdout:
        ap.error("--auto needs a backend, so it can't be combined with --stdout")

    out = sys.stdout
    log_stream = sys.stderr if args.stdout else sys.stdout

    def log(msg: str) -> None:
        print(msg, file=log_stream, flush=True)

    sim = VitalsSimulator(random.Random(args.seed))
    poster = None if args.stdout else Poster(args.url)
    auto = AutoSpiker(args.url, log) if args.auto else None
    pending_keys: list[str] = []

    if not args.stdout:
        log(f"mock_vitals -> {args.url}/api/vitals   keys: s spike, d distraction, f face lost, q quit")
    t0_wall = datetime.now(timezone.utc)
    t0 = time.monotonic()
    tick = 0
    try:
        with Keys() as keys:
            while args.duration is None or tick < args.duration:
                if not args.fast:                        # wait for the next whole second, reading keys
                    while (wait := t0 + tick - time.monotonic()) > 0:
                        pending_keys += keys.poll()
                        time.sleep(min(0.05, wait))
                    pending_keys += keys.poll()
                    # Fell a whole second or more behind (slow POST, machine stall): skip the
                    # missed seconds instead of bursting them out. A real sensor drops readings
                    # too, and the backend must never see two readings in the same second.
                    behind = int(time.monotonic() - t0) - tick
                    if args.duration is not None:
                        behind = min(behind, args.duration - tick - 1)
                    if behind > 0:
                        for skipped in range(tick, tick + behind):
                            pending_keys += args.events.get(skipped, [])
                        log(f"t={tick:4d}s  fell {behind} s behind, skipping to t={tick + behind}s")
                        tick += behind
                if "q" in pending_keys:
                    break
                for key in pending_keys + args.events.get(tick, []):
                    if key in EVENTS:
                        sim.trigger(key, tick)
                        log(f"t={tick:4d}s  >>> {EVENTS[key]}")
                pending_keys.clear()
                if auto:
                    for _ in range(auto.spikes_due()):
                        sim.trigger("s", tick)
                        log(f"t={tick:4d}s  >>> SPIKE (auto)")

                ts = t0_wall + timedelta(seconds=tick) if args.fast else datetime.now(timezone.utc)
                r = sim.reading(tick, ts)
                if auto and not auto.session_id:
                    status = "waiting for an active session" if tick % 5 == 0 else None
                elif args.stdout:
                    print(json.dumps(r), file=out, flush=not args.fast)
                    status = None
                else:
                    status = poster.send(r)
                if status is not None:
                    hr = "  -- " if r["heart_rate"] is None else f"{r['heart_rate']:5.1f}"
                    br = " -- " if r["breathing_rate"] is None else f"{r['breathing_rate']:4.1f}"
                    fo = " -- " if r["focus"] is None else f"{r['focus']:.2f}"
                    log(f"t={tick:4d}s  HR {hr}  BR {br}  focus {fo}  {r['expression'] or '-':8s}"
                        f"conf {r['confidence']:.2f}  {sim.label(tick):14s} {status}")
                tick += 1
    except KeyboardInterrupt:
        pass
    finally:
        if auto:
            auto.stop()
    if poster:
        log(f"done: {tick} ticks, {poster.sent} readings sent, {poster.stored} stored by the backend, "
            f"{poster.failed} failed POSTs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
