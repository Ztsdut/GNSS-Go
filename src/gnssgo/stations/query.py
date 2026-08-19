from __future__ import annotations

from gnssgo.models import Station
from gnssgo.stations.catalog import StationCatalog


class StationQuery:
    def __init__(self, catalog: StationCatalog) -> None:
        self.catalog = catalog

    def search(self, text: str) -> list[Station]:
        return self.catalog.search(text)

    def info(self, code: str) -> Station | None:
        return self.catalog.get(code)
