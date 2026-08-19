from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import date

import httpx

from gnssgo.models import (
    NavigationRequest,
    NavigationType,
    ObservationRequest,
    ProductRequest,
    ProductTier,
    ProductType,
    RemoteFile,
)
from gnssgo.products.naming import (
    ProductNamingRegistry,
    parse_product_filename,
    product_matches_request,
)
from gnssgo.products.resolver import logical_key
from gnssgo.providers.base import GNSSProvider, ProviderCapabilities
from gnssgo.providers.ftp import filter_existing_ftp_urls, list_ftp_filenames
from gnssgo.providers.listing import parse_listing_filenames
from gnssgo.utils.dates import datetime_to_doy
from gnssgo.utils.gps_time import datetime_to_gpsweek

DirectoryBuilder = Callable[[date], str]


class IGSMirrorPathResolver:
    def __init__(
        self,
        base_url: str,
        observation_directory: DirectoryBuilder,
        navigation_directories: Callable[[date, NavigationType], list[str]],
        product_directory: Callable[[int], str] | None = None,
        flat_daily: bool = False,
        fallback_to_candidates: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._observation_directory = observation_directory
        self._navigation_directories = navigation_directories
        self._product_directory = product_directory
        self.flat_daily = flat_daily
        self.fallback_to_candidates = fallback_to_candidates

    def observation_directory(self, day: date) -> str:
        return self._observation_directory(day)

    def navigation_directories(self, day: date, nav_type: NavigationType) -> list[str]:
        return self._navigation_directories(day, nav_type)

    def observation_candidates(self, station: str, day: date) -> list[str]:
        doy = datetime_to_doy(day)
        year2 = day.year % 100
        daily = self.observation_directory(day)
        station_upper = station.upper()
        station_lower = station.lower()
        return [
            f"{daily}{station_upper}_R_{day.year}{doy:03d}0000_01D_30S_MO.crx.gz",
            f"{daily}{station_lower[:4]}{doy:03d}0.{year2:02d}d.Z",
            f"{daily}{station_lower[:4]}{doy:03d}0.{year2:02d}d.gz",
        ]

    def navigation_candidates(self, day: date, nav_type: NavigationType) -> list[str]:
        doy = datetime_to_doy(day)
        year2 = day.year % 100
        suffix = _nav_suffix(nav_type)
        candidates: list[str] = []
        for directory in self.navigation_directories(day, nav_type):
            candidates.append(
                f"{directory}BRDC00IGS_R_{day.year}{doy:03d}0000_01D_{suffix}.rnx.gz"
            )
            if nav_type == NavigationType.MIXED:
                candidates.extend(
                    [
                        f"{directory}BRDC00WRD_R_{day.year}{doy:03d}0000_01D_MN.rnx.gz",
                        f"{directory}BRDM00DLR_S_{day.year}{doy:03d}0000_01D_MN.rnx.gz",
                        f"{directory}BRD400DLR_S_{day.year}{doy:03d}0000_01D_MN.rnx.gz",
                        f"{directory}brdc{doy:03d}0.{year2:02d}n.Z",
                        f"{directory}brdc{doy:03d}0.{year2:02d}n.gz",
                    ]
                )
        return candidates

    def product_candidates(self, day: date, product_type: ProductType) -> list[str]:
        if not self._product_directory:
            return []
        gps_week, dow = datetime_to_gpsweek(day)
        directory = self._product_directory(gps_week)
        mapping = {
            ProductType.ORBIT: [f"{directory}igs{gps_week}{dow}.sp3.Z"],
            ProductType.CLOCK: [f"{directory}igs{gps_week}{dow}.clk.Z"],
            ProductType.ERP: [f"{directory}igs{gps_week}7.erp.Z"],
            ProductType.BIAS: [],
            ProductType.IONEX: [],
            ProductType.SINEX: [],
            ProductType.ANTEX: [],
        }
        return mapping[product_type]

    def product_directories(self, day: date, product_type: ProductType) -> list[str]:
        if not self._product_directory:
            return []
        gps_week, _dow = datetime_to_gpsweek(day)
        directories = [self._product_directory(gps_week)]
        doy = datetime_to_doy(day)
        if product_type == ProductType.IONEX:
            directories.append(f"{self.base_url}/products/ionex/{day.year}/{doy:03d}/")
        if product_type == ProductType.BIAS:
            directories.append(f"{self.base_url}/products/bias/{day.year}/")
        return directories


class IGSMirrorProvider(GNSSProvider):
    def __init__(
        self,
        name: str,
        resolver: IGSMirrorPathResolver,
        check_existing: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.resolver = resolver
        self.check_existing = check_existing
        self.client = client or httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            trust_env=False,
        )

    def capabilities(self) -> ProviderCapabilities:
        products = ["orbit", "clock", "erp", "sinex"] if self.resolver._product_directory else []
        return ProviderCapabilities(
            observations=True,
            navigation=True,
            products=products,
            station_metadata=False,
            authentication_required=False,
        )

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            for station in request.stations or []:
                urls = await self._discover_observation_urls(station, day, request)
                for url in urls:
                    files.append(_remote(self.name, url, "obs", station=station, day=day))
        return files

    async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            urls = await self._discover_navigation_urls(day, request.nav_type)
            for url in urls:
                files.append(_remote(self.name, url, "nav", day=day))
        return files

    async def search_products(self, request: ProductRequest) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            for product_type in request.product_types:
                urls = await self._discover_product_urls(day, product_type, request)
                for url in urls:
                    files.append(_remote(self.name, url, product_type.value, day=day))
        return files

    async def health_check(self) -> dict[str, str]:
        return {"provider": self.name, "status": "configured"}

    async def _discover_observation_urls(
        self,
        station: str,
        day: date,
        request: ObservationRequest,
    ) -> list[str]:
        directory = self.resolver.observation_directory(day)
        if self.name == "noaa":
            directory = f"{directory}{station.lower()[:4]}/"
        filenames = await self._list_directory(directory)
        station_upper = station.upper()
        station_lower = station.lower()
        matches = [
            name
            for name in filenames
            if (
                name.startswith(station_upper)
                or name.startswith(station_lower)
                or name[:4].lower() == station_lower[:4]
            )
            and _matches_rinex_request(name, request)
        ]
        if matches:
            return _preferred_observation_urls([directory + name for name in matches])
        if not self.resolver.fallback_to_candidates:
            return []
        candidates = await self._existing_or_candidate_urls(
            self.resolver.observation_candidates(station, day)
        )
        return _preferred_observation_urls(candidates)

    async def _discover_navigation_urls(
        self,
        day: date,
        nav_type: NavigationType,
    ) -> list[str]:
        found: list[str] = []
        for directory in self.resolver.navigation_directories(day, nav_type):
            filenames = await self._list_directory(directory)
            matches = [name for name in filenames if _matches_navigation_request(name, nav_type)]
            found.extend(directory + name for name in matches)
        if found:
            return _preferred_navigation_urls(found, nav_type)
        if not self.resolver.fallback_to_candidates:
            return []
        return await self._existing_or_candidate_urls(
            self.resolver.navigation_candidates(day, nav_type)
        )

    async def _list_directory(self, directory: str) -> list[str]:
        if directory.startswith("ftp://"):
            return await asyncio.to_thread(list_ftp_filenames, directory)
        try:
            response = await self.client.get(directory)
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        return parse_listing_filenames(response.text)

    async def _discover_product_urls(
        self,
        day: date,
        product_type: ProductType,
        request: ProductRequest,
    ) -> list[str]:
        found: list[str] = []
        for directory in self.resolver.product_directories(day, product_type):
            filenames = await self._list_directory(directory)
            matches = [
                name
                for name in filenames
                if _matches_product_request(name, request, day, product_type)
            ]
            found.extend(directory + name for name in matches)
        if found:
            return _preferred_product_urls(found, request)
        if not self.resolver.fallback_to_candidates:
            return []
        fallback_names = ProductNamingRegistry().candidates(day, product_type, request)
        if not fallback_names:
            return await self._existing_or_candidate_urls(
                self.resolver.product_candidates(day, product_type)
            )
        directories = self.resolver.product_directories(day, product_type)
        if not directories:
            return []
        return await self._existing_or_candidate_urls(
            [directories[0] + name for name in fallback_names]
        )

    async def _existing_or_candidate_urls(self, urls: list[str]) -> list[str]:
        if not self.check_existing:
            return urls
        ftp_urls = [url for url in urls if url.startswith("ftp://")]
        http_urls = [url for url in urls if not url.startswith("ftp://")]
        existing_ftp = await asyncio.to_thread(filter_existing_ftp_urls, ftp_urls)
        return [*existing_ftp, *http_urls]


def _remote(
    provider: str,
    url: str,
    data_type: str,
    station: str | None = None,
    day: date | None = None,
) -> RemoteFile:
    filename = url.rstrip("/").split("/")[-1]
    compression = ".gz" if filename.endswith(".gz") else ".Z" if filename.endswith(".Z") else None
    metadata = _remote_metadata(data_type, station, day, filename)
    return RemoteFile(
        provider=provider,
        url=url,
        filename=filename,
        compression=compression,
        data_type=data_type,
        station=station,
        date=day,
        metadata=metadata,
    )


def _matches_rinex_request(filename: str, request: ObservationRequest) -> bool:
    lower = filename.lower()
    if request.rinex == "2":
        return LEGACY_OBS_RE.search(lower) is not None
    if request.rinex in {"3", "4"}:
        return lower.endswith((".crx.gz", ".rnx.gz", ".crx", ".rnx"))
    return lower.endswith((".crx.gz", ".rnx.gz", ".crx", ".rnx")) or (
        LEGACY_OBS_RE.search(lower) is not None
    )


def _matches_navigation_request(filename: str, nav_type: NavigationType) -> bool:
    lower = filename.lower()
    if nav_type == NavigationType.MIXED:
        return (
            filename.startswith(("BRDC", "BRDM", "BRD4"))
            and lower.endswith((".rnx.gz", ".rnx.z", ".rnx"))
        ) or LEGACY_BROADCAST_RE.search(lower) is not None
    suffix = _nav_suffix(nav_type).lower()
    return lower.endswith(f"_{suffix}.rnx.gz") or lower.endswith(f"_{suffix}.rnx")


def _preferred_observation_urls(urls: list[str]) -> list[str]:
    """Return one preferred representation for a station/day logical OBS file."""

    if not urls:
        return []

    def sort_key(url: str) -> tuple[int, str]:
        name = url.rsplit("/", 1)[-1].lower()
        if name.endswith((".crx.gz", ".rnx.gz")):
            priority = 0
        elif name.endswith(".d.z"):
            priority = 1
        elif name.endswith(".d.gz"):
            priority = 2
        else:
            priority = 3
        return priority, name

    return [min(urls, key=sort_key)]


def _preferred_navigation_urls(urls: list[str], nav_type: NavigationType) -> list[str]:
    if nav_type != NavigationType.MIXED:
        return sorted(urls)
    priority = ("BRDC00IGS", "BRDC00WRD", "BRDM", "BRD4", "brdc")
    result: list[str] = []
    for prefix in priority:
        result.extend(url for url in sorted(urls) if url.rsplit("/", 1)[-1].startswith(prefix))
    return result or sorted(urls)


def _matches_product_request(
    filename: str,
    request: ProductRequest,
    day: date,
    product_type: ProductType,
) -> bool:
    descriptor = parse_product_filename(filename)
    if not descriptor:
        return False
    if descriptor.product_type != product_type:
        return False
    return product_matches_request(descriptor, request, day)


def _preferred_product_urls(urls: list[str], request: ProductRequest) -> list[str]:
    tier_order = (
        [ProductTier.FINAL, ProductTier.RAPID, ProductTier.ULTRA]
        if request.tier == ProductTier.AUTO
        else [request.tier]
    )
    center_order = [request.center.upper()] if request.center.lower() != "auto" else [
        "IGS",
        "WUM",
        "GFZ",
        "COD",
        "ESA",
        "GRG",
        "CAS",
    ]
    return sorted(
        urls,
        key=lambda url: _product_sort_key(url.rsplit("/", 1)[-1], tier_order, center_order),
    )


def _product_sort_key(
    filename: str,
    tier_order: list[ProductTier],
    center_order: list[str],
) -> tuple[int, int, str]:
    descriptor = parse_product_filename(filename)
    if not descriptor:
        return (999, 999, filename)
    tier_index = tier_order.index(descriptor.tier) if descriptor.tier in tier_order else 999
    center_index = (
        center_order.index(descriptor.center) if descriptor.center in center_order else 999
    )
    return (tier_index, center_index, filename)


def _nav_suffix(nav_type: NavigationType) -> str:
    return {
        NavigationType.MIXED: "MN",
        NavigationType.GPS: "GN",
        NavigationType.GLONASS: "RN",
        NavigationType.GALILEO: "EN",
        NavigationType.BEIDOU: "CN",
    }[nav_type]


LEGACY_BROADCAST_RE = re.compile(r"brdc\d{3}0\.\d{2}[nglcp](?:\.gz|\.z)?$")
LEGACY_OBS_RE = re.compile(r"\.\d{2}[do](?:\.gz|\.z)?$")


def _logical_id(
    data_type: str,
    station: str | None,
    day: date | None,
    filename: str,
) -> str:
    if data_type not in {"obs", "nav"}:
        parsed = parse_product_filename(filename)
        if parsed:
            return logical_key(
                RemoteFile(
                    provider="logical",
                    url=f"https://example.invalid/{filename}",
                    filename=filename,
                    data_type=parsed.product_type.value,
                    date=day,
                    metadata={
                        "analysis_center": parsed.center,
                        "product_tier": parsed.tier.value,
                        "product_system": parsed.system.value,
                        "duration": parsed.duration or "",
                        "sampling": parsed.sampling or "",
                        "campaign": parsed.campaign or "",
                    },
                )
            ).model_dump_json()
    if data_type == "nav":
        return f"{data_type}:{day.isoformat() if day else ''}:{filename}"
    return f"{data_type}:{station or ''}:{day.isoformat() if day else ''}:{filename}"


def _remote_metadata(
    data_type: str,
    station: str | None,
    day: date | None,
    filename: str,
) -> dict[str, str]:
    metadata = {"logical_id": _logical_id(data_type, station, day, filename)}
    parsed = parse_product_filename(filename)
    if parsed:
        metadata.update(
            {
                "analysis_center": parsed.center,
                "product_tier": parsed.tier.value,
                "product_system": parsed.system.value,
                "duration": parsed.duration or "",
                "sampling": parsed.sampling or "",
                "campaign": parsed.campaign or "",
                "reference_frame": parsed.reference_frame or "",
                "product_format": parsed.format or "",
                "product_content": parsed.content or "",
                "product_type": parsed.product_type.value,
            }
        )
    return metadata


def whu_provider(check_existing: bool = True) -> IGSMirrorProvider:
    base = "ftp://igs.gnsswhu.cn/pub/gps"
    return IGSMirrorProvider(
        "whu",
        _yy_subdir_resolver(base),
        check_existing=check_existing,
    )


def kasi_provider(check_existing: bool = True) -> IGSMirrorProvider:
    base = "ftp://nfs.kasi.re.kr/gps"
    return IGSMirrorProvider(
        "kasi",
        _yy_subdir_resolver(base),
        check_existing=check_existing,
    )


def esa_provider(check_existing: bool = True) -> IGSMirrorProvider:
    base = "ftp://gssc.esa.int/gnss"
    return IGSMirrorProvider(
        "esa",
        _flat_daily_resolver(base),
        check_existing=check_existing,
    )


def bkgftp_provider(check_existing: bool = True) -> IGSMirrorProvider:
    base = "ftp://igs-ftp.bkg.bund.de/IGS"
    return IGSMirrorProvider(
        "bkgftp",
        IGSMirrorPathResolver(
            base_url=base,
            observation_directory=lambda day: _bkg_obs_dir(base, day),
            navigation_directories=lambda day, _nav_type: [_bkg_brdc_dir(base, day)],
            product_directory=lambda gps_week: f"{base}/products/{gps_week}/",
        ),
        check_existing=check_existing,
    )


def bdsmart_provider(check_existing: bool = True) -> IGSMirrorProvider:
    base = "https://data.bdsmart.cn/pub/data/igs"
    return IGSMirrorProvider(
        "bdsmart",
        IGSMirrorPathResolver(
            base_url=base,
            observation_directory=lambda day: _bdsmart_daily_dir(base, day),
            navigation_directories=lambda day, _nav_type: [_bdsmart_daily_dir(base, day)],
            fallback_to_candidates=False,
        ),
        check_existing=check_existing,
    )


def ign_provider(check_existing: bool = True) -> IGSMirrorProvider:
    base = "ftp://igs.ign.fr/pub/igs"
    return IGSMirrorProvider(
        "ign",
        IGSMirrorPathResolver(
            base_url=base,
            observation_directory=lambda day: _ign_daily_dir(base, day),
            navigation_directories=lambda day, _nav_type: [_ign_daily_dir(base, day)],
            product_directory=lambda gps_week: f"{base}/products/{gps_week}/",
        ),
        check_existing=check_existing,
    )


def sopac_provider(check_existing: bool = True) -> IGSMirrorProvider:
    base = "https://garner.ucsd.edu/pub"
    return IGSMirrorProvider(
        "sopac",
        IGSMirrorPathResolver(
            base_url=base,
            observation_directory=lambda day: _sopac_rinex_dir(base, day),
            navigation_directories=lambda day, _nav_type: [_sopac_nav_dir(base, day)],
            product_directory=lambda gps_week: f"{base}/products/{gps_week}/",
            fallback_to_candidates=False,
        ),
        check_existing=check_existing,
    )


def noaa_provider(check_existing: bool = True) -> IGSMirrorProvider:
    base = "https://www.ngs.noaa.gov/corsdata"
    return IGSMirrorProvider(
        "noaa",
        IGSMirrorPathResolver(
            base_url=base,
            observation_directory=lambda day: _noaa_daily_dir(base, day),
            navigation_directories=lambda _day, _nav_type: [],
            fallback_to_candidates=False,
        ),
        check_existing=check_existing,
    )


def _yy_subdir_resolver(base: str) -> IGSMirrorPathResolver:
    return IGSMirrorPathResolver(
        base_url=base,
        observation_directory=lambda day: _yy_dir(base, day, "d"),
        navigation_directories=lambda day, nav_type: _yy_nav_dirs(base, day, nav_type),
        product_directory=lambda gps_week: f"{base}/products/{gps_week}/",
    )


def _flat_daily_resolver(base: str) -> IGSMirrorPathResolver:
    return IGSMirrorPathResolver(
        base_url=base,
        observation_directory=lambda day: _flat_daily_dir(base, day),
        navigation_directories=lambda day, _nav_type: [_flat_daily_dir(base, day)],
        product_directory=lambda gps_week: f"{base}/products/{gps_week}/",
        flat_daily=True,
    )


def _yy_dir(base: str, day: date, suffix: str) -> str:
    doy = datetime_to_doy(day)
    return f"{base}/data/daily/{day.year}/{doy:03d}/{day.year % 100:02d}{suffix}/"


def _yy_nav_dirs(base: str, day: date, nav_type: NavigationType) -> list[str]:
    if nav_type == NavigationType.MIXED:
        return [_yy_dir(base, day, "p"), _yy_dir(base, day, "n")]
    suffix = {
        NavigationType.GPS: "n",
        NavigationType.GLONASS: "g",
        NavigationType.GALILEO: "l",
        NavigationType.BEIDOU: "c",
    }.get(nav_type, "p")
    return [_yy_dir(base, day, suffix)]


def _flat_daily_dir(base: str, day: date) -> str:
    doy = datetime_to_doy(day)
    return f"{base}/data/daily/{day.year}/{doy:03d}/"


def _bkg_obs_dir(base: str, day: date) -> str:
    doy = datetime_to_doy(day)
    return f"{base}/obs/{day.year}/{doy:03d}/"


def _bkg_brdc_dir(base: str, day: date) -> str:
    doy = datetime_to_doy(day)
    return f"{base}/BRDC/{day.year}/{doy:03d}/"


def _bdsmart_daily_dir(base: str, day: date) -> str:
    doy = datetime_to_doy(day)
    return f"{base}/{day.year}/{doy:03d}/"


def _ign_daily_dir(base: str, day: date) -> str:
    doy = datetime_to_doy(day)
    return f"{base}/data/{day.year}/{doy:03d}/"


def _sopac_rinex_dir(base: str, day: date) -> str:
    doy = datetime_to_doy(day)
    return f"{base}/rinex/{day.year}/{doy:03d}/"


def _sopac_nav_dir(base: str, day: date) -> str:
    doy = datetime_to_doy(day)
    return f"{base}/nav/{day.year}/{doy:03d}/"


def _noaa_daily_dir(base: str, day: date) -> str:
    doy = datetime_to_doy(day)
    return f"{base}/rinex/{day.year}/{doy:03d}/"
