from __future__ import annotations

import asyncio
import csv
import re
from collections import defaultdict
from datetime import date, timedelta
from ftplib import FTP, error_perm
from pathlib import Path

from gnssgo.exceptions import ProviderError
from gnssgo.models import ObservationRequest, RemoteFile, Station
from gnssgo.providers.base import ProviderCapabilities
from gnssgo.providers.regional_live import RegionalLiveProvider, _remote, _sampling_code
from gnssgo.utils.dates import datetime_to_doy


_KOREA_RESOURCE = Path(__file__).resolve().parent.parent / "resources" / "KOREA_CORS_stations.csv"


def _load_korea_station_coordinates() -> dict[str, tuple[float, float, float | None]]:
    result: dict[str, tuple[float, float, float | None]] = {}
    if not _KOREA_RESOURCE.exists():
        return result
    with _KOREA_RESOURCE.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("station") or "").strip().upper()
            if not re.fullmatch(r"[A-Z0-9]{4}", code):
                continue
            try:
                lat = float(row.get("latitude") or "")
                lon = float(row.get("longitude") or "")
            except (TypeError, ValueError):
                continue
            try:
                height = float(row.get("height") or "")
            except (TypeError, ValueError):
                height = None
            if 30.0 <= lat <= 40.5 and 120.0 <= lon <= 135.0:
                result[code] = (lat, lon, height)
    return result


def _korea_station(
    code: str,
    *,
    provider: str,
    source: str,
    network_label: str,
    coordinates: dict[str, tuple[float, float, float | None]],
    metadata: dict[str, str] | None = None,
) -> Station:
    code = code.upper()[:4]
    lat, lon, height = coordinates.get(code, (None, None, None))
    return Station(
        id=f"{code}00KOR",
        marker_name=code,
        latitude=lat,
        longitude=lon,
        height=height,
        country="KOR",
        network=[network_label],
        data_networks=["korea"],
        regional_sources=[source],
        providers=[provider],
        sampling_rates=["30S"],
        rinex_versions=["2", "3"],
        aliases=[code, code.lower()],
        metadata={
            "coordinate_source": str(_KOREA_RESOURCE),
            **(metadata or {}),
        },
    )


