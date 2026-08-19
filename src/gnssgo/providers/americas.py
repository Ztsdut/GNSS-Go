from __future__ import annotations

import asyncio
import csv
import gzip
import io
import math
import posixpath
import queue
import re
import stat
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
import httpx
from datetime import date, timedelta
from ftplib import FTP, error_perm
from html import unescape
from pathlib import Path

from gnssgo.exceptions import ProviderError, ProviderProtocolError
from gnssgo.models import ObservationRequest, RemoteFile, Station
from gnssgo.network import ProxyConfig
from gnssgo.providers.base import ProviderCapabilities
from gnssgo.providers.listing import parse_listing_filenames
from gnssgo.providers.regional_live import (
    RBMCProvider,
    RegionalLiveProvider,
    _matches_rinex,
    _remote,
    _sampling_code,
)
from gnssgo.rinex.naming import parse_rinex_filename
from gnssgo.stations.coordinates import normalize_longitude, valid_sirgas_coordinate
from gnssgo.utils.dates import datetime_to_doy


_SIRGAS_STATION_LIST = "https://sirgas.ipgh.org/maps/stations/stations-list.php"
_SIRGAS_FTP_HOST = "ftp.sirgas.org"
_SIRGAS_FTP_ROOT = "/pub/gps/SIRGAS"
_SIRGAS_HTTPS_ROOT = "https://www.sirgas.org/archive/gps/SIRGAS"
_SIRGAS_COORD_CACHE: dict[str, tuple[float, float, float | None]] | None = None
_SIRGAS_COORD_CACHE_SOURCE = ""
_SIRGAS_COORD_CACHE_AT = 0.0
_SIRGAS_COORD_LOCK = threading.Lock()


def _run_daemon_bounded(callable_, *, timeout: float, timeout_message: str):
    """Run a blocking network discovery with a hard wall-clock timeout.

    ``asyncio.wait_for(asyncio.to_thread(...))`` is not a hard timeout when the
    outer synchronous entry point uses ``asyncio.run``: loop shutdown waits for
    the default executor thread to finish.  Legacy FTP/SFTP servers can therefore
    leave the GUI in PLAN indefinitely when DNS/SSH stalls.  A daemon thread is
    intentionally used here so the planning worker can return after the deadline.
    """
    results: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            results.put((True, callable_()))
        except BaseException as exc:  # propagate to the planning worker
            results.put((False, exc))

    worker = threading.Thread(target=runner, daemon=True, name="gnssgo-network-discovery")
    worker.start()
    worker.join(timeout=max(0.1, float(timeout)))
    if worker.is_alive():
        raise ProviderError(timeout_message)
    try:
        ok, value = results.get_nowait()
    except queue.Empty as exc:
        raise ProviderError(timeout_message) from exc
    if ok:
        return value
    if isinstance(value, BaseException):
        raise value
    raise ProviderError(str(value))


