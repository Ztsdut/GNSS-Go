from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from gnssgo.models import Station

EARTH_RADIUS_KM = 6371.0088


def normalize_longitude(longitude: float) -> float:
    value = ((longitude + 180) % 360) - 180
    return 180.0 if value == -180 and longitude > 0 else value


def validate_latitude(latitude: float) -> float:
    if latitude < -90 or latitude > 90:
        raise ValueError("Latitude must be between -90 and 90 degrees.")
    return latitude


def longitude_in_range(longitude: float, west: float, east: float) -> bool:
    lon = normalize_longitude(longitude)
    west_n = normalize_longitude(west)
    east_n = normalize_longitude(east)
    if west_n <= east_n:
        return west_n <= lon <= east_n
    return lon >= west_n or lon <= east_n


def bbox_filter(
    stations: list[Station],
    west: float,
    south: float,
    east: float,
    north: float,
) -> list[Station]:
    validate_latitude(south)
    validate_latitude(north)
    if south > north:
        raise ValueError("South latitude must be less than or equal to north latitude.")
    return [
        station
        for station in stations
        if station.latitude is not None
        and station.longitude is not None
        and south <= station.latitude <= north
        and longitude_in_range(station.longitude, west, east)
    ]


def haversine_distance_km(
    latitude1: float,
    longitude1: float,
    latitude2: float,
    longitude2: float,
) -> float:
    validate_latitude(latitude1)
    validate_latitude(latitude2)
    phi1 = radians(latitude1)
    phi2 = radians(latitude2)
    dphi = radians(latitude2 - latitude1)
    dlambda = radians(normalize_longitude(longitude2 - longitude1))
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def radius_filter(
    stations: list[Station],
    latitude: float,
    longitude: float,
    radius_km: float,
) -> list[Station]:
    validate_latitude(latitude)
    if radius_km < 0:
        raise ValueError("Radius must be non-negative.")
    return [
        station
        for station in stations
        if station.latitude is not None
        and station.longitude is not None
        and haversine_distance_km(latitude, longitude, station.latitude, station.longitude)
        <= radius_km
    ]
