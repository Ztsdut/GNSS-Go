#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${1:-0.1.1}"
STAGE="$ROOT/build/dmg-stage"
OUT="$ROOT/release/GNSS-Go-${VERSION}-macOS.dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE/CLI"
cp -R "$ROOT/dist/GNSS-Go.app" "$STAGE/GNSS Go.app"
cp "$ROOT/dist/gnssgo" "$STAGE/CLI/gnssgo"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/CLI/README.txt" <<'TXT'
GNSS Go CLI

To install the command-line program system-wide, copy gnssgo to a directory in PATH, for example:
  sudo cp gnssgo /usr/local/bin/gnssgo
  sudo chmod +x /usr/local/bin/gnssgo

Then run:
  gnssgo --help
TXT
mkdir -p "$ROOT/release"
hdiutil create -volname "GNSS Go" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
