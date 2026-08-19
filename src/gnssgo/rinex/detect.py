from __future__ import annotations

from pathlib import Path


def detect_compression(filename: str | Path) -> str | None:
    name = str(filename)
    lowered = name.lower()
    if lowered.endswith(".gz"):
        return ".gz"
    if lowered.endswith(".z"):
        return ".Z"
    if lowered.endswith(".zip"):
        return ".zip"
    return None


def strip_compression(filename: str | Path) -> str:
    text = str(filename)
    compression = detect_compression(text)
    if compression:
        return text[: -len(compression)]
    return text


def is_compact_rinex(filename: str | Path) -> bool:
    name = strip_compression(filename).lower()
    return name.endswith(".crx") or name.endswith("d") or ".d." in name
