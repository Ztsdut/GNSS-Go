from __future__ import annotations

import asyncio
import csv
import io
import html as html_lib
import math
import re
import httpx
from pathlib import Path, PurePosixPath
from datetime import date, timedelta
from urllib.parse import quote, urljoin

from gnssgo.exceptions import ProviderError, ProviderProtocolError
from gnssgo.models import ObservationRequest, RemoteFile, Station
from gnssgo.providers.base import ProviderCapabilities
from gnssgo.providers.listing import parse_listing_filenames
from gnssgo.providers.ftp import list_ftp_filenames
from gnssgo.rinex.naming import parse_rinex_filename
from gnssgo.providers.regional_live import (
    RegionalLiveProvider,
    _matches_rinex,
    _remote,
    _sampling_code,
)
from gnssgo.utils.dates import datetime_to_doy


class DirectoryListingRegionalProvider(RegionalLiveProvider):
    """Base class for public regional archives exposed as HTTP directory listings."""

    source_type = "official_directory_listing"
    station_metadata_url: str | None = None

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, station_metadata=False)

    async def _listing(self, url: str) -> str:
        key = f"listing:{url}"
        cached = self.discovery_cache.get(self.name, key)
        if isinstance(cached, str):
            return cached
        text = await self._get_text(url)
        self.discovery_cache.set(self.name, key, text)
        return text

    async def _files_from_directory(
        self,
        *,
        url: str,
        request: ObservationRequest,
        station: str,
        day: date,
        metadata: dict[str, str] | None = None,
    ) -> list[RemoteFile]:
        listing = await self._listing(url)
        if not listing:
            return []
        station_upper = station.upper()
        station4 = station_upper[:4]
        station9 = station_upper[:9]
        files: list[RemoteFile] = []
        for filename in parse_listing_filenames(listing):
            upper = filename.upper()
            if not (upper.startswith(station4) or upper.startswith(station9)):
                continue
            if not _matches_rinex(filename, request):
                continue
            files.append(
                _remote(
                    self.name,
                    urljoin(url.rstrip("/") + "/", filename),
                    filename,
                    station=station_upper,
                    day=day,
                    metadata={"source_type": self.source_type, **(metadata or {})},
                )
            )
        return files




