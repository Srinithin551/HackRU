# Role: Frontend

**Owner:** Shreyas Sahoo
**You own:** everything the judges see, plus the domain, deploy, and demo video.
**Files:** `frontend/*`
**Sponsor prizes you carry:** .Tech domain, DEV post (and you make every other sponsor visible on screen)

Read [../PLAN.md](../PLAN.md) first. You talk to the backend only through the REST endpoints and WebSocket events listed there.

---

## What you consume and what you provide

| You consume | From |
|---|---|
| `question`, `filler`, `state`, `vitals`, `coach_flag`, `session_ended` events | WebSocket `/ws/{session_id}` |
| `/audio/*.mp3` | Brain |
| `GET /api/sessions/{id}/replay` | Data |
| `GET /api/sessions/{id}/report` | Brain |
| `presage.ts` sensing module (only if Sensing picks Option A) | Sensing |

| You provide | To |
|---|---|
| `POST /api/sessions`, `POST /api/sessions/{id}/start` calls | Brain |
| `answer_started`, `answer_done` (with transcript), `end_session` | Brain via WebSocket |

---

## Stack

Vite + React + TypeScript + Tailwind + Recharts. `VITE_API_URL` env var (default `http://localhost:8000`). WebSocket URL derived from it.

## Hour 1

1. `npm create vite@latest frontend -- --template react-ts`, add Tailwind and Recharts, push.
2. `src/mocks/`: a fake WebSocket emitter that plays a scripted session (question → 20s of vitals with a spike → question...) and a `replay_sample.json`. Toggle with `VITE_MOCK=1`. You never wait on the backend.
3. `src/api.ts` with typed functions and `src/types.ts` mirroring the contracts in PLAN.md exactly.

From hour 2, switch to the real backend with `BRAIN_FAKE=1` and `mock_vitals.py` running.

---

## Pages

### 1. Setup (`/`)
- Name, interview type (`Software Engineering`, `Behavioral`, `Class Presentation`, `Quick Demo`), role title.
- "Start" → `POST /api/sessions` → navigate to `/session/:id`.
- One line on what's about to happen: "We'll measure your heart rate and focus through your webcam. Nothing is recorded."

### 2. Live session (`/session/:id`)

On mount: open the WebSocket, then `POST /api/sessions/{id}/start`.

Layout (one screen, no scrolling, works on the demo laptop):

```
┌───────────────────────────────┬─────────────────────────────┐
│  Interviewer card             │  Live vitals                │
│  - name + voice waveform      │  - big HR number (bpm)      │
│    animates while speaking    │  - stress gauge 0-100       │
│  - current question text      │    green <40 / amber / red >70│
│  - Q 2 of 5                   │  - 60s HR + stress sparkline│
│                               │  - focus bar                │
├───────────────────────────────┴─────────────────────────────┤
│  Your answer: live transcript ...          [ I'm done ▶ ]   │
│  Coach flag toast: "HR up 22% from baseline. Slow down."    │
└─────────────────────────────────────────────────────────────┘
```

Webcam preview: only if Sensing chose Option A (the page owns the camera). For Option B show a small "Sensor connected" indicator driven by whether `vitals` events are arriving.

State handling:

| Event | UI |
|---|---|
| `state: calibrating` | Overlay: "Getting your baseline. Just chat normally." with a 30s progress ring |
| `question` | Show text, play `audio_url`. On audio `ended` → send `answer_started`, start mic |
| `filler` | Play it, don't change the question text |
| `state: thinking` | Subtle "..." on the interviewer card |
| `vitals` | Push to a 60-point ring buffer, update number, gauge, sparkline |
| `coach_flag` | Toast for 4 seconds, amber or red |
| `session_ended` | Navigate to `/session/:id/report` |

### Speech-to-text

Browser Web Speech API (Chrome): `continuous = true`, `interimResults = true`. Show interim text live.

- Start only after the interviewer audio ends, so it doesn't transcribe the AI's own voice.
- Answer ends when the user clicks "I'm done" **or** after 3.5 seconds of silence following at least 3 seconds of speech.
- Then stop the mic and send `answer_done` with the final transcript.
- Chrome's recognizer stops on its own sometimes. Restart it in `onend` while still listening.

Brain may later switch this to ElevenLabs speech-to-text. Keep the STT code behind one `useSpeechToText()` hook so swapping is easy.

### 3. Report and replay (`/session/:id/report`)

Show a loading screen ("Analyzing your session...") until `/report` returns. Then:

- **Header:** composure score (big), summary, hardest moment.
- **Timeline chart (the money shot):** Recharts line chart from `/replay` `series`. Heart rate line and stress area. Each question is a shaded `ReferenceArea` from `answer_started_at` to `answered_at`, labeled Q1, Q2... A dashed `ReferenceLine` at baseline HR. Red dots where `coach_flag`s fired, if available.
- **Click a question region** (or a list below the chart) → side panel: question text, transcript, the stats (HR mean/peak, % above baseline, stress peak and when), and Gemini's `what_happened`, `body_signal`, `advice`.
- **Top 3 tips** at the bottom.
- Small footer: "Vitals by Presage · Stored in Tiger Data · Interviewer by Gemini + ElevenLabs". Judges look for this.

---

## Design

- Calm, clinical feel: dark background, one accent color, big readable numbers. It should look like a performance dashboard, not a toy.
- Stress colors: green < 40, amber 40 to 70, red > 70 (same thresholds as PLAN.md).
- Everything readable from 2 meters away on a projector. Minimum 16px body, 48px+ for the live HR number.
- Animate the HR number and gauge smoothly; it sells "live".

## Domain, deploy, submission (you own these)

- [ ] Register the .tech domain (stresstest.tech or the closest available) through the MLH .Tech link. Point it at a Vercel/Netlify deploy of the frontend (landing page at minimum).
- [ ] For judging, run everything locally on the demo laptop. Don't depend on a deployed backend.
- [ ] Record a 2-minute backup demo video by 6 AM Sunday.
- [ ] Own the Devpost page: screenshots, video, sponsor sections (collect write-ups from each teammate).
- [ ] DEV post after the hackathon (for the writing challenge).

---

## Your checklist

**Sat 1 to 2 PM**
- [ ] Vite app, Tailwind, Recharts, `types.ts`, mock WS + fixtures

**2 to 6 PM**
- [ ] Setup page
- [ ] Live page fully working against mocks, then against `BRAIN_FAKE=1`
- [ ] Audio playback + STT + `answer_started` / `answer_done`

**6 PM: Integration 1**

**6 PM to 12 AM**
- [ ] Coach flag toasts, calibration overlay, thinking state
- [ ] Report + replay page against Data's real `/replay`
- [ ] Question side panel

**12 AM: Integration 2**

**After**
- [ ] Visual polish, projector test
- [ ] Domain + deploy, backup video, Devpost

## Done means

A judge watches the HR number move live, sees the interviewer react, and the replay chart makes the stress spike on the hard question obvious in under five seconds of looking.
