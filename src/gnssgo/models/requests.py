from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from gnssgo.models.products import ProductSystem, ProductTier, ProductType
from gnssgo.models.stations import SpatialFilter
from gnssgo.utils.dates import iter_dates, parse_date


class DateRange(BaseModel):
    start: date
    end: date

    @field_validator("start", "end", mode="before")
    @classmethod
    def parse_dates(cls, value: object) -> date:
        if isinstance(value, str | date):
            return parse_date(value)
        raise TypeError("Date values must be dates or strings.")

    @model_validator(mode="after")
    def validate_order(self) -> DateRange:
        if self.end < self.start:
            raise ValueError("End date must be on or after start date.")
        return self

    def days(self) -> list[date]:
        return list(iter_dates(self.start, self.end))


class RinexSelection(StrEnum):
    AUTO = "auto"
    V2 = "2"
    V3 = "3"
    V4 = "4"


class ObservationRequest(BaseModel):
    stations: list[str] | None = None
    date_range: DateRange
    provider: str = "auto"
    sampling: str | None = "30s"
    rinex: RinexSelection = RinexSelection.AUTO
    spatial_filter: SpatialFilter | None = None
    network: list[str] | None = None
    data_networks: list[str] | None = None
    regional_sources: list[str] | None = None
    # When true, the provider itself discovers every observation file available
    # for the requested regional source/date.  This is used by directory-backed
    # CORS networks such as IBGE RBMC, where the day directory is a more accurate
    # availability source than the static/map station catalogue.
    discover_available: bool = False

    @model_validator(mode="after")
    def require_selection(self) -> ObservationRequest:
        if (
            not self.stations
            and not self.spatial_filter
            and not self.network
            and not (self.discover_available and (self.data_networks or self.regional_sources))
        ):
            raise ValueError("At least one station, spatial filter, network, or provider discovery scope must be specified.")
        return self


class NavigationType(StrEnum):
    MIXED = "mixed"
    GPS = "gps"
    GLONASS = "glonass"
    GALILEO = "galileo"
    BEIDOU = "beidou"


class NavigationRequest(BaseModel):
    date_range: DateRange
    provider: str = "auto"
    nav_type: NavigationType = NavigationType.MIXED


class ProductRequest(BaseModel):
    date_range: DateRange
    product_types: list[ProductType] = Field(default_factory=list)
    provider: str = "auto"
    center: str = "auto"
    tier: ProductTier = ProductTier.AUTO
    system: ProductSystem = ProductSystem.AUTO
    sampling: str | None = None

    @field_validator("product_types", mode="before")
    @classmethod
    def parse_product_types(cls, value: object) -> list[ProductType]:
        if value is None:
            return []
        if isinstance(value, list):
            return [ProductType(item) for item in value]
        return [ProductType(value)]

    @field_validator("tier", mode="before")
    @classmethod
    def parse_tier(cls, value: object) -> ProductTier:
        if isinstance(value, str) and value == "ultra-rapid":
            value = "ultra"
        return ProductTier(value)

    @field_validator("system", mode="before")
    @classmethod
    def parse_system(cls, value: object) -> ProductSystem:
        return ProductSystem(value)

    @field_validator("center")
    @classmethod
    def normalize_center(cls, value: str) -> str:
        return value.lower() if value.lower() == "auto" else value.upper()
