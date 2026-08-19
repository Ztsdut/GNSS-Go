from __future__ import annotations

from datetime import date as dt_date
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ProductType(StrEnum):
    ORBIT = "orbit"
    CLOCK = "clock"
    ERP = "erp"
    BIAS = "bias"
    IONEX = "ionex"
    SINEX = "sinex"
    ANTEX = "antex"


class ProductTier(StrEnum):
    FINAL = "final"
    RAPID = "rapid"
    ULTRA = "ultra"
    PREDICTED = "predicted"
    REALTIME = "realtime"
    AUTO = "auto"


class ProductSystem(StrEnum):
    GPS = "gps"
    GLONASS = "glonass"
    GALILEO = "galileo"
    BEIDOU = "beidou"
    QZSS = "qzss"
    MULTI = "multi"
    AUTO = "auto"


class ProductAvailability(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    NOT_PUBLISHED = "not_published"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    AUTH_FAILED = "auth_failed"
    UNSUPPORTED = "unsupported"
    INVALID_REMOTE_CONTENT = "invalid_remote_content"


class BiasProductKind(StrEnum):
    SINEX_BIAS = "sinex_bias"
    OSB = "osb"
    DSB = "dsb"
    DCB = "dcb"


class AnalysisCenter(BaseModel):
    code: str
    aliases: list[str] = Field(default_factory=list)
    supports_multi_gnss: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)


class ProductDescriptor(BaseModel):
    product_type: ProductType
    center: str
    tier: ProductTier
    system: ProductSystem = ProductSystem.AUTO
    start_epoch: datetime | None = None
    date: dt_date | None = None
    duration: str | None = None
    sampling: str | None = None
    format: str | None = None
    campaign: str | None = None
    reference_frame: str | None = None
    content: str | None = None
    bias_kind: BiasProductKind | None = None
    filename: str | None = None
    compression: str | None = None


class ProductLogicalKey(BaseModel, frozen=True):
    product_type: ProductType
    center: str
    tier: ProductTier
    system: ProductSystem = ProductSystem.AUTO
    date: dt_date | None = None
    duration: str | None = None
    sampling: str | None = None
    campaign: str | None = None


class ProductCandidate(BaseModel):
    descriptor: ProductDescriptor
    provider: str | None = None
    url: str | None = None
    filename: str | None = None
    availability: ProductAvailability = ProductAvailability.NOT_FOUND
    reason: str | None = None


class ProductResolution(BaseModel):
    logical_products: list[ProductDescriptor] = Field(default_factory=list)
    candidates: list[ProductCandidate] = Field(default_factory=list)
    selected: list[ProductCandidate] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    valid: bool
    product_type: ProductType
    metadata: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class ProductRule(BaseModel):
    product_type: ProductType
    center: str = "auto"
    tier: str | ProductTier = ProductTier.AUTO
    available: bool = True
    reason: str | None = None
