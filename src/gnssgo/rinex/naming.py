from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from gnssgo.rinex.detect import detect_compression, strip_compression

RINEX2_RE = re.compile(
    r"^(?P<station>[A-Za-z0-9]{4})(?P<doy>\d{3})(?P<hour>[0-9a-xA-X])\.(?P<year>\d{2})(?P<type>[odnglpc])$",
    re.IGNORECASE,
)
RINEX3_RE = re.compile(
    r"^(?P<station>[A-Za-z0-9]{9})_(?P<src>[RSU])_(?P<year>\d{4})(?P<doy>\d{3})(?P<hm>\d{4})_"
    r"(?P<duration>\d{2}[DHMU])_(?P<interval>\d{2}[SMHDU])_(?P<kind>[A-Z]{2})\.(?P<ext>rnx|crx)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RinexFileInfo:
    station: str | None
    year: int | None
    doy: int | None
    hour: str | None
    duration: str | None
    interval: str | None
    file_type: str | None
    rinex_version_family: str | None
    compression: str | None
    compact: bool = False


def parse_rinex_filename(filename: str | Path) -> RinexFileInfo:
    original = Path(filename).name
    compression = detect_compression(original)
    name = Path(strip_compression(original)).name

    if match := RINEX3_RE.match(name):
        kind = match["kind"].upper()
        if kind.endswith("O"):
            file_type = "observation"
        elif kind.endswith("N"):
            file_type = "navigation"
        else:
            file_type = kind.lower()
        return RinexFileInfo(
            station=match["station"].upper(),
            year=int(match["year"]),
            doy=int(match["doy"]),
            hour=match["hm"][:2],
            duration=match["duration"].upper(),
            interval=match["interval"].upper(),
            file_type=file_type,
            rinex_version_family="3/4",
            compression=compression,
            compact=match["ext"].lower() == "crx",
        )

    if match := RINEX2_RE.match(name):
        ext = match["type"].lower()
        year2 = int(match["year"])
        year = 2000 + year2 if year2 < 80 else 1900 + year2
        compact = ext == "d"
        if ext in {"o", "d"}:
            file_type = "observation"
        elif ext in {"n", "g", "l", "p", "c"}:
            file_type = "navigation"
        else:
            file_type = ext
        return RinexFileInfo(
            station=match["station"].upper(),
            year=year,
            doy=int(match["doy"]),
            hour=match["hour"].lower(),
            duration=None,
            interval=None,
            file_type=file_type,
            rinex_version_family="2",
            compression=compression,
            compact=compact,
        )

    return RinexFileInfo(
        station=None,
        year=None,
        doy=None,
        hour=None,
        duration=None,
        interval=None,
        file_type=None,
        rinex_version_family=None,
        compression=compression,
    )