def _ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Convert ECEF XYZ to geodetic latitude/longitude/height on GRS80."""
    a = 6378137.0
    inv_f = 298.257222101
    f = 1.0 / inv_f
    e2 = f * (2.0 - f)
    longitude = math.atan2(y, x)
    p = math.hypot(x, y)
    latitude = math.atan2(z, p * (1.0 - e2))
    height = 0.0
    for _ in range(10):
        sin_lat = math.sin(latitude)
        radius = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
        cos_lat = math.cos(latitude)
        if abs(cos_lat) < 1e-15:
            height = abs(z) - radius * (1.0 - e2)
            break
        height = p / cos_lat - radius
        next_latitude = math.atan2(z, p * (1.0 - e2 * radius / (radius + height)))
        if abs(next_latitude - latitude) < 1e-13:
            latitude = next_latitude
            break
        latitude = next_latitude
    return math.degrees(latitude), math.degrees(longitude), height


def _gps_week(day: date) -> int:
    return (day - date(1980, 1, 6)).days // 7


def _gps_week_start(week: int) -> date:
    return date(1980, 1, 6) + timedelta(weeks=week)


def _parse_sirgas_crd_text(text: str) -> dict[str, tuple[float, float, float | None]]:
    """Parse a Bernese/SIRGAS CRD coordinate file without assuming one column layout.

    Current and historical SIRGAS CRD files have changed spacing/auxiliary columns.
    The stable facts are a station identifier plus one ECEF XYZ triple with a norm
    near the Earth radius.  Detect those facts and keep both the full and 4-char ID.
    """
    result: dict[str, tuple[float, float, float | None]] = {}
    float_re = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[DEde][-+]?\d+)?")
    for line in text.splitlines():
        upper = line.upper()
        long_match = re.search(r"\b([A-Z0-9]{4}00[A-Z0-9]{3})\b", upper)
        short_match = re.search(r"\b([A-Z][A-Z0-9]{3})\b", upper)
        station_id = long_match.group(1) if long_match else (short_match.group(1) if short_match else "")
        if not station_id or station_id in _STATION_CELL_STOPWORDS:
            continue
        numbers: list[float] = []
        for token in float_re.findall(line):
            try:
                numbers.append(float(token.replace("D", "E").replace("d", "e")))
            except ValueError:
                pass
        xyz: tuple[float, float, float] | None = None
        for i in range(max(0, len(numbers) - 8), len(numbers) - 2):
            x, y, z = numbers[i : i + 3]
            radius = math.sqrt(x * x + y * y + z * z)
            if 5.5e6 <= radius <= 7.2e6 and max(abs(x), abs(y), abs(z)) >= 1e6:
                xyz = (x, y, z)
                break
        if xyz is None:
            for i in range(0, len(numbers) - 2):
                x, y, z = numbers[i : i + 3]
                radius = math.sqrt(x * x + y * y + z * z)
                if 5.5e6 <= radius <= 7.2e6 and max(abs(x), abs(y), abs(z)) >= 1e6:
                    xyz = (x, y, z)
                    break
        if xyz is None:
            continue
        latitude, longitude, height = _ecef_to_geodetic(*xyz)
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        value = (latitude, longitude, height)
        result[station_id] = value
        result.setdefault(station_id[:4], value)
    return result


def _fetch_latest_sirgas_crd_sync() -> tuple[dict[str, tuple[float, float, float | None]], str]:
    """Fetch one recent final SIRGAS weekly coordinate solution from the official FTP.

    We deliberately discover by GPS-week directory and backtrack several weeks: the
    current week is normally not combined yet.  A failure is soft; national provider
    catalogs still work and simply keep their original coordinate coverage.
    """
    with FTP(_SIRGAS_FTP_HOST, timeout=12) as ftp:
        ftp.login()
        current = _gps_week(date.today())
        for week in range(current, max(0, current - 16), -1):
            directory = f"{_SIRGAS_FTP_ROOT}/{week}"
            try:
                ftp.cwd(directory)
                names = ftp.nlst()
            except (OSError, error_perm):
                continue
            base_names = [name.rsplit("/", 1)[-1] for name in names]
            matches = [
                name for name in base_names
                if re.fullmatch(r"(?i)(?:sir|ibg)\d{2}P\d{4}\.crd(?:\.gz)?", name)
            ]
            if not matches:
                continue
            # Prefer SIR combined solution, then IBGE combined solution.
            matches.sort(key=lambda name: (0 if name.lower().startswith("sir") else 1, name.lower()))
            for filename in matches:
                buffer = io.BytesIO()
                try:
                    ftp.retrbinary(f"RETR {filename}", buffer.write)
                except (OSError, error_perm):
                    continue
                raw = buffer.getvalue()
                if filename.lower().endswith(".gz"):
                    try:
                        raw = gzip.decompress(raw)
                    except OSError:
                        continue
                text = raw.decode("utf-8", errors="replace")
                coords = _parse_sirgas_crd_text(text)
                if len(coords) >= 20:
                    return coords, f"ftp://{_SIRGAS_FTP_HOST}{directory}/{filename}"
        return {}, ""


def _sirgas_weekly_coordinates_sync() -> tuple[dict[str, tuple[float, float, float | None]], str]:
    global _SIRGAS_COORD_CACHE, _SIRGAS_COORD_CACHE_SOURCE, _SIRGAS_COORD_CACHE_AT
    with _SIRGAS_COORD_LOCK:
        now = time.time()
        if _SIRGAS_COORD_CACHE is not None and now - _SIRGAS_COORD_CACHE_AT < 24 * 3600:
            return _SIRGAS_COORD_CACHE, _SIRGAS_COORD_CACHE_SOURCE
        try:
            coords, source = _fetch_latest_sirgas_crd_sync()
        except Exception:
            coords, source = {}, ""
        # Cache failures briefly too so selecting 10 country layers does not open
        # 10 FTP sessions when ftp.sirgas.org is temporarily unreachable.
        _SIRGAS_COORD_CACHE = coords
        _SIRGAS_COORD_CACHE_SOURCE = source
        _SIRGAS_COORD_CACHE_AT = now if coords else now - (23 * 3600 + 50 * 60)
        return coords, source


async def _sirgas_weekly_coordinates() -> tuple[dict[str, tuple[float, float, float | None]], str]:
    return await asyncio.to_thread(_sirgas_weekly_coordinates_sync)


async def _sirgas_weekly_coordinates_for(
    provider: RegionalLiveProvider,
) -> tuple[dict[str, tuple[float, float, float | None]], str]:
    """Load a recent SIRGAS weekly CRD, preferring the official HTTPS archive.

    Many desktop/corporate networks block active/passive FTP even though normal
    HTTPS works.  The previous release therefore showed correct national station
    counts but MAP counts of 0--4 because the coordinate enrichment silently
    failed.  SIRGAS mirrors the operational weekly archive on www.sirgas.org, so
    discover the newest populated GPS-week directory over HTTPS first and retain
    FTP as a compatibility fallback.
    """
    global _SIRGAS_COORD_CACHE, _SIRGAS_COORD_CACHE_SOURCE, _SIRGAS_COORD_CACHE_AT
    now = time.time()
    with _SIRGAS_COORD_LOCK:
        if _SIRGAS_COORD_CACHE and now - _SIRGAS_COORD_CACHE_AT < 24 * 3600:
            return _SIRGAS_COORD_CACHE, _SIRGAS_COORD_CACHE_SOURCE

    weekly_coords: dict[str, tuple[float, float, float | None]] = {}
    weekly_source = ""
    try:
        root_text = await provider._get_text(f"{_SIRGAS_HTTPS_ROOT}/")
        current = _gps_week(date.today())
        weeks = {
            int(value)
            for value in re.findall(r"(?<!\d)(2\d{3})/(?!\d)", root_text)
            if 1800 <= int(value) <= current + 1
        }
        # Current SIRGAS operational solutions can be published several weeks
        # after observation. Search enough populated directories to survive a
        # temporary publication delay without walking the whole archive.
        for week in sorted(weeks, reverse=True)[:40]:
            directory = f"{_SIRGAS_HTTPS_ROOT}/{week}"
            try:
                listing = await provider._get_text(f"{directory}/")
            except Exception:
                continue
            names = parse_listing_filenames(listing)
            candidates = [
                name.rsplit("/", 1)[-1]
                for name in names
                if re.fullmatch(
                    rf"(?i)(?:sir|ibg)\d{{2}}P{week}\.crd",
                    name.rsplit("/", 1)[-1],
                )
            ]
            if not candidates:
                candidates = re.findall(
                    rf"(?i)\b(?:sir|ibg)\d{{2}}P{week}\.crd\b", listing
                )
            candidates = sorted(
                set(candidates),
                key=lambda name: (0 if name.lower().startswith("sir") else 1, name.lower()),
            )
            for filename in candidates:
                url = f"{directory}/{filename}"
                try:
                    text = await provider._get_text(url)
                except Exception:
                    continue
                coords = _parse_sirgas_crd_text(text)
                if len(coords) >= 20:
                    weekly_coords = coords
                    weekly_source = url
                    break
            if weekly_coords:
                break
    except Exception:
        pass

    # The current weekly solution naturally omits inactive stations.  SIRGAS also
    # publishes a compact multi-year SIRGAS2022 coordinate solution.  Merge that
    # as a historical fallback so national layers can still draw older CORS while
    # letting current weekly coordinates win for active stations.
    historical_coords: dict[str, tuple[float, float, float | None]] = {}
    historical_url = (
        f"{_SIRGAS_HTTPS_ROOT}/SIRGAS2022/SIRGAS2022_XYZ.CRD.gz"
    )
    try:
        response = await provider._request_get(historical_url)
        if response.status_code < 400 and response.content:
            raw = gzip.decompress(response.content)
            historical_coords = _parse_sirgas_crd_text(
                raw.decode("utf-8", errors="replace")
            )
    except Exception:
        pass

    if weekly_coords or historical_coords:
        merged = dict(historical_coords)
        merged.update(weekly_coords)
        source_parts = [x for x in (weekly_source, historical_url if historical_coords else "") if x]
        source = " + ".join(source_parts)
        with _SIRGAS_COORD_LOCK:
            _SIRGAS_COORD_CACHE = merged
            _SIRGAS_COORD_CACHE_SOURCE = source
            _SIRGAS_COORD_CACHE_AT = now
        return merged, source

    return await _sirgas_weekly_coordinates()


def _apply_coordinate_lookup(
    stations: list[Station],
    lookup: dict[str, tuple[float, float, float | None]],
    *,
    source: str,
) -> int:
    """Apply trusted SIRGAS coordinates and repair malformed table coordinates.

    Some national/SIRGAS HTML tables contain unrelated numeric columns such as
    year, DOY and sampling interval.  Older code could interpret two such values
    as latitude/longitude.  Before deciding a station is already mapped, validate
    its coordinate against the broad Latin-America envelope and the station's
    national envelope.  Invalid values are cleared so the official CRD lookup can
    replace them.
    """
    mapped = 0
    for station in stations:
        if station.latitude is not None and station.longitude is not None:
            if valid_sirgas_coordinate(
                station.latitude,
                station.longitude,
                country=station.country,
                country_strict=True,
            ):
                station.longitude = normalize_longitude(station.longitude)
                mapped += 1
                continue
            station.latitude = None
            station.longitude = None

        value = lookup.get(station.id.upper()) or lookup.get(station.id[:4].upper())
        if not value:
            continue
        latitude, longitude, height = value
        if not valid_sirgas_coordinate(
            latitude,
            longitude,
            country=station.country,
            country_strict=True,
        ):
            continue
        station.latitude = float(latitude)
        station.longitude = normalize_longitude(longitude)
        if station.height is None and height is not None:
            station.height = height
        if source:
            station.metadata["coordinate_source"] = source
        mapped += 1
    return mapped


def _strip_html(value: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _html_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, flags=re.I | re.S):
        cells = [
            _strip_html(cell)
            for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, flags=re.I | re.S)
        ]
        if cells:
            rows.append(cells)
    return rows


def _decimal_from_dms(value: str) -> float | None:
    text = value.strip().replace("º", "°").replace(",", ".")
    # Plain decimal first.
    m = re.search(r"[-+]?\d{1,3}(?:\.\d+)?", text)
    if m and not any(token in text for token in ("°", "'", '"')):
        try:
            number = float(m.group(0))
        except ValueError:
            return None
        if any(x in text.upper() for x in ("S", "W", "O")):
            number = -abs(number)
        return number
    m = re.search(
        r"(?P<deg>[-+]?\d{1,3})\D+(?P<min>\d{1,2})\D+(?P<sec>\d{1,2}(?:\.\d+)?)\s*(?P<hem>[NSEWO])?",
        text,
        flags=re.I,
    )
    if not m:
        return None
    deg = float(m.group("deg"))
    minute = float(m.group("min"))
    second = float(m.group("sec"))
    sign = -1.0 if deg < 0 or (m.group("hem") or "").upper() in {"S", "W", "O"} else 1.0
    return sign * (abs(deg) + minute / 60.0 + second / 3600.0)


_STATION_CELL_STOPWORDS = {
    "SITE", "CODE", "NAME", "STAT", "GNSS", "SIRG", "LAT", "LATI", "LONG",
    "LON", "CITY", "COUN", "NORT", "SOUT", "EAST", "WEST", "ITRF",
}


def _station_id_from_cells(
    cells: list[str],
    country_code: str,
    country_names: tuple[str, ...] = (),
) -> str | None:
    """Return the station in *this* country, never relabel another country's row.

    The SIRGAS table mixes stations from every national network.  The previous
    parser looked for a target-country 9-char id and, when it did not find one,
    blindly took the first 4-char token and appended the requested country code.
    Consequently the same ~600 SIRGAS rows were cloned into Bolivia, Colombia,
    Ecuador, Peru, etc.  A real 9-char RINEX id is authoritative: if the row
    contains one for another country, reject the row.  We only synthesize a
    long id from a 4-char code when the row explicitly names the target country.
    """
    joined = " ".join(cells).upper()
    long_ids = re.findall(r"\b([A-Z0-9]{4}00[A-Z0-9]{3})\b", joined)
    if long_ids:
        for station_id in long_ids:
            if station_id[-3:] == country_code:
                return station_id
        return None

    if country_names:
        row = " ".join(cells).casefold()
        if not any(name.casefold() in row for name in country_names):
            return None

    for cell in cells:
        token = cell.strip().upper()
        if (
            re.fullmatch(r"[A-Z0-9]{4}", token)
            and not token.isdigit()
            and token not in _STATION_CELL_STOPWORDS
        ):
            return f"{token}00{country_code}"
    return None


def _lat_lon_from_cells(cells: list[str]) -> tuple[float | None, float | None]:
    candidates: list[float] = []
    for cell in cells:
        value = _decimal_from_dms(cell)
        if value is not None:
            candidates.append(value)
    for i, lat in enumerate(candidates):
        if not -90 <= lat <= 90:
            continue
        for lon in candidates[i + 1 :]:
            if -180 <= lon <= 180:
                return lat, lon
    return None, None


class SIRGASCountryProvider(RegionalLiveProvider):
    """SIRGAS national-network catalog with explicit portal-only download fallback.

    SIRGAS delegates most SIRGAS-N observation archives to national data centres.
    This class supplies one consistent station layer for those centres for which a
    stable machine-download API is not yet public.  Concrete subclasses can override
    ``search_observations`` when a direct archive is known.
    """

    data_network = "sirgas"
    source_type = "sirgas_official_station_catalog"
    country_code = ""
    country_names: tuple[str, ...] = ()
    network_label = "SIRGAS"
    regional_source = ""
    portal_url = "https://sirgas.ipgh.org/en/gnss-network/data-centres/"
    _shared_station_catalog_text: str | None = None

    # Official SIRGAS/DGFI-TUM site-log archive.  The national station list is
    # the membership source; site logs are used only to fill map coordinates.
    # This is the same source that successfully fills the Colombia layer.
    sirgas_log_base = "https://www.sirgas.org/archive/gps/DGF/station/log"
    sirgas_log_concurrency = 16
    _shared_station_log_listing: str | None = None
    _shared_station_log_index: dict[tuple[str, str], str] | None = None

    async def _sirgas_catalog_text(self) -> str:
        # All national layers are filtered views of the same SIRGAS table. Cache
        # it process-wide so selecting the full Latin America group does not issue
        # one identical HTTP request per country. Concurrent first-load callers may
        # race at most once; subsequent country layers reuse the downloaded table.
        cached = SIRGASCountryProvider._shared_station_catalog_text
        if cached:
            return cached
        text = await self._get_text(_SIRGAS_STATION_LIST)
        if text:
            SIRGASCountryProvider._shared_station_catalog_text = text
        return text

    async def _sirgas_station_log_index(self) -> dict[tuple[str, str], str]:
        """Return newest official SIRGAS site log per (country, station code)."""
        cached = SIRGASCountryProvider._shared_station_log_index
        if cached is not None:
            return cached

        listing = SIRGASCountryProvider._shared_station_log_listing
        if not listing:
            listing = await self._get_text(f"{self.sirgas_log_base}/")
            if listing:
                SIRGASCountryProvider._shared_station_log_listing = listing

        names = list(parse_listing_filenames(listing or ""))
        # Recover filenames from Apache/theme variants that the generic listing
        # parser may not expose as plain href text.
        names.extend(
            re.findall(
                r"(?i)\b[a-z0-9]{4}00[a-z0-9]{3}_\d{8}\.log\b",
                listing or "",
            )
        )

        latest: dict[tuple[str, str], tuple[str, str]] = {}
        for raw_name in names:
            filename = raw_name.rsplit("/", 1)[-1]
            match = re.fullmatch(
                r"(?i)([a-z0-9]{4})00([a-z0-9]{3})_(\d{8})\.log",
                filename,
            )
            if not match:
                continue
            code = match.group(1).upper()
            country = match.group(2).upper()
            stamp = match.group(3)
            key = (country, code)
            previous = latest.get(key)
            if previous is None or stamp > previous[0]:
                latest[key] = (stamp, filename)

        result = {key: value[1] for key, value in latest.items()}
        if result:
            SIRGASCountryProvider._shared_station_log_index = result
        return result

    async def _enrich_from_sirgas_site_logs(
        self,
        stations: list[Station],
    ) -> tuple[int, str, int]:
        """Fill missing national SIRGAS map coordinates from official site logs."""
        missing = {
            station.id[:4].upper(): station
            for station in stations
            if station.latitude is None or station.longitude is None
        }
        if not missing:
            return 0, self.sirgas_log_base, 0

        index = await self._sirgas_station_log_index()
        selected = {
            code: filename
            for (country, code), filename in index.items()
            if country == self.country_code.upper() and code in missing
        }
        if not selected:
            return 0, self.sirgas_log_base, 0

        semaphore = asyncio.Semaphore(self.sirgas_log_concurrency)

        async def load_one(code: str, filename: str):
            url = f"{self.sirgas_log_base}/{filename}"
            try:
                async with semaphore:
                    text = await self._get_text(url)
                coordinate = RBMCProvider._rbmc_site_log_coordinate(text)
                return code, coordinate, url
            except Exception:
                return code, None, url

        loaded = await asyncio.gather(
            *(load_one(code, filename) for code, filename in selected.items())
        )

        mapped = 0
        for code, coordinate, url in loaded:
            if coordinate is None:
                continue
            latitude, longitude, height = coordinate
            longitude = normalize_longitude(longitude)
            if not valid_sirgas_coordinate(
                latitude,
                longitude,
                country=self.country_code,
                country_strict=True,
            ):
                continue

            station = missing[code]
            station.latitude = float(latitude)
            station.longitude = float(longitude)
            if station.height is None and height is not None:
                station.height = float(height)
            station.metadata["coordinate_source"] = url
            station.metadata["sirgas_site_log"] = url
            mapped += 1

        return mapped, self.sirgas_log_base, len(selected)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, station_metadata=True)

    async def fetch_station_catalog(self) -> list[Station]:
        text = await self._sirgas_catalog_text()
        stations = self._parse_sirgas_station_rows(text)
        if not stations:
            raise ProviderProtocolError(
                f"{self.name} could not identify {self.country_code} stations in the SIRGAS catalog."
            )

        # First use the shared weekly/multi-year SIRGAS coordinate solution.
        coordinate_source = ""
        weekly_mapped = 0
        try:
            before = sum(
                1 for station in stations
                if station.latitude is not None and station.longitude is not None
            )
            weekly, coordinate_source = await _sirgas_weekly_coordinates_for(self)
            _apply_coordinate_lookup(stations, weekly, source=coordinate_source)
            after = sum(
                1 for station in stations
                if station.latitude is not None and station.longitude is not None
            )
            weekly_mapped = max(0, after - before)
        except Exception:
            pass

        # Then fill every remaining station from the official SIRGAS site-log
        # archive.  This is especially important for national-network stations
        # not present in the current weekly combined solution.
        log_mapped = 0
        log_available = 0
        log_source = ""
        log_error = ""
        if any(
            station.latitude is None or station.longitude is None
            for station in stations
        ):
            try:
                log_mapped, log_source, log_available = (
                    await self._enrich_from_sirgas_site_logs(stations)
                )
            except Exception as exc:
                log_error = f"{type(exc).__name__}: {exc}"

        mapped_total = sum(
            1 for station in stations
            if station.latitude is not None and station.longitude is not None
        )
        stats = {
            "catalog_complete": True,
            "station_count": len(stations),
            "mapped_station_count": mapped_total,
            "regional_source": self.regional_source,
            "catalog_source_used": _SIRGAS_STATION_LIST,
            "coordinate_source_used": (
                log_source if log_mapped else (coordinate_source or _SIRGAS_STATION_LIST)
            ),
            "sirgas_weekly_mapped": weekly_mapped,
            "sirgas_site_log_available": log_available,
            "sirgas_site_log_mapped": log_mapped,
        }
        if log_error:
            stats["sirgas_site_log_error"] = log_error
        self.last_station_catalog_stats = stats
        return stations

    def _parse_sirgas_station_rows(self, text: str) -> list[Station]:
        stations: dict[str, Station] = {}
        for cells in _html_rows(text):
            station_id = _station_id_from_cells(
                cells, self.country_code, self.country_names
            )
            if not station_id:
                continue
            lat, lon = _lat_lon_from_cells(cells)
            # Reject numeric table fields that merely happen to fit latitude/
            # longitude ranges (e.g. DOY=12, interval=30 -> 12N, 30E).
            # Missing/invalid coordinates are intentionally kept as None so the
            # official weekly SIRGAS CRD enrichment can fill them below.
            if lat is not None and lon is not None:
                lon = normalize_longitude(lon)
                if not valid_sirgas_coordinate(
                    lat, lon, country=self.country_code, country_strict=True
                ):
                    lat = lon = None
            stations[station_id] = Station(
                id=station_id,
                marker_name=station_id[:4],
                latitude=lat,
                longitude=lon,
                country=self.country_code,
                network=[self.network_label, "SIRGAS-CON"],
                data_networks=["sirgas"],
                regional_sources=[self.regional_source],
                providers=[self.name],
                aliases=[station_id[:4]],
                sampling_rates=["30S"],
                rinex_versions=["2", "3"],
                metadata={
                    "catalog_source": _SIRGAS_STATION_LIST,
                    "distribution": "SIRGAS national data centre",
                },
            )
        return list(stations.values())

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        raise ProviderError(
            f"{self.network_label} observations are managed by the national SIRGAS data centre. "
            f"GNSS Go has indexed the stations, but this centre does not expose a stable public "
            f"machine-download endpoint yet. Official access: {self.portal_url}"
        )


class RAMSACArgentinaProvider(SIRGASCountryProvider):
    name = "ramsac_ar"
    country_code = "ARG"
    country_names = ("Argentina",)
    network_label = "Argentina RAMSAC"
    regional_source = "sirgas_argentina"
    portal_url = "https://www.ign.gob.ar/NuestrasActividades/Geodesia/Ramsac/DescargaRinex"
    status_url = "https://www.ign.gob.ar/NuestrasActividades/Ramsac/EstacionesPermanentes"
    # Official KML linked by IGN's RAMSAC map page.  Treat this layer as the
    # coordinate catalog, not merely as an optional enrichment of SIRGAS-CON.
    kml_url = (
        "https://dnsg.ign.gob.ar/apps/api/v1/capas-sig/"
        "Geodesia%2By%2Bdemarcaci%C3%B3n/Redes%2Bgeod%C3%A9sicas/ramsac/kml"
    )

    async def fetch_station_catalog(self) -> list[Station]:
        status_text = await self._get_text(self.status_url)
        status_rows = _html_rows(status_text)
        status_by_code: dict[str, list[str]] = {}
        for cells in status_rows:
            if not cells:
                continue
            code = cells[0].strip().upper()
            if re.fullmatch(r"[A-Z0-9]{4}", code) and not code.isdigit():
                status_by_code[code] = cells
        known_codes = set(status_by_code)

        coords: dict[str, tuple[float, float, float | None]] = {}
        coordinate_source = ""
        kml_error = ""
        try:
            response = await self._request_get(self.kml_url)
            if response.status_code < 400 and response.content:
                coords = self._parse_ramsac_kml_payload(
                    response.content,
                    known_codes=known_codes,
                )
                if coords:
                    coordinate_source = self.kml_url
            else:
                kml_error = f"HTTP {response.status_code}"
        except Exception as exc:
            kml_error = f"{type(exc).__name__}: {exc}"

        # Keep SIRGAS coordinates only as a fallback for RAMSAC status stations
        # that are genuinely absent from the official map layer.
        weekly_lookup: dict[str, tuple[float, float, float | None]] = {}
        weekly_source = ""
        if any(code not in coords for code in known_codes):
            try:
                weekly_lookup, weekly_source = await _sirgas_weekly_coordinates_for(self)
            except Exception:
                pass

        # The official map layer is a coordinate catalog in its own right.  Use
        # the union of map placemarks and the live status table so a station is
        # never discarded merely because one of the two IGN views lags the other.
        all_codes = sorted(known_codes | set(coords))
        stations: dict[str, Station] = {}
        for code in all_codes:
            cells = status_by_code.get(code, [])
            latitude = longitude = height = None
            used_source = ""
            if code in coords:
                latitude, longitude, height = coords[code]
                used_source = coordinate_source
            else:
                weekly_value = weekly_lookup.get(f"{code}00ARG") or weekly_lookup.get(code)
                if weekly_value:
                    candidate_lat, candidate_lon, candidate_height = weekly_value
                    # RAMSAC contains a few partner/border stations.  Use a broad
                    # southern-South-America envelope instead of ARG strict bounds.
                    if (
                        -60.0 <= candidate_lat <= -15.0
                        and -80.0 <= normalize_longitude(candidate_lon) <= -45.0
                    ):
                        latitude = float(candidate_lat)
                        longitude = normalize_longitude(candidate_lon)
                        height = candidate_height
                        used_source = weekly_source

            status = cells[-1].strip() if cells else ""
            interval = next(
                (x for x in cells if re.fullmatch(r"\d+(?:\.\d+)?", x.strip())),
                "",
            )
            station_id = f"{code}00ARG"
            stations[station_id] = Station(
                id=station_id,
                marker_name=code,
                latitude=latitude,
                longitude=longitude,
                height=height,
                country="ARG",
                network=["RAMSAC"],
                data_networks=["argentina", "sirgas"],
                regional_sources=[self.regional_source],
                providers=[self.name],
                aliases=[code],
                sampling_rates=["01S", "15S", "30S"],
                rinex_versions=["2", "3"],
                data_availability=status or None,
                metadata={
                    "catalog_source": self.status_url,
                    "coordinate_source": used_source,
                    "latest_file": cells[1].strip() if len(cells) > 1 else "",
                    "reported_interval_seconds": interval,
                    "official_map_source": self.kml_url,
                },
            )

        if not stations:
            raise ProviderProtocolError("RAMSAC station status/KML returned no stations.")
        mapped_count = sum(
            1 for station in stations.values()
            if station.latitude is not None and station.longitude is not None
        )
        self.last_station_catalog_stats = {
            "catalog_complete": True,
            "station_count": len(stations),
            "mapped_station_count": mapped_count,
            "ramsac_status_station_count": len(known_codes),
            "ramsac_kml_station_count": len(coords),
            "regional_source": self.regional_source,
            "catalog_source_used": self.status_url,
            "coordinate_source_used": coordinate_source or weekly_source,
        }
        if kml_error:
            self.last_station_catalog_stats["ramsac_kml_error"] = kml_error
        return list(stations.values())

    @classmethod
    def _parse_ramsac_kml_payload(
        cls,
        raw: bytes,
        *,
        known_codes: set[str] | None = None,
    ) -> dict[str, tuple[float, float, float | None]]:
        """Parse IGN's KML endpoint even if a proxy returns KMZ/ZIP bytes."""
        if not raw:
            return {}
        text = ""
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = [name for name in archive.namelist() if name.lower().endswith(".kml")]
                if names:
                    text = archive.read(names[0]).decode("utf-8", errors="replace")
        except (zipfile.BadZipFile, OSError, KeyError):
            pass
        if not text:
            text = raw.decode("utf-8", errors="replace")
        return cls._parse_ramsac_kml(text, known_codes=known_codes)

    @staticmethod
    def _parse_ramsac_kml(
        text: str,
        *,
        known_codes: set[str] | None = None,
    ) -> dict[str, tuple[float, float, float | None]]:
        """Parse the official RAMSAC KML/GeoJSON into station coordinates."""
        if not text.strip():
            return {}
        result: dict[str, tuple[float, float, float | None]] = {}
        known = {code.upper() for code in (known_codes or set())}

        def pick_code(haystack: str, preferred: str = "") -> str | None:
            preferred = preferred.strip().upper()
            if re.fullmatch(r"[A-Z0-9]{4}", preferred) and not preferred.isdigit():
                if not known or preferred in known:
                    return preferred
            upper = haystack.upper()
            long_match = re.search(r"(?<![A-Z0-9])([A-Z0-9]{4})00[A-Z0-9]{3}(?![A-Z0-9])", upper)
            if long_match:
                code = long_match.group(1)
                if not known or code in known:
                    return code
            if known:
                for code in sorted(known):
                    if re.search(rf"(?<![A-Z0-9]){re.escape(code)}(?![A-Z0-9])", upper):
                        return code
                return None
            for match in re.finditer(r"(?<![A-Z0-9])([A-Z0-9]{4})(?![A-Z0-9])", upper):
                code = match.group(1)
                if code not in _STATION_CELL_STOPWORDS and not code.isdigit():
                    return code
            return None

        def valid_point(latitude: float, longitude: float) -> bool:
            longitude = normalize_longitude(longitude)
            return -60.0 <= latitude <= -15.0 and -80.0 <= longitude <= -45.0

        stripped = text.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                import json
                payload = json.loads(text)
            except Exception:
                return {}
            features = payload.get("features", []) if isinstance(payload, dict) else payload
            if not isinstance(features, list):
                return {}
            for feature in features:
                if not isinstance(feature, dict):
                    continue
                props = feature.get("properties") or {}
                geom = feature.get("geometry") or {}
                coordinates = geom.get("coordinates") if isinstance(geom, dict) else None
                preferred = str(props.get("name") or props.get("codigo") or props.get("code") or "")
                code = pick_code(" ".join(f"{k} {v}" for k, v in props.items()), preferred)
                if not code or not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
                    continue
                try:
                    longitude = normalize_longitude(float(coordinates[0]))
                    latitude = float(coordinates[1])
                    height = float(coordinates[2]) if len(coordinates) > 2 and coordinates[2] is not None else None
                except (TypeError, ValueError):
                    continue
                if valid_point(latitude, longitude):
                    result[code] = (latitude, longitude, height)
            return result

        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return {}
        for placemark in root.iter():
            if placemark.tag.rsplit("}", 1)[-1].lower() != "placemark":
                continue
            texts: list[str] = []
            preferred_name = ""
            coordinate_text = ""
            gx_coord_text = ""
            for node in placemark.iter():
                tag = node.tag.rsplit("}", 1)[-1].lower()
                value = (node.text or "").strip()
                if value:
                    texts.append(value)
                if tag == "name" and value and not preferred_name:
                    preferred_name = value
                if tag == "coordinates" and value and not coordinate_text:
                    coordinate_text = value
                if tag == "coord" and value and not gx_coord_text:
                    gx_coord_text = value
                for key, attr_value in node.attrib.items():
                    if attr_value:
                        texts.append(f"{key} {attr_value}")
            code = pick_code(" ".join(texts), preferred_name)
            if not code:
                continue
            longitude = latitude = height = None
            try:
                if coordinate_text:
                    parts = coordinate_text.split()[0].split(",")
                    if len(parts) >= 2:
                        longitude = normalize_longitude(float(parts[0]))
                        latitude = float(parts[1])
                        height = float(parts[2]) if len(parts) > 2 and parts[2] else None
                elif gx_coord_text:
                    parts = gx_coord_text.replace(",", " ").split()
                    if len(parts) >= 2:
                        longitude = normalize_longitude(float(parts[0]))
                        latitude = float(parts[1])
                        height = float(parts[2]) if len(parts) > 2 else None
            except ValueError:
                continue
            if latitude is None or longitude is None:
                continue
            if valid_point(latitude, longitude):
                result[code] = (latitude, longitude, height)
        return result


