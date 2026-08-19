#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${1:-0.1.1}"
APPDIR="$ROOT/build/GNSS-Go.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/512x512/apps"
cp "$ROOT/dist/GNSS-Go" "$APPDIR/usr/bin/GNSS-Go"
cp "$ROOT/packaging/linux/AppRun" "$APPDIR/AppRun"
cp "$ROOT/packaging/linux/gnss-go.desktop" "$APPDIR/gnss-go.desktop"
cp "$ROOT/packaging/linux/gnss-go.desktop" "$APPDIR/usr/share/applications/gnss-go.desktop"
cp "$ROOT/src/gnssgo/gui/resources/icons/gnss_go.png" "$APPDIR/gnss-go.png"
cp "$ROOT/src/gnssgo/gui/resources/icons/gnss_go.png" "$APPDIR/usr/share/icons/hicolor/512x512/apps/gnss-go.png"
chmod +x "$APPDIR/AppRun" "$APPDIR/usr/bin/GNSS-Go"
APPIMAGE_TOOL="${APPIMAGE_TOOL:-$ROOT/build/appimagetool-x86_64.AppImage}"
if [[ ! -x "$APPIMAGE_TOOL" ]]; then
  echo "appimagetool not found: $APPIMAGE_TOOL" >&2
  exit 2
fi
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$APPIMAGE_TOOL" "$APPDIR" "$ROOT/release/GNSS-Go-${VERSION}-Linux-x86_64.AppImage"
