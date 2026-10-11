# StressTest

**An AI interviewer that can see you sweat.**

StressTest runs a live mock interview with an AI voice while your webcam tracks your heart rate and breathing. When you're done, you get a replay timeline showing exactly which questions spiked your stress, plus specific coaching on how to answer them better.

Built at **HackRU 2026** (Rutgers University).

---

## The Problem

Interview anxiety is universal, but practice tools only judge *what* you said. They miss *how your body reacted* while you said it. You can't fix the panic if you never see when it happens.

## The Solution

1. Pick an interview type (software engineering, behavioral, class presentation, or a quick 3-question demo).
2. An AI interviewer holds a spoken conversation: small talk first, then questions that adapt to your answers. A wrong or off-topic answer gets immediate coaching before the next question.
3. Your webcam measures heart rate and breathing every second (Presage), no wearables needed.
4. After the session, a replay timeline lines up each question with your heart rate and stress, and the AI explains where you lost composure and how to recover next time.

---

## Tech Stack and Sponsor Integrations

| Sponsor | Role in StressTest |
|---|---|
| **Presage** | Camera-based vitals (heart rate, breathing rate) through the SmartSpectra Node SDK, running hidden in the background |
| **ElevenLabs** | Natural voice for the AI interviewer |
| **Gemini API** | Plans the questions, writes every conversational turn and the live coaching, adapts to stress signals, writes post-session feedback |
| **Tiger Data** | Time-series storage for per-second vitals; continuous aggregates power the replay timeline |
| **.Tech** | Project domain (stresstest.tech) |
| **Solana** *(stretch)* | Mint a "Certified Calm" badge on devnet when you pass a session under a stress threshold |
| **DEV** | Post-hackathon write-up |

**Frontend:** React 19 + Vite + Tailwind ([frontend/README.md](frontend/README.md))
**Backend:** Python (FastAPI)
**Database:** Tiger Data (PostgreSQL + TimescaleDB), or in memory when no database is configured
**Sensor:** Node.js + Presage SmartSpectra SDK ([sensing_client/presage_node/README.md](sensing_client/presage_node/README.md))

Presage's SDK can also report focus and facial expression, but this build doesn't request them, so the
stress score uses heart rate and breathing only (the formula renormalizes the weights of missing inputs).

---

## How it works

```
 Webcam ─> Presage sensor (hidden Node process) ──POST /api/vitals, 1/s──┐
                                                                         ▼
 Browser (React, :5173) <══ WebSocket /ws/{id} ══> FastAPI backend (:8000) ──> Tiger Data
   mic → speech-to-text ─── answers ──────────────>   brain/  interview loop ──> Gemini
   plays interviewer audio <── /audio/*.mp3 ──────    voice   ─────────────────> ElevenLabs
   replay + report  <── GET /replay, /report ─────    data/   stress score, baseline, replay
```

One interview, end to end:

1. **Setup page** → `POST /api/sessions`. The live page opens the WebSocket, then calls `/start`.
2. **Small talk (calibration, ~35 s).** While you chat, the Presage sensor (it starts when an interview
   starts) locks on, and Data computes your calm **baseline**. Every stress number afterwards is relative to it.
3. **Questions.** Gemini planned them during the small talk. After each answer the backend reads your vitals
   for that answer, and Gemini decides the next turn: move on, follow up, ease off, push harder, ask you to
   repeat, or step out of the interview to coach you. Code enforces the rules (one follow-up per question,
   every planned question gets asked within the turn budget). Coach flags pop up on screen when stress rises.
4. **Report.** The closing line plays while Gemini writes the report; the page then shows the replay chart
   (5-second buckets from a TimescaleDB continuous aggregate) and per-question feedback that cites real numbers.

Every external call has a timeout and a fallback (canned questions, rule-based decisions, a numbers-only
report, text without audio), so a slow or missing API never stalls a live session.

