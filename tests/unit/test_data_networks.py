from gnssgo.client import GNSSGo
from gnssgo.data_networks import default_data_network_registry
from gnssgo.models import Station
from gnssgo.stations import StationCatalog


def test_data_network_registry_contains_phase4a_networks() -> None:
    registry = default_data_network_registry()

    assert registry.get("igs").name == "IGS"
    assert registry.get("japan").providers == ["geonet_jp"]
    assert registry.get("north-america").id == "north_america"
    assert "ga" in registry.providers_for(["australia"])


def test_station_catalog_merges_data_network_membership(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [Station(id="TEST00JPN", country="JPN", network=["igs"], data_networks=["igs"])],
        provider="bkg",
    )
    catalog.upsert_many(
        [Station(id="TEST00JPN", country="JPN", data_networks=["japan"])],
        provider="geonet_jp",
    )

    station = catalog.get("TEST00JPN")
    assert station is not None
    assert station.data_networks == ["igs", "japan"]
    assert [item.id for item in catalog.search(data_networks=["japan"])] == ["TEST00JPN"]


def test_station_catalog_infers_data_networks_for_old_payloads(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many([Station(id="AIRA00JPN", country="JPN", network=["igs"])])

    station = catalog.get("AIRA00JPN")

    assert station is not None
    assert {"igs", "japan"}.issubset(set(station.data_networks))


def test_data_network_provider_priority_prefers_selected_network() -> None:
    client = GNSSGo()

    priority = client.data_network_provider_priority(["japan"])

    assert priority[0] == "geonet_jp"
    assert "whu" in priority


def test_station_catalog_infers_new_country_data_networks(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [
            Station(id="DELF00NLD", country="NLD"),
            Station(id="MATE00ITA", country="ITA"),
            Station(id="ULAB00MNG", country="MNG"),
            Station(id="CORC00USA", country="USA"),
        ]
    )

    assert "netherlands" in catalog.get("DELF00NLD").data_networks
    assert "italy" in catalog.get("MATE00ITA").data_networks
    assert "mongolia" in catalog.get("ULAB00MNG").data_networks
    usa_networks = set(catalog.get("CORC00USA").data_networks)
    assert {"united_states", "north_america"}.issubset(usa_networks)
