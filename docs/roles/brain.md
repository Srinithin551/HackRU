# Role: Brain

**Owner:** Srinithin
**You own:** the interview loop, Gemini, ElevenLabs, the WebSocket hub, and the backend skeleton.
**Files:** `backend/main.py`, `config.py`, `contracts.py`, `ws.py`, `backend/brain/*`
**Sponsor prizes you carry:** Gemini, ElevenLabs

Read [../PLAN.md](../PLAN.md) first. Everything here plugs into those contracts.

---

## What you consume and what you provide

| You consume | From | How |
|---|---|---|
| Live stress, baseline, per-question stats | Data | `backend/data/queries.py` functions (stubbed hour 1, real later) |
| `answer_started`, `answer_done`, `end_session` | Frontend | WebSocket |
| Nothing directly from Sensing | | Sensing goes through Data |

| You provide | To | How |
|---|---|---|
| App skeleton, `contracts.py`, `ws.broadcast()` | Everyone | Hour 1 |
| `question`, `filler`, `state`, `coach_flag`, `session_ended` events | Frontend | WebSocket |
| `/audio/*.mp3` | Frontend | Static files |
| `GET /api/sessions/{id}/report` | Frontend | REST |
| `BRAIN_FAKE=1` mode | Frontend | Env flag |

---

## Hour 1 (do this before anything else, everyone is waiting on it)

1. `backend/` skeleton with FastAPI, CORS open for `localhost:5173`, `/audio` static mount, `/health`.
2. `contracts.py` copied exactly from PLAN.md.
3. `ws.py` hub:
   ```python
   connections: dict[str, set[WebSocket]] = defaultdict(set)
   inbox: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)   # client→server events per session

   async def broadcast(session_id: str, type_: str, data: dict): ...
   @router.websocket("/ws/{session_id}")   # accept, add to set, push every incoming message into inbox[session_id]
   ```
   Sensing's ingest route and your loop both call `broadcast()`.
4. `requirements.txt`: `fastapi uvicorn[standard] pydantic python-dotenv asyncpg google-genai elevenlabs`
5. `.env.example`, `.gitignore` (`.env`, `backend/static/audio/*.mp3`, `node_modules`).
6. Push to `main`, post in chat: "skeleton is up, pull now."

## Hours 1 to 2: fake mode

`BRAIN_FAKE=1` runs the whole loop with 4 hardcoded questions, a fixed "next question" decision, a canned report, and mp3s you generate once and commit to `backend/static/fake_audio/`. Frontend builds the entire live page against this. Ship it before touching Gemini.

---

## The interview loop (`brain/interviewer.py`)

```python
async def run_session(session_id: str):
    s = SessionState(session_id)
    await broadcast(session_id, "state", {"state": "calibrating"})

    # 1. Plan questions in the background while calibration runs
    plan_task = asyncio.create_task(gemini.plan_interview(s.interview_type, s.role_title))

    # 2. Calibration (baseline)
    qid = await data.insert_question(session_id, 0, "calibration", CALIBRATION_LINE)
    await ask(s, qid, CALIBRATION_LINE, kind="calibration")
    await wait_answer(s, qid)                    # handles answer_started / answer_done
    await data.compute_baseline(session_id)

    watcher = asyncio.create_task(watch_stress(s))   # coach flags from here on
    plan = await plan_task
    s.queue = list(plan)                          # PlannedQuestion objects

    # 3. Main loop
    next_text, kind = s.queue.pop(0).text, "main"
    while True:
        s.idx += 1
        qid = await data.insert_question(session_id, s.idx, kind, next_text)
        await ask(s, qid, next_text, kind)
        transcript = await wait_answer(s, qid)
        await play_filler(s)                      # instant, masks Gemini latency
        await broadcast(session_id, "state", {"state": "thinking"})

        stats = await data.get_question_stats(qid)
        window = await data.get_window_stats(session_id, 15)
        decision = await gemini.decide_next(s, transcript, stats, window)

        if decision.action == "wrap_up" or s.turns >= MAX_TURNS or s.elapsed() > MAX_SECONDS:
            break
        next_text, kind = resolve(decision, s)    # follow_up/ease_off/push → decision.spoken_text; next → queue

    # 4. Wrap up
    watcher.cancel()
    await ask_closing_line(s)
    report = await gemini.final_report(s, await data.get_session_history(session_id))
    await data.save_report(session_id, report)
    await data.end_session(session_id)
    await broadcast(session_id, "session_ended",
                    {"report_url": f"/api/sessions/{session_id}/report",
                     "replay_url": f"/api/sessions/{session_id}/replay"})
```

