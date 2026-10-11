#!/usr/bin/env bash
# Launch the patched Presage SmartSpectra app with the API key from .env (PRESAGE_API_KEY) pre-filled.
# Then press Start in the app (allow camera access the first time). It writes ~/smartspectra_session.txt,
# which sensing_client/client.py (started by scripts/run_demo.py) turns into vitals.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${SMARTSPECTRA_APP:-${SMARTSPECTRA_REPO:-$HOME/SmartSpectra}/cpp/samples/macos_swiftui_example/native_build/Debug/smartspectra_swift_ui.app}"
LOG_FILE="${SESSION_FILE:-$HOME/smartspectra_session.txt}"

[ -d "$APP" ] || { echo "[presage] app not built yet: run sensing_client/presage_macos/setup_presage.sh" >&2; exit 1; }
KEY="$(grep -E '^PRESAGE_API_KEY=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- || true)"
[ -n "$KEY" ] || echo "[presage] PRESAGE_API_KEY isn't set in .env: you'll have to type the key into the app" >&2

if pgrep -f "smartspectra_swift_ui.app/Contents/MacOS" >/dev/null; then
    echo "[presage] already running"
else
    open -n --env SMARTSPECTRA_API_KEY="$KEY" --env SMARTSPECTRA_LOG_FILE="$LOG_FILE" "$APP"
    echo "[presage] launched: press Start in the SmartSpectra window (key is pre-filled)"
fi
echo "[presage] metrics go to $LOG_FILE"
