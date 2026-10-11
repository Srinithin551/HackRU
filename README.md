# Osiris

**Take the stand. Your body testifies too.**

Osiris is a spoken mock interview with an AI interviewer called **Judge**. While you answer, your webcam reads
your heart rate and breathing, with no wearables. Judge adapts to what you say and to how your body reacts,
and when you lose composure it interrupts you mid-answer. Afterwards a replay lines up every question with
your heart rate and stress, and explains where you broke and how to hold it next time.

Built at **HackRU 2026** (Rutgers University). Early commits and some docs call it by its working name,
*StressTest*.

---

## The problem

Interview practice tools judge *what* you said. They can't see *how your body reacted* while you said it,
so you never find out which question made you panic or how long it took you to recover.

## What Osiris does

1. **Set up.** Pick a trial type (Software Engineering, Behavioral, Class Presentation, or a 3-question Quick
   Demo) and, optionally, **import a file**: your slides or notes, a résumé, or a job description (PDF,
   PowerPoint, Word or text). Judge plans its questions from that file.
2. **Take the stand.** Judge talks with you out loud (ElevenLabs voice). You answer by speaking (or typing).
   - It greets you by the name you typed, then starts with small talk while Presage locks on to your
     pulse and Osiris measures your calm **baseline**.
   - For a **Class Presentation with no file**, Judge first asks what your presentation covers, then plans
     its questions from your answer.
   - After each answer, Gemini decides the next turn: move on, follow up, ease off, push harder, ask you to
     repeat, or step out of the interview to **coach** a wrong or off-topic answer.
   - If your stress rises, Judge **interrupts** on screen with the number that triggered it and one thing to
     do ("Please take one slow breath"). Interrupts go away on their own once read.
   - If you keep **looking away** from the screen while answering (3 look-aways within a minute), Judge says
     it suspects you of cheating, on screen and in the report.
3. **The record.** The report shows:
   - a **composure score** and where Judge saw you break;
   - three tips;
   - a **replay chart**: heart rate over stress, a shaded band for each answer, coach-flag dots, and hover
     notes written by Gemini;
   - per-question numbers and feedback that cite your real measurements;
   - a suspected-cheating notice, if you were flagged for looking away.
4. **The ruling.** Judge delivers its verdict on you, by name. You can take the stand again or adjourn.

---

## Sponsor technology

| Sponsor | How Osiris uses it |
|---|---|
| **Presage** | Camera-based heart rate and breathing rate through the SmartSpectra **Node.js SDK**. It runs headless in the background (no window), one reading per second, each with Presage's own confidence. Presage's framing hints ("Hold still", "No face found") are shown on the live page. |
| **Tiger Data** | Every reading goes into a TimescaleDB **hypertable**. A **continuous aggregate** (5-second buckets, real-time) drives the replay chart. A time-range join between answer windows and vitals gives each question's stats, and the **columnstore** compresses old sessions. |
| **Gemini** | Plans the questions (from your imported file when there is one) and writes every spoken turn after the small talk. It also decides how to adapt after each answer, writes the coaching, the report and the chart notes. Two models with automatic failover. |
| **ElevenLabs** | Judge's voice (one voice for the whole interview), cached on disk by text. |

**Stack:** FastAPI (Python 3.12) · React 19 + Vite + Tailwind 4 + Recharts · MediaPipe face landmarker (look-aways,
in the browser) · Node.js sensor · TimescaleDB.

---

## How it works

```
                 page opens the webcam once, at load ──► POST /api/sensor/page-camera
                                                                  │ (sensor waits for this)
 Webcam ──► Presage sensor (hidden Node process) ── POST /api/vitals, 1/s ──┐
                                                  ── POST /api/sensor/status ┤
                                                                             ▼
 Chrome window (React, :5173) ◄══ WebSocket /ws/{id} ══► FastAPI backend (:8000) ──► Tiger Data
   mic → speech-to-text ──── answers ────────────────►     brain/  interview loop ──► Gemini
   plays Judge's voice ◄──── /audio/*.mp3 ────────────     voice   ──────────────────► ElevenLabs
   report + replay ◄──────── /report, /replay ────────     data/   stress score, baseline, replay
```

**Sensing writes, Data computes, Brain decides, Frontend shows.** `scripts/run_demo.py` starts all of it with one command and opens the app in its own Chrome window. Presage has no window, so Chrome is the only thing you see.

