# GNSS Go

GNSS Go is an open-source desktop and command-line toolkit for discovering, planning, and downloading GNSS data from global and regional data sources.

The public naming is now consistent:

- **Application:** `GNSS Go`
- **Python package:** `gnssgo`
- **CLI:** `gnssgo` (also `gnss-go` when installed from Python)
- **Standalone GUI executable:** `GNSS-Go`
- **Python API:** `GNSSGo`
- **Environment-variable prefix:** `GNSSGO_`


## User manual

Separate Chinese and English manuals are provided:

- [Chinese PDF](docs/GNSS-Go_User_Manual_ZH.pdf)
- [English PDF](docs/GNSS-Go_User_Manual_EN.pdf)
- [Chinese source](docs/user_manual_zh.md)
- [English source](docs/user_manual_en.md)

To rebuild the PDFs after replacing the screenshots in `docs/images/`:

```bash
python -m pip install -e ".[docs]"
python tools/build_user_manuals.py
```

## Main features

- Global IGS station browsing and observation/product access.
- Regional GNSS/CORS networks across Asia, Europe, the Americas, Oceania, Africa, and Antarctica/IGS filtering.
- Interactive station map with OpenStreetMap when online and an offline fallback map.
- Observation, navigation, and precise-product download planning.
- Concurrent/resumable downloads with validation, retries, archive layout, and manifest records.
- RINEX 2/3/4 filename handling, compressed-file detection, and optional Hatanaka/Unix `.Z` decompression support.
- English/Chinese desktop GUI.
- Provider-specific workflows, including Japan GEONET browser automation and Korea GNSSData temporary-ZIP HTTP download.
- Proxy support for HTTP(S), FTP/SFTP workflows, and ChromeDriver-based providers.

## Install for end users

The recommended public distribution is through **GitHub Releases**. The release workflow in this repository builds artifacts on the corresponding native operating system:

- **Windows:** `GNSS-Go-Setup-<version>-Windows-x64.exe`
- **macOS:** `GNSS-Go-<version>-macOS.dmg`
- **Linux:** `GNSS-Go-<version>-Linux-x86_64.AppImage`
- A standalone `gnssgo` CLI executable is also published for each platform.

The Windows installer can optionally add the CLI to `PATH`. On macOS/Linux the release also contains the standalone CLI binary.

## Install from source

Python 3.11+ is required.

```bash
python -m venv .venv
```

Activate the environment:

```powershell
# Windows
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

Then install:

```bash
python -m pip install -e ".[build,hatanaka,unix-z]"
```

Launch the GUI:

```bash
gnssgo gui
```

or:

```bash
gnss-go gui
```

A GUI-only launcher is also installed:

```bash
gnss-go-gui
```

## Windows quick start (source install and local EXE build)

For Windows development/testing, open PowerShell in the repository root. The recommended sequence is:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[build,hatanaka,unix-z]"
```

Check that GNSS Go and PyInstaller are available:

```powershell
gnssgo --version
python -m PyInstaller --version
```

Launch the source-installed GUI:

```powershell
gnssgo gui
```

Build standalone GUI and CLI executables:

```powershell
python packaging\build.py --clean --gui --cli
```

The generated files are:

```text
dist\GNSS-Go.exe   # desktop GUI
dist\gnssgo.exe    # command-line program
```

Test them before creating an installer:

```powershell
.\dist\GNSS-Go.exe
.\dist\gnssgo.exe --help
```

### Windows installer (Inno Setup)

Install **Inno Setup 6**, then run:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=0.1.2 packaging\windows\GNSS-Go.iss
```

The installer is written to `release\` as:

```text
GNSS-Go-Setup-0.1.2-Windows-x64.exe
```

### Troubleshooting: `No module named PyInstaller`

If the build stops with:

```text
No module named PyInstaller
```

then PyInstaller is not installed in the active virtual environment. Keep the `.venv` activated and run:

```powershell
python -m pip install -e ".[build,hatanaka,unix-z]"
python -m PyInstaller --version
python packaging\build.py --clean --gui --cli
```

Installing only PyInstaller also works for this specific error:

```powershell
python -m pip install pyinstaller
```

However, installing the repository `build` extra is recommended because it keeps the packaging environment consistent with `pyproject.toml`.

## Bundled station-position snapshot

GNSS Go packages a compressed station-position snapshot so the map is populated immediately at first launch. The snapshot is loaded before the GUI appears, while live provider catalogs refresh silently in the background and merge newer metadata into the local cache.

The release build automatically runs:

```bash
python tools/update_bundled_station_snapshot.py --online
```

`packaging/build.py` also performs this refresh automatically before PyInstaller collects application resources. If some metadata services are unreachable, the previous packaged snapshot is retained and the build continues.

The repository snapshot includes all locally bundled coordinates (for example NOAA CORS, Korea, Taiwan, China, Europe/EPN, Brazil, Chile, Hong Kong, China, and seed IGS sites). Online release builds additionally refresh IGS and other major official catalogs before producing the installer. End users therefore see station points immediately and normally do not notice the background refresh.

English geographic labels use **Taiwan, China** and **Hong Kong, China** in the GUI.

## CLI examples

```bash
gnssgo --help
gnssgo doctor
```

Observation planning:

```bash
gnssgo obs \
  --station WUH200CHN \
  --start 2026-08-01 \
  --end 2026-08-03 \
  --provider whu \
  --dry-run