class SIRGASRBMCProvider(RBMCProvider):
    """IBGE RBMC presented inside the SIRGAS / Latin America group."""

    name = "sirgas_rbmc_br"
    data_network = "sirgas"

    async def fetch_station_catalog(self) -> list[Station]:
        # Brazil normally comes straight from the bundled/local official IBGE KMZ.
        # The older SIRGAS CRD/site-log chain is retained only as a fallback when
        # that local snapshot is unavailable or damaged.
        self._defer_site_log_enrichment = True
        try:
            stations = await super().fetch_station_catalog()
        finally:
            self._defer_site_log_enrichment = False
        stats = dict(getattr(self, "last_station_catalog_stats", {}) or {})
        local_kmz_loaded = bool(stats.get("local_rbmc_kmz"))

        coordinate_source = ""
        # When the packaged/local official IBGE KMZ supplied the map catalog, do
        # not make Brazil startup depend on a second SIRGAS weekly network call.
        if not local_kmz_loaded:
            try:
                weekly, coordinate_source = await _sirgas_weekly_coordinates_for(self)
                _apply_coordinate_lookup(stations, weekly, source=coordinate_source)
            except Exception:
                pass

        ibge_log_mapped = 0
        ibge_log_source = ""
        if (
            not local_kmz_loaded
            and any(station.latitude is None or station.longitude is None for station in stations)
        ):
            try:
                ibge_log_mapped, ibge_log_source = (
                    await self._enrich_from_ibge_site_logs(stations)
                )
            except Exception:
                # A coordinate fallback must never make the station catalogue fail.
                ibge_log_mapped = 0
                ibge_log_source = ""

        for station in stations:
            station.data_networks = sorted({*station.data_networks, "brazil", "sirgas"})
            station.regional_sources = sorted({*station.regional_sources, "sirgas_brazil"})
            station.providers = sorted({*station.providers, self.name})
            station.network = sorted({*station.network, "SIRGAS"})
        stats["mapped_station_count"] = sum(
            1 for station in stations
            if station.latitude is not None and station.longitude is not None
        )
        if coordinate_source:
            stats["sirgas_coordinate_source"] = coordinate_source
        if ibge_log_mapped:
            stats["ibge_site_log_mapped"] = ibge_log_mapped
        if ibge_log_source:
            stats["ibge_coordinate_source"] = ibge_log_source
        self.last_station_catalog_stats = stats
        return stations


