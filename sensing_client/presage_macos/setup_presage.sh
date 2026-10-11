#!/usr/bin/env bash
# Build Presage's SmartSpectra macOS sample app with the StressTest log patch.
#
# The patched app appends every metrics update ("Pulse rate: 72.0 bpm (92%)", "Breathing rate: ...",
# "EDA Proxy level: ...") to ~/smartspectra_session.txt, which sensing_client/client.py follows.
#
# Needs (one time, see README.md here): Apple Silicon Mac on macOS 14+, full Xcode with an Apple ID
# signed in (Xcode → Settings → Accounts) and an "Apple Development" certificate, and Presage's SDK:
#     brew trust presage/smartspectra && brew install presage/smartspectra/smartspectra
#
# Usage:  sensing_client/presage_macos/setup_presage.sh            (team ID auto-detected from Xcode)
#         TEAM_ID=ABCDE12345 sensing_client/presage_macos/setup_presage.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="${SMARTSPECTRA_REPO:-$HOME/SmartSpectra}"
SAMPLE="$REPO_DIR/cpp/samples/macos_swiftui_example"
PREFIX="$(brew --prefix)"
LIB="$PREFIX/lib"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"

say() { printf '[presage] %s\n' "$*"; }
die() { printf '[presage] ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(uname -m)" = "arm64" ] || die "Presage's macOS SDK needs an Apple Silicon Mac"
[ -d "$DEVELOPER_DIR" ] || die "full Xcode not found at $DEVELOPER_DIR (install it from the App Store)"
[ -f "$LIB/libsmartspectra.dylib" ] || die "Presage SDK missing: brew trust presage/smartspectra && brew install presage/smartspectra/smartspectra"

TEAM_ID="${TEAM_ID:-$(defaults read com.apple.dt.Xcode IDEProvisioningTeamByIdentifier 2>/dev/null \
    | sed -n 's/.*teamID = \([A-Z0-9]\{10\}\);/\1/p' | head -1)}"
[ -n "$TEAM_ID" ] || die "no Xcode team found: sign in under Xcode → Settings → Accounts, or set TEAM_ID"
say "signing team: $TEAM_ID"

if [ ! -d "$REPO_DIR/.git" ]; then
    say "cloning Presage's SmartSpectra repo into $REPO_DIR"
    git clone --depth 1 --branch main https://github.com/Presage-Security/SmartSpectra.git "$REPO_DIR"
fi
if grep -q "AppendLinesToStressTestLog" "$SAMPLE/swift_ui/SmartSpectraRunner.mm"; then
    say "StressTest log patch already applied"
else
    git -C "$REPO_DIR" apply "$HERE/stresstest_log.patch" || die "patch didn't apply (Presage's sample changed?)"
    say "applied StressTest log patch"
fi

# Link the SDK's bundled protobuf + Abseil (exactly the versions it was built with), like Presage's own
# CMake config (SmartSpectraTargets.cmake). The sample's default links Homebrew's protobuf, whose version
# doesn't match the SDK's headers.
ABSL="$(ls "$LIB"/libSmartSpectra_Abseil_*.a | tr '\n' ' ')"
LDFLAGS="-framework AppKit -framework Accelerate -framework CoreGraphics $LIB/libsmartspectra.dylib \
-force_load $LIB/libSmartSpectra_Protobuf_protobuf.a -force_load $LIB/libSmartSpectra_Protobuf_utf8_validity.a $ABSL"

say "building (first build takes a minute)…"
xcodebuild -project "$SAMPLE/smartspectra_swift_ui.xcodeproj" -scheme smartspectra_swift_ui \
    -configuration Debug -derivedDataPath "$REPO_DIR/build" \
    DEVELOPMENT_TEAM="$TEAM_ID" PRODUCT_BUNDLE_IDENTIFIER="com.stresstest.smartspectra.$(whoami)" \
    OTHER_LDFLAGS="$LDFLAGS" -allowProvisioningUpdates -quiet build

APP="$SAMPLE/native_build/Debug/smartspectra_swift_ui.app"
[ -d "$APP" ] || die "build finished but the app wasn't found at $APP"
codesign --verify --strict "$APP" || die "the app isn't validly signed"
say "built: $APP"
say "start it with: sensing_client/presage_macos/start_presage.sh"
