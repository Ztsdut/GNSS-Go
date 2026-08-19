from __future__ import annotations

import gzip
import json
from pathlib import Path

from gnssgo.models import Station

SNAPSHOT_RESOURCE = (
    Path(__file__).resolve().parents[1] / "resources" / "bundled_station_snapshot.json.gz"
)


def load_bundled_station_snapshot(path: Path | None = None) -> list[Station]:
    """Load the packaged station-position snapshot.

    The snapshot is deliberately read before background network refreshes.  It is
    only a fast/offline bootstrap layer: live provider metadata remains
    authoritative and is merged into the local SQLite catalog later.
    """
    source = path or SNAPSHOT_RESOURCE
    if not source.is_file():
        return []
    try:
        with gzip.open(source, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []

    rows = payload.get("stations", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    stations: list[Station] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            station = Station.model_validate(row)
        except Exception:
            continue
        if station.latitude is None or station.longitude is None:
            continue
        stations.append(station)
    return stations


def snapshot_metadata(path: Path | None = None) -> dict[str, object]:
    source = path or SNAPSHOT_RESOURCE
    if not source.is_file():
        return {}
    try:
        with gzip.open(source, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if key != "stations"}
