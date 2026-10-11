# Osiris build plan and contracts

The team's shared plan from HackRU 2026 (written when the app was still called *StressTest*). Sections 1-5
and 8 are kept up to date with what was built; sections 6, 7 and 9 are the original plan, kept for the
record. The product overview is in the [README](../README.md).

**Contracts (section 3) are the single source of truth for how the pieces connect.**

| Role | Owner | Guide |
|---|---|---|
| Brain (interview logic, Gemini, ElevenLabs) | Srinithin | [roles/brain.md](roles/brain.md) |
| Data (Tiger Data, metrics, replay) | Likhil | [roles/data.md](roles/data.md) |
| Sensing (Presage, vitals ingest) | Sharvil | [roles/sensing.md](roles/sensing.md) |
| Frontend (React UI, demo) | Shreyas | [roles/frontend.md](roles/frontend.md) |

---

## 1. How it fits together

```
 ┌──────────────────────────── Browser (Frontend) ─────────────────────────────┐
 │  setup (file import) · live interview · report + replay · verdict           │
 │  plays Judge's voice · mic speech-to-text · live vitals · webcam preview    │
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

 Presage sensor: sensing_client/presage_node/sensor.mjs, a hidden Node.js process (SmartSpectra Node SDK)
        └── POSTs one VitalsReading per second to /api/vitals, and its status to /api/sensor/status
```

Key idea: **Sensing writes, Data computes, Brain decides, Frontend shows.** Brain never talks to Presage directly and Sensing never talks to Gemini. Everything flows through Data's functions and the contracts below, which is what lets all four people build in parallel.

## 2. What happens during one question

