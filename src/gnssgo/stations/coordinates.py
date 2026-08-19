from __future__ import annotations

import math

# Broad guard for the national networks currently exposed under the
# "SIRGAS / Latin America" selector.  It is deliberately a little wider than
# the continental coastline so border/offshore stations are not lost, while
# Antarctica, Africa, Europe and Asia can never be rendered as SIRGAS pins.
SIRGAS_LATIN_AMERICA_BOUNDS = (-120.0, -60.0, -30.0, 35.0)  # west, south, east, north

# Generous national envelopes used when parsing a national SIRGAS catalogue.
# These are validation guards, not geographic clipping polygons.  Chile includes
# Easter Island and Ecuador includes the Galapagos.
SIRGAS_COUNTRY_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "ARG": (-76.5, -57.0, -51.0, -20.0),
    "BRA": (-75.0, -36.0, -29.0, 7.0),
    "CHL": (-112.0, -59.0, -64.0, -16.0),
    "MEX": (-120.0, 10.0, -85.0, 35.0),
    "BOL": (-70.5, -24.5, -56.5, -8.5),
    "COL": (-82.5, -5.5, -65.0, 14.5),
    "ECU": (-93.5, -6.0, -74.0, 3.5),
    "PER": (-82.5, -19.5, -67.0, 1.5),
    "URY": (-59.5, -36.0, -51.5, -29.0),
    "CRI": (-88.5, 5.0, -82.0, 12.0),
    "PAN": (-84.5, 6.0, -76.5, 10.5),
}


def normalize_longitude(value: float | int | str) -> float:
    """Normalize a longitude to [-180, 180)."""
    longitude = float(value)
    return ((longitude + 180.0) % 360.0) - 180.0


def valid_map_coordinate(latitude: object, longitude: object) -> bool:
    try:
        lat = float(latitude)  # type: ignore[arg-type]
        raw_lon = float(longitude)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return False
    # Accept the two common longitude conventions only: [-180, 180] and
    # [0, 360].  Do not modulo-wrap arbitrary corrupt values such as 9999.
    return (
        math.isfinite(lat)
        and math.isfinite(raw_lon)
        and -90.0 <= lat <= 90.0
        and -180.0 <= raw_lon <= 360.0
    )


def _inside(latitude: float, longitude: float, bounds: tuple[float, float, float, float]) -> bool:
    west, south, east, north = bounds
    return south <= latitude <= north and west <= longitude <= east


def valid_sirgas_coordinate(
    latitude: object,
    longitude: object,
    *,
    country: str | None = None,
    country_strict: bool = False,
) -> bool:
    """Validate a coordinate for the SIRGAS/Latin-America map layer.

    ``country_strict=False`` is used for cache/map safety: it rejects only points
    clearly outside Latin America.  ``country_strict=True`` is used while parsing
    a national catalogue so a table's unrelated numeric fields cannot become a
    plausible-looking point in another country.
    """
    if not valid_map_coordinate(latitude, longitude):
        return False
    lat = float(latitude)  # type: ignore[arg-type]
    lon = normalize_longitude(longitude)  # type: ignore[arg-type]
    if not _inside(lat, lon, SIRGAS_LATIN_AMERICA_BOUNDS):
        return False
    if country_strict:
        bounds = SIRGAS_COUNTRY_BOUNDS.get(str(country or "").upper())
        if bounds is not None and not _inside(lat, lon, bounds):
            return False
    return True