class ChileCSNProvider(SIRGASCountryProvider):
    # One CSN YYYY/DOY directory listing can satisfy every selected station for
    # that date.  Let the core batch explicit selections and also support the
    # GUI all-available directory mode.
    batch_observation_passthrough = True
    network_directory_discovery = True
    name = "sirgas_cl"
    country_code = "CHL"
    country_names = ("Chile",)
    network_label = "Chile CSN"
    regional_source = "sirgas_chile"
    portal_url = "https://gps.csn.uchile.cl/data/"
    archive = "https://gps.csn.uchile.cl/data"
    archive_fallback = "http://gps.csn.uchile.cl/data"

    # The official CSN KML is bundled with the application and is the primary
    # station/coordinate source used by the map.
    station_kml_filename = "CSN_GNSS_stations.kml"
    station_catalog_filename = "csn_chile_stations.csv"

    @classmethod
    def station_kml_path(cls) -> Path:
        return Path(__file__).resolve().parents[1] / "resources" / cls.station_kml_filename

    @classmethod
    def station_catalog_path(cls) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / cls.station_catalog_filename

    @staticmethod
    def _parse_csn_kml(text: str) -> dict[str, tuple[float, float]]:
        """Parse official CSN KML placemarks into 4-char station coordinates."""
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ProviderProtocolError(f"Invalid Chile CSN KML: {exc}") from exc

        coordinates: dict[str, tuple[float, float]] = {}
        for placemark in root.iter():
            if placemark.tag.rsplit("}", 1)[-1].lower() != "placemark":
                continue

            code = ""
            point = ""
            for node in placemark.iter():
                tag = node.tag.rsplit("}", 1)[-1].lower()
                value = (node.text or "").strip()
                if tag == "name" and not code:
                    candidate = value.upper()[:4]
                    if re.fullmatch(r"[A-Z0-9]{4}", candidate):
                        code = candidate
                elif tag == "coordinates" and value and not point:
                    point = value

            if not code or not point:
                continue

            parts = point.split()[0].split(",")
            if len(parts) < 2:
                continue
            try:
                longitude = normalize_longitude(float(parts[0]))
                latitude = float(parts[1])
            except ValueError:
                continue
            if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
                continue
            coordinates[code] = (latitude, longitude)

        return coordinates

    def _stations_from_csn_kml(self, path: Path) -> list[Station]:
        coordinates = self._parse_csn_kml(path.read_text(encoding="utf-8", errors="replace"))
        return [
            Station(
                id=f"{code}00CHL",
                marker_name=code,
                country="CHL",
                latitude=latitude,
                longitude=longitude,
                height=None,
                network=["CSN"],
                data_networks=["sirgas"],
                regional_sources=[self.regional_source],
                providers=[self.name],
                aliases=[code],
                sampling_rates=["01S"],
                rinex_versions=["2", "3"],
                metadata={
                    "catalog_source": str(path),
                    "membership_source": "bundled official CSN GNSS KML",
                    "coordinate_source": str(path),
                    "published_sampling": "01S",
                },
            )
            for code, (latitude, longitude) in sorted(coordinates.items())
        ]

    @staticmethod
    def _csn_station_id(filename: str) -> str:
        name = filename.rsplit("/", 1)[-1]
        info = parse_rinex_filename(name)
        raw = str(info.station or "").upper()
        if len(raw) >= 9:
            return raw[:9]
        token = name.split("_", 1)[0].upper()
        code = (raw or token[:4])[:4]
        if not re.fullmatch(r"[A-Z0-9]{4}", code):
            return ""
        return f"{code}00CHL"

    @staticmethod
    def _is_csn_observation_name(filename: str) -> bool:
        low = filename.lower()
        return (
            low.endswith((".crx.gz", ".rnx.gz", ".crx", ".rnx", ".zip"))
            or bool(re.search(r"\.\d{2}[do](?:\.gz|\.z|\.zip)?$", low))
        )

    async def _csn_day_listing(self, day: date) -> tuple[str, list[str]]:
        key = f"csn-day:{day.year}:{datetime_to_doy(day):03d}"
        cached = self.discovery_cache.get(self.name, key)
        if cached is not None:
            directory, names = cached
            return str(directory), list(names)

        directory = f"{self.archive}/{day.year}/{datetime_to_doy(day):03d}/"
        try:
            if isinstance(self.client, httpx.Client):
                network_settings = getattr(self, "_network_settings", None)
                proxy_config = ProxyConfig.from_settings(network_settings)
                proxy_config.validate()
                proxy_url = proxy_config.proxy_url(protocol="https")
                trust_env = (
                    proxy_config.mode == "system"
                    and proxy_config.use_for_http
                    and proxy_url is None
                )

                def fetch_directory() -> httpx.Response:
                    # Use a short-lived client so PLAN follows the same effective
                    # proxy as DOWNLOAD.  Keep this request minimal: one GET to
                    # the authoritative day directory, with no HTTP fallback.
                    with httpx.Client(
                        timeout=httpx.Timeout(10.0, connect=6.0, read=8.0),
                        follow_redirects=False,
                        proxy=proxy_url,
                        trust_env=trust_env,
                    ) as client:
                        return client.get(
                            directory,
                            headers={
                                "User-Agent": (
                                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/150.0 Safari/537.36"
                                ),
                                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                            },
                        )

                response = _run_daemon_bounded(
                    fetch_directory,
                    timeout=12.0,
                    timeout_message=f"Chile CSN directory request timed out: {directory}",
                )
                if response.status_code == 404:
                    text = ""
                elif response.status_code in {301, 302, 303, 307, 308}:
                    location = str(response.headers.get("location") or "").strip()
                    raise ProviderError(
                        f"Chile CSN directory redirected unexpectedly (HTTP {response.status_code}, "
                        f"Location={location or 'missing'}): {directory}"
                    )
                elif response.status_code >= 400:
                    raise ProviderError(
                        f"Chile CSN returned HTTP {response.status_code}: {directory}"
                    )
                else:
                    text = response.text
            else:
                # Preserve injected AsyncClient support used by tests.
                text = await self._get_text(directory)
        except Exception:
            raise

        names = [
            name.rsplit("/", 1)[-1]
            for name in parse_listing_filenames(text)
            if self._is_csn_observation_name(name.rsplit("/", 1)[-1])
        ]
        names = list(dict.fromkeys(names))
        if names:
            self.discovery_cache.set(self.name, key, (directory, names))
        return directory, names

    async def fetch_station_catalog(self) -> list[Station]:
        kml_path = self.station_kml_path()
        stations: list[Station] = []
        source_kind = ""
        source_path: Path | None = None

        if kml_path.exists():
            stations = self._stations_from_csn_kml(kml_path)
            source_kind = "KML"
            source_path = kml_path

        if not stations:
            csv_path = self.station_catalog_path()
            if csv_path.exists():
                stations = []
                with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        code = str(row.get("code") or "").strip().upper()[:4]
                        if not re.fullmatch(r"[A-Z0-9]{4}", code):
                            continue
                        try:
                            latitude = float(row["latitude"])
                            longitude = normalize_longitude(float(row["longitude"]))
                        except (KeyError, TypeError, ValueError):
                            continue
                        if not (-90.0 <= latitude <= 90.0):
                            continue

                        height = None
                        try:
                            raw_height = str(row.get("height_m") or "").strip()
                            if raw_height:
                                height = float(raw_height)
                        except ValueError:
                            height = None

                        station_id = str(
                            row.get("station_id") or f"{code}00CHL"
                        ).strip().upper()
                        if len(station_id) < 9:
                            station_id = f"{code}00CHL"
                        marker_name = str(row.get("name") or code).strip() or code
                        downloadable = str(row.get("downloadable") or "").strip().lower() in {
                            "1", "true", "yes", "y"
                        }
                        stations.append(
                            Station(
                                id=station_id,
                                marker_name=marker_name,
                                country="CHL",
                                latitude=latitude,
                                longitude=longitude,
                                height=height,
                                network=["CSN"],
                                data_networks=["sirgas"],
                                regional_sources=[self.regional_source],
                                providers=[self.name],
                                aliases=[code],
                                sampling_rates=["01S"],
                                rinex_versions=["2", "3"],
                                metadata={
                                    "catalog_source": str(
                                        row.get("source_url") or "https://gps.csn.uchile.cl/"
                                    ),
                                    "membership_source": "static CSN GNSS archive snapshot",
                                    "coordinate_source": str(
                                        row.get("source_url") or "CSN/FDSN station metadata"
                                    ),
                                    "downloadable_on_snapshot": downloadable,
                                    "map_style": str(row.get("map_style") or ""),
                                    "snapshot_updated_utc": str(row.get("updated_utc") or ""),
                                    "published_sampling": "01S",
                                },
                            )
                        )
                source_kind = "CSV fallback"
                source_path = csv_path

        if not stations or source_path is None:
            raise ProviderProtocolError(
                "Chile CSN station catalogue is missing or invalid. Expected bundled resource "
                f"{self.station_kml_filename} (preferred) or {self.station_catalog_filename}."
            )

        self.last_station_catalog_stats = {
            "catalog_complete": True,
            "station_count": len(stations),
            "mapped_station_count": len(stations),
            "regional_source": self.regional_source,
            "catalog_source_used": str(source_path),
            "coordinate_source_used": str(source_path),
            "csn_primary_catalog": True,
            "csn_static_catalog": True,
            "csn_catalog_format": source_kind,
            "sirgas_coordinate_fallback_only": False,
        }
        return stations

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        # CSN publishes daily 1 Hz compact/Hatanaka RINEX.  For explicitly
        # selected stations, build the exact classic filename locally instead of
        # hitting the Apache directory page first.  That saves one request per
        # day and matters because gps.csn.uchile.cl rate-limits bursts with 429.
        # When the user asks for the whole network, a single directory listing is
        # still used so the plan contains only files that are actually published.
        requested_sampling = _sampling_code(request.sampling)
        if request.sampling and requested_sampling != "01S":
            return []

        rinex_value = str(getattr(request.rinex, "value", request.rinex) or "auto").lower()
        if rinex_value not in {"auto", "2", "2.11", "rinex2"}:
            return []

        selected: dict[str, str] = {}
        for station in request.stations or []:
            canonical = str(station).upper()
            code = canonical[:4]
            if re.fullmatch(r"[A-Z0-9]{4}", code):
                selected.setdefault(code, canonical)

        files: list[RemoteFile] = []
        for day in request.date_range.days():
            doy = datetime_to_doy(day)
            yy = day.year % 100
            directory = f"{self.archive}/{day.year}/{doy:03d}/"
            names: list[str] = []
            listing_ok = False
            try:
                directory, names = await self._csn_day_listing(day)
                listing_ok = bool(names)
            except Exception:
                # CSN sometimes serves the exact RINEX file to a normal browser
                # while rejecting scripted requests to the Apache directory page.
                # For an explicit station selection, do not turn that directory
                # failure into a false "no data" result.  The exact file URL is
                # deterministic and the browser-backed downloader will perform the
                # authoritative 200/404 check.  Whole-network discovery still
                # requires a real day listing so we never invent 125 files.
                names = []

            if selected and not listing_ok:
                names = [
                    f"{code.lower()}{doy:03d}0.{yy:02d}d.Z"
                    for code in sorted(selected)
                ]
                discovery = "explicit_station_candidate"
            else:
                discovery = "official_day_directory"

            for filename in names:
                station_id = self._csn_station_id(filename)
                code = station_id[:4].upper() if station_id else filename[:4].upper()
                if selected and code not in selected:
                    continue
                if not self._is_csn_observation_name(filename):
                    continue
                canonical = selected.get(code, station_id or f"{code}00CHL")
                primary = _remote(
                    self.name,
                    f"{directory}{filename}",
                    filename,
                    station=canonical,
                    day=day,
                    metadata={
                        "regional_source": self.regional_source,
                        "regional_archive": "Centro Sismologico Nacional (Chile)",
                        "distribution": "CSN daily 1 Hz Hatanaka RINEX",
                        "download_protocol": "HTTPS",
                        "sampling": "01S",
                        "archive_directory": f"/{day.year}/{doy:03d}",
                        "discovery": discovery,
                        "availability": (
                            "verified_by_day_directory"
                            if discovery == "official_day_directory"
                            else "verify_on_download"
                        ),
                        "http_user_agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/150.0 Safari/537.36"
                        ),
                        # Use standalone ChromeDriver for the final file transfer.
                        # Chrome's download.default_directory is set to the GNSS Go
                        # archive folder before navigation, so no browser Downloads
                        # folder/History database import is required.
                        "http_transport": "chromedriver",
                        "csn_browser_get": "1",
                        "csn_browser_engine": "standalone_chromedriver",
                        "no_transport_retries": "1",
                        "max_parallel_downloads": "1",
                        "min_interval_seconds": "5.0",
                        # Use only the canonical HTTPS file URL and never follow
                        # a CSN redirect automatically.
                        "csn_strict_https": "1",
                    },
                )
                primary.fallback_candidates = []
                files.append(primary)

        unique: dict[str, RemoteFile] = {}
        for item in files:
            unique[str(item.url)] = item
        return list(unique.values())



