from __future__ import annotations

from collections.abc import Iterable

from gnssgo.models import Station
from gnssgo.stations import StationCatalog
from gnssgo.stations.coordinates import normalize_longitude, valid_map_coordinate, valid_sirgas_coordinate


def _normalized_networks(values: Iterable[str] | None) -> set[str]:
    return {
        str(value).strip().lower()
        for value in (values or [])
        if str(value).strip()
    }


def station_marker_class(
    station: Station,
    active_data_networks: Iterable[str] | None = None,
) -> str:
    """Return one of the two user-facing map classes.

    IGS membership always has visual priority.  Therefore an IGS station is
    blue in every scope (IGS-only, continent-only, or IGS + regional).  A
    non-IGS regional CORS station is orange.  The filter is responsible for
    excluding unrelated stations, so no overlap/grey class is needed in normal
    map views.
    """
    memberships = _normalized_networks(station.data_networks)
    is_igs = "igs" in memberships or "igs" in _normalized_networks(station.network)
    is_regional = bool(station.regional_sources) or any(
        value and value != "igs" for value in memberships
    )
    if is_igs:
        return "igs_only"
    if is_regional:
        return "regional_only"
    return "other"


def station_to_json(
    station: Station,
    active_data_networks: Iterable[str] | None = None,
) -> dict:
    latitude = station.latitude
    longitude = station.longitude

    if latitude is not None and longitude is not None:
        # Normalize legacy 0..360 longitudes before they reach Leaflet.
        if valid_map_coordinate(latitude, longitude):
            longitude = normalize_longitude(longitude)
        else:
            latitude = longitude = None

    memberships = _normalized_networks(station.data_networks)
    active = None if active_data_networks is None else _normalized_networks(active_data_networks)
    sirgas_visible = "sirgas" in memberships or any(
        str(source).strip().lower().startswith("sirgas_")
        for source in station.regional_sources
    )
    if active is not None:
        sirgas_visible = sirgas_visible and "sirgas" in active

    # Defense in depth for old station caches: even before the background
    # provider refresh completes, a SIRGAS view must never draw points outside
    # Latin America.  Keep the station selectable/listed by returning null map
    # coordinates rather than deleting the station record.
    if sirgas_visible and latitude is not None and longitude is not None:
        if not valid_sirgas_coordinate(latitude, longitude, country_strict=False):
            latitude = longitude = None

    return {
        "id": station.id,
        "lat": latitude,
        "lon": longitude,
        "country": station.country,
        "data_networks": station.data_networks,
        "regional_sources": station.regional_sources,
        "networks": station.network,
        "providers": station.providers,
        "marker_class": station_marker_class(station, active_data_networks),
    }


class MapService:
    def __init__(self, catalog: StationCatalog | None = None) -> None:
        self.catalog = catalog or StationCatalog()

    def stations_json(self) -> list[dict]:
        return [station_to_json(station) for station in self.catalog.search()]

    def bbox(self, west: float, south: float, east: float, north: float) -> list[dict]:
        return [
            station_to_json(station)
            for station in self.catalog.search_bbox(west, south, east, north)
        ]

    def radius(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        return [
            station_to_json(station)
            for station in self.catalog.search_radius(lat, lon, radius_km)
        ]
