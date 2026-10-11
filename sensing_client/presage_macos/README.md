# Presage on macOS (real webcam vitals)

Presage's SDK runs inside its **SmartSpectra macOS sample app**. `stresstest_log.patch` makes that app
append every metrics update to `~/smartspectra_session.txt`, in the format `sensing_client/client.py`
reads:

```
Pulse rate: 72.0 bpm (92%)        ← confidence is 0-100; the client sends it as 0-1
Breathing rate: 14.2 bpm (88%)
EDA Proxy level: 0.120
Status: validation …              ← Presage's own status, for debugging; the client ignores these lines
```

## One-time setup
1. Apple Silicon Mac, macOS 14+.
2. Full **Xcode**: sign in under **Xcode → Settings → Accounts**, then **Manage Certificates → + → Apple Development**.
3. Presage's SDK: `brew trust presage/smartspectra && brew install presage/smartspectra/smartspectra`
4. `PRESAGE_API_KEY=…` in the repo's `.env`.
5. Build the patched app: `sensing_client/presage_macos/setup_presage.sh`
   (clones Presage's repo to `~/SmartSpectra` if needed, applies the patch, builds and signs with your Xcode team).

## Every time
```bash
.venv/bin/python scripts/run_demo.py --presage --no-preview
```
Press **Start** in the SmartSpectra window (the key is pre-filled from `.env`; allow camera access the
first time). Sit centered, facing a light. The launcher prints `Presage vitals: LIVE (heart rate coming
through)` once readings flow. `--no-preview` leaves the webcam to SmartSpectra.

## Notes
- The build links the SDK's **bundled** protobuf and Abseil (as Presage's CMake config does). The sample's
  default links Homebrew's protobuf, whose version doesn't match the SDK's headers.
- Presage marks heart rate accurate (±3 bpm) from confidence 40, so Data's cutoff defaults to 0.4
  (`DATA_MIN_CONFIDENCE`).
- `start_presage.sh` alone launches just the app.