class RGNAMexicoProvider(SIRGASCountryProvider):
    # RGNA files are hourly ZIP bundles; preserve every matching bundle instead
    # of collapsing a station/day to one logical candidate.
    batch_observation_passthrough = True
    name = "rgna_mx"
    country_code = "MEX"
    country_names = ("Mexico", "México")
    network_label = "Mexico INEGI RGNA"
    regional_source = "sirgas_mexico"
    portal_url = "https://www.inegi.org.mx/app/geo2/rgna/"
    coordinates_url = "https://www.inegi.org.mx/app/geo2/coordenadasGeoRGNA/"
    sftp_host = "geodesia2.inegi.org.mx"
    # INEGI currently publishes the same SFTP service by IP.  Prefer the IP to
    # bypass slow/broken DNS resolvers and keep the hostname as a fallback.
    sftp_ip = "200.23.8.97"
    sftp_user = "rgnasftp"
    sftp_password = "rgnasftp"
    sftp_port = 22
    # INEGI announced that the legacy FTP would be retired in July 2026, but it
    # remains a useful compatibility fallback on networks where outbound SSH/22
    # is blocked.  It uses the same public RGNA credentials.
    legacy_ftp_host = "geodesia.inegi.org.mx"
    legacy_ftp_ip = "200.23.8.97"
    legacy_ftp_port = 21
    legacy_ftp_user = "rgnaftp"
    legacy_ftp_password = "rgnaftp"

    # Stable official ITRF2008 epoch 2010.0 coordinates published by INEGI.
    # These are used only as a local coordinate fallback when the current RGNA
    # web application does not server-render its station rows.  Membership still
    # comes from the current SIRGAS/INEGI station catalog, so retired snapshot
    # stations are not added back to the GUI.
    _official_coordinate_snapshot_dms = {
        "CHET": ((18, 29, 42.99641), (88, 17, 57.20961), 2.955),
        "COL2": ((19, 14, 39.99474), (103, 42, 6.78208), 528.784),
        "CULC": ((24, 47, 42.30742), (107, 24, 45.34764), 36.138),
        "ICAM": ((19, 51, 12.44688), (90, 31, 38.90207), 2.587),
        "ICDV": ((23, 44, 20.90655), (99, 9, 24.75710), 314.855),
        "ICEP": ((19, 1, 58.88475), (98, 11, 15.35143), 2150.327),
        "ICHI": ((28, 38, 50.05040), (106, 3, 58.01000), 1405.717),
        "ICHS": ((16, 46, 14.32120), (93, 11, 35.30631), 635.182),
        "ICMX": ((19, 24, 20.30945), (99, 10, 15.07088), 2267.439),
        "IDGO": ((24, 4, 2.83116), (104, 36, 25.48267), 1863.116),
        "IHER": ((29, 4, 3.46672), (110, 57, 40.67655), 176.585),
        "IHGO": ((20, 7, 24.13693), (98, 44, 38.73552), 2398.349),
        "IIEG": ((20, 41, 4.21961), (103, 26, 45.74239), 1656.986),
        "IMIE": ((31, 51, 42.69707), (116, 36, 58.81264), -22.222),
        "IMIP": ((31, 44, 41.75718), (106, 26, 45.12587), 1113.428),
        "INAY": ((21, 30, 15.65925), (104, 53, 45.85918), 925.451),
        "INEG": ((21, 51, 22.15280), (102, 17, 3.13231), 1887.823),
        "IPAZ": ((24, 8, 42.97974), (110, 19, 50.67946), -14.835),
        "ISLP": ((22, 8, 39.18986), (101, 0, 55.81688), 1910.286),
        "ITLA": ((19, 18, 32.86728), (98, 12, 56.28240), 2327.685),
        "IZAC": ((22, 46, 41.31955), (102, 36, 45.80515), 2427.673),
        "MERI": ((20, 58, 48.16346), (89, 37, 13.14324), 7.863),
        "MEXI": ((32, 37, 58.77103), (115, 28, 32.53523), -22.427),
        "MTY2": ((25, 42, 55.82372), (100, 18, 46.46275), 521.741),
        "OAX2": ((17, 4, 42.02383), (96, 43, 0.26225), 1607.262),
        "TAMP": ((22, 16, 41.95540), (97, 51, 50.49882), 21.050),
        "TOL2": ((19, 17, 35.64347), (99, 38, 36.50048), 2651.730),
        "UGTO": ((21, 0, 9.75456), (101, 16, 17.99246), 2062.282),
        "UQRO": ((20, 35, 28.09773), (100, 24, 45.69377), 1817.973),
        "UVER": ((19, 9, 55.68003), (96, 6, 51.67505), 3.212),
        "VIL2": ((17, 59, 25.47838), (92, 55, 51.95484), 27.744),
    }

    @staticmethod
    def _dms_tuple_to_decimal(value: tuple[float, float, float], *, west: bool = False) -> float:
        deg, minute, second = value
        result = abs(float(deg)) + float(minute) / 60.0 + float(second) / 3600.0
        return -result if west else result

    @classmethod
    def _official_coordinate_snapshot(cls) -> dict[str, tuple[float, float, float | None]]:
        return {
            code: (
                cls._dms_tuple_to_decimal(lat_dms),
                cls._dms_tuple_to_decimal(lon_dms, west=True),
                height,
            )
            for code, (lat_dms, lon_dms, height) in cls._official_coordinate_snapshot_dms.items()
        }

    async def fetch_station_catalog(self) -> list[Station]:
        # INEGI is authoritative for Mexico.  Do not use the smaller SIRGAS
        # Mexico subset as network membership.  Start from the official INEGI
        # coordinate table snapshot and merge any newer rows that the current web
        # application happens to server-render.
        snapshot = self._official_coordinate_snapshot()
        stations: dict[str, Station] = {}
        for code, (latitude, longitude, height) in snapshot.items():
            station_id = f"{code}00MEX"
            stations[station_id] = Station(
                id=station_id,
                marker_name=code,
                latitude=latitude,
                longitude=longitude,
                height=height,
                country="MEX",
                network=["RGNA"],
                data_networks=["sirgas"],
                regional_sources=[self.regional_source],
                providers=[self.name],
                aliases=[code],
                sampling_rates=["15S", "30S"],
                rinex_versions=["2", "3"],
                metadata={
                    "catalog_source": self.coordinates_url,
                    "coordinate_source": self.coordinates_url,
                    "coordinate_reference": "ITRF2008 epoch 2010.0",
                    "membership_source": "INEGI RGNA official coordinate table",
                    "download_protocol": "SFTP / official web fallback",
                },
            )

        text = ""
        try:
            text = await self._get_text(self.coordinates_url)
        except Exception:
            pass
        dynamic = self._parse_inegi_coordinate_catalog(text)
        for station_id, station in dynamic.items():
            stations[station_id] = station

        result = list(stations.values())
        self.last_station_catalog_stats = {
            "catalog_complete": True,
            "station_count": len(result),
            "mapped_station_count": sum(
                1 for station in result
                if station.latitude is not None and station.longitude is not None
            ),
            "regional_source": self.regional_source,
            "catalog_source_used": self.coordinates_url,
            "coordinate_source_used": self.coordinates_url,
            "inegi_dynamic_station_count": len(dynamic),
            "inegi_snapshot_station_count": len(snapshot),
            "inegi_primary_catalog": True,
            "sirgas_membership_used": False,
        }
        return result

    def _parse_inegi_coordinate_catalog(self, text: str) -> dict[str, Station]:
        """Parse INEGI's RGNA coordinate table, including LOG-link station IDs.

        INEGI labels longitude as ``Longitud Oeste`` and some page generations
        publish positive west-longitude magnitudes.  Convert those values to
        negative geographic longitude before validating them.
        """
        if not text.strip():
            return {}
        row_html = re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, flags=re.I | re.S)
        header_lat = header_lon = None
        stations: dict[str, Station] = {}
        for raw_row in row_html:
            raw_cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", raw_row, flags=re.I | re.S)
            if not raw_cells:
                continue
            cells = [_strip_html(cell) for cell in raw_cells]
            normalized = [unicodedata.normalize("NFKD", cell).encode("ascii", "ignore").decode().lower() for cell in cells]
            if header_lat is None:
                lat_candidates = [i for i, value in enumerate(normalized) if "latitud" in value]
                lon_candidates = [i for i, value in enumerate(normalized) if "longitud" in value]
                if lat_candidates and lon_candidates:
                    header_lat = lat_candidates[0]
                    header_lon = lon_candidates[0]
                    continue

            joined_raw = " ".join(raw_cells)
            joined_text = " ".join(cells)
            code = ""
            # Prefer a 9-char RINEX/SiteLog identity, then a LOG filename/href,
            # then an exact 4-char table cell.
            long_match = re.search(r"(?i)(?<![A-Z0-9])([A-Z0-9]{4})00MEX(?![A-Z0-9])", joined_raw)
            if long_match:
                code = long_match.group(1).upper()
            if not code:
                log_match = re.search(r"(?i)(?:href\s*=\s*['\"][^'\"]*)?([A-Z0-9]{4})(?:00MEX)?[^/'\"]*\.log", joined_raw)
                if log_match:
                    code = log_match.group(1).upper()
            if not code:
                for cell in cells:
                    token = cell.strip().upper()
                    if (
                        re.fullmatch(r"[A-Z0-9]{4}", token)
                        and not token.isdigit()
                        and token not in _STATION_CELL_STOPWORDS
                        and token not in {"ITRF", "GNSS", "NORT", "OEST"}
                    ):
                        code = token
                        break
            if not code:
                # Last chance: a parenthesized/isolated 4-char code in the
                # station-name cell.  Avoid arbitrary receiver model tokens.
                name_cells = cells[:3]
                name_match = re.search(r"(?<![A-Z0-9])([A-Z][A-Z0-9]{3})(?![A-Z0-9])", " ".join(name_cells).upper())
                if name_match and name_match.group(1) not in _STATION_CELL_STOPWORDS:
                    code = name_match.group(1)
            if not code:
                continue

            latitude = longitude = None
            if header_lat is not None and header_lon is not None and max(header_lat, header_lon) < len(cells):
                latitude = _decimal_from_dms(cells[header_lat])
                longitude = _decimal_from_dms(cells[header_lon])
            if latitude is None or longitude is None:
                # Fall back to coordinate-looking cells, but do not let year/
                # antenna numbers become coordinates.
                lat_lon = _lat_lon_from_cells(cells)
                latitude, longitude = lat_lon
            if latitude is None or longitude is None:
                continue

            # The official column is explicitly "Longitud Oeste".  Some HTML
            # versions omit W/O from each cell and give 86..118 as a positive
            # magnitude, so enforce western sign for Mexico.
            if longitude > 0:
                longitude = -abs(longitude)
            longitude = normalize_longitude(longitude)
            if not valid_sirgas_coordinate(latitude, longitude, country="MEX", country_strict=True):
                continue

            station_id = f"{code}00MEX"
            stations[station_id] = Station(
                id=station_id,
                marker_name=code,
                latitude=float(latitude),
                longitude=float(longitude),
                country="MEX",
                network=["RGNA"],
                data_networks=["mexico", "sirgas"],
                regional_sources=[self.regional_source],
                providers=[self.name],
                aliases=[code],
                sampling_rates=["15S", "30S"],
                rinex_versions=["2", "3"],
                metadata={
                    "catalog_source": self.coordinates_url,
                    "coordinate_source": self.coordinates_url,
                    "download_protocol": "SFTP",
                    "coordinate_row": joined_text[:500],
                },
            )
        return stations


    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        """Resolve Mexico RGNA observations from the official INEGI archive.

        Do not route arbitrary RGNA stations through NOAA/NGS: NOAA only stores
        stations that belong to the U.S. NCN, so a Mexican RGNA code can produce
        a syntactically valid but nonexistent NOAA URL.  INEGI is therefore the
        source of truth here.  The default Auto/30 s request uses the official
        daily RINEX 2.11 GZ product (one file per station/day); explicit RINEX 3
        or 15 s requests use the documented 24 hourly ZIP sessions.
        """
        rinex_value = str(getattr(request.rinex, "value", request.rinex) or "auto").lower()
        sampling = _sampling_code(request.sampling)

        if rinex_value in {"auto", "2", "2.11", "rinex2"} and sampling in {"", "30S"}:
            return self._deterministic_daily_sftp_candidates(request)
        return self._deterministic_sftp_candidates(request)

    def _deterministic_daily_sftp_candidates(
        self, request: ObservationRequest
    ) -> list[RemoteFile]:
        """Build the official daily RINEX 2.11 GZ candidates.

        INEGI documents daily RINEX 2.11 files packed as GZ and station folders
        with DDMMM day subdirectories.  Both normal RINEX (``o.gz``) and compact
        Hatanaka (``d.gz``) names are tried because holdings vary by station and
        epoch; the downloader already supports one RemoteFile fallback chain.
        """
        month_codes = (
            "ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
            "JUL", "AGO", "SEP", "OCT", "NOV", "DIC",
        )
        files: list[RemoteFile] = []
        for station in request.stations or []:
            station4 = str(station)[:4].upper()
            canonical = str(station).upper() if len(str(station)) >= 9 else f"{station4}00MEX"
            for day in request.date_range.days():
                doy = datetime_to_doy(day)
                yy = day.year % 100
                day_folder = f"{day.day:02d}{month_codes[day.month - 1]}"
                names = [
                    f"{station4.lower()}{doy:03d}0.{yy:02d}o.gz",
                    f"{station4.lower()}{doy:03d}0.{yy:02d}d.gz",
                ]
                remotes: list[RemoteFile] = []
                for filename in names:
                    path_candidates = [
                        f"/home/rgna/{station4}/{day_folder}/{filename}",
                        f"/{station4}/{day_folder}/{filename}",
                        f"/RGNA/{station4}/{day_folder}/{filename}",
                        f"/rgna/{station4}/{day_folder}/{filename}",
                        f"/home/rgna/{station4}/{filename}",
                        f"/{station4}/{filename}",
                    ]
                    item = _remote(
                        self.name,
                        f"sftp://{self.sftp_host}:{self.sftp_port}{path_candidates[0]}",
                        filename,
                        station=canonical,
                        day=day,
                        metadata={
                            "regional_source": self.regional_source,
                            "regional_archive": "INEGI RGNA official daily archive",
                            "download_protocol": "SFTP",
                            "sftp_username": self.sftp_user,
                            "sftp_password": self.sftp_password,
                            "sftp_port": str(self.sftp_port),
                            "sftp_host_candidates": f"{self.sftp_host}|{self.sftp_ip}",
                            "sftp_path_candidates": "|".join(path_candidates),
                            "rinex_version": "2.11",
                            "sampling": "30S",
                            "archive_day_folder": day_folder,
                            "discovery": "deterministic_official_daily_naming",
                        },
                    )
                    # If SSH/22 is filtered by the user's ISP/firewall, try
                    # INEGI's legacy authenticated FTP transport on port 21 with
                    # the same station/day path variants.  The official site says
                    # this service is being retired, so it is fallback-only.
                    ftp_item = _remote(
                        self.name,
                        f"ftp://{self.legacy_ftp_host}{path_candidates[0]}",
                        filename,
                        station=canonical,
                        day=day,
                        metadata={
                            "regional_source": self.regional_source,
                            "regional_archive": "INEGI RGNA legacy FTP fallback",
                            "download_protocol": "FTP",
                            "ftp_username": self.legacy_ftp_user,
                            "ftp_password": self.legacy_ftp_password,
                            "ftp_port": str(self.legacy_ftp_port),
                            "ftp_host_candidates": f"{self.legacy_ftp_host}|{self.legacy_ftp_ip}",
                            "ftp_path_candidates": "|".join(path_candidates),
                            "rinex_version": "2.11",
                            "sampling": "30S",
                            "archive_day_folder": day_folder,
                            "discovery": "legacy_ftp_transport_fallback",
                            "curl_fallback": "1",
                        },
                    )
                    item.fallback_candidates = [ftp_item]
                    remotes.append(item)
                # normal-RINEX SFTP -> same file over legacy FTP -> compact
                # Hatanaka SFTP -> same compact file over legacy FTP.
                first, second = remotes
                first_chain = list(first.fallback_candidates)
                second_chain = list(second.fallback_candidates)
                first.fallback_candidates = first_chain + [second] + second_chain
                second.fallback_candidates = second_chain
                files.append(first)
        return files

    def _deterministic_sftp_candidates(
        self, request: ObservationRequest
    ) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        requested_rinex = str(getattr(request.rinex, "value", request.rinex) or "auto").lower()
        if requested_rinex in {"auto", "3", "3.04", "rinex3"}:
            versions: list[tuple[str, str]] = [("3.04", "_304")]
        elif requested_rinex in {"2", "2.11", "rinex2"}:
            versions = [("2.11", "")]
        else:
            return []

        month_codes = (
            "ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
            "JUL", "AGO", "SEP", "OCT", "NOV", "DIC",
        )

        for station in request.stations or []:
            station4 = str(station)[:4].upper()
            canonical = (
                str(station).upper() if len(str(station)) >= 9 else f"{station4}00MEX"
            )
            for day in request.date_range.days():
                doy = datetime_to_doy(day)
                day_folder = f"{day.day:02d}{month_codes[day.month - 1]}"
                for session in "abcdefghijklmnopqrstuvwx":
                    for version, suffix in versions:
                        filename = f"{station4}{doy:03d}{session}{suffix}.zip"
                        paths = [
                            f"/home/rgna/{station4}/{day_folder}/{filename}",
                            f"/{station4}/{day_folder}/{filename}",
                            f"/RGNA/{station4}/{day_folder}/{filename}",
                            f"/rgna/{station4}/{day_folder}/{filename}",
                            f"/home/rgna/{station4}/{filename}",
                            f"/{station4}/{filename}",
                        ]
                        item = _remote(
                            self.name,
                            f"sftp://{self.sftp_host}:{self.sftp_port}{paths[0]}",
                            filename,
                            station=canonical,
                            day=day,
                            metadata={
                                "regional_source": self.regional_source,
                                "regional_archive": "INEGI RGNA native hourly archive",
                                "sftp_username": self.sftp_user,
                                "sftp_password": self.sftp_password,
                                "sftp_port": str(self.sftp_port),
                                "sftp_host_candidates": f"{self.sftp_host}|{self.sftp_ip}",
                                "sftp_path_candidates": "|".join(paths),
                                "curl_fallback": "1",
                                "rinex_version": version,
                                "sampling": "15S",
                                "session": session,
                                "archive_day_folder": day_folder,
                                "discovery": "deterministic_official_naming",
                            },
                        )
                        ftp_item = _remote(
                            self.name,
                            f"ftp://{self.legacy_ftp_host}{paths[0]}",
                            filename,
                            station=canonical,
                            day=day,
                            metadata={
                                "regional_source": self.regional_source,
                                "regional_archive": "INEGI RGNA legacy FTP fallback",
                                "download_protocol": "FTP",
                                "ftp_username": self.legacy_ftp_user,
                                "ftp_password": self.legacy_ftp_password,
                                "ftp_port": str(self.legacy_ftp_port),
                                "ftp_host_candidates": f"{self.legacy_ftp_host}|{self.legacy_ftp_ip}",
                                "ftp_path_candidates": "|".join(paths),
                                "rinex_version": version,
                                "sampling": "15S",
                                "session": session,
                                "archive_day_folder": day_folder,
                                "discovery": "legacy_ftp_transport_fallback",
                            "curl_fallback": "1",
                            },
                        )
                        item.fallback_candidates = [ftp_item]
                        files.append(item)
        return files

    def _search_sftp_sync(self, request: ObservationRequest) -> list[RemoteFile]:
        try:
            import paramiko
        except ImportError:
            raise

        last_error: Exception | None = None
        for host in (self.sftp_ip, self.sftp_host):
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(
                    host,
                    port=self.sftp_port,
                    username=self.sftp_user,
                    password=self.sftp_password,
                    timeout=4,
                    banner_timeout=4,
                    auth_timeout=4,
                )
                sftp = client.open_sftp()
                try:
                    sftp.get_channel().settimeout(2.5)
                except Exception:
                    pass

                deadline = time.monotonic() + 13.0
                result: list[RemoteFile] = []
                # Once one station reveals the server's chroot/root layout,
                # reuse that prefix for the rest of the selected stations.  This
                # avoids dozens of failed stat() calls on multi-station plans.
                root_layout: tuple[str, bool] | None = None
                for station in request.stations or []:
                    if time.monotonic() >= deadline:
                        break
                    station4 = station[:4].upper()
                    root: str | None = None
                    if root_layout is not None:
                        prefix, lower_case = root_layout
                        root = prefix + (station4.lower() if lower_case else station4)
                    else:
                        root = self._resolve_station_root(sftp, station4, deadline=deadline)
                        if root is not None:
                            if root.endswith(station4):
                                root_layout = (root[:-len(station4)], False)
                            elif root.endswith(station4.lower()):
                                root_layout = (root[:-len(station4)], True)
                    if root is None:
                        continue
                    for day in request.date_range.days():
                        if time.monotonic() >= deadline:
                            break
                        for path, filename in self._walk_matching_day(
                            sftp, root, station4, day, deadline=deadline
                        ):
                            if not self._mexico_file_for_day(
                                filename, station4=station4, day=day, request=request
                            ):
                                continue
                            result.append(
                                _remote(
                                    self.name,
                                    f"sftp://{host}:{self.sftp_port}{path}",
                                    filename,
                                    station=(
                                        station.upper()
                                        if len(str(station)) >= 9
                                        else f"{station4}00MEX"
                                    ),
                                    day=day,
                                    metadata={
                                        "regional_source": self.regional_source,
                                        "regional_archive": "INEGI RGNA",
                                        "sftp_username": self.sftp_user,
                                        "sftp_password": self.sftp_password,
                                        "sftp_port": str(self.sftp_port),
                                        "sftp_host_used": host,
                                    },
                                )
                            )
                return list({str(item.url): item for item in result}.values())
            except Exception as exc:
                last_error = exc
            finally:
                try:
                    client.close()
                except Exception:
                    pass

        if last_error is not None:
            raise ProviderError(f"Could not connect/query INEGI RGNA SFTP: {last_error}") from last_error
        return []

    @staticmethod
    def _mexico_file_for_day(
        filename: str,
        *,
        station4: str,
        day: date,
        request: ObservationRequest,
    ) -> bool:
        name = filename.rsplit("/", 1)[-1]
        low = name.lower()
        code = station4.lower()
        doy = datetime_to_doy(day)
        if not low.startswith(code):
            return False

        # Current RGNA server uses hourly ZIP wrappers (e.g. INEG152a.zip and
        # INEG152a_304.zip).  The generic RINEX matcher intentionally does not
        # classify these wrappers, so handle them explicitly.
        zip_match = re.match(
            rf"^{re.escape(code)}{doy:03d}[a-x0](?:_304)?\.zip$", low
        )
        if zip_match:
            requested = str(request.rinex)
            is_v3 = low.endswith("_304.zip")
            if requested == "2" and is_v3:
                return False
            if requested in {"3", "4"} and not is_v3:
                return False
            return True

        if not _matches_rinex(name, request):
            return False
        info = parse_rinex_filename(name)
        if info.file_type not in {None, "observation"}:
            return False
        if info.year and info.year != day.year:
            return False
        if info.doy and info.doy != doy:
            return False
        return True

    @staticmethod
    def _resolve_station_root(
        sftp,
        station4: str,
        *,
        deadline: float | None = None,
    ) -> str | None:
        # SFTP servers frequently chroot the account, so a station directory
        # may appear relative to the login home even when the legacy FTP exposed
        # a longer absolute path.  Try the cheap/common forms first.
        for path in (
            f"/home/rgna/{station4}",
            f"/home/rgna/{station4.lower()}",
            station4,
            station4.lower(),
            f"RGNA/{station4}",
            f"RGNA/{station4.lower()}",
            f"rgna/{station4}",
            f"rgna/{station4.lower()}",
            f"/{station4}",
            f"/{station4.lower()}",
            f"/RGNA/{station4}",
            f"/RGNA/{station4.lower()}",
            f"/rgna/{station4}",
            f"/rgna/{station4.lower()}",
            f"/home/rgnasftp/{station4}",
            f"/home/rgnasftp/{station4.lower()}",
        ):
            if deadline is not None and time.monotonic() >= deadline:
                return None
            try:
                if stat.S_ISDIR(sftp.stat(path).st_mode):
                    return path
            except (OSError, TimeoutError):
                continue
        return None

    @staticmethod
    def _walk_matching_day(
        sftp,
        root: str,
        station4: str,
        day: date,
        *,
        deadline: float | None = None,
    ):
        target_doy = datetime_to_doy(day)

        # Prefer known date-oriented layouts.  This avoids recursively walking
        # thousands of entries on the public server during PLAN.
        month_codes = (
            "ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
            "JUL", "AGO", "SEP", "OCT", "NOV", "DIC",
        )
        ddmmm = f"{day.day:02d}{month_codes[day.month - 1]}"
        candidates = [
            f"{root}/{ddmmm}",
            f"{root}/{day.year}/{target_doy:03d}",
            f"{root}/{day.year}/{day.month:02d}/{day.day:02d}",
            f"{root}/{target_doy:03d}",
            f"{root}/{day.year}",
            root,
        ]
        seen: set[str] = set()
        for directory in candidates:
            if directory in seen:
                continue
            seen.add(directory)
            if deadline is not None and time.monotonic() >= deadline:
                return
            try:
                entries = sftp.listdir_attr(directory)
            except (OSError, TimeoutError):
                continue

            yielded = False
            for entry in entries:
                if deadline is not None and time.monotonic() >= deadline:
                    return
                path = posixpath.join(directory, entry.filename)
                if stat.S_ISDIR(entry.st_mode):
                    # Only descend one date-relevant level from a year/root
                    # listing; never perform an unbounded recursive walk.
                    token = entry.filename.strip().lower()
                    relevant = token in {
                        f"{target_doy:03d}",
                        f"{day.month:02d}",
                        str(day.year),
                    }
                    if not relevant:
                        continue
                    try:
                        children = sftp.listdir_attr(path)
                    except (OSError, TimeoutError):
                        continue
                    for child in children[:500]:
                        if stat.S_ISDIR(child.st_mode):
                            continue
                        yield posixpath.join(path, child.filename), child.filename
                        yielded = True
                    continue
                yield path, entry.filename
                yielded = True
            if yielded:
                return