class GLASSRegionalProvider(RegionalLiveProvider):
    """Reusable EPOS-GLASS station/file discovery for European regional networks.

    GLASS is a federation/distribution layer, not a logical station network.  Concrete
    providers keep their own GUI identity (ReNEP, NOA, ...), while sharing this
    structured API implementation.
    """

    source_type = "epos_glass_api"
    glass_bases: tuple[str, ...] = (
        "https://gnssdata-epos.oca.eu/GlassFramework/webresources",
    )
    glass_network: str | None = None
    glass_country: str | None = None
    # Some GLASS installations use the long ISO country name while others use
    # the common short name.  Providers may list aliases and the first endpoint
    # that returns stations wins.
    glass_country_aliases: tuple[str, ...] = ()
    country_code: str = ""
    network_label: str = "EPOS-GNSS"
    regional_source: str = ""
    europe_catalog_version: int = 1
    station_sampling_rates: list[str] = ["30S"]
    station_rinex_versions: list[str] = ["2", "3", "4"]
    glass_page_size: int = 100
    glass_max_pages: int = 50

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, station_metadata=True)

    async def fetch_station_catalog(self) -> list[Station]:
        errors: list[str] = []
        for base in self.glass_bases:
            endpoint_templates: list[str] = []
            if self.glass_network:
                # GLASS 3.x removed the legacy /stations/v2 prefix. Query the
                # current endpoint first and retain v2 only for older national nodes.
                network = quote(self.glass_network, safe=",")
                endpoint_templates.extend((
                    f"{base}/stations/network/{network}/short/json?page={{page}}&perpage={self.glass_page_size}",
                    f"{base}/stations/v2/network/{network}/short/json?page={{page}}&perpage={self.glass_page_size}",
                ))
            country_names = []
            if self.glass_country:
                country_names.append(self.glass_country)
            country_names.extend(self.glass_country_aliases)
            for country_name in dict.fromkeys(country_names):
                country = quote(country_name)
                endpoint_templates.extend((
                    f"{base}/stations/location/country/{country}/short/json?page={{page}}&perpage={self.glass_page_size}",
                    f"{base}/stations/v2/location/country/{country}/short/json?page={{page}}&perpage={self.glass_page_size}",
                ))

            for template in endpoint_templates:
                try:
                    records, source_url = await self._glass_paginated_records(template)
                    stations = self._stations_from_glass(records, catalog_source=source_url)
                    if stations:
                        self.last_station_catalog_stats = {
                            "catalog_complete": True,
                            "europe_catalog_version": self.europe_catalog_version,
                            "station_count": len(stations),
                            "regional_source": self.regional_source,
                            "catalog_source_used": source_url,
                            "glass_pages_fetched": max(1, (len(records) + self.glass_page_size - 1) // self.glass_page_size),
                        }
                        return stations
                except (ProviderError, ProviderProtocolError, OSError) as exc:
                    errors.append(f"{template.format(page=0)}: {exc}")
        raise ProviderError(
            f"{self.name} GLASS station catalog could not be loaded"
            + (f": {' | '.join(errors)}" if errors else ".")
        )

    async def _glass_paginated_records(self, template: str) -> tuple[list[dict[str, object]], str]:
        """Fetch one GLASS query across pages with loop/duplicate protection.

        Many national networks exceed the API default page size.  Treating page 0
        as a complete catalog silently truncates networks such as RING/OS Net.
        """
        records: list[dict[str, object]] = []
        fingerprints: set[str] = set()
        source_url = template.format(page=0)
        for page in range(self.glass_max_pages):
            url = template.format(page=page)
            payload = await self._get_json(url)
            page_records = _glass_records(payload)
            if not page_records:
                break
            new_count = 0
            for record in page_records:
                marker = _glass_string(record, "marker", "station", "station_id", "stationId", "id")
                lat = _glass_string(record, "latitude", "lat")
                lon = _glass_string(record, "longitude", "lon", "long")
                key = f"{marker}|{lat}|{lon}|{repr(sorted(record.items()))[:256]}"
                if key in fingerprints:
                    continue
                fingerprints.add(key)
                records.append(record)
                new_count += 1
            # Full pages may be followed by another page.  A partial page is final.
            # If a server ignores the page parameter and repeats page 0, stop too.
            if len(page_records) < self.glass_page_size or new_count == 0:
                break
        return records, source_url

    def _stations_from_glass(
        self, records: list[dict[str, object]], *, catalog_source: str
    ) -> list[Station]:
        stations: dict[str, Station] = {}
        for record in records:
            parsed = _glass_station_fields(record, forced_country=self.country_code)
            if parsed is None:
                continue
            station_id, marker, latitude, longitude, height, networks, country = parsed
            # Country endpoint responses can contain cross-node metadata.  A logical
            # national source must never leak other countries into its map layer.
            if self.country_code and station_id[-3:] != self.country_code:
                continue
            network_names = list(dict.fromkeys([self.network_label, *networks]))
            stations[station_id] = Station(
                id=station_id,
                marker_name=marker,
                latitude=latitude,
                longitude=longitude,
                height=height,
                country=country or self.country_code or None,
                network=network_names,
                data_networks=["europe"],
                regional_sources=[self.regional_source],
                providers=[self.name],
                aliases=list(dict.fromkeys([marker, station_id])),
                sampling_rates=list(self.station_sampling_rates),
                rinex_versions=list(self.station_rinex_versions),
                metadata={
                    "catalog_source": catalog_source,
                    "source_type": self.source_type,
                    "distribution": "EPOS GLASS",
                },
            )
        return list(stations.values())

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        # GLASS supports several markers in one request. Batch rather than issuing
        # station x day queries; this is also how pyglass avoids excessive PLAN time.
        station_map = {station.upper(): station.upper() for station in request.stations or []}
        if not station_map:
            return []
        marker_csv = ",".join(station_map)
        date_range = f"{request.date_range.start.isoformat()},{request.date_range.end.isoformat()}"
        errors: list[str] = []
        for base in self.glass_bases:
            # GLASS 3.3.5+ renamed dateRange to reference_date. Keep dateRange as a
            # compatibility retry for older national nodes.
            base_failed = True
            for date_key in ("reference_date", "dateRange"):
                instance = f"marker={marker_csv}&{date_key}={date_range}"
                template = (
                    f"{base}/files/combination/{instance}/json"
                    f"?filtervalidatedfiles=1&page={{page}}&perpage={self.glass_page_size}"
                )
                try:
                    records, _ = await self._glass_paginated_records(template)
                    base_failed = False
                    files = self._glass_remote_files(records, request)
                    if files:
                        return files
                    # A valid empty response is authoritative: do not duplicate the
                    # query against another federation node just because data is absent.
                    return []
                except (ProviderError, ProviderProtocolError, OSError) as exc:
                    errors.append(f"{template.format(page=0)}: {exc}")
                    continue
            if not base_failed:
                return []
        if errors:
            raise ProviderError(f"{self.name} GLASS file query failed: {' | '.join(errors)}")
        return []

    def _glass_remote_files(
        self, records: list[dict[str, object]], request: ObservationRequest
    ) -> list[RemoteFile]:
        selected = {station.upper() for station in request.stations or []}
        selected4 = {station[:4].upper() for station in selected}
        result: list[RemoteFile] = []
        seen: set[tuple[str, str]] = set()
        for record in records:
            url = _glass_file_url(record)
            filename = _glass_file_name(record, url=url)
            if not url or not filename:
                continue
            info = parse_rinex_filename(filename)
            station = _glass_station_id_from_file_record(record)
            if not station and info is not None:
                station = str(info.station).upper()
            if not station:
                station = filename[:9].upper() if "_" in filename else filename[:4].upper()
            station_upper = station.upper()
            if selected and station_upper not in selected and station_upper[:4] not in selected4:
                continue
            if not _matches_rinex(filename, request):
                continue
            if not _is_daily_observation_name(filename):
                continue
            file_day = _glass_file_day(record, filename)
            if file_day is None or not (request.date_range.start <= file_day <= request.date_range.end):
                continue
            key = (station_upper, filename)
            if key in seen:
                continue
            seen.add(key)
            item = _remote(
                self.name,
                url,
                filename,
                station=station_upper,
                day=file_day,
                metadata={
                    "regional_archive": self.network_label,
                    "regional_source": self.regional_source,
                    "source_type": self.source_type,
                    "distribution": "EPOS GLASS",
                    "glass_data_center": _glass_string(record, "data_center_acronym", "datacenter_acronym", "dataCenterAcronym"),
                },
            )
            checksum = _glass_string(record, "md5", "checksum", "md5sum")
            if checksum:
                item.checksum = checksum
            result.append(item)
        return result


class BelgiumGNSSProvider(GLASSRegionalProvider):
    """Belgian national/EPOS repository maintained by ROB.

    Station discovery can use the federated GLASS metadata service, while RINEX
    discovery/download stays on ROB's authoritative Belgium API/file server.
    """

    name = "belgium_be"
    data_network = "europe"
    glass_country = "Belgium"
    country_code = "BEL"
    network_label = "Belgium GNSS"
    regional_source = "europe_belgium"
    europe_catalog_version = 2
    belgium_api = "https://gnss.be/api/v1/belgium/station-data"

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        if _sampling_code(request.sampling) not in {"30S", ""}:
            return []
        files: list[RemoteFile] = []
        for station in request.stations or []:
            station_id = station.upper()
            rinex = "all" if str(request.rinex) == "auto" else str(request.rinex)
            url = (
                f"{self.belgium_api}/{station_id}?rinexVersion={rinex}"
                f"&startDate={request.date_range.start.isoformat()}"
                f"&endDate={request.date_range.end.isoformat()}"
            )
            payload = await self._get_json(url)
            if not isinstance(payload, list):
                raise ProviderProtocolError("Belgian GNSS API returned an unexpected schema.")
            for record in payload:
                if not isinstance(record, dict):
                    continue
                remote_url = str(record.get("url") or "")
                filename = str(record.get("filename") or PurePosixPath(remote_url).name)
                if not remote_url or not filename or not _matches_rinex(filename, request):
                    continue
                file_day = _glass_file_day(record, filename)
                if file_day is None or not (request.date_range.start <= file_day <= request.date_range.end):
                    continue
                files.append(
                    _remote(
                        self.name, remote_url, filename,
                        station=str(record.get("stationId") or station_id),
                        day=file_day,
                        metadata={
                            "regional_archive": "Belgian GNSS repository",
                            "regional_source": self.regional_source,
                            "source_type": "gnss.be_api",
                            "file_server": "https://gnss.be/pub/RINEX",
                        },
                    )
                )
        return files


class NOAGreeceProvider(GLASSRegionalProvider):
    """Greek NOA/EPOS stations with direct GEIN RINEX download.

    GLASS supplies structured station metadata and remains a file-query fallback,
    while the NOA/GEIN ``GPSData/YYYY/DOY`` directory is the authoritative and
    faster path for daily files.
    """

    name = "noa_gr"
    data_network = "europe"
    glass_country = "Greece"
    country_code = "GRC"
    network_label = "NOA / EPOS-GNSS"
    regional_source = "europe_greece"
    europe_catalog_version = 2
    glass_bases = ("https://gnssdata-epos.oca.eu/GlassFramework/webresources",)
    gein_base = "https://www.gein.noa.gr/services/GPSData"

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        sampling = _sampling_code(request.sampling)
        if sampling not in {"30S", ""}:
            return await super().search_observations(request)
        files: list[RemoteFile] = []
        try:
            for day in request.date_range.days():
                directory = f"{self.gein_base}/{day.year}/{datetime_to_doy(day):03d}/"
                listing = await self._get_text(directory)
                names = parse_listing_filenames(listing)
                for station in request.stations or []:
                    station_upper = station.upper()
                    station4 = station_upper[:4]
                    station9 = station_upper[:9]
                    for filename in names:
                        upper = filename.upper()
                        if not (upper.startswith(station9) or upper.startswith(station4)):
                            continue
                        if not _matches_rinex(filename, request):
                            continue
                        if not _is_daily_observation_name(filename):
                            continue
                        files.append(_remote(
                            self.name,
                            urljoin(directory, filename),
                            filename,
                            station=station_upper,
                            day=day,
                            metadata={
                                "regional_archive": "NOA / GEIN",
                                "regional_source": self.regional_source,
                                "source_type": "official_noa_directory",
                                "fallback_distribution": "EPOS GLASS",
                            },
                        ))
            if files:
                return files
            return await super().search_observations(request)
        except (ProviderError, ProviderProtocolError, OSError):
            # The federation remains useful when the NOA web directory is
            # temporarily unavailable or when a station is held at another node.
            return await super().search_observations(request)

class ItalyEPOSProvider(GLASSRegionalProvider):
    """Italian GNSS stations distributed through EPOS GLASS (RING and peers)."""

    name = "epos_it"
    data_network = "europe"
    glass_country = "Italy"
    country_code = "ITA"
    network_label = "Italy · EPOS/GLASS (RING + others)"
    regional_source = "europe_italy"
    europe_catalog_version = 1
    # The INGV RING node is an official EPOS local node; central GLASS remains
    # first because it also indexes Italian stations contributed by other nodes.
    glass_bases = (
        "https://gnssdata-epos.oca.eu/GlassFramework/webresources",
        "http://glass.ingv.it:8080/GlassFramework/webresources",
    )


class PolandEPOSProvider(GLASSRegionalProvider):
    """Polish stations exposed by EPOS/GLASS, including ASG-EUPOS contributors."""

    name = "epos_pl"
    data_network = "europe"
    glass_country = "Poland"
    country_code = "POL"
    network_label = "Poland · EPOS/GLASS (ASG-EUPOS)"
    regional_source = "europe_poland"
    europe_catalog_version = 1


class RomaniaEPOSProvider(GLASSRegionalProvider):
    name = "epos_ro"
    data_network = "europe"
    glass_country = "Romania"
    country_code = "ROU"
    network_label = "Romania · EPOS National Node"
    regional_source = "europe_romania"
    europe_catalog_version = 1


class UnitedKingdomEPOSProvider(GLASSRegionalProvider):
    """GB stations mirrored in EPOS; direct OS Net API remains account based."""

    name = "epos_uk"
    data_network = "europe"
    glass_country = "United Kingdom of Great Britain and Northern Ireland"
    glass_country_aliases = ("United Kingdom", "Great Britain")
    country_code = "GBR"
    network_label = "United Kingdom · EPOS/GLASS (OS Net)"
    regional_source = "europe_uk"
    europe_catalog_version = 1


class SwedenEPOSProvider(GLASSRegionalProvider):
    name = "epos_se"
    data_network = "europe"
    glass_country = "Sweden"
    country_code = "SWE"
    network_label = "Sweden · EPOS/GLASS (SWEPOS)"
    regional_source = "europe_sweden"
    europe_catalog_version = 1


class FinlandEPOSProvider(GLASSRegionalProvider):
    name = "epos_fi"
    data_network = "europe"
    glass_country = "Finland"
    country_code = "FIN"
    network_label = "Finland · EPOS/GLASS (FinnRef/FINPOS)"
    regional_source = "europe_finland"
    europe_catalog_version = 1


class SwitzerlandEPOSProvider(GLASSRegionalProvider):
    name = "epos_ch"
    data_network = "europe"
    glass_country = "Switzerland"
    country_code = "CHE"
    network_label = "Switzerland · EPOS/GLASS (AGNES)"
    regional_source = "europe_switzerland"
    europe_catalog_version = 1


class HungaryEPOSProvider(GLASSRegionalProvider):
    name = "epos_hu"
    data_network = "europe"
    glass_country = "Hungary"
    country_code = "HUN"
    network_label = "Hungary · EPOS/GLASS"
    regional_source = "europe_hungary"


class CzechiaEPOSProvider(GLASSRegionalProvider):
    name = "epos_cz"
    data_network = "europe"
    glass_country = "Czechia"
    glass_country_aliases = ("Czech Republic",)
    country_code = "CZE"
    network_label = "Czechia · EPOS/GLASS"
    regional_source = "europe_czechia"


class SloveniaEPOSProvider(GLASSRegionalProvider):
    name = "epos_si"
    data_network = "europe"
    glass_country = "Slovenia"
    country_code = "SVN"
    network_label = "Slovenia · EPOS/GLASS"
    regional_source = "europe_slovenia"


class IrelandEPOSProvider(GLASSRegionalProvider):
    name = "epos_ie"
    data_network = "europe"
    glass_country = "Ireland"
    country_code = "IRL"
    network_label = "Ireland · EPOS/GLASS"
    regional_source = "europe_ireland"


class IcelandEPOSProvider(GLASSRegionalProvider):
    name = "epos_is"
    data_network = "europe"
    glass_country = "Iceland"
    country_code = "ISL"
    network_label = "Iceland · EPOS/GLASS"
    regional_source = "europe_iceland"


class CroatiaEPOSProvider(GLASSRegionalProvider):
    name = "epos_hr"
    data_network = "europe"
    glass_country = "Croatia"
    country_code = "HRV"
    network_label = "Croatia · EPOS/GLASS"
    regional_source = "europe_croatia"


class NorwayEPOSProvider(GLASSRegionalProvider):
    name = "epos_no"
    data_network = "europe"
    glass_country = "Norway"
    country_code = "NOR"
    network_label = "Norway · EPOS/GLASS"
    regional_source = "europe_norway"


class DenmarkEPOSProvider(GLASSRegionalProvider):
    name = "epos_dk"
    data_network = "europe"
    glass_country = "Denmark"
    country_code = "DNK"
    network_label = "Denmark · EPOS/GLASS"
    regional_source = "europe_denmark"


class EstoniaEPOSProvider(GLASSRegionalProvider):
    name = "epos_ee"
    data_network = "europe"
    glass_country = "Estonia"
    country_code = "EST"
    network_label = "Estonia · EPOS/GLASS"
    regional_source = "europe_estonia"


class LatviaEPOSProvider(GLASSRegionalProvider):
    name = "epos_lv"
    data_network = "europe"
    glass_country = "Latvia"
    country_code = "LVA"
    network_label = "Latvia · EPOS/GLASS"
    regional_source = "europe_latvia"


class LithuaniaEPOSProvider(GLASSRegionalProvider):
    name = "epos_lt"
    data_network = "europe"
    glass_country = "Lithuania"
    country_code = "LTU"
    network_label = "Lithuania · EPOS/GLASS"
    regional_source = "europe_lithuania"


class SlovakiaEPOSProvider(GLASSRegionalProvider):
    name = "epos_sk"
    data_network = "europe"
    glass_country = "Slovakia"
    country_code = "SVK"
    network_label = "Slovakia · EPOS/GLASS"
    regional_source = "europe_slovakia"


class BulgariaEPOSProvider(GLASSRegionalProvider):
    name = "epos_bg"
    data_network = "europe"
    glass_country = "Bulgaria"
    country_code = "BGR"
    network_label = "Bulgaria · EPOS/GLASS"
    regional_source = "europe_bulgaria"


class CyprusEPOSProvider(GLASSRegionalProvider):
    name = "epos_cy"
    data_network = "europe"
    glass_country = "Cyprus"
    country_code = "CYP"
    network_label = "Cyprus · EPOS/GLASS"
    regional_source = "europe_cyprus"


class SerbiaEPOSProvider(GLASSRegionalProvider):
    name = "epos_rs"
    data_network = "europe"
    glass_country = "Serbia"
    country_code = "SRB"
    network_label = "Serbia · EPOS/GLASS"
    regional_source = "europe_serbia"


class TurkeyEPOSProvider(GLASSRegionalProvider):
    name = "epos_tr"
    data_network = "europe"
    glass_country = "Türkiye"
    glass_country_aliases = ("Turkey",)
    country_code = "TUR"
    network_label = "Türkiye · EPOS/GLASS"
    regional_source = "europe_turkey"


class LuxembourgEPOSProvider(GLASSRegionalProvider):
    name = "epos_lu"
    data_network = "europe"
    glass_country = "Luxembourg"
    country_code = "LUX"
    network_label = "Luxembourg · EPOS/GLASS"
    regional_source = "europe_luxembourg"


class AlbaniaEPOSProvider(GLASSRegionalProvider):
    name = "epos_al"
    data_network = "europe"
    glass_country = "Albania"
    country_code = "ALB"
    network_label = "Albania · EPOS/GLASS"
    regional_source = "europe_albania"


class BosniaEPOSProvider(GLASSRegionalProvider):
    name = "epos_ba"
    data_network = "europe"
    glass_country = "Bosnia and Herzegovina"
    country_code = "BIH"
    network_label = "Bosnia and Herzegovina · EPOS/GLASS"
    regional_source = "europe_bosnia"


class NorthMacedoniaEPOSProvider(GLASSRegionalProvider):
    name = "epos_mk"
    data_network = "europe"
    glass_country = "North Macedonia"
    glass_country_aliases = ("Macedonia",)
    country_code = "MKD"
    network_label = "North Macedonia · EPOS/GLASS"
    regional_source = "europe_north_macedonia"


class MoldovaEPOSProvider(GLASSRegionalProvider):
    name = "epos_md"
    data_network = "europe"
    glass_country = "Moldova"
    glass_country_aliases = ("Republic of Moldova",)
    country_code = "MDA"
    network_label = "Moldova · EPOS/GLASS"
    regional_source = "europe_moldova"


class UkraineEPOSProvider(GLASSRegionalProvider):
    name = "epos_ua"
    data_network = "europe"
    glass_country = "Ukraine"
    country_code = "UKR"
    network_label = "Ukraine · EPOS/GLASS"
    regional_source = "europe_ukraine"


class MaltaEPOSProvider(GLASSRegionalProvider):
    name = "epos_mt"
    data_network = "europe"
    glass_country = "Malta"
    country_code = "MLT"
    network_label = "Malta · EPOS/GLASS"
    regional_source = "europe_malta"


class MontenegroEPOSProvider(GLASSRegionalProvider):
    name = "epos_me"
    data_network = "europe"
    glass_country = "Montenegro"
    country_code = "MNE"
    network_label = "Montenegro · EPOS/GLASS"
    regional_source = "europe_montenegro"


class DPGANetherlandsProvider(DirectoryListingRegionalProvider):
    name = "dpga_nl"
    data_network = "netherlands"
    base = "https://gnss1.tudelft.nl/dpga/rinex"
    coordinates_url = "https://gnss1.tudelft.nl/dpga/coordinates.html"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, station_metadata=True)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        sampling = _sampling_code(request.sampling)
        for day in request.date_range.days():
            year, doy = day.year, datetime_to_doy(day)
            if sampling == "01S":
                directory = f"{self.base}/highrate/{year}/{doy:03d}/"
            elif sampling == "10S":
                directory = f"{self.base}/hourly/{year}/{doy:03d}/"
            else:
                # The official DPGA archive exposes daily data directly below YEAR/DOY.
                directory = f"{self.base}/{year}/{doy:03d}/"
            for station in request.stations or []:
                files.extend(
                    await self._files_from_directory(
                        url=directory,
                        request=request,
                        station=station,
                        day=day,
                        metadata={"regional_archive": "DPGA"},
                    )
                )
        return files

    async def fetch_station_catalog(self) -> list[Station]:
        text = await self._get_text(self.coordinates_url)
        stations: dict[str, Station] = {}
        # The official coordinate page publishes station marker, DOMES, and ECEF XYZ.
        # Convert those published XYZ coordinates to geodetic coordinates for map display.
        plain = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
        pattern = re.compile(
            r"\b(?P<station>[A-Z0-9]{4})\s+"
            r"(?P<domes>\d{5}[MS]\d{3})\s+"
            r"(?P<x>[-+]?\d+(?:\.\d+)?)\s+"
            r"(?P<y>[-+]?\d+(?:\.\d+)?)\s+"
            r"(?P<z>[-+]?\d+(?:\.\d+)?)",
            flags=re.IGNORECASE,
        )
        for match in pattern.finditer(plain):
            marker = match.group("station").upper()
            try:
                x = float(match.group("x"))
                y = float(match.group("y"))
                z = float(match.group("z"))
            except ValueError:
                continue
            latitude, longitude, height = _ecef_to_geodetic(x, y, z)
            station_id = f"{marker}00NLD"
            stations[station_id] = Station(
                id=station_id,
                marker_name=marker,
                domes=match.group("domes").upper(),
                latitude=latitude,
                longitude=longitude,
                height=height,
                country="NLD",
                network=["DPGA"],
                data_networks=["netherlands"],
                providers=[self.name],
                sampling_rates=["01S", "10S", "30S"],
                rinex_versions=["2", "3"],
                aliases=[marker],
                metadata={"catalog_source": self.coordinates_url},
            )
        return list(stations.values())


class RENAGFranceProvider(DirectoryListingRegionalProvider):
    name = "renag_fr"
    data_network = "france"
    bases = {
        ("2", False): "https://renag.resif.fr/pub/data",
        ("2", True): "https://renag.resif.fr/pub/data_1s",
        ("3", False): "https://renag.resif.fr/pub/rinex3",
        ("3", True): "https://renag.resif.fr/pub/rinex3_1s",
    }

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        highrate = _sampling_code(request.sampling) == "01S"
        requested_rinex = str(request.rinex)
        version = "2" if requested_rinex == "2" else "3"
        base = self.bases[(version, highrate)]
        for day in request.date_range.days():
            year, doy = day.year, datetime_to_doy(day)
            directory = f"{base}/{year}/{doy:03d}/"
            for station in request.stations or []:
                files.extend(
                    await self._files_from_directory(
                        url=directory,
                        request=request,
                        station=station,
                        day=day,
                        metadata={"regional_archive": "RENAG", "rinex_family": version},
                    )
                )
        return files



