from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Station(BaseModel):
    id: str
    marker_name: str | None = None
    domes: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    height: float | None = None
    country: str | None = None
    network: list[str] = Field(default_factory=list)
    data_networks: list[str] = Field(default_factory=list)
    regional_sources: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    receiver: str | None = None
    antenna: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    sampling_rates: list[str] = Field(default_factory=list)
    rinex_versions: list[str] = Field(default_factory=list)
    constellations: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    metadata: dict[str, str | list[str]] = Field(default_factory=dict)
    data_availability: str | None = None

    def __init__(self, **data) -> None:
        if "id" not in data and "code" in data:
            data["id"] = data.pop("code")
        if isinstance(data.get("network"), str):
            data["network"] = [data["network"]]
        super().__init__(**data)

    @property
    def code(self) -> str:
        return self.id

    @property
    def legacy_id(self) -> str:
        return self.id[:4].upper()


class SpatialFilter(BaseModel):
    bbox: tuple[float, float, float, float] | None = Field(
        default=None,
        description="west, south, east, north",
    )
    radius: tuple[float, float, float] | None = Field(
        default=None,
        description="longitude, latitude, radius_km",
    )


class BoundingBox(BaseModel):
    west: float
    south: float
    east: float
    north: float


class RadiusFilter(BaseModel):
    latitude: float
    longitude: float
    radius_km: float