Limits: `MAX_TURNS = 7` (excluding calibration), `MAX_SECONDS = 480`. Keep the demo under 3 minutes by using an `interview_type="demo"` option with 3 main questions.

`wait_answer` reads from `ws.inbox[session_id]`: on `answer_started` call `data.mark_answer_started`, on `answer_done` call `data.mark_answered` and return the transcript. Ignore events for other question ids. Add a timeout (120s) so a dropped tab doesn't hang the loop.

## Stress watcher (`brain/watcher.py`)

This is the "Gemini has constant access to the data" piece. It runs every 3 seconds while the user is answering:

```python
async def watch_stress(s):
    last_flag = 0
    while True:
        await asyncio.sleep(3)
        if s.phase != "listening": continue
        w = await data.get_window_stats(s.session_id, 10)
        s.live_window = w                       # decide_next sees the latest
        if w.stress is None: continue
        level = "high" if w.stress > 70 else "rising" if w.stress > 40 else None
        if level and time.time() - last_flag > 12:
            await broadcast(s.session_id, "coach_flag",
                {"ts": now_iso(), "level": level, "message": flag_message(w)})
            s.flags.append((s.current_qid, level, w.stress))
            last_flag = time.time()
```

`flag_message` is a template, not Gemini (latency): e.g. `"Heart rate up 22% from your baseline. Slow down."` Coach flags are on screen only. Never interrupt the user with voice.

---

## Gemini (`brain/gemini.py`, `brain/prompts.py`)

Use the `google-genai` SDK with JSON output (`response_mime_type="application/json"` plus a `response_schema` from Pydantic). Use a Flash model for everything. Log every prompt and response to `backend/logs/` so you can debug at 3 AM.

### Call 1: `plan_interview` (during calibration)

```python
class PlannedQuestion(BaseModel):
    text: str          # spoken aloud, max ~30 words
    topic: str
    difficulty: int    # 1 to 5
```

Prompt gist: "You are a {role_title} interviewer. Write {n} {interview_type} questions in increasing difficulty (2, 3, 3, 4, 5). Each must be speakable in under 12 seconds. No lists, no code blocks, no markdown." Temperature 0.8.

### Call 2: `decide_next` (after every answer)

```python
class Decision(BaseModel):
    action: Literal["follow_up", "ease_off", "push", "next", "wrap_up"]
    spoken_text: str     # what the interviewer says next (empty if action == "next")
    answer_quality: int  # 1 to 5, Gemini's read of the transcript
    rationale: str       # for logs and the report, never spoken
```

Context you send (keep it compact, under ~1,500 tokens):

```
ROLE: {role_title}   TYPE: {interview_type}   TURN: {turn}/{max}   ELAPSED: {s}s
BASELINE: HR {72} bpm, BR {14}/min
QUESTION ({kind}, difficulty {d}): "{text}"
ANSWER TRANSCRIPT: "{transcript, truncated to 600 chars}"
BODY DURING ANSWER: HR mean {84} (+17%), peak {91}, stress mean {46}, peak {78} at {12}s in, focus {0.62}
RIGHT NOW (last 10s): stress {55}, expression {frown}
REMAINING PLANNED QUESTIONS: {count}
PREVIOUS DECISIONS: {last 3 actions}
```

Rules in the system prompt:

- `ease_off` if stress_peak > 70 **and** answer_quality <= 2: rephrase more simply or give a small hint, warm tone.
- `push` if stress_mean < 40 **and** answer_quality >= 4: a harder follow-up on the same topic.
- `follow_up` if the answer was vague but the person seems okay: ask for a specific example.
- `next` otherwise. Max one follow-up per planned question.
- `wrap_up` when no planned questions remain.
- `spoken_text` sounds like a real human interviewer: short, natural, no praise inflation, under 30 words.