1. Brain has the next turn (written by Gemini: a short reaction, then the question) and calls `data.insert_question()`.
2. Brain sends the text to ElevenLabs (one voice, Judge's), caches the mp3 under `/audio/<hash>.mp3`, and emits a `question` WebSocket event. Coaching on the previous answer, if any, rides in the same clip and in `question.coaching`.
3. Frontend plays the audio. When it ends, Frontend sends `answer_started` and turns on the mic.
4. The whole time, Sensing posts a reading every second. Data stores it, computes `stress`, and the ingest route broadcasts a `vitals` event so the live numbers move.
5. Brain's watcher reads `data.get_window_stats()` every 3 seconds and emits `coach_flag` events when stress rises: a message citing the number plus one command, shown as an interrupt that goes away on its own once read.
6. User finishes (silence auto-stop or "I've answered"). Frontend sends `answer_done` with the transcript.
7. Brain calls `data.get_question_stats()` and asks Gemini what to do next: `next`, `follow_up`, `ease_off`, `push`, `redirect` ("didn't catch that") or `coach` (step out and give a model answer). Code enforces the limits. There is no filler clip between turns.
8. Loop. After the last question the closing line plays (the only `filler` event) while Brain writes the report; Data saves it, and Brain emits `session_ended`.

Session start: a **calibration** chat of at least 35 seconds runs first ("tell me a little about yourself"; for a Class Presentation with no imported file, "walk me through your presentation", and the questions are then planned from the answer). Data computes the person's calm **baseline** from those readings. Every stress number afterward is relative to that baseline. Presage starts before the interview (pre-warm), so its 10-20 s lock-on is already done.

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
| GET | `/api/sessions/{id}/insights` | Brain | Gemini-written notes per question and variable, for the replay chart's tooltips |
| GET | `/api/sessions/active` | Data | `{session_id}` or `null` (sensing client uses this) |
| POST | `/api/vitals` | Sensing (route), Data (storage) | body `VitalsBatch` → `{stored: n}` |
| POST | `/api/sensor/status` | Sensing | body `{state, message, simulated}` from the Presage sensor, broadcast as `sensor` |
| POST / GET | `/api/sensor/page-camera` | Sensing | `{open}`: the page reports its webcam open (it opens it once, at load); the sensor waits for it before starting Presage |
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
| `question` | `{question_id, index, total, kind, text, coaching, audio_url}` | Brain |
| `filler` | `{audio_url}`: the closing line only | Brain |
| `vitals` | `{ts, heart_rate, breathing_rate, stress}` (about 1 per second) | Sensing route |
| `coach_flag` | `{ts, level: "rising" \| "high", message, command}` | Brain |
| `sensor` | `{state: "idle" \| "starting" \| "warming_up" \| "measuring" \| "adjust" \| "error", message, simulated}` (every 3 s and on change) | Sensing route |
| `integrity_flag` | `{ts, question_id, message, command, look_aways}`: "I suspect you of cheating" after repeated look-aways | Brain (`integrity.py`) |
| `session_ended` | `{report_url, replay_url}` | Brain |

Client → server:

| type | data |
|---|---|
| `answer_started` | `{question_id}` |
| `answer_done` | `{question_id, transcript}` |
| `look_away` | `{question_id}`: the candidate looked off screen for 1.5 s while answering |
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
  main.py              FastAPI app, mounts routers, /audio, /health          (Brain)
  config.py            env vars, APP_NAME                                    (Brain)
  contracts.py         shared models above                                   (Brain)
  ws.py, guards.py     WebSocket hub; local-only guard for sensor endpoints  (Brain)
  brain/               interviewer, gemini, prompts, material, voice, watcher, report, insights   (Brain)
  data/                db.py, schema.sql, columnstore.sql, metrics.py, queries.py, routes.py      (Data)
  sensing/             routes.py: /api/vitals, /api/sensor/status, /api/sensor/page-camera       (Sensing)
  static/audio/        generated mp3s (gitignored, emptied every launch)
  logs/                Gemini log and per-run launcher logs (gitignored)
sensing_client/
  presage_node/        the hidden Presage sensor (Node.js)                   (Sensing)
  presage_macos/, client.py   the earlier SmartSpectra-app setup (--legacy-presage)
frontend/              Vite + React app: Setup, Live, Report, Verdict        (Frontend)
scripts/
  run_demo.py          one-command launcher (wipes data, supervises the sensor, opens Chrome)
  init_db.py           schema + checks (--fresh is the launcher's wipe)      (Data)
  mock_vitals.py       fake vitals with keyboard-triggered stress spikes     (Data)
  fake_frontend.py     drives an interview from the terminal                 (Brain)
docs/
.env.example
```

## 5. Mocks so nobody waits on anybody

| Mock | Owner | Ready by | Who it unblocks |
|---|---|---|---|
| Stubbed `data/queries.py` | Data | hour 1 | Brain |
| `scripts/mock_vitals.py` (`s` = stress spike, `f` = face lost) | Data | hour 2 | Brain, Frontend, Data's own testing |
| `sensor.mjs --simulate` / `run_demo.py --simulate-vitals`: a scripted Presage | Sensing | added later | everyone, without a camera |
| `BRAIN_FAKE=1` mode: canned questions, no Gemini/ElevenLabs calls, uses pre-made mp3s | Brain | hour 2 | Frontend |
| `frontend/src/mocks/` fixture JSON for replay + fake WS emitter | Frontend | hour 1 | Frontend only |

With `uvicorn backend.main:app` + `mock_vitals.py` + `BRAIN_FAKE=1`, the full app runs end to end before any sponsor API is wired. Real pieces swap in one at a time.

## 6. Timeline

The original plan, kept for the record.

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

## 8. Environment variables

Every setting, with an explanation, is in [`.env.example`](../.env.example): Gemini (`GEMINI_API_KEY`, the two
models, thinking level), ElevenLabs (key, `ELEVENLABS_VOICE_ID` = Judge's voice, model), Presage
(`PRESAGE_API_KEY`, `PRESAGE_BACKUP_API_KEYS`, `PRESAGE_CAMERA`), `TIGER_DATABASE_URL`, `DATA_MIN_CONFIDENCE`,
`BRAIN_FAKE`, `PUBLIC_BASE_URL`, `CORS_ORIGINS` and `ALLOW_REMOTE_SENSOR`.

## 9. Risks and fallbacks (the original plan)

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
2. (75s) Live: a teammate takes the stand for a Class Presentation, importing their slides. Judge questions them out loud on those slides while the heart rate moves. A hard question lands, stress spikes, Judge interrupts with the number, and adapts.
3. (20s) The record: shaded answer bands, the spike, Gemini's notes and advice with real numbers, then the ruling.
4. (10s) Sponsor callouts: Presage (headless webcam vitals), Tiger Data (hypertable, continuous aggregate, columnstore), Gemini (planning from your file, adaptive turns, report), ElevenLabs (Judge's voice).