def _regional_archive_file_matches(
    filename: str,
    *,
    station4: str,
    day: date,
    request: ObservationRequest,
) -> bool:
    """Return True when an archive filename is the requested station/day RINEX obs.

    National CORS archives in Latin America mix RINEX 2 short names, RINEX 3 long
    names, Hatanaka compression and ZIP wrappers.  Accept the standard parser
    first, then use conservative filename/date fallbacks for older archives.
    """
    name = filename.rsplit("/", 1)[-1]
    if not name:
        return False

    upper = name.upper()
    code = station4[:4].upper()
    if not upper.startswith(code):
        return False

    # Respect an explicit RINEX-version request, but allow the broad set when
    # the GUI is on "auto".
    if not _matches_rinex(name, request):
        return False

    info = parse_rinex_filename(name)
    if info.file_type not in {None, "observation"}:
        return False
    if info.station and not str(info.station).upper().startswith(code):
        return False
    if info.year and info.year != day.year:
        return False
    if info.doy and info.doy != datetime_to_doy(day):
        return False
    if info.year or info.doy:
        return True

    doy = datetime_to_doy(day)
    yy = day.year % 100
    low = name.lower()

    # Legacy RINEX 2:
    #   ssssDDD0.YYo
    #   ssssDDDa.YYo.Z
    #   ssssDDD0.YYd.gz
    #   ... optionally wrapped in .zip
    legacy = re.match(
        rf"^{re.escape(code.lower())}(?P<doy>\d{{3}})[0-9a-x]?"
        rf"\.(?P<yy>\d{{2}})[do](?:\.(?:z|gz|zip))?$",
        low,
    )
    if legacy:
        return int(legacy.group("doy")) == doy and int(legacy.group("yy")) == yy

    # Some national archives add extra tokens before the RINEX2 extension.
    legacy_loose = re.match(
        rf"^{re.escape(code.lower())}(?P<doy>\d{{3}}).*"
        rf"\.(?P<yy>\d{{2}})[do](?:\.(?:z|gz|zip))?$",
        low,
    )
    if legacy_loose:
        return int(legacy_loose.group("doy")) == doy and int(legacy_loose.group("yy")) == yy

    # Non-standard ZIP wrappers occasionally keep YYYY/DOY in the filename.
    if low.endswith(".zip"):
        if f"{day.year}{doy:03d}" in low or f"{day.year}_{doy:03d}" in low:
            return True

    return False

def _archive_dir_is_relevant(
    name: str,
    *,
    selected4: set[str],
    years: set[int],
    doys: set[int],
) -> bool:
    """Prune only directories that are unambiguously unrelated.

    Do *not* treat every 4-character directory as a station code: national
    servers commonly use generic folders such as DATA, OBS, RINE, etc.
    """
    token = name.strip("/").rsplit("/", 1)[-1]
    upper = token.upper()

    if token in {".", ".."}:
        return False

    # Year branches are safe to prune.
    if re.fullmatch(r"20\d{2}", token):
        return int(token) in years

    # Three-digit numeric branches are normally DOY folders.
    if re.fullmatch(r"\d{3}", token):
        value = int(token)
        if 1 <= value <= 366:
            return value in doys

    # Only a *9-character GNSS marker* is unambiguously a station directory.
    # A generic four-letter directory must remain traversable.
    if re.fullmatch(r"[A-Z0-9]{4}00[A-Z0-9]{3}", upper):
        return upper[:4] in selected4

    # Common station folders may be exactly four chars, but pruning them is unsafe
    # because DATA/RINX/OBSX and similar generic folders look identical. Keep them.
    return True

def _ftp_entries(ftp: FTP, directory: str) -> list[tuple[str, bool]]:
    """List one FTP directory as (name, is_dir), supporting old FTP servers."""
    result: list[tuple[str, bool]] = []
    try:
        for name, facts in ftp.mlsd(directory):
            if name in {'.', '..'}:
                continue
            result.append((name, str(facts.get('type', '')).lower() == 'dir'))
        return result
    except Exception:
        pass

    try:
        raw = ftp.nlst(directory)
    except Exception:
        return []
    current = None
    try:
        current = ftp.pwd()
    except Exception:
        pass
    for item in raw:
        name = item.rstrip('/').rsplit('/', 1)[-1]
        if not name or name in {'.', '..'}:
            continue
        path = posixpath.join(directory.rstrip('/') or '/', name)
        is_dir = False
        try:
            ftp.cwd(path)
            is_dir = True
        except Exception:
            is_dir = False
        finally:
            if current:
                try:
                    ftp.cwd(current)
                except Exception:
                    pass
        result.append((name, is_dir))
    return result


def _bounded_ftp_observation_search(
    ftp: FTP,
    request: ObservationRequest,
    *,
    root: str = '/',
    max_depth: int = 9,
    max_entries: int = 60000,
) -> list[tuple[str, str, date]]:
    station_map = {
        str(station)[:4].upper(): str(station).upper()
        for station in request.stations or []
        if str(station)[:4]
    }
    if not station_map:
        return []
    days = list(request.date_range.days())
    years = {day.year for day in days}
    doys = {datetime_to_doy(day) for day in days}
    selected4 = set(station_map)

    # Prefer likely station/year roots first.  If none exist, fall back to a
    # bounded walk from the login root.  This is intentionally layout-agnostic:
    # both REGNA-ROU and other legacy CORS servers have changed directory layout.
    candidates = []
    for code in sorted(selected4):
        candidates.extend([
            f'/{code}', f'/{code.lower()}',
            f'/RINEX/{code}', f'/rinex/{code.lower()}',
            f'/DATA/{code}', f'/data/{code.lower()}',
            f'/OBS/{code}', f'/obs/{code.lower()}',
        ])
    for year in sorted(years):
        candidates.extend([
            f'/{year}', f'/RINEX/{year}', f'/rinex/{year}',
            f'/DATA/{year}', f'/data/{year}', f'/OBS/{year}', f'/obs/{year}',
        ])
    candidates.append(root or '/')

    valid_roots: list[str] = []
    original = None
    try:
        original = ftp.pwd()
    except Exception:
        pass
    for candidate in candidates:
        try:
            ftp.cwd(candidate)
            if candidate not in valid_roots:
                valid_roots.append(candidate)
        except Exception:
            continue
        finally:
            if original:
                try:
                    ftp.cwd(original)
                except Exception:
                    pass
    if not valid_roots:
        valid_roots = [root or '/']

    found: dict[str, tuple[str, str, date]] = {}
    scanned = 0
    seen_dirs: set[str] = set()
    stack = [(path, 0) for path in reversed(valid_roots)]
    while stack and scanned < max_entries:
        directory, depth = stack.pop()
        normalized = posixpath.normpath(directory or '/')
        if normalized in seen_dirs:
            continue
        seen_dirs.add(normalized)
        for name, is_dir in _ftp_entries(ftp, normalized):
            scanned += 1
            if scanned >= max_entries:
                break
            path = posixpath.join(normalized.rstrip('/') or '/', name)
            if is_dir:
                if depth < max_depth and _archive_dir_is_relevant(
                    name, selected4=selected4, years=years, doys=doys
                ):
                    stack.append((path, depth + 1))
                continue
            for day in days:
                for code, canonical in station_map.items():
                    if _regional_archive_file_matches(
                        name, station4=code, day=day, request=request
                    ):
                        found[path] = (path, canonical, day)
                        break
                else:
                    continue
                break
    return list(found.values())