Temperature 0.4. **Target: Gemini returns in under 2 seconds.** If it takes longer than 6s, fall back to `next` with the queued question.

### Call 3: `final_report`

```python
class QuestionFeedback(BaseModel):
    question_id: str
    what_happened: str   # 1-2 sentences about the answer content
    body_signal: str     # MUST cite numbers, e.g. "HR jumped 22% above baseline 8s in"
    advice: str          # one concrete thing to do next time

class Report(BaseModel):
    composure_score: int          # 0 to 100, higher is calmer
    summary: str                  # 2-3 sentences
    hardest_moment: str           # the single biggest spike, with numbers
    per_question: list[QuestionFeedback]
    top_tips: list[str]           # exactly 3
```

Input: full `get_session_history()` plus the coach flags. Temperature 0.5. This is the slowest call, so emit `state: thinking` and let the frontend show a loading screen. Serve it from `GET /api/sessions/{id}/report` (returns `data.get_report()`).

---

## ElevenLabs (`brain/voice.py`)

```python
async def tts(text: str) -> str:
    key = sha1(f"{VOICE_ID}:{text}").hexdigest()[:16]
    path = AUDIO_DIR / f"{key}.mp3"
    if not path.exists():
        audio = client.text_to_speech.convert(voice_id=VOICE_ID, model_id=MODEL, text=text,
                                              output_format="mp3_44100_128")
        path.write_bytes(b"".join(audio))
    return f"/audio/{key}.mp3"
```

- Pick one calm, professional voice and keep it for the whole project. Pick it in the first 2 hours with the team.
- Use a low-latency model (check ElevenLabs docs for the current fast one).
- At startup, pre-generate and cache: the calibration line, the closing line, and 4 to 5 fillers ("Okay.", "Got it.", "Mm, alright.", "Take your time, let's keep going."). Rotate fillers so they don't repeat back to back.
- Run TTS in a thread (`asyncio.to_thread`) so it doesn't block the event loop.
- Stretch: generate the next planned question's audio while the user is still answering, so "next" decisions play instantly.
- Stretch for the ElevenLabs prize: use ElevenLabs speech-to-text for the user's answer instead of the browser's, so the app uses ElevenLabs both ways. Coordinate with Frontend first.

`ask(s, qid, text, kind)` = `tts(text)` → `broadcast("question", {...})` → set `s.phase = "listening"` → broadcast `state: listening`.

---

## As built: integration reference

Everything above is implemented on the `brain` branch. This section is what the other roles need to plug in; where the build differs from the plan above, it says so.