class RGPFranceProvider(DirectoryListingRegionalProvider):
    """French RGP network inside the Europe regional selector.

    The RGP web portal (``rgp.ign.fr``) can be noticeably slower or time out
    from some networks even while the actual RINEX file server remains
    reachable.  Station metadata therefore uses the EPOS/GLASS federation
    first and falls back to the official RGP CSV/HTML exports.  Observation
    files still come from IGN's authoritative ``rgpdata.ign.fr`` archive.
    """

    name = "rgp_fr"
    data_network = "europe"
    base = "https://rgpdata.ign.fr/pub"
    coordinates_url = "https://rgp.ign.fr/STATIONS/coordRGP.php?t=csv"
    coordinates_html_url = "https://rgp.ign.fr/STATIONS/coordonnees.php"
    glass_base = "https://gnssdata-epos.oca.eu/GlassFramework/webresources"
    station_catalog_source = glass_base
    source_type = "official_rgp"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, station_metadata=True)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        sampling = _sampling_code(request.sampling)
        folder = {"01S": "1", "05S": "5", "10S": "10", "15S": "15", "30S": "30"}.get(sampling)
        if folder is None:
            return []
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            doy = datetime_to_doy(day)
            if str(request.rinex) == "2":
                roots = [f"{self.base}/data/{day.year}/{doy:03d}/data_{folder}/"]
            elif str(request.rinex) in {"3", "4"}:
                roots = [f"{self.base}/data_v3/{day.year}/{doy:03d}/data_{folder}/"]
            else:
                roots = [
                    f"{self.base}/data_v3/{day.year}/{doy:03d}/data_{folder}/",
                    f"{self.base}/data/{day.year}/{doy:03d}/data_{folder}/",
                ]
            for station in request.stations or []:
                for directory in roots:
                    found = await self._files_from_directory(
                        url=directory,
                        request=request,
                        station=station,
                        day=day,
                        metadata={"regional_archive": "RGP", "regional_source": "europe_rgp"},
                    )
                    found = [item for item in found if _is_daily_observation_name(item.filename)]
                    if found:
                        files.extend(found)
                        break
        return files

    async def fetch_station_catalog(self) -> list[Station]:
        # Prefer EPOS/GLASS for station metadata.  In practice the RGP portal
        # itself can time out while the RINEX archive remains healthy; using
        # the federated metadata service keeps the Europe selector responsive.
        glass_rows = await self._fetch_glass_rgp_catalog()
        if glass_rows:
            stations = self._stations_from_rgp_rows(
                glass_rows, source="EPOS GLASS / RGP", source_type="epos_glass_api"
            )
            if stations:
                self.last_station_catalog_stats = {
                    "catalog_complete": True,
                    "europe_catalog_version": 7,
                    "station_count": len(stations),
                    "regional_source": "europe_rgp",
                    "catalog_source_used": "EPOS GLASS / RGP",
                }
                return stations

        # GLASS is a federation and may occasionally be unavailable as well.
        # Retain IGN's own compact export and browser table as authoritative
        # fallbacks, but do not make them the first network hop.
        rows: list[tuple[str, float, float, float]] = []
        source = self.coordinates_url
        try:
            text = await self._get_text(self.coordinates_url)
            rows = _rgp_csv_rows(text)
        except Exception:
            rows = []
        if not rows:
            try:
                html = await self._get_text(self.coordinates_html_url)
                rows = _rgp_coordinate_rows(html)
                source = self.coordinates_html_url
            except Exception as exc:
                raise ProviderError(
                    "RGP station catalog unavailable from both EPOS GLASS and IGN: "
                    f"{exc}"
                ) from exc

        stations = self._stations_from_rgp_rows(rows, source=source, source_type=self.source_type)
        self.last_station_catalog_stats = {
            "catalog_complete": bool(stations),
            "europe_catalog_version": 7,
            "station_count": len(stations),
            "regional_source": "europe_rgp",
            "catalog_source_used": source,
        }
        return stations

    async def _fetch_glass_rgp_catalog(self) -> list[tuple[str, float, float, float]]:
        """Return RGP/French station coordinates from the central GLASS node.

        Query the RGP network first.  If a node does not expose that network
        label, fall back to the France country query.  Pagination is required
        because France has far more than the 100-record default page size.
        """
        query_paths = (
            "stations/network/RGP/short/json",
            "stations/location/country/France/short/json",
        )
        for path in query_paths:
            rows: dict[str, tuple[str, float, float, float]] = {}
            try:
                for page in range(10):
                    url = f"{self.glass_base}/{path}?page={page}&perpage=100"
                    payload = await self._get_json(url)
                    records = _glass_records(payload)
                    if not records:
                        break
                    for record in records:
                        parsed = _glass_station_fields(record, forced_country="FRA")
                        if parsed is None:
                            continue
                        station_id, marker, latitude, longitude, height, _networks, _country = parsed
                        # Europe selector intentionally excludes French overseas
                        # territories.  They can be exposed as separate regional
                        # networks later without distorting the Europe map.
                        if not (34.0 <= latitude <= 72.0 and -25.0 <= longitude <= 45.0):
                            continue
                        rows[station_id] = (marker, latitude, longitude, float(height or 0.0))
                    if len(records) < 100:
                        break
            except (ProviderError, ProviderProtocolError, OSError):
                rows = {}
            if rows:
                return list(rows.values())
        return []

    def _stations_from_rgp_rows(
        self,
        rows: list[tuple[str, float, float, float]],
        *,
        source: str,
        source_type: str,
    ) -> list[Station]:
        stations: list[Station] = []
        seen: set[str] = set()
        for marker, latitude, longitude, height in rows:
            marker4 = marker[:4].upper()
            if not marker4 or marker4 in seen:
                continue
            if not (34.0 <= latitude <= 72.0 and -25.0 <= longitude <= 45.0):
                continue
            seen.add(marker4)
            station_id = f"{marker4}00FRA"
            stations.append(
                Station(
                    id=station_id,
                    marker_name=marker4,
                    latitude=latitude,
                    longitude=longitude,
                    height=height,
                    country="FRA",
                    network=["RGP"],
                    data_networks=["europe"],
                    regional_sources=["europe_rgp"],
                    providers=[self.name],
                    aliases=[marker4],
                    sampling_rates=["01S", "05S", "10S", "15S", "30S"],
                    rinex_versions=["2", "3"],
                    metadata={"catalog_source": source, "source_type": source_type},
                )
            )
        return stations



class GREFGermanyProvider(DirectoryListingRegionalProvider):
    """German GREF network and the BKG GREF RINEX archive."""

    name = "gref_de"
    data_network = "europe"
    base = "https://igs.bkg.bund.de/root_ftp/GREF"
    station_index = "https://gref.bkg.bund.de/Subsites/GREF/DE/Home/home.html"
    station_api = "https://igs.bkg.bund.de/api/collections/stations/items?limit=1000&f=json"
    station_catalog_source = station_api
    source_type = "official_gref"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, station_metadata=True)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        if _sampling_code(request.sampling) not in {"30S", ""}:
            return []
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            doy = datetime_to_doy(day)
            directory = f"{self.base}/obs/{day.year}/{doy:03d}/"
            for station in request.stations or []:
                found = await self._files_from_directory(
                    url=directory,
                    request=request,
                    station=station,
                    day=day,
                    metadata={"regional_archive": "GREF", "regional_source": "europe_gref"},
                )
                files.extend(item for item in found if _is_daily_observation_name(item.filename))
        return files

    async def fetch_station_catalog(self) -> list[Station]:
        # The public BKG Station List is JavaScript driven.  Its station metadata
        # collection is the structured source behind that UI; use it first and
        # filter the explicit GREF network membership locally.  The archive/site-log
        # paths remain fallbacks because BKG labels its general archive REST API as
        # under development, while the station collection itself is publicly used
        # by the station pages.
        stations: list[Station] = []
        api_used = False
        try:
            payload = await self._get_json(self.station_api)
            stations = _gref_stations_from_bkg_api(payload, provider=self.name)
            api_used = bool(stations)
        except (ProviderError, ProviderProtocolError, OSError):
            stations = []

        # BKG is also an EPOS-GNSS contributor.  The federated GLASS metadata
        # service is a reliable structured fallback when the JavaScript-backed
        # BKG station collection changes schema or is temporarily unavailable.
        glass_used = False
        if not stations:
            try:
                stations = await _glass_station_catalog_for(
                    self,
                    network="GREF",
                    country="Germany",
                    country_code="DEU",
                    network_label="GREF",
                    regional_source="europe_gref",
                    provider_name=self.name,
                )
                glass_used = bool(stations)
            except (ProviderError, ProviderProtocolError, OSError):
                stations = []

        # BKG's GREF archive publishes standard IGS site logs in /station/.
        # This is more stable than relying on the public GREF homepage navigation,
        # which no longer exposes individual station links in its HTML.
        station_dir = f"{self.base}/station/"
        try:
            listing = "" if stations else await self._listing(station_dir)
        except Exception:
            listing = ""
        log_names = _dedupe_station_log_names(
            [name for name in parse_listing_filenames(listing) if name.lower().endswith(".log")]
        )
        if log_names:
            semaphore = asyncio.Semaphore(8)

            async def fetch_log(name: str):
                async with semaphore:
                    url = urljoin(station_dir, name)
                    try:
                        return _parse_gref_station_log(await self._get_text(url), url=url)
                    except Exception:
                        return None

            parsed = await asyncio.gather(*(fetch_log(name) for name in log_names))
            stations = [station for station in parsed if station is not None]

        # Fallback for older mirrors/tests: parse individual station web pages when
        # the archive station-log directory is unavailable.
        if not stations:
            index = await self._get_text(self.station_index)
            links = _gref_station_links(index)
            if not links:
                try:
                    root = await self._get_text("https://gref.bkg.bund.de/")
                except Exception:
                    root = ""
                links = _gref_station_links(root)
            semaphore = asyncio.Semaphore(8)

            async def fetch_one(url: str):
                async with semaphore:
                    try:
                        return _parse_gref_station_page(await self._get_text(url), url=url)
                    except (ProviderError, OSError):
                        return None

            parsed = await asyncio.gather(*(fetch_one(url) for url in links))
            stations = [station for station in parsed if station is not None]

        # The current GREF homepage may expose the station network primarily as
        # an image rather than normal anchor links.  As a second official fallback,
        # infer active marker IDs from a recent BKG GREF observation directory and
        # then request the predictable BKG station pages for those markers.
        if not stations:
            markers: list[str] = []
            today = date.today()
            for offset in range(2, 16):
                day = today - timedelta(days=offset)
                directory = f"{self.base}/obs/{day.year}/{datetime_to_doy(day):03d}/"
                try:
                    listing = await self._listing(directory)
                except Exception:
                    continue
                markers = _gref_markers_from_obs_listing(listing)
                if markers:
                    break
            if markers:
                semaphore = asyncio.Semaphore(6)

                async def fetch_marker(marker: str):
                    url = (
                        "https://gref.bkg.bund.de/Subsites/GREF/DE/Stationen/"
                        f"{marker}/{marker}.html"
                    )
                    async with semaphore:
                        try:
                            return _parse_gref_station_page(await self._get_text(url), url=url)
                        except Exception:
                            return None

                parsed = await asyncio.gather(*(fetch_marker(marker) for marker in markers))
                stations = [station for station in parsed if station is not None]

        self.last_station_catalog_stats = {
            "catalog_complete": bool(stations),
            "europe_catalog_version": 5,
            "station_count": len(stations),
            "regional_source": "europe_gref",
            "catalog_source_used": (
                self.station_api if api_used else
                "EPOS GLASS / GREF" if glass_used else
                station_dir if log_names else self.station_index
            ),
        }
        return stations


class RedGAESpainProvider(DirectoryListingRegionalProvider):
    """Spanish redGAE federation.

    The station catalog indexes all networks published by redGAE.  Automated
    download currently uses the official IGN ERGNSS daily 30 s archive, which
    contains a broad national collection.  Stations not mirrored there return
    unavailable immediately instead of stalling PLAN; their network-specific
    portals remain discoverable from redGAE and can be added incrementally.
    """

    name = "redgae_es"
    data_network = "europe"
    station_catalog_source = "https://redgae.ign.es/estaciones"
    daily_base = "https://datos-geodesia.ign.es/ERGNSS/diario_30s"
    source_type = "official_redgae"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, station_metadata=True)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        if _sampling_code(request.sampling) not in {"30S", ""}:
            return []
        if str(request.rinex) == "2":
            # The current central ERGNSS daily directory is modern RINEX 3.
            return []
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            directory = f"{self.daily_base}/{day.year}/{day:%Y%m%d}/"
            for station in request.stations or []:
                found = await self._files_from_directory(
                    url=directory,
                    request=request,
                    station=station,
                    day=day,
                    metadata={
                        "regional_archive": "redGAE / ERGNSS",
                        "regional_source": "europe_redgae",
                        "integration_scope": "central_ergnss_daily_archive",
                    },
                )
                files.extend(item for item in found if _is_daily_observation_name(item.filename))
        return files

    async def fetch_station_catalog(self) -> list[Station]:
        text = await self._get_text(self.station_catalog_source)
        stations: dict[str, Station] = {}
        for marker, domes, latitude, longitude, height in _redgae_coordinate_rows(text):
            station_id = f"{marker}00ESP"
            stations[station_id] = Station(
                id=station_id,
                marker_name=marker,
                domes=domes,
                latitude=latitude,
                longitude=longitude,
                height=height,
                country="ESP",
                network=["redGAE"],
                data_networks=["europe"],
                regional_sources=["europe_redgae"],
                providers=[self.name],
                aliases=[marker],
                sampling_rates=["30S"],
                rinex_versions=["3"],
                metadata={
                    "catalog_source": self.station_catalog_source,
                    "source_type": self.source_type,
                    "download_scope": "central ERGNSS mirror where available",
                },
            )
        result = list(stations.values())
        self.last_station_catalog_stats = {
            "catalog_complete": bool(result),
            "europe_catalog_version": 3,
            "station_count": len(result),
            "regional_source": "europe_redgae",
        }
        return result