**Stress score** (0-100, [backend/data/metrics.py](backend/data/metrics.py)): heart rate vs your baseline
(+25% = max) weighted 0.5, breathing (+40% = max) 0.3, focus drop 0.2, with missing inputs dropped and the
rest renormalized. Below 40 is calm, 40 to 70 rising, above 70 high.

**Data model:** [backend/data/schema.sql](backend/data/schema.sql). `sessions` (setup, baseline, report),
`questions` (each spoken turn with its answer window and transcript), `vitals` (a hypertable, one row per
second, stress computed on insert) and `vitals_5s` (the continuous aggregate behind the replay).

### Limits worth knowing

- **One person, one laptop.** All live interview state is in the backend's memory, so run a single backend
  process (no `--workers`, no `--reload` during a demo). Sensor vitals always go to the newest session.
- **No login.** Instead: the sensor endpoints (`/api/vitals`, `/api/sensor/status`, `/api/sessions/active`)
  only answer requests from this machine (`ALLOW_REMOTE_SENSOR=1` lifts that), browsers may only open the
  WebSocket from the app's own origin (`CORS_ORIGINS` or any localhost port), and `/start` only starts
  sessions that exist. Don't expose the backend to the internet as is.
- **What's stored:** transcripts, vitals and the report go to the database, and every Gemini prompt and reply
  (transcripts included) to `backend/logs/`. Video never leaves the camera.

---

## Getting Started

### Prerequisites
- Python 3.12 (`brew install python@3.12`; 3.13+ also works)
- Node.js 20+ (the frontend and the Presage sensor)
- Google Chrome (speech-to-text uses its Web Speech API)
- API keys: Presage, ElevenLabs, Gemini, Tiger Data connection string (none are needed for fake mode)

### Environment variables

Copy `.env.example` to `.env` at the repo root and fill in the keys. Every setting is explained there.
With `BRAIN_FAKE=1` and no `TIGER_DATABASE_URL`, everything runs end to end with no keys at all.

### Database (Tiger Data / TimescaleDB)

Tiger Data runs on TimescaleDB. For a Tiger Data cloud service, paste its connection string into
`TIGER_DATABASE_URL` in `.env`. To run the same engine locally for free (needs Docker Desktop):

```bash
docker run -d --name stresstest-tigerdb --restart unless-stopped -p 127.0.0.1:5433:5432 \
  -e POSTGRES_PASSWORD=<pick-a-password> -e POSTGRES_DB=stresstest \
  -v stresstest-tigerdb:/var/lib/postgresql/data timescale/timescaledb:latest-pg17
# then in .env:  TIGER_DATABASE_URL=postgres://postgres:<pick-a-password>@127.0.0.1:5433/stresstest
python scripts/init_db.py --columnstore      # hypertable, 5 s continuous aggregate, columnstore
```

Without `TIGER_DATABASE_URL`, Data runs in memory (nothing is saved). `/health` shows which is live
(`"data_store": "tiger_data"` or `"in_memory"`).

