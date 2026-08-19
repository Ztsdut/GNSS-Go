from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ICON_DIR = ROOT / "src" / "gnssgo" / "gui" / "resources" / "icons"


def run(cmd: list[str]) -> None:
    print("+", " ".join(str(x) for x in cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def mac_icon() -> Path | None:
    if sys.platform != "darwin":
        return None
    source = ICON_DIR / "gnss_go.png"
    iconset = BUILD / "GNSS-Go.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 128, 256, 512]
    for size in sizes:
        run(["sips", "-z", str(size), str(size), str(source), "--out", str(iconset / f"icon_{size}x{size}.png")])
        run(["sips", "-z", str(size * 2), str(size * 2), str(source), "--out", str(iconset / f"icon_{size}x{size}@2x.png")])
    output = BUILD / "gnss_go.icns"
    run(["iconutil", "-c", "icns", str(iconset), "-o", str(output)])
    return output


def common_args() -> list[str]:
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--paths",
        str(ROOT / "src"),
        "--collect-all",
        "gnssgo",
        "--hidden-import",
        "PySide6.QtWebEngineWidgets",
        "--hidden-import",
        "PySide6.QtWebChannel",
    ]



def refresh_station_snapshot() -> None:
    """Refresh the bundled station-position snapshot before freezing the app.

    The updater is best-effort: it always preserves the existing packaged
    snapshot and exits successfully when one or more live metadata endpoints are
    unreachable.  This lets offline/local builds continue while online release
    builds embed a fuller current station map.
    """
    updater = ROOT / "tools" / "update_bundled_station_snapshot.py"
    if updater.exists():
        run([sys.executable, str(updater), "--online", "--timeout", "25"])


def build_gui() -> None:
    icon: Path | None
    if sys.platform == "win32":
        icon = ICON_DIR / "gnss_go.ico"
    elif sys.platform == "darwin":
        icon = mac_icon()
    else:
        icon = ICON_DIR / "gnss_go.png"

    cmd = common_args() + [
        "--onefile",
        "--windowed",
        "--name",
        "GNSS-Go",
    ]
    if icon and icon.exists():
        cmd += ["--icon", str(icon)]
    cmd.append(str(ROOT / "packaging" / "entrypoints" / "gui.py"))
    run(cmd)


def build_cli() -> None:
    cmd = common_args() + [
        "--onefile",
        "--console",
        "--name",
        "gnssgo",
        str(ROOT / "packaging" / "entrypoints" / "cli.py"),
    ]
    run(cmd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build GNSS Go standalone executables with PyInstaller.")
    parser.add_argument("--gui", action="store_true", help="Build the desktop GUI executable")
    parser.add_argument("--cli", action="store_true", help="Build the CLI executable")
    parser.add_argument("--clean", action="store_true", help="Remove existing build/dist first")
    args = parser.parse_args()

    if args.clean:
        shutil.rmtree(DIST, ignore_errors=True)
        shutil.rmtree(BUILD, ignore_errors=True)
    DIST.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    if not args.gui and not args.cli:
        args.gui = args.cli = True

    # Make the first-launch map useful before PyInstaller collects package data.
    # With internet access this refreshes IGS and several major regional catalogs;
    # without internet the existing bundled snapshot is retained.
    refresh_station_snapshot()

    if args.gui:
        build_gui()
    if args.cli:
        build_cli()


if __name__ == "__main__":
    main()
