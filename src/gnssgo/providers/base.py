from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from gnssgo.models import (
    NavigationRequest,
    ObservationRequest,
    ProductRequest,
    RemoteFile,
    Station,
)


class ProviderCapabilities(BaseModel):
    observations: bool = False
    navigation: bool = False
    products: list[str] = Field(default_factory=list)
    station_metadata: bool = False
    authentication_required: bool = False


class GNSSProvider(ABC):
    name: str

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        raise NotImplementedError

    @abstractmethod
    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        raise NotImplementedError

    @abstractmethod
    async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
        raise NotImplementedError

    @abstractmethod
    async def search_products(self, request: ProductRequest) -> list[RemoteFile]:
        raise NotImplementedError

    async def fetch_station_catalog(self) -> list[Station]:
        return []

    async def search_stations(self, query: str) -> list[Station]:
        return []

    async def health_check(self) -> dict[str, str]:
        return {"provider": self.name, "status": "unknown"}
