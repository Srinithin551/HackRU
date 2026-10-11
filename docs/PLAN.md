# StressTest Build Plan

**Everyone reads this file first, then their own role file.** This file is the single source of truth for how the pieces connect. If you need to change anything in the "Contracts" section, tell the whole team before you push.

| Role | Owner | Plan |
|---|---|---|
| Brain (interview logic, Gemini, ElevenLabs) | Srinithin | [roles/brain.md](roles/brain.md) |
| Data (Tiger Data, metrics, replay) | _teammate_ | [roles/data.md](roles/data.md) |
| Sensing (Presage, vitals ingest) | _teammate_ | [roles/sensing.md](roles/sensing.md) |
| Frontend (React UI, demo, domain) | _teammate_ | [roles/frontend.md](roles/frontend.md) |

---

## 1. How it fits together

```
 ┌──────────────────────────── Browser (Frontend) ─────────────────────────────┐
 │  setup page · live interview page · replay page                             │
 │  plays interviewer audio · mic speech-to-text · live charts                 │
 └───────────────▲──────────────────────────────────────────┬──────────────────┘
                 │ WebSocket events + REST                  │ answer transcripts
                 │                                          ▼
 ┌──────────────────────────── FastAPI backend (one app) ───────────────────────┐
 │                                                                              │
 │  sensing/  POST /api/vitals ──► data.insert_vitals() ──► ws.broadcast(vitals)│
 │                                      │                                       │
 │  data/     Tiger Data reads/writes, stress score, baseline, replay           │
 │                                      ▲                                       │
 │  brain/    interview loop ── reads stats every few seconds ──┘               │
 │            ├─► Gemini (plan questions, decide next move, final report)       │
 │            └─► ElevenLabs (interviewer voice, mp3 files)                     │
 └──────────────────────────────────────┬───────────────────────────────────────┘
                                        ▼
                     Tiger Data (Postgres + TimescaleDB, cloud)
        sessions · questions · vitals (hypertable) · vitals_5s (continuous aggregate)

 Presage sensing client (browser module OR local process on the demo laptop)
        └── POSTs one VitalsReading per second to /api/vitals
```

Key idea: **Sensing writes, Data computes, Brain decides, Frontend shows.** Brain never talks to Presage directly and Sensing never talks to Gemini. Everything flows through Data's functions and the contracts below, which is what lets all four people build in parallel.

## 2. What happens during one question

1. Brain picks the next question (from Gemini) and calls `data.insert_question()`.
2. Brain sends the text to ElevenLabs, saves `/audio/<question_id>.mp3`, and emits a `question` WebSocket event.
3. Frontend plays the audio. When it ends, Frontend sends `answer_started` and turns on the mic.
4. The whole time, Sensing posts a reading every second. Data stores it, computes `stress`, and the ingest route broadcasts a `vitals` event so the live chart moves.
5. Brain's watcher loop reads `data.get_window_stats()` every 3 seconds and emits `coach_flag` events when stress is high (on screen only).
6. User finishes. Frontend sends `answer_done` with the transcript.
7. Brain immediately plays a short filler clip ("Okay."), then calls `data.get_question_stats()` and asks Gemini what to do next (follow up, ease off, push harder, next question, or wrap up).
8. Loop. After the last question Brain generates the report, Data saves it, and Brain emits `session_ended`.

Session start: a 30 to 45 second **calibration** chat ("Hey, how's HackRU going?") runs first. Data computes the person's calm **baseline** from those readings. Every stress number afterward is relative to that baseline. This also covers Presage's warm-up time.

## 3. Contracts (do not change without telling everyone)

All shared types live in **`backend/contracts.py`** as Pydantic models. Brain creates this file in the first hour with exactly these definitions.

```python
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel

Expression = Literal["neutral", "happy", "surprised", "frown", "confused", "unknown"]

class VitalsReading(BaseModel):
    session_id: Optional[str] = None   # if missing, backend uses the active session
    ts: datetime                       # UTC, stamped by the sensing client
    heart_rate: Optional[float] = None       # bpm
    breathing_rate: Optional[float] = None   # breaths per minute
    expression: Optional[Expression] = None
    confidence: float = 1.0                  # 0 to 1, face/signal quality

class VitalsBatch(BaseModel):
    readings: list[VitalsReading]

class Baseline(BaseModel):
    heart_rate: float
    breathing_rate: float
    n_samples: int

class WindowStats(BaseModel):          # "how is the person right now"
    seconds: int
    n_samples: int
    hr_mean: Optional[float]
    hr_delta_pct: Optional[float]      # % above/below baseline
    br_mean: Optional[float]
    br_delta_pct: Optional[float]
    dominant_expression: Optional[str]
    stress: Optional[float]            # 0 to 100

class QuestionStats(BaseModel):        # "how did they react to this question"
    question_id: str
    duration_s: float
    n_samples: int
    hr_mean: Optional[float]
    hr_peak: Optional[float]
    hr_delta_pct: Optional[float]
    br_mean: Optional[float]
    stress_mean: Optional[float]
    stress_peak: Optional[float]
    peak_offset_s: Optional[float]     # seconds into the answer when stress peaked
```

