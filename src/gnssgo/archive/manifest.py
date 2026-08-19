from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from gnssgo.data_networks import default_data_network_registry
from gnssgo.models import DownloadResult, RemoteFile


class ManifestRecord(BaseModel):
    provider: str
    url: str
    filename: str
    local_path: str
    download_time: datetime = Field(default_factory=datetime.utcnow)
    size: int | None = None
    status: str
    checksum: str | None = None
    station: str | None = None
    date: str | None = None
    data_type: str
    rinex_version: str | None = None
    rinex_type: str | None = None
    postprocess_status: str | None = None
    provider_requested: str | None = None
    provider_used: str | None = None
    attempted_providers: list[dict[str, str | None]] = Field(default_factory=list)
    product_type: str | None = None
    product_tier_requested: str | None = None
    product_tier_used: str | None = None
    analysis_center_requested: str | None = None
    analysis_center_used: str | None = None
    system_requested: str | None = None
    system_used: str | None = None
    campaign: str | None = None
    reference_frame: str | None = None
    sampling: str | None = None
    duration: str | None = None
    resolution_trace: str | None = None
    fallback_reason: str | None = None
    data_network_requested: str | None = None
    regional_sources_requested: list[str] = Field(default_factory=list)
    station_regional_sources: list[str] = Field(default_factory=list)
    regional_provider: str | None = None
    regional_fallback: bool = False
    error: str | None = None


class Manifest:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def append(self, record: ManifestRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line]

    def append_result(self, result: DownloadResult, plan=None) -> None:
        remote = result.task.remote
        local_file = result.local_file
        attempts = []
        if plan is not None:
            attempts = [attempt.model_dump() for attempt in plan.attempted_providers]
        request = plan.requests[0] if plan is not None and plan.requests else None
        requested_networks = getattr(request, "data_networks", None) or []
        requested_sources = getattr(request, "regional_sources", None) or []
        requested_regional_providers = _requested_regional_providers(requested_networks)
        self.append(
            ManifestRecord(
                provider=remote.provider,
                url=_safe_url(str(remote.url)),
                filename=remote.filename,
                local_path=str(local_file.path if local_file else result.task.destination),
                size=local_file.size if local_file else None,
                status=result.status,
                checksum=local_file.checksum if local_file else None,
                station=remote.station,
                date=remote.date.isoformat() if remote.date else None,
                data_type=remote.data_type,
                rinex_version=local_file.rinex_version if local_file else None,
                rinex_type=local_file.rinex_type if local_file else None,
                postprocess_status=local_file.status if local_file else None,
                provider_requested=plan.provider_requested if plan is not None else None,
                provider_used=remote.provider,
                attempted_providers=attempts,
                product_type=remote.metadata.get("product_type"),
                product_tier_requested=(
                    getattr(getattr(request, "tier", None), "value", None)
                    if request is not None
                    else None
                ),
                product_tier_used=remote.metadata.get("product_tier"),
                analysis_center_requested=getattr(request, "center", None),
                analysis_center_used=remote.metadata.get("analysis_center"),
                system_requested=(
                    getattr(getattr(request, "system", None), "value", None)
                    if request is not None
                    else None
                ),
                system_used=remote.metadata.get("product_system"),
                campaign=remote.metadata.get("campaign"),
                reference_frame=remote.metadata.get("reference_frame"),
                sampling=remote.metadata.get("sampling"),
                duration=remote.metadata.get("duration"),
                resolution_trace=remote.metadata.get("resolution_trace"),
                fallback_reason=remote.metadata.get("resolution_warnings"),
                data_network_requested=(
                    ",".join(requested_networks)
                    if request is not None
                    else None
                ),
                regional_sources_requested=list(requested_sources),
                station_regional_sources=_csv_metadata(remote, "station_regional_sources"),
                regional_provider=remote.metadata.get("regional_provider"),
                regional_fallback=bool(
                    requested_regional_providers
                    and remote.provider not in requested_regional_providers
                ),
                error=result.error,
            )
        )


def record_for_existing(remote: RemoteFile, path: Path) -> ManifestRecord:
    return ManifestRecord(
        provider=remote.provider,
        url=_safe_url(str(remote.url)),
        filename=remote.filename,
        local_path=str(path),
        size=path.stat().st_size if path.exists() else None,
        status="skipped",
        station=remote.station,
        date=remote.date.isoformat() if remote.date else None,
        data_type=remote.data_type,
    )


def _requested_regional_providers(network_ids: list[str]) -> set[str]:
    registry = default_data_network_registry()
    providers: set[str] = set()
    for network_id in network_ids:
        try:
            network = registry.get(network_id)
        except Exception:
            continue
        if network.category == "regional":
            providers.update(network.providers)
    return providers


def _csv_metadata(remote: RemoteFile, key: str) -> list[str]:
    value = remote.metadata.get(key, "")
    return [item for item in value.split(",") if item]


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url.split("?", 1)[0]
    return parsed._replace(query="", fragment="").geturl()
