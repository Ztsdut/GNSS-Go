from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from gnssgo.models.files import LocalFile, ProviderAttempt, RemoteFile


class DownloadTask(BaseModel):
    remote: RemoteFile
    destination: Path
    temporary_path: Path
    overwrite: bool = False
    # False means the downloaded archive itself is the final output.
    decompress: bool = True
    # Only applies when decompress=True.
    keep_compressed: bool = False


class DownloadResult(BaseModel):
    task: DownloadTask
    status: str
    local_file: LocalFile | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None


class DownloadPlan(BaseModel):
    requests: list[object] = Field(default_factory=list)
    remote_files: list[RemoteFile] = Field(default_factory=list)
    existing_files: list[LocalFile] = Field(default_factory=list)
    download_tasks: list[DownloadTask] = Field(default_factory=list)
    archive_root: Path | None = None
    matched_stations: list[str] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)
    provider_stats: dict[str, int] = Field(default_factory=dict)
    estimated_size: int | None = None
    missing: list[str] = Field(default_factory=list)
    provider_requested: str = "auto"
    attempted_providers: list[ProviderAttempt] = Field(default_factory=list)
    task_id: str | None = None

    @property
    def to_download_count(self) -> int:
        return len(self.download_tasks)

    @property
    def provider_used(self) -> str | None:
        for remote in self.remote_files:
            return remote.provider
        for local in self.existing_files:
            if local.remote:
                return local.remote.provider
        return None


class BatchDownloadResult(BaseModel):
    total: int = 0
    downloaded: int = 0
    skipped: int = 0
    unavailable: int = 0
    failed: int = 0
    cancelled: int = 0
    bytes_downloaded: int = 0
    provider_stats: dict[str, int] = Field(default_factory=dict)

    @property
    def state(self) -> str:
        if self.cancelled:
            return "cancelled"
        if self.total == 0:
            return "completed"
        if self.failed == self.total:
            return "failed"
        if self.failed or self.unavailable:
            return "partial"
        return "completed"