class KoreaKASIFTPProvider(RegionalLiveProvider):
    """Anonymous KASI FTP access for the KASINet and KVN daily archives.

    Both archive roots are queried for every requested day.  They contain
    different station sets, so treating one as a mirror of the other would
    silently lose observations.
    """

    name = "kasi_kr"
    data_network = "korea"
    source_type = "anonymous_ftp_directory"
    ftp_host = "gnss-ftp.kasi.re.kr"
    roots = ("kasinet", "kvn")
    regional_source = "korea_kasi"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, navigation=False, station_metadata=True)

    @staticmethod
    def _directory(root: str, day: date) -> str:
        doy = datetime_to_doy(day)
        return f"/{root}/daily/{day.year}/{doy:03d}/{day.year % 100:02d}d"

    @staticmethod
    def _station_from_filename(filename: str, day: date) -> tuple[str, str] | None:
        # Legacy/Hatanaka: ekvn0020.24d[.Z|.gz]
        short = re.match(
            rf"^(?P<site>[A-Za-z0-9]{{4}}){datetime_to_doy(day):03d}0\."
            rf"{day.year % 100:02d}d(?:\.(?:gz|z))?$",
            filename,
            re.I,
        )
        if short:
            return short.group("site").upper(), "2"

        # Long RINEX 3 daily observation: EKVN00KOR_R_20240020000_01D_30S_MO.crx[.gz]
        long_name = re.match(
            rf"^(?P<site>[A-Za-z0-9]{{4}})[A-Za-z0-9]{{5}}_[RSU]_"
            rf"{day.year}{datetime_to_doy(day):03d}\d{{4}}_01D_\d{{2}}S_MO\."
            rf"(?:crx|rnx)(?:\.gz)?$",
            filename,
            re.I,
        )
        if long_name:
            return long_name.group("site").upper(), "3"
        return None

    def _list_day_sync(self, day: date) -> list[tuple[str, str, int | None]]:
        rows: list[tuple[str, str, int | None]] = []
        with FTP(self.ftp_host, timeout=18) as ftp:
            ftp.login()
            ftp.voidcmd("TYPE I")
            for root in self.roots:
                directory = self._directory(root, day)
                try:
                    ftp.cwd(directory)
                except (OSError, error_perm):
                    continue
                try:
                    names = ftp.nlst()
                except (OSError, error_perm):
                    names = []
                for raw_name in names:
                    filename = raw_name.rstrip("/").rsplit("/", 1)[-1]
                    if not self._station_from_filename(filename, day):
                        continue
                    size = None
                    try:
                        size = ftp.size(filename)
                    except Exception:
                        pass
                    rows.append((root, filename, size))
        return rows

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        sampling = _sampling_code(request.sampling)
        if sampling not in {"", "30S"}:
            return []
        requested = {str(value)[:4].upper() for value in (request.stations or [])}
        rinex = str(request.rinex or "auto").lower()
        output: list[RemoteFile] = []

        for day in request.date_range.days():
            rows = await asyncio.to_thread(self._list_day_sync, day)
            variants: dict[str, list[RemoteFile]] = defaultdict(list)
            for root, filename, size in rows:
                parsed = self._station_from_filename(filename, day)
                if not parsed:
                    continue
                station4, version = parsed
                if requested and station4 not in requested:
                    continue
                if rinex == "2" and version != "2":
                    continue
                if rinex in {"3", "4"} and version != "3":
                    continue
                directory = self._directory(root, day)
                variants[station4].append(
                    _remote(
                        self.name,
                        f"ftp://{self.ftp_host}{directory}/{filename}",
                        filename,
                        station=f"{station4}00KOR",
                        day=day,
                        size=size,
                        metadata={
                            "regional_source": self.regional_source,
                            "regional_archive": f"KASI {root.upper()}",
                            "ftp_root": root,
                            "curl_fallback": "1",
                            "rinex_family": version,
                        },
                    )
                )

            # Auto prefers the long RINEX 3 observation when the same station/day
            # is available in both naming families, but keeps every alternative as
            # a same-station fallback.  KASINet and KVN are both searched.
            for station4, candidates in sorted(variants.items()):
                ordered = sorted(
                    candidates,
                    key=lambda item: (
                        0 if item.metadata.get("rinex_family") == "3" else 1,
                        0 if item.metadata.get("ftp_root") == "kasinet" else 1,
                        item.filename.lower(),
                    ),
                )
                primary = ordered[0]
                primary.fallback_candidates = ordered[1:]
                output.append(primary)
        return output

    async def fetch_station_catalog(self) -> list[Station]:
        coordinates = _load_korea_station_coordinates()
        # Discover the actual automatic-FTP subset from recent archive indices;
        # the national catalog is much larger and is exposed by ngii_kr below.
        codes: set[str] = set()
        today = date.today()
        errors: list[str] = []
        for offset in (2, 1, 3, 0, 4, 5, 6):
            day = today - timedelta(days=offset)
            try:
                rows = await asyncio.to_thread(self._list_day_sync, day)
            except Exception as exc:
                errors.append(f"{day}: {exc}")
                continue
            for _root, filename, _size in rows:
                parsed = self._station_from_filename(filename, day)
                if parsed:
                    codes.add(parsed[0])
            if codes:
                break
        if not codes and errors:
            raise ProviderError("KASI FTP station discovery failed: " + errors[-1])
        return [
            _korea_station(
                code,
                provider=self.name,
                source=self.regional_source,
                network_label="KASI KASINet/KVN",
                coordinates=coordinates,
                metadata={
                    "catalog_source": f"ftp://{self.ftp_host}/kasinet + /kvn",
                    "download_portal": "https://gnss.kasi.re.kr/gnss_download.php",
                },
            )
            for code in sorted(codes)
        ]


