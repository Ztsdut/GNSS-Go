#!/usr/bin/env python3
"""Update the bundled Japan GEONET station catalog from official GSI sources.

Coordinates come from the GSI control-point GeoJSON tiles at zoom 7.  Station
number/prefecture/receiver/antenna are joined from Terras observation_code.php by
基準点コード.  The output is directly consumable by GNSS Go's Japan provider.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gnssgo.providers.japan_catalog import build_station_records, write_station_csv


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=str(ROOT / "src" / "gnssgo" / "resources" / "japan_geonet_stations.csv"),
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--reference-names",
        default=str(ROOT / "src" / "gnssgo" / "resources" / "JAPAN_GEONET_station_names_reference.txt"),
        help="Optional Japanese-name list used only for QC; official GSI sources remain authoritative.",
    )
    args = parser.parse_args()

    print("[GEONET] Fetching official GSI z=7 control-point GeoJSON tiles...")
    rows = build_station_records(workers=args.workers)
    if not rows:
        print("[GEONET] ERROR: no matched stations were produced.", file=sys.stderr)
        return 2
    target = write_station_csv(args.output, rows)
    mapped = sum(1 for row in rows if -90 <= row.latitude <= 90 and -180 <= row.longitude <= 180)
    print(f"[GEONET] stations={len(rows)} mapped={mapped}")
    reference = Path(args.reference_names) if args.reference_names else None
    if reference and reference.exists():
        import unicodedata
        def norm(value: str) -> str:
            return " ".join(unicodedata.normalize("NFKC", value).replace("\u3000", " ").split())
        expected = {norm(line) for line in reference.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()}
        actual = {norm(row.name_jp) for row in rows}
        print(f"[GEONET] reference-name QC: matched={len(expected & actual)}/{len(expected)} unmatched={len(expected - actual)}")
        if expected - actual:
            preview = ", ".join(sorted(expected - actual)[:12])
            print(f"[GEONET] reference names not in current official catalog (first 12): {preview}")
    print(f"[GEONET] wrote: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
