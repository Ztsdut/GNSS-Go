from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path

from gnssgo.models import NavigationRequest, ObservationRequest, ProductRequest, RemoteFile, Station
from gnssgo.providers.base import GNSSProvider, ProviderCapabilities
from gnssgo.providers.japan_catalog import (
    JapanStationRecord,
    build_station_records,
    legacy_geonet_id,
    read_station_csv,
    write_station_csv,
)

_RESOURCE = Path(__file__).resolve().parent.parent / "resources" / "japan_geonet_stations.csv"
_USER_CACHE = Path.home() / ".gnssgo" / "japan_geonet_stations.csv"
_TERRAS_URL = (
    "https://terras.gsi.go.jp/data_service.php#5/34.976002/138.713379/"
    "&base=pale&ls=pale%7Ckijuntengis_works%7Ckijuntengis_plan%7Ckijuntengis_stop"
    "&disp=1111&vs=c1j0h0k0l0u0t0z0r0s0f1"
)


def _records() -> list[JapanStationRecord]:
    for path in (_RESOURCE, _USER_CACHE):
        rows = read_station_csv(path)
        if rows:
            return rows
    return []


def _station(row: JapanStationRecord) -> Station:
    return Station(
        id=row.station_id,
        marker_name=row.name_jp,
        latitude=row.latitude,
        longitude=row.longitude,
        country="JPN",
        network=["GEONET"],
        data_networks=["japan"],
        regional_sources=["japan_geonet"],
        providers=["geonet_jp"],
        receiver=row.receiver or None,
        antenna=row.antenna or None,
        sampling_rates=["30S"],
        rinex_versions=["2", "3", "4"],
        constellations=["GPS", "GLONASS", "Galileo", "QZSS"],
        aliases=[row.station_no, row.file_code, row.point_code, row.name_jp, legacy_geonet_id(row.file_code)],
        metadata={
            "station_no": row.station_no,
            "file_code": row.file_code,
            "point_code": row.point_code,
            "prefecture": row.prefecture,
            "facility": row.facility,
            "coordinate_source": "GSI control-point GeoJSON z=7",
            "metadata_source": "https://terras.gsi.go.jp/observation_code.php",
        },
        data_availability=(
            "Official Terras web download; GNSS Go uses ChromeDriver automation in batches. "
            "GSI SFTP is also available separately after SFTP user registration."
        ),
    )


def _chunk_days(start: date, end: date, span_days: int = 10):
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=span_days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


class JapanGEONETProvider(GNSSProvider):
    name = "geonet_jp"
    data_network = "japan"
    regional_source = "japan_geonet"
    source_type = "official_geojson_plus_browser_automation"
    portal_url = _TERRAS_URL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, navigation=False, station_metadata=True)

    async def fetch_station_catalog(self) -> list[Station]:
        rows = _records()
        if not rows:
            # First use can bootstrap itself; the command-line updater writes the
            # same schema into the bundled resources directory for release builds.
            rows = await asyncio.to_thread(build_station_records)
            if rows:
                await asyncio.to_thread(write_station_csv, _USER_CACHE, rows)
        return [_station(row) for row in rows]

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        rows = _records()
        by_id: dict[str, JapanStationRecord] = {}
        for row in rows:
            for key in (row.station_id, legacy_geonet_id(row.file_code), row.station_no, row.file_code, row.point_code, row.name_jp):
                if key:
                    by_id[str(key).strip().upper()] = row

        selected: list[JapanStationRecord] = []
        seen: set[str] = set()
        for value in request.stations or []:
            row = by_id.get(str(value).strip().upper())
            if row is None or row.station_no in seen:
                continue
            seen.add(row.station_no)
            selected.append(row)
        if not selected:
            return []

        rinex = str(request.rinex or "auto").lower()
        if rinex == "2":
            rinex_choices = "2.11,2.12"
        elif rinex == "4":
            rinex_choices = "4.01,4"
        elif rinex == "3":
            rinex_choices = "3.02,3.03"
        else:
            # Default/Auto follows the proven Terras workflow: multi-GNSS GRJE
            # in RINEX 3.02.  RINEX 4.01 remains available when explicitly chosen.
            rinex_choices = "3.02"

        remotes: list[RemoteFile] = []
        # Terras is an interactive service. Keep station batches small (matching
        # the user's proven script) and split long date ranges to avoid server-side
        # range limits/very large bulk packages.
        for station_index in range(0, len(selected), 10):
            batch = selected[station_index:station_index + 10]
            names = [row.name_jp for row in batch]
            station_ids = [row.station_id for row in batch]
            for chunk_no, (start, end) in enumerate(
                _chunk_days(request.date_range.start, request.date_range.end), start=1
            ):
                filename = (
                    f"GEONET_{start:%Y%m%d}_{end:%Y%m%d}_"
                    f"b{station_index // 10 + 1:02d}_{chunk_no:02d}.zip"
                )
                remotes.append(
                    RemoteFile(
                        provider=self.name,
                        url=self.portal_url,
                        filename=filename,
                        data_type="obs",
                        station="GEONET",
                        date=start,
                        metadata={
                            "regional_source": self.regional_source,
                            "http_transport": "geonet_chromedriver",
                            "max_parallel_downloads": "1",
                            "min_interval_seconds": "2",
                            "no_transport_retries": "1",
                            "multi_station_bundle": "1",
                            "geonet_station_names": "\u241f".join(names),
                            "geonet_station_ids": "\u241f".join(station_ids),
                            "geonet_start": start.isoformat(),
                            "geonet_end": end.isoformat(),
                            "geonet_satellite_choices": "GRJE",
                            "geonet_rinex_choices": rinex_choices,
                            "availability_note": (
                                "Interactive Terras web download automated with ChromeDriver; "
                                "maximum 10 stations per browser batch and 10 days per package; Auto uses GRJE / RINEX 3.02."
                            ),
                        },
                    )
                )
        return remotes

    async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
        return []

    async def search_products(self, request: ProductRequest) -> list[RemoteFile]:
        return []

    async def health_check(self) -> dict[str, str]:
        return {
            "provider": self.name,
            "status": "browser_automation",
            "portal": self.portal_url,
            "catalog": str(_RESOURCE if _RESOURCE.exists() else _USER_CACHE),
        }
