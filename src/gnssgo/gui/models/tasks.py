from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class GuiTaskState(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    READY = "ready"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    PAUSED = "paused"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GuiTaskType(StrEnum):
    OBS = "obs"
    NAV = "nav"
    PRODUCT = "product"
    STATION_UPDATE = "station_update"


class DownloadEventType(StrEnum):
    PLAN_STARTED = "plan_started"
    PLAN_READY = "plan_ready"
    DOWNLOAD_STARTED = "download_started"
    DOWNLOAD_COMPLETED = "download_completed"
    FILE_FAILED = "file_failed"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"


class DownloadEvent(BaseModel):
    type: DownloadEventType
    task_id: str
    file: str | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    message: str | None = None


class GuiTask(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    type: GuiTaskType
    created_at: datetime = Field(default_factory=datetime.utcnow)
    state: GuiTaskState = GuiTaskState.PENDING
    total_files: int = 0
    completed_files: int = 0
    failed_files: int = 0
    total_bytes: int | None = None
    downloaded_bytes: int | None = None
    current_file: str | None = None
    file_progress: dict[str, tuple[int, int | None]] = Field(default_factory=dict)
    completed_keys: set[str] = Field(default_factory=set)
    failed_keys: set[str] = Field(default_factory=set)
    output_paths: list[str] = Field(default_factory=list)
    request: dict = Field(default_factory=dict)
    plan: object | None = None
    cancellation_token: object | None = None
    manifest_path: str | None = None
    message: str | None = None