class NSGINetherlandsProvider(DirectoryListingRegionalProvider):
    """Official Dutch NSGI/Kadaster AGRS.NL + NETPOS archive."""

    name = "nsgi_nl"
    data_network = "europe"
    base = "https://gnss-data.kadaster.nl/data/daily"
    station_catalog_source = "https://gnss-data.kadaster.nl/info/current_metadata.html"
    source_type = "official_nsgi"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, station_metadata=True)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        if _sampling_code(request.sampling) not in {"30S", ""}:
            return []
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            directory = f"{self.base}/{day.year}/{datetime_to_doy(day):03d}/"
            for station in request.stations or []:
                found = await self._files_from_directory(
                    url=directory,
                    request=request,
                    station=station,
                    day=day,
                    metadata={
                        "regional_archive": "NSGI / Kadaster",
                        "regional_source": "europe_nsgi",
                    },
                )
                files.extend(item for item in found if _is_daily_observation_name(item.filename))
        return files

    async def fetch_station_catalog(self) -> list[Station]:
        text = await self._get_text(self.station_catalog_source)
        stations: list[Station] = []
        for station_id, site_name, networks, latitude, longitude, height in _nsgi_coordinate_rows(text):
            # The NSGI metadata page also contains Caribbean stations. Europe keeps
            # only the NLD identifiers here; BES/ABW can be exposed separately later.
            if not station_id.endswith("NLD"):
                continue
            station_networks = [item for item in re.split(r"[+,/]", networks) if item]
            stations.append(
                Station(
                    id=station_id,
                    marker_name=site_name or station_id[:4],
                    latitude=latitude,
                    longitude=longitude,
                    height=height,
                    country="NLD",
                    network=station_networks or ["NSGI"],
                    data_networks=["europe"],
                    regional_sources=["europe_nsgi"],
                    providers=[self.name],
                    aliases=[station_id[:4]],
                    sampling_rates=["30S"],
                    rinex_versions=["2", "3"],
                    metadata={
                        "catalog_source": self.station_catalog_source,
                        "source_type": self.source_type,
                    },
                )
            )
        self.last_station_catalog_stats = {
            "catalog_complete": bool(stations),
            "europe_catalog_version": 3,
            "station_count": len(stations),
            "regional_source": "europe_nsgi",
        }
        return stations


class APOSAustriaProvider(RegionalLiveProvider):
    """Austria APOS station catalog. APOS-PP files are delivered via BEV Geoportal.

    BEV documents APOS-PP as free and registration-free, but it does not publish a
    stable direct per-file API/URL contract on the public product page.  Keep PLAN
    deterministic: expose the complete official station catalog and return a clear
    actionable error instead of scraping/guessing Geoportal internals.
    """

    name = "apos_at"
    data_network = "europe"
    station_catalog_source = "https://www.bev.gv.at/en/Services/Products/Austrian-POsitioning-Service.html"
    portal_url = "https://data.bev.gv.at/"
    source_type = "official_apos"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, station_metadata=True)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        raise ProviderError(
            "Austria APOS-PP is free and requires no registration, but BEV currently "
            "publishes it through the interactive Geoportal. A stable public direct-file "
            "API has not been verified, so GNSS Go will not guess download URLs. "
            "Use the BEV Geoportal for APOS-PP until a direct endpoint is documented."
        )

    async def fetch_station_catalog(self) -> list[Station]:
        text = await self._get_text(self.station_catalog_source)
        stations: list[Station] = []
        for marker, name, latitude, longitude, height in _apos_coordinate_rows(text):
            stations.append(
                Station(
                    id=f"{marker}00AUT",
                    marker_name=name or marker,
                    latitude=latitude,
                    longitude=longitude,
                    height=height,
                    country="AUT",
                    network=["APOS"],
                    data_networks=["europe"],
                    regional_sources=["europe_apos"],
                    providers=[self.name],
                    aliases=[marker],
                    sampling_rates=["01S", "30S"],
                    rinex_versions=["2", "3"],
                    metadata={
                        "catalog_source": self.station_catalog_source,
                        "source_type": self.source_type,
                        "download_access": "BEV Geoportal (interactive)",
                    },
                )
            )
        self.last_station_catalog_stats = {
            "catalog_complete": bool(stations),
            "europe_catalog_version": 2,
            "station_count": len(stations),
            "regional_source": "europe_apos",
        }
        return stations


class ReNEPPortugalProvider(GLASSRegionalProvider):
    """Portugal ReNEP via the official EPOS-GLASS distribution infrastructure.

    The previous Drupal+recursive-FTP implementation was fragile and is no longer
    used for PLAN.  GLASS is the documented distribution interface used by pyglass.
    """

    name = "renep_pt"
    data_network = "europe"
    glass_bases = (
        "https://glass.epos.ubi.pt/GlassFramework/webresources",
        "https://glass.c4g-pt.eu/GlassFramework/webresources",
        "https://gnssdata-epos.oca.eu/GlassFramework/webresources",
    )
    glass_network = "ReNEP"
    glass_country = "Portugal"
    country_code = "PRT"
    network_label = "ReNEP"
    regional_source = "europe_renep"
    europe_catalog_version = 5
    station_sampling_rates = ["30S"]
    station_rinex_versions = ["2", "3", "4"]



class CACSCanadaProvider(DirectoryListingRegionalProvider):
    """NRCan CACS public daily observation archive.

    The archive uses YYDDD/YYd directories, for example 2026 DOY 001::

        https://cacsa.nrcan.gc.ca/gps/data/gpsdata/26001/26d/

    A directory may contain both modern long-name RINEX 3/4 observation files
    (typically ``*_MO.crx.gz``) and legacy Compact RINEX 2 ``*.26d.*`` files.
    Their station counts are not expected to be identical.  They are treated as
    variants of the same logical station/day observation request:

    * Auto: RINEX 3/4 first, RINEX 2 fallback.
    * RINEX 2: legacy ``d`` only.
    * RINEX 3/4: long-name observation files only.

    The direct YYDDD/YYd archive implemented here is the 30 s daily path.  CACS
    also advertises 1 s data through its web service, but its high-rate archive
    layout is intentionally not guessed here.
    """

    name = "cacs_ca"
    data_network = "canada"
    base = "https://cacsa.nrcan.gc.ca/gps/data/gpsdata"

    station_logs_base = "https://cacsa.nrcan.gc.ca/gps/station_logs/"
    station_logs_mirror = "https://cacsb.nrcan.gc.ca/gps/station_logs/"
    station_catalog_source = station_logs_base

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            observations=True,
            navigation=False,
            station_metadata=True,
            authentication_required=False,
        )

    def _daily_directory(self, day: date) -> str:
        yy = day.year % 100
        doy = datetime_to_doy(day)
        return f"{self.base}/{yy:02d}{doy:03d}/{yy:02d}d/"


    async def fetch_station_catalog(self) -> list[Station]:
        """Build the Canada catalog from NRCan's official station-log archive.

        The observation YYDDD/YYd directory is an availability index and does not
        contain coordinates.  The station-log archive provides the station identity
        and approximate ITRF XYZ needed for map display.  It is therefore the
        authoritative metadata source for the GUI station catalog.
        """
        listing_url = self.station_logs_base
        listing = await self._listing(listing_url)
        if not listing:
            listing_url = self.station_logs_mirror
            listing = await self._listing(listing_url)
        names = [
            name
            for name in parse_listing_filenames(listing)
            if _is_cacs_station_log_filename(name)
        ]
        # A site may have archived/duplicate log names. Keep the newest-looking file
        # per station marker where the filename exposes a marker, while still letting
        # the parser resolve files whose naming is less conventional.
        names = _dedupe_cacs_log_names(names)

        semaphore = asyncio.Semaphore(12)

        async def fetch_one(name: str):
            async with semaphore:
                url = urljoin(listing_url.rstrip('/') + '/', name)
                text = await self._get_text(url)
                station = _parse_cacs_station_log(text, url=url)
                return station

        results = await asyncio.gather(
            *(fetch_one(name) for name in names),
            return_exceptions=True,
        )
        stations: dict[str, Station] = {}
        failed = 0
        for item in results:
            if isinstance(item, Exception):
                failed += 1
                continue
            if item is None:
                failed += 1
                continue
            stations[item.id.upper()] = item

        # Cross-check station IDs against the newest recent daily directory we can
        # discover without downloading observation bodies.  This does not create
        # coordinate-less map stations; it only records how many current archive
        # stations were represented by metadata.
        active_ids: set[str] = set()
        active_day: str | None = None
        today = date.today()
        for offset in range(0, 14):
            day = today - timedelta(days=offset)
            try:
                daily_listing = await self._listing(self._daily_directory(day))
            except Exception:
                continue
            daily_names = parse_listing_filenames(daily_listing)
            ids = _cacs_station_ids_from_listing(daily_names, day)
            if ids:
                active_ids = ids
                active_day = day.isoformat()
                break

        catalog_ids = {station.legacy_id for station in stations.values()}
        self.last_station_catalog_stats = {
            "station_log_files": len(names),
            "station_logs_parsed": len(stations),
            "station_logs_failed": failed,
            "active_archive_day": active_day or "",
            "active_archive_stations": len(active_ids),
            "active_archive_with_metadata": len(active_ids & catalog_ids),
            "active_archive_missing_metadata": len(active_ids - catalog_ids),
        }
        return sorted(stations.values(), key=lambda station: station.id)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        sampling = _sampling_code(request.sampling)
        # The public YYDDD/YYd directory is the daily 30 s archive.  Do not infer
        # a 1 s path merely because the CACS web UI advertises high-rate data.
        if sampling not in {"", "30S"}:
            return []

        results: list[RemoteFile] = []
        stats: dict[str, dict[str, int]] = {}
        for day in request.date_range.days():
            directory = self._daily_directory(day)
            listing = await self._listing(directory)
            names = parse_listing_filenames(listing)
            stats[day.isoformat()] = _cacs_listing_stats(names, day)
            for station in request.stations or []:
                candidates = _cacs_candidates(
                    provider=self.name,
                    directory=directory,
                    names=names,
                    station=station,
                    day=day,
                    request=request,
                )
                if not candidates:
                    continue
                primary, alternates = _cacs_choose_variants(candidates, request)
                if primary is None:
                    continue
                primary.fallback_candidates = alternates
                results.append(primary)

        self.last_discovery_stats = stats
        return results


class CHAINCanadaProvider(DirectoryListingRegionalProvider):
    """University of New Brunswick CHAIN GNSS/GPS archive.

    CHAIN currently exposes two public observation trees under the same web archive:

    * ``/data/gnss`` — multi-constellation RINEX 3.03 data.
    * ``/data/gps``  — legacy GPS RINEX 2.11 data.

    The documented cadence is daily 30 s and high-rate 1 s.  For ``rinex=auto``
    GNSS/RINEX 3 is preferred when present and the same station/day RINEX 2 file
    is retained as an internal fallback.  Explicit RINEX requests never silently
    cross families.  RINEX 4 is not advertised by CHAIN and is therefore not
    fabricated here.
    """

    name = "chain_ca"
    data_network = "canada"
    archive_root = "https://www.chain-project.net/data"
    gps_base = f"{archive_root}/gps/data"
    gnss_base = f"{archive_root}/gnss/data"
    stations_url = "https://chain-new.chain-project.net/index.php/stations"
    download_info_url = (
        "https://chain-new.chain-project.net/index.php/data-products/data-download"
    )
    legacy_download_page = "http://chain.physics.unb.ca/chain/pages/data_download"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, station_metadata=True)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        rinex_request = str(request.rinex)
        if rinex_request == "4":
            # The current public CHAIN archive identifies the modern tree as RINEX 3.03.
            return []

        results: list[RemoteFile] = []
        sampling = _sampling_code(request.sampling)
        for day in request.date_range.days():
            for station in request.stations or []:
                modern: list[RemoteFile] = []
                legacy: list[RemoteFile] = []

                if rinex_request in {"auto", "3"}:
                    modern = await self._chain_modern_files(
                        request=request,
                        station=station,
                        day=day,
                        sampling=sampling,
                    )
                if rinex_request in {"auto", "2"}:
                    legacy = await self._chain_legacy_files(
                        request=request,
                        station=station,
                        day=day,
                        sampling=sampling,
                    )

                if rinex_request == "3":
                    results.extend(modern)
                    continue
                if rinex_request == "2":
                    results.extend(legacy)
                    continue

                primary, alternates = _chain_choose_variants(modern, legacy)
                if primary is not None:
                    primary.fallback_candidates = alternates
                    results.append(primary)

        return _dedupe_remote_files(results)

    async def _chain_modern_files(
        self,
        *,
        request: ObservationRequest,
        station: str,
        day: date,
        sampling: str,
    ) -> list[RemoteFile]:
        year, doy = day.year, datetime_to_doy(day)
        kind = "highrate" if sampling == "01S" else "daily"
        directory = f"{self.gnss_base}/{kind}/{year}/{doy:03d}/"
        files = await self._files_from_directory(
            url=directory,
            request=request,
            station=station,
            day=day,
            metadata={
                "regional_archive": "CHAIN",
                "chain_dataset": "GNSS_RINEX3",
                "rinex_family": "3",
                "sampling": sampling,
                "logical_id": f"obs:{station.upper()}:{day.isoformat()}:{sampling}",
            },
        )
        return [item for item in files if _is_chain_modern_observation(item.filename)]

    async def _chain_legacy_files(
        self,
        *,
        request: ObservationRequest,
        station: str,
        day: date,
        sampling: str,
    ) -> list[RemoteFile]:
        year, doy = day.year, datetime_to_doy(day)
        if sampling == "01S":
            directories = [f"{self.gps_base}/highrate/{year}/{doy:03d}/"]
        elif sampling in {"", "30S"}:
            yy = year % 100
            # Prefer Hatanaka-compressed observation data. Keep the ordinary RINEX
            # observation directory as a same-provider fallback when present.
            directories = [
                f"{self.gps_base}/daily/{year}/{doy:03d}/{yy:02d}d/",
                f"{self.gps_base}/daily/{year}/{doy:03d}/{yy:02d}o/",
            ]
        else:
            return []

        files: list[RemoteFile] = []
        for directory in directories:
            found = await self._files_from_directory(
                url=directory,
                request=request,
                station=station,
                day=day,
                metadata={
                    "regional_archive": "CHAIN",
                    "chain_dataset": "GPS_RINEX2",
                    "rinex_family": "2",
                    "sampling": sampling or "30S",
                    "logical_id": f"obs:{station.upper()}:{day.isoformat()}:{sampling or '30S'}",
                },
            )
            files.extend(found)
        return _dedupe_remote_files(files)

    async def fetch_station_catalog(self) -> list[Station]:
        text = await self._get_text(self.stations_url)
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.IGNORECASE | re.DOTALL)
        stations: list[Station] = []
        for row in rows:
            cells = [
                _clean_html(cell)
                for cell in re.findall(
                    r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL
                )
            ]
            if len(cells) < 4:
                continue
            name = cells[0].strip()
            abbr = cells[1].strip().upper()
            if not re.fullmatch(r"[A-Z0-9]{2,4}", abbr):
                continue
            try:
                latitude = float(cells[2])
                longitude = float(cells[3])
            except ValueError:
                # The public table also contains ionosonde-only rows. They do not
                # have geographic columns in the GNSS row layout and are skipped.
                continue
            if longitude > 180:
                longitude -= 360

            instrument = cells[4] if len(cells) > 4 else ""
            model = cells[5] if len(cells) > 5 else ""
            status = cells[6] if len(cells) > 6 else ""
            realtime_status = cells[7] if len(cells) > 7 else ""
            complete_status = cells[8] if len(cells) > 8 else ""
            station = Station(
                id=abbr,
                marker_name=abbr,
                latitude=latitude,
                longitude=longitude,
                country="CAN",
                network=["CHAIN"],
                data_networks=["canada"],
                regional_sources=["chain_ca"],
                providers=[self.name],
                sampling_rates=["01S", "30S"],
                rinex_versions=["2", "3"],
                aliases=[abbr, abbr.lower()],
                data_availability=complete_status or realtime_status or status or None,
                metadata={
                    "catalog_source": self.stations_url,
                    "source_station_id": abbr,
                    "station_name": name,
                    "instrument": instrument,
                    "receiver_model": model,
                    "status": status,
                    "real_time_status": realtime_status,
                    "complete_data_availability": complete_status,
                    "download_info": self.download_info_url,
                    "legacy_download_page": self.legacy_download_page,
                },
            )
            stations.append(station)
        return _dedupe_stations(stations)


