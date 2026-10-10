# StressTest frontend

Vite + React 19 + TypeScript + Tailwind CSS v4 + Recharts 3 + React Router 8.
Owner: Shreyas. Spec: [`docs/roles/frontend.md`](../docs/roles/frontend.md). Contracts: [`docs/PLAN.md`](../docs/PLAN.md) §3 plus Brain's "As built" notes in [`docs/roles/brain.md`](../docs/roles/brain.md).

> **Status:** all three screens are built and verified at 1440×900 (laptop) and 1280×720 (projector) with no
> page scrolling. They work against the real backend or fully offline with `VITE_MOCK=1`. The raw wiring
> test page lives at `/dev`.

## Screens

| Route | Screen |
|---|---|
| `/` | **Setup:** name, interview type, role/topic → creates the session. |
| `/session/:id` | **Live:** your webcam in the middle (mirrored preview, nothing recorded) with live captions, warm-up ring and coach-flag toasts. **Left:** interviewer (speaking waveform / thinking dots), "Question 2 of 5", the question, **I'm done**, questions asked so far. **Right:** heart rate (pulses at your bpm), stress gauge (calm / rising / high), 60 s sparklines, breathing, focus, sensor status. |
| `/session/:id/report` | **Report** (opens automatically when the interview ends): composure score, where you felt it most, top tips, replay timeline (heart rate over stress, question bands, coach-flag dots), question breakdown with the toughest one highlighted, per-question stats + Gemini feedback. |
| `/dev` | **Dev Console:** every WebSocket event logged; for debugging integration. |

Design tokens live in `src/styles/tokens.css` (Tailwind `@theme`) and `src/styles/tokens.ts` (same values for
charts; a test keeps them in sync). Stress colors always come with an icon + word, so they read without color.

| Code | Job |
|---|---|
| `src/hooks/useLiveSession.ts` | The live protocol: socket, audio → `answer_started`, vitals buffer, toasts, auto-navigate on `session_ended`. |
| `src/hooks/useSpeechToText.ts` | Browser speech-to-text; auto-stops after 3.5 s of silence; typing fallback when unsupported. |
| `src/hooks/useCamera.ts` | Webcam preview (video only). Hide it if Sensing's local Presage process needs the camera. |
| `src/components/` | Gauge, sparklines, timeline chart, overlays, shared UI. |

## How the pieces connect

```
 Browser (localhost:5173, Vite dev server)
 ┌──────────────────────────────────────────────────────────────┐
 │ pages / Dev Console                                          │
 │   ├─ src/api.ts ─────────── REST ──┐                         │
 │   ├─ src/lib/socket.ts ─── WS ─────┤ same origin             │
 │   └─ src/lib/playClip.ts ─ audio ──┘                         │
 └───────────────────────────────────────┬──────────────────────┘
          VITE_MOCK=1: never leaves the  │  VITE_MOCK=0: Vite proxy forwards
          browser (src/mocks/ fakes the  │  /api /audio /ws /health to
          whole backend)                 ▼
                         FastAPI (localhost:8000)  uvicorn backend.main:app
                           brain/  data/  sensing/  ws.py
```

