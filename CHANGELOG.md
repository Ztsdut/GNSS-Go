# Changelog

## 0.1.2

- Bundled an offline EPN station-coordinate fallback so European stations are visible immediately.
- Added silent EPN background refresh after startup.
- Removed the unused NASA-authenticated archive integration and related configuration/documentation.
- Split the user manual into separate Chinese and English PDF editions using the current GUI screenshots.

## 0.1.1

- Added a bundled station-position snapshot loaded before the GUI appears, with silent background refresh.
- Release/local packaging refreshes the snapshot online before PyInstaller when possible.
- English labels now use `Taiwan, China` and `Hong Kong, China`.
- Restored Hong Kong, China SatRef with bundled official station coordinates.
- Added/updated the bilingual PDF user manual and Windows build instructions.

## 0.1.0

Initial public release preparation of GNSS Go:

- Unified desktop GUI and command-line interface.
- Global IGS plus regional GNSS/CORS source catalogs.
- Observation, navigation, and precise-product planning/downloading.
- Japan GEONET browser automation and Korea GNSSData HTTP ZIP workflow.
- OpenStreetMap/offline map modes and bilingual English/Chinese GUI.
- Cross-platform PyInstaller build scripts and GitHub Actions release workflow.