def _bounded_sftp_observation_search(
    sftp,
    request: ObservationRequest,
    *,
    root: str = '/',
    max_depth: int = 9,
    max_entries: int = 60000,
) -> list[tuple[str, str, date]]:
    station_map = {
        str(station)[:4].upper(): str(station).upper()
        for station in request.stations or []
        if str(station)[:4]
    }
    if not station_map:
        return []
    days = list(request.date_range.days())
    years = {day.year for day in days}
    doys = {datetime_to_doy(day) for day in days}
    selected4 = set(station_map)

    candidate_roots: list[str] = []
    for code in sorted(selected4):
        candidate_roots.extend([
            f'/{code}', f'/{code.lower()}',
            f'/RINEX/{code}', f'/rinex/{code.lower()}',
            f'/DATA/{code}', f'/data/{code.lower()}',
            f'/OBS/{code}', f'/obs/{code.lower()}',
        ])
    for year in sorted(years):
        candidate_roots.extend([
            f'/{year}', f'/RINEX/{year}', f'/rinex/{year}',
            f'/DATA/{year}', f'/data/{year}', f'/OBS/{year}', f'/obs/{year}',
        ])
    candidate_roots.append(root or '/')

    roots: list[str] = []
    for candidate in candidate_roots:
        try:
            if stat.S_ISDIR(sftp.stat(candidate).st_mode) and candidate not in roots:
                roots.append(candidate)
        except OSError:
            continue
    if not roots:
        roots = [root or '/']

    found: dict[str, tuple[str, str, date]] = {}
    scanned = 0
    seen_dirs: set[str] = set()
    stack = [(path, 0) for path in reversed(roots)]
    while stack and scanned < max_entries:
        directory, depth = stack.pop()
        normalized = posixpath.normpath(directory or '/')
        if normalized in seen_dirs:
            continue
        seen_dirs.add(normalized)
        try:
            entries = sftp.listdir_attr(normalized)
        except OSError:
            continue
        for entry in entries:
            scanned += 1
            if scanned >= max_entries:
                break
            path = posixpath.join(normalized.rstrip('/') or '/', entry.filename)
            if stat.S_ISDIR(entry.st_mode):
                if depth < max_depth and _archive_dir_is_relevant(
                    entry.filename, selected4=selected4, years=years, doys=doys
                ):
                    stack.append((path, depth + 1))
                continue
            for day in days:
                for code, canonical in station_map.items():
                    if _regional_archive_file_matches(
                        entry.filename, station4=code, day=day, request=request
                    ):
                        found[path] = (path, canonical, day)
                        break
                else:
                    continue
                break
    return list(found.values())


class BoliviaSIRGASProvider(SIRGASCountryProvider):
    name = "sirgas_bo"; country_code = "BOL"; country_names = ("Bolivia",); network_label = "Bolivia IGM"; regional_source = "sirgas_bolivia"

class ColombiaSIRGASProvider(SIRGASCountryProvider):
    name = "sirgas_co"
    country_code = "COL"
    country_names = ("Colombia",)
    network_label = "Colombia IGAC"
    regional_source = "sirgas_colombia"
    portal_url = "https://redgeodesica.igac.gov.co/red_activa.html"


class EcuadorSIRGASProvider(SIRGASCountryProvider):
    name = "sirgas_ec"; country_code = "ECU"; country_names = ("Ecuador",); network_label = "Ecuador IGM"; regional_source = "sirgas_ecuador"; portal_url = "https://www.geoportaligm.gob.ec/geodesia/"

class PeruSIRGASProvider(SIRGASCountryProvider):
    name = "sirgas_pe"; country_code = "PER"; country_names = ("Peru", "Perú"); network_label = "Peru IGN"; regional_source = "sirgas_peru"

