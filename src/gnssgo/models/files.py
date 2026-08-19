from __future__ import annotations

from datetime import date as dt_date
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, HttpUrl


class RemoteFile(BaseModel):
    provider: str
    url: HttpUrl | str
    filename: str
    size: int | None = None
    checksum: str | None = None
    compression: str | None = None
    data_type: str
    station: str | None = None
    date: dt_date | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    fallback_candidates: list[RemoteFile] = Field(default_factory=list)


class ProviderAttempt(BaseModel):
    provider: str
    status: str
    message: str | None = None


class LocalFile(BaseModel):
    path: Path
    size: int | None = None
    checksum: str | None = None
    status: str = "planned"
    remote: RemoteFile | None = None
    processed_path: Path | None = None
    processed_at: datetime | None = None
    rinex_version: str | None = None
    rinex_type: str | None = None