class SatRefHKProvider(DirectoryListingRegionalProvider):
    name = "satref_hk"
    data_network = "hong_kong"
    regional_source = "hongkong_satref"
    base = "https://rinex.geodetic.gov.hk/rinex3"
    local_catalog = Path(__file__).resolve().parent.parent / "resources" / "HONG_KONG_SATREF_stations.csv"
    station_catalog_source = "https://www.geodetic.gov.hk/common/data/pdf/SatRef_Coord.pdf"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, station_metadata=True)

    def bundled_station_catalog(self) -> list[Station]:
        if not self.local_catalog.exists():
            return []
        stations: list[Station] = []
        with self.local_catalog.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                code = str(row.get("station") or "").strip().upper()
                if not re.fullmatch(r"[A-Z0-9]{4}", code):
                    continue
                try:
                    latitude = float(row.get("latitude") or "")
                    longitude = float(row.get("longitude") or "")
                except (TypeError, ValueError):
                    continue
                try:
                    height = float(row.get("height") or "")
                except (TypeError, ValueError):
                    height = None
                name = str(row.get("name") or code).strip()
                stations.append(
                    Station(
                        id=f"{code}00HKG",
                        marker_name=code,
                        latitude=latitude,
                        longitude=longitude,
                        height=height,
                        country="HKG",
                        network=["SatRef"],
                        data_networks=["hong_kong"],
                        regional_sources=[self.regional_source],
                        providers=[self.name],
                        sampling_rates=["01S", "05S", "30S"],
                        rinex_versions=["2", "3"],
                        aliases=[code],
                        metadata={
                            "station_name": name,
                            "catalog_source": self.station_catalog_source,
                            "bundled_coordinate_snapshot": "2024-12-05",
                        },
                        data_availability="Hong Kong, China SatRef RINEX web/HTTPS service",
                    )
                )
        return stations

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        sampling = _sampling_code(request.sampling)
        sample_directory = {"01S": "1s", "05S": "5s", "30S": "30s"}.get(sampling, "30s")
        for day in request.date_range.days():
            year, doy = day.year, datetime_to_doy(day)
            for station in request.stations or []:
                station_dir = station[:4].lower()
                directory = f"{self.base}/{year}/{doy:03d}/{station_dir}/{sample_directory}/"
                files.extend(
                    await self._files_from_directory(
                        url=directory,
                        request=request,
                        station=station,
                        day=day,
                        metadata={"regional_archive": "SatRef"},
                    )
                )
        return files

    async def fetch_station_catalog(self) -> list[Station]:
        # Always begin with the packaged official coordinate snapshot so Hong Kong,
        # China is fully mapped immediately, including when the user is offline.
        bundled = {station.id[:4].upper(): station for station in self.bundled_station_catalog()}

        # The current RINEX directory is still checked in the background. It can add
        # newly published station IDs without removing the packaged coordinates.
        listing = ""
        for days_back in range(0, 8):
            candidate = date.today() - timedelta(days=days_back)
            year, doy = candidate.year, datetime_to_doy(candidate)
            try:
                listing = await self._get_text(f"{self.base}/{year}/{doy:03d}/")
            except Exception:
                listing = ""
            if listing:
                break
        station_dirs = [
            name.lower()
            for name in parse_listing_filenames(listing)
            if re.fullmatch(r"[a-zA-Z0-9]{4}", name)
        ]
        for station in station_dirs:
            code = station.upper()
            if code in bundled:
                continue
            bundled[code] = Station(
                id=f"{code}00HKG",
                marker_name=code,
                country="HKG",
                network=["SatRef"],
                data_networks=["hong_kong"],
                regional_sources=[self.regional_source],
                providers=[self.name],
                sampling_rates=["01S", "05S", "30S"],
                rinex_versions=["2", "3"],
                aliases=[code],
                metadata={"catalog_source": "rinex3 day directory"},
            )
        return list(bundled.values())


def _is_us_noaa_cors_row(lat: float, lon: float, agency: str = "") -> bool:
    """Keep the NOAA map export scoped to the United States/territories.

    NOAA's CORS map export also contains a small number of cooperative foreign
    stations (Canada, Mexico, IGS sites in Africa/Asia/Oceania).  The GUI source
    is explicitly ``North America -> United States -> NOAA CORS``, so those
    international rows must not appear as bogus USA markers.  The bounding
    regions cover CONUS, Alaska/Aleutians, Hawaii, Puerto Rico/USVI, Guam/CNMI,
    American Samoa, Wake and Midway.  Two nearby foreign operator groups that
    fall inside those broad boxes are excluded by agency.
    """
    agency_l = (agency or "").casefold()
    if "natural resources canada" in agency_l:
        return False
    if "instituto nacional de estadistica" in agency_l and "mexico" in agency_l:
        return False
    regions = (
        24.0 <= lat <= 50.0 and -125.0 <= lon <= -66.0,  # CONUS
        50.0 <= lat <= 72.0 and (-180.0 <= lon <= -129.0 or 170.0 <= lon <= 180.0),
        18.0 <= lat <= 23.0 and -161.0 <= lon <= -154.0,  # Hawaii
        17.0 <= lat <= 19.8 and -68.5 <= lon <= -64.0,    # Puerto Rico / USVI
        12.0 <= lat <= 21.0 and 143.0 <= lon <= 146.5,    # Guam / CNMI
        -15.0 <= lat <= -10.0 and -172.0 <= lon <= -168.0, # American Samoa
        18.0 <= lat <= 21.0 and 165.0 <= lon <= 168.0,     # Wake Island
        27.0 <= lat <= 29.5 and -178.5 <= lon <= -176.0,  # Midway
    )
    return any(regions)


class NOAANCNProvider(DirectoryListingRegionalProvider):
    name = "noaa_ncn"
    data_network = "united_states"
    base = "https://geodesy.noaa.gov/corsdata/rinex"
    aws_base = "https://noaa-cors-pds.s3.amazonaws.com/rinex"
    station_list = "https://geodesy.noaa.gov/CORS/dates_sites.txt"
    coordinate_list = "https://geodesy.noaa.gov/corsdata/coord/coord_20/itrf2020_geo.comp.txt"
    local_catalog = Path(__file__).resolve().parent.parent / "resources" / "NOAA_CORS_Network.csv"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, navigation=False, station_metadata=True)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        # NOAA documents permanent daily NCN RINEX 2 on both the public NGS
        # server and the NOAA Open Data AWS bucket. The user's proven script uses
        # exactly this YYYY/DDD/ssss/ssssDDD0.YYd.gz layout.
        if str(request.rinex) in {"3", "4"}:
            return []
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            year, doy = day.year, datetime_to_doy(day)
            yy = year % 100
            for station in request.stations or []:
                station4 = station[:4].lower()
                compact = f"{station4}{doy:03d}0.{yy:02d}d.gz"
                plain = f"{station4}{doy:03d}0.{yy:02d}o.gz"

                def remote(base: str, filename: str, source: str) -> RemoteFile:
                    return _remote(
                        self.name,
                        f"{base}/{year}/{doy:03d}/{station4}/{filename}",
                        filename,
                        station=station[:9].upper(),
                        day=day,
                        metadata={
                            "regional_archive": "NOAA National CORS Network",
                            "regional_source": "usa_noaa_cors",
                            "download_source": source,
                            "data_network": "united_states",
                        },
                    )

                # Keep the user's already-proven NGS URL as the primary path.
                # NOAA Open Data on AWS is only a fallback mirror so the new
                # provider preserves the workflow that was already known to work.
                primary = remote(self.base, compact, "NOAA NGS")
                primary.fallback_candidates = [
                    remote(self.aws_base, compact, "NOAA Open Data on AWS"),
                    remote(self.base, plain, "NOAA NGS"),
                    remote(self.aws_base, plain, "NOAA Open Data on AWS"),
                ]
                files.append(primary)
        return files

    def _local_station_catalog(self) -> list[Station]:
        if not self.local_catalog.exists():
            return []
        stations: list[Station] = []
        with self.local_catalog.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                station_id = str(row.get("SITEID") or "").strip().upper()
                if not re.fullmatch(r"[A-Z0-9]{4}", station_id):
                    continue
                try:
                    lon = float(row.get("x") or "")
                    lat = float(row.get("y") or "")
                except (TypeError, ValueError):
                    continue
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                agency = str(row.get("AGENCY") or "").strip()
                if not _is_us_noaa_cors_row(lat, lon, agency):
                    continue
                sampling = str(row.get("SAMPLING") or "").strip()
                sampling_rates = [f"{int(float(sampling)):02d}S"] if sampling else ["30S"]
                constellations = [
                    token.strip().upper()
                    for token in str(row.get("GNSS") or "").split("+")
                    if token.strip()
                ]
                metadata = {
                    "agency": agency,
                    "status": str(row.get("STATUS") or "").strip(),
                    "availability": str(row.get("AVAIL") or "").strip(),
                    "start_date": str(row.get("START_DATE") or "").strip(),
                    "site_info": str(row.get("Site Info") or "").strip(),
                    "data_availability_url": str(row.get("Data Availability") or "").strip(),
                    "station_log_url": str(row.get("SiteLog") or "").strip(),
                    "catalog_source": str(self.local_catalog),
                    "map_source": "NOAA CORS Network ArcGIS export",
                }
                stations.append(
                    Station(
                        id=f"{station_id}00USA",
                        marker_name=station_id,
                        latitude=lat,
                        longitude=lon,
                        country="USA",
                        network=["NOAA NCN"],
                        data_networks=["united_states"],
                        regional_sources=["usa_noaa_cors"],
                        providers=[self.name],
                        sampling_rates=sampling_rates,
                        rinex_versions=["2"],
                        constellations=constellations,
                        aliases=[station_id, station_id.lower()],
                        data_availability=str(row.get("STATUS") or "").strip() or None,
                        metadata=metadata,
                    )
                )
        return _dedupe_stations(stations)

    async def fetch_station_catalog(self) -> list[Station]:
        # Prefer the user's NOAA ArcGIS CSV export: it already contains the map
        # coordinates, station status, sampling, constellation and agency fields
        # for the complete NCN view and avoids a second online coordinate join.
        # Production uses the bundled official-map export. Explicit AsyncClient
        # injection is kept as a deterministic hook for the legacy live-parser
        # tests/integrations.
        if not isinstance(self.client, httpx.AsyncClient):
            local = self._local_station_catalog()
            if local:
                self.last_station_catalog_stats = {
                    "catalog_complete": True,
                    "station_count": len(local),
                    "mapped_station_count": len(local),
                    "catalog_source_used": str(self.local_catalog),
                    "catalog_scope": "United States and U.S. territories",
                }
                return local

        text, coordinate_text = await asyncio.gather(
            self._get_text(self.station_list),
            self._get_text(self.coordinate_list),
            return_exceptions=True,
        )
        if isinstance(text, Exception):
            raise text
        coordinates = {} if isinstance(coordinate_text, Exception) else _parse_noaa_geodetic_coordinates(coordinate_text)
        stations: list[Station] = []
        for raw in text.splitlines():
            parts = raw.split()
            if len(parts) < 9:
                continue
            cc, state = parts[0].upper(), parts[1].upper()
            if cc != "US":
                continue
            station_id = _extract_ncn_station_id(parts)
            if not station_id:
                continue
            status = parts[-1]
            lat, lon, height = coordinates.get(station_id.upper(), (None, None, None))
            stations.append(
                Station(
                    id=station_id,
                    marker_name=station_id,
                    latitude=lat,
                    longitude=lon,
                    height=height,
                    country="USA",
                    network=["NOAA NCN"],
                    data_networks=["united_states"],
                    regional_sources=["usa_noaa_cors"],
                    providers=[self.name],
                    sampling_rates=["30S"],
                    rinex_versions=["2"],
                    aliases=[station_id.lower()],
                    data_availability=status,
                    metadata={
                        "state_or_region": state,
                        "catalog_source": self.station_list,
                        "coordinate_source": self.coordinate_list if lat is not None else "",
                    },
                )
            )
        return _dedupe_stations(stations)



