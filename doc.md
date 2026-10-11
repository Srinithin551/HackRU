# StressTest: re-test report and improvement points

> **Status at submission (HackRU 2026).** This report was written on 2026-10-10, before the app became
> **Osiris**. It's kept as a record. Where each issue stands now:
>
> | Issue | Status |
> |---|---|
> | P1 breathing used at 0% confidence | ✅ Fixed: the Node sensor uses breathing only at Presage's "stable" confidence (≥ 45) |
> | P2 slow pulse cadence dropped most readings | ✅ Fixed: the pulse is held at its own confidence for 8 s, and a strong recent pulse beats a weak new one |
> | P3 a "push" follow-up can repeat the next planned question | ✅ Fixed (2026-10-11): a follow-up that is really a planned question is asked once, as that question (`turns.planned_repeat`) |
> | P4 an empty follow-up answer moves on without a redirect | Open |
> | P5 Gemini free-tier daily limit | Open: the fallback to `gemini-3.5-flash-lite` handles it |
> | P6 ~60 ms Tiger Cloud round trip | Unchanged (fine for a 3 s watcher) |
> | P7 database URL ownership and a Data test design flaw | Not re-checked |
> | P8 report breakdown truncated question text | ✅ Fixed: the report page was rebuilt, and the follow-up label no longer overlaps |
> | P9 frontend bundle over 500 kB | Open (a build warning only) |
> | P10 mock mode still played filler clips | ✅ Fixed: no filler between turns, like the real backend |
> | P11 Presage setup needed Xcode and a patched app | ✅ Fixed: the headless Node SDK needs only `npm install` |
> | P12 a backend restart drops the running interview | Partly: `/start` rebuilds a session's setup from Data, but a running interview still ends |
> | P13 rotate the API keys after the hackathon | Still to do |
> | P14 seat the demo person facing a light, face and chest in view | Still the right advice |

**Date:** 2026-10-10 · **Branch tested:** `brain-conversation` (merged into `main` with this doc) · **Tester:** Brain (Srinithin, with Claude)

Scope: everything this branch changes compared with `main` (71 files: Brain, Data's `queries.py`/`metrics.py`,
the backend hub and `/health`, the Live and Report pages, the coaching pop-up, the audio player, the camera hook,
the mocks, and the scripts). Code that didn't change (Sensing's client and route, Data's schema and routes)
was only tested as part of the end-to-end flow. **Presage now runs on this MacBook**: see section 3.

---

## 1. Results

| Area | Test | Result |
|---|---|---|
| Brain | `pytest tests` (decisions, coaching, prompts, reports, WebSocket loop, failure paths, one-voice check) | ✅ 119 passed |
| Data | `pytest backend/data/tests` against **Tiger Cloud** (incl. 5 real-database tests) | ✅ 28 passed, 0 skipped |
| Tiger Cloud | `scripts/init_db.py --columnstore` | ✅ all 8 checks PASS (hypertable, `vitals_5s` continuous aggregate + policy, columnstore + policy) |
| Tiger Cloud | Query latency from this laptop | ⚠️ ~60 ms per query (target 50 ms), see P6 |
| Frontend | `tsc -b`, `npm run lint`, `npm test`, `npm run build` | ✅ typecheck, lint (0 issues), 42 tests, build. ⚠️ bundle-size warning (P9) |
| Frontend | Browser: Setup → Live (coaching pop-up, docking, reopen, Escape) → Report labels | ✅ works at 1440×900 and 1280×720. ⚠️ breakdown list truncation (P8) |
| End to end | Real Gemini + ElevenLabs interview on Tiger Cloud, with sensing client + simulated stress spike | ✅ no problems flagged; 1.9 s average turn (2.4 s max); coaching, redirect, coach flags, report with real numbers. ⚠️ repeated question (P3) |
| End to end | `scripts/run_demo.py` (incl. new `--presage`), clean shutdown | ✅ |
| Presage | Real SmartSpectra SDK on this Mac → `~/smartspectra_session.txt` → sensing client | ✅ real pulse 63–98 bpm. ⚠️ breathing unusable, slow pulse cadence (P1, P2) |

---

## 2. Problems and improvement points

Severity: 🔴 fix before the demo · 🟠 should fix · 🟡 nice to have

