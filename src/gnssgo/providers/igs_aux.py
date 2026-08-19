from __future__ import annotations

from datetime import datetime, timezone

from gnssgo.models import (
    NavigationRequest,
    ObservationRequest,
    ProductRequest,
    ProductTier,
    ProductType,
    RemoteFile,
)
from gnssgo.providers.base import GNSSProvider, ProviderCapabilities


class IGSAuxiliaryProvider(GNSSProvider):
    """Public IGS auxiliary products that are not date-keyed operational files.

    The IGS Central Bureau publishes the current antenna model and the current
    station SINEX directly under files.igs.org as public static/current files.
    """

    name = "igsfiles"

    ANTEX_URL = "https://files.igs.org/pub/station/general/igs20.atx.gz"
    SINEX_URL = "https://files.igs.org/pub/station/general/igs.snx.gz"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            observations=False,
            navigation=False,
            products=[ProductType.ANTEX.value, ProductType.SINEX.value],
            station_metadata=False,
            authentication_required=False,
        )

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        return []

    async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
        return []

    async def search_products(self, request: ProductRequest) -> list[RemoteFile]:
        if request.center.lower() not in {"auto", "igs"}:
            return []
        if request.tier not in {ProductTier.AUTO, ProductTier.FINAL}:
            return []

        files: list[RemoteFile] = []
        if ProductType.ANTEX in request.product_types:
            files.append(self._remote(self.ANTEX_URL, ProductType.ANTEX))
        if ProductType.SINEX in request.product_types and _includes_current_utc_day(request):
            files.append(self._remote(self.SINEX_URL, ProductType.SINEX))
        return files

    async def health_check(self) -> dict[str, str]:
        return {"provider": self.name, "status": "configured", "access": "public-https"}

    def _remote(self, url: str, product_type: ProductType) -> RemoteFile:
        filename = url.rsplit("/", 1)[-1]
        return RemoteFile(
            provider=self.name,
            url=url,
            filename=filename,
            compression=".gz",
            data_type=product_type.value,
            date=None,
            metadata={
                "analysis_center": "IGS",
                "product_tier": ProductTier.FINAL.value,
                "product_system": "multi",
                "duration": "CURRENT",
                "sampling": "",
                "campaign": "CURRENT",
                "source_kind": "igs-central-bureau",
            },
        )


def _includes_current_utc_day(request: ProductRequest) -> bool:
    today = datetime.now(timezone.utc).date()
    return any(day == today for day in request.date_range.days())