class KoreaNationalCatalogProvider(RegionalLiveProvider):
    """Korea GNSS Data Integrated Center daily RINEX provider.

    The public download page creates a short-lived ZIP on demand.  The browser
    workflow has been reproduced with the site's own public endpoints:

    ``GET getDownloadView.do`` -> session cookie
    ``POST poll/add.json`` -> usage statistic
    ``POST downLog/set.json`` -> download log
    ``POST download/createToZip.json`` -> temporary ``key``
    ``GET download/getZip.do?key=...`` -> ZIP bytes

    The ZIP key is intentionally *not* created while planning.  A RemoteFile
    here is a logical station/day request; the transport creates a fresh ZIP and
    consumes its key only when the user actually starts the download.
    """

    name = "ngii_kr"
    data_network = "korea"
    source_type = "public_web_session_zip"
    regional_source = "korea_national"
    base_url = "https://www.gnssdata.or.kr"
    portal_url = f"{base_url}/download/getDownloadView.do"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, navigation=False, station_metadata=True)

    async def fetch_station_catalog(self) -> list[Station]:
        coordinates = _load_korea_station_coordinates()
        return [
            _korea_station(
                code,
                provider=self.name,
                source=self.regional_source,
                network_label="Korea National CORS",
                coordinates=coordinates,
                metadata={
                    "catalog_source": str(_KOREA_RESOURCE),
                    "official_download_portal": self.portal_url,
                    "automatic_download": "true",
                    "download_transport": "GNSSData public web-session ZIP",
                },
            )
            for code in sorted(coordinates)
        ]

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        sampling = _sampling_code(request.sampling)
        if sampling not in {"", "30S"}:
            return []

        # The workflow verified against the current public portal is the daily
        # 30-second service (dataTyp=30).  Do not silently claim a requested
        # RINEX 3/4 file until the portal exposes/identifies a version selector
        # in this endpoint.  ``auto`` follows the portal's current daily product.
        rinex = str(request.rinex or "auto").lower()
        if rinex not in {"auto", "2"}:
            return []

        coordinates = _load_korea_station_coordinates()
        requested: list[str] = []
        for raw in request.stations or []:
            code = str(raw)[:4].upper()
            if code in coordinates and code not in requested:
                requested.append(code)
        if not requested:
            return []

        output: list[RemoteFile] = []
        for day in request.date_range.days():
            ymd = day.strftime("%Y%m%d")
            for code in requested:
                # This filename is GNSS Go's deterministic archive name.  The
                # server's Content-Disposition filename is timestamp-based and
                # changes for every temporary ZIP creation.
                filename = f"KOREA_NGII_{code}_{ymd}_30S.zip"
                output.append(
                    _remote(
                        self.name,
                        self.portal_url,
                        filename,
                        station=f"{code}00KOR",
                        day=day,
                        metadata={
                            "regional_source": self.regional_source,
                            "regional_archive": "Korea GNSS Data Integrated Center",
                            "http_transport": "ngii_session_zip",
                            "ngii_base_url": self.base_url,
                            "ngii_station": code,
                            "ngii_start": ymd,
                            "ngii_end": ymd,
                            "ngii_data_type": "30",
                            # Observed in the site's own downLog/set.json call.
                            # ZIP creation itself is keyed by corsId/date/dataTyp;
                            # this value is kept for the site's download log step.
                            "ngii_manager_code": "RZ",
                            "sampling": "30S",
                            "rinex_family": "2",
                            "max_parallel_downloads": "1",
                            "min_interval_seconds": "0.5",
                            "availability_scope": "on_demand_zip_created_at_download_time",
                        },
                    )
                )
        return output
