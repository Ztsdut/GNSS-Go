from __future__ import annotations

import asyncio
import csv
import io
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx

from gnssgo.models import NavigationRequest, ObservationRequest, ProductRequest, RemoteFile, Station
from gnssgo.network.proxy import ProxyConfig
from gnssgo.providers.base import GNSSProvider, ProviderCapabilities

_GDMS_RESOURCE = Path(__file__).resolve().parent.parent / "resources" / "GDMSstations.csv"
_GDMS_USER_CACHE = Path.home() / ".gnssgo" / "GDMSstations.csv"
_GDMS_MAP_URL = "https://gdms.cwa.gov.tw/map.php"
_GDMS_DOWNLOAD_URL = "https://gdms.cwa.gov.tw/GeophyDownload.php"

_CMONOC_RESOURCE = Path(__file__).resolve().parent.parent / "resources" / "CMONOC.csv"
_CMONOC_URL = "https://data.earthquake.cn/datashare/report.shtml?PAGEID=siteInfo_jizhun"

_GDMS_NETWORK_RE = re.compile(r"^GNSS(?:_|$)", re.I)


def _canonical(code: str, country: str) -> str:
    code4 = str(code or "").strip().upper()[:4]
    return f"{code4}00{country}"


def _parse_gdms_csv_text(text: str) -> list[Station]:
    rows: list[Station] = []
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    seen: set[str] = set()
    for raw in reader:
        if len(raw) < 4:
            continue
        network = str(raw[0] or "").strip()
        if not _GDMS_NETWORK_RE.match(network):
            continue
        code = str(raw[1] or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{4}", code) or code in seen:
            continue
        try:
            lat = float(raw[2])
            lon = float(raw[3])
        except (TypeError, ValueError):
            continue
        try:
            height = float(raw[4]) if len(raw) > 4 and str(raw[4]).strip() else None
        except (TypeError, ValueError):
            height = None
        if not (20.0 <= lat <= 27.5 and 117.0 <= lon <= 123.5):
            continue
        seen.add(code)
        rows.append(
            Station(
                id=_canonical(code, "TWN"),
                marker_name=code,
                latitude=lat,
                longitude=lon,
                height=height,
                country="TWN",
                network=[network],
                data_networks=["taiwan"],
                regional_sources=["taiwan_gdms"],
                providers=["gdms_tw"],
                aliases=[code],
                metadata={
                    "gdms_network": network,
                    "catalog_source": _GDMS_MAP_URL,
                    "download_portal": _GDMS_DOWNLOAD_URL,
                },
                data_availability=(
                    "Taiwan, China GDMS GNSS data require an official GDMS account/login and are downloaded "
                    "through the interactive GeophyDownload web page. GNSS external networks "
                    "(GNSS_IES/GNSS_ETEC) are subject to the portal's availability rules."
                ),
            )
        )
    return rows


def _read_gdms_file(path: Path) -> list[Station]:
    try:
        return _parse_gdms_csv_text(path.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        return []


def _parse_cmonoc_csv(path: Path = _CMONOC_RESOURCE) -> list[Station]:
    stations: list[Station] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                code = str(row.get("code") or "").strip().upper()
                if not re.fullmatch(r"[A-Z0-9]{4}", code):
                    continue
                try:
                    lon = float(row.get("longitude") or "")
                    lat = float(row.get("latitude") or "")
                except (TypeError, ValueError):
                    continue
                if not (0.0 <= lat <= 55.0 and 70.0 <= lon <= 140.0):
                    continue
                stations.append(
                    Station(
                        id=_canonical(code, "CHN"),
                        marker_name=code,
                        latitude=lat,
                        longitude=lon,
                        country="CHN",
                        network=["CMONOC"],
                        data_networks=["china"],
                        regional_sources=["china_cmonoc"],
                        providers=["cmonoc_cn"],
                        aliases=[code],
                        metadata={
                            "catalog_source": _CMONOC_URL,
                            "coordinate_source": str(path),
                        },
                        data_availability=(
                            "CMONOC station metadata are indexed from the National Earthquake Science "
                            "Data Center page. GNSS Go currently provides the station catalog and the "
                            "official source link; no stable public machine RINEX endpoint is assumed."
                        ),
                    )
                )
    except OSError:
        return []
    return stations


def _looks_like_gdms_csv(text: str) -> bool:
    stations = _parse_gdms_csv_text(text)
    return len(stations) >= 20


def _extract_candidate_urls(base_url: str, text: str) -> list[str]:
    candidates: list[str] = []
    patterns = (
        r"[\"']([^\"']+\.csv(?:\?[^\"']*)?)[\"']",
        r"[\"']([^\"']*(?:station|site)[^\"']*\.php(?:\?[^\"']*)?)[\"']",
    )
    for pattern in patterns:
        for value in re.findall(pattern, text, flags=re.I):
            url = urljoin(base_url, value.replace("&amp;", "&"))
            if url.startswith("https://gdms.cwa.gov.tw/") and url not in candidates:
                candidates.append(url)
    return candidates


def _live_gdms_csv(network_settings=None, timeout: float = 12.0) -> str | None:
    """Best-effort discovery of the official GDMS station export.

    The map page generates/downloads ``GDMSstations.csv`` client-side on some
    deployments, so there is not a stable documented CSV URL to hard-code.  We
    inspect the official page and its first-party scripts for a current CSV/API
    endpoint; if GDMS changes its implementation or the network is offline the
    bundled last-known CSV remains authoritative for startup.
    """
    proxy = ProxyConfig.from_settings(network_settings).proxy_url(protocol="https")
    kwargs = {"timeout": timeout, "follow_redirects": True, "trust_env": True}
    if proxy:
        kwargs["proxy"] = proxy
    headers = {"User-Agent": "Mozilla/5.0 GNSS Go/0.1 (+station-catalog refresh)"}
    try:
        with httpx.Client(headers=headers, **kwargs) as client:
            response = client.get(_GDMS_MAP_URL)
            response.raise_for_status()
            texts: list[tuple[str, str]] = [(_GDMS_MAP_URL, response.text)]
            # First inspect directly referenced first-party scripts; this avoids
            # guessing a private endpoint and adapts when the site's JS changes.
            script_urls = []
            for src in re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", response.text, flags=re.I):
                url = urljoin(_GDMS_MAP_URL, src)
                if url.startswith("https://gdms.cwa.gov.tw/") and url not in script_urls:
                    script_urls.append(url)
            for url in script_urls[:10]:
                try:
                    r = client.get(url)
                    if r.status_code == 200:
                        texts.append((url, r.text))
                except Exception:
                    continue

            candidates: list[str] = []
            for base, text in texts:
                if _looks_like_gdms_csv(text):
                    return text
                for url in _extract_candidate_urls(base, text):
                    if url not in candidates:
                        candidates.append(url)
            for url in candidates[:16]:
                try:
                    r = client.get(url)
                    if r.status_code == 200 and _looks_like_gdms_csv(r.text):
                        return r.text
                except Exception:
                    continue
    except Exception:
        return None
    return None


class TaiwanGDMSProvider(GNSSProvider):
    name = "gdms_tw"
    data_network = "taiwan"
    regional_source = "taiwan_gdms"
    source_type = "official_gdms_station_export_with_bundled_fallback"
    portal_url = _GDMS_DOWNLOAD_URL
    station_catalog_source = _GDMS_MAP_URL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            observations=True,
            station_metadata=True,
            authentication_required=True,
        )

    def bundled_station_catalog(self) -> list[Station]:
        for path in (_GDMS_USER_CACHE, _GDMS_RESOURCE):
            stations = _read_gdms_file(path)
            if stations:
                return stations
        return []

    async def fetch_station_catalog(self) -> list[Station]:
        live_text = await asyncio.to_thread(
            _live_gdms_csv, getattr(self, "_network_settings", None)
        )
        source_used = "bundled fallback"
        live = False
        if live_text:
            stations = _parse_gdms_csv_text(live_text)
            if stations:
                try:
                    _GDMS_USER_CACHE.parent.mkdir(parents=True, exist_ok=True)
                    _GDMS_USER_CACHE.write_text(live_text, encoding="utf-8-sig")
                    source_used = _GDMS_MAP_URL
                    live = True
                except OSError:
                    source_used = _GDMS_MAP_URL
                self.last_station_catalog_stats = {
                    "catalog_complete": True,
                    "station_count": len(stations),
                    "mapped_station_count": len(stations),
                    "live_refresh": True,
                    "catalog_source_used": source_used,
                }
                return stations
        stations = self.bundled_station_catalog()
        self.last_station_catalog_stats = {
            "catalog_complete": True,
            "station_count": len(stations),
            "mapped_station_count": len(stations),
            "live_refresh": live,
            "catalog_source_used": source_used,
            "fallback_resource": str(_GDMS_RESOURCE),
        }
        return stations

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        # The official GDMS page currently requires login and enforces its own
        # station/file-type/date rules. Do not invent a private download URL or
        # bypass the account workflow.
        return []

    async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
        return []

    async def search_products(self, request: ProductRequest) -> list[RemoteFile]:
        return []

    async def health_check(self) -> dict[str, str]:
        return {"provider": self.name, "status": "auth_required", "portal": self.portal_url}


class CMONOCChinaProvider(GNSSProvider):
    name = "cmonoc_cn"
    data_network = "china"
    regional_source = "china_cmonoc"
    source_type = "bundled_official_station_catalog"
    portal_url = _CMONOC_URL
    station_catalog_source = _CMONOC_URL

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=False, station_metadata=True)

    def bundled_station_catalog(self) -> list[Station]:
        return _parse_cmonoc_csv()

    async def fetch_station_catalog(self) -> list[Station]:
        stations = self.bundled_station_catalog()
        self.last_station_catalog_stats = {
            "catalog_complete": True,
            "station_count": len(stations),
            "mapped_station_count": len(stations),
            "catalog_source_used": str(_CMONOC_RESOURCE),
        }
        return stations

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        return []

    async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
        return []

    async def search_products(self, request: ProductRequest) -> list[RemoteFile]:
        return []

    async def health_check(self) -> dict[str, str]:
        return {"provider": self.name, "status": "catalog_only", "portal": self.portal_url}
