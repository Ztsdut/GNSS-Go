from __future__ import annotations

import re
from pathlib import Path

from gnssgo.exceptions import DownloadError
from gnssgo.models import RemoteFile
from gnssgo.utils.dates import datetime_to_doy

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


class ArchiveLayout:
    def __init__(self, root: Path | str = "./data", layout: str = "year_doy") -> None:
        self.root = Path(root)
        self.layout = layout

    def destination_for(self, remote: RemoteFile) -> Path:
        filename = self.safe_filename(remote.filename)
        if remote.date is None:
            return self.root / remote.data_type / filename
        doy = datetime_to_doy(remote.date)
        if self.layout == "year_doy":
            return (
                self.root
                / f"{remote.date.year:04d}"
                / f"{doy:03d}"
                / remote.data_type
                / filename
            )
        raise DownloadError(f"Unsupported archive layout: {self.layout}")

    def safe_filename(self, filename: str) -> str:
        candidate = Path(filename).name
        if candidate != filename or not SAFE_NAME_RE.match(candidate):
            raise DownloadError(f"Unsafe remote filename: {filename!r}")
        return candidate
