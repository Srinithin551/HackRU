# Role: Sensing

**Owner:** _teammate_
**You own:** getting real vitals out of Presage and into the backend, reliably, once per second.
**Files:** `backend/sensing/routes.py`, `sensing_client/` (or a browser module handed to Frontend)
**Sponsor prize you carry:** Presage

Everything the product claims depends on your signal being real and stable. Read [../PLAN.md](../PLAN.md) first; your output is the `VitalsReading` contract.

---

## What you consume and what you provide

| You consume | From |
|---|---|
| Webcam frames | Presage SDK |
| Active session id | `GET /api/sessions/active` (Data), only if you run as a separate process |
| `data.insert_vitals()` | Data |
| `ws.broadcast()` | Brain |

| You provide | To |
|---|---|
| `POST /api/vitals` route | Your client |
| One `VitalsReading` per second during a session | Data (storage), Frontend (live chart via broadcast) |
| Real-world tuning numbers for the stress score | Data |
| Lighting and seating setup for the demo | Team |

---

## First 30 minutes: decide how Presage runs (blocking decision)

1. Get your Presage API key / dev credits from the MLH prizes page link.
2. Check which SDKs Presage offers and which metrics each returns (heart rate, breathing rate, focus/engagement, expressions). Check what runs on **the demo laptop** (macOS) and/or in **Chrome**.
3. Pick one and tell the team in chat:

| Option | When to choose | How it works |
|---|---|---|
| **A: browser** | Presage has a web/JS SDK | You write `frontend/src/sensing/presage.ts` that starts Presage on the page's webcam stream and POSTs readings. Frontend imports it. Frontend owns the webcam, you own this module |
| **B: local process** | Native/desktop or Python SDK on macOS | `sensing_client/` runs on the demo laptop, opens the webcam itself, polls `/api/sessions/active`, POSTs readings. The browser does **not** open the camera (macOS camera sharing is unreliable) |
| **C: escalate** | Neither works on our hardware | Walk to the Presage sponsor table immediately. Ask what they recommend for a laptop webcam demo. Everyone keeps building on `mock_vitals.py` meanwhile |

Do not spend more than 45 minutes stuck before choosing C. Sponsor engineers are at the event specifically to unblock this.

---

## The ingest route (`backend/sensing/routes.py`)

```python
@router.post("/api/vitals")
async def ingest(batch: VitalsBatch):
    rows = await data.insert_vitals(batch.readings)        # Data resolves session + computes stress
    for r in rows:
        await broadcast(r["session_id"], "vitals", {
            "ts": r["time"].isoformat(), "heart_rate": r["heart_rate"],
            "breathing_rate": r["breathing_rate"], "focus": r["focus"], "stress": r["stress"]})
    return {"stored": len(rows)}
```

Push this in hour 1 so `mock_vitals.py` has somewhere to post.

## The client (either option)

Responsibilities, in order of importance:

1. **Cadence.** Exactly one reading per second while a session is active. If Presage emits faster, keep the latest value per second. If slower, repeat the last value with lower confidence rather than leaving gaps.
2. **Confidence.** Map Presage's signal quality / face detection into `confidence` 0 to 1. No face = all metrics `null`, confidence 0. Data drops anything under 0.5 from the math.
3. **Smoothing.** Rolling median over 3 seconds for heart rate and breathing so single bad frames don't spike stress. Don't smooth more than that or real spikes get flattened.
4. **Normalization.** `focus` must be 0.0 to 1.0. `expression` must be one of `neutral, happy, surprised, frown, confused, unknown`. Map whatever Presage returns into these.
5. **Timestamps.** `ts` in UTC from the laptop clock. The backend runs on the same laptop, so no skew.
6. **Session binding (Option B).** Poll `/api/sessions/active` every 2 seconds. Only send while it returns a session. Option A gets the session id from the page.
7. **Resilience.** If a POST fails, buffer up to 30 readings and send them in the next batch. Never crash the client; log and keep going.

Expose a debug view (terminal printout for B, a tiny overlay for A) showing current HR, BR, focus, confidence. You'll need it at 3 AM.

---

## Real-world testing (this is what makes the demo believable)

Once you have readings, run these with a teammate and save the numbers in `docs/sensing_tests.md`:

| Test | Expect |
|---|---|
| Sit still, calm, 60 seconds | Stable HR, confidence high. This is a baseline |
| 20 jumping jacks, then sit | HR clearly up, then decays |
| Hard mental math out loud for 45 seconds (17 × 23, 312 / 8...) | Some HR and breathing rise, focus changes |
| Look away from the screen | Focus drops, maybe confidence |
| Leave the frame | Confidence 0, nulls |
| Bad lighting (window behind you) | See how bad it gets |

How long does Presage take to give a stable HR after the face appears? Tell Brain and Data. The calibration phase assumes 10 seconds of warm-up plus about 30 seconds of usable baseline.

Share results with Data so they can tune the constants in the stress formula (the jumping jacks result is roughly what "stress 100" should look like).

## Demo setup (you own this)

- Pick the exact demo seat early. Light source in front of the face, no window or bright screen behind.
- Laptop at eye level, face fills about a third of the frame, about 60 cm away.
- Run a full session in that exact spot before judging starts. Check confidence stays above 0.7.
- The demo person should avoid big head movements and hands near the face.

---

## Your checklist

**Sat 1 to 2 PM**
- [ ] Presage key, SDK chosen (A, B, or C), decision posted in chat
- [ ] `POST /api/vitals` route pushed

**2 to 6 PM**
- [ ] Client producing real HR from the webcam
- [ ] 1 Hz cadence, confidence, smoothing, normalization
- [ ] Posting into the backend; values visible in Tiger Data

**6 PM: Integration 1** (real Presage data driving the app)

**6 PM to 12 AM**
- [ ] Breathing rate, focus, expression wired (whatever Presage supports)
- [ ] Real-world tests done and shared with Data
- [ ] Buffering + resilience

**12 AM: Integration 2**

**After**
- [ ] Demo seat and lighting locked in
- [ ] Write the Presage section of the Devpost (which SDK, which signals, how stress is derived)

## Done means

During a live session, the heart rate line on the dashboard moves within a few seconds of a real reaction, never freezes, and drops to "no face" cleanly when the person leaves the frame.