def _parse_noaa_geodetic_coordinates(text: str) -> dict[str, tuple[float, float, float | None]]:
    result: dict[str, tuple[float, float, float | None]] = {}
    dms = re.compile(
        r"^\s*(?P<site>[A-Za-z0-9]{4})\s+\S+\s+"
        r"(?P<latd>\d{1,2})\s+(?P<latm>\d{1,2})\s+(?P<lats>[\d.]+)\s*(?P<lath>[NS])\s+"
        r"(?P<lond>\d{1,3})\s+(?P<lonm>\d{1,2})\s+(?P<lons>[\d.]+)\s*(?P<lonh>[EW])\s+"
        r"(?P<h>[-+\d.]+)",
        re.I,
    )
    decimal = re.compile(
        r"^\s*(?P<site>[A-Za-z0-9]{4})\s+\S+\s+"
        r"(?P<lat>[-+]?\d{1,2}(?:\.\d+)?)\s+"
        r"(?P<lon>[-+]?\d{1,3}(?:\.\d+)?)\s+"
        r"(?P<h>[-+\d.]+)"
    )
    for line in text.splitlines():
        match = dms.search(line)
        if match:
            lat = float(match["latd"]) + float(match["latm"]) / 60 + float(match["lats"]) / 3600
            lon = float(match["lond"]) + float(match["lonm"]) / 60 + float(match["lons"]) / 3600
            if match["lath"].upper() == "S": lat = -lat
            if match["lonh"].upper() == "W": lon = -lon
            result[match["site"].upper()] = (lat, lon, float(match["h"]))
            continue
        match = decimal.search(line)
        if match:
            lat, lon = float(match["lat"]), float(match["lon"])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                result[match["site"].upper()] = (lat, lon, float(match["h"]))
    return result


def _is_chain_modern_observation(filename: str) -> bool:
    lower = filename.lower()
    if not lower.endswith((".crx.gz", ".rnx.gz", ".crx", ".rnx")):
        return False
    # Modern CHAIN data are multi-constellation RINEX 3.03.  Observation long
    # names normally carry MO; keep generic long-name observation files too.
    info = parse_rinex_filename(filename)
    return info.file_type == "observation" and info.rinex_version_family == "3/4"


def _chain_choose_variants(
    modern: list[RemoteFile],
    legacy: list[RemoteFile],
) -> tuple[RemoteFile | None, list[RemoteFile]]:
    modern = _dedupe_remote_files(modern)
    legacy = _dedupe_remote_files(legacy)
    if modern:
        primary = sorted(modern, key=lambda item: item.filename)[0]
        alternates = [
            *[item for item in modern if item.filename != primary.filename],
            *legacy,
        ]
        return primary, alternates
    if legacy:
        primary = sorted(legacy, key=lambda item: (
            0 if re.search(r"\.\d{2}d(?:\.|$)", item.filename.lower()) else 1,
            item.filename,
        ))[0]
        alternates = [item for item in legacy if item.filename != primary.filename]
        return primary, alternates
    return None, []


def _is_cacs_station_log_filename(name: str) -> bool:
    lower = name.lower()
    return lower.endswith('.log') or lower.endswith('.txt')


def _dedupe_cacs_log_names(names: list[str]) -> list[str]:
    # Prefer lexicographically later names for the same four-character marker;
    # common IGS station-log revisions contain a date/revision suffix.
    by_marker: dict[str, str] = {}
    passthrough: list[str] = []
    for name in sorted(set(names)):
        match = re.match(r'([A-Za-z0-9]{4})', name)
        if not match:
            passthrough.append(name)
            continue
        marker = match.group(1).upper()
        current = by_marker.get(marker)
        if current is None or name > current:
            by_marker[marker] = name
    return sorted(by_marker.values()) + sorted(passthrough)


def _parse_cacs_station_log(text: str, *, url: str) -> Station | None:
    if not text.strip():
        return None

    def field(patterns: list[str]) -> str | None:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
            if match:
                value = match.group(1).strip()
                if value:
                    return value
        return None

    marker = field([
        r'Four\s+Character\s+ID\s*[:=]\s*([A-Z0-9]{4})',
        r'Marker\s+Name\s*[:=]\s*([A-Z0-9]{4})',
    ])
    nine = field([
        r'Nine\s+Character\s+ID\s*[:=]\s*([A-Z0-9]{9})',
        r'Nine\s+Char(?:acter)?\s+ID\s*[:=]\s*([A-Z0-9]{9})',
    ])
    if not marker and nine:
        marker = nine[:4]
    if not marker:
        return None
    marker = marker.upper()
    station_id = (nine or f'{marker}00CAN').upper()

    domes = field([r'IERS\s+DOMES\s+(?:Number|No\.?)\s*[:=]\s*([A-Z0-9-]+)'])
    site_name = field([r'Site\s+Name\s*[:=]\s*([^\r\n]+)'])

    def number(axis: str) -> float | None:
        patterns = [
            rf'{axis}\s+coordinate[^:=]*[:=]\s*([-+]?\d+(?:\.\d+)?)',
            rf'Approximate\s+Position.*?{axis}[^:=]*[:=]\s*([-+]?\d+(?:\.\d+)?)',
        ]
        raw = field(patterns)
        try:
            return float(raw) if raw is not None else None
        except ValueError:
            return None

    x, y, z = number('X'), number('Y'), number('Z')
    if x is None or y is None or z is None:
        # Some legacy logs put XYZ on a compact three-number line after the label.
        compact = re.search(
            r'Approximate\s+Position[^\r\n]*[\r\n]+\s*'
            r'([-+]?\d+(?:\.\d+)?)\s+'
            r'([-+]?\d+(?:\.\d+)?)\s+'
            r'([-+]?\d+(?:\.\d+)?)',
            text,
            flags=re.IGNORECASE,
        )
        if compact:
            x, y, z = map(float, compact.groups())
    if x is None or y is None or z is None:
        return None
    if not 5_000_000 <= math.sqrt(x*x + y*y + z*z) <= 7_000_000:
        return None
    latitude, longitude, height = _ecef_to_geodetic(x, y, z)

    aliases = [marker]
    if nine and nine.upper() != station_id:
        aliases.append(nine.upper())
    metadata: dict[str, str | list[str]] = {
        'catalog_source': url,
        'station_log_url': url,
    }
    if site_name:
        metadata['site_name'] = site_name

    return Station(
        id=station_id,
        marker_name=marker,
        domes=domes.upper() if domes else None,
        latitude=latitude,
        longitude=longitude,
        height=height,
        country='CAN',
        network=['CACS/CGS'],
        data_networks=['canada'],
        regional_sources=['cacs_ca'],
        providers=['cacs_ca'],
        sampling_rates=['01S', '30S'],
        rinex_versions=['2', '3', '4'],
        aliases=aliases,
        metadata=metadata,
    )


def _cacs_station_ids_from_listing(names: list[str], day: date) -> set[str]:
    ids: set[str] = set()
    yy = day.year % 100
    doy = datetime_to_doy(day)
    legacy_re = re.compile(rf'^([A-Za-z0-9]{{4}}){doy:03d}[0-9a-x]\.{yy:02d}d', re.I)
    for name in names:
        parsed = parse_rinex_filename(name)
        if parsed is not None and getattr(parsed, 'station', None):
            ids.add(str(parsed.station)[:4].upper())
            continue
        match = legacy_re.match(name)
        if match:
            ids.add(match.group(1).upper())
    return ids

def _cacs_candidates(
    *,
    provider: str,
    directory: str,
    names: list[str],
    station: str,
    day: date,
    request: ObservationRequest,
) -> list[RemoteFile]:
    requested = station.upper()
    requested4 = requested[:4]
    requested9 = requested[:9]
    rinex_request = str(request.rinex)
    sampling = _sampling_code(request.sampling)
    candidates: list[RemoteFile] = []

    for filename in names:
        info = parse_rinex_filename(filename)
        if info.file_type != "observation" or not info.station:
            continue
        if info.year is not None and info.year != day.year:
            continue
        if info.doy is not None and info.doy != datetime_to_doy(day):
            continue
        parsed_station = info.station.upper()
        if parsed_station[:4] != requested4:
            continue
        if len(requested) >= 9 and len(parsed_station) >= 9 and parsed_station != requested9:
            continue

        family = info.rinex_version_family or ""
        if rinex_request == "2" and family != "2":
            continue
        if rinex_request in {"3", "4"} and family != "3/4":
            continue

        # Long-name files encode the actual interval.  Legacy d files in this
        # specific daily archive are treated as the 30 s variant.
        if family == "3/4" and info.interval and sampling and info.interval != sampling:
            continue
        if family == "2" and sampling not in {"", "30S"}:
            continue

        variant = "rinex34" if family == "3/4" else "rinex2"
        actual_sampling = info.interval or "30S"
        remote = _remote(
            provider,
            urljoin(directory.rstrip("/") + "/", filename),
            filename,
            station=requested,
            day=day,
            metadata={
                "source_type": "official_directory_listing",
                "regional_archive": "CACS_CACSA",
                "cacs_variant": variant,
                "rinex_family": family,
                "sampling": actual_sampling,
                "duration": info.duration or "01D",
                "archive_station": parsed_station,
                # Both filename families represent the same logical observation.
                "logical_id": f"obs:{requested}:{day.isoformat()}:{actual_sampling}",
            },
        )
        candidates.append(remote)

    return _dedupe_remote_files(candidates)


def _cacs_choose_variants(
    candidates: list[RemoteFile],
    request: ObservationRequest,
) -> tuple[RemoteFile | None, list[RemoteFile]]:
    if not candidates:
        return None, []

    rinex_request = str(request.rinex)

    def score(remote: RemoteFile) -> tuple[int, int, str]:
        family = remote.metadata.get("rinex_family", "")
        archive_station = remote.metadata.get("archive_station", "")
        requested = remote.station or ""
        # For a 4-char request, prefer the conventional XXXX00CAN long ID if it exists.
        station_score = 0
        if len(requested) >= 9 and archive_station == requested[:9].upper():
            station_score = -2
        elif archive_station == f"{requested[:4].upper()}00CAN":
            station_score = -1

        if rinex_request == "auto":
            family_score = 0 if family == "3/4" else 10
        elif rinex_request == "2":
            family_score = 0 if family == "2" else 10
        else:
            family_score = 0 if family == "3/4" else 10
        return family_score, station_score, remote.filename

    ordered = sorted(candidates, key=score)
    return ordered[0], ordered[1:]


def _cacs_listing_stats(names: list[str], day: date) -> dict[str, int]:
    rinex34: set[str] = set()
    rinex2: set[str] = set()
    for filename in names:
        info = parse_rinex_filename(filename)
        if info.file_type != "observation" or not info.station:
            continue
        if info.year is not None and info.year != day.year:
            continue
        if info.doy is not None and info.doy != datetime_to_doy(day):
            continue
        station4 = info.station[:4].upper()
        if info.rinex_version_family == "3/4":
            rinex34.add(station4)
        elif info.rinex_version_family == "2":
            rinex2.add(station4)
    union = rinex34 | rinex2
    overlap = rinex34 & rinex2
    return {
        "rinex34_stations": len(rinex34),
        "rinex2_stations": len(rinex2),
        "overlap_stations": len(overlap),
        "rinex34_only": len(rinex34 - rinex2),
        "rinex2_only": len(rinex2 - rinex34),
        "unique_stations": len(union),
    }