### Stress score (owned by Data, used by everyone)

Defined once in `backend/data/metrics.py`:

```
hr_part    = clamp(hr_delta_pct / 25, 0, 1)            # +25% HR = max
br_part    = clamp(br_delta_pct / 40, 0, 1)            # +40% breathing = max
stress     = 100 * (0.5*hr_part + 0.3*br_part) / 0.8
# There is no focus vital (removed 2026-10-11): Presage never measured it.
```

Missing inputs drop out and the remaining weights are renormalized. Thresholds everyone uses: **< 40 calm, 40 to 70 rising, > 70 high.** Data and Sensing may tune the constants after real testing, but the 0 to 100 scale and the thresholds stay fixed.

### REST endpoints

| Method | Path | Owner | Notes |
|---|---|---|---|
| POST | `/api/sessions` | Brain | body `{user_name, interview_type, role_title}` → `{session_id}` |
| POST | `/api/sessions/{id}/start` | Brain | starts calibration + interview loop in background |
| POST | `/api/sessions/{id}/material` | Brain | optional, before /start: `{filename, data (base64)}` (.pdf .pptx .docx .txt .md) → `{name, kind, chars}`; the questions are planned from it |
| GET | `/api/sessions/active` | Data | `{session_id}` or `null` (sensing client uses this) |
| POST | `/api/vitals` | Sensing (route), Data (storage) | body `VitalsBatch` → `{stored: n}` |
| POST | `/api/sensor/status` | Sensing | body `{state, message, simulated}` from the Presage sensor, broadcast as `sensor` |
| GET | `/api/sessions/{id}/replay` | Data | chart series + questions + per-question stats |
| GET | `/api/sessions/{id}/report` | Brain | Gemini feedback JSON (shape in brain.md) |
| GET | `/audio/{file}.mp3` | Brain | static files from `backend/static/audio/` |
| WS | `/ws/{session_id}` | Brain (hub in `backend/ws.py`) | live events below |

### WebSocket events

Every message is JSON: `{"type": "...", "data": {...}}`.

Server → client:

| type | data | sent by |
|---|---|---|
| `state` | `{state: "calibrating" \| "asking" \| "listening" \| "thinking" \| "done"}` | Brain |
| `question` | `{question_id, index, total, kind, text, audio_url}` | Brain |
| `filler` | `{audio_url}` | Brain |
| `vitals` | `{ts, heart_rate, breathing_rate, stress}` (about 1 per second) | Sensing route |
| `coach_flag` | `{ts, level: "rising" \| "high", message}` | Brain |
| `sensor` | `{state: "idle" \| "starting" \| "warming_up" \| "measuring" \| "adjust" \| "error", message, simulated}` (every 3 s and on change) | Sensing route |
| `session_ended` | `{report_url, replay_url}` | Brain |

Client → server:

| type | data |
|---|---|
| `answer_started` | `{question_id}` |
| `answer_done` | `{question_id, transcript}` |
| `end_session` | `{}` |

`kind` is one of `calibration`, `main`, `follow_up`.

### Python functions Data provides to Brain and Sensing

All in `backend/data/queries.py`, all `async`:

```python
create_session(user_name, interview_type, role_title) -> str
get_active_session() -> str | None
end_session(session_id) -> None
insert_vitals(readings: list[VitalsReading]) -> list[dict]   # returns rows incl. stress, for broadcasting
compute_baseline(session_id) -> Baseline                     # call after calibration answer
get_baseline(session_id) -> Baseline | None
get_window_stats(session_id, seconds=15) -> WindowStats
insert_question(session_id, idx, kind, text) -> str
mark_answer_started(question_id, ts) -> None
mark_answered(question_id, ts, transcript) -> None
get_question_stats(question_id) -> QuestionStats
get_session_history(session_id) -> list[dict]                # questions + transcripts + stats, for Gemini
save_report(session_id, report: dict) -> None
get_report(session_id) -> dict | None
```

**Day-one rule:** Data pushes stub versions of all of these in the first hour (returning fake but correctly shaped values) so Brain can code against them immediately. Real SQL replaces the stubs later with no signature changes.

## 4. Repo layout

