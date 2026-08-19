from __future__ import annotations

import asyncio
from datetime import date

from gnssgo.client import GNSSGo
from gnssgo.config import Settings
from gnssgo.download.geonet import _choose_select_value_script, _select_first_script
from gnssgo.models import DateRange, ObservationRequest
from gnssgo.providers import japan as japan_module
from gnssgo.providers.japan import JapanGEONETProvider
from gnssgo.providers.japan_catalog import JapanStationRecord
from gnssgo.providers.registry import ProviderRegistry


def _row(station_id: str, station_no: str, file_code: str, name: str) -> JapanStationRecord:
    return JapanStationRecord(
        station_id=station_id,
        station_no=station_no,
        file_code=file_code,
        point_code=f"EL{station_no}",
        name_jp=name,
        prefecture="Test",
        facility="",
        receiver="",
        antenna="",
        latitude=35.0,
        longitude=139.0,
    )


def test_raw_webdriver_select_helpers_explicitly_return_values():
    # ChromeDriver Execute Script treats the source as a function body.  A
    # trailing IIFE expression is not enough; without top-level return Python
    # receives None and the polling loop times out even after changing the page.
    assert _choose_select_value_script("day_st_year", "2026").lstrip().startswith("return (function()")
    assert _select_first_script("day_satellite", ["GRJE"]).lstrip().startswith("return (function()")


def test_geonet_multi_station_bundle_is_not_reported_unavailable(monkeypatch, tmp_path):
    rows = [
        _row("016600JPN", "000166", "0166", "A"),
        _row("095400JPN", "000954", "0954", "B"),
        _row("102800JPN", "001028", "1028", "C"),
        _row("304700JPN", "003047", "3047", "D"),
        _row("309900JPN", "003099", "3099", "E"),
    ]
    monkeypatch.setattr(japan_module, "_records", lambda: rows)

    provider = JapanGEONETProvider()
    request = ObservationRequest(
        stations=[row.station_id for row in rows],
        date_range=DateRange(start=date(2026, 1, 19), end=date(2026, 1, 19)),
        provider="geonet_jp",
        sampling="30s",
        rinex="3",
        data_networks=["japan"],
        regional_sources=["japan_geonet"],
    )
    remotes = asyncio.run(provider.search_observations(request))
    assert len(remotes) == 1

    registry = ProviderRegistry()
    registry.register(provider)
    settings = Settings()
    settings.archive.root = tmp_path
    client = GNSSGo(settings=settings, registry=registry)
    plan = asyncio.run(
        client._build_plan(
            request,
            "geonet_jp",
            lambda p: p.search_observations(request),
            tmp_path,
            False,
            True,
        )
    )
    assert len(plan.remote_files) == 1
    assert plan.unavailable == []
