from pathlib import Path

import httpx
import pytest

from gnssgo.download.events import CancellationToken, DownloadEventType
from gnssgo.download.manager import DownloadManager, make_task
from gnssgo.exceptions import RemoteFileNotFound
from gnssgo.models import RemoteFile


@pytest.mark.anyio
async def test_download_events_success(tmp_path: Path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "4"}, content=b"data")

    events = []
    remote = RemoteFile(
        provider="test",
        url="https://example.test/file.bin",
        filename="file.bin",
        data_type="product",
    )
    manager = DownloadManager(event_callback=events.append, task_id="task-1")
    manager.downloader.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        results = await manager.run([make_task(remote, tmp_path / "file.bin")])
    finally:
        await manager.close()

    assert results[0].status == "validated"
    event_types = [event.type for event in events]
    assert DownloadEventType.FILE_QUEUED in event_types
    assert DownloadEventType.FILE_STARTED in event_types
    assert DownloadEventType.FILE_VALIDATED in event_types
    assert DownloadEventType.TASK_COMPLETED in event_types


@pytest.mark.anyio
async def test_cancellation_token_cancels_before_scheduling(tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()
    events = []
    remote = RemoteFile(
        provider="test",
        url="https://example.test/file.bin",
        filename="file.bin",
        data_type="product",
    )
    manager = DownloadManager(
        event_callback=events.append,
        cancellation_token=token,
        task_id="task-cancel",
    )
    try:
        results = await manager.run([make_task(remote, tmp_path / "file.bin")])
    finally:
        await manager.close()

    assert results[0].status == "cancelled"
    assert events[-1].type == DownloadEventType.TASK_CANCELLED


@pytest.mark.anyio
async def test_download_manager_tries_fallback_candidate_after_primary_failure(
    tmp_path: Path,
) -> None:
    primary = RemoteFile(
        provider="first",
        url="https://example.test/missing.rnx.gz",
        filename="missing.rnx.gz",
        data_type="nav",
    )
    fallback = RemoteFile(
        provider="second",
        url="https://example.test/found.rnx.gz",
        filename="found.rnx.gz",
        data_type="nav",
    )
    primary.fallback_candidates = [fallback]
    task = make_task(primary, tmp_path / primary.filename, overwrite=True, keep_compressed=True)
    manager = DownloadManager(retries=0)

    async def fake_download(download_task, **_kwargs):
        if download_task.remote.provider == "first":
            raise RemoteFileNotFound("missing")
        path = download_task.temporary_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")
        return path

    manager.downloader.download = fake_download

    result = await manager.download_one(task)

    assert result.status == "validated"
    assert result.task.remote.provider == "second"
    assert result.task.destination.name == "found.rnx.gz"