def _glass_records(payload: object) -> list[dict[str, object]]:
    """Normalize the slightly different JSON envelopes returned by GLASS nodes."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    features = payload.get("features")
    if isinstance(features, list):
        result: list[dict[str, object]] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties")
            row: dict[str, object] = dict(props) if isinstance(props, dict) else {}
            geometry = feature.get("geometry")
            if isinstance(geometry, dict):
                row["geometry"] = geometry
            if feature.get("id") is not None:
                row.setdefault("id", feature.get("id"))
            result.append(row)
        return result
    for key in ("items", "data", "stations", "files", "results", "records", "content"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _glass_records(value)
            if nested:
                return nested
    return [payload] if payload else []


def _glass_flat(record: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}

    def walk(value: object) -> None:
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            norm = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if norm and not isinstance(item, (dict, list)):
                out.setdefault(norm, item)
            elif norm and isinstance(item, list) and all(not isinstance(v, (dict, list)) for v in item):
                out.setdefault(norm, ",".join(str(v) for v in item))
            if isinstance(item, dict):
                walk(item)

    walk(record)
    return out


def _glass_string(record: dict[str, object], *keys: str) -> str:
    flat = _glass_flat(record)
    for key in keys:
        norm = re.sub(r"[^a-z0-9]", "", key.lower())
        value = flat.get(norm)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _glass_float(record: dict[str, object], *keys: str) -> float | None:
    value = _glass_string(record, *keys)
    if not value:
        return None
    try:
        return float(value.replace(",", "."))
    except (TypeError, ValueError):
        return None


def _glass_networks(record: dict[str, object]) -> list[str]:
    result: list[str] = []
    for key in ("network", "networks"):
        value = record.get(key)
        if isinstance(value, str):
            result.extend(part.strip() for part in re.split(r"[,;+]", value) if part.strip())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    name = _glass_string(item, "name", "network", "acronym", "code")
                    if name:
                        result.append(name)
                elif str(item).strip():
                    result.append(str(item).strip())
    if not result:
        text = _glass_string(record, "network", "networks", "network_name", "networkname")
        if text:
            result.extend(part.strip() for part in re.split(r"[,;+]", text) if part.strip())
    return list(dict.fromkeys(result))


def _glass_station_fields(
    record: dict[str, object], *, forced_country: str = ""
) -> tuple[str, str, float, float, float | None, list[str], str] | None:
    marker_text = _glass_string(
        record,
        "marker_long_name", "markerlongname", "long_marker", "marker9",
        "nine_char_id", "ninecharid", "station_id", "stationid", "marker",
    ).upper()
    if not marker_text:
        marker_text = _glass_string(record, "id").upper()
    match9 = re.search(r"(?:^|[^A-Z0-9])([A-Z0-9]{4}[0-9A-Z]{2}[A-Z]{3})(?:$|[^A-Z0-9])", marker_text)
    if match9:
        station_id = match9.group(1)
        marker4 = station_id[:4]
    else:
        match4 = re.search(r"(?:^|[^A-Z0-9])([A-Z0-9]{4})(?:$|[^A-Z0-9])", marker_text)
        if not match4 or not forced_country:
            return None
        marker4 = match4.group(1)
        station_id = f"{marker4}00{forced_country}"

    latitude = _glass_float(record, "latitude", "lat")
    longitude = _glass_float(record, "longitude", "lon", "lng", "long")
    height = _glass_float(record, "altitude", "height", "elevation", "ellipsoidal_height")
    geometry = record.get("geometry")
    if (latitude is None or longitude is None) and isinstance(geometry, dict):
        coords = geometry.get("coordinates")
        if isinstance(coords, list) and len(coords) >= 2:
            try:
                longitude = float(coords[0])
                latitude = float(coords[1])
                if len(coords) >= 3 and height is None:
                    height = float(coords[2])
            except (TypeError, ValueError):
                pass
    if latitude is None or longitude is None:
        x = _glass_float(record, "x", "x_coordinate", "xcoordinate", "ecef_x", "ecefx")
        y = _glass_float(record, "y", "y_coordinate", "ycoordinate", "ecef_y", "ecefy")
        z = _glass_float(record, "z", "z_coordinate", "zcoordinate", "ecef_z", "ecefz")
        if x is not None and y is not None and z is not None:
            radius = math.sqrt(x*x + y*y + z*z)
            if 5_000_000 <= radius <= 7_000_000:
                latitude, longitude, xyz_height = _ecef_to_geodetic(x, y, z)
                if height is None:
                    height = xyz_height
    if latitude is None or longitude is None:
        return None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None

    country = _glass_string(record, "country_code", "countrycode", "country").upper()
    if len(country) != 3:
        country = forced_country or station_id[-3:]
    return station_id, marker4, latitude, longitude, height, _glass_networks(record), country


def _scan_glass_url(value: object) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        lower = candidate.lower()
        if candidate.startswith(("http://", "https://", "ftp://")) and any(
            token in lower for token in (".rnx", ".crx", ".gz", ".z", ".zip", "rinex")
        ):
            return candidate
        return ""
    if isinstance(value, dict):
        for item in value.values():
            found = _scan_glass_url(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _scan_glass_url(item)
            if found:
                return found
    return ""


def _glass_file_url(record: dict[str, object]) -> str:
    flat = _glass_flat(record)
    for key in (
        "url", "rinex_url", "rinexurl", "download_url", "downloadurl",
        "file_url", "fileurl", "uri", "href", "link",
    ):
        norm = re.sub(r"[^a-z0-9]", "", key.lower())
        value = flat.get(norm)
        if isinstance(value, str) and value.lower().startswith(("http://", "https://", "ftp://")):
            return value.strip()
    return _scan_glass_url(record)


def _glass_file_name(record: dict[str, object], *, url: str = "") -> str:
    name = _glass_string(
        record, "file_name", "filename", "rinex_file", "rinexfilename", "name"
    )
    if name and ("." in name or "_" in name):
        return PurePosixPath(name.split("?", 1)[0]).name
    if url:
        return PurePosixPath(url.split("?", 1)[0]).name
    return ""


def _glass_station_id_from_file_record(record: dict[str, object]) -> str:
    value = _glass_string(
        record,
        "marker_long_name", "markerlongname", "station_id", "stationid",
        "station_marker", "stationmarker", "marker",
    ).upper()
    match = re.search(r"([A-Z0-9]{4}[0-9A-Z]{2}[A-Z]{3})", value)
    if match:
        return match.group(1)
    return value[:4] if re.fullmatch(r"[A-Z0-9]{4}", value) else ""


def _glass_file_day(record: dict[str, object], filename: str) -> date | None:
    info = parse_rinex_filename(filename)
    if info.year is not None and info.doy is not None:
        try:
            return date(int(info.year), 1, 1) + timedelta(days=int(info.doy) - 1)
        except ValueError:
            pass
    raw = _glass_string(
        record, "reference_date", "referencedate", "date", "file_date", "filedate",
        "start_date", "startdate",
    )
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw) if raw else None
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    year = _glass_string(record, "year")
    doy = _glass_string(record, "doy", "day_of_year", "dayofyear")
    if year.isdigit() and doy.isdigit():
        try:
            return date(int(year), 1, 1) + timedelta(days=int(doy) - 1)
        except ValueError:
            pass
    return None


async def _glass_station_catalog_for(
    provider_obj: RegionalLiveProvider,
    *,
    network: str | None,
    country: str | None,
    country_code: str,
    network_label: str,
    regional_source: str,
    provider_name: str,
) -> list[Station]:
    """Fetch one logical network from current/legacy GLASS station endpoints."""
    bases = ("https://gnssdata-epos.oca.eu/GlassFramework/webresources",)
    urls: list[str] = []
    for base in bases:
        if network:
            net = quote(network, safe=",")
            urls.extend((
                f"{base}/stations/network/{net}/short/json?page=0&perpage=100",
                f"{base}/stations/v2/network/{net}/short/json?page=0&perpage=100",
            ))
        if country:
            c = quote(country)
            urls.extend((
                f"{base}/stations/location/country/{c}/short/json?page=0&perpage=100",
                f"{base}/stations/v2/location/country/{c}/short/json?page=0&perpage=100",
            ))
    errors: list[str] = []
    for url in urls:
        try:
            payload = await provider_obj._get_json(url)
            out: dict[str, Station] = {}
            for record in _glass_records(payload):
                parsed = _glass_station_fields(record, forced_country=country_code)
                if parsed is None:
                    continue
                station_id, marker4, lat, lon, height, networks, record_country = parsed
                if station_id[-3:] != country_code:
                    continue
                if network and networks and not any(n.upper() == network.upper() for n in networks):
                    # Country fallback may contain stations outside the requested
                    # logical network; network endpoint records need no such guess.
                    if "/location/country/" in url:
                        continue
                out[station_id] = Station(
                    id=station_id, marker_name=marker4, latitude=lat, longitude=lon,
                    height=height, country=record_country or country_code,
                    network=list(dict.fromkeys([network_label, *networks])),
                    data_networks=["europe"], regional_sources=[regional_source],
                    providers=[provider_name], aliases=[marker4, station_id],
                    sampling_rates=["30S"], rinex_versions=["2", "3", "4"],
                    metadata={"catalog_source": url, "source_type": "epos_glass_api"},
                )
            if out:
                return list(out.values())
        except (ProviderError, ProviderProtocolError, OSError) as exc:
            errors.append(f"{url}: {exc}")
    if errors:
        raise ProviderError("GLASS station query failed: " + " | ".join(errors))
    return []


def _gref_stations_from_bkg_api(payload: object, *, provider: str) -> list[Station]:
    """Parse the station metadata collection behind BKG's Station List UI."""
    stations: dict[str, Station] = {}
    for record in _glass_records(payload):
        station_id = _glass_string(
            record, "nine_char_id", "ninecharid", "station_id", "stationid", "marker", "id"
        ).upper()
        match = re.search(r"([A-Z0-9]{4}[0-9A-Z]{2}DEU)", station_id)
        if not match:
            continue
        station_id = match.group(1)
        networks = _glass_networks(record)
        # BKG's station collection can expose membership under alternate property names.
        for key in ("network_name", "networkname", "network_acronym", "networkacronym"):
            value = _glass_string(record, key)
            if value:
                networks.extend(part.strip() for part in re.split(r"[,;+]", value) if part.strip())
        networks = list(dict.fromkeys(networks))
        if not any(name.upper() == "GREF" for name in networks):
            continue

        latitude = _glass_float(record, "latitude", "lat")
        longitude = _glass_float(record, "longitude", "lon", "lng", "long")
        height = _glass_float(record, "altitude", "height", "elevation")
        geometry = record.get("geometry")
        if (latitude is None or longitude is None) and isinstance(geometry, dict):
            coords = geometry.get("coordinates")
            if isinstance(coords, list) and len(coords) >= 2:
                try:
                    longitude = float(coords[0]); latitude = float(coords[1])
                    if len(coords) >= 3 and height is None:
                        height = float(coords[2])
                except (TypeError, ValueError):
                    pass
        if latitude is None or longitude is None:
            x = _glass_float(record, "x", "x_coordinate", "xcoordinate", "ecef_x", "ecefx")
            y = _glass_float(record, "y", "y_coordinate", "ycoordinate", "ecef_y", "ecefy")
            z = _glass_float(record, "z", "z_coordinate", "zcoordinate", "ecef_z", "ecefz")
            if x is not None and y is not None and z is not None:
                radius = math.sqrt(x*x + y*y + z*z)
                if 5_000_000 <= radius <= 7_000_000:
                    latitude, longitude, xyz_height = _ecef_to_geodetic(x, y, z)
                    if height is None:
                        height = xyz_height
        if latitude is None or longitude is None:
            continue
        if not (45.0 <= latitude <= 56.5 and 5.0 <= longitude <= 16.0):
            continue
        marker = station_id[:4]
        stations[station_id] = Station(
            id=station_id,
            marker_name=marker,
            latitude=latitude,
            longitude=longitude,
            height=height,
            country="DEU",
            network=list(dict.fromkeys(["GREF", *networks])),
            data_networks=["europe"],
            regional_sources=["europe_gref"],
            providers=[provider],
            aliases=[marker, station_id],
            sampling_rates=["01S", "30S"],
            rinex_versions=["2", "3"],
            metadata={"catalog_source": "BKG Station List API", "source_type": "official_gref"},
        )
    return list(stations.values())


def _html_cells(row_html: str) -> list[str]:
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL)
    result = []
    for cell in cells:
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", cell))
        result.append(" ".join(text.split()))
    return result


def _decimal(value: str) -> float | None:
    text = value.strip().replace(" ", "").replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _rgp_csv_rows(text: str) -> list[tuple[str, float, float, float]]:
    """Parse IGN's coordRGP CSV export without depending on exact column names.

    The export has changed separators/headers over time.  For each row, detect a
    four-character marker and the first physically plausible ECEF XYZ triplet;
    convert that triplet to geodetic coordinates.
    """
    if not text.strip():
        return []
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        rows = csv.reader(io.StringIO(text), dialect)
    except csv.Error:
        rows = csv.reader(io.StringIO(text), delimiter=";")
    result: dict[str, tuple[str, float, float, float]] = {}
    for row in rows:
        if not row:
            continue
        marker = None
        for cell in row[:4]:
            match = re.search(r"\b([A-Z0-9]{4})\b", str(cell).upper())
            if match and match.group(1) not in {"SITE", "CODE", "STAT"}:
                marker = match.group(1)
                break
        if not marker:
            continue
        values: list[float] = []
        for cell in row[1:]:
            value = _decimal(str(cell))
            if value is not None:
                values.append(value)
        xyz = None
        for i in range(max(0, len(values) - 2)):
            x, y, z = values[i:i+3]
            radius = math.sqrt(x*x + y*y + z*z)
            if 5_000_000 <= radius <= 7_000_000:
                xyz = (x, y, z)
                break
        if xyz is None:
            continue
        latitude, longitude, height = _ecef_to_geodetic(*xyz)
        result[marker] = (marker, latitude, longitude, height)
    return list(result.values())


def _rgp_coordinate_rows(html: str) -> list[tuple[str, float, float, float]]:
    records: dict[str, tuple[str, float, float, float]] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL):
        cells = _html_cells(row)
        if len(cells) < 4:
            continue
        marker_match = re.search(r"\b([A-Z0-9]{4})\b", cells[0].upper())
        if not marker_match:
            continue
        xyz = [_decimal(value) for value in cells[1:4]]
        if any(value is None for value in xyz):
            continue
        latitude, longitude, height = _ecef_to_geodetic(float(xyz[0]), float(xyz[1]), float(xyz[2]))
        marker = marker_match.group(1)
        records[marker] = (marker, latitude, longitude, height)
    return list(records.values())


def _dedupe_station_log_names(names: list[str]) -> list[str]:
    by_marker: dict[str, str] = {}
    for name in names:
        match = re.match(r"([A-Za-z0-9]{4})", name)
        if not match:
            continue
        marker = match.group(1).upper()
        current = by_marker.get(marker)
        if current is None or name > current:
            by_marker[marker] = name
    return sorted(by_marker.values())


def _parse_gref_station_log(text: str, *, url: str = "") -> Station | None:
    if not text.strip():
        return None
    marker_match = re.search(
        r"(?:Four\s+Character\s+ID|Marker\s+Name)\s*[:=]\s*([A-Z0-9]{4})",
        text, flags=re.IGNORECASE,
    )
    nine_match = re.search(
        r"(?:Nine\s+Character\s+ID|Nine\s+Char(?:acter)?\s+ID)\s*[:=]\s*([A-Z0-9]{9})",
        text, flags=re.IGNORECASE,
    )
    marker = (marker_match.group(1) if marker_match else (nine_match.group(1)[:4] if nine_match else "")).upper()
    if not marker:
        file_match = re.search(r"/([A-Za-z0-9]{4})[^/]*\.log$", url)
        marker = file_match.group(1).upper() if file_match else ""
    if not marker:
        return None
    station_id = (nine_match.group(1).upper() if nine_match else f"{marker}00DEU")
    domes_match = re.search(r"IERS\s+DOMES\s+(?:Number|No\.?)\s*[:=]\s*([A-Z0-9-]+)", text, flags=re.IGNORECASE)
    site_match = re.search(r"Site\s+Name\s*[:=]\s*([^\r\n]+)", text, flags=re.IGNORECASE)

    xyz = []
    for axis in "XYZ":
        match = re.search(
            rf"{axis}\s+coordinate[^:=]*[:=]\s*([-+]?\d+(?:\.\d+)?)",
            text, flags=re.IGNORECASE,
        )
        xyz.append(float(match.group(1)) if match else None)
    if any(value is None for value in xyz):
        compact = re.search(
            r"Approximate\s+Position[^\r\n]*[\r\n]+\s*"
            r"([-+]?\d+(?:\.\d+)?)\s+"
            r"([-+]?\d+(?:\.\d+)?)\s+"
            r"([-+]?\d+(?:\.\d+)?)",
            text, flags=re.IGNORECASE,
        )
        if compact:
            xyz = [float(value) for value in compact.groups()]
    if any(value is None for value in xyz):
        return None
    x, y, z = (float(value) for value in xyz)
    if not 5_000_000 <= math.sqrt(x*x + y*y + z*z) <= 7_000_000:
        return None
    latitude, longitude, height = _ecef_to_geodetic(x, y, z)
    return Station(
        id=station_id,
        marker_name=(site_match.group(1).strip() if site_match else marker),
        domes=domes_match.group(1).upper() if domes_match else None,
        latitude=latitude,
        longitude=longitude,
        height=height,
        country="DEU",
        network=["GREF"],
        data_networks=["europe"],
        regional_sources=["europe_gref"],
        providers=["gref_de"],
        aliases=[marker],
        sampling_rates=["30S"],
        rinex_versions=["2", "3"],
        metadata={"catalog_source": url, "source_type": "official_gref_site_log"},
    )


