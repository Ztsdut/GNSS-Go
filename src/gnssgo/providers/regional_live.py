from __future__ import annotations

import asyncio
import csv
import io
import math
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Iterable
from html.parser import HTMLParser
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx

from gnssgo.exceptions import ProviderError, ProviderProtocolError
from gnssgo.models import (
    NavigationRequest,
    ObservationRequest,
    ProductRequest,
    RemoteFile,
    Station,
)
from gnssgo.providers.base import GNSSProvider, ProviderCapabilities
from gnssgo.providers.cache import ProviderHealthCache, RemoteDiscoveryCache
from gnssgo.providers.listing import parse_listing_filenames
from gnssgo.regional_sources import default_regional_source_registry
from gnssgo.utils.dates import datetime_to_doy


class RegionalLiveProvider(GNSSProvider):
    data_network: str
    source_type: str = "official_api"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | httpx.Client | None = None,
        discovery_cache: RemoteDiscoveryCache | None = None,
        health_cache: ProviderHealthCache | None = None,
    ) -> None:
        # GUI/CLI sync entry points use ``asyncio.run`` for individual operations.
        # A long-lived ``httpx.AsyncClient`` is bound to the event loop that first
        # uses it and cannot safely be reused after that loop has been closed.
        # Regional providers are shared by the application, so use a synchronous
        # client by default and execute requests in worker threads.  This keeps
        # connection pooling while making the provider safe across repeated
        # ``asyncio.run`` calls and Qt worker threads.  Tests/integrations may still
        # inject an AsyncClient explicitly.
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(15.0, connect=5.0, read=10.0),
            follow_redirects=True,
            trust_env=False,
        )
        self.discovery_cache = discovery_cache or RemoteDiscoveryCache()
        self.health_cache = health_cache or ProviderHealthCache()

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            observations=True,
            navigation=False,
            station_metadata=True,
            authentication_required=False,
        )

    async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
        return []

    async def search_products(self, request: ProductRequest) -> list[RemoteFile]:
        return []

    async def _request_get(self, url: str) -> httpx.Response:
        # GeoNet's 2026 Data API introduced rate limiting (HTTP 429).  Regional
        # catalog/listing requests are small, so a short bounded retry prevents a
        # temporary rate-limit response from making the whole PLAN fail.
        response: httpx.Response | None = None
        for attempt in range(3):
            if isinstance(self.client, httpx.AsyncClient):
                response = await self.client.get(url)
            else:
                response = await asyncio.to_thread(self.client.get, url)
            if response.status_code != 429 or attempt == 2:
                return response
            retry_after = response.headers.get("retry-after", "").strip()
            try:
                delay = min(10.0, max(0.25, float(retry_after)))
            except ValueError:
                delay = 0.5 * (2**attempt)
            await asyncio.sleep(delay)
        assert response is not None
        return response

    async def _get_json(self, url: str) -> object:
        try:
            response = await self._request_get(url)
        except httpx.HTTPError as exc:
            self.health_cache.record_failure(self.name)
            raise ProviderError(f"{self.name} request failed: {exc}") from exc
        if response.status_code >= 400:
            self.health_cache.record_failure(self.name, status_code=response.status_code)
            if response.status_code == 404:
                return []
            raise ProviderError(f"{self.name} returned HTTP {response.status_code}.")
        self.health_cache.record_success(self.name)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderProtocolError(
                f"{self.name} returned non-JSON data where JSON was expected."
            ) from exc

    async def _get_text(self, url: str) -> str:
        try:
            response = await self._request_get(url)
        except httpx.HTTPError as exc:
            self.health_cache.record_failure(self.name)
            raise ProviderError(f"{self.name} request failed: {exc}") from exc
        if response.status_code >= 400:
            self.health_cache.record_failure(self.name, status_code=response.status_code)
            if response.status_code == 404:
                return ""
            raise ProviderError(f"{self.name} returned HTTP {response.status_code}.")
        self.health_cache.record_success(self.name)
        return response.text

    async def health_check(self) -> dict[str, str]:
        state = self.health_cache.state(self.name)
        return {
            "provider": self.name,
            "status": state.status.value,
            "data_network": self.data_network,
        }


class GAProvider(RegionalLiveProvider):
    name = "ga"
    data_network = "australia"
    source_type = "official_api"
    rinex_api = "https://data.gnss.ga.gov.au/api/rinexFiles"
    station_api = "https://metadata.gnss.ga.gov.au/api/siteLogs"
    cors_site_api = "https://prod-metadata.gnss.ga.gov.au/api/corsSites"
    cors_network_api = "https://prod-metadata.gnss.ga.gov.au/api/corsNetworks"
    station_page_size = 200
    station_max_pages = 1000
    # The GA RINEX query API accepts a comma-separated stationId list and a
    # multi-day time range.  Planning used to issue one HTTP request for every
    # station/day pair, which made a regional selection look permanently stuck
    # in PLAN before the Download Plan dialog could be shown.  Keep query URLs
    # comfortably small while collapsing hundreds/thousands of serial calls
    # into a handful of batched requests.
    rinex_query_batch_size = 100
    rinex_query_concurrency = 4
    excluded_source_names = (
        "GEONET",
        "POSITIONZ",
        "JAXA",
        "HKSAR",
        "GEOSPATIAL_INFORMATION_AGENCY",
        "TRIMBLE",
    )

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        station_map: dict[str, str] = {}
        for station in request.stations or []:
            station_id = station[:4].upper()
            if station_id:
                station_map.setdefault(station_id, station.upper())
        if not station_map:
            return []

        station_ids = list(station_map)
        batches = [
            station_ids[index : index + self.rinex_query_batch_size]
            for index in range(0, len(station_ids), self.rinex_query_batch_size)
        ]
        semaphore = asyncio.Semaphore(self.rinex_query_concurrency)

        async def fetch_batch(batch: list[str]) -> list[RemoteFile]:
            query = self._query_range_url(
                batch,
                request.date_range.start,
                request.date_range.end,
                request,
            )
            async with semaphore:
                payload = await self._cached_json(query)
            if not isinstance(payload, list):
                raise ProviderProtocolError("GA RINEX API returned an unexpected schema.")
            return self._remote_files_for_request(payload, station_map, request)

        batch_results = await asyncio.gather(*(fetch_batch(batch) for batch in batches))
        return [remote for batch in batch_results for remote in batch]

    def _with_download_mirrors(self, rob_remote: RemoteFile) -> RemoteFile:
        """Prefer BKG for transfer while preserving robust mirror fallbacks."""
        day = rob_remote.date
        if day is None:
            return rob_remote
        doy = datetime_to_doy(day)
        filename = rob_remote.filename
        lower = filename.lower()
        modern = "_" in filename

        def candidate(url: str, source_id: str, data_center: str) -> RemoteFile:
            item = rob_remote.model_copy(deep=True)
            item.url = url
            item.metadata["regional_source"] = source_id
            item.metadata["data_center"] = data_center
            item.metadata["discovery_source"] = "ROB / EPN API"
            item.metadata["download_source"] = source_id
            item.fallback_candidates = []
            return item

        primary = candidate(
            f"{self.bkg_base}/obs/{day.year}/{doy:03d}/{filename}",
            "epn_bkg",
            "BKG",
        )
        fallbacks: list[RemoteFile] = []

        bev_folder = "obs_v3" if modern else "obs"
        fallbacks.append(
            candidate(
                f"{self.bev_base}/{bev_folder}/{day.year}/{doy:03d}/{filename}",
                "epn_bev",
                "BEV",
            )
        )

        rob_fallback = rob_remote.model_copy(deep=True)
        rob_fallback.metadata["regional_source"] = "epn_rob"
        rob_fallback.metadata["data_center"] = "ROB / EPN"
        rob_fallback.metadata["discovery_source"] = "ROB / EPN API"
        rob_fallback.metadata["download_source"] = "epn_rob"
        rob_fallback.fallback_candidates = []
        fallbacks.append(rob_fallback)

        if _sampling_from_name(filename) == "30S":
            ign_root = "data_v3" if modern else "data"
            fallbacks.append(
                candidate(
                    f"{self.ign_base}/{ign_root}/{day.year}/{doy:03d}/data_30/{filename}",
                    "epn_ign",
                    "IGN",
                )
            )

        primary.fallback_candidates = fallbacks
        primary.metadata["mirror_order"] = ",".join(
            ["BKG", "BEV", "ROB / EPN"] + (["IGN"] if len(fallbacks) == 3 else [])
        )
        return primary

    async def fetch_station_catalog(self) -> list[Station]:
        raw_sites = await self._cors_site_pages()
        network_names = await self._cors_network_names()
        source_registry = default_regional_source_registry()
        source_counts: Counter[str] = Counter()
        excluded_source_counts: Counter[str] = Counter()
        stations: list[Station] = []
        all_ids: set[str] = set()
        included_ids: set[str] = set()
        for site in raw_sites:
            station_id = _ga_cors_station_id(site)
            if station_id:
                all_ids.add(station_id)
            source_names = _ga_cors_source_names(site, network_names)
            regional_sources = _ga_australia_sources_from_names(source_names)
            for source_id in regional_sources:
                source_counts[source_id] += 1
            if not regional_sources:
                for source_name in source_names:
                    excluded_source_counts[source_name] += 1
            if not regional_sources:
                continue
            station = _ga_station_from_cors_site(site, regional_sources, source_names)
            if station:
                stations.append(station)
                included_ids.add(station.id)
        page_stats = dict(getattr(self, "last_station_catalog_stats", {}))
        self.last_station_catalog_stats = {
            **page_stats,
            "ga_total_records_fetched": len(raw_sites),
            "ga_total_unique_stations": len(all_ids),
            "australia_regional_records": len(stations),
            "australia_regional_unique": len(included_ids),
            "excluded_non_australia": max(0, len(all_ids) - len(included_ids)),
            "regional_source_counts": {
                source.name: source_counts[source.id]
                for source in source_registry.all("australia")
            },
            "excluded_source_counts": dict(sorted(excluded_source_counts.items())),
        }
        return stations

    async def _cors_network_names(self) -> dict[int, str]:
        cached = self.discovery_cache.get(self.name, "cors_networks")
        if cached is not None:
            return cached
        payload = await self._get_json(
            str(httpx.URL(self.cors_network_api, params={"page": "0", "size": "1000"}))
        )
        if not isinstance(payload, dict):
            raise ProviderProtocolError("GA CORS network API returned an unexpected schema.")
        networks = payload.get("_embedded", {}).get("corsNetworks", [])
        if not isinstance(networks, list):
            raise ProviderProtocolError("GA CORS network API did not include corsNetworks.")
        result: dict[int, str] = {}
        for item in networks:
            if not isinstance(item, dict):
                continue
            network_id = _int_or_none(item.get("id"))
            name = item.get("name")
            if network_id is not None and name:
                result[network_id] = str(name)
        return self.discovery_cache.set(self.name, "cors_networks", result)

    async def _cors_site_pages(self) -> list[dict]:
        next_url: str | None = str(
            httpx.URL(
                self.cors_site_api,
                params={
                    "siteStatus": "PUBLIC",
                    "page": "0",
                    "size": str(self.station_page_size),
                },
            )
        )
        raw_sites: list[dict] = []
        seen_urls: set[str] = set()
        seen_page_numbers: set[int] = set()
        pages_fetched = 0
        expected_total: int | None = None
        while next_url:
            if next_url in seen_urls:
                raise ProviderProtocolError("GA CORS site API repeated the next page URL.")
            if pages_fetched >= self.station_max_pages:
                raise ProviderProtocolError("GA CORS site API exceeded max_pages.")
            seen_urls.add(next_url)
            payload = await self._cached_json(next_url)
            if not isinstance(payload, dict):
                raise ProviderProtocolError("GA CORS site API returned an unexpected schema.")
            page_number = _ga_page_number(payload)
            if page_number in seen_page_numbers:
                raise ProviderProtocolError("GA CORS site API repeated a page number.")
            seen_page_numbers.add(page_number)
            pages_fetched += 1
            expected_total = _ga_total_elements(payload) or expected_total
            page_sites = _ga_cors_sites(payload)
            raw_sites.extend(page_sites)
            candidate_next_url = _ga_next_url(payload)
            if candidate_next_url in seen_urls:
                raise ProviderProtocolError("GA CORS site API repeated the next page URL.")
            if not page_sites:
                break
            next_url = candidate_next_url
        self.last_station_catalog_stats = {
            "pages_fetched": pages_fetched,
            "catalog_complete": expected_total is None or len(raw_sites) >= expected_total,
            "ga_total_records_expected": expected_total,
        }
        return raw_sites

    async def _station_pages(self) -> list[dict]:
        next_url: str | None = str(
            httpx.URL(
                self.station_api,
                params={"page": "0", "size": str(self.station_page_size)},
            )
        )
        raw_sites: list[dict] = []
        seen_urls: set[str] = set()
        seen_page_numbers: set[int] = set()
        pages_fetched = 0
        expected_total: int | None = None
        while next_url:
            if next_url in seen_urls:
                raise ProviderProtocolError("GA station API repeated the next page URL.")
            if pages_fetched >= self.station_max_pages:
                raise ProviderProtocolError("GA station API exceeded max_pages.")
            seen_urls.add(next_url)
            payload = await self._cached_json(next_url)
            if not isinstance(payload, dict):
                raise ProviderProtocolError("GA station API returned an unexpected schema.")
            page_number = _ga_page_number(payload)
            if page_number in seen_page_numbers:
                raise ProviderProtocolError("GA station API repeated a page number.")
            seen_page_numbers.add(page_number)
            pages_fetched += 1
            expected_total = _ga_total_elements(payload) or expected_total
            page_sites = _ga_site_logs(payload)
            raw_sites.extend(page_sites)
            candidate_next_url = _ga_next_url(payload)
            if candidate_next_url in seen_urls:
                raise ProviderProtocolError("GA station API repeated the next page URL.")
            if not page_sites:
                break
            next_url = candidate_next_url
        self.last_station_catalog_stats = {
            "pages_fetched": pages_fetched,
            "catalog_complete": expected_total is None or len(raw_sites) >= expected_total,
            "ga_total_records_expected": expected_total,
        }
        return raw_sites

    def _query_url(self, station: str, day: date, request: ObservationRequest) -> str:
        return self._query_range_url([station[:4]], day, day, request)

    def _query_range_url(
        self,
        stations: Iterable[str],
        start_day: date,
        end_day: date,
        request: ObservationRequest,
    ) -> str:
        # Preserve the existing GA selection policy: auto/3/4 query the RINEX 3
        # holding used by the current desktop workflow; RINEX 2 remains explicit.
        rinex = "3" if str(request.rinex) in {"auto", "3", "4"} else str(request.rinex)
        start = f"{start_day.isoformat()}T00:00:00Z"
        end = f"{end_day.isoformat()}T23:59:59Z"
        params = {
            "stationId": ",".join(
                dict.fromkeys(str(station)[:4].upper() for station in stations if station)
            ),
            "startDate": start,
            "endDate": end,
            "filePeriod": "01D",
            "fileType": "obs",
            "rinexVersion": rinex,
            "metadataStatus": "all",
        }
        return str(httpx.URL(self.rinex_api, params=params))

    async def _cached_json(self, url: str) -> object:
        cached = self.discovery_cache.get(self.name, url)
        if cached is not None:
            return cached
        return self.discovery_cache.set(self.name, url, await self._get_json(url))

    def _remote_files(
        self,
        payload: Iterable[object],
        station: str,
        day: date,
    ) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for item in payload:
            if not isinstance(item, dict):
                raise ProviderProtocolError("GA RINEX API item is not an object.")
            url = item.get("fileLocation")
            filename = item.get("fileName") or _filename_from_url(str(url or ""))
            if not url or not filename:
                raise ProviderProtocolError("GA RINEX API item lacks fileLocation.")
            files.append(
                _remote(
                    self.name,
                    str(url),
                    str(filename),
                    station=item.get("stationId") or station,
                    day=day,
                    size=_int_or_none(item.get("fileSize")),
                    metadata={
                        "data_network": self.data_network,
                        "regional_provider": self.name,
                        "rinex_version": str(item.get("rinexVersion", "")),
                        "duration": str(item.get("filePeriod", "")),
                        "sampling": _sampling_from_name(str(filename)),
                    },
                )
            )
        return files

    def _remote_files_for_request(
        self,
        payload: Iterable[object],
        station_map: dict[str, str],
        request: ObservationRequest,
    ) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for item in payload:
            if not isinstance(item, dict):
                raise ProviderProtocolError("GA RINEX API item is not an object.")
            url = item.get("fileLocation")
            filename = item.get("fileName") or _filename_from_url(str(url or ""))
            if not url or not filename:
                raise ProviderProtocolError("GA RINEX API item lacks fileLocation.")

            raw_site_id = item.get("siteId") or item.get("stationId") or ""
            site_id = str(raw_site_id)[:4].upper()
            station = station_map.get(site_id)
            if station is None:
                if "_" in str(filename):
                    station = str(filename).split("_", 1)[0].upper()
                elif site_id:
                    station = f"{site_id}00AUS"
                else:
                    # A response without a site id cannot be matched safely to a
                    # selected station in a multi-station query.
                    continue

            day = _ga_rinex_day(item, str(filename))
            if day is None:
                # The API normally returns startDate.  If an older/mocked
                # response omits it, keep single-day compatibility but avoid
                # assigning an arbitrary date to a multi-day response.
                if request.date_range.start == request.date_range.end:
                    day = request.date_range.start
                else:
                    continue
            if day < request.date_range.start or day > request.date_range.end:
                continue

            files.append(
                _remote(
                    self.name,
                    str(url),
                    str(filename),
                    station=station,
                    day=day,
                    size=_int_or_none(item.get("fileSize")),
                    metadata={
                        "data_network": self.data_network,
                        "regional_provider": self.name,
                        "rinex_version": str(item.get("rinexVersion", "")),
                        "duration": str(item.get("filePeriod", "")),
                        "sampling": _sampling_from_name(str(filename)),
                    },
                )
            )
        return files