**One interview, end to end**
1. `POST /api/sessions` (and `/material` if you imported a file). The live page opens the WebSocket and calls `/start`.
2. **Warm-up (at least 35 s).** Small talk while Data collects the calm baseline. Gemini plans the questions in
   the background.
3. **Questions.** After each answer the backend reads your vitals for that answer window, and Gemini picks the
   next turn. Code enforces the rules: at most one follow-up and one "didn't catch that" per question, every
   planned question asked within the turn budget, silence never coached. A watcher checks your stress every
   3 s and raises interrupts.
   **Suspected cheating:** while you answer, the page tracks your head and eyes on the camera (MediaPipe face
   landmarker, in the browser, loaded from a CDN). Looking off screen for 1.5 s is one look-away; 3 within a
   minute raise "I suspect you of cheating" on screen and in the report (`backend/brain/integrity.py`).
4. **Report.** The closing line plays while Gemini writes the report. The page then shows the record and the ruling.

**Stress score** (0-100, [`backend/data/metrics.py`](backend/data/metrics.py)), always relative to *your*
baseline:

| Part | Maxes out at | Weight |
|---|---|---|
| Heart rate | +25% over baseline | 5/8 |
| Breathing rate | +40% over baseline | 3/8 |

A missing input drops out and the other is re-weighted. Below 40 is calm, 40 to 70 rising, above 70 high.
Readings below Presage's own accuracy standard (confidence 0.4) are never stored or scored.

**Built to survive a live demo**
- **Every external call has a timeout and a fallback:** canned questions, rule-based decisions, a
  numbers-only report, or text without audio.
- **Presage restarts on its own.** If its engine crashes, the launcher restarts only the sensor, and the
  interview keeps going.
- **API keys fail over.** When a Presage key runs out of credits, the next key in `PRESAGE_BACKUP_API_KEYS`
  takes over.
- **Gemini models fail over.** On a quota error or overload, the other model takes over.
- **Gemini's turns are checked in code.** A question is never asked twice, moving on always asks the planned
  question, coaching is never spoken twice, and the "step out / back in" lines rotate so they don't repeat.
- **A weak pulse never replaces a recent strong one.** Hints wait 2.5 s before showing, so they don't flicker.
- **No camera fight.** The page opens the camera first and Presage second, so the two never fight over the
  camera's mode.

---

## Getting started