### Run it

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env                                  # fill in keys; BRAIN_FAKE=1 needs none
BRAIN_FAKE=1 uvicorn backend.main:app --reload        # from the repo root
python scripts/fake_frontend.py                       # terminal stand-in for the browser
pytest                                                # 99 tests, < 2 seconds
```

`GET /health` reports fake mode, whether Gemini/ElevenLabs keys are set, and which routers are mounted.

### Files

| File | What it does |
|---|---|
| `backend/main.py` | App, CORS (`CORS_ORIGINS` + any localhost port), `/health`, `/audio/{file}.mp3`, mounts routers |
| `backend/config.py` | All env vars (`.env` at repo root or `backend/.env`) |
| `backend/contracts.py` | PLAN.md section 3, verbatim |
| `backend/ws.py` | `broadcast()`, `inbox`, `/ws/{session_id}` |
| `backend/brain/routes.py` | `POST /api/sessions`, `POST /api/sessions/{id}/start`, `GET /api/sessions/{id}/report`, debug `GET /api/sessions/{id}/status` |
| `backend/brain/interviewer.py` | The loop: calibration, questions, filler, decide, resolve, wrap-up, crash recovery |
| `backend/brain/watcher.py` | Coach flags every 3s while listening |
| `backend/brain/gemini.py`, `prompts.py` | The three Gemini calls, each with a timeout and a fallback, logged to `backend/logs/` |
| `backend/brain/voice.py` | ElevenLabs TTS, sha1 disk cache, request dedupe, clip length from file size |
| `backend/brain/report.py` | Validates Gemini's report, and builds a numbers-only report when Gemini is unavailable |
| `backend/brain/fake.py` | `BRAIN_FAKE=1` questions, rule-based decisions, list of every fake-mode line |
| `backend/brain/specs.py`, `lines.py` | Interview types (counts, difficulty curves, limits, fallback questions) and fixed spoken lines |
| `scripts/make_fake_audio.py` | Renders fake-mode clips into `backend/static/fake_audio/` (ElevenLabs if keyed, else macOS `say`) |
| `scripts/fake_frontend.py` | Runs a whole session over REST + WebSocket from the terminal (`--type` to type answers, `--end-after N`) |

### For Frontend

- **`audio_url` is a relative path** (`/audio/<hash>.mp3`), resolved with `apiUrl()`. The Vite proxy then serves it same-origin, which the waveform's Web Audio analysis needs. It is **`null`** if TTS failed. In that case show the text and send `answer_started` right away. `QuestionEvent.audio_url` / `FillerEvent.audio_url` in `frontend/src/types.ts` should be `string | null`. (Setting `PUBLIC_BASE_URL` makes them absolute again, if ever needed.)
- **`interview_type`**: the frontend slugs `software_engineering`, `behavioral`, `class_presentation`, `demo` are all recognized (human labels work too).
- **Coach flags for the replay chart** are already in the report (`report.coach_flags`, each with its `question_id`), so the proposed `ReplayResponse.coach_flags` isn't needed.
- **`question.index` / `total`** = the planned question number and the planned count ("Q 2 of 5"). Calibration is `index: 0`. A follow-up keeps its parent's number, so check `kind`. The replay chart's Q1, Q2... come from Data's sequential `idx`, which counts follow-ups too.
- **State sequence:**
  - `calibrating` covers the whole calibration (one or two small-talk prompts, no `asking`/`listening` during it);
  - after the last small-talk answer: `thinking` while Gemini writes the bridge into the first question;
  - each question then goes `asking` → `listening` → (answer) → `thinking`. **No filler clips** ("Okay.", "Got it."): they sounded scripted and overlapped the next question;
  - at the end: `thinking` → one `filler` clip (reaction or coaching on the last answer, plus the closing line) → `done` → `session_ended`. **`session_ended` is only sent after that clip has finished playing** (its length comes from the file size), so navigating to the report never cuts it off.
- **One voice at a time.** The server sends at most one clip per turn, and the next one only after `answer_done`. `frontend/src/lib/playClip.ts` is also a single channel: starting any clip stops the previous one (covered by `playClip.test.ts`). Verified in a real browser: 7 clips in a session, never more than 1 playing.
- **Question `text` is the question** with at most a short lead-in, e.g. "That reconciliation work sounds tricky. Let's switch gears: how would you…". It never contains coaching.
- **`question.coaching`** (string or null): feedback on the previous answer, said outside the interview frame. It is spoken first, in the same clip as `text`. The Live page shows it as a "Coaching on your last answer" note under the question.
- **Reconnects:** on connect the hub replays the latest `state`, `question` and `session_ended`. A reload mid-session lands on the right screen.
- **`POST /start`** is idempotent and returns `status`: `started`, `already_running` or `finished`.
- **`end_session`** works at any time. There's no closing line in that case.
- **Timeouts:** an unanswered question times out after 120s, counted from `answer_started`. If no tab is connected at that point, the session ends.
- **`GET /report`** returns `404 {"detail": "report not ready"}` until it's saved. It's always saved before `session_ended` is sent. On top of the shape above, it has these additive fields:
  - `coach_flags: [{ts, question_id, level, stress, message}]`, for red dots on the replay chart;
  - `adaptations: [{question_id, action, answer_quality, rationale}]` (the decision made after that question's answer; `action` can be `coach`, `redirect`, `follow_up`, `ease_off`, `push`, `next`, `wrap_up`);
  - `generated_by: "gemini" | "fallback" | "fake"`;
  - `composure_measured: bool`. When false, no usable vitals were captured, so `composure_score` is a neutral 50 and shouldn't be read as a verdict (the Report page shows "Not measured");
  - `adaptations[].coaching`: the coaching that was spoken, for `coach` actions.

### For Data

- Brain only touches `backend/data/queries.py`. **An in-memory stub is there now** with the exact signatures. It computes real stats from inserted vitals, so mock vitals produce real stress numbers. Replace the bodies and keep the signatures. Brain needs no change.
- Return ids as `str`, not asyncpg `UUID` objects: `create_session`, `insert_question` and `QuestionStats.question_id`. Brain coerces at its boundary, but Pydantic won't turn a UUID into a `str` field.
- `get_session_history` items: please include `question_id` (the stub does). `stats` may be a `QuestionStats` or a dict.
- `save_report` gets a JSON-ready dict, so `json.dumps` it for JSONB. `get_report` may return a dict or a JSON string.
- **DB pool hook:** if `backend/data/db.py` defines `async def startup()` / `async def shutdown()`, `main.py` calls them in the app lifespan.
- **Routes:** `backend/data/routes.py` must expose `router = APIRouter()`. `main.py` mounts it automatically when the file exists. The same goes for `backend/sensing/routes.py`.
- **Calibration timing:** Brain keeps calibration going until at least 35s after `/start` (it asks a second small-talk prompt if the first answer was short), then calls `compute_baseline`. Calibration rows are `kind="calibration"`, `idx=0`, and there can be two of them.
- **Question text** stored via `insert_question` is the question as asked (with any short lead-in), never the coaching, so replay and report lists show real questions.
- **`mock_vitals.py --auto`** can poll `GET /api/sessions/{id}/status` for `phase` and `question.{index,kind}` to know when an answer is in progress.

### For Sensing

- `from backend.ws import broadcast` and `from backend.data import queries as data`. `broadcast()` never raises (dead sockets are dropped) and accepts a `str` or `UUID` session id.

### Tunables

| Constant | Default | Where |
|---|---|---|
| `CALIBRATION_MIN_S` | 35 | `interviewer.py` |
| `ANSWER_TIMEOUT_S` | 120 | `interviewer.py` |
| `CLOSING_PAUSE_S` | 4 | `interviewer.py` (lets the closing line finish before navigation) |
| `DECIDE_TIMEOUT_S` / `PLAN_TIMEOUT_S` / `REPORT_TIMEOUT_S` | 6 / 20 / 45 | `gemini.py` |
| `INTERVAL_S` / `WINDOW_S` / `COOLDOWN_S` / `MIN_SAMPLES` | 3 / 10 / 12 / 3 | `watcher.py` |
| Questions, `max_turns`, `max_seconds` per type | SWE/Behavioral/Presentation: 5 (2,3,3,4,5), 7, 480s. Demo: 3 (2,4,5), 5, 240s | `specs.py` |
| `GEMINI_MODEL` (plan + report), `GEMINI_FAST_MODEL` (decide_next), `GEMINI_THINKING_LEVEL` | `gemini-3.6-flash`, `gemini-3.5-flash-lite`, `minimal` | `.env` |
| `ELEVENLABS_MODEL`, `ELEVENLABS_VOICE_ID` | `eleven_flash_v2_5`, Eric (`cjVigY5qzO86Huf0OWal`) | `.env` |

### Where the build differs from the plan above

- **Two Gemini models with failover.** Measured on 2026-10-10: `gemini-3.8-flash` took 2–6s per decision and returned 503 "high demand" errors, while `3.6-flash` (about 1.3s) and `3.5-flash-lite` (about 0.7s) made the same decisions. Free-tier limits are 5 requests per minute *per model*, so splitting the calls doubles the headroom. If one model returns 503/429 or errors, the other gets the remaining time budget.
- **No filler clips**, unlike the plan above (see the state sequence).
- **Fake-mode decisions are rule-based**, not a fixed `next`. They follow the same thresholds as the Gemini prompt, so pressing `s` in `mock_vitals.py` makes the fake interviewer ease off.
- **Fake clips are placeholder macOS voices.** Once the ElevenLabs voice is picked, run `python scripts/make_fake_audio.py --force` and commit the result.
- **Brain enforces Gemini's rules in code** (`resolve_action`): at most one follow-up and one redirect per planned question, no `wrap_up` while questions remain, and an empty or runaway (70+ word) `spoken_text` becomes `next`. If the rules change Gemini's action, the planned question is asked as written. Budget checks skip the Gemini call entirely.
- **Crash safety:** if the loop throws, the session still ends with a numbers-only report and `session_ended`.
- **A conversation, not a list.** Gemini writes every turn after the small talk:
  - **`open_interview`** (fast model, 5s budget) bridges from the small-talk answers into the first planned question.
  - **`decide_next`** returns the whole next turn: a short reaction to the answer, then the next question (the planned one, reworded to flow) or a follow-up.
  - The planned questions remain the backbone, so the difficulty curve holds. Any failure falls back to the planned text.
  - Prefetching was removed, since the next question's wording isn't known in advance. Measured live: 1.2–1.7s from `answer_done` to the next `question`, voice included.
- **`coach` action: immediate feedback on wrong answers.** When an answer is wrong, off-topic (even if technically sound), rude, dismissive or very weak, the interviewer steps outside the interview. In one clip it:
  - says it's stepping out;
  - says what would realistically happen with that answer in a real interview;
  - gives what they should have said, with real substance (e.g. "add the new column, backfill in batches, switch reads, drop the old one");
  - steps back in and asks the next planned question.

  On the last question it ends after the model answer. Code trims any trailing question or "back in the interview" sentence, which Gemini sometimes adds anyway.
- **`redirect`** now only means "I didn't catch that" (empty or garbled speech-to-text), once per planned question.
- **Voice style.** Prompts ban stock acknowledgments ("got it", "I see", "I understand", "makes sense", "great answer", "thanks for sharing"…) and ask for specific, substantive reactions with contractions. A code backstop (`gemini.strip_stock_openers`) removes stock openers that still slip through.
- **Fake mode** approximates `coach` with a word-overlap check and a fixed coaching line, pre-recorded joined to each next question so it's still one clip.
- **Adaptive small talk.** The opener is pre-recorded so the session starts instantly ("Thanks for joining me today. To start, tell me a little about yourself.").
  - The second small-talk turn, asked only when the baseline needs more time, is written live by Gemini (`small_talk_follow_up`, fast model, 4s budget). It reacts to what they said: it plays along with something random, calmly notes something rude, or says it didn't catch an empty answer. Then it asks an easy background question tied to what they're interviewing for: why they chose their major or field, or what drew them to a presentation topic.
  - The fixed per-type line is the fallback. Measured: about 0.6–1.1s.
- **ElevenLabs speech-to-text is not done** (it needs Frontend's agreement first).

---

## Your checklist

**Sat 1 to 2 PM**
- [ ] Skeleton, `contracts.py`, `ws.py`, requirements, env example pushed
- [ ] Gemini key and ElevenLabs key working with a hello-world script

**2 to 6 PM**
- [ ] `BRAIN_FAKE=1` mode end to end with Frontend
- [ ] `voice.py` with caching + pre-generated fillers
- [ ] `plan_interview` returning good questions for SWE, behavioral, demo types
- [ ] Interview loop running against Data's stubs and `mock_vitals.py`

**6 PM: Integration 1** with real Presage + real Tiger Data

**6 PM to 12 AM**
- [ ] `decide_next` with all five actions working (test each by pressing `s` in mock_vitals to fake a spike)
- [ ] Watcher + coach flags
- [ ] `final_report` with numbers in every `body_signal`
- [ ] Latency pass: answer_done → next question audio playing in under 4 seconds

**12 AM: Integration 2** full session

**After**
- [ ] Prompt polish on real transcripts
- [ ] Prefetch next question audio
- [ ] Write the Gemini + ElevenLabs sections of the Devpost

## Done means

A judge does a 3-question demo session. The interviewer speaks naturally, at least one answer triggers a visible adaptation (ease_off or push) tied to a real stress spike, and the report quotes the actual heart rate numbers from that spike.