class EPNProvider(RegionalLiveProvider):
    """EUREF Permanent GNSS Network provider.

    Europe is presented to users as four physical/public data servers rather
    than BKG's internal BKGE/BKGI distributor identities:

    * ROB / EPN (Belgium) -- EPN central/historical service
    * BEV (Austria)       -- EPN regional data centre
    * BKG (Germany)       -- EUREF archive
    * IGN (France)        -- RGP archive

    Selecting several sources means fallback, not duplicate downloading: the
    first source that contains the requested station/day satisfies that logical
    request.  Explicitly selecting one source forces discovery against that
    source only.
    """

    name = "epn"
    data_network = "europe"
    source_type = "official_epn_data_centres"

    # Complete EPN station/coordinate inventory maintained by ROB.
    station_catalog_source = "https://gnss.be/epndata.php"

    # ROB/EPN Historical Data Centre REST API.  This is more robust for planner
    # discovery than scraping the epncb.oma.be file-server directory tree and
    # still represents the same ROB/EPN data centre in the UI.
    hdc_station_api = "https://gnss.be/api/v1/epn/station-data"
    data_api = "https://gnss.be/api/v1/epn/data"

    # Public HTTPS mirrors.  IGN also publishes anonymous FTP, but GNSS Go uses
    # the official HTTPS mirror because modern clients/firewalls handle it more
    # reliably while exposing the same /pub archive.
    bev_base = "https://gnss.bev.gv.at/at.gv.bev.dc/data"
    bkg_base = "https://igs.bkg.bund.de/root_ftp/EUREF"
    ign_base = "https://rgpdata.ign.fr/pub"

    # Source membership metadata.  BKG's user-facing server combines the EPN
    # and IGS/EPN distributor identities published by M3G, so both pages are
    # merged into one BKG source.  IGN membership is intersected with the
    # official RGP station list by 4-character marker.
    bev_membership_url = (
        "https://gnss-metadata.eu/MOID/"
        "datadistributor.6409a3893fe0d347d90261de"
    )
    bkg_membership_urls = (
        "https://gnss-metadata.eu/MOID/"
        "datadistributor.6409a3893fe0d347d90261df",
        "https://gnss-metadata.eu/MOID/"
        "datadistributor.6409a3893fe0d347d90261e0",
    )
    ign_station_list_url = "https://rgp.ign.fr/STATIONS/liste.php"

    # Physical download fallback order.  Discovery is handled by ROB first,
    # but actual file transfer prefers BKG because its EUREF HTTPS archive is
    # generally the best default mirror for GNSS Go.
    default_source_order = ("epn_bkg", "epn_bev", "epn_rob", "epn_ign")
    all_source_ids = ("epn_rob", "epn_bev", "epn_bkg", "epn_ign")
    fallback_catalog = Path(__file__).resolve().parent.parent / "resources" / "EPN_stations_fallback.csv"
    legacy_source_aliases = {
        "epn_hdc": "epn_rob",
        "epn_bkge": "epn_bkg",
        "epn_bkgi": "epn_bkg",
    }

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        requested = {
            self.legacy_source_aliases.get(source_id, source_id)
            for source_id in (request.regional_sources or [])
        }
        requested = {source_id for source_id in requested if source_id in self.all_source_ids}

        # Normal Europe/EPN use no longer exposes physical data centres in the
        # GUI.  Use ROB as a lightweight availability/index service first and
        # then rewrite each discovered logical file so BKG is the primary
        # download URL with BEV -> ROB -> IGN fallbacks.  This avoids scraping
        # several large daily directories merely to build a PLAN.
        if not requested:
            try:
                rob_files = await self._search_rob(request, "epn_rob")
            except (ProviderError, OSError):
                rob_files = []
            if rob_files:
                return [self._with_download_mirrors(remote) for remote in rob_files]

        # Explicit legacy data-centre selections are still supported, and this
        # path also acts as a safety fallback when ROB discovery is temporarily
        # unavailable or has not yet indexed very recent EPN data.
        if requested and len(request.stations or []) == 1:
            station_id = (request.stations or [""])[0].upper()
            membership = getattr(self, "_station_source_membership", {}).get(station_id)
            reliable = getattr(self, "_source_membership_reliable", set())
            if membership is not None:
                requested = {
                    source_id
                    for source_id in requested
                    if source_id not in reliable or source_id in membership
                }
                if not requested:
                    return []

        source_order = (
            [source_id for source_id in self.default_source_order if source_id in requested]
            if requested
            else list(self.default_source_order)
        )

        errors: list[str] = []
        for source_id in source_order:
            # ROB was already tried as the fast discovery/index path above.
            if not requested and source_id == "epn_rob":
                continue
            failure_key = f"epn-source-failed:{source_id}"
            if self.discovery_cache.get(self.name, failure_key) is not None:
                continue
            try:
                if source_id == "epn_rob":
                    files = await self._search_rob(request, source_id)
                elif source_id == "epn_bev":
                    files = await self._search_bev(request, source_id)
                elif source_id == "epn_bkg":
                    files = await self._search_bkg(request, source_id)
                elif source_id == "epn_ign":
                    files = await self._search_ign(request, source_id)
                else:
                    continue
            except (ProviderError, OSError) as exc:
                self.discovery_cache.set(self.name, failure_key, True)
                errors.append(f"{source_id}: {exc}")
                continue
            if files:
                for remote in files:
                    if errors:
                        remote.metadata["epn_source_errors"] = " | ".join(errors)
                return files
        return []

    def _with_download_mirrors(self, rob_remote: RemoteFile) -> RemoteFile:
        """Prefer BKG for transfer while preserving robust mirror fallbacks."""
        day = rob_remote.date
        if day is None:
            return rob_remote
        doy = datetime_to_doy(day)
        filename = rob_remote.filename
        lower = filename.lower()
        modern = "_" in filename

        def candidate(url: str, source_id: str, data_center: str) -> RemoteFile:
            item = rob_remote.model_copy(deep=True)
            item.url = url
            item.metadata["regional_source"] = source_id
            item.metadata["data_center"] = data_center
            item.metadata["discovery_source"] = "ROB / EPN API"
            item.metadata["download_source"] = source_id
            item.fallback_candidates = []
            return item

        primary = candidate(
            f"{self.bkg_base}/obs/{day.year}/{doy:03d}/{filename}",
            "epn_bkg",
            "BKG",
        )
        fallbacks: list[RemoteFile] = []

        bev_folder = "obs_v3" if modern else "obs"
        fallbacks.append(
            candidate(
                f"{self.bev_base}/{bev_folder}/{day.year}/{doy:03d}/{filename}",
                "epn_bev",
                "BEV",
            )
        )

        rob_fallback = rob_remote.model_copy(deep=True)
        rob_fallback.metadata["regional_source"] = "epn_rob"
        rob_fallback.metadata["data_center"] = "ROB / EPN"
        rob_fallback.metadata["discovery_source"] = "ROB / EPN API"
        rob_fallback.metadata["download_source"] = "epn_rob"
        rob_fallback.fallback_candidates = []
        fallbacks.append(rob_fallback)

        if _sampling_from_name(filename) == "30S":
            ign_root = "data_v3" if modern else "data"
            fallbacks.append(
                candidate(
                    f"{self.ign_base}/{ign_root}/{day.year}/{doy:03d}/data_30/{filename}",
                    "epn_ign",
                    "IGN",
                )
            )

        primary.fallback_candidates = fallbacks
        primary.metadata["mirror_order"] = ",".join(
            ["BKG", "BEV", "ROB / EPN"] + (["IGN"] if len(fallbacks) == 3 else [])
        )
        return primary

    def bundled_station_catalog(self) -> list[Station]:
        """Return a curated EPN coordinate snapshot shipped with GNSS Go.

        The bundled rows are sourced from the public EPN station table and make
        Europe visible immediately on a fresh/offline installation.  A live EPN
        refresh runs quietly after startup and supersedes/augments this snapshot.
        """
        if not self.fallback_catalog.exists():
            return []
        stations: list[Station] = []
        with self.fallback_catalog.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                station_id = str(row.get("station_id") or "").strip().upper()
                if not station_id:
                    continue
                try:
                    latitude = float(str(row.get("latitude") or "").strip())
                    longitude = float(str(row.get("longitude") or "").strip())
                except ValueError:
                    continue
                country = str(row.get("country_code") or "").strip().upper() or None
                city = str(row.get("city") or "").strip()
                stations.append(
                    Station(
                        id=station_id,
                        marker_name=city or station_id[:4],
                        latitude=latitude,
                        longitude=longitude,
                        country=country,
                        network=["EPN"],
                        data_networks=[self.data_network],
                        regional_sources=["europe_epn"],
                        providers=[self.name],
                        aliases=[station_id[:4]],
                        sampling_rates=["30S"],
                        metadata={
                            "catalog_sources": [self.name],
                            "catalog_source": "bundled EPN release snapshot",
                            "source_type": self.source_type,
                            "epn_data_centres": list(self.all_source_ids),
                            "bundled_fallback": "true",
                        },
                    )
                )
        return stations

    async def fetch_station_catalog(self) -> list[Station]:
        try:
            html = await self._cached_text(self.station_catalog_source)
            rows = _epn_station_rows(html)
        except (ProviderError, OSError):
            rows = []
        if not rows:
            fallback = self.bundled_station_catalog()
            if fallback:
                self._station_source_membership = {
                    station.id.upper(): set(self.all_source_ids) for station in fallback
                }
                self._source_membership_reliable = {"epn_rob"}
                self.last_station_catalog_stats = {
                    "catalog_complete": False,
                    "epn_catalog_version": 6,
                    "epn_station_count": len(fallback),
                    "station_catalog_source": str(self.fallback_catalog),
                    "data_centres": list(self.all_source_ids),
                    "regional_source_counts": {source_id: len(fallback) for source_id in self.all_source_ids},
                    "fallback": True,
                }
                return fallback
            raise ProviderProtocolError(
                "EPN station catalog did not contain any station/coordinate rows."
            )

        known_station_ids = {row[0] for row in rows}
        source_membership: dict[str, set[str]] = {
            "epn_rob": set(known_station_ids),
            "epn_bev": set(),
            "epn_bkg": set(),
            "epn_ign": set(),
        }
        membership_errors: dict[str, str] = {}

        async def load_epn_members(source_id: str, urls: tuple[str, ...]) -> tuple[str, set[str], str | None]:
            members: set[str] = set()
            errors: list[str] = []
            for index, url in enumerate(urls):
                try:
                    source_html = await self._cached_text(
                        f"epn-data-centre-members:{source_id}:{index}",
                        url=url,
                    )
                    members.update(_epn_data_centre_station_ids(source_html, known_station_ids))
                except (ProviderError, OSError) as exc:
                    errors.append(str(exc))
            if members:
                return source_id, members, None if not errors else " | ".join(errors)
            return source_id, set(), " | ".join(errors) if errors else "station list was empty"

        async def load_ign_members() -> tuple[str, set[str], str | None]:
            try:
                source_html = await self._cached_text(
                    "epn-data-centre-members:epn_ign",
                    url=self.ign_station_list_url,
                )
                marker_ids = _rgp_station_markers(source_html)
                members = {
                    station_id
                    for station_id in known_station_ids
                    if station_id[:4] in marker_ids
                }
                if not members:
                    return "epn_ign", set(), "station list was empty"
                return "epn_ign", members, None
            except (ProviderError, OSError) as exc:
                return "epn_ign", set(), str(exc)

        membership_results = await asyncio.gather(
            load_epn_members("epn_bev", (self.bev_membership_url,)),
            load_epn_members("epn_bkg", self.bkg_membership_urls),
            load_ign_members(),
        )
        for source_id, members, error in membership_results:
            if members:
                source_membership[source_id] = members
            if error:
                membership_errors[source_id] = error

        # Metadata endpoints are auxiliary.  If M3G is temporarily unavailable,
        # keep BEV/BKG usable as fallback servers rather than showing zero Europe
        # stations.  IGN gets a conservative FRA fallback when the RGP station
        # page is unavailable.
        if not source_membership["epn_bev"]:
            source_membership["epn_bev"] = set(known_station_ids)
        if not source_membership["epn_bkg"]:
            source_membership["epn_bkg"] = set(known_station_ids)
        if not source_membership["epn_ign"]:
            source_membership["epn_ign"] = {
                station_id for station_id in known_station_ids if station_id.endswith("FRA")
            }

        stations: list[Station] = []
        for station_id, city, _country_name, latitude, longitude in rows:
            station_sources = [
                source_id
                for source_id in self.all_source_ids
                if station_id in source_membership[source_id]
            ]
            stations.append(
                Station(
                    id=station_id,
                    marker_name=city or station_id[:4],
                    latitude=latitude,
                    longitude=longitude,
                    country=station_id[-3:] if len(station_id) >= 9 else None,
                    network=["EPN"],
                    data_networks=[self.data_network],
                    # The GUI's second level represents station networks.  The
                    # physical ROB/BEV/BKG/IGN mirrors stay in metadata and are
                    # handled internally by EPNProvider fallback.
                    regional_sources=["europe_epn"],
                    providers=[self.name],
                    aliases=[station_id[:4]],
                    sampling_rates=["30S"],
                    metadata={
                        "catalog_sources": [self.name],
                        "catalog_source": self.station_catalog_source,
                        "source_type": self.source_type,
                        "epn_data_centres": station_sources,
                    },
                )
            )

        self._station_source_membership = {
            station.id.upper(): set(station.metadata.get("epn_data_centres", []))
            for station in stations
        }
        # ROB is authoritative from the same full EPN catalog.  Mark the other
        # memberships reliable only if their official metadata parsed cleanly.
        self._source_membership_reliable = {"epn_rob"}.union(
            {
                source_id
                for source_id in ("epn_bev", "epn_bkg", "epn_ign")
                if source_id not in membership_errors
            }
        )

        source_counts = {
            source_id: len(members)
            for source_id, members in source_membership.items()
        }
        self.last_station_catalog_stats = {
            "catalog_complete": True,
            "epn_catalog_version": 6,
            "epn_station_count": len(stations),
            "station_catalog_source": self.station_catalog_source,
            "data_centres": list(self.all_source_ids),
            "regional_source_counts": source_counts,
            "data_centre_membership_errors": membership_errors,
        }
        return stations

    async def _search_bev(
        self,
        request: ObservationRequest,
        source_id: str,
    ) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            doy = datetime_to_doy(day)
            if str(request.rinex) == "2":
                roots = [f"{self.bev_base}/obs/{day.year}/{doy:03d}/"]
            elif str(request.rinex) in {"3", "4"}:
                roots = [f"{self.bev_base}/obs_v3/{day.year}/{doy:03d}/"]
            else:
                roots = [
                    f"{self.bev_base}/obs_v3/{day.year}/{doy:03d}/",
                    f"{self.bev_base}/obs/{day.year}/{doy:03d}/",
                ]
            for station in request.stations or []:
                for directory in roots:
                    found = await self._files_from_listing(
                        directory,
                        request,
                        station,
                        day,
                        source_id=source_id,
                        data_center="BEV",
                    )
                    if found:
                        files.extend(found)
                        break
        return files

    async def _search_bkg(
        self,
        request: ObservationRequest,
        source_id: str,
    ) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            doy = datetime_to_doy(day)
            # BKG documents EUREF/obs as the public archive for observations.
            # Its listing can contain both RINEX 2 and modern long-name files;
            # the common listing filter below enforces the requested version.
            directory = f"{self.bkg_base}/obs/{day.year}/{doy:03d}/"
            for station in request.stations or []:
                files.extend(
                    await self._files_from_listing(
                        directory,
                        request,
                        station,
                        day,
                        source_id=source_id,
                        data_center="BKG",
                    )
                )
        return files

    async def _search_rob(
        self,
        request: ObservationRequest,
        source_id: str,
    ) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for station in request.stations or []:
            station_id = station.upper()
            url = self._hdc_query_url(station_id, request)
            payload = await self._cached_json(url)
            if not isinstance(payload, list):
                raise ProviderProtocolError("ROB/EPN API returned an unexpected schema.")
            for day in request.date_range.days():
                files.extend(
                    self._remote_files(
                        payload,
                        station_id,
                        day,
                        request=request,
                        source_id=source_id,
                        data_center="ROB / EPN",
                    )
                )
        return _dedupe_epn_remote_files(files)

    # Compatibility alias for code/tests written against the earlier EPN-HDC id.
    async def _search_hdc(
        self,
        request: ObservationRequest,
        source_id: str = "epn_rob",
    ) -> list[RemoteFile]:
        return await self._search_rob(request, self.legacy_source_aliases.get(source_id, source_id))

    async def _search_ign(
        self,
        request: ObservationRequest,
        source_id: str,
    ) -> list[RemoteFile]:
        sampling = _sampling_code(request.sampling)
        sampling_folder = {
            "01S": "1",
            "05S": "5",
            "10S": "10",
            "15S": "15",
            "30S": "30",
        }.get(sampling)
        if sampling_folder is None:
            return []

        files: list[RemoteFile] = []
        for day in request.date_range.days():
            doy = datetime_to_doy(day)
            if str(request.rinex) == "2":
                roots = [f"{self.ign_base}/data/{day.year}/{doy:03d}/data_{sampling_folder}/"]
            elif str(request.rinex) in {"3", "4"}:
                roots = [f"{self.ign_base}/data_v3/{day.year}/{doy:03d}/data_{sampling_folder}/"]
            else:
                roots = [
                    f"{self.ign_base}/data_v3/{day.year}/{doy:03d}/data_{sampling_folder}/",
                    f"{self.ign_base}/data/{day.year}/{doy:03d}/data_{sampling_folder}/",
                ]
            for station in request.stations or []:
                for directory in roots:
                    found = await self._files_from_listing(
                        directory,
                        request,
                        station,
                        day,
                        source_id=source_id,
                        data_center="IGN",
                        daily_only=(sampling == "30S"),
                    )
                    if found:
                        files.extend(found)
                        break
        return files

    async def _files_from_listing(
        self,
        directory: str,
        request: ObservationRequest,
        station: str,
        day: date,
        *,
        source_id: str,
        data_center: str,
        daily_only: bool = False,
    ) -> list[RemoteFile]:
        listing = await self._cached_text(f"listing:{directory}", url=directory)
        if not listing:
            return []
        station_upper = station.upper()
        station4 = station_upper[:4]
        station9 = station_upper[:9]
        files: list[RemoteFile] = []
        for filename in parse_listing_filenames(listing):
            upper = filename.upper()
            if not (upper.startswith(station9) or upper.startswith(station4)):
                continue
            if not _matches_rinex(filename, request):
                continue
            if not _is_epn_observation_filename(filename):
                continue
            if daily_only and not _is_daily_observation_filename(filename):
                continue
            files.append(
                _remote(
                    self.name,
                    urljoin(directory, filename),
                    filename,
                    station=station_upper,
                    day=day,
                    metadata={
                        "data_network": self.data_network,
                        "regional_provider": self.name,
                        "regional_source": source_id,
                        "data_center": data_center,
                        "source_type": "official_directory_listing",
                        "sampling": _sampling_from_name(filename),
                    },
                )
            )
        return _prefer_epn_rinex(files, request)

    def _hdc_query_url(
        self,
        station: str,
        request: ObservationRequest,
    ) -> str:
        params = {
            "startDate": request.date_range.start.isoformat(),
            "endDate": request.date_range.end.isoformat(),
            "rinexVersion": "all" if str(request.rinex) == "auto" else str(request.rinex),
        }
        return str(httpx.URL(f"{self.hdc_station_api}/{station.upper()}", params=params))

    def _query_url(
        self,
        station: str,
        day: date,
        request: ObservationRequest | None,
    ) -> str:
        params = {
            "startDate": day.isoformat(),
            "endDate": day.isoformat(),
            "rinexVersion": "all",
        }
        if station:
            params["stationId"] = station.upper()
        return str(httpx.URL(self.data_api, params=params))

    async def _cached_json(self, url: str) -> object:
        cached = self.discovery_cache.get(self.name, url)
        if cached is not None:
            return cached
        return self.discovery_cache.set(self.name, url, await self._get_json(url))

    async def _cached_text(self, key: str, *, url: str | None = None) -> str:
        cache_key = key
        cached = self.discovery_cache.get(self.name, cache_key)
        if isinstance(cached, str):
            return cached
        text = await self._get_text(url or key)
        return self.discovery_cache.set(self.name, cache_key, text)

    def _remote_files(
        self,
        payload: Iterable[object],
        station: str,
        day: date,
        *,
        request: ObservationRequest | None = None,
        source_id: str = "epn_rob",
        data_center: str = "ROB / EPN",
    ) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for item in payload:
            if not isinstance(item, dict):
                raise ProviderProtocolError("ROB/EPN API item is not an object.")
            item_day = _epn_item_day(item)
            if item_day is not None and item_day != day:
                continue
            url = item.get("url")
            filename = item.get("filename") or _filename_from_url(str(url or ""))
            if not url or not filename:
                raise ProviderProtocolError("ROB/EPN API item lacks url/filename.")
            if request is not None and not _matches_rinex(str(filename), request):
                continue
            if not _is_epn_observation_filename(str(filename)):
                continue
            files.append(
                _remote(
                    self.name,
                    str(url),
                    str(filename),
                    station=str(item.get("stationId") or station),
                    day=item_day or day,
                    metadata={
                        "data_network": self.data_network,
                        "regional_provider": self.name,
                        "regional_source": source_id,
                        "data_center": data_center,
                        "rinex_version": str(item.get("rinexVersion", "")),
                        "sampling": _sampling_from_name(str(filename)),
                    },
                )
            )
        return _prefer_epn_rinex(files, request) if request is not None else files


