from __future__ import annotations

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
    Station,
)
from gnssgo.products.naming import (
    ProductNamingRegistry,
    parse_product_filename,
    product_matches_request,
)
from gnssgo.products.resolver import logical_key
from gnssgo.providers.base import GNSSProvider, ProviderCapabilities
from gnssgo.providers.listing import parse_listing_filenames
from gnssgo.utils.dates import datetime_to_doy
from gnssgo.utils.gps_time import datetime_to_gpsweek

BKG_ARCHIVE = "https://igs.bkg.bund.de/root_ftp"
IGS_NETWORK_JSON = "https://files.igs.org/pub/station/general/IGSNetwork.json"


class BKGPathResolver:
    base_url = BKG_ARCHIVE

    def observation_candidates(self, station: str, day: date) -> list[str]:
        doy = datetime_to_doy(day)
        year2 = day.year % 100
        return [
            f"{self.base_url}/IGS/obs/{day.year}/{doy:03d}/"
            f"{station.upper()}_R_{day.year}{doy:03d}0000_01D_30S_MO.crx.gz",
            f"{self.base_url}/IGS/obs/{day.year}/{doy:03d}/"
            f"{station.lower()}{doy:03d}0.{year2:02d}d.Z",
        ]

    def observation_directory(self, day: date) -> str:
        doy = datetime_to_doy(day)
        return f"{self.base_url}/IGS/obs/{day.year}/{doy:03d}/"

    def navigation_candidates(self, day: date, nav_type: NavigationType) -> list[str]:
        doy = datetime_to_doy(day)
        suffix = {
            NavigationType.MIXED: "MN",
            NavigationType.GPS: "GN",
            NavigationType.GLONASS: "RN",
            NavigationType.GALILEO: "EN",
            NavigationType.BEIDOU: "CN",
        }[nav_type]
        candidates = [
            f"{self.base_url}/IGS/BRDC/{day.year}/{doy:03d}/"
            f"BRDC00WRD_R_{day.year}{doy:03d}0000_01D_{suffix}.rnx.gz"
        ]
        if nav_type == NavigationType.MIXED:
            candidates.extend(
                [
                    f"{self.base_url}/IGS/BRDC/{day.year}/{doy:03d}/"
                    f"BRDC00IGS_R_{day.year}{doy:03d}0000_01D_MN.rnx.gz",
                ]
            )
        return candidates

    def product_candidates(self, day: date, product_type: ProductType) -> list[str]:
        gps_week, dow = datetime_to_gpsweek(day)
        mapping = {
            ProductType.ORBIT: [
                f"{self.base_url}/IGS/products/{gps_week}/igs{gps_week}{dow}.sp3.Z"
            ],
            ProductType.CLOCK: [
                f"{self.base_url}/IGS/products/{gps_week}/igs{gps_week}{dow}.clk.Z"
            ],
            ProductType.ERP: [f"{self.base_url}/IGS/products/{gps_week}/igs{gps_week}7.erp.Z"],
            ProductType.BIAS: [],
            ProductType.IONEX: [],
            ProductType.SINEX: [],
            ProductType.ANTEX: [f"{self.base_url}/IGS/products/antex/igs20.atx"],
        }
        return mapping[product_type]

    def product_directories(self, day: date, product_type: ProductType) -> list[str]:
        gps_week, _dow = datetime_to_gpsweek(day)
        directories = [f"{self.base_url}/IGS/products/{gps_week}/"]
        doy = datetime_to_doy(day)
        if product_type == ProductType.IONEX:
            directories.append(f"{self.base_url}/IGS/products/ionex/{day.year}/{doy:03d}/")
        if product_type == ProductType.BIAS:
            directories.append(f"{self.base_url}/IGS/products/bias/{day.year}/")
        return directories


