from pathlib import Path

import httpx
import pytest

from gnssgo.download.http import HttpDownloader, _safe_url
from gnssgo.download.manager import make_task
from gnssgo.download.validator import parse_content_range, validate_download_content
from gnssgo.exceptions import InvalidRemoteContent
from gnssgo.models import RemoteFile


def test_parse_content_range() -> None:
    parsed = parse_content_range("bytes 100-999/1000")
    assert parsed is not None
    assert parsed.start == 100
    assert parsed.end == 999
    assert parsed.total == 1000
    assert parse_content_range("bytes 500-999/*").total is None
    assert parse_content_range("bytes 999-100/1000") is None
    assert parse_content_range("items 0-1/2") is None
    assert parse_content_range(None) is None


def test_safe_url_removes_signed_query_parameters() -> None:
    safe = _safe_url(
        "https://example.test/file.crx.gz?X-Amz-Security-Token=secret&X-Amz-Signature=abc"
    )
    assert safe == "https://example.test/file.crx.gz"
    assert "X-Amz" not in safe


def test_validate_206_content_range(tmp_path: Path) -> None:
    path = tmp_path / "file.bin.part"
    path.write_bytes(b"0123456789")
    remote = RemoteFile(
        provider="test",
        url="https://example.test/file.bin",
        filename="file.bin",
        data_type="product",
        size=10,
    )
    validate_download_content(
        path,
        remote,
        headers={"content-length": "5", "content-range": "bytes 5-9/10"},
        status_code=206,
        resume_offset=5,
    )


def test_validate_206_wrong_start(tmp_path: Path) -> None:
    path = tmp_path / "file.bin.part"
    path.write_bytes(b"0123456789")
    remote = RemoteFile(
        provider="test",
        url="https://example.test/file.bin",
        filename="file.bin",
        data_type="product",
    )
    with pytest.raises(InvalidRemoteContent, match="start mismatch"):
        validate_download_content(
            path,
            remote,
            headers={"content-length": "5", "content-range": "bytes 4-9/10"},
            status_code=206,
            resume_offset=5,
        )


def test_validate_206_missing_content_range(tmp_path: Path) -> None:
    path = tmp_path / "file.bin.part"
    path.write_bytes(b"0123456789")
    remote = RemoteFile(
        provider="test",
        url="https://example.test/file.bin",
        filename="file.bin",
        data_type="product",
    )
    with pytest.raises(InvalidRemoteContent, match="Content-Range"):
        validate_download_content(
            path,
            remote,
            headers={"content-length": "5"},
            status_code=206,
            resume_offset=5,
        )


@pytest.mark.anyio
async def test_http_200_after_range_restarts_partial(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("range") == "bytes=5-"
        return httpx.Response(200, headers={"content-length": "10"}, content=b"0123456789")

    remote = RemoteFile(
        provider="test",
        url="https://example.test/file.bin",
        filename="file.bin",
        data_type="product",
    )
    destination = tmp_path / "file.bin"
    task = make_task(remote, destination)
    task.temporary_path.write_bytes(b"xxxxx")
    downloader = HttpDownloader()
    downloader.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await downloader.download(task, resume=True)
    finally:
        await downloader.close()
    assert result.read_bytes() == b"0123456789"


@pytest.mark.anyio
async def test_http_206_resume_validates_total(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("range") == "bytes=5-"
        return httpx.Response(
            206,
            headers={"content-length": "5", "content-range": "bytes 5-9/10"},
            content=b"56789",
        )

    remote = RemoteFile(
        provider="test",
        url="https://example.test/file.bin",
        filename="file.bin",
        data_type="product",
        size=10,
    )
    destination = tmp_path / "file.bin"
    task = make_task(remote, destination)
    task.temporary_path.write_bytes(b"01234")
    downloader = HttpDownloader()
    downloader.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await downloader.download(task, resume=True)
    finally:
        await downloader.close()
    assert result.read_bytes() == b"0123456789"
