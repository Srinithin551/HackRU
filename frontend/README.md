# Osiris frontend

Vite + React 19 + TypeScript + Tailwind CSS v4 + Recharts 3 + React Router.
Owner: Shreyas. Contracts: [`docs/PLAN.md`](../docs/PLAN.md) §3, plus Brain's notes in [`docs/roles/brain.md`](../docs/roles/brain.md).

Every screen fits a laptop (1440×900) or projector (1280×720) without page scrolling, and works against the real
backend or fully offline with `VITE_MOCK=1`.

## Screens

| Route | Screen |
|---|---|
| `/` | **Setup:** name, trial type, role or presentation topic, and an optional **file import** (slides, notes, a résumé or a job description; PDF, PowerPoint, Word or text, up to 10 MB). Creates the session, uploads the file (`POST /api/sessions/{id}/material`) and goes to the interview. |
| `/session/:id` | **Live:** **left**, Judge (one interviewer: speaking halo, thinking dots), "Question 2 of 5" with a transition banner on every new question, the question, the answer controls and the questions asked so far. **Right**, your webcam (mirrored, nothing recorded) with live captions, the warm-up ring, Presage hints and Judge's **interrupts**. Under it: heart rate, composure gauge and breathing, with 60 s sparklines. |
| `/session/:id/report` | **The record** (opens when the interview ends): composure score, where Judge saw you break, three tips, the replay chart (heart rate over composure, shaded answer bands, coach-flag dots, Gemini's notes on hover; a caption explains that only those highlighted metrics are drawn), the question breakdown with the toughest question marked, and per-question stats with feedback. |
| `/session/:id/verdict` | **The ruling:** Judge's verdict and score. "Take the stand again" opens a gold light into a new session; "I'll come back later" dims the screen and adjourns. |
| `/dev` | **Dev Console:** every WebSocket event logged, for debugging the wiring. |

Names live in one place: `APP_NAME` ("Osiris") and `INTERVIEWER_NAME` ("Judge") in `src/lib/constants.ts`.
Design tokens are in `src/styles/tokens.css` (Tailwind `@theme`) and `src/styles/tokens.ts` (the same values for
charts; a test keeps them in sync). Stress colours always come with an icon and a word, so they read without colour.

## How the pieces connect

```
 Browser (Chrome app window, localhost:5173, Vite dev server)
 ┌──────────────────────────────────────────────────────────────┐
 │ pages / Dev Console                                          │
 │   ├─ src/api.ts ─────────── REST ──┐                         │
 │   ├─ src/lib/socket.ts ─── WS ─────┤ same origin             │
 │   ├─ src/lib/playClip.ts ─ audio ──┤                         │
 │   └─ src/lib/cameraStream.ts ──────┘ webcam, opened at load  │
 └───────────────────────────────────────┬──────────────────────┘
          VITE_MOCK=1: never leaves the  │  otherwise the Vite proxy forwards
          browser (src/mocks/ fakes the  │  /api /audio /ws /health to
          whole backend)                 ▼
                         FastAPI (localhost:8000)  backend/main.py
```

| File | Job |
|---|---|
| `src/types.ts` | Every wire type: PLAN.md §3, Data's replay shape, Brain's report, WebSocket events, the material upload. |
| `src/api.ts` | REST client: create, upload material, start, replay, report polling, insights, `/health`. Swaps to `src/mocks/mockApi.ts` in mock mode. |
| `src/lib/socket.ts` | WebSocket client: validates events, reconnects with backoff, queues outgoing events while offline. |
| `src/lib/playClip.ts` | Plays Judge's audio on a single channel and reports when it ends, which is when `answer_started` is sent. |
| `src/lib/cameraStream.ts` | The app's one webcam stream, opened when the app loads and kept for the life of the window. It tells the backend (`POST /api/sensor/page-camera`) so the Presage sensor opens the camera *after* the page. Opening it second used to change the camera's mode and crash Presage. Prefers the built-in camera over an iPhone. |
| `src/hooks/useLiveSession.ts` | The live protocol: socket, audio → `answer_started`, vitals buffer, interrupts, Presage status, navigation on `session_ended`. |
| `src/hooks/useSpeechToText.ts` | Chrome speech-to-text; the answer ends after 3.5 s of silence; typing fallback when unsupported. |
| `src/hooks/useInterrupt.ts`, `src/lib/interrupts.ts` | The interrupt rules: reset on a new question, never show the same text twice, and the user must acknowledge ("Yes, Your Honor") before answering on. |
| `src/components/` | Interviewer panel, interrupt box, vitals row, gauge, sparklines, replay chart, overlays, shared UI. |
| `src/mocks/` | An in-browser fake backend that follows Brain's event order (see "Mock mode"). |

## Running it

The one-command way is from the repo root: `.venv/bin/python scripts/run_demo.py` starts the backend, the Presage
sensor and this dev server, then opens Chrome. To run only the frontend:

```bash
cd frontend
npm install
VITE_MOCK=1 npm run dev        # no backend at all: open http://localhost:5173
npm run dev                    # against a backend on :8000 (the Vite proxy forwards to it)
```

In mock mode, add `?mock=auto` to the URL and the mock answers for you.

### Environment variables (`frontend/.env.local`, gitignored; template in `.env.example`)

| Var | Default | Meaning |
|---|---|---|
| `VITE_MOCK` | `0` | `1` = run against `src/mocks/`, no backend. |
| `VITE_API_URL` | empty | Empty = same origin through the proxy (recommended). Set e.g. `http://localhost:8000` to call the backend directly. |
| `VITE_PROXY_TARGET` | `http://localhost:8000` | Where the dev proxy forwards. The launcher sets it. |
| `VITE_CAMERA_PREVIEW` | `1` | `0` = the page never opens the webcam (`run_demo.py --no-preview`); Presage keeps it alone. |

The backend's settings and API keys live in the repo-root `.env` (see `/.env.example`). Restart `npm run dev`
after editing env files.

## Commands

```bash
npm run dev            # dev server on :5173
npm test               # unit + contract tests (Vitest): 63
npx tsc -b             # typecheck only
npm run lint           # oxlint
npm run build          # typecheck + production build into dist/
npm run gen:fixtures   # regenerate src/mocks/generated_{replay,report}.json
```

## Mock mode details

`src/mocks/engine.ts` follows `backend/brain/interviewer.py`:
- **Calibration:** `calibrating`, then 1-2 small-talk questions (a second one if under 35 s).
- **Each question:** `asking` → `question` → `listening` → answer → `thinking`. There are no filler clips between turns.
- **Follow-ups:** an ease-off follow-up when the answer's stress peak is above 70. It keeps its parent's `index`.
- **End:** the closing line (the only `filler`), then `done` and `session_ended`.

Vitals use the same stress formula as the backend (heart rate and breathing). Interrupts use the watcher's rule:
a 10 s mean above 70 is high, above 40 is rising, with a 12 s cooldown.

Fixtures in `src/mocks/`:
- `replay_sample.json`: Data's export of a real Tiger Data session (the contract sample).
- `generated_replay.json` + `generated_report.json`: a matched pair from `npm run gen:fixtures`, served for unknown ids in mock mode.

## Contract notes

- `audio_url` is relative (`/audio/<hash>.mp3`) or **null**. If it's null, show the text and send `answer_started` immediately (`playClip` does).
- `question.index` / `total` are planned numbers ("Q 2 of 5"). A follow-up keeps its parent's number.
- `question.coaching` (string or null) is feedback on the previous answer, spoken first in the same clip. It's shown as an interrupt.
- `coach_flag` carries `message` (citing the number) and `command` (shown as "Please …").
- `sensor` events carry Presage's status; hints appear only once a problem has lasted 2.5 s.
- `GET /report` returns 404 until it's ready. It's always ready by the time `session_ended` arrives.
- `POST /start` is idempotent (`started` / `already_running` / `finished`).
- On reconnect the server replays the latest `state` and unanswered `question`.
- Vitals can be `null` (face lost), and `stress` is `null` until the baseline exists.
- Stress thresholds are fixed team-wide: below 40 calm, 40 to 70 rising, above 70 high.