```
backend/
  main.py              FastAPI app, mounts routers + /audio static   (Brain)
  config.py            env vars                                       (Brain)
  contracts.py         shared models above                           (Brain, frozen after hour 1)
  ws.py                WebSocket hub: connect, broadcast(session_id, event)   (Brain)
  brain/               interviewer.py, gemini.py, prompts.py, voice.py, watcher.py   (Brain)
  data/                db.py, schema.sql, metrics.py, queries.py, routes.py   (Data)
  sensing/             routes.py (POST /api/vitals)                  (Sensing)
  static/audio/        generated mp3s (gitignored)
  requirements.txt
sensing_client/        Presage client if it runs as a separate process   (Sensing)
frontend/              Vite + React app                               (Frontend)
scripts/
  mock_vitals.py       fake vitals with keyboard-triggered stress spikes   (Data)
docs/
.env.example
```

## 5. Mocks so nobody waits on anybody

| Mock | Owner | Ready by | Who it unblocks |
|---|---|---|---|
| Stubbed `data/queries.py` | Data | hour 1 | Brain |
| `scripts/mock_vitals.py` (press `s` to spike stress) | Data | hour 2 | Brain, Frontend, Data's own testing |
| `BRAIN_FAKE=1` mode: canned questions, no Gemini/ElevenLabs calls, uses pre-made mp3s | Brain | hour 2 | Frontend |
| `frontend/src/mocks/` fixture JSON for replay + fake WS emitter | Frontend | hour 1 | Frontend only |

With `uvicorn backend.main:app` + `mock_vitals.py` + `BRAIN_FAKE=1`, the full app runs end to end before any sponsor API is wired. Real pieces swap in one at a time.

## 6. Timeline

Assumes submissions close Sunday around 10:00 AM. **Check the real deadline and shift everything.**

| When | Goal | Checkpoint |
|---|---|---|
| Sat 1:00 to 2:00 PM | Read docs, set up accounts/keys, create skeleton, `contracts.py`, stubs | Everyone can run the backend locally |
| 2:00 to 6:00 PM | Build your piece against mocks | Each person demos their piece alone |
| **6:00 PM** | **Integration 1:** real Presage → Tiger Data → Brain reads stats → voice plays in browser | One question asked with real data |
| 6:00 PM to 12:00 AM | Adaptive follow-ups, coach flags, replay page, report | |
| **12:00 AM** | **Integration 2:** full 5-question session end to end, replay + report work | Feature freeze for new features |
| 12:00 to 4:00 AM | Bug fixes, latency, UI polish, .tech domain, deploy | |
| 4:00 to 8:00 AM | Sleep in shifts. Someone always awake | |
| 8:00 to 10:00 AM | Demo rehearsal x3, Devpost writeup, demo video, sponsor prize submissions | Submitted |

## 7. Working together

- Branches: `brain/<thing>`, `data/<thing>`, `sensing/<thing>`, `frontend/<thing>`. Small PRs, merge to `main` often (at least every 2 hours).
- Never commit `.env`. Copy `.env.example` and fill in keys locally. Share keys in the group chat, not the repo.
- Only Brain edits `main.py`, `ws.py`, and `contracts.py`. Need a change there? Ask in the chat.
- Every integration checkpoint: everyone pulls `main`, runs the full stack on the **demo laptop**, and fixes what breaks before moving on.
- Post in the group chat when you finish a checklist item so others know what's real vs mocked.

## 8. Environment variables (`.env.example`)

```
GEMINI_API_KEY=
GEMINI_MODEL=              # a fast Flash model, check AI Studio for the current name
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
ELEVENLABS_MODEL=          # a low-latency model, check ElevenLabs docs
PRESAGE_API_KEY=
TIGER_DATABASE_URL=postgres://...
BRAIN_FAKE=0
PUBLIC_BASE_URL=http://localhost:8000
```

## 9. Risks and fallbacks

| Risk | Fallback | Owner |
|---|---|---|
| Presage SDK doesn't run on our laptops/browser | Go to the Presage sponsor table in the first hour. Meanwhile mock_vitals keeps everyone moving | Sensing |
| Gemini too slow between questions | Flash model, short prompts, filler audio, pre-plan questions during calibration | Brain |
| ElevenLabs quota runs out | Cache every generated clip by text hash. Calibration + filler clips generated once | Brain |
| Venue Wi-Fi dies during demo | Phone hotspot ready. Record a backup demo video by 6 AM | Frontend |
| Bad lighting kills heart rate signal | Demo seat faces a light source, no window behind. Test the exact spot before judging | Sensing |
| Something breaks at 3 AM | Keep the Integration 2 commit tagged `demo-safe`. Roll back if needed | Everyone |

## 10. Demo script (2 minutes)

1. (15s) The problem: interview prep tools hear your words, not your panic.
2. (75s) Live: a teammate does a short session. Judges watch the heart rate line. A hard question lands, stress spikes, the coach flag pops, the interviewer adapts.
3. (20s) Replay page: shaded question regions, the spike, Gemini's advice with real numbers.
4. (10s) Sponsor callouts: Presage sensing, Tiger Data time-series + continuous aggregates, Gemini adaptive logic, ElevenLabs voice, stresstest.tech.
