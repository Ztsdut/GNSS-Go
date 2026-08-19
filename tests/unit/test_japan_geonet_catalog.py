from __future__ import annotations

import asyncio
from datetime import date

from gnssgo.models import DateRange, ObservationRequest, Station
from gnssgo.providers import japan as japan_module
from gnssgo.providers.japan import JapanGEONETProvider
from gnssgo.providers.japan_catalog import JapanStationRecord, canonical_geonet_id, parse_cp_geojson, parse_observation_code_html, read_station_csv
from gnssgo.stations import StationCatalog


def _row() -> JapanStationRecord:
    return JapanStationRecord(
        station_id="085100JPN",
        station_no="020851",
        file_code="0851",
        point_code="EL06742209102",
        name_jp="幌延",
        prefecture="北海道",
        facility="test",
        receiver="TRIMBLE ALLOY",
        antenna="TPSCR.G5 GSI5",
        latitude=45.02,
        longitude=141.85,
    )


def test_observation_code_html_parser_reads_official_columns():
    html = """
    <table><tr><th>局番号</th><th>基準点コード</th><th>局名称</th><th>県名</th><th>施設名</th><th>受信機名</th><th>アンテナ名</th></tr>
    <tr><td>020851</td><td>EL06742209102</td><td>幌延</td><td>北海道</td><td>学校</td><td>TRIMBLE ALLOY</td><td>TPSCR.G5 GSI5</td></tr></table>
    """
    rows = parse_observation_code_html(html)
    assert rows["EL06742209102"]["station_no"] == "020851"
    assert rows["EL06742209102"]["name_jp"] == "幌延"


def test_cp_geojson_parser_joins_point_code_and_coordinates():
    payload = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [141.85, 45.02]},
            "properties": {"基準点コード": "EL06742209102", "名称": "幌延"},
        }],
    }
    rows = parse_cp_geojson(payload, source_tile="tile")
    assert rows == [{
        "point_code": "EL06742209102",
        "name_jp": "幌延",
        "latitude": 45.02,
        "longitude": 141.85,
        "source_tile": "tile",
    }]


def test_japan_provider_creates_browser_batch_plan(monkeypatch):
    monkeypatch.setattr(japan_module, "_records", lambda: [_row()])
    provider = JapanGEONETProvider()
    request = ObservationRequest(
        stations=["085100JPN"],
        date_range=DateRange(start=date(2025, 12, 1), end=date(2025, 12, 8)),
        provider="geonet_jp",
        sampling="30s",
        rinex="3",
        data_networks=["japan"],
        regional_sources=["japan_geonet"],
    )
    files = asyncio.run(provider.search_observations(request))
    assert len(files) == 1  # Terras daily download supports up to 10 consecutive days
    assert files[0].metadata["http_transport"] == "geonet_chromedriver"
    assert files[0].metadata["geonet_station_names"] == "幌延"
    assert files[0].metadata["geonet_rinex_choices"] == "3.02,3.03"
    assert files[0].filename.endswith(".zip")


def test_continent_selection_adds_igs_even_without_regional_country_source(tmp_path):
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    regional = Station(
        id="085100JPN", marker_name="幌延", latitude=45.02, longitude=141.85,
        country="JPN", network=["GEONET"], data_networks=["japan"],
        regional_sources=["japan_geonet"], providers=["geonet_jp"],
    )
    china_igs = Station(
        id="WUHN00CHN", marker_name="WUHN", latitude=30.5, longitude=114.5,
        country="CHN", network=["IGS"], data_networks=["igs"], providers=["igs_aux"],
    )
    catalog.upsert_many([regional], provider="geonet_jp", data_network="japan")
    catalog.upsert_many([china_igs], provider="igs_aux", data_network="igs")
    stations = catalog.search(
        data_networks=["japan"],
        regional_sources=["japan_geonet"],
        continents=["Asia"],
    )
    assert {s.id for s in stations} == {"085100JPN", "WUHN00CHN"}


def test_geonet_public_id_is_four_char_plus_00jpn():
    assert canonical_geonet_id("0851") == "085100JPN"
    assert canonical_geonet_id("1102") == "110200JPN"


def test_japan_provider_accepts_legacy_id_but_plans_new_id(monkeypatch):
    monkeypatch.setattr(japan_module, "_records", lambda: [_row()])
    provider = JapanGEONETProvider()
    request = ObservationRequest(
        stations=["JP0851JPN"],
        date_range=DateRange(start=date(2026, 1, 17), end=date(2026, 1, 17)),
        provider="geonet_jp", sampling="30s", rinex="auto",
        data_networks=["japan"], regional_sources=["japan_geonet"],
    )
    files = asyncio.run(provider.search_observations(request))
    assert len(files) == 1
    assert files[0].metadata["geonet_station_ids"] == "085100JPN"
    assert files[0].metadata["geonet_satellite_choices"] == "GRJE"
    assert files[0].metadata["geonet_rinex_choices"] == "3.02"


def test_old_japan_csv_ids_are_normalized_on_read(tmp_path):
    path = tmp_path / "japan.csv"
    path.write_text(
        "station_id,station_no,file_code,point_code,name_jp,prefecture,facility,receiver,antenna,latitude,longitude,source_tile\n"
        "JP1102JPN,001102,1102,EL0001,Test,Tokyo,,,,35.0,139.0,\n",
        encoding="utf-8",
    )
    rows = read_station_csv(path)
    assert rows[0].station_id == "110200JPN"
    assert rows[0].file_code == "1102"
