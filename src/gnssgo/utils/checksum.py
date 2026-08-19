from __future__ import annotations

from hashlib import md5, sha256
from pathlib import Path


def file_checksum(path: Path, algorithm: str = "sha256") -> str:
    hasher = sha256() if algorithm.lower() == "sha256" else md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