class UruguaySIRGASProvider(SIRGASCountryProvider):
    batch_observation_passthrough = True
    name = "sirgas_uy"
    country_code = "URY"
    country_names = ("Uruguay",)
    network_label = "Uruguay IGM REGNA-ROU"
    regional_source = "sirgas_uruguay"
    portal_url = "https://igm.gub.uy/2016/05/20/servicios-regna-rou/"

    # Current-year archive (anonymous FTP): /hatanaka/YYYY/DOY
    # Historical archive (public SFTP):
    #   30 s -> /sftpserver/YYYY/MM/DD/STATION
    #    1 s -> /sftpserver/YYYY/MM/DD/0/STATION
    current_ftp_host = "pp.igm.gub.uy"
    current_ftp_root = "/hatanaka"
    current_catalog_root = "/regna"
    historical_sftp_host = "sftp.igm.gub.uy"
    historical_sftp_port = 2222
    historical_sftp_user = "regna"
    historical_sftp_password = "historico"
    historical_sftp_root = "/sftpserver"

    @staticmethod
    def _uy_station_id_from_filename(filename: str) -> str:
        name = filename.rsplit("/", 1)[-1]
        info = parse_rinex_filename(name)
        raw = str(info.station or "").upper()
        if len(raw) >= 9:
            return raw[:9]
        code = (raw or name[:4]).upper()[:4]
        if not re.fullmatch(r"[A-Z0-9]{4}", code):
            return ""
        return f"{code}00URY"

    def _current_archive_station_codes_sync(self) -> tuple[dict[str, str], str]:
        """Discover REGNA-ROU membership from the official current-year FTP.

        IGM's own FTP guide shows ``/regna`` as the public root and the station
        codes (UYAR, UYDU, UYLA, ...) immediately below it.  Earlier GNSS Go builds
        builds incorrectly probed a non-existent ``/hatanaka/YYYY/DOY`` tree,
        which made valid current stations disappear from the catalogue.
        """
        codes: dict[str, str] = {}
        source = f"ftp://{self.current_ftp_host}{self.current_catalog_root}"
        with FTP(self.current_ftp_host, timeout=10) as ftp:
            ftp.login()
            try:
                names = ftp.nlst(self.current_catalog_root)
            except Exception:
                ftp.cwd(self.current_catalog_root)
                names = ftp.nlst()
                ftp.cwd("/")
            for raw_name in names:
                code = str(raw_name).rstrip("/").rsplit("/", 1)[-1].upper()[:4]
                if re.fullmatch(r"UY[A-Z0-9]{2}", code):
                    codes.setdefault(code, f"{code}00URY")
        return codes, source

    async def fetch_station_catalog(self) -> list[Station]:
        # REGNA-ROU's own IGM archive is authoritative for membership.  SIRGAS
        # is used only to fill plotting coordinates for matching station codes.
        try:
            codes, source = await asyncio.to_thread(self._current_archive_station_codes_sync)
        except Exception:
            codes, source = {}, ""
        if not codes:
            stations = await super().fetch_station_catalog()
            stats = dict(getattr(self, "last_station_catalog_stats", {}) or {})
            stats["igm_catalog_fallback"] = True
            self.last_station_catalog_stats = stats
            return stations

        stations = [
            Station(
                id=station_id,
                marker_name=code,
                country="URY",
                network=["REGNA-ROU"],
                data_networks=["sirgas"],
                regional_sources=[self.regional_source],
                providers=[self.name],
                aliases=[code],
                sampling_rates=["01S", "15S", "30S"],
                rinex_versions=["2", "3"],
                metadata={
                    "catalog_source": source,
                    "membership_source": "IGM REGNA-ROU official FTP archive",
                },
            )
            for code, station_id in sorted(codes.items())
        ]
        coordinate_source = ""
        try:
            weekly, coordinate_source = await _sirgas_weekly_coordinates_for(self)
            _apply_coordinate_lookup(stations, weekly, source=coordinate_source)
        except Exception:
            pass
        try:
            mapped, log_source, _ = await self._enrich_from_sirgas_site_logs(stations)
            if mapped:
                coordinate_source = log_source
        except Exception:
            pass
        self.last_station_catalog_stats = {
            "catalog_complete": True,
            "station_count": len(stations),
            "mapped_station_count": sum(
                1 for station in stations
                if station.latitude is not None and station.longitude is not None
            ),
            "regional_source": self.regional_source,
            "catalog_source_used": source,
            "coordinate_source_used": coordinate_source,
            "igm_primary_catalog": True,
            "sirgas_coordinate_fallback_only": True,
        }
        return stations

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        station_map = self._requested_station_map(request)
        if not station_map:
            return []

        files: list[RemoteFile] = []
        # IGM current-year daily files are published in the day-scoped Hatanaka
        # archive: /hatanaka/YYYY/DOY.  List that single directory once and keep
        # only files for the requested stations.  This supports both the classic
        # short RINEX-2/Hatanaka name (uyar0180.26d.gz) and modern long names such
        # as UYFD00XXX_S_20260180000_01D_15S_MO.crx.gz.
        if any(day.year == date.today().year for day in request.date_range.days()):
            try:
                current = _run_daemon_bounded(
                    lambda: self._search_current_hatanaka_sync(request),
                    timeout=8.0,
                    timeout_message="Uruguay IGM current Hatanaka FTP discovery timed out.",
                )
                files.extend(current)
            except Exception:
                # Keep PLAN useful even if the FTP directory listing is temporarily
                # unavailable; the deterministic short-name candidate below is a
                # valid official layout and will be verified by the downloader.
                pass

        deterministic = self._deterministic_archive_candidates(request)
        found_pairs = {
            (item.date, (item.station or "")[:4].upper())
            for item in files
            if item.date is not None
        }
        current_year = date.today().year
        for item in deterministic:
            pair = (item.date, (item.station or "")[:4].upper())
            if pair not in found_pairs:
                files.append(item)

        unique: dict[str, RemoteFile] = {}
        for item in files:
            unique[str(item.url)] = item
        return list(unique.values())

    def _deterministic_archive_candidates(
        self, request: ObservationRequest
    ) -> list[RemoteFile]:
        station_map = self._requested_station_map(request)
        if not station_map:
            return []

        sampling = _sampling_code(request.sampling)
        files: list[RemoteFile] = []
        current_year = date.today().year
        for day in request.date_range.days():
            doy = datetime_to_doy(day)
            yy = day.year % 100
            for code, canonical in station_map.items():
                code_upper = code.upper()
                code_lower = code.lower()

                if day.year == current_year:
                    # The current public archive keeps daily Hatanaka files in
                    # /hatanaka/YYYY/DOY.  The short RINEX-2 name is deterministic
                    # and is present alongside long-name RINEX-3 files at many
                    # stations, so use it as a safe fallback when directory listing
                    # is unavailable.
                    filename = f"{code_lower}{doy:03d}0.{yy:02d}d.gz"
                    directory = f"{self.current_ftp_root}/{day.year}/{doy:03d}"
                    files.append(
                        _remote(
                            self.name,
                            f"ftp://{self.current_ftp_host}{directory}/{filename}",
                            filename,
                            station=canonical,
                            day=day,
                            metadata={
                                "regional_source": self.regional_source,
                                "regional_archive": "IGM REGNA-ROU current Hatanaka FTP",
                                "download_protocol": "FTP",
                                "archive_directory": directory,
                                "curl_fallback": "1",
                                "sampling": "15S",
                                "discovery": "deterministic_current_hatanaka_shortname",
                            },
                        )
                    )
                    continue

                filename = f"{code_lower}{doy:03d}0.rnx.zip"
                date_root = (
                    f"{self.historical_sftp_root}/{day.year}/"
                    f"{day.month:02d}/{day.day:02d}"
                )
                preferred_root = (
                    f"{date_root}/0/{code_upper}" if sampling == "01S"
                    else f"{date_root}/{code_upper}"
                )
                alternate_root = (
                    f"{date_root}/{code_upper}" if sampling == "01S"
                    else f"{date_root}/0/{code_upper}"
                )
                full_paths = [
                    f"{preferred_root}/{filename}",
                    f"{alternate_root}/{filename}",
                ]
                paths: list[str] = []
                for path in full_paths:
                    for candidate in (
                        path,
                        path[len(self.historical_sftp_root):]
                        if path.startswith(self.historical_sftp_root + "/") else path,
                        path.lstrip("/"),
                    ):
                        if candidate and candidate not in paths:
                            paths.append(candidate)

                files.append(
                    _remote(
                        self.name,
                        (f"sftp://{self.historical_sftp_host}:"
                         f"{self.historical_sftp_port}{full_paths[0]}"),
                        filename,
                        station=canonical,
                        day=day,
                        metadata={
                            "regional_source": self.regional_source,
                            "regional_archive": "IGM REGNA-ROU historical SFTP",
                            "download_protocol": "SFTP",
                            "sftp_username": self.historical_sftp_user,
                            "sftp_password": self.historical_sftp_password,
                            "sftp_port": str(self.historical_sftp_port),
                            "sftp_path_candidates": "|".join(paths),
                            "curl_fallback": "1",
                            "sampling": "01S" if sampling == "01S" else "30S",
                            "discovery": "deterministic_official_layout",
                        },
                    )
                )

        return files

    def _search_current_hatanaka_sync(self, request: ObservationRequest) -> list[RemoteFile]:
        station_map = self._requested_station_map(request)
        if not station_map:
            return []
        current_year = date.today().year
        days = [day for day in request.date_range.days() if day.year == current_year]
        if not days:
            return []

        results: list[RemoteFile] = []
        with FTP(self.current_ftp_host, timeout=6) as ftp:
            ftp.login()
            for day in days:
                doy = datetime_to_doy(day)
                directory = f"{self.current_ftp_root}/{day.year}/{doy:03d}"
                try:
                    names = ftp.nlst(directory)
                except Exception:
                    ftp.cwd(directory)
                    names = ftp.nlst()
                    try:
                        ftp.cwd("/")
                    except Exception:
                        pass

                for raw_name in names[:3000]:
                    filename = str(raw_name).rstrip("/").rsplit("/", 1)[-1]
                    for code, canonical in station_map.items():
                        if not self._uruguay_file_for_day(
                            filename, station4=code, day=day, request=request
                        ):
                            continue
                        remote_path = (
                            str(raw_name)
                            if str(raw_name).startswith("/")
                            else f"{directory}/{filename}"
                        )
                        # Long-name files explicitly encode their sampling (e.g.
                        # 15S); short RINEX-2 Hatanaka names do not, but the current
                        # archive is distributed at 15 s in the observed 2026 tree.
                        sampling_match = re.search(r"_(\d{2}S)_M[ON]", filename.upper())
                        sampling_meta = sampling_match.group(1) if sampling_match else "15S"
                        results.append(
                            _remote(
                                self.name,
                                f"ftp://{self.current_ftp_host}{remote_path}",
                                filename,
                                station=canonical,
                                day=day,
                                metadata={
                                    "regional_source": self.regional_source,
                                    "regional_archive": "IGM REGNA-ROU current Hatanaka FTP",
                                    "download_protocol": "FTP",
                                    "archive_directory": directory,
                                    "curl_fallback": "1",
                                    "sampling": sampling_meta,
                                    "discovery": "official_hatanaka_day_listing",
                                },
                            )
                        )
        return results

    def _search_current_ftp_sync(self, request: ObservationRequest) -> list[RemoteFile]:
        station_map = self._requested_station_map(request)
        if not station_map:
            return []
        current_year = date.today().year
        days = [day for day in request.date_range.days() if day.year == current_year]
        if not days:
            return []

        results: list[RemoteFile] = []
        with FTP(self.current_ftp_host, timeout=6) as ftp:
            ftp.login()
            for code, canonical in station_map.items():
                root = self._find_current_ftp_station_root(ftp, code)
                if root is None:
                    continue
                for day in days:
                    for remote_path, filename in self._walk_current_ftp_station_day(
                        ftp, root, code, day, request
                    ):
                        results.append(
                            _remote(
                                self.name,
                                f"ftp://{self.current_ftp_host}{remote_path}",
                                filename,
                                station=canonical,
                                day=day,
                                metadata={
                                    "regional_source": self.regional_source,
                                    "regional_archive": "IGM REGNA-ROU current-year FTP",
                                    "download_protocol": "FTP",
                                    "archive_directory": posixpath.dirname(remote_path),
                                    "curl_fallback": "1",
                                    "discovery": "official_ftp_station_tree",
                                },
                            )
                        )
        return results

    @staticmethod
    def _find_current_ftp_station_root(ftp: FTP, code: str) -> str | None:
        # The official IGM FTP access guide shows the public tree as
        # /regna/UYxx (not /UYxx at the FTP root).  Retain the root variants for
        # older/chrooted servers, but try the documented layout first.
        for candidate in (
            f"/regna/{code.upper()}",
            f"/regna/{code.lower()}",
            f"/REGNA/{code.upper()}",
            f"/{code.upper()}",
            f"/{code.lower()}",
        ):
            try:
                ftp.cwd(candidate)
                ftp.cwd("/")
                return candidate
            except (OSError, error_perm):
                try:
                    ftp.cwd("/")
                except Exception:
                    pass
        return None

    def _walk_current_ftp_station_day(
        self,
        ftp: FTP,
        root: str,
        code: str,
        day: date,
        request: ObservationRequest,
    ):
        """Walk one official IGM station subtree and yield matching day files.

        The public FTP exposes station folders under ``/regna``.  Depending on
        the FTP daemon, entries below a station can be reported as symlinks or
        with incomplete MLSD facts.  Therefore directory-ness is verified with
        ``CWD`` instead of trusting only ``facts['type']``.  The tree is shallow,
        but allow a few levels because daily/hourly/current receiver layouts have
        changed over time.
        """
        queue_: list[tuple[str, int]] = [(root, 0)]
        seen: set[str] = set()
        visited = 0
        max_depth = 6
        max_dirs = 240

        while queue_ and visited < max_dirs:
            directory, depth = queue_.pop(0)
            if directory in seen:
                continue
            seen.add(directory)
            visited += 1

            entries: list[tuple[str, dict[str, str]]] = []
            try:
                entries = [(str(n), dict(f)) for n, f in ftp.mlsd(directory)]
            except Exception:
                try:
                    raw_names = ftp.nlst(directory)
                except Exception:
                    try:
                        ftp.cwd(directory)
                        raw_names = ftp.nlst()
                    except (OSError, error_perm):
                        try:
                            ftp.cwd("/")
                        except Exception:
                            pass
                        continue
                    finally:
                        try:
                            ftp.cwd("/")
                        except Exception:
                            pass
                entries = [
                    (str(raw).rstrip("/").rsplit("/", 1)[-1], {})
                    for raw in raw_names[:800]
                ]

            for name, facts in entries[:800]:
                if name in {".", ".."}:
                    continue
                full = posixpath.join(directory.rstrip("/"), name)
                if not full.startswith("/"):
                    full = "/" + full

                fact_type = str(facts.get("type", "")).lower()
                is_dir = fact_type in {"dir", "cdir", "pdir"}
                if not is_dir and fact_type not in {"file"}:
                    # MLSD often reports FTP links as OS.unix=slink or omits a
                    # useful type.  CWD is the authoritative test for this server.
                    try:
                        ftp.cwd(full)
                        is_dir = True
                    except Exception:
                        is_dir = False
                    finally:
                        try:
                            ftp.cwd("/")
                        except Exception:
                            pass

                if is_dir:
                    if depth < max_depth:
                        queue_.append((full, depth + 1))
                    continue

                if self._uruguay_file_for_day(
                    name, station4=code, day=day, request=request
                ):
                    yield full, name

    @staticmethod
    def _requested_station_map(request: ObservationRequest) -> dict[str, str]:
        result: dict[str, str] = {}
        for station in request.stations or []:
            canonical = str(station).upper()
            code = canonical[:4]
            if code:
                result.setdefault(code, canonical)
        return result

    @staticmethod
    def _uruguay_file_for_day(
        filename: str,
        *,
        station4: str,
        day: date,
        request: ObservationRequest,
    ) -> bool:
        """Match a RINEX/Hatanaka observation file in a day-scoped directory."""
        name = filename.rsplit('/', 1)[-1]
        if not name or not name.upper().startswith(station4.upper()):
            return False

        # IGM historical SFTP also publishes short wrappers such as
        # ``uyri0010.rnx.zip``. The generic matcher cannot infer a RINEX version
        # from that non-standard short filename, so it rejects an existing file
        # when the GUI explicitly requests RINEX 2 or RINEX 3. Treat these ZIP
        # wrappers as version-neutral, while still requiring the selected day.
        lower = name.lower()
        generic_zip = lower.endswith((".rnx.zip", ".crx.zip"))
        if generic_zip:
            doy = datetime_to_doy(day)
            short_day = re.match(
                rf"^{re.escape(station4.lower())}{doy:03d}[0-9a-z]\."
                r"(?:rnx|crx)\.zip$",
                lower,
            )
            long_day = re.search(rf"_[rs]_{day.year}{doy:03d}\d{{4}}_", lower)
            if not (short_day or long_day):
                return False
        elif not _matches_rinex(name, request):
            return False

        info = parse_rinex_filename(name)
        if info.file_type not in {None, "observation"}:
            return False
        if info.station and not str(info.station).upper().startswith(station4.upper()):
            return False
        if info.year and info.year != day.year:
            return False
        if info.doy and info.doy != datetime_to_doy(day):
            return False
        return True

    def _search_archives_sync(self, request: ObservationRequest) -> list[RemoteFile]:
        station_map = self._requested_station_map(request)
        if not station_map:
            return []

        today = date.today()
        files: list[RemoteFile] = []
        current_days = [day for day in request.date_range.days() if day.year == today.year]
        historical_days = [day for day in request.date_range.days() if day.year != today.year]
        current_found_pairs: set[tuple[date, str]] = set()

        # IGM current-year anonymous FTP. The published /hatanaka tree is the
        # ordinary 30 s post-processing archive, so 1 s requests are not sent here.
        if current_days and _sampling_code(request.sampling) != "01S":
            try:
                with FTP(self.current_ftp_host, timeout=5) as ftp:
                    ftp.login()
                    for day in current_days:
                        directory = (
                            f"{self.current_ftp_root}/{day.year}/"
                            f"{datetime_to_doy(day):03d}"
                        )
                        try:
                            ftp.cwd(directory)
                            names = ftp.nlst()
                        except error_perm as exc:
                            try:
                                ftp.cwd("/")
                            except Exception:
                                pass
                            if str(exc).startswith("550"):
                                continue
                            raise
                        for raw_name in names:
                            filename = str(raw_name).rsplit('/', 1)[-1]
                            for code, canonical in station_map.items():
                                if not self._uruguay_file_for_day(
                                    filename, station4=code, day=day, request=request
                                ):
                                    continue
                                remote_path = (
                                    str(raw_name)
                                    if str(raw_name).startswith('/')
                                    else f"{directory}/{filename}"
                                )
                                files.append(
                                    _remote(
                                        self.name,
                                        f"ftp://{self.current_ftp_host}{remote_path}",
                                        filename,
                                        station=canonical,
                                        day=day,
                                        metadata={
                                            "regional_source": self.regional_source,
                                            "regional_archive": "IGM REGNA-ROU current-year Hatanaka FTP",
                                            "download_protocol": "FTP",
                                            "sampling": "30S",
                                            "archive_directory": directory,
                                        },
                                    )
                                )
                                current_found_pairs.add((day, code))
                                break
            except (OSError, error_perm) as exc:
                # Do not abort a current-year plan here.  The public historical
                # SFTP often provides the same YYYY/MM/DD tree and is a useful
                # fallback when the anonymous FTP is temporarily unavailable.
                pass

        # Historical SFTP is also used as a fallback for current-year dates
        # that the anonymous FTP could not serve.  This handles temporary FTP
        # outages and installations where the same date is already mirrored in
        # /sftpserver.
        sftp_days = list(historical_days)
        for day in current_days:
            all_selected_found = all(
                (day, code) in current_found_pairs for code in station_map
            )
            if not all_selected_found and day not in sftp_days:
                sftp_days.append(day)

        if sftp_days:
            try:
                import paramiko
            except ImportError:
                raise

            high_rate = _sampling_code(request.sampling) == "01S"
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                self.historical_sftp_host,
                port=self.historical_sftp_port,
                username=self.historical_sftp_user,
                password=self.historical_sftp_password,
                timeout=5,
                banner_timeout=5,
                auth_timeout=5,
            )
            try:
                sftp = client.open_sftp()
                try:
                    sftp.get_channel().settimeout(5.0)
                except Exception:
                    pass
                deadline = time.monotonic() + 12.0
                timed_out = False
                for day in sftp_days:
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    date_root = (
                        f"{self.historical_sftp_root}/{day.year}/"
                        f"{day.month:02d}/{day.day:02d}"
                    )
                    sampling_root = f"{date_root}/0" if high_rate else date_root
                    for code, canonical in station_map.items():
                        if (day, code) in current_found_pairs:
                            continue
                        if time.monotonic() >= deadline:
                            timed_out = True
                            break
                        directory = f"{sampling_root}/{code}"
                        try:
                            names = sftp.listdir(directory)
                        except (OSError, TimeoutError):
                            lower_directory = f"{sampling_root}/{code.lower()}"
                            try:
                                names = sftp.listdir(lower_directory)
                                directory = lower_directory
                            except (OSError, TimeoutError):
                                continue
                        for filename in names:
                            if not self._uruguay_file_for_day(
                                filename, station4=code, day=day, request=request
                            ):
                                continue
                            remote_path = posixpath.join(directory, filename)
                            files.append(
                                _remote(
                                    self.name,
                                    (
                                        f"sftp://{self.historical_sftp_host}:"
                                        f"{self.historical_sftp_port}{remote_path}"
                                    ),
                                    filename,
                                    station=canonical,
                                    day=day,
                                    metadata={
                                        "regional_source": self.regional_source,
                                        "regional_archive": "IGM REGNA-ROU historical SFTP",
                                        "download_protocol": "SFTP",
                                        "sftp_username": self.historical_sftp_user,
                                        "sftp_password": self.historical_sftp_password,
                                        "sftp_port": str(self.historical_sftp_port),
                                        "sampling": "01S" if high_rate else "30S",
                                        "archive_directory": directory,
                                    },
                                )
                            )
                if timed_out and not files:
                    raise ProviderError(
                        "REGNA-ROU SFTP discovery timed out after 12 seconds. "
                        "The historical server did not respond quickly enough to build a plan."
                    )
            finally:
                client.close()

        unique: dict[str, RemoteFile] = {}
        for item in files:
            unique[str(item.url)] = item
        return list(unique.values())

class CostaRicaSIRGASProvider(SIRGASCountryProvider):
    name = "sirgas_cr"; country_code = "CRI"; country_names = ("Costa Rica",); network_label = "Costa Rica IGN"; regional_source = "sirgas_costa_rica"

class PanamaSIRGASProvider(SIRGASCountryProvider):
    name = "sirgas_pa"
    country_code = "PAN"
    country_names = ("Panama", "Panamá")
    network_label = "Panama IGNTG"
    regional_source = "sirgas_panama"
    portal_url = "https://ignpanama.anati.gob.pa/index.php/cors?sigplus=14"


class ParaguaySIRGASProvider(SIRGASCountryProvider):
    name = "sirgas_py"; country_code = "PRY"; country_names = ("Paraguay",); network_label = "Paraguay SIRGAS"; regional_source = "sirgas_paraguay"

class VenezuelaSIRGASProvider(SIRGASCountryProvider):
    name = "sirgas_ve"; country_code = "VEN"; country_names = ("Venezuela",); network_label = "Venezuela SIRGAS"; regional_source = "sirgas_venezuela"

class GuyanaSIRGASProvider(SIRGASCountryProvider):
    name = "sirgas_gy"; country_code = "GUY"; country_names = ("Guyana",); network_label = "Guyana SIRGAS"; regional_source = "sirgas_guyana"

class SurinameSIRGASProvider(SIRGASCountryProvider):
    name = "sirgas_sr"; country_code = "SUR"; country_names = ("Suriname",); network_label = "Suriname SIRGAS"; regional_source = "sirgas_suriname"


__all__ = [
    "RAMSACArgentinaProvider", "SIRGASRBMCProvider", "ChileCSNProvider", "RGNAMexicoProvider",
    "BoliviaSIRGASProvider", "ColombiaSIRGASProvider", "EcuadorSIRGASProvider",
    "PeruSIRGASProvider", "UruguaySIRGASProvider", "CostaRicaSIRGASProvider",
    "PanamaSIRGASProvider", "ParaguaySIRGASProvider", "VenezuelaSIRGASProvider",
    "GuyanaSIRGASProvider", "SurinameSIRGASProvider",
]