class GeoNetNZProvider(RegionalLiveProvider):
    name = "geonet_nz"
    data_network = "new_zealand"
    source_type = "s3_public"
    # GeoNet migrated data.geonet.org.nz to the versioned /v1/data/ URL
    # structure on 17 March 2026.  Keep that service for the current UTC day,
    # but use GeoNet's official AWS Open Data archive for completed historical
    # days.  The S3 archive is the stable/routine-download path published by
    # GeoNet and avoids a slow/failed HTML directory listing leaving the GUI in
    # PLAN when a historical station/day has no file.
    rinex_base = "https://data.geonet.org.nz/v1/data/gnss/rinex"
    archive_base = "https://geonet-open-data.s3-ap-southeast-2.amazonaws.com"
    archive_prefix = "gnss/rinex"
    archive_max_pages = 10
    station_info = (
        "https://geonet-meta.s3-ap-southeast-2.amazonaws.com/share/sitelogs/"
        "station.info.geonet"
    )
    # GeoNet Network Data API. Sensor type 8 is GNSS/GPS and the active-station
    # query supplies authoritative map coordinates, which station.info.geonet
    # does not provide in a simple machine-readable form.
    station_api = (
        "https://api.geonet.org.nz/network/station"
        "?sensorType=8&endDate=9999-01-01"
    )

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        wanted: set[str] = set()
        requested_by_alias: dict[str, str] = {}
        for station in request.stations or []:
            station_id = station.upper()
            wanted.add(station_id)
            requested_by_alias[station_id] = station_id
            if len(station_id) >= 4:
                wanted.add(station_id[:4])
                requested_by_alias.setdefault(station_id[:4], station_id)

        for day in request.date_range.days():
            names = await self._day_listing(day)
            historical = self._use_archive(day)
            for name in names:
                station_id = _station_id_from_rinex_name(name)
                if wanted and station_id not in wanted and station_id[:4] not in wanted:
                    continue
                if not _matches_rinex(name, request):
                    continue
                canonical_station = requested_by_alias.get(
                    station_id, requested_by_alias.get(station_id[:4], station_id)
                )
                if historical:
                    url = (
                        f"{self.archive_base}/{self.archive_prefix}/"
                        f"{day.year}/{datetime_to_doy(day):03d}/{name}"
                    )
                else:
                    url = (
                        f"{self.rinex_base}/{day.year}/"
                        f"{datetime_to_doy(day):03d}/{name}"
                    )
                files.append(
                    _remote(
                        self.name,
                        url,
                        name,
                        station=canonical_station,
                        day=day,
                        metadata={
                            "data_network": self.data_network,
                            "regional_provider": self.name,
                            "sampling": _sampling_from_name(name),
                            "discovery_source": "aws_open_data" if historical else "data_api",
                        },
                    )
                )
        return files

    def _with_download_mirrors(self, rob_remote: RemoteFile) -> RemoteFile:
        """Prefer BKG for transfer while preserving robust mirror fallbacks."""
        day = rob_remote.date
        if day is None:
            return rob_remote
        doy = datetime_to_doy(day)
        filename = rob_remote.filename
        lower = filename.lower()
        modern = "_" in filename

        def candidate(url: str, source_id: str, data_center: str) -> RemoteFile:
            item = rob_remote.model_copy(deep=True)
            item.url = url
            item.metadata["regional_source"] = source_id
            item.metadata["data_center"] = data_center
            item.metadata["discovery_source"] = "ROB / EPN API"
            item.metadata["download_source"] = source_id
            item.fallback_candidates = []
            return item

        primary = candidate(
            f"{self.bkg_base}/obs/{day.year}/{doy:03d}/{filename}",
            "epn_bkg",
            "BKG",
        )
        fallbacks: list[RemoteFile] = []

        bev_folder = "obs_v3" if modern else "obs"
        fallbacks.append(
            candidate(
                f"{self.bev_base}/{bev_folder}/{day.year}/{doy:03d}/{filename}",
                "epn_bev",
                "BEV",
            )
        )

        rob_fallback = rob_remote.model_copy(deep=True)
        rob_fallback.metadata["regional_source"] = "epn_rob"
        rob_fallback.metadata["data_center"] = "ROB / EPN"
        rob_fallback.metadata["discovery_source"] = "ROB / EPN API"
        rob_fallback.metadata["download_source"] = "epn_rob"
        rob_fallback.fallback_candidates = []
        fallbacks.append(rob_fallback)

        if _sampling_from_name(filename) == "30S":
            ign_root = "data_v3" if modern else "data"
            fallbacks.append(
                candidate(
                    f"{self.ign_base}/{ign_root}/{day.year}/{doy:03d}/data_30/{filename}",
                    "epn_ign",
                    "IGN",
                )
            )

        primary.fallback_candidates = fallbacks
        primary.metadata["mirror_order"] = ",".join(
            ["BKG", "BEV", "ROB / EPN"] + (["IGN"] if len(fallbacks) == 3 else [])
        )
        return primary

    async def fetch_station_catalog(self) -> list[Station]:
        # GeoNet station.info is useful for the canonical site label, while the
        # Network Data API is the reliable coordinate source for GNSS/GPS sites.
        # Treat either source as independently useful so a temporary failure of
        # one endpoint does not make New Zealand disappear from the GUI.
        latest: dict[str, str] = {}
        try:
            text = await self._get_text(self.station_info)
        except (ProviderError, ProviderProtocolError):
            text = ""
        for line in text.splitlines():
            if not line or line.startswith("*") or line.startswith(" SITE"):
                continue
            code = line[:5].strip().upper()
            if not code:
                continue
            latest[code] = line

        coordinates: dict[str, tuple[float, float, str]] = {}
        try:
            payload = await self._get_json(self.station_api)
            coordinates = _geonet_gnss_station_coordinates(payload)
        except (ProviderError, ProviderProtocolError):
            coordinates = {}

        codes = sorted(set(latest) | set(coordinates))
        stations: list[Station] = []
        for code in codes:
            line = latest.get(code, "")
            latitude = longitude = None
            api_name = ""
            if code in coordinates:
                latitude, longitude, api_name = coordinates[code]
            marker_name = _geonet_station_name(line) if line else api_name
            stations.append(
                Station(
                    id=f"{code}00NZL" if len(code) == 4 else f"{code}0NZL",
                    marker_name=marker_name or code,
                    latitude=latitude,
                    longitude=longitude,
                    country="NZL",
                    network=["geonet"],
                    data_networks=[self.data_network],
                    providers=[self.name],
                    aliases=[code],
                    metadata={
                        "catalog_sources": [self.name],
                        "source_type": self.source_type,
                        "source_station_id": code,
                        "coordinate_source": "GeoNet Network Data API" if latitude is not None else "",
                    },
                )
            )

        self.last_station_catalog_stats = {
            "station_count": len(stations),
            "mapped_station_count": sum(
                1 for station in stations
                if station.latitude is not None and station.longitude is not None
            ),
            "geonet_network_api_gnss_count": len(coordinates),
        }
        return stations

    def _use_archive(self, day: date) -> bool:
        # GeoNet states that AWS Open Data is synchronised daily.  A completed
        # historical UTC day is therefore better served by the archive; the
        # current day stays on data.geonet.org.nz for freshest availability.
        return day < datetime.now(timezone.utc).date()

    async def _day_listing(self, day: date) -> list[str]:
        key = f"rinex:{day.year}:{datetime_to_doy(day):03d}"
        cached = self.discovery_cache.get(self.name, key)
        if cached is not None:
            return cached

        if self._use_archive(day):
            try:
                names = await self._archive_day_listing(day)
            except (ProviderError, ProviderProtocolError):
                # The official data API remains a compatibility fallback if AWS
                # itself is temporarily unavailable.  An *empty* AWS listing is
                # authoritative for a completed day and returns immediately as
                # no-data instead of probing another service and keeping PLAN open.
                names = await self._data_api_day_listing(day)
        else:
            names = await self._data_api_day_listing(day)

        return self.discovery_cache.set(self.name, key, names)

    async def _data_api_day_listing(self, day: date) -> list[str]:
        url = f"{self.rinex_base}/{day.year}/{datetime_to_doy(day):03d}/"
        text = await self._get_text(url)
        return [
            name
            for name in parse_listing_filenames(text)
            if _is_observation_archive_name(name)
        ]

    async def _archive_day_listing(self, day: date) -> list[str]:
        prefix = f"{self.archive_prefix}/{day.year}/{datetime_to_doy(day):03d}/"
        names: list[str] = []
        continuation: str | None = None
        seen_tokens: set[str] = set()

        for _ in range(self.archive_max_pages):
            params = {
                "list-type": "2",
                "prefix": prefix,
                "max-keys": "1000",
            }
            if continuation:
                params["continuation-token"] = continuation
            url = f"{self.archive_base}/?{urlencode(params)}"
            text = await self._get_text(url)
            page_names, is_truncated, next_token = _parse_s3_listing(text, prefix)
            names.extend(page_names)
            if not is_truncated:
                return _dedupe_preserve_order(names)
            if not next_token or next_token in seen_tokens:
                raise ProviderProtocolError(
                    "GeoNet AWS archive returned an invalid continuation token."
                )
            seen_tokens.add(next_token)
            continuation = next_token

        raise ProviderProtocolError("GeoNet AWS archive listing exceeded the page limit.")


