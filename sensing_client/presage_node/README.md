# Presage sensor (hidden, no window)

Runs Presage's SmartSpectra SDK ([`@smartspectra/node-sdk`](https://www.npmjs.com/package/@smartspectra/node-sdk),
the same SDK version, 3.4.0, as the C++ one) in the background on the Mac's webcam, and POSTs one
`VitalsReading` per second to `/api/vitals` during an interview. Nothing opens on screen: Osiris in
Chrome is the only window.

```
webcam → sensor.mjs (Presage SDK, headless) → POST /api/vitals → Data → Tiger Data → WebSocket → live page
                                           → POST /api/sensor/status → `sensor` event → hints on the live page
```

It replaces the SmartSpectra macOS app, its log file and `sensing_client/client.py` (all still there as a
fallback: `scripts/run_demo.py --legacy-presage`).

## Running it
`scripts/run_demo.py` starts it for you (and installs the SDK the first time). By hand:

```bash
cd sensing_client/presage_node && npm install     # one time, ~400 MB (native runtimes for every platform)
node sensing_client/presage_node/sensor.mjs              # real webcam
node sensing_client/presage_node/sensor.mjs --simulate   # artificial vitals, no camera (testing)
node sensing_client/presage_node/sensor.mjs --dry-run    # no backend: start Presage now and print readings
```

Needs Node.js 20+, an Apple Silicon Mac (or Linux / Windows), and `PRESAGE_API_KEY` in the repo's `.env`.
The first time, macOS asks whether your terminal app may use the camera: allow it. If it was denied
before, turn it on in System Settings → Privacy & Security → Camera.

## How it behaves
- **Pre-warm, after the page.** Presage starts before any interview, so its 10-20 s lock-on is done by the
  first question. It waits until the page reports that it has opened the webcam
  (`GET /api/sensor/page-camera`, at most 25 s): when the page opened the camera second, macOS changed the
  camera's frame size under Presage and its engine aborted. `--no-prewarm` instead starts Presage only when an
  interview starts. Readings are only POSTed while an interview is active.
- **Camera:** the Mac's built-in camera is preferred over an iPhone (Continuity Camera) unless `PRESAGE_CAMERA`
  says otherwise.
- **Pulse:** Presage updates it every few seconds, so the last value is held at its own confidence for
  8 s (`PULSE_HOLD_S`), with a median over the last 5 s. A weak newest pulse never replaces a strong one
  (confidence ≥ 40, Presage's accuracy standard) from that window, so far fewer readings fall under Data's 0.4
  cutoff. After the hold, the last value coasts for up to 12 s at a fading confidence while the face is in view.
- **Breathing:** only values at Presage's own "stable" confidence (≥ 45) are used, held for 15 s (doc.md P1).
- **No face:** Presage's validation status says so directly; the reading is all nulls with confidence 0.
- **Status:** posted to `/api/sensor/status` on every change and every 3 s. The live page shows the
  hint ("Hold still.", "Too dark. Face a light.", camera or API key errors), and `/health` →
  `sensor.presage` has it too. A hint shows only once its problem has lasted 2.5 s and then stays at least
  3 s, so it doesn't flicker; errors show at once.
- **Failures:** camera or network errors are reported to the page and retried (3, 5, 10, 20 s). A key that
  is out of credits or rejected is swapped for the next one in `PRESAGE_BACKUP_API_KEYS`.
  The process never exits on its own. If Presage's native engine crashes anyway (it can abort on a camera
  change), `scripts/run_demo.py` restarts the sensor after a few seconds and the interview carries on.

## What the backend checks
`backend/sensing/routes.py` receives the readings. It accepts them only from this machine (the endpoints
have no login; set `ALLOW_REMOTE_SENSOR=1` in `.env` if the sensor must run elsewhere) and at most 120 per
POST (the sensor buffers 30 while the backend is down). It drops a reading stamped more than 5 s in the
future, since that would sit in every live window and drive coach flags. If the log says so, check the
laptop's clock. NaN or impossible heart / breathing rates become null, and confidence is clamped to 0-1.
A reading for a session the backend didn't create is dropped.

## Settings
| Setting | Where | Meaning |
|---|---|---|
| `PRESAGE_API_KEY` | `.env` | Presage API key, used first (required for real vitals) |
| `PRESAGE_BACKUP_API_KEYS` | `.env` (optional) | More keys, comma-separated. When Presage says a key is out of credits (or rejects it), a fresh SDK instance takes over with the next one, mid-interview too (a few seconds without a pulse). Read again at every switch, so keys added while running are used. With every key spent, the page says so and the sensor checks `.env` for a new key every 30 s. Logs show keys as "key 2 of 3 (…a1b2)", never in full |
| `PRESAGE_CAMERA` | `.env` (optional) | Part of a camera's name, e.g. `MacBook`, if the default camera is wrong (an iPhone via Continuity Camera, an external webcam) |
| `BACKEND_URL` | environment | Default `http://localhost:8000` (the launcher sets it) |

## Tests
```bash
cd sensing_client/presage_node && npm test    # reading logic + simulator, through the real protobuf decoding
```
`--simulate` follows a fixed script (no face, warm-up, calm baseline, then 60 s cycles with a stress spike,
"hold still" and a lost face) and mixes in weak pulses and junk breathing values, so the whole pipeline
can be checked end to end without a camera: `scripts/run_demo.py --fake --simulate-vitals`. The live page
shows "Simulated vitals" while it runs.
