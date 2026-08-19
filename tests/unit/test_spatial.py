import pytest

from gnssgo.models import Station
from gnssgo.stations.spatial import bbox_filter, haversine_distance_km, radius_filter


def test_bbox_crosses_dateline() -> None:
    stations = [
        Station(id="EAST", latitude=10, longitude=175),
        Station(id="WEST", latitude=10, longitude=-175),
        Station(id="OUT", latitude=10, longitude=0),
    ]
    result = bbox_filter(stations, 170, 0, -170, 20)
    assert {station.id for station in result} == {"EAST", "WEST"}


def test_radius_filter_and_distance() -> None:
    stations = [
        Station(id="TOKYO", latitude=35.68, longitude=139.76),
        Station(id="OSAKA", latitude=34.69, longitude=135.50),
    ]
    assert haversine_distance_km(35.68, 139.76, 35.68, 139.76) == pytest.approx(0)
    result = radius_filter(stations, 35.68, 139.76, 100)
    assert [station.id for station in result] == ["TOKYO"]


def test_invalid_latitude_is_rejected() -> None:
    with pytest.raises(ValueError):
        bbox_filter([], 0, -91, 10, 10)
