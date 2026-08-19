from pathlib import Path

from gnssgo.stations import StationCatalog


def test_builtin_igs_seed_has_mappable_africa_and_antarctica(tmp_path: Path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=True)

    africa = catalog.search(data_networks=["igs"], continents=["Africa"])
    antarctica = catalog.search(data_networks=["igs"], continents=["Antarctica"])

    assert any(station.id == "HRAO00ZAF" for station in africa)
    assert any(station.id == "DAV100ATA" for station in antarctica)
    assert all(station.latitude is not None and station.longitude is not None for station in africa + antarctica)


def test_igs_without_continent_filter_remains_global(tmp_path: Path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=True)

    global_igs = catalog.search(data_networks=["igs"])
    africa = catalog.search(data_networks=["igs"], continents=["Africa"])

    assert len(global_igs) > len(africa)
    assert {station.id for station in africa}.issubset({station.id for station in global_igs})