### 🔴 P1. Sensing client uses breathing values even at 0% confidence (owner: Sensing / Sharvil)
- **Evidence (real Presage on this Mac):** 14 breathing readings, **9.8–48.6 breaths/min at 0–25% confidence**.
  Presage only considers breathing reliable from **45%** (its `stable` flag; the docs say the chest must be in view,
  and the app said "Place more of the chest in view"). `sensing_client/client.py` only checks the *pulse*
  confidence, so these junk values reach the stress score, where breathing is 30% of the weight.
- **Fix:** in `VitalsState.reading()`, ignore breathing samples whose confidence is below 45 (or hold the last good one).
  Also sit far enough back for the chest to be in frame.

### 🔴 P2. Presage's pulse arrives far less than once a second; the client's staleness penalty drops most readings (owner: Sensing)
- **Evidence:** in the real recording, 28 pulse values arrived against 101 metric updates, so the pulse updates every
  several seconds. The client multiplies confidence by 0.7 for every second it repeats a stale value: a 60% pulse
  becomes 42% after 1 s and 29% after 2 s (below the 0.4 cutoff), so most seconds are dropped. Fewer samples
  during calibration means the baseline can fall back to population defaults (Data needs ≥ 10 good samples).
- **Fix:** hold the last pulse at its own confidence for a few seconds (like `BR_HOLD_S` does for breathing), or decay much
  more slowly. Measure the real cadence first: add a timestamp to the end of each log line in the Presage patch
  (the client's patterns match from the start of the line, so a trailing timestamp is safe).

### 🟠 P3. A "push" follow-up can duplicate the next planned question (owner: Brain)
- **Evidence (live run):** the follow-up "how do you weigh the consistency guarantees of SQL against the throughput of
  NoSQL for a new high-traffic service?" was followed by the planned Q4 "What trade-offs do you consider when deciding
  between SQL and NoSQL databases for a new high-traffic service?"
- **Fix:** show Gemini the remaining planned questions in `decide_context` and tell it not to use their topics for
  follow-ups; or skip a planned question that's too similar to one already asked.

### 🟡 P4. An empty answer to a follow-up moved on instead of one "didn't catch that" (owner: Brain)
- **Evidence:** the live run's empty follow-up answer resolved to `next`. The rule (redirect once, then move on) is
  followed for main questions. Minor: tighten the prompt or force a redirect in `resolve_action` for an empty first answer.

### 🔴 P5. Gemini free tier runs out every day (owner: Srinithin)
- `gemini-3.6-flash` allows **20 requests/day** on the free tier and was exhausted again during testing. The Brain rests
  it for an hour and uses `gemini-3.5-flash-lite` (this works, and quality held up), but **enable billing on the Gemini
  key before the demo**.

### 🟡 P6. Tiger Cloud queries take ~60 ms from this laptop (owner: Data / Likhil)
- About one network round trip (~35 ms) plus query time, against Data's 50 ms target. Impact is small: the watcher runs every 3 s,
  and the two per-turn reads now run concurrently. Options: a service region closer to the venue, or keep the local
  TimescaleDB container as an offline backup (`docker start stresstest-tigerdb` and swap `TIGER_DATABASE_URL`).

### 🟠 P7. `TIGER_DATABASE_URL` ownership and a test-design flaw (owner: Data / Likhil)
- The cloud service is currently configured only in Srinithin's local `.env`. Likhil should own the service, share the URL
  **privately** (never in git), and rotate the password after the hackathon.
- `backend/data/tests/test_queries_contract.py::test_routes_through_brains_app` opens the app's DB pool in one event
  loop and then queries from a second `asyncio.run(...)` loop. It only breaks when forced onto a real DB (`DATA_FAKE=0`),
  but then it leaves the pool unusable and 4 later tests fail as a knock-on. Fix: skip it when not in-memory, or use
  `client.portal.call(...)`. Also consider making `db.get_pool()` reopen the pool when the event loop changes (scripts).

### 🟠 P8. Report page: question breakdown truncates question text to one character (owner: Frontend)
- At 1440×900 the "Question breakdown" column shows "T…", "B…", "G…". Give the text more width, wrap to two lines,
  or show the text only in the detail panel.

### 🟡 P9. Frontend bundle is over 500 kB (owner: Frontend)
- `npm run build` warns about a chunk over 500 kB (Recharts is the likely cause). Lazy-load the Report page or split the chart code.

### 🟡 P10. Mock mode still plays "Okay." / "Got it." fillers (owner: Frontend)
- The real backend no longer sends fillers. `src/mocks/engine.ts` and `scenario.ts` (plus `engine.test.ts`) still
  emit them, and the mock has no `coach` turns. The mock should mirror the real event flow.

### 🟠 P11. Presage setup friction (owner: Sensing / Sharvil to verify)
- Running Presage on a Mac needs full Xcode, an Apple ID signing certificate, `brew trust presage/smartspectra`,
  and Presage's SDK. Presage's macOS sample **doesn't build as shipped** with the current Homebrew protobuf (the SDK
  requires protobuf 6.31.1). `sensing_client/presage_macos/setup_presage.sh` works around it by linking the SDK's
  bundled protobuf. Sharvil: please run it on your machine and confirm.

