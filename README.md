# StressTest

**An AI interviewer that can see you sweat.**

StressTest runs a live mock interview with an AI voice while your webcam tracks your heart rate, breathing, focus, and facial expression. When you're done, you get a replay timeline showing exactly which questions spiked your stress, plus specific coaching on how to answer them better.

Built at **HackRU 2026** (Rutgers University).

---

## The Problem

Interview anxiety is universal, but practice tools only judge *what* you said. They miss *how your body reacted* while you said it. You can't fix the panic if you never see when it happens.

## The Solution

1. Pick an interview type (software engineering, behavioral, class presentation).
2. An AI interviewer asks questions out loud and adapts follow-ups in real time.
3. Your webcam measures physiological signals every second, no wearables needed.
4. After the session, a replay timeline lines up each question with your heart rate, breathing, and focus, and the AI explains where you lost composure and how to recover next time.

---

## Tech Stack and Sponsor Integrations

| Sponsor | Role in StressTest |
|---|---|
| **Presage** | Camera-based vitals: heart rate, breathing rate, focus/engagement, facial expression |
| **ElevenLabs** | Natural voice for the AI interviewer |
| **Gemini API** | Generates questions, adapts follow-ups based on stress signals, writes post-session feedback |
| **Tiger Data** | Time-series storage for per-second vitals; continuous aggregates power the replay timeline |
| **.Tech** | Project domain (stresstest.tech) |
| **Solana** *(stretch)* | Mint a "Certified Calm" badge on devnet when you pass a session under a stress threshold |
| **DEV** | Post-hackathon write-up |

**Frontend:** React
**Backend:** Python (FastAPI)
**Database:** Tiger Data (PostgreSQL + TimescaleDB)

---

## Architecture

```
Webcam ─> C++ SmartSpectra SDK ─> Python bridge ─> Backend API (FastAPI)
                                                       │
Mic ─> Speech-to-text ─────────────────────────────────┤
                                                       ├─> Tiger Data
                                                       │     (vitals, questions, transcripts)
                                                       ├─> Gemini (next question / feedback)
                                                       └─> ElevenLabs (TTS) ─> Browser audio

React Dashboard <── continuous aggregates + replay queries <── Tiger Data
```

### Data model (draft)

```sql
CREATE TABLE sessions (
  id UUID PRIMARY KEY,
  user_name TEXT,
  interview_type TEXT,
  started_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE questions (
  id UUID PRIMARY KEY,
  session_id UUID REFERENCES sessions(id),
  text TEXT,
  asked_at TIMESTAMPTZ,
  answered_at TIMESTAMPTZ
);

CREATE TABLE vitals (
  time TIMESTAMPTZ NOT NULL,
  session_id UUID NOT NULL,
  heart_rate REAL,
  breathing_rate REAL,
  focus REAL,
  expression TEXT
);
SELECT create_hypertable('vitals', 'time');
```

---

## Getting Started

### Prerequisites
- Node.js 18+
- Python 3.12 (`brew install python@3.12`)
- ffmpeg (only to regenerate fake-mode audio: `brew install ffmpeg`)
- API keys: Presage, ElevenLabs, Gemini, Tiger Data connection string

### Environment variables

Copy `.env.example` to `.env` at the repo root and fill in the keys (Gemini, ElevenLabs, Presage, Tiger Data).
With `BRAIN_FAKE=1` the backend runs end to end with no keys at all.

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
.venv/bin/python scripts/run_demo.py            # backend + Presage sensing client + frontend
```

For real vitals, set up Presage once (`sensing_client/presage_macos/README.md`) and add `--presage` to launch it:
press Start in the SmartSpectra window and it writes `~/smartspectra_session.txt`.
Then open http://localhost:5173 in Chrome. The launcher prints when Presage vitals go live.
Useful flags: `--fake` (no Gemini/ElevenLabs calls), `--no-preview` (the browser leaves the webcam to
SmartSpectra), `--no-sensing` (use `scripts/mock_vitals.py` instead of Presage).

### Run pieces by hand

> For a demo, never start the backend with `--reload`: a reload restarts the server and drops the live
> interview. `scripts/run_demo.py` never uses it.

```bash
source .venv/bin/activate
uvicorn backend.main:app                          # backend (BRAIN_FAKE=1 for no API calls)
python sensing_client/client.py                   # Presage → /api/vitals
(cd frontend && npm run dev)                      # http://localhost:5173
python scripts/fake_frontend.py                   # or drive an interview from the terminal
pytest && pytest backend/data/tests               # Brain + Data tests (Data's DB tests run when the URL is set)
```

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

- [ ] **MVP loop:** one question spoken by ElevenLabs, answer recorded, Presage heart rate logged to Tiger Data
- [ ] Gemini generates questions and adapts to stress spikes
- [ ] Live dashboard with heart rate and focus charts
- [ ] Post-session replay timeline with AI feedback
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