| File | Job |
|---|---|
| `src/types.ts` | Every wire type: PLAN.md §3, Data's replay shape, Brain's report (+ `coach_flags`, `adaptations`, `generated_by`), WebSocket events. |
| `src/lib/config.ts` | Reads `VITE_*` env vars. Builds REST, WebSocket and audio URLs (`resolveAudioUrl` rewrites Brain's absolute audio URLs to go through the proxy). |
| `src/api.ts` | REST client: create/start session, replay, report polling, `/health`. Swaps to `src/mocks/mockApi.ts` in mock mode. |
| `src/lib/socket.ts` | WebSocket client: validates events, reconnects with backoff, queues outgoing events while offline. Swaps to the mock socket in mock mode. |
| `src/lib/playClip.ts` | Plays interviewer audio (mp3, mock speech, or none) and reports when it ends, which is when `answer_started` is sent. |
| `src/mocks/` | In-browser fake backend that follows Brain's event order exactly (see "Mock mode"). |
| `src/dev/DevConsole.tsx` | Temporary test page. The browser twin of `scripts/fake_frontend.py`. |
| `src/contract.test.ts` | Fails if Data's `replay_sample.json` stops matching our types. |

## Localhost

| URL | What runs there | Started by |
|---|---|---|
| `http://localhost:5173` | This frontend (Vite dev server with hot reload) | `npm run dev` in `frontend/` |
| `http://localhost:8000` | FastAPI backend: `/api/*`, `/ws/{id}`, `/audio/*.mp3`, `/health` | `uvicorn backend.main:app --reload` at the repo root |
| `http://localhost:8000/docs` | FastAPI's auto-generated API explorer (try every REST endpoint by hand) | same |
| `http://localhost:8000/health` | `{fake, gemini, elevenlabs, routers}`: which pieces are real | same |
| `http://localhost:8000/api/sessions/{id}/status` | Brain's debug view of a running interview | same |

The browser only ever talks to `:5173`. Vite forwards `/api`, `/audio`, `/ws` and `/health` to `:8000`
(`vite.config.ts`, target set by `VITE_PROXY_TARGET`), so there's no CORS to configure.

### Option A: mock mode (no backend, no Python)

```bash
cd frontend
npm install
VITE_MOCK=1 npm run dev          # open http://localhost:5173
```

Click **Create + start**. The mock speaks each question with your browser's voice, then the answer box
unlocks. Type something and press **I'm done**. Fake vitals stream at 1 Hz, a stress spike on the hard
question fires amber/red coach flags, the interviewer eases off, and the report and replay appear at the end.
Add `?mock=auto` to the URL (`http://localhost:5173/?mock=auto`) and the mock answers for you.

### Option B: real backend in fake mode (no API keys)

Terminal 1, backend (repo root):
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
BRAIN_FAKE=1 uvicorn backend.main:app --reload
```
Terminal 2, fake webcam vitals (repo root, standard library only, nothing to install). Press `s` for a stress spike, `d` for distraction, `f` for face lost:
```bash
python3 scripts/mock_vitals.py
```
> ⚠️ As of this commit **`POST /api/vitals` does not exist yet**: it's Sensing's `backend/sensing/routes.py`,
> which hasn't been pushed. Until it lands, mock_vitals.py gets 404s and the live page shows the interview
> but no vitals or coach flags. Use mock mode to see the full vitals experience.
Terminal 3, frontend:
```bash
cd frontend && npm run dev       # VITE_MOCK unset = real backend
```
Open `http://localhost:5173`. `/health` should show `"fake": true` and the mounted routers.

With no `TIGER_DATABASE_URL` in the root `.env`, Data runs **in memory** automatically (`backend/data/db.py`
logs "Data is running IN MEMORY"), so no keys are needed. Its routes (`/replay`, `/active`) still mount.
`/health` → `routers` should list `brain` and `data` (and `sensing` once it exists).

### Environment variables (`frontend/.env.local`, gitignored; template in `.env.example`)

| Var | Default | Meaning |
|---|---|---|
| `VITE_MOCK` | `0` | `1` = run against `src/mocks/`, no backend. |
| `VITE_API_URL` | empty | Empty = same origin via the proxy (recommended). Set e.g. `http://localhost:8000` to call the backend directly (Brain's CORS allows any localhost port). |
| `VITE_PROXY_TARGET` | `http://localhost:8000` | Where the dev proxy forwards. Change it if the backend runs elsewhere. |

The backend's own settings (`BRAIN_FAKE`, `PUBLIC_BASE_URL`, `CORS_ORIGINS`, API keys) live in the repo-root `.env` (see `/.env.example`).
Restart `npm run dev` after editing env files.

## Commands

```bash
npm run dev            # dev server on :5173
npm test               # all unit + contract tests (Vitest)
npx tsc -b             # typecheck only
npm run lint           # oxlint
npm run build          # typecheck + production build into dist/
npm run gen:fixtures   # regenerate src/mocks/generated_{replay,report}.json
```

## Mock mode details

`src/mocks/engine.ts` copies `backend/brain/interviewer.py`:
- **Calibration:** `calibrating`, then 1-2 small-talk questions, with a second one if under 35 s.
- **Each question:** `asking` → `question` → `listening` → answer → `filler` → `thinking`.
- **Follow-ups:** an ease-off follow-up when the answer's stress peak is above 70. It keeps its parent's `index`.
- **End:** a closing filler, then `done` and `session_ended`.

Vitals use PLAN.md's stress formula. Coach flags use the watcher's rule: a 10 s mean above 70 is high, above 40 is rising, with a 12 s cooldown.

Fixture files in `src/mocks/`:
- `replay_sample.json`: **Data's** export of a real Tiger Data session (the contract sample). Never overwrite it.
- `generated_replay.json` + `generated_report.json`: a matched pair from `npm run gen:fixtures`, served for unknown ids in mock mode.

## Contract notes (from Brain's build)

- `audio_url` is absolute or **null**. If it's null, show the text and send `answer_started` immediately (`playClip` does).
- `question.index`/`total` are planned numbers ("Q 2 of 5"). A follow-up keeps its parent's number.
- `GET /report` returns 404 until it's ready. It's always ready by the time `session_ended` arrives.
- `POST /start` is idempotent (`started` / `already_running` / `finished`).
- On reconnect the server replays the latest `state`, `question` and `session_ended`.
- Vitals can be `null` (face lost), and `stress` is `null` until the baseline exists.
- Stress thresholds are fixed team-wide: below 40 calm, 40 to 70 rising, above 70 high.
