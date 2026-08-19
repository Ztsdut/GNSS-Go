from __future__ import annotations

import asyncio

import httpx

from gnssgo.gui import network_probe
from gnssgo.providers.regional_live import GeoNetNZProvider, _geonet_gnss_station_coordinates


def test_openstreetmap_probe_true_on_success(monkeypatch) -> None:
    class Response:
        status_code = 200
        content = b"png"

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _url):
            return Response()

    monkeypatch.setattr(network_probe.httpx, "Client", Client)
    assert network_probe.openstreetmap_available(timeout=0.1) is True


def test_openstreetmap_probe_false_and_never_raises(monkeypatch) -> None:
    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("offline")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(network_probe.httpx, "Client", Client)
    assert network_probe.openstreetmap_available(timeout=0.1) is False


def test_geonet_network_api_parser_extracts_gnss_coordinates() -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"Code": "DUND", "Location": "Dunedin", "SensorType": "8"},
                "geometry": {"type": "Point", "coordinates": [170.5, -45.9]},
            },
            {
                "properties": {"code": "WGTN", "location": "Wellington"},
                "geometry": {"coordinates": [174.78, -41.29]},
            },
        ],
    }
    parsed = _geonet_gnss_station_coordinates(payload)
    assert parsed["DUND"] == (-45.9, 170.5, "Dunedin")
    assert parsed["WGTN"] == (-41.29, 174.78, "Wellington")


def test_geonet_station_catalog_merges_api_coordinates_with_station_info() -> None:
    station_line = "DUND  Dunedin Station    something"

    def handler(req: httpx.Request) -> httpx.Response:
        if "station.info.geonet" in str(req.url):
            return httpx.Response(200, text=station_line)
        if "/network/station" in str(req.url):
            return httpx.Response(
                200,
                json={
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "properties": {"Code": "DUND", "Location": "Dunedin"},
                            "geometry": {"coordinates": [170.5, -45.9]},
                        }
                    ],
                },
            )
        raise AssertionError(str(req.url))

    provider = GeoNetNZProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    stations = asyncio.run(provider.fetch_station_catalog())
    station = next(item for item in stations if item.id == "DUND00NZL")
    assert station.latitude == -45.9
    assert station.longitude == 170.5
    assert station.country == "NZL"
    assert provider.last_station_catalog_stats["mapped_station_count"] >= 1


def test_bundled_snapshot_contains_europe_epn_stations() -> None:
    from gnssgo.stations.snapshot import load_bundled_station_snapshot

    stations = load_bundled_station_snapshot()
    europe = [station for station in stations if "europe" in station.data_networks]
    assert len(europe) >= 100
    assert all("europe_epn" in station.regional_sources for station in europe)