class BKGProvider(GNSSProvider):
    name = "bkg"
    station_catalog_source = IGS_NETWORK_JSON

    def __init__(
        self,
        resolver: BKGPathResolver | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.resolver = resolver or BKGPathResolver()
        self.client = client or httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            trust_env=False,
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            observations=True,
            navigation=True,
            products=["orbit", "clock", "erp", "antex"],
            station_metadata=True,
            authentication_required=False,
        )

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            for station in request.stations or []:
                urls = await self._discover_observation_urls(station, day, request)
                for url in urls:
                    files.append(_remote("bkg", url, "obs", station=station, day=day))
        return files

    async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            for url in self.resolver.navigation_candidates(day, request.nav_type):
                files.append(_remote("bkg", url, "nav", day=day))
        return files

    async def search_products(self, request: ProductRequest) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        for day in request.date_range.days():
            for product_type in request.product_types:
                urls = await self._discover_product_urls(day, product_type, request)
                for url in urls:
                    files.append(_remote("bkg", url, product_type.value, day=day))
        return files

    async def health_check(self) -> dict[str, str]:
        return {"provider": self.name, "status": "configured"}

    async def fetch_station_catalog(self) -> list[Station]:
        response = await self.client.get(IGS_NETWORK_JSON)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        return [_station_from_igs_network(code, item) for code, item in payload.items()]

    async def _discover_observation_urls(
        self,
        station: str,
        day: date,
        request: ObservationRequest,
    ) -> list[str]:
        directory = self.resolver.observation_directory(day)
        try:
            response = await self.client.get(directory)
            response.raise_for_status()
        except httpx.HTTPError:
            return self.resolver.observation_candidates(station, day)

        filenames = parse_listing_filenames(response.text)
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
        if not matches:
            return self.resolver.observation_candidates(station, day)
        return [directory + name for name in matches]

    async def _discover_product_urls(
        self,
        day: date,
        product_type: ProductType,
        request: ProductRequest,
    ) -> list[str]:
        found: list[str] = []
        for directory in self.resolver.product_directories(day, product_type):
            try:
                response = await self.client.get(directory)
                response.raise_for_status()
            except httpx.HTTPError:
                continue
            filenames = parse_listing_filenames(response.text)
            matches = [
                name
                for name in filenames
                if _matches_product_request(name, request, day, product_type)
            ]
            found.extend(directory + name for name in matches)
        if found:
            return _preferred_product_urls(found, request)
        fallback_names = ProductNamingRegistry().candidates(day, product_type, request)
        directories = self.resolver.product_directories(day, product_type)
        if fallback_names and directories:
            return [directories[0] + name for name in fallback_names]
        return self.resolver.product_candidates(day, product_type)


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
        return lower.endswith((".d.z", ".o.z", ".d.gz", ".o.gz"))
    if request.rinex in {"3", "4"}:
        return lower.endswith((".crx.gz", ".rnx.gz", ".crx", ".rnx"))
    return lower.endswith((".crx.gz", ".rnx.gz", ".d.z", ".o.z"))


def _logical_id(data_type: str, station: str | None, day: date | None) -> str:
    return f"{data_type}:{station or ''}:{day.isoformat() if day else ''}"


def _matches_product_request(
    filename: str,
    request: ProductRequest,
    day: date,
    product_type: ProductType,
) -> bool:
    descriptor = parse_product_filename(filename)
    return bool(
        descriptor
        and descriptor.product_type == product_type
        and product_matches_request(descriptor, request, day)
    )


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


def _remote_metadata(
    data_type: str,
    station: str | None,
    day: date | None,
    filename: str,
) -> dict[str, str]:
    parsed = parse_product_filename(filename)
    if parsed:
        return {
            "logical_id": logical_key(
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
                    },
                )
            ).model_dump_json(),
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
    return {"logical_id": _logical_id(data_type, station, day)}


def _station_from_igs_network(code: str, payload: object) -> Station:
    data = payload if isinstance(payload, dict) else {}
    receiver = data.get("Receiver") if isinstance(data.get("Receiver"), dict) else {}
    antenna = data.get("Antenna") if isinstance(data.get("Antenna"), dict) else {}
    marker = code[:4].upper()
    return Station(
        id=code.upper(),
        marker_name=marker,
        latitude=_float_or_none(data.get("Latitude")),
        longitude=_float_or_none(data.get("Longitude")),
        height=_float_or_none(data.get("Height")),
        country=_string_or_none(data.get("CountryOrRegion")),
        network=["igs"],
        data_networks=["igs"],
        providers=["bkg"],
        receiver=_string_or_none(receiver.get("Name")),
        antenna=_antenna_name(antenna),
        aliases=[marker],
        constellations=_constellations_from_receiver(receiver),
        sampling_rates=["30s"],
        rinex_versions=["3"],
        metadata={
            key: str(value)
            for key, value in {
                "source": IGS_NETWORK_JSON,
                "last_data": data.get("LastData"),
                "x": data.get("X"),
                "y": data.get("Y"),
                "z": data.get("Z"),
                "receiver_satellite_system": receiver.get("SatelliteSystem"),
            }.items()
            if value not in (None, "")
        },
        data_availability=_string_or_none(data.get("LastData")),
    )


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _string_or_none(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _antenna_name(antenna: dict[str, object]) -> str | None:
    name = _string_or_none(antenna.get("Name"))
    radome = _string_or_none(antenna.get("Radome"))
    if name and radome:
        return f"{name} {radome}"
    return name


def _constellations_from_receiver(receiver: dict[str, object]) -> list[str]:
    value = _string_or_none(receiver.get("SatelliteSystem"))
    if not value:
        return []
    mapping = {
        "GPS": "G",
        "GLO": "R",
        "GAL": "E",
        "BDS": "C",
        "QZSS": "J",
        "IRNSS": "I",
        "SBAS": "S",
    }
    return [mapping[item] for item in value.split("+") if item in mapping]
