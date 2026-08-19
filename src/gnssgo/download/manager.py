from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from gnssgo.download.events import (
    CancellationToken,
    DownloadEvent,
    DownloadEventType,
    EventCallback,
)
from gnssgo.download.http import HttpDownloader
from gnssgo.download.resume import part_path
from gnssgo.download.retry import backoff_seconds
from gnssgo.download.validator import validate_file
from gnssgo.exceptions import GNSSGoError, RemoteFileNotFound
from gnssgo.models import DownloadResult, DownloadTask, LocalFile
from gnssgo.utils.checksum import file_checksum

PostProcessCallable = Callable[[DownloadTask, Path], LocalFile]


class DownloadManager:
    def __init__(
        self,
        workers: int = 4,
        per_provider_workers: int = 3,
        retries: int = 5,
        connect_timeout: float = 20,
        read_timeout: float = 120,
        resume: bool = True,
        proxy: str | None = None,
        network_settings=None,
        postprocessor: PostProcessCallable | None = None,
        event_callback: EventCallback | None = None,
        cancellation_token: CancellationToken | None = None,
        task_id: str | None = None,
    ) -> None:
        self.workers = max(1, min(workers, 32))
        self.per_provider_workers = max(1, min(per_provider_workers, self.workers))
        self.retries = retries
        self.resume = resume
        self.downloader = HttpDownloader(
            connect_timeout,
            read_timeout,
            proxy,
            network_settings=network_settings,
        )
        self.postprocessor = postprocessor
        self.event_callback = event_callback
        self.cancellation_token = cancellation_token or CancellationToken()
        self.task_id = task_id

    async def close(self) -> None:
        await self.downloader.close()

    async def run(self, tasks: list[DownloadTask]) -> list[DownloadResult]:
        semaphore = asyncio.Semaphore(self.workers)
        provider_semaphores: dict[str, asyncio.Semaphore] = {}
        total_files = len(tasks)

        async def guarded(task: DownloadTask) -> DownloadResult:
            provider = task.remote.provider.lower()
            # Some national archives impose aggressive request-rate limits.
            # Providers can lower only their own concurrency without reducing
            # throughput for every other data centre.
            provider_limit = self.per_provider_workers
            raw_limit = task.remote.metadata.get("max_parallel_downloads")
            if raw_limit not in {None, ""}:
                try:
                    provider_limit = min(
                        provider_limit,
                        max(1, int(str(raw_limit))),
                    )
                except (TypeError, ValueError):
                    pass
            provider_semaphore = provider_semaphores.setdefault(
                provider,
                asyncio.Semaphore(provider_limit),
            )
            async with semaphore, provider_semaphore:
                if self.cancellation_token.cancelled:
                    return DownloadResult(
                        task=task,
                        status="cancelled",
                        error="Download task was cancelled.",
                    )
                result = await self.download_one(task)
                raw_delay = task.remote.metadata.get("min_interval_seconds")
                try:
                    delay = max(0.0, float(str(raw_delay))) if raw_delay else 0.0
                except (TypeError, ValueError):
                    delay = 0.0
                if delay:
                    # Hold the provider semaphore during the quiet period so
                    # the next file from the same archive cannot start early.
                    await asyncio.sleep(delay)
                return result

        for task in tasks:
            self._emit(
                DownloadEvent(
                    type=DownloadEventType.FILE_QUEUED,
                    task_id=self.task_id,
                    remote_file=task.remote,
                    total_files=total_files,
                )
            )
        results = await asyncio.gather(*(guarded(task) for task in tasks))
        failed = sum(1 for item in results if item.status == "failed")
        cancelled = sum(1 for item in results if item.status == "cancelled")
        completed = sum(1 for item in results if item.status not in {"failed", "cancelled"})
        event_type = DownloadEventType.TASK_COMPLETED
        if cancelled:
            event_type = DownloadEventType.TASK_CANCELLED
        elif failed and completed:
            event_type = DownloadEventType.TASK_PARTIAL
        elif failed:
            event_type = DownloadEventType.TASK_FAILED
        self._emit(
            DownloadEvent(
                type=event_type,
                task_id=self.task_id,
                completed_files=completed,
                total_files=total_files,
            )
        )
        return results

    async def download_one(self, task: DownloadTask) -> DownloadResult:
        started = datetime.utcnow()
        candidates = [
            task,
            *[
                _task_for_fallback(task, remote)
                for remote in task.remote.fallback_candidates
            ],
        ]
        last_result: DownloadResult | None = None
        for candidate_task in candidates:
            result = await self._download_one_candidate(candidate_task, started)
            if result.status != "failed":
                return result
            last_result = result
        if last_result is not None:
            return last_result
        raise AssertionError("unreachable")

    async def _download_one_candidate(
        self,
        task: DownloadTask,
        started: datetime,
    ) -> DownloadResult:
        self.cancellation_token.raise_if_cancelled()
        if task.destination.exists() and not task.overwrite:
            self._emit(
                DownloadEvent(
                    type=DownloadEventType.FILE_SKIPPED,
                    task_id=self.task_id,
                    remote_file=task.remote,
                )
            )
            return DownloadResult(
                task=task,
                status="skipped",
                local_file=LocalFile(
                    path=task.destination,
                    size=task.destination.stat().st_size,
                    checksum=file_checksum(task.destination),
                    status="skipped",
                    remote=task.remote,
                ),
                started_at=started,
                finished_at=datetime.utcnow(),
            )

        max_attempts = 1 if task.remote.metadata.get("no_transport_retries") == "1" else self.retries + 1
        for attempt in range(1, max_attempts + 1):
            try:
                self._emit(
                    DownloadEvent(
                        type=DownloadEventType.FILE_STARTED,
                        task_id=self.task_id,
                        remote_file=task.remote,
                    )
                )
                downloaded = await self.downloader.download(
                    task,
                    resume=self.resume,
                    cancellation_token=self.cancellation_token,
                    event_callback=self._emit,
                )
                self._emit(
                    DownloadEvent(
                        type=DownloadEventType.FILE_DOWNLOADED,
                        task_id=self.task_id,
                        remote_file=task.remote,
                    )
                )
                checksum = validate_file(downloaded, task.remote)
                task.destination.parent.mkdir(parents=True, exist_ok=True)
                downloaded.replace(task.destination)
                if self.postprocessor:
                    self._emit(
                        DownloadEvent(
                            type=DownloadEventType.FILE_POSTPROCESS_STARTED,
                            task_id=self.task_id,
                            remote_file=task.remote,
                        )
                    )
                    self.cancellation_token.raise_if_cancelled()
                    local = self.postprocessor(task, task.destination)
                    self._emit(
                        DownloadEvent(
                            type=DownloadEventType.FILE_POSTPROCESS_COMPLETED,
                            task_id=self.task_id,
                            remote_file=task.remote,
                        )
                    )
                    if checksum and not local.checksum:
                        local.checksum = checksum
                else:
                    local = LocalFile(
                        path=task.destination,
                        size=task.destination.stat().st_size,
                        checksum=checksum or file_checksum(task.destination),
                        status="validated",
                        remote=task.remote,
                    )
                self._emit(
                    DownloadEvent(
                        type=DownloadEventType.FILE_VALIDATED,
                        task_id=self.task_id,
                        remote_file=task.remote,
                    )
                )
                return DownloadResult(
                    task=task,
                    status=local.status,
                    local_file=local,
                    started_at=started,
                    finished_at=datetime.utcnow(),
                )
            except GNSSGoError as exc:
                if "cancelled" in str(exc).lower():
                    return DownloadResult(
                        task=task,
                        status="cancelled",
                        error=str(exc),
                        started_at=started,
                        finished_at=datetime.utcnow(),
                    )
                if isinstance(exc, RemoteFileNotFound) or attempt >= max_attempts:
                    self._emit(
                        DownloadEvent(
                            type=DownloadEventType.FILE_FAILED,
                            task_id=self.task_id,
                            remote_file=task.remote,
                            message=str(exc),
                        )
                    )
                    return DownloadResult(
                        task=task,
                        status="failed",
                        error=str(exc),
                        started_at=started,
                        finished_at=datetime.utcnow(),
                    )
                await asyncio.sleep(backoff_seconds(attempt))
            except Exception as exc:  # never let a transport library tear down the whole batch
                message = (
                    f"Unexpected transport error for {task.remote.filename}: "
                    f"{exc.__class__.__name__}: {exc}"
                )
                self._emit(
                    DownloadEvent(
                        type=DownloadEventType.FILE_FAILED,
                        task_id=self.task_id,
                        remote_file=task.remote,
                        message=message,
                    )
                )
                return DownloadResult(
                    task=task,
                    status="failed",
                    error=message,
                    started_at=started,
                    finished_at=datetime.utcnow(),
                )
        raise AssertionError("unreachable")

    def _emit(self, event: DownloadEvent) -> None:
        if self.event_callback:
            self.event_callback(event)


def make_task(
    remote,
    destination: Path,
    overwrite: bool = False,
    keep_compressed: bool = False,
    decompress: bool = True,
) -> DownloadTask:
    return DownloadTask(
        remote=remote,
        destination=destination,
        temporary_path=part_path(destination),
        overwrite=overwrite,
        decompress=decompress,
        keep_compressed=keep_compressed,
    )


def _task_for_fallback(task: DownloadTask, remote) -> DownloadTask:
    destination = task.destination.with_name(remote.filename)
    remote.metadata["fallback_reason"] = f"primary provider {task.remote.provider} failed"
    return DownloadTask(
        remote=remote,
        destination=destination,
        temporary_path=part_path(destination),
        overwrite=task.overwrite,
        decompress=task.decompress,
        keep_compressed=task.keep_compressed,
    )
