from __future__ import annotations

import asyncio
from datetime import date

import pytest

from gnssgo.models import DateRange, ObservationRequest
from gnssgo.providers.korea import KoreaKASIFTPProvider, KoreaNationalCatalogProvider
from gnssgo.providers.regional_expansion import NOAANCNProvider
from gnssgo.regional_sources import default_regional_source_registry


def _request(station: str, *, rinex: str = "auto", sampling: str = "30s", day: date = date(2024, 1, 2)):
    return ObservationRequest(
        stations=[station],
        date_range=DateRange(start=day, end=day),
        sampling=sampling,
        rinex=rinex,
    )


def test_noaa_bundled_arcgis_catalog_has_full_station_metadata() -> None:
    provider = NOAANCNProvider()
    stations = provider._local_station_catalog()
    by_code = {station.marker_name: station for station in stations}

    assert len(stations) == 1846
    assert "1LSU" in by_code
    one = by_code["1LSU"]
    assert one.id == "1LSU00USA"
    assert one.country == "USA"
    assert one.regional_sources == ["usa_noaa_cors"]
    assert one.sampling_rates == ["30S"]
    assert round(one.latitude, 6) == 30.407222
    assert round(one.longitude, 3) == -91.18
    assert one.metadata["status"] == "Operational"
    assert "WUHN" not in by_code  # foreign cooperative rows are not shown as USA markers
    assert "MEXI" not in by_code
    assert "GUAM" in by_code      # U.S. territory remains in the USA source


def test_noaa_daily_url_matches_proven_noaa_layout() -> None:
    provider = NOAANCNProvider()
    files = asyncio.run(provider.search_observations(_request("1LSU00USA", day=date(2026, 6, 24))))
    assert len(files) == 1
    primary = files[0]
    assert primary.filename == "1lsu1750.26d.gz"
    assert str(primary.url) == (
        "https://geodesy.noaa.gov/corsdata/rinex/2026/175/1lsu/1lsu1750.26d.gz"
    )
    assert str(primary.fallback_candidates[0].url) == (
        "https://noaa-cors-pds.s3.amazonaws.com/rinex/2026/175/1lsu/1lsu1750.26d.gz"
    )


def test_korea_normalized_official_catalog_contains_many_mapped_stations() -> None:
    provider = KoreaNationalCatalogProvider()
    stations = asyncio.run(provider.fetch_station_catalog())
    by_code = {station.marker_name: station for station in stations}

    assert len(stations) == 207
    assert "GANR" in by_code
    assert "DAEJ" in by_code
    assert by_code["GANR"].country == "KOR"
    assert by_code["GANR"].regional_sources == ["korea_national"]
    assert round(by_code["GANR"].latitude, 6) == 37.786215
    assert round(by_code["GANR"].longitude, 6) == 128.872927


def test_kasi_parser_accepts_short_and_long_daily_observation_names() -> None:
    day = date(2024, 1, 2)
    assert KoreaKASIFTPProvider._station_from_filename("ekvn0020.24d", day) == ("EKVN", "2")
    assert KoreaKASIFTPProvider._station_from_filename(
        "EKVN00KOR_R_20240020000_01D_30S_MO.crx", day
    ) == ("EKVN", "3")


def test_kasi_auto_checks_both_roots_and_prefers_rinex3(monkeypatch) -> None:
    provider = KoreaKASIFTPProvider()

    def fake_list(_day):
        return [
            ("kasinet", "ekvn0020.24d", 100),
            ("kvn", "EKVN00KOR_R_20240020000_01D_30S_MO.crx", 200),
            ("kasinet", "daeje0020.24d", 100),  # invalid 5-char station: ignored
        ]

    monkeypatch.setattr(provider, "_list_day_sync", fake_list)
    files = asyncio.run(provider.search_observations(_request("EKVN00KOR")))

    assert len(files) == 1
    assert files[0].filename == "EKVN00KOR_R_20240020000_01D_30S_MO.crx"
    assert "/kvn/daily/2024/002/24d/" in str(files[0].url)
    assert [item.filename for item in files[0].fallback_candidates] == ["ekvn0020.24d"]
    assert "/kasinet/daily/2024/002/24d/" in str(files[0].fallback_candidates[0].url)


def test_korea_national_source_plans_automatic_session_zip() -> None:
    provider = KoreaNationalCatalogProvider()
    files = asyncio.run(provider.search_observations(_request("GANR00KOR")))
    assert len(files) == 1
    assert files[0].provider == "ngii_kr"
    assert files[0].metadata["http_transport"] == "ngii_session_zip"
    assert files[0].metadata["ngii_station"] == "GANR"


def test_new_regional_sources_are_registered() -> None:
    registry = default_regional_source_registry()
    assert registry.get("usa_noaa_cors").provider == "noaa_ncn"
    assert registry.get("korea_kasi").provider == "kasi_kr"
    assert registry.get("korea_national").provider == "ngii_kr"
