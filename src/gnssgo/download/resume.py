from __future__ import annotations

from pathlib import Path


def part_path(destination: Path) -> Path:
    return destination.with_name(destination.name + ".part")
