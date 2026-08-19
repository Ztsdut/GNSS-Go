#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
import sys
sys.path.insert(0, str(SRC))

from gnssgo import GNSSGo  # noqa: E402
from gnssgo.models import Station  # noqa: E402
from gnssgo.stations.catalog import seed_stations  # noqa: E402
from gnssgo.regional_sources import default_regional_source_registry  # noqa: E402
from gnssgo.stations.snapshot import load_bundled_station_snapshot  # noqa: E402

OUT = SRC / "gnssgo" / "resources" / "bundled_station_snapshot.json.gz"

# Reliable/high-value online metadata providers.  The release workflow refreshes
# these before PyInstaller runs.  Failures are best-effort and never block a
# local build because the previous packaged snapshot remains usable.
def _online_provider_targets() -> list[tuple[str, str]]:
    """Return station-metadata providers worth refreshing before release packaging.

    The IGS catalog is always included.  Regional sources are discovered from the
    registry so new integrated networks automatically join the release snapshot.
    All fetches are best-effort and concurrent; an unreachable provider cannot
    shrink or block a build because the previous snapshot is merged first.
    """
    targets: list[tuple[str, str]] = [("igs", "bkg")]
    seen = {"bkg"}
    for source in default_regional_source_registry().all():
        if source.provider in seen:
            continue
        seen.add(source.provider)
        targets.append((source.data_network, source.provider))
    return targets



def _key(station: Station) -> str:
    return station.id.upper()


def _merge(target: dict[str, Station], station: Station) -> None:
    if station.latitude is None or station.longitude is None:
        return
    key = _key(station)
    existing = target.get(key)
    if existing is None:
        target[key] = station
        return

    data = existing.model_dump(mode="python")
    incoming = station.model_dump(mode="python")
    # Preserve existing scalar values unless absent; memberships/capabilities are
    # always unioned so one station can simultaneously belong to IGS and a
    # regional network.
    for field in (
        "marker_name", "domes", "latitude", "longitude", "height", "country",
        "receiver", "antenna", "start_time", "end_time", "data_availability",
    ):
        if data.get(field) in (None, "") and incoming.get(field) not in (None, ""):
            data[field] = incoming[field]
    for field in (
        "network", "data_networks", "regional_sources", "providers",
        "sampling_rates", "rinex_versions", "constellations", "aliases",
    ):
        data[field] = sorted({*(data.get(field) or []), *(incoming.get(field) or [])})
    metadata = dict(data.get("metadata") or {})
    for k, v in (incoming.get("metadata") or {}).items():
        if k not in metadata and v not in (None, "", [], {}):
            metadata[k] = v
    data["metadata"] = metadata
    target[key] = Station.model_validate(data)


def _local_stations(client: GNSSGo) -> list[Station]:
    stations: list[Station] = list(seed_stations())

    # Sources with official coordinate snapshots already shipped in the repo.
    local_jobs = [
        ("noaa_ncn", "_local_station_catalog"),
        ("sirgas_rbmc_br", "_local_cartogram_station_catalog"),
        ("sirgas_cl", "_stations_from_csn_kml"),
        ("gdms_tw", "bundled_station_catalog"),
        ("cmonoc_cn", "bundled_station_catalog"),
        ("satref_hk", "bundled_station_catalog"),
        ("epn", "bundled_station_catalog"),
    ]
    for provider_name, method_name in local_jobs:
        try:
            provider = client.registry.get(provider_name)
            method = getattr(provider, method_name)
            if provider_name == "sirgas_rbmc_br":
                rows = method()[0]
            elif provider_name == "sirgas_cl":
                rows = method(provider.station_kml_path())
            else:
                rows = method()
            stations.extend(list(rows or []))
        except Exception as exc:
            print(f"[snapshot] local {provider_name}: {exc}")

    # Korea's national station list is local but exposed through an async method.
    try:
        provider = client.registry.get("ngii_kr")
        stations.extend(asyncio.run(provider.fetch_station_catalog()))
    except Exception as exc:
        print(f"[snapshot] local ngii_kr: {exc}")
    return stations


async def _fetch_one(client: GNSSGo, network_id: str, provider_name: str, timeout: float):
    provider = client.registry.get(provider_name)
    try:
        rows = await asyncio.wait_for(provider.fetch_station_catalog(), timeout=timeout)
        return network_id, provider_name, list(rows or []), None
    except Exception as exc:
        return network_id, provider_name, [], exc


async def _online_stations(client: GNSSGo, timeout: float) -> list[Station]:
    tasks = [
        _fetch_one(client, network_id, provider_name, timeout)
        for network_id, provider_name in _online_provider_targets()
    ]
    results = await asyncio.gather(*tasks)
    stations: list[Station] = []
    for network_id, provider_name, rows, error in results:
        if error is not None:
            print(f"[snapshot] online {provider_name}: {error}")
            continue
        mapped = [row for row in rows if row.latitude is not None and row.longitude is not None]
        print(f"[snapshot] online {provider_name}: {len(mapped)} mapped stations")
        stations.extend(mapped)
    return stations


def main() -> None:
    parser = argparse.ArgumentParser(description="Build GNSS Go's bundled station-position snapshot")
    parser.add_argument("--online", action="store_true", help="also refresh reliable official online catalogs")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-provider online timeout in seconds")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()

    client = GNSSGo()
    merged: dict[str, Station] = {}

    # Start from an existing release snapshot so transient network failures never
    # shrink the next package.
    for station in load_bundled_station_snapshot(args.output):
        _merge(merged, station)
    for station in _local_stations(client):
        _merge(merged, station)
    if args.online:
        try:
            for station in asyncio.run(_online_stations(client, args.timeout)):
                _merge(merged, station)
        except Exception as exc:
            print(f"[snapshot] online refresh failed: {exc}")

    stations = [merged[key].model_dump(mode="json") for key in sorted(merged)]
    by_network: dict[str, int] = {}
    for row in merged.values():
        for network in row.data_networks:
            by_network[network] = by_network.get(network, 0) + 1

    payload = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "station_count": len(stations),
        "mapped_station_count": len(stations),
        "by_network": dict(sorted(by_network.items())),
        "stations": stations,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.output, "wt", encoding="utf-8", compresslevel=9) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    print(f"[snapshot] wrote {len(stations)} mapped stations -> {args.output}")
    print(f"[snapshot] by network: {payload['by_network']}")


if __name__ == "__main__":
    main()
