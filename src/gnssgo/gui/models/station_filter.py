from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StationFilterState:
    query: str | None = None
    country: str | None = None
    network: list[str] | None = None
    provider: str | None = None
    data_networks: list[str] | None = None
    regional_sources: list[str] | None = None
    bbox: tuple[float, float, float, float] | None = None
    radius: tuple[float, float, float] | None = None
