from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import Event
from typing import Any

from gnssgo.models import RemoteFile


class DownloadEventType(StrEnum):
    PLAN_STARTED = "plan_started"
    PLAN_READY = "plan_ready"
    FILE_QUEUED = "file_queued"
    FILE_STARTED = "file_started"
    FILE_PROGRESS = "file_progress"
    FILE_DOWNLOADED = "file_downloaded"
    FILE_VALIDATED = "file_validated"
    FILE_POSTPROCESS_STARTED = "file_postprocess_started"
    FILE_POSTPROCESS_COMPLETED = "file_postprocess_completed"
    FILE_SKIPPED = "file_skipped"
    FILE_UNAVAILABLE = "file_unavailable"
    FILE_FAILED = "file_failed"
    TASK_PAUSING = "task_pausing"
    TASK_PAUSED = "task_paused"
    TASK_RESUMED = "task_resumed"
    TASK_CANCELLING = "task_cancelling"
    TASK_CANCELLED = "task_cancelled"
    TASK_COMPLETED = "task_completed"
    TASK_PARTIAL = "task_partial"
    TASK_FAILED = "task_failed"


@dataclass(frozen=True)
class DownloadEvent:
    type: DownloadEventType
    task_id: str | None = None
    logical_key: object | None = None
    remote_file: RemoteFile | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    completed_files: int | None = None
    total_files: int | None = None
    message: str | None = None
    metadata: dict[str, Any] | None = None


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            from gnssgo.exceptions import DownloadError

            raise DownloadError("Download task was cancelled.")


EventCallback = Callable[[DownloadEvent], None]