### 🟡 P12. A backend restart mid-interview drops that interview (owner: Brain)
- Never use `--reload` for a demo (`run_demo.py` doesn't). If it happens anyway, the browser reconnects to a session the
  new process doesn't know. Suggested: on reconnect, end it cleanly with a report built from what Tiger Data saved.

### 🔴 P13. Secrets to rotate after the hackathon (owner: everyone)
- The Gemini, ElevenLabs and Presage keys were pasted into chat, and the Gemini key is still in git history
  (commit `d235a11`). Rotate all three after judging. `.env` is git-ignored and was never committed.

### 🟡 P14. Demo framing for Presage (owner: Sensing)
- Presage's status during the real run: "Hold still and record" (most), "Hold still – motion may affect accuracy" (30),
  "Place more of the chest in view" (22), "No face found" (3). Seat the demo person so head **and chest** are in frame,
  facing a light, and keep them still.

---

## 3. Presage on this MacBook: how it's used now

**Flow:** webcam → **Presage SmartSpectra macOS app** (Presage's C++ SDK 3.4.0) → `~/smartspectra_session.txt` →
`sensing_client/client.py` → `POST /api/vitals` → Tiger Cloud → Brain → dashboard.

- `sensing_client/presage_macos/stresstest_log.patch`: makes Presage's sample append its metric lines
  (`Pulse rate: 72.0 bpm (92%)`, `Breathing rate: …`, `EDA Proxy level: …`) plus its status (`Status: validation …`)
  to the log. The client parses exactly this format; it ignores the status lines.
- `setup_presage.sh`: clones Presage's repo if needed, applies the patch, builds and signs with your Xcode team.
- `start_presage.sh`: launches the app with `PRESAGE_API_KEY` from `.env` pre-filled.
- `scripts/run_demo.py --presage --no-preview`: does all of it, plus the backend, client and frontend.
- **Data's confidence cutoff is now 0.4** (`DATA_MIN_CONFIDENCE`), Presage's own accuracy standard for heart rate (±3 bpm).
  Real recording: confidence 30–77% (median 60%). The old 0.7 cutoff kept **2 of 28** pulse readings; 0.4 keeps **26 of 28**.

**Still to do (P1, P2):** fix breathing confidence and pulse staleness in the client, then run a full interview with
real Presage vitals end to end.

**Update (later on 2026-10-10):** Presage now runs as a hidden Node.js sensor (`sensing_client/presage_node`) instead
of the SmartSpectra app + log file + `client.py`, which fixes P1 and P2 at the source and removes most of P11's
setup. The old path stays as `run_demo.py --legacy-presage`.

---

## 4. Hand-offs
- **Sharvil (Presage):** run `sensing_client/presage_macos/setup_presage.sh` and `run_demo.py --presage` on your machine;
  confirm the log format and fix P1/P2; check whether your existing Presage app is the same sample.
- **Likhil (Tiger Data):** own the Tiger Cloud service and `TIGER_DATABASE_URL` (P7); share privately; look at P6.
- **Frontend:** P8, P9, P10, then the planned UI/UX polish.
- **Srinithin (Brain):** P3, P4, P5, P12.

## 5. Re-running these tests
```bash
.venv/bin/python -m pytest tests                 # Brain
.venv/bin/python -m pytest backend/data/tests    # Data (uses TIGER_DATABASE_URL from .env)
(cd frontend && npx tsc -b && npm run lint && npm test && npm run build)
.venv/bin/python scripts/run_demo.py --presage --no-preview    # full system with Presage
```
