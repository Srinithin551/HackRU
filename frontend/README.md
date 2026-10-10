# StressTest frontend

Vite + React 19 + TypeScript + Tailwind CSS v4 + Recharts 3 + React Router 8.
Owner: Shreyas. Spec: [`docs/roles/frontend.md`](../docs/roles/frontend.md). Contracts: [`docs/PLAN.md`](../docs/PLAN.md) §3.

> Status: scaffold. The data layer (types, API client, WebSocket client, mock backend) exists.
> The design tokens and the three pages (Setup, Live session, Report) are not built yet.

## How the frontend talks to the backend

```
 pages (Setup / Live / Report)
   │  call                          │  subscribe
   ▼                                ▼
 src/api.ts  ── REST ──┐       src/lib/socket.ts ── WebSocket ──┐
   │                   │            │                           │
   │ VITE_MOCK=1       ▼            │ VITE_MOCK=1               ▼
   └─► src/mocks/mockApi.ts   FastAPI /api/*      └─► src/mocks/mockSocket.ts   FastAPI /ws/{id}
```

Pages never know whether they run against the real backend or the mock. Each module exposes one
interface (`Api`, `SessionSocket`) with two implementations, picked once by `IS_MOCK`.

| File | Job |
|---|---|
| `src/types.ts` | Every wire type. Mirrors PLAN.md §3, the replay shape in data.md and the report shape in brain.md. |
| `src/lib/config.ts` | Reads `VITE_*` env vars. Builds REST and WebSocket URLs. |
| `src/api.ts` | Typed REST client: create/start session, replay, report (with polling). |
| `src/lib/socket.ts` | WebSocket client with auto-reconnect and an outbox, so `answer_done` is never lost. |
| `src/mocks/` | In-browser fake backend (scripted interview, fake vitals with a stress spike, sample replay and report). |
| `vite.config.ts` | Tailwind plugin, dev proxy to the backend, Vitest settings. |

## Run it

```bash
cd frontend
npm install
cp .env.example .env.local     # then set VITE_MOCK=1 to work with no backend
npm run dev                    # http://localhost:5173
npm run build                  # typecheck + production build
npx vitest run                 # unit tests
```

- **Mock mode** (`VITE_MOCK=1`): no backend. Add `?mock=auto` to a session URL and the mock answers for you.
- **Real backend**: run `uvicorn backend.main:app` on port 8000. The dev server proxies `/api`, `/audio`
  and `/ws` to it (override with `VITE_PROXY_TARGET`), so there is no CORS to configure.

## Things that are easy to get wrong (from PLAN.md / brain.md)

- **Calibration** is announced as `state: listening` right after a question with `kind: "calibration"`,
  not only as `state: calibrating`. Key the overlay off the question kind.
- **Do not auto-stop the calibration answer on silence.** A short answer would leave Data with too few
  samples and it silently falls back to population-average baselines.
- **`question.index` can exceed `total`** because follow-ups also increment it.
- **`POST /start` must be sent once.** React StrictMode runs effects twice in dev; `startSession()` dedupes.
- **Vitals can be `null`** (face lost) and `stress` is `null` until the baseline exists.
- **Stress thresholds are fixed team-wide:** below 40 calm, 40 to 70 rising, above 70 high.
