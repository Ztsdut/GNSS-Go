from gnssgo.data_networks import default_data_network_registry
from gnssgo.providers.regional_expansion import SatRefHKProvider
from gnssgo.regional_sources import default_regional_source_registry
from gnssgo.stations.snapshot import load_bundled_station_snapshot, snapshot_metadata


def test_release_snapshot_is_large_and_mapped() -> None:
    stations = load_bundled_station_snapshot()
    meta = snapshot_metadata()
    assert len(stations) >= 2500
    assert meta.get("mapped_station_count", 0) >= 2500
    assert all(st.latitude is not None and st.longitude is not None for st in stations)


def test_release_snapshot_contains_hong_kong_satref() -> None:
    stations = load_bundled_station_snapshot()
    hk = [st for st in stations if "hong_kong" in st.data_networks]
    assert len(hk) >= 19
    assert {"HKCL00HKG", "HKST00HKG", "T43000HKG"}.issubset({st.id for st in hk})


def test_english_geographic_names_are_explicit() -> None:
    networks = default_data_network_registry()
    sources = default_regional_source_registry()
    assert networks.get("taiwan").name == "Taiwan, China"
    assert networks.get("hong_kong").name == "Hong Kong, China"
    assert sources.get("taiwan_gdms").name.startswith("Taiwan, China")
    assert sources.get("hongkong_satref").name.startswith("Hong Kong, China")


def test_hong_kong_bundled_catalog_has_official_coordinates() -> None:
    stations = SatRefHKProvider().bundled_station_catalog()
    assert len(stations) == 19
    assert all(st.country == "HKG" for st in stations)
    assert all(st.latitude is not None and st.longitude is not None for st in stations)
    assert all("hong_kong" in st.data_networks for st in stations)