def _is_observation_archive_name(name: str) -> bool:
    return name.lower().endswith((
        ".rnx.gz",
        ".crx.gz",
        ".d.z",
        ".d.gz",
        "o.gz",
    ))


def _station_id_from_rinex_name(name: str) -> str:
    # RINEX 3/4 long names start with the 9-char marker ID.  Legacy RINEX 2
    # daily files start with the 4-char site code and have no underscores.
    first = name.split("_", 1)[0].upper()
    if "_" in name:
        return first
    return first[:4]


def _parse_s3_listing(text: str, prefix: str) -> tuple[list[str], bool, str | None]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ProviderProtocolError(
            "GeoNet AWS archive returned malformed XML."
        ) from exc

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    names: list[str] = []
    is_truncated = False
    next_token: str | None = None
    for element in root.iter():
        tag = local_name(element.tag)
        value = (element.text or "").strip()
        if tag == "Key" and value.startswith(prefix):
            name = value.rsplit("/", 1)[-1]
            if name and _is_observation_archive_name(name):
                names.append(name)
        elif tag == "IsTruncated":
            is_truncated = value.lower() == "true"
        elif tag == "NextContinuationToken" and value:
            next_token = value
    return names, is_truncated, next_token


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


class RBMCProvider(RegionalLiveProvider):
    # The official IBGE YYYY/DOY directory itself is authoritative for daily
    # availability, so the core may ask this provider to enumerate the complete
    # directory without first supplying station IDs from the local catalogue.
    network_directory_discovery = True
    name = "rbmc_br"
    data_network = "brazil"
    source_type = "directory_listing"
    rinex3_base = (
        "https://geoftp.ibge.gov.br/informacoes_sobre_posicionamento_geodesico/"
        "rbmc/dados_RINEX3"
    )
    rinex2_base = (
        "https://geoftp.ibge.gov.br/informacoes_sobre_posicionamento_geodesico/"
        "rbmc/dados"
    )
    one_second_base = (
        "https://geoftp.ibge.gov.br/informacoes_sobre_posicionamento_geodesico/"
        "rbmc/dados_RINEX3_1s"
    )
    report_base = (
        "https://geoftp.ibge.gov.br/informacoes_sobre_posicionamento_geodesico/"
        "rbmc/relatorio"
    )
    station_xml_url = f"{report_base}/estacoes.xml"
    station_log_base = f"{report_base}/log_sirgas"
    # IBGE also publishes an official RBMC cartogram as KMZ.  Unlike the compact
    # operational XML, the KMZ carries map coordinates for most of the national
    # network and is therefore the preferred coordinate enrichment source.
    cartogram_base = (
        "https://geoftp.ibge.gov.br/informacoes_sobre_posicionamento_geodesico/"
        "rbmc/cartogramas"
    )
    # The IBGE cartogram directory is an Apache index whose text decoding can vary
    # by server/proxy.  Do not make map coordinates depend on parsing that index.
    # As of 2026-08-16 the newest official KMZ published by IBGE is RBMC_2024.kmz,
    # so request the binary file directly first.  Older official snapshots remain
    # as fallbacks if IBGE replaces/removes a file.
    cartogram_direct_urls = (
        f"{cartogram_base}/RBMC_2024.kmz",
        f"{cartogram_base}/RBMC_2023.kmz",
        f"{cartogram_base}/rbmc_2019.kmz",
    )
    # Ship the official 2024 RBMC cartogram with GNSS Go and seed a persistent
    # user copy at ~/.gnssgo/RBMC_2024.kmz.  Brazil map coordinates are read
    # from that local file first on every catalog refresh; network metadata is
    # not required to draw the RBMC network.
    local_cartogram_filename = "RBMC_2024.kmz"
    station_log_concurrency = 16

    @classmethod
    def bundled_cartogram_path(cls) -> Path:
        return Path(__file__).resolve().parent.parent / "resources" / cls.local_cartogram_filename

    @classmethod
    def project_cartogram_path(cls) -> Path:
        """Return the source-tree ``.gnssgo`` RBMC snapshot when available.

        Desktop development builds are normally started from this repository.
        Keeping the official KMZ in ``<project>/.gnssgo`` makes the map source
        explicit and, importantly, prevents an older file in the user's home
        directory from silently winning over the file shipped with the project.
        """
        try:
            project_root = Path(__file__).resolve().parents[3]
        except IndexError:
            return Path.cwd() / ".gnssgo" / cls.local_cartogram_filename
        return project_root / ".gnssgo" / cls.local_cartogram_filename

    @classmethod
    def local_cartogram_path(cls) -> Path:
        # Keep a user-level mirror for installed builds, but it is no longer
        # blindly trusted over the project/bundled official snapshot.
        return Path.home() / ".gnssgo" / cls.local_cartogram_filename

    @classmethod
    def _cartogram_station_count(cls, path: Path) -> int:
        try:
            return len(cls._parse_rbmc_kmz(path.read_bytes())) if path.is_file() else 0
        except OSError:
            return 0

    @classmethod
    def ensure_local_cartogram(cls) -> Path | None:
        """Select the most complete local official RBMC KMZ and repair stale copies.

        Earlier builds returned ``~/.gnssgo/RBMC_2024.kmz`` whenever it merely
        existed.  A stale/partial file there could therefore contain only 20--30
        placemarks and permanently mask the 153-station official snapshot bundled
        with the current project.  Choose the candidate that actually parses the
        most stations, then mirror that file to the user-level path.
        """
        project = cls.project_cartogram_path()
        local = cls.local_cartogram_path()
        bundled = cls.bundled_cartogram_path()

        # Tie order is intentional: an explicit project .gnssgo file wins, then
        # the packaged resource, then an old user-level mirror.
        candidates = [project, bundled, local]
        ranked: list[tuple[int, int, Path]] = []
        seen: set[Path] = set()
        for priority, path in enumerate(candidates):
            try:
                key = path.resolve()
            except OSError:
                key = path
            if key in seen:
                continue
            seen.add(key)
            count = cls._cartogram_station_count(path)
            if count:
                ranked.append((count, -priority, path))
        if not ranked:
            return None

        _count, _priority, best = max(ranked, key=lambda item: (item[0], item[1]))

        # Repair a stale user-level mirror automatically.  The map can still use
        # ``best`` directly if the home directory is read-only.
        try:
            if best.resolve() != local.resolve():
                local.parent.mkdir(parents=True, exist_ok=True)
                temp = local.with_suffix(local.suffix + ".tmp")
                shutil.copyfile(best, temp)
                temp.replace(local)
                if cls._cartogram_station_count(local) == _count:
                    return local
        except OSError:
            pass
        return best

    def _local_cartogram_station_catalog(self) -> tuple[list[Station], str]:
        path = self.ensure_local_cartogram()
        if path is None:
            return [], ""
        try:
            raw = path.read_bytes()
            coordinates = self._parse_rbmc_kmz(raw)
        except OSError:
            coordinates = {}

        # If the persistent copy is damaged, restore the packaged snapshot once.
        if not coordinates:
            bundled = self.bundled_cartogram_path()
            if bundled.is_file() and bundled != path:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(bundled, path)
                    coordinates = self._parse_rbmc_kmz(path.read_bytes())
                except OSError:
                    coordinates = {}
        if not coordinates:
            return [], str(path)

        stations: list[Station] = []
        for code, (latitude, longitude, height) in sorted(coordinates.items()):
            station = self._rbmc_station(
                code,
                latitude,
                longitude,
                source=str(path),
            )
            station.height = height
            station.metadata["coordinate_source"] = str(path)
            station.metadata["catalog_snapshot"] = self.local_cartogram_filename
            stations.append(station)
        return stations, str(path)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        wanted = {station.upper() for station in request.stations or []}
        for day in request.date_range.days():
            if _sampling_code(request.sampling) == "01S":
                entries = await self._one_second_entries(day)
                base = self.one_second_base
            else:
                base = self.rinex3_base
                entries = [
                    (name, f"{base}/{day.year}/{datetime_to_doy(day):03d}/{name}")
                    for name in await self._day_listing(day, base)
                ]
            for name, url in entries:
                station_id = _station_from_filename(name)
                if wanted and station_id not in wanted and station_id[:4] not in wanted:
                    continue
                if not _matches_rinex(name, request):
                    continue
                files.append(
                    _remote(
                        self.name,
                        url,
                        name,
                        station=station_id,
                        day=day,
                        metadata={
                            "data_network": self.data_network,
                            "regional_provider": self.name,
                            "sampling": _sampling_from_name(name),
                            "duration": _duration_from_name(name),
                        },
                    )
                )
        return files

    def _with_download_mirrors(self, rob_remote: RemoteFile) -> RemoteFile:
        """Prefer BKG for transfer while preserving robust mirror fallbacks."""
        day = rob_remote.date
        if day is None:
            return rob_remote
        doy = datetime_to_doy(day)
        filename = rob_remote.filename
        lower = filename.lower()
        modern = "_" in filename

        def candidate(url: str, source_id: str, data_center: str) -> RemoteFile:
            item = rob_remote.model_copy(deep=True)
            item.url = url
            item.metadata["regional_source"] = source_id
            item.metadata["data_center"] = data_center
            item.metadata["discovery_source"] = "ROB / EPN API"
            item.metadata["download_source"] = source_id
            item.fallback_candidates = []
            return item

        primary = candidate(
            f"{self.bkg_base}/obs/{day.year}/{doy:03d}/{filename}",
            "epn_bkg",
            "BKG",
        )
        fallbacks: list[RemoteFile] = []

        bev_folder = "obs_v3" if modern else "obs"
        fallbacks.append(
            candidate(
                f"{self.bev_base}/{bev_folder}/{day.year}/{doy:03d}/{filename}",
                "epn_bev",
                "BEV",
            )
        )

        rob_fallback = rob_remote.model_copy(deep=True)
        rob_fallback.metadata["regional_source"] = "epn_rob"
        rob_fallback.metadata["data_center"] = "ROB / EPN"
        rob_fallback.metadata["discovery_source"] = "ROB / EPN API"
        rob_fallback.metadata["download_source"] = "epn_rob"
        rob_fallback.fallback_candidates = []
        fallbacks.append(rob_fallback)

        if _sampling_from_name(filename) == "30S":
            ign_root = "data_v3" if modern else "data"
            fallbacks.append(
                candidate(
                    f"{self.ign_base}/{ign_root}/{day.year}/{doy:03d}/data_30/{filename}",
                    "epn_ign",
                    "IGN",
                )
            )

        primary.fallback_candidates = fallbacks
        primary.metadata["mirror_order"] = ",".join(
            ["BKG", "BEV", "ROB / EPN"] + (["IGN"] if len(fallbacks) == 3 else [])
        )
        return primary

    async def fetch_station_catalog(self) -> list[Station]:
        return await self._fetch_station_catalog(
            enrich_site_logs=not bool(getattr(self, "_defer_site_log_enrichment", False))
        )

    async def _fetch_station_catalog(
        self,
        *,
        enrich_site_logs: bool,
    ) -> list[Station]:
        # Brazil map catalog: use the bundled/local official KMZ directly.  This
        # is deliberately independent of estacoes.xml, SIRGAS CRD, and site-log
        # matching: every Placemark in RBMC_2024.kmz becomes a map Station.
        local_stations, local_source = self._local_cartogram_station_catalog()
        if local_stations:
            self._last_rbmc_cartogram_debug = {
                "local_path": local_source,
                "parsed_counts": {local_source: len(local_stations)},
                "mapped": len(local_stations),
                "source": local_source,
            }
            self.last_station_catalog_stats = {
                "catalog_complete": True,
                "station_count": len(local_stations),
                "mapped_station_count": len(local_stations),
                "ibge_cartogram_mapped": len(local_stations),
                "ibge_cartogram_source": local_source,
                "catalog_source_used": local_source,
                "local_rbmc_kmz": True,
                "local_rbmc_kmz_station_count": len(local_stations),
            }
            return local_stations

        # Fallback only: if the local packaged snapshot is unavailable/corrupt,
        # retain the older online identity/enrichment chain rather than failing.
        # IBGE publishes a compact XML station catalogue next to the descriptive
        # PDFs.  Prefer it for station identity/status.  Some generations of that
        # XML do not carry geodetic coordinates, so the official IGS-style site
        # logs under relatorio/log_sirgas are used as a coordinate fallback.
        try:
            xml_text = await self._get_text(self.station_xml_url)
            stations = self._parse_station_xml(xml_text)
        except Exception:
            stations = []

        catalog_source = self.station_xml_url
        if not stations:
            text = await self._get_text(f"{self.report_base}/")
            fallback: list[Station] = []
            for name in parse_listing_filenames(text):
                match = re.match(r"Descritivo_([A-Za-z0-9]{4})\.pdf$", name)
                if not match:
                    continue
                code = match.group(1).upper()
                fallback.append(
                    self._rbmc_station(code, None, None, source=f"{self.report_base}/")
                )
            stations = fallback
            catalog_source = f"{self.report_base}/"

        xml_mapped = sum(
            1
            for station in stations
            if station.latitude is not None and station.longitude is not None
        )

        # The XML is reliable for station identity but often lacks coordinates.
        # IBGE's official RBMC cartogram is a tiny KMZ and contains map positions
        # for far more stations than the SIRGAS/IGS subset.  Use it before the
        # slower per-station site-log fallback.
        cartogram_mapped = 0
        cartogram_source = ""
        if stations:
            try:
                cartogram_mapped, cartogram_source = (
                    await self._enrich_from_ibge_cartogram(stations)
                )
            except Exception:
                cartogram_mapped = 0
                cartogram_source = ""

        log_mapped = 0
        log_source = ""
        if stations and enrich_site_logs:
            try:
                log_mapped, log_source = await self._enrich_from_ibge_site_logs(stations)
            except Exception:
                # Site-log enrichment is a map-quality fallback.  The RBMC station
                # catalogue remains usable even if one metadata endpoint is down.
                log_mapped = 0
                log_source = ""

        mapped = sum(
            1
            for station in stations
            if station.latitude is not None and station.longitude is not None
        )
        self.last_station_catalog_stats = {
            "catalog_complete": bool(stations),
            "station_count": len(stations),
            "xml_mapped_station_count": xml_mapped,
            "mapped_station_count": mapped,
            "catalog_source_used": catalog_source,
        }
        if cartogram_mapped:
            self.last_station_catalog_stats["ibge_cartogram_mapped"] = cartogram_mapped
        if cartogram_source:
            self.last_station_catalog_stats["ibge_cartogram_source"] = cartogram_source
        cartogram_debug = getattr(self, "_last_rbmc_cartogram_debug", None)
        if cartogram_debug:
            # Keep compact values here so GUI diagnostics can show whether the
            # direct KMZ was fetched and how many placemarks matched RBMC codes.
            self.last_station_catalog_stats["ibge_cartogram_attempted"] = list(
                cartogram_debug.get("attempted", [])
            )
            self.last_station_catalog_stats["ibge_cartogram_parsed_counts"] = dict(
                cartogram_debug.get("parsed_counts", {})
            )
            if cartogram_debug.get("listing_error"):
                self.last_station_catalog_stats["ibge_cartogram_listing_error"] = str(
                    cartogram_debug["listing_error"]
                )
        if log_mapped:
            self.last_station_catalog_stats["ibge_site_log_mapped"] = log_mapped
        if log_source:
            self.last_station_catalog_stats["ibge_coordinate_source"] = log_source
        if stations and mapped == 0:
            self.last_station_catalog_stats["coordinate_warning"] = (
                "IBGE station coordinates were not available; map pins may be incomplete."
            )
        return stations

    def _parse_station_xml(self, text: str) -> list[Station]:
        if not text.strip():
            return []
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []

        stations: dict[str, Station] = {}
        # The public IBGE XML has changed field names over time.  Parse by local
        # tag name and accept the common Portuguese/English aliases instead of
        # coupling GNSS Go to one exact XML serialization.
        for element in root.iter():
            values: dict[str, str] = {}
            for node in element.iter():
                key = node.tag.rsplit("}", 1)[-1].strip().lower()
                value = (node.text or "").strip()
                if value and key not in values:
                    values[key] = value
                for attr_key, attr_value in node.attrib.items():
                    attr_key = attr_key.rsplit("}", 1)[-1].strip().lower()
                    attr_value = str(attr_value).strip()
                    if attr_value and attr_key not in values:
                        values[attr_key] = attr_value

            code = self._rbmc_xml_code(values)
            if not code:
                continue
            latitude = self._rbmc_xml_coordinate(
                values, ("latitude", "lat", "latitude_gd", "lat_gd", "coord_lat"), kind="lat"
            )
            longitude = self._rbmc_xml_coordinate(
                values,
                ("longitude", "lon", "lng", "long", "longitude_gd", "lon_gd", "coord_lon"),
                kind="lon",
            )
            if latitude is None or longitude is None:
                xyz = self._rbmc_xml_xyz(values)
                if xyz is not None:
                    xyz_lat, xyz_lon, _ = self._rbmc_ecef_to_geodetic(*xyz)
                    if latitude is None:
                        latitude = xyz_lat
                    if longitude is None:
                        longitude = xyz_lon
            if latitude is not None and not -90.0 <= latitude <= 90.0:
                latitude = None
            if longitude is not None and not -180.0 <= longitude <= 180.0:
                longitude = None
            # RBMC is Brazilian; reject clearly unrelated coordinates if the XML
            # directory also contains descriptive records for neighbouring sites.
            if latitude is not None and longitude is not None:
                if not (-35.5 <= latitude <= 6.5 and -75.5 <= longitude <= -32.0):
                    latitude = longitude = None
            current = stations.get(code)
            candidate = self._rbmc_station(
                code, latitude, longitude, source=self.station_xml_url, metadata=values
            )
            if current is None or (
                current.latitude is None and candidate.latitude is not None
            ):
                stations[code] = candidate
        return list(stations.values())

    async def _enrich_from_ibge_cartogram(
        self,
        stations: list[Station],
    ) -> tuple[int, str]:
        """Fill missing RBMC coordinates directly from IBGE's official KMZ.

        The KMZ itself is the authoritative map source.  We intentionally do not
        require the ``cartogramas/`` HTML directory listing to decode correctly:
        some Windows/proxy combinations return that index with a legacy encoding,
        which previously made the KMZ branch fail silently and left only the small
        IGS/SIRGAS coordinate subset on the map.
        """
        missing = {
            station.id[:4].upper(): station
            for station in stations
            if station.latitude is None or station.longitude is None
        }
        if not missing:
            return 0, ""

        known_codes = set(missing) | {station.id[:4].upper() for station in stations}
        debug: dict[str, Any] = {
            "direct_urls": list(self.cartogram_direct_urls),
            "attempted": [],
            "parsed_counts": {},
        }

        async def try_kmz(url: str) -> tuple[int, str]:
            debug["attempted"].append(url)
            try:
                response = await self._request_get(url)
                debug[f"status:{url}"] = response.status_code
                if response.status_code >= 400 or not response.content:
                    return 0, ""
                coordinates = self._parse_rbmc_kmz(
                    response.content, known_codes=known_codes
                )
                debug["parsed_counts"][url] = len(coordinates)
            except Exception as exc:
                debug[f"error:{url}"] = f"{type(exc).__name__}: {exc}"
                return 0, ""
            if not coordinates:
                return 0, ""

            mapped = 0
            for code, station in missing.items():
                value = coordinates.get(code)
                if not value:
                    continue
                latitude, longitude, height = value
                if not (-38.0 <= latitude <= 8.0 and -78.0 <= longitude <= -28.0):
                    continue
                station.latitude = latitude
                station.longitude = longitude
                if station.height is None and height is not None:
                    station.height = height
                station.metadata["coordinate_source"] = url
                mapped += 1
            return mapped, url if mapped else ""

        # Primary path: fetch the known official KMZ binaries directly.
        for url in self.cartogram_direct_urls:
            mapped, source = await try_kmz(url)
            if mapped:
                debug["mapped"] = mapped
                debug["source"] = source
                self._last_rbmc_cartogram_debug = debug
                return mapped, source

        # Last-resort compatibility path: discover any newer RBMC_YYYY.kmz from
        # the index if the fixed snapshots all fail.  Failure here is diagnostic
        # only and no longer prevents the direct KMZ attempts above.
        listing_url = f"{self.cartogram_base}/"
        try:
            listing = await self._get_text(listing_url)
            candidates: list[tuple[int, str]] = []
            for raw_name in parse_listing_filenames(listing):
                filename = raw_name.rsplit("/", 1)[-1]
                match = re.fullmatch(r"(?i)rbmc[_-](\d{4})\.kmz", filename)
                if match:
                    candidates.append((int(match.group(1)), filename))
            for match in re.finditer(r"(?i)\b(rbmc[_-](\d{4})\.kmz)\b", listing):
                candidates.append((int(match.group(2)), match.group(1)))
            direct = set(self.cartogram_direct_urls)
            for _, filename in sorted(set(candidates), reverse=True):
                url = f"{self.cartogram_base}/{filename}"
                if url in direct:
                    continue
                mapped, source = await try_kmz(url)
                if mapped:
                    debug["mapped"] = mapped
                    debug["source"] = source
                    self._last_rbmc_cartogram_debug = debug
                    return mapped, source
        except Exception as exc:
            debug["listing_error"] = f"{type(exc).__name__}: {exc}"

        debug["mapped"] = 0
        self._last_rbmc_cartogram_debug = debug
        return 0, self.cartogram_direct_urls[0]

    @classmethod
    def _parse_rbmc_kmz(
        cls,
        raw: bytes,
        *,
        known_codes: set[str] | None = None,
    ) -> dict[str, tuple[float, float, float | None]]:
        """Parse IBGE RBMC KMZ/KML placemarks into 4-char station coordinates."""
        if not raw:
            return {}
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                kml_names = [name for name in archive.namelist() if name.lower().endswith(".kml")]
                if not kml_names:
                    return {}
                text = archive.read(kml_names[0]).decode("utf-8", errors="replace")
        except (OSError, zipfile.BadZipFile, KeyError):
            # Also accept raw KML in tests/proxies that transparently unpack KMZ.
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                return {}

        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return {}

        known = {code.upper()[:4] for code in (known_codes or set())}
        result: dict[str, tuple[float, float, float | None]] = {}
        for placemark in root.iter():
            if placemark.tag.rsplit("}", 1)[-1].lower() != "placemark":
                continue
            fields: list[str] = []
            coordinate_text = ""
            for node in placemark.iter():
                local = node.tag.rsplit("}", 1)[-1].lower()
                value = (node.text or "").strip()
                if value and local in {"name", "description", "simpledata", "value"}:
                    fields.append(value)
                if value and local == "coordinates" and not coordinate_text:
                    coordinate_text = value
            if not coordinate_text:
                continue

            joined = " ".join(fields).upper()
            code = ""
            long_match = re.search(r"\b([A-Z0-9]{4})00BRA\b", joined)
            if long_match:
                code = long_match.group(1)
            elif known:
                for candidate in known:
                    if re.search(rf"(?<![A-Z0-9]){re.escape(candidate)}(?![A-Z0-9])", joined):
                        code = candidate
                        break
            else:
                short = re.search(r"(?<![A-Z0-9])([A-Z0-9]{4})(?![A-Z0-9])", joined)
                if short:
                    code = short.group(1)
            if not code:
                continue

            first = coordinate_text.split()[0].strip()
            parts = [part.strip() for part in first.split(",")]
            if len(parts) < 2:
                continue
            try:
                longitude = float(parts[0])
                latitude = float(parts[1])
                height = float(parts[2]) if len(parts) > 2 and parts[2] else None
            except ValueError:
                continue
            if not (-38.0 <= latitude <= 8.0 and -78.0 <= longitude <= -28.0):
                continue
            result[code] = (latitude, longitude, height)
        return result

    async def _enrich_from_ibge_site_logs(
        self,
        stations: list[Station],
    ) -> tuple[int, str]:
        """Fill missing RBMC coordinates from IBGE's official site-log archive.

        ``estacoes.xml`` is primarily an operational station list and its schema has
        changed over time.  The ``log_sirgas`` directory contains standard GNSS site
        logs with an approximate ITRF XYZ position, which is stable and sufficient
        for plotting stations on the map.  Only stations still missing coordinates
        are requested, and the newest log per station is used.
        """
        missing = {
            station.id[:4].upper(): station
            for station in stations
            if station.latitude is None or station.longitude is None
        }
        if not missing:
            return 0, ""

        listing_url = f"{self.station_log_base}/"
        listing = await self._get_text(listing_url)
        names = parse_listing_filenames(listing)
        # Some Apache/theme variants are not perfectly parsed by the generic
        # listing helper; recover log names directly from the HTML as well.
        names.extend(
            re.findall(
                r"(?i)\b[A-Z0-9]{4}00BRA_\d{8}\.log\b",
                listing,
            )
        )

        latest: dict[str, tuple[str, str]] = {}
        for raw_name in names:
            filename = raw_name.rsplit("/", 1)[-1]
            match = re.fullmatch(
                r"(?i)([A-Z0-9]{4})00BRA_(\d{8})\.log",
                filename,
            )
            if not match:
                continue
            code, stamp = match.group(1).upper(), match.group(2)
            if code not in missing:
                continue
            previous = latest.get(code)
            if previous is None or stamp > previous[0]:
                latest[code] = (stamp, filename)

        if not latest:
            return 0, listing_url

        semaphore = asyncio.Semaphore(self.station_log_concurrency)

        async def load_one(code: str, filename: str):
            url = f"{self.station_log_base}/{filename}"
            try:
                async with semaphore:
                    text = await self._get_text(url)
                coordinate = self._rbmc_site_log_coordinate(text)
                return code, coordinate, url
            except Exception:
                return code, None, url

        loaded = await asyncio.gather(
            *(load_one(code, item[1]) for code, item in latest.items())
        )
        mapped = 0
        for code, coordinate, url in loaded:
            if coordinate is None:
                continue
            latitude, longitude, height = coordinate
            # The official RBMC catalogue also contains a handful of partner sites
            # close to Brazil's borders.  Keep a generous South-America envelope,
            # while still rejecting corrupted XYZ/angle values.
            if not (-38.0 <= latitude <= 8.0 and -78.0 <= longitude <= -28.0):
                continue
            station = missing[code]
            station.latitude = latitude
            station.longitude = longitude
            if station.height is None and height is not None:
                station.height = height
            station.metadata["coordinate_source"] = url
            mapped += 1
        return mapped, listing_url

    @classmethod
    def _rbmc_site_log_coordinate(
        cls,
        text: str,
    ) -> tuple[float, float, float | None] | None:
        """Parse map coordinates from a standard IGS/SIRGAS station log."""
        if not text.strip():
            return None

        def line_value(label: str) -> str | None:
            match = re.search(
                rf"(?im)^\s*{label}[^:\r\n]*:\s*([^\r\n]+)",
                text,
            )
            return match.group(1).strip() if match else None

        # Many site logs provide latitude/longitude as compact DMS or decimal
        # values.  Prefer those when present.
        raw_lat = line_value(r"Latitude")
        raw_lon = line_value(r"Longitude")
        if raw_lat and raw_lon:
            latitude = cls._rbmc_parse_angle(raw_lat, limit=90.0)
            longitude = cls._rbmc_parse_angle(raw_lon, limit=180.0)
            if latitude is not None and longitude is not None:
                raw_height = line_value(r"(?:Elevation|Ellipsoid(?:al)? height|Height)")
                height = cls._rbmc_first_number(raw_height) if raw_height else None
                return latitude, longitude, height

        xyz: list[float] = []
        for axis in ("X", "Y", "Z"):
            raw = line_value(rf"{axis}\s*(?:coordinate|coordenada)?")
            value = cls._rbmc_first_number(raw) if raw else None
            if value is None:
                return None
            xyz.append(value)
        radius = math.sqrt(sum(value * value for value in xyz))
        if not 5.5e6 <= radius <= 7.2e6:
            return None
        return cls._rbmc_ecef_to_geodetic(*xyz)

    @staticmethod
    def _rbmc_first_number(raw: str | None) -> float | None:
        if not raw:
            return None
        text = str(raw).strip()

        # IBGE descriptive reports and some metadata exports use Brazilian
        # number formatting, e.g. ``5.101.506,4602`` and
        # ``-3.682.915,9169`` for Cartesian coordinates.  The old parser only
        # accepted one decimal separator and therefore read these values as
        # ``5.101`` / ``-3.682``.  Normalize grouped thousands before falling
        # back to the generic scientific-notation parser.
        grouped = re.search(
            r"[-+]?\s*\d{1,3}(?:\.\d{3}){2,}(?:,\d+)?(?:[DEde][-+]?\d+)?",
            text,
        )
        if grouped:
            token = grouped.group(0).replace(" ", "").replace(".", "").replace(",", ".")
            try:
                return float(token.replace("D", "E").replace("d", "e"))
            except ValueError:
                pass

        match = re.search(r"[-+]?\s*\d+(?:[.,]\d+)?(?:[DEde][-+]?\d+)?", text)
        if not match:
            return None
        token = match.group(0).replace(" ", "").replace(",", ".")
        try:
            return float(token.replace("D", "E").replace("d", "e"))
        except ValueError:
            return None

    @staticmethod
    def _rbmc_normalize_key(value: str) -> str:
        text = unicodedata.normalize("NFKD", value)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9]", "", text.lower())

    @classmethod
    def _rbmc_xml_code(cls, values: dict[str, str]) -> str | None:
        normalized = {cls._rbmc_normalize_key(k): v for k, v in values.items()}
        for key in (
            "codigo", "code", "sigla", "station", "stationid",
            "estacao", "id", "nome", "name", "identificador", "codestacao",
        ):
            value = normalized.get(key, "").upper()
            long_match = re.search(r"\b([A-Z0-9]{4})00BRA\b", value)
            if long_match:
                return long_match.group(1)
            match = re.search(r"(?<![A-Z0-9])([A-Z0-9]{4})(?![A-Z0-9])", value)
            if match and not match.group(1).isdigit():
                return match.group(1)
        for value in values.values():
            match = re.search(r"\b([A-Z0-9]{4})00BRA\b", value.upper())
            if match:
                return match.group(1)
        return None

    @classmethod
    def _rbmc_xml_coordinate(
        cls,
        values: dict[str, str],
        aliases: tuple[str, ...],
        *,
        kind: str,
    ) -> float | None:
        normalized = {cls._rbmc_normalize_key(k): v for k, v in values.items()}
        wanted = {cls._rbmc_normalize_key(alias) for alias in aliases}
        if kind == "lat":
            wanted.update({"latitudedecimal", "latdecimal", "latgeodesica", "latitudegeodesica"})
            fuzzy = lambda key: ("latitude" in key or key.startswith("lat")) and "altura" not in key
            limit = 90.0
        else:
            wanted.update({"longitudedecimal", "londecimal", "lngdecimal", "longitudegeodesica"})
            fuzzy = lambda key: ("longitude" in key or key.startswith("lon") or key.startswith("lng"))
            limit = 180.0

        for key, raw in normalized.items():
            if key not in wanted and not fuzzy(key):
                continue
            value = cls._rbmc_parse_angle(raw, limit=limit)
            if value is not None:
                return value

        # Some IBGE XML generations split degrees/minutes/seconds into fields.
        prefix_tokens = ("lat", "latitude") if kind == "lat" else ("lon", "long", "longitude")
        def component(names: tuple[str, ...]) -> float | None:
            for key, raw in normalized.items():
                if not any(token in key for token in prefix_tokens):
                    continue
                if not any(name in key for name in names):
                    continue
                match = re.search(r"[-+]?\d+(?:[.,]\d+)?", raw)
                if match:
                    try:
                        return float(match.group(0).replace(",", "."))
                    except ValueError:
                        pass
            return None
        deg = component(("grau", "degree", "deg"))
        minute = component(("minuto", "minute", "min"))
        second = component(("segundo", "second", "sec"))
        if deg is not None and minute is not None:
            second = second or 0.0
            sign = -1.0 if deg < 0 else 1.0
            # Hemisphere can be stored in a separate field.
            all_text = " ".join(normalized.values()).upper()
            if kind == "lat" and re.search(r"\bS\b|SUL", all_text):
                sign = -1.0
            if kind == "lon" and re.search(r"\bW\b|\bO\b|OESTE", all_text):
                sign = -1.0
            value = sign * (abs(deg) + abs(minute) / 60.0 + abs(second) / 3600.0)
            if abs(value) <= limit:
                return value
        return None

    @staticmethod
    def _rbmc_parse_angle(raw: str, *, limit: float) -> float | None:
        text = str(raw).strip().replace(",", ".")
        if not text:
            return None

        # A large fraction of the official IBGE station reports use a detached
        # sign such as ``- 09º 27' 55,00289"``.  ``[-+]?\d+`` does not keep
        # that minus because of the intervening blank, which used to turn most
        # Brazilian latitudes/longitudes positive; the subsequent Brazil bounds
        # check then discarded them.  Preserve an explicit leading sign before
        # tokenising the numeric components.
        explicit_sign = 0
        sign_match = re.match(r"^\s*([+-])\s*(?=\d)", text)
        if sign_match:
            explicit_sign = -1 if sign_match.group(1) == "-" else 1

        nums = re.findall(r"[-+]?\s*\d+(?:\.\d+)?", text)
        nums = [item.replace(" ", "") for item in nums]
        if not nums:
            return None

        upper = text.upper()
        hemisphere_negative = bool(
            re.search(r"(?:^|[^A-Z])(S|W|O)(?:$|[^A-Z])", upper)
            or "SUL" in upper
            or "OESTE" in upper
        )
        hemisphere_positive = bool(
            re.search(r"(?:^|[^A-Z])(N|E)(?:$|[^A-Z])", upper)
            or "NORTE" in upper
            or "LESTE" in upper
        )

        try:
            is_dms = len(nums) >= 3 and (
                any(ch in text for ch in ("°", "º", "'", '"', "′", "″"))
                or re.search(r"\b[NSEWO]\b", upper)
            )
            if is_dms:
                deg, minute, second = map(float, nums[:3])
                if explicit_sign:
                    sign = float(explicit_sign)
                elif deg < 0 or hemisphere_negative:
                    sign = -1.0
                elif hemisphere_positive:
                    sign = 1.0
                else:
                    sign = 1.0
                if minute >= 60.0 or second >= 60.0:
                    return None
                value = sign * (abs(deg) + abs(minute) / 60.0 + abs(second) / 3600.0)
                return value if abs(value) <= limit else None
            number = float(nums[0])
        except ValueError:
            return None

        if explicit_sign:
            number = abs(number) * explicit_sign

        # Compact DDMMSS.s / DDDMMSS.s is common in legacy geodetic exports.
        if abs(number) > limit and abs(number) < (limit + 1) * 10000:
            if explicit_sign:
                sign = float(explicit_sign)
            else:
                sign = -1.0 if number < 0 or hemisphere_negative else 1.0
            compact = abs(number)
            deg = int(compact // 10000)
            minute = int((compact - deg * 10000) // 100)
            second = compact - deg * 10000 - minute * 100
            if minute < 60 and second < 60:
                value = sign * (deg + minute / 60.0 + second / 3600.0)
                if abs(value) <= limit:
                    return value
        if hemisphere_negative:
            number = -abs(number)
        elif hemisphere_positive:
            number = abs(number)
        return number if abs(number) <= limit else None

    @classmethod
    def _rbmc_xml_xyz(cls, values: dict[str, str]) -> tuple[float, float, float] | None:
        normalized = {cls._rbmc_normalize_key(k): v for k, v in values.items()}
        aliases = {
            "x": ("x", "coordx", "coordenadax", "ecefx", "xitrf", "xcartesiano"),
            "y": ("y", "coordy", "coordenaday", "ecefy", "yitrf", "ycartesiano"),
            "z": ("z", "coordz", "coordenadaz", "ecefz", "zitrf", "zcartesiano"),
        }
        xyz: list[float] = []
        for axis in ("x", "y", "z"):
            value = None
            for key in aliases[axis]:
                raw = normalized.get(key)
                if raw is None:
                    continue
                value = cls._rbmc_first_number(raw)
                if value is not None:
                    break
            if value is None:
                return None
            xyz.append(value)
        radius = math.sqrt(sum(value * value for value in xyz))
        if not 5.5e6 <= radius <= 7.2e6:
            return None
        return xyz[0], xyz[1], xyz[2]

    @staticmethod
    def _rbmc_ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
        a = 6378137.0
        f = 1.0 / 298.257222101
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
                break
            height = p / cos_lat - radius
            next_lat = math.atan2(z, p * (1.0 - e2 * radius / (radius + height)))
            if abs(next_lat - latitude) < 1e-13:
                latitude = next_lat
                break
            latitude = next_lat
        return math.degrees(latitude), math.degrees(longitude), height


    def _rbmc_station(
        self,
        code: str,
        latitude: float | None,
        longitude: float | None,
        *,
        source: str,
        metadata: dict[str, str] | None = None,
    ) -> Station:
        meta = {
            "catalog_sources": [self.name],
            "catalog_source": source,
            "source_type": self.source_type,
            "source_station_id": code,
        }
        if metadata:
            for key in ("nome", "name", "cidade", "city", "uf", "estado", "status"):
                if metadata.get(key):
                    meta[key] = metadata[key]
        return Station(
            id=f"{code}00BRA",
            marker_name=code,
            latitude=latitude,
            longitude=longitude,
            country="BRA",
            network=["rbmc"],
            data_networks=[self.data_network],
            providers=[self.name],
            aliases=[code],
            sampling_rates=["01S", "30S"],
            rinex_versions=["2", "3"],
            metadata=meta,
        )

    async def _day_listing(self, day: date, base: str) -> list[str]:
        key = f"{base}:{day.year}:{datetime_to_doy(day):03d}"
        cached = self.discovery_cache.get(self.name, key)
        if cached is not None:
            return cached
        url = f"{base}/{day.year}/{datetime_to_doy(day):03d}/"
        text = await self._get_text(url)
        names = [
            name
            for name in parse_listing_filenames(text)
            if name.lower().endswith((".crx.gz", ".rnx.gz", ".zip"))
        ]
        return self.discovery_cache.set(self.name, key, names)

    async def _one_second_entries(self, day: date) -> list[tuple[str, str]]:
        day_url = f"{self.one_second_base}/{day.year}/{datetime_to_doy(day):03d}/"
        key = f"one-second-root:{day.year}:{datetime_to_doy(day):03d}"
        cached = self.discovery_cache.get(self.name, key)
        if cached is not None:
            return cached
        root_text = await self._get_text(day_url)
        root_names = parse_listing_filenames(root_text)
        entries: list[tuple[str, str]] = []

        # Current IBGE high-rate layout nests quarter-hour RINEX files below HH folders.
        # Keep a direct-file fallback so older archive layouts still work.
        for name in root_names:
            if name.lower().endswith((".crx.gz", ".rnx.gz", ".zip")):
                entries.append((name, f"{day_url}{name}"))

        hour_dirs = sorted({name for name in root_names if re.fullmatch(r"[0-2]\d", name)})
        for hour in hour_dirs:
            hour_url = f"{day_url}{hour}/"
            hour_key = (
                f"one-second-hour:{day.year}:{datetime_to_doy(day):03d}:{hour}"
            )
            hour_names = self.discovery_cache.get(self.name, hour_key)
            if hour_names is None:
                hour_text = await self._get_text(hour_url)
                hour_names = [
                    name
                    for name in parse_listing_filenames(hour_text)
                    if name.lower().endswith((".crx.gz", ".rnx.gz", ".zip"))
                ]
                self.discovery_cache.set(self.name, hour_key, hour_names)
            entries.extend((name, f"{hour_url}{name}") for name in hour_names)

        return self.discovery_cache.set(self.name, key, entries)


def _ga_station(site: dict, regional_sources: list[str]) -> Station | None:
    ident = site.get("siteIdentification") or {}
    location = site.get("siteLocation") or {}
    station_id = _ga_station_id(site)
    four_char = (ident.get("fourCharacterId") or "").upper()
    if not station_id and four_char:
        station_id = f"{four_char}00AUS"
    if not station_id:
        return None
    lat, lon, height = _coordinates_from_ga(location)
    source_registry = default_regional_source_registry()
    return Station(
        id=station_id,
        marker_name=ident.get("siteName"),
        domes=ident.get("iersDOMESNumber"),
        latitude=lat,
        longitude=lon,
        height=height,
        country=station_id[-3:] if len(station_id) >= 9 else "AUS",
        network=["ga"],
        data_networks=["australia"],
        regional_sources=regional_sources,
        providers=["ga"],
        aliases=[four_char] if four_char else [],
        metadata={
            "catalog_sources": ["ga"],
            "source_network": ident.get("monumentInscription") or "",
            "source_type": "official_api",
            "source_station_id": four_char or station_id,
            "ga_source_networks": [
                source_registry.get(source_id).name for source_id in regional_sources
            ],
        },
    )


def _ga_station_from_cors_site(
    site: dict,
    regional_sources: list[str],
    source_names: list[str],
) -> Station | None:
    station_id = _ga_cors_station_id(site)
    four_char = str(site.get("fourCharacterId") or "").upper()
    if not station_id:
        return None
    lat, lon, height = _coordinates_from_cors_site(site)
    source_registry = default_regional_source_registry()
    return Station(
        id=station_id,
        marker_name=site.get("name"),
        domes=site.get("domesNumber"),
        latitude=lat,
        longitude=lon,
        height=height,
        country="AUS",
        network=["ga"],
        data_networks=["australia"],
        regional_sources=regional_sources,
        providers=["ga"],
        aliases=[four_char] if four_char else [],
        metadata={
            "catalog_sources": ["ga"],
            "source_type": "official_api",
            "source_station_id": four_char or station_id,
            "ga_source_networks": source_names
            or [source_registry.get(source_id).name for source_id in regional_sources],
        },
    )


def _ga_station_id(site: dict) -> str:
    ident = site.get("siteIdentification") or {}
    return (ident.get("nineCharacterId") or "").upper()


def _ga_cors_station_id(site: dict) -> str:
    four_char = str(site.get("fourCharacterId") or "").upper()
    return f"{four_char}00AUS" if four_char else ""


def _ga_site_logs(payload: dict) -> list[dict]:
    raw_sites = payload.get("_embedded", {}).get("siteLogs", [])
    if not isinstance(raw_sites, list):
        raise ProviderProtocolError("GA station API did not include siteLogs.")
    return [site for site in raw_sites if isinstance(site, dict)]


def _ga_cors_sites(payload: dict) -> list[dict]:
    raw_sites = payload.get("_embedded", {}).get("corsSites", [])
    if not isinstance(raw_sites, list):
        raise ProviderProtocolError("GA CORS site API did not include corsSites.")
    return [site for site in raw_sites if isinstance(site, dict)]


def _ga_total_elements(payload: dict) -> int | None:
    page = payload.get("page") or {}
    try:
        return int(page.get("totalElements"))
    except (TypeError, ValueError):
        return None


def _ga_next_url(payload: dict) -> str | None:
    links = payload.get("_links") or {}
    next_link = links.get("next") or {}
    href = next_link.get("href") if isinstance(next_link, dict) else None
    return str(href) if href else None


def _ga_page_number(payload: dict) -> int:
    page = payload.get("page") or {}
    try:
        return int(page.get("number") or 0)
    except (TypeError, ValueError):
        raise ProviderProtocolError("GA station API page number is invalid.") from None


def _ga_australia_sources(site: dict) -> list[str]:
    text = _ga_source_text(site)
    source_registry = default_regional_source_registry()
    return sorted(
        {
            source.id
            for source in source_registry.all("australia")
            if _source_name_pattern(source.name).search(text)
        }
    )


def _ga_australia_sources_from_names(source_names: Iterable[str]) -> list[str]:
    source_registry = default_regional_source_registry()
    source_ids: set[str] = set()
    for name in source_names:
        if source_registry.contains(name, data_network="australia"):
            source_ids.add(source_registry.normalize(name))
    return sorted(source_ids)


def _ga_cors_source_names(site: dict, network_names: dict[int, str]) -> list[str]:
    names: list[str] = []
    for tenancy in site.get("networkTenancies") or []:
        if not isinstance(tenancy, dict):
            continue
        network_id = _int_or_none(tenancy.get("corsNetworkId"))
        name = network_names.get(network_id) if network_id is not None else None
        if name and name not in names:
            names.append(name)
    return names


def _ga_excluded_source_names(site: dict, names: Iterable[str]) -> list[str]:
    text = _ga_source_text(site)
    return sorted({name for name in names if _source_name_pattern(name).search(text)})


def _ga_source_text(site: dict[str, Any]) -> str:
    fields: list[str] = []
    ident = site.get("siteIdentification") or {}
    if isinstance(ident, dict):
        for value in ident.values():
            if isinstance(value, str):
                fields.append(value)
    for key in ("formInformation", "moreInformation", "siteLogText"):
        value = site.get(key)
        if isinstance(value, str):
            fields.append(value)
        elif isinstance(value, dict):
            fields.extend(str(item) for item in value.values() if isinstance(item, str))
    return "\n".join(fields)


def _source_name_pattern(name: str) -> re.Pattern[str]:
    tokens = [token for token in re.split(r"[-_\s]+", name.upper()) if token]
    if not tokens:
        return re.compile(r"a^")
    body = r"[-_\s]+".join(re.escape(token) for token in tokens)
    return re.compile(rf"(?<![A-Z0-9]){body}(?![A-Z0-9])", re.IGNORECASE)


def _coordinates_from_ga(location: dict) -> tuple[float | None, float | None, float | None]:
    approximate = location.get("approximatePosition") or {}
    geodetic = approximate.get("geodeticPosition") or {}
    position = geodetic.get("coordinates") or []
    if len(position) < 2:
        return None, None, None
    lat = _float_or_none(position[0])
    lon = _wrap_lon(_float_or_none(position[1]))
    height = _float_or_none(position[2]) if len(position) > 2 else None
    return lat, lon, height


def _coordinates_from_cors_site(site: dict) -> tuple[float | None, float | None, float | None]:
    position = (site.get("approximatePosition") or {}).get("coordinates") or []
    if len(position) < 2:
        return None, None, None
    lat = _float_or_none(position[0])
    lon = _wrap_lon(_float_or_none(position[1]))
    height = _float_or_none(position[2]) if len(position) > 2 else None
    return lat, lon, height



class _EPNStationTableParser(HTMLParser):
    """Small dependency-free parser for the ROB EPN HDC station table."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            value = " ".join("".join(self._cell).split())
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def _epn_station_rows(html: str) -> list[tuple[str, str, str, float, float]]:
    parser = _EPNStationTableParser()
    parser.feed(html)
    records: dict[str, tuple[str, str, str, float, float]] = {}
    for cells in parser.rows:
        station_index = next(
            (
                index
                for index, value in enumerate(cells)
                if re.fullmatch(r"[A-Z0-9]{9}", value.strip().upper())
            ),
            None,
        )
        if station_index is None or len(cells) < station_index + 5:
            continue
        station_id = cells[station_index].strip().upper()
        city = cells[station_index + 1].strip()
        country_name = cells[station_index + 2].strip()
        latitude = _float_or_none(cells[station_index + 3])
        longitude = _wrap_lon(_float_or_none(cells[station_index + 4]))
        if latitude is None or longitude is None:
            continue
        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            continue
        records[station_id] = (station_id, city, country_name, latitude, longitude)
    return [records[key] for key in sorted(records)]


def _epn_data_centre_station_ids(
    html: str,
    known_station_ids: set[str],
) -> set[str]:
    """Return EPN station ids advertised by one M3G data-centre page.

    M3G renders the 9-character station identifiers in the page content.  The
    intersection with the already validated ROB/EPN catalog makes the parser
    deliberately conservative and prevents unrelated 9-character tokens from
    becoming station memberships.
    """

    if not html or not known_station_ids:
        return set()
    candidates = {
        value.upper()
        for value in re.findall(r"(?<![A-Z0-9])[A-Z0-9]{9}(?![A-Z0-9])", html.upper())
    }
    return candidates.intersection(known_station_ids)


def _rgp_station_markers(html: str) -> set[str]:
    """Extract 4-character station markers from IGN/RGP's official station table."""

    if not html:
        return set()
    parser = _EPNStationTableParser()
    parser.feed(html)
    markers: set[str] = set()
    for cells in parser.rows:
        if not cells:
            continue
        marker = cells[0].strip().upper()
        if re.fullmatch(r"[A-Z0-9]{4}", marker):
            markers.add(marker)
    return markers


def _epn_item_day(item: dict[str, Any]) -> date | None:
    value = item.get("date")
    if value:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            pass
    year = _int_or_none(item.get("year"))
    doy = _int_or_none(item.get("DOY") or item.get("doy"))
    if year is not None and doy is not None:
        try:
            return date.fromordinal(date(year, 1, 1).toordinal() + doy - 1)
        except ValueError:
            return None
    return None


def _is_epn_observation_filename(filename: str) -> bool:
    upper = filename.upper()
    # Modern long-name RINEX uses the two-character data-type field; the second
    # character O denotes observations (MO, GO, RO, ...), while N denotes
    # navigation (EN, GN, RN, ...).  The BKG EUREF directory contains both.
    if "_" in upper:
        return bool(re.search(r"_[A-Z]O\.(?:CRX|RNX)(?:\.GZ)?$", upper))
    # Legacy Compact/regular RINEX observation extensions are D/O.
    return bool(re.search(r"\.\d{2}[DO](?:\.GZ|\.Z|\.ZIP)?$", upper))


def _is_daily_observation_filename(filename: str) -> bool:
    """True for a full-day observation file (long-name 01D or RINEX2 session 0)."""

    upper = filename.upper()
    if "_" in upper:
        return "_01D_" in upper and _is_epn_observation_filename(filename)
    return bool(
        re.match(r"^[A-Z0-9]{4}\d{3}0\.\d{2}[DO](?:\.GZ|\.Z|\.ZIP)?$", upper)
    )


def _prefer_epn_rinex(
    files: list[RemoteFile],
    request: ObservationRequest | None,
) -> list[RemoteFile]:
    unique = _dedupe_epn_remote_files(files)
    if request is None or str(request.rinex) != "auto" or len(unique) <= 1:
        return unique
    # For Auto prefer modern long-name compact RINEX while retaining all valid
    # variants for the core resolver/fallback machinery.
    return sorted(
        unique,
        key=lambda remote: (
            0 if "_" in remote.filename and remote.filename.lower().endswith(".crx.gz") else
            1 if "_" in remote.filename else
            2,
            remote.filename,
        ),
    )


def _dedupe_epn_remote_files(files: list[RemoteFile]) -> list[RemoteFile]:
    seen: set[tuple[str, str, str]] = set()
    result: list[RemoteFile] = []
    for remote in files:
        key = (str(remote.station or "").upper(), str(remote.date or ""), remote.filename)
        if key in seen:
            continue
        seen.add(key)
        result.append(remote)
    return result

def _remote(
    provider: str,
    url: str,
    filename: str,
    *,
    station: str,
    day: date,
    size: int | None = None,
    metadata: dict[str, str] | None = None,
) -> RemoteFile:
    compression = (
        ".gz"
        if filename.lower().endswith(".gz")
        else ".Z"
        if filename.lower().endswith(".z")
        else None
    )
    return RemoteFile(
        provider=provider,
        url=url,
        filename=filename,
        size=size,
        compression=compression,
        data_type="obs",
        station=station.upper(),
        date=day,
        metadata={
            "logical_id": f"obs:{station.upper()}:{day.isoformat()}:{filename}",
            **(metadata or {}),
        },
    )


def _filename_from_url(url: str) -> str:
    match = re.search(r"filename%3D%22([^%]+)%22", url)
    if match:
        return match.group(1)
    return url.split("?", 1)[0].rstrip("/").split("/")[-1]


def _ga_rinex_day(item: dict[str, Any], filename: str) -> date | None:
    start = item.get("startDate")
    if start:
        try:
            return date.fromisoformat(str(start)[:10])
        except ValueError:
            pass

    # Long RINEX: ALIC00AUS_R_20262130000_01D_30S_MO.crx.gz
    match = re.search(r"_[RS]_(\d{4})(\d{3})\d{4}_", filename.upper())
    if match:
        try:
            start_of_year = date(int(match.group(1)), 1, 1).toordinal()
            return date.fromordinal(start_of_year + int(match.group(2)) - 1)
        except ValueError:
            return None

    # RINEX 2: alic2130.26d.gz (DDD + session + YY)
    match = re.match(r"^[A-Z0-9]{4}(\d{3})[0-9A-Z]\.([0-9]{2})[A-Z]", filename.upper())
    if match:
        year_2 = int(match.group(2))
        year = 2000 + year_2 if year_2 < 80 else 1900 + year_2
        try:
            return date.fromordinal(date(year, 1, 1).toordinal() + int(match.group(1)) - 1)
        except ValueError:
            return None
    return None


def _station_from_filename(filename: str) -> str:
    if "_" in filename:
        return filename.split("_", 1)[0].upper()
    return f"{filename[:4].upper()}00BRA"


def _matches_rinex(filename: str, request: ObservationRequest) -> bool:
    lower = filename.lower()
    rinex = str(request.rinex)
    if rinex == "2":
        return bool(re.search(r"\.\d{2}[do](?:\.gz|\.z|\.zip)?$", lower))
    if rinex in {"3", "4"}:
        return lower.endswith((".crx.gz", ".rnx.gz", ".crx", ".rnx"))
    return lower.endswith((".crx.gz", ".rnx.gz", ".crx", ".rnx", ".zip")) or bool(
        re.search(r"\.\d{2}[do](?:\.gz|\.z|\.zip)?$", lower)
    )


def _sampling_from_name(filename: str) -> str:
    tokens = [
        token
        for token in filename.upper().split("_")
        if re.fullmatch(r"\d{2}[SM]", token)
    ]
    for token in tokens:
        if token.endswith("S"):
            return token
    return tokens[-1] if tokens else ""


def _duration_from_name(filename: str) -> str:
    for token in filename.upper().split("_"):
        if re.fullmatch(r"\d{2}[DHM]", token):
            return token
    return ""


def _sampling_code(value: str | None) -> str:
    if not value:
        return "30S"
    text = value.upper().replace("SEC", "S").replace(" ", "")
    if text in {"1S", "01S"}:
        return "01S"
    if text in {"5S", "05S"}:
        return "05S"
    if text in {"10S", "15S", "30S", "01M"}:
        return text
    return text


def _geonet_gnss_station_coordinates(
    payload: object,
) -> dict[str, tuple[float, float, str]]:
    """Parse GeoNet /network/station GeoJSON into code -> (lat, lon, name)."""

    if not isinstance(payload, dict):
        return {}
    features = payload.get("features")
    if not isinstance(features, list):
        # Be liberal about a future wrapper while keeping the parser deterministic.
        features = payload.get("Features")
    if not isinstance(features, list):
        return {}

    result: dict[str, tuple[float, float, str]] = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") or feature.get("Properties") or {}
        geometry = feature.get("geometry") or feature.get("Geometry") or {}
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            continue
        code = str(
            properties.get("Code")
            or properties.get("code")
            or properties.get("Station")
            or properties.get("station")
            or ""
        ).strip().upper()
        coords = geometry.get("coordinates") or geometry.get("Coordinates")
        if not code or not isinstance(coords, (list, tuple)) or len(coords) < 2:
            continue
        longitude = _float_or_none(coords[0])
        latitude = _float_or_none(coords[1])
        if latitude is None or longitude is None:
            continue
        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            continue
        name = str(
            properties.get("Location")
            or properties.get("location")
            or properties.get("Name")
            or properties.get("name")
            or code
        ).strip()
        result[code] = (latitude, longitude, name)
    return result


def _geonet_station_name(line: str) -> str:
    return line[6:24].strip()


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _wrap_lon(value: float | None) -> float | None:
    if value is None:
        return None
    if value > 180:
        return value - 360
    if value < -180:
        return value + 360
    return value
