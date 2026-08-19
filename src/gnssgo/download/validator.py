from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from gnssgo.exceptions import InvalidRemoteContent, ValidationError
from gnssgo.models import RemoteFile
from gnssgo.utils.checksum import file_checksum

HTML_PREFIXES = (b"<html", b"<!doctype html")
DATA_EXTENSIONS = (
    ".gz",
    ".z",
    ".zip",
    ".rnx",
    ".crx",
    ".sp3",
    ".clk",
    ".erp",
    ".bsx",
    ".bia",
    ".snx",
    ".atx",
)

CONTENT_RANGE_RE = re.compile(r"^bytes (?P<start>\d+)-(?P<end>\d+)/(?P<total>\d+|\*)$")


@dataclass(frozen=True)
class ContentRange:
    start: int
    end: int
    total: int | None


def parse_content_range(value: str | None) -> ContentRange | None:
    if not value:
        return None
    match = CONTENT_RANGE_RE.match(value.strip())
    if not match:
        return None
    start = int(match.group("start"))
    end = int(match.group("end"))
    total_raw = match.group("total")
    if end < start:
        return None
    total = None if total_raw == "*" else int(total_raw)
    if total is not None and total <= end:
        return None
    return ContentRange(start=start, end=end, total=total)


def validate_file(path: Path, remote: RemoteFile) -> str | None:
    if not path.exists():
        raise ValidationError(f"Downloaded file is missing: {path}")
    size = path.stat().st_size
    if remote.size is not None and size != remote.size:
        raise ValidationError(f"Size mismatch for {path.name}: expected {remote.size}, got {size}.")
    if remote.checksum:
        digest = file_checksum(path, "sha256")
        if digest.lower() != remote.checksum.lower():
            raise ValidationError(f"Checksum mismatch for {path.name}.")
        return digest
    return None


def validate_download_content(
    path: Path,
    remote: RemoteFile,
    headers: Mapping[str, str] | None = None,
    *,
    status_code: int | None = None,
    resume_offset: int = 0,
) -> None:
    if not path.exists():
        raise InvalidRemoteContent(f"Downloaded content is empty: {remote.url}")
    if path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        raise InvalidRemoteContent(f"Downloaded content is empty: {remote.url}")

    _validate_response_size(
        path,
        remote,
        headers=headers,
        status_code=status_code,
        resume_offset=resume_offset,
    )

    with path.open("rb") as handle:
        head = handle.read(512).lstrip().lower()

    if head.startswith(HTML_PREFIXES):
        raise InvalidRemoteContent(
            f"Remote returned HTML instead of data for {remote.filename}."
        )

    content_type = (headers or {}).get("content-type", "").lower()
    if "text/html" in content_type:
        raise InvalidRemoteContent(
            f"Remote returned HTML content-type for {remote.filename}."
        )

    if path.name.lower().endswith(DATA_EXTENSIONS) and b"<html" in head[:128]:
        raise InvalidRemoteContent(f"Invalid data payload for {remote.filename}.")


def partial_file_is_invalid(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as handle:
        head = handle.read(512).lstrip().lower()
    return head.startswith(HTML_PREFIXES) or b"<html" in head[:128]


def _validate_response_size(
    path: Path,
    remote: RemoteFile,
    headers: Mapping[str, str] | None,
    status_code: int | None,
    resume_offset: int,
) -> None:
    local_size = path.stat().st_size
    content_length = _header_int(headers, "content-length")
    if status_code == 206:
        content_range = parse_content_range((headers or {}).get("content-range"))
        if content_range is None:
            raise InvalidRemoteContent(
                f"Missing or invalid Content-Range for resumed download: {remote.filename}."
            )
        if content_range.start != resume_offset:
            raise InvalidRemoteContent(
                f"Content-Range start mismatch for {remote.filename}: "
                f"expected {resume_offset}, got {content_range.start}."
            )
        expected_delta = content_range.end - content_range.start + 1
        if content_length is not None and content_length != expected_delta:
            raise InvalidRemoteContent(
                f"Content-Length mismatch for resumed {remote.filename}: "
                f"expected remaining {expected_delta}, got {content_length}."
            )
        if content_range.total is not None:
            if local_size != content_range.total:
                raise InvalidRemoteContent(
                    f"Final size mismatch for resumed {remote.filename}: "
                    f"expected {content_range.total}, got {local_size}."
                )
            if remote.size is not None and remote.size != content_range.total:
                raise InvalidRemoteContent(
                    f"Remote size mismatch for resumed {remote.filename}: "
                    f"expected {remote.size}, got {content_range.total}."
                )
        return

    if content_length is not None and content_length != local_size:
        raise InvalidRemoteContent(
            f"Content-Length mismatch for {remote.filename}: "
            f"expected {content_length}, got {local_size}."
        )


def _header_int(headers: Mapping[str, str] | None, name: str) -> int | None:
    if not headers:
        return None
    value = headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