def _gref_station_links(html: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for marker in re.findall(r"(?:Subsites/GREF/)?DE/Stationen/([A-Z0-9]{4})/", html, flags=re.IGNORECASE):
        code = marker.upper()
        url = f"https://gref.bkg.bund.de/Subsites/GREF/DE/Stationen/{code}/{code}.html"
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def _gref_markers_from_obs_listing(html: str) -> list[str]:
    """Extract unique four-character markers from a BKG GREF obs listing."""
    markers: set[str] = set()
    for name in parse_listing_filenames(html):
        info = parse_rinex_filename(name)
        if info is not None and info.station:
            markers.add(info.station[:4].upper())
            continue
        match = re.match(r"^([A-Z0-9]{4})(?:00[A-Z]{3})?", name.upper())
        if match:
            markers.add(match.group(1))
    return sorted(marker for marker in markers if re.fullmatch(r"[A-Z0-9]{4}", marker))


def _parse_gref_station_page(html: str, *, url: str = "") -> Station | None:
    plain = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
    plain = " ".join(plain.split())
    station_match = re.search(r"Stations-ID[^A-Z0-9]+([A-Z0-9]{4})\b", plain, flags=re.IGNORECASE)
    if not station_match:
        # URL is stable enough to recover the station id if the label wording changes.
        url_match = re.search(r"/Stationen/([A-Z0-9]{4})/", url, flags=re.IGNORECASE)
        if not url_match:
            return None
        marker = url_match.group(1).upper()
    else:
        marker = station_match.group(1).upper()
    lat_match = re.search(r"\bB\s+([-+]?\d{1,2}\.\d+)\b", plain)
    lon_match = re.search(r"\bL\s+([-+]?\d{1,3}\.\d+)\b", plain)
    height_match = re.search(r"ellipsoidische Höhe.*?([-+]?\d+(?:\.\d+)?)", plain, flags=re.IGNORECASE)
    if not lat_match or not lon_match:
        x = re.search(r"\bX\s+([-+]?\d+(?:\.\d+)?)", plain)
        y = re.search(r"\bY\s+([-+]?\d+(?:\.\d+)?)", plain)
        z = re.search(r"\bZ\s+([-+]?\d+(?:\.\d+)?)", plain)
        if not (x and y and z):
            return None
        latitude, longitude, height = _ecef_to_geodetic(float(x.group(1)), float(y.group(1)), float(z.group(1)))
    else:
        latitude, longitude = float(lat_match.group(1)), float(lon_match.group(1))
        height = float(height_match.group(1)) if height_match else None
    domes_match = re.search(r"\b(\d{5}[MS]\d{3})\b", plain, flags=re.IGNORECASE)
    name_match = re.search(r"Stationsname\s+(.+?)\s+Stations-ID", plain, flags=re.IGNORECASE)
    return Station(
        id=f"{marker}00DEU",
        marker_name=(name_match.group(1).strip() if name_match else marker),
        domes=domes_match.group(1).upper() if domes_match else None,
        latitude=latitude,
        longitude=longitude,
        height=height,
        country="DEU",
        network=["GREF"],
        data_networks=["europe"],
        regional_sources=["europe_gref"],
        providers=["gref_de"],
        aliases=[marker],
        sampling_rates=["30S"],
        rinex_versions=["2", "3"],
        metadata={"catalog_source": url, "source_type": "official_gref"},
    )


def _redgae_coordinate_rows(html: str) -> list[tuple[str, str | None, float, float, float]]:
    records: dict[str, tuple[str, str | None, float, float, float]] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL):
        cells = _html_cells(row)
        if len(cells) < 5:
            continue

        # redGAE's official table is: Localización | Código IDN | X | Y | Z | ...
        # Never infer the station code from the location cell: many Spanish place
        # names contain a standalone four-letter word, which previously shifted the
        # numeric columns and produced bogus points in the Indian Ocean.
        identity_index = None
        marker = None
        domes = None
        for i, cell in enumerate(cells[1:4], start=1):
            match = re.match(
                r"^\s*([A-Z0-9]{4})(?:\s+(\d{5}[MS]\d{3}))?(?:\s|$)",
                cell.upper(),
            )
            if match:
                identity_index = i
                marker = match.group(1)
                domes = match.group(2)
                break
        if marker is None or identity_index is None:
            continue

        # Select the first physically plausible ECEF XYZ triplet after Código IDN.
        values: list[float] = []
        for cell in cells[identity_index + 1:]:
            value = _decimal(cell)
            if value is not None:
                values.append(value)
        xyz = None
        for i in range(max(0, len(values) - 2)):
            x, y, z = values[i:i + 3]
            radius = math.sqrt(x*x + y*y + z*z)
            if 5_000_000 <= radius <= 7_000_000:
                xyz = (x, y, z)
                break
        if xyz is None:
            continue
        latitude, longitude, height = _ecef_to_geodetic(*xyz)
        # redGAE covers mainland Spain, Balearics, Canaries, Ceuta and Melilla.
        # A final geographic sanity check prevents malformed rows from polluting
        # the Europe map if the upstream table changes again.
        if not (26.0 <= latitude <= 45.0 and -20.0 <= longitude <= 6.0):
            continue
        records[marker] = (marker, domes, latitude, longitude, height)
    return list(records.values())


def _plain_html(text: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def _nsgi_coordinate_rows(html: str) -> list[tuple[str, str, str, float, float, float]]:
    """Parse Kadaster/NSGI current metadata table robustly.

    The live page is an HTML table; flattening the whole document and relying on
    line breaks is fragile because browsers/servers may minify the markup.  Parse
    rows/cells first and locate the ETRF2000 coordinate block explicitly.
    """
    rows: list[tuple[str, str, str, float, float, float]] = []
    seen: set[str] = set()
    for raw_row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL):
        cells = _html_cells(raw_row)
        if not cells:
            continue
        id_index = None
        station_id = None
        for i, cell in enumerate(cells[:4]):
            match = re.search(r"\b([A-Z0-9]{4}00[A-Z]{3})\b", cell.upper())
            if match:
                id_index = i
                station_id = match.group(1)
                break
        if station_id is None or id_index is None or station_id in seen:
            continue
        try:
            etrf_index = next(i for i, cell in enumerate(cells) if "ETRF2000" in cell.upper())
        except StopIteration:
            continue
        numeric: list[float] = []
        for cell in cells[etrf_index + 1:]:
            value = _decimal(cell)
            if value is not None:
                numeric.append(value)
            if len(numeric) >= 6:
                break
        if len(numeric) < 6:
            continue
        x, y, z, latitude, longitude, height = numeric[:6]
        radius = math.sqrt(x*x + y*y + z*z)
        if not (
            5_000_000 <= radius <= 7_000_000
            and 50.0 <= latitude <= 54.5
            and 3.0 <= longitude <= 8.0
        ):
            continue
        site_name = cells[id_index + 1].strip() if id_index + 1 < len(cells) else station_id[:4]
        prefix_text = " ".join(cells[id_index + 1:etrf_index])
        network_match = re.search(
            r"\b((?:IGS|EPN|AGRS\.NL|NETPOS)(?:\+(?:IGS|EPN|AGRS\.NL|NETPOS))*)\b",
            prefix_text,
            flags=re.IGNORECASE,
        )
        networks = network_match.group(1).upper() if network_match else ""
        rows.append((station_id, site_name or station_id[:4], networks, latitude, longitude, height))
        seen.add(station_id)

    if rows:
        return rows

    # Compatibility fallback for older/preformatted snapshots used by tests.
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
    pattern = re.compile(
        r"^(?P<id>[A-Z0-9]{4}00[A-Z]{3})\s+(?P<prefix>.*?)\s+ETRF2000\s+"
        r"[-+]?\d+(?:\.\d+)?\s+[-+]?\d+(?:\.\d+)?\s+[-+]?\d+(?:\.\d+)?\s+"
        r"(?P<lat>[-+]?\d+(?:\.\d+)?)\s+(?P<lon>[-+]?\d+(?:\.\d+)?)\s+"
        r"(?P<h>[-+]?\d+(?:\.\d+)?)",
        flags=re.IGNORECASE,
    )
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        match = pattern.search(line)
        if not match:
            continue
        station_id = match.group("id").upper()
        prefix = match.group("prefix").strip()
        date_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", prefix)
        if date_match:
            site_name = prefix[:date_match.start()].strip()
            tail = prefix[date_match.end():].strip()
        else:
            site_name, tail = station_id[:4], prefix
        network_match = re.search(
            r"\b((?:IGS|EPN|AGRS\.NL|NETPOS)(?:\+(?:IGS|EPN|AGRS\.NL|NETPOS))*)\b",
            tail, flags=re.IGNORECASE,
        )
        networks = network_match.group(1).upper() if network_match else ""
        rows.append((station_id, site_name, networks, float(match.group("lat")), float(match.group("lon")), float(match.group("h"))))
    return rows


def _apos_coordinate_rows(html: str) -> list[tuple[str, str, float, float, float]]:
    candidates: list[str] = []
    table_rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL)
    if table_rows:
        candidates.extend(" ".join(_html_cells(row)) for row in table_rows)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
    candidates.extend(" ".join(line.split()) for line in text.splitlines() if line.strip())
    pattern = re.compile(
        r"^(?P<code>[A-Z0-9]{4})\s+(?P<name>.+?)\s+(?:19|20)\d{2}\s+"
        r"(?:(?:\d{3,4}-\d{3}\s+[A-Z]\d)\s+)?"
        r"(?P<lat>4\d[,.]\d+)\s+(?P<lon>\d{1,2}[,.]\d+)\s+(?P<h>\d+(?:[,.]\d+)?)$",
        flags=re.IGNORECASE,
    )
    result: dict[str, tuple[str, str, float, float, float]] = {}
    for line in candidates:
        match = pattern.search(line.strip())
        if not match or match.group("code").upper() == "CODE":
            continue
        code = match.group("code").upper()
        latitude = float(match.group("lat").replace(",", "."))
        longitude = float(match.group("lon").replace(",", "."))
        if not (46.0 <= latitude <= 50.0 and 9.0 <= longitude <= 18.0):
            continue
        result[code] = (
            code,
            match.group("name").strip(),
            latitude,
            longitude,
            float(match.group("h").replace(",", ".")),
        )
    return list(result.values())


def _dms_decimal(deg: str, minute: str, second: str, hemi: str) -> float:
    value = float(deg.replace(",", ".")) + float(minute.replace(",", ".")) / 60.0 + float(second.replace(",", ".")) / 3600.0
    if hemi.upper() in {"S", "W"}:
        value = -value
    return value


def _renep_coordinate_rows(html: str) -> list[tuple[str, float, float, float, str | None]]:
    pattern = re.compile(
        r"\b(?P<code>[A-Z0-9]{4})\b.*?Latitude\s+(?P<latd>\d{1,2})[º°]\s*(?P<latm>\d{1,2})[´']\s*(?P<lats>\d+(?:[,.]\d+)?)\s*[´'’]*[´'’]*\s*(?P<lath>[NS]).*?"
        r"Longitude\s+(?P<lond>\d{1,3})[º°]\s*(?P<lonm>\d{1,2})[´']\s*(?P<lons>\d+(?:[,.]\d+)?)\s*[´'’]*[´'’]*\s*(?P<lonh>[EW]).*?"
        r"Altitude\s+elipsoidal(?:\s*\*)?\s+(?P<h>[-+]?\d+(?:[,.]\d+)?)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    result: list[tuple[str, float, float, float, str | None]] = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL) or [html]
    for row_html in rows:
        plain = _plain_html(row_html)
        match = pattern.search(plain)
        if not match:
            continue
        marker = match.group("code").upper()
        ftp_match = re.search(
            r"ftp://ftp\.dgterritorio\.pt/ReNEP/([A-Z0-9]+)/",
            row_html,
            flags=re.IGNORECASE,
        )
        ftp_folder = ftp_match.group(1).upper() if ftp_match else None
        result.append((
            marker,
            _dms_decimal(match.group("latd"), match.group("latm"), match.group("lats"), match.group("lath")),
            _dms_decimal(match.group("lond"), match.group("lonm"), match.group("lons"), match.group("lonh")),
            float(match.group("h").replace(",", ".")),
            ftp_folder,
        ))
    return result


def _renep_descend_name(name: str, day: date, depth: int) -> bool:
    value = name.strip().strip("/")
    lower = value.lower()
    if not value or "." in value and not value.isdigit():
        return False
    year = str(day.year)
    yy = f"{day.year % 100:02d}"
    doy = f"{datetime_to_doy(day):03d}"
    compact = day.strftime("%Y%m%d")
    if value in {year, yy, doy, compact, f"{yy}{doy}", f"{year}{doy}"}:
        return True
    return any(token in lower for token in ("rinex", "rnx", "data", "obs", "daily"))


def _is_daily_observation_name(filename: str) -> bool:
    upper = filename.upper()
    if "_01D_" in upper and ("_MO." in upper or upper.endswith(("_MO.CRX.GZ", "_MO.RNX.GZ"))):
        return True
    return bool(re.search(r"\.\d{2}D(?:\.GZ|\.Z|\.ZIP)?$", filename, flags=re.IGNORECASE))


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
        next_latitude = math.atan2(
            z,
            p * (1.0 - e2 * radius / (radius + height)),
        )
        if abs(next_latitude - latitude) < 1e-13:
            latitude = next_latitude
            break
        latitude = next_latitude
    return math.degrees(latitude), math.degrees(longitude), height



def _extract_ncn_station_id(parts: list[str]) -> str | None:
    # The four-character ID sits immediately before online/offline dates.  Scan for a
    # four-character alphanumeric token followed by a seven-digit YYYYDDD value.
    for index in range(2, len(parts) - 1):
        token = parts[index].upper()
        if not re.fullmatch(r"[A-Z0-9]{4}", token):
            continue
        if re.fullmatch(r"\d{7}", parts[index + 1]):
            return token
    return None


def _clean_html(value: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _dedupe_remote_files(files: list[RemoteFile]) -> list[RemoteFile]:
    result: list[RemoteFile] = []
    seen: set[tuple[str, str]] = set()
    for item in files:
        key = (item.station or "", item.filename)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedupe_stations(stations: list[Station]) -> list[Station]:
    result: dict[str, Station] = {}
    for station in stations:
        result.setdefault(station.id.upper(), station)
    return list(result.values())