```

Navigation data:

```bash
gnssgo nav \
  --start 2026-08-01 \
  --end 2026-08-02 \
  --type mixed \
  --provider whu \
  --dry-run
```

Precise products:

```bash
gnssgo product \
  --type orbit clock erp \
  --date 2026-08-01 \
  --dry-run \
  --explain
```

Station catalog examples:

```bash
gnssgo station update
gnssgo station bbox --west 128 --south 30 --east 146 --north 46
gnssgo station list --data-network australia --regional-source CORSNET-NSW
```

## Python API

```python
from gnssgo import GNSSGo

client = GNSSGo()
plan = client.plan_observations(
    stations=["WUH200CHN"],
    start="2026-08-01",
    end="2026-08-03",
)
results = client.execute_plan(plan)
```

## Configuration and local files

Desktop settings use the platform-specific application configuration directory under **GNSS Go**. The station catalog uses the platform-specific cache directory.

Environment overrides use the `GNSSGO_` prefix and `__` for nested fields, for example:

```bash
GNSSGO_DOWNLOAD__WORKERS=8
```

Some provider-specific cached files/browser profiles may use:

```text
~/.gnssgo/
```

Downloaded data are stored under `./data` by default and are ignored by Git.

## Repository layout

```text
src/gnssgo/
  archive/       archive layout and manifest
  cli/           Typer command-line interface
  config/        settings and defaults
  download/      HTTP/browser/FTP/SFTP download machinery
  gui/           PySide6 desktop GUI and map resources
  models/        request/file/result/station models
  network/       proxy/network helpers
  products/      precise-product resolver and validation
  providers/     global and regional data-source adapters
  rinex/         RINEX naming/compression/post-processing
  stations/      local station catalog and spatial queries
  utils/         dates, GPS time, checksum, logging

packaging/
  build.py       cross-platform PyInstaller build helper
  windows/       Inno Setup installer
  macos/         DMG packaging
  linux/         AppImage packaging

.github/workflows/
  ci.yml
  build-release.yml
```

## Build standalone executables locally

Install the build dependencies:

```bash
pip install -e ".[build,hatanaka,unix-z]"
```

Build GUI + CLI using PyInstaller:

```bash
python packaging/build.py --clean --gui --cli
```

This creates platform-native artifacts under `dist/`. Installer/container packaging is then performed by the platform scripts under `packaging/`.

### Windows installer

After PyInstaller has generated `dist/GNSS-Go.exe` and `dist/gnssgo.exe`, build the installer with Inno Setup:

```powershell
ISCC.exe /DMyAppVersion=0.1.2 packaging\windows\GNSS-Go.iss
```

### macOS DMG

```bash
packaging/macos/build_dmg.sh 0.1.2
```

### Linux AppImage

Provide `appimagetool` and run:

```bash
APPIMAGE_TOOL=/path/to/appimagetool-x86_64.AppImage \
  packaging/linux/build_appimage.sh 0.1.2
```

For normal releases you do not need to build all three platforms locally. Push a tag such as `v0.1.2`; GitHub Actions builds each package on its native runner and attaches the artifacts to the GitHub Release.

## Tests

```bash
pip install -e ".[dev,hatanaka,unix-z]"
pytest -q -m "not integration"
ruff check src tests
```

Integration tests may contact third-party services and are kept separate from normal CI checks.

## Data-source policy

GNSS Go only automates endpoints that have been verified or explicitly documented. Sources that require registration, browser interaction, or manual retrieval are presented as such instead of guessing undocumented download URLs.

Third-party data remain subject to the original provider's copyright, license, citation, and access rules.

## License

MIT. See [LICENSE](LICENSE).
