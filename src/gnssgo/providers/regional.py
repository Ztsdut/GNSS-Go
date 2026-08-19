from __future__ import annotations

from gnssgo.exceptions import ProviderError
from gnssgo.models import (
    NavigationRequest,
    ObservationRequest,
    ProductRequest,
    RemoteFile,
    Station,
)
from gnssgo.providers.base import GNSSProvider, ProviderCapabilities


class RegionalPlaceholderProvider(GNSSProvider):
    def __init__(
        self,
        name: str,
        data_network: str,
        status: str,
        portal_url: str | None = None,
    ) -> None:
        self.name = name
        self.data_network = data_network
        self.status = status
        self.portal_url = portal_url

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, navigation=False, station_metadata=False)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        if self.status in {"BROWSER_REQUIRED", "AUTH_REQUIRED", "INTERACTIVE_WEB"}:
            raise ProviderError(
                f"{self.data_network} data are available through the official portal, "
                f"but this source is currently {self.status.lower()}."
            )
        return []

    async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
        return []

    async def search_products(self, request: ProductRequest) -> list[RemoteFile]:
        return []

    async def fetch_station_catalog(self) -> list[Station]:
        return []

    async def health_check(self) -> dict[str, str]:
        return {
            "provider": self.name,
            "status": self.status,
            "data_network": self.data_network,
            "portal": self.portal_url or "",
        }


def regional_placeholder_providers() -> list[RegionalPlaceholderProvider]:
    return [
        RegionalPlaceholderProvider(
            "geonet_jp",
            "japan",
            "BROWSER_REQUIRED",
            "https://www.gsi.go.jp/denshi/denshi_38136.html",
        ),
        RegionalPlaceholderProvider(
            "ring_it",
            "italy",
            "IMPLEMENTED_BUT_NOT_LIVE_VERIFIED",
            "https://webring.gm.ingv.it:44324/",
        ),
        RegionalPlaceholderProvider("osnet_uk", "united_kingdom", "AUTH_REQUIRED"),
        # Legacy ERGNSS provider id retained for old saved tasks. New Europe UI
        # uses redgae_es; this placeholder is intentionally non-routable by Europe.
        RegionalPlaceholderProvider("ergnss_es", "spain", "IMPLEMENTED_BUT_NOT_LIVE_VERIFIED"),
        RegionalPlaceholderProvider(
            "monpos_mn",
            "mongolia",
            "AUTH_REQUIRED",
            "https://monpos.gazar.gov.mn/download/",
        ),
        RegionalPlaceholderProvider(
            "ramsac_ar", "argentina", "IMPLEMENTED_BUT_NOT_LIVE_VERIFIED"
        ),
        RegionalPlaceholderProvider(
            "trignet_za",
            "south_africa",
            "IMPLEMENTED_BUT_NOT_LIVE_VERIFIED",
            "https://www.trignet.co.za/",
        ),
        RegionalPlaceholderProvider(
            "kasi_kr",
            "korea",
            "IMPLEMENTED_BUT_NOT_LIVE_VERIFIED",
            "https://gnss.kasi.re.kr/gnss_download.php",
        ),
        RegionalPlaceholderProvider(
            "ngii_kr",
            "korea",
            "FULLY_AUTOMATED + LIVE_VERIFIED",
            "https://www.gnssdata.or.kr/download/getDownloadView.do",
        ),
        RegionalPlaceholderProvider("sirent_sg", "singapore", "AUTH_REQUIRED"),
        RegionalPlaceholderProvider(
            "earthscope_us",
            "north_america",
            "IMPLEMENTED_BUT_NOT_LIVE_VERIFIED",
        ),
    ]