### Run everything (one command)

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
(cd frontend && npm install)
.venv/bin/python scripts/run_demo.py            # backend + hidden Presage sensor + frontend, opens Chrome
```

The first run installs Presage's Node SDK into `sensing_client/presage_node` (about 400 MB) and macOS asks
whether your terminal may use the camera. The launcher prints when Presage vitals go live and stops
everything on Ctrl+C.

| Flag | Effect |
|---|---|
| `--fake` | `BRAIN_FAKE=1`: canned questions and pre-recorded audio, no Gemini or ElevenLabs calls |
| `--simulate-vitals` | artificial Presage vitals, no camera (the live page labels them "Simulated") |
| `--no-sensing` | no Presage at all (drive vitals with `scripts/mock_vitals.py` instead) |
| `--no-preview` | the page doesn't show the webcam (Presage still uses it) |
| `--no-open` | don't open the Chrome window |
| `--legacy-presage` | old setup: the SmartSpectra app window + `sensing_client/client.py` ([sensing_client/presage_macos/README.md](sensing_client/presage_macos/README.md)) |

No keys at all? `.venv/bin/python scripts/run_demo.py --fake --simulate-vitals` runs the whole product.

### Run pieces by hand

> For a demo, never start the backend with `--reload`: a reload restarts the server and drops the live
> interview. `scripts/run_demo.py` never uses it.

```bash
source .venv/bin/activate
uvicorn backend.main:app                          # backend (BRAIN_FAKE=1 for no API calls)
node sensing_client/presage_node/sensor.mjs       # Presage → /api/vitals (add --simulate for no camera)
(cd frontend && npm run dev)                      # http://localhost:5173
python scripts/fake_frontend.py                   # or drive an interview from the terminal
python scripts/mock_vitals.py                     # or fake vitals with keyboard spikes (s / d / f)
```

### Tests

```bash
pytest                                            # Brain (tests/) + Data (backend/data/tests/)
(cd frontend && npm test)                         # mock engine, replay/report builders, contracts
(cd sensing_client/presage_node && npm test)      # Presage → VitalsReading mapping, simulator
```

Data's `test_queries_db.py` runs against the real Tiger Data service whenever `TIGER_DATABASE_URL` is set
(it deletes every row it writes) and skips otherwise.

---

## Repository map

| Path | What's there |
|---|---|
| `backend/main.py`, `ws.py`, `config.py`, `guards.py` | FastAPI app, WebSocket hub, settings, the local-only guard |
| `backend/brain/` | Interview loop, Gemini calls and prompts, ElevenLabs voice, coach-flag watcher, report |
| `backend/data/` | Tiger Data queries (and their in-memory twin), stress score, schema ([docs/data/README.md](docs/data/README.md)) |
| `backend/sensing/routes.py` | `POST /api/vitals` (cleans and stores readings) and `POST /api/sensor/status` |
| `sensing_client/` | The Presage sensor ([presage_node](sensing_client/presage_node/README.md)) and the legacy app-window setup ([presage_macos](sensing_client/presage_macos/README.md)) |
| `frontend/` | The React app ([frontend/README.md](frontend/README.md)) |
| `scripts/` | `run_demo.py` launcher, `init_db.py`, `mock_vitals.py`, `fake_frontend.py`, export and chart scripts |
| `docs/` | Build plan and contracts ([PLAN.md](docs/PLAN.md)), role guides, Data's analysis |

---

## Team Roles

Full build plan and integration contracts: **[docs/PLAN.md](docs/PLAN.md)**

| Area | Owner | Plan |
|---|---|---|
| Brain (Gemini, ElevenLabs, interview loop) | Srinithin | [docs/roles/brain.md](docs/roles/brain.md) |
| Data (Tiger Data, stress score, replay) | Likhil | [docs/roles/data.md](docs/roles/data.md) |
| Sensing (Presage, vitals ingest) | Sharvil | [docs/roles/sensing.md](docs/roles/sensing.md) |
| Frontend (React UI, domain, demo) | Shreyas | [docs/roles/frontend.md](docs/roles/frontend.md) |

---

## Build Plan

- [x] **MVP loop:** one question spoken by ElevenLabs, answer recorded, Presage heart rate logged to Tiger Data
- [x] Gemini generates questions and adapts to stress spikes
- [x] Live dashboard with heart rate and stress charts
- [x] Post-session replay timeline with AI feedback
- [ ] Register .tech domain and deploy
- [ ] *(Stretch)* Solana "Certified Calm" badge
- [ ] Demo script and slides
- [ ] DEV post

## Demo

1. A teammate starts a software engineering interview live.
2. Judges watch the heart rate line on the dashboard.
3. A hard question lands, the line spikes, and the AI calls it out.
4. Replay shows the exact moment and Gemini's coaching.

---

## License

MIT
