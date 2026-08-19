from __future__ import annotations

from gnssgo.data_networks import default_data_network_registry
from gnssgo.gui.services.map_service import station_marker_class
from gnssgo.models import Station
from gnssgo.provider_info import provider_info
from gnssgo.providers.china_region import CMONOCChinaProvider, TaiwanGDMSProvider
from gnssgo.regional_sources import default_regional_source_registry
from gnssgo.stations import StationCatalog


def test_taiwan_bundled_catalog_keeps_only_gnss_networks():
    stations = TaiwanGDMSProvider().bundled_station_catalog()
    assert len(stations) == 223
    assert {s.country for s in stations} == {"TWN"}
    assert all(s.network and s.network[0].startswith("GNSS") for s in stations)
    networks = {s.network[0] for s in stations}
    assert {"GNSS", "GNSS_IES", "GNSS_ETEC"}.issubset(networks)


def test_cmonoc_bundled_catalog_contains_all_263_sites():
    stations = CMONOCChinaProvider().bundled_station_catalog()
    assert len(stations) == 263
    assert any(s.id == "BJFS00CHN" for s in stations)
    assert any(s.id == "HIYS00CHN" for s in stations)


def test_china_and_taiwan_are_asian_regional_networks():
    networks = default_data_network_registry()
    assert networks.get("china").providers == ["cmonoc_cn"]
    assert networks.get("taiwan").providers == ["gdms_tw"]
    sources = default_regional_source_registry()
    assert sources.get("china_cmonoc").data_network == "china"
    assert sources.get("taiwan_gdms").data_network == "taiwan"


def test_original_source_urls_are_exposed():
    assert provider_info("gdms_tw").url == "https://gdms.cwa.gov.tw/GeophyDownload.php"
    assert "siteInfo_jizhun" in provider_info("cmonoc_cn").url


def test_continent_selection_includes_igs_and_regional_and_igs_stays_blue(tmp_path):
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    igs = Station(
        id="WUH200CHN", marker_name="WUH2", latitude=30.5, longitude=114.4,
        country="CHN", network=["IGS"], data_networks=["igs"], providers=["igs_aux"],
    )
    overlap = Station(
        id="BJFS00CHN", marker_name="BJFS", latitude=39.6, longitude=115.8,
        country="CHN", network=["IGS", "CMONOC"], data_networks=["igs", "china"],
        regional_sources=["china_cmonoc"], providers=["igs_aux", "cmonoc_cn"],
    )
    regional = Station(
        id="AHAQ00CHN", marker_name="AHAQ", latitude=30.6, longitude=116.9,
        country="CHN", network=["CMONOC"], data_networks=["china"],
        regional_sources=["china_cmonoc"], providers=["cmonoc_cn"],
    )
    catalog.upsert_many([igs], provider="igs_aux", data_network="igs")
    catalog.upsert_many([overlap, regional], provider="cmonoc_cn", data_network="china")
    rows = catalog.search(
        data_networks=["china"], regional_sources=["china_cmonoc"], continents=["Asia"]
    )
    assert {s.id for s in rows} == {"WUH200CHN", "BJFS00CHN", "AHAQ00CHN"}
    by_id = {s.id: s for s in rows}
    assert station_marker_class(by_id["WUH200CHN"], ["china"]) == "igs_only"
    assert station_marker_class(by_id["BJFS00CHN"], ["china"]) == "igs_only"
    assert station_marker_class(by_id["AHAQ00CHN"], ["china"]) == "regional_only"