### Prerequisites
- An Apple Silicon Mac (the Presage sensor also runs on Linux and Windows)
- Python 3.12, Node.js 20+, Google Chrome (speech-to-text uses Chrome's Web Speech API)
- Keys for Presage, ElevenLabs and Gemini, and a Tiger Data connection string. **None are needed for a
  keyless run** (see below).

### Install
```bash
python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt -r requirements-dev.txt
(cd frontend && npm install)
cp .env.example .env        # fill in the keys; every setting is explained in the file
```

### Database (Tiger Data / TimescaleDB)
Paste a Tiger Data service's connection string into `TIGER_DATABASE_URL`. To run the same engine locally
(needs Docker):

```bash
docker run -d --name stresstest-tigerdb -p 127.0.0.1:5433:5432 \
  -e POSTGRES_PASSWORD=<pick-a-password> -e POSTGRES_DB=stresstest timescale/timescaledb:latest-pg17
# .env:  TIGER_DATABASE_URL=postgres://postgres:<pick-a-password>@127.0.0.1:5433/stresstest
```

With no `TIGER_DATABASE_URL`, Data runs in memory and nothing is saved.

### Run (one command)
```bash
.venv/bin/python scripts/run_demo.py
```

This starts the backend, the hidden Presage sensor and the frontend, then opens Osiris in a Chrome window.
On the first run it installs Presage's Node SDK into `sensing_client/presage_node` (about 400 MB). macOS
asks whether your terminal and Chrome may use the camera: allow both. **Ctrl+C** stops everything.

> ⚠️ **Every launch starts from an empty database.** The launcher drops and recreates the Tiger Data tables
> (`scripts/init_db.py --fresh --columnstore`) and deletes the cached voice clips. Pass `--keep-data` to keep
> earlier sessions.

| Flag | Effect |
|---|---|
| `--fake` | No Gemini or ElevenLabs calls: canned questions and pre-recorded audio |
| `--simulate-vitals` | Artificial Presage vitals, no camera (the page labels them "Simulated vitals") |
| `--keep-data` | Don't empty the database and voice cache at startup |
| `--no-preview` | The page doesn't show the webcam (Presage still uses it) |
| `--no-open` | Don't open the Chrome window |
| `--no-sensing` | No Presage at all (drive vitals with `scripts/mock_vitals.py`) |
| `--legacy-presage` | The old setup: Presage's SmartSpectra app window plus a log-file client |

**No keys at all?** `.venv/bin/python scripts/run_demo.py --fake --simulate-vitals` runs the whole product
offline.

Each run's full output is saved to `backend/logs/run-<date>-<time>.log`, and every Gemini prompt and reply to
`backend/logs/gemini-<date>.jsonl`.

### Tests
```bash
.venv/bin/python -m pytest tests                     # Brain: 228 tests
.venv/bin/python -m pytest backend/data/tests        # Data: 28 (real-database tests run when TIGER_DATABASE_URL is set)
(cd sensing_client/presage_node && npm test)         # Presage sensor: 15
(cd frontend && npx tsc -b && npm run lint && npm test && npm run build)   # Frontend: 63 + typecheck, lint, build
```

---

## Repository map

| Path | What's there |
|---|---|
| [`backend/main.py`](backend/main.py), `ws.py`, `config.py`, `guards.py`, `contracts.py` | FastAPI app, WebSocket hub, settings (`APP_NAME` lives here), the local-only guard, shared types |
| [`backend/brain/`](backend/brain/) | Interview loop, Gemini calls and prompts, turn checks, imported material, Judge's voice, stress watcher, look-away (cheating) watcher, report |
| [`backend/data/`](backend/data/) | Tiger Data schema and queries (and their in-memory twin), stress score ([docs](docs/data/README.md)) |
| [`backend/sensing/routes.py`](backend/sensing/routes.py) | Where readings enter: cleans and stores vitals, Presage status, the page-camera signal |
| [`sensing_client/presage_node/`](sensing_client/presage_node/README.md) | The hidden Presage sensor (Node.js) |
| [`frontend/`](frontend/README.md) | The React app: Setup, Live, Report, Verdict |
| [`scripts/`](scripts/) | `run_demo.py` launcher, `init_db.py`, `mock_vitals.py`, `fake_frontend.py`, export and chart scripts |
| [`docs/`](docs/) | Contracts and build plan ([PLAN.md](docs/PLAN.md)), role guides, Data's Tiger Data analysis ([docs/data](docs/data/README.md)) |

The product name is one variable on each side: `APP_NAME` in [`frontend/src/lib/constants.ts`](frontend/src/lib/constants.ts)
and [`backend/config.py`](backend/config.py), next to `INTERVIEWER_NAME` ("Judge").

---

## Known limits

- **Built for one person on one laptop.** Live interview state is in the backend's memory, so run a single
  backend process (no `--workers`, never `--reload` during a demo). Sensor vitals go to the newest interview.
- **No login.** The sensor endpoints only answer requests from the same machine, and the WebSocket only
  accepts the app's own origin. Don't expose the backend to the internet as is.
- **What's stored:** transcripts, vitals and the report go to the database, and Gemini prompts and replies
  (transcripts included) go to `backend/logs/`. Video never leaves the camera.
- **Presage needs light and stillness.** Sit facing a light with your face and chest in view. The first
  stable pulse takes 10-20 s, which the warm-up covers.
- **The Gemini free tier is small.** `gemini-3.6-flash` allows about 20 requests a day; after that Osiris
  falls back to `gemini-3.5-flash-lite`.
- **Look-away detection needs internet.** The MediaPipe face landmarker loads from a CDN; without it the
  interview still runs, just with no cheating check. Glancing away to think is fine: it takes 3 look-aways
  within a minute to be flagged.
- **Large frontend bundle.** The production build warns about a chunk over 500 kB (the chart library).

---

## Team

| Area | Owner | Guide |
|---|---|---|
| Brain: Gemini, ElevenLabs, the interview loop | Srinithin | [docs/roles/brain.md](docs/roles/brain.md) |
| Data: Tiger Data, stress score, replay | Likhil | [docs/roles/data.md](docs/roles/data.md) |
| Sensing: Presage, vitals ingest | Sharvil | [docs/roles/sensing.md](docs/roles/sensing.md) |
| Frontend: React UI, demo | Shreyas | [docs/roles/frontend.md](docs/roles/frontend.md) |

The role guides are the plans written at the start of the hackathon; this README describes what was built.

## License

MIT, see [LICENSE.md](LICENSE.md).
