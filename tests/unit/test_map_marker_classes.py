from gnssgo.gui.services.map_service import station_marker_class, station_to_json
from gnssgo.models import Station


def test_station_marker_classes_use_igs_priority_over_regional_overlap() -> None:
    igs = Station(id="IGS000AAA", data_networks=["igs"])
    regional = Station(
        id="REG000AAA",
        data_networks=["canada"],
        regional_sources=["cacs_ca"],
    )
    overlap = Station(
        id="BOTH00AAA",
        data_networks=["igs", "canada"],
        regional_sources=["cacs_ca"],
    )

    assert station_marker_class(igs) == "igs_only"
    assert station_marker_class(regional) == "regional_only"
    assert station_marker_class(overlap) == "igs_only"
    assert station_to_json(overlap)["marker_class"] == "igs_only"


def test_regional_source_alone_is_enough_for_regional_marker() -> None:
    station = Station(id="REG001AAA", regional_sources=["chain_ca"])
    assert station_marker_class(station) == "regional_only"


def test_igs_only_view_paints_overlap_station_as_igs() -> None:
    overlap = Station(
        id="BOTH01AAA",
        data_networks=["igs", "canada"],
        regional_sources=["cacs_ca"],
    )
    assert station_marker_class(overlap, ["igs"]) == "igs_only"
    assert station_to_json(overlap, ["igs"])["marker_class"] == "igs_only"


def test_regional_only_view_keeps_igs_overlap_visible() -> None:
    regional = Station(
        id="REG002AAA",
        data_networks=["canada"],
        regional_sources=["cacs_ca"],
    )
    overlap = Station(
        id="BOTH02AAA",
        data_networks=["igs", "canada"],
        regional_sources=["cacs_ca"],
    )
    assert station_marker_class(regional, ["canada"]) == "regional_only"
    assert station_marker_class(overlap, ["canada"]) == "igs_only"


def test_igs_plus_regional_view_uses_blue_for_igs_and_orange_for_regional() -> None:
    igs = Station(id="IGS002AAA", data_networks=["igs"])
    regional = Station(
        id="REG003AAA",
        data_networks=["canada"],
        regional_sources=["cacs_ca"],
    )
    overlap = Station(
        id="BOTH03AAA",
        data_networks=["igs", "canada"],
        regional_sources=["cacs_ca"],
    )
    active = ["igs", "canada"]
    assert station_marker_class(igs, active) == "igs_only"
    assert station_marker_class(regional, active) == "regional_only"
    assert station_marker_class(overlap, active) == "igs_only"


def test_leaflet_keeps_continuous_wrapped_world_map() -> None:
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "src/gnssgo/gui/resources/map/map.js").read_text(encoding="utf-8")
    assert "worldCopyJump: true" in js
    assert "noWrap: true" not in js
    assert "maxBoundsViscosity" not in js


def test_leaflet_rejects_null_coordinates_instead_of_mapping_them_to_zero() -> None:
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "src/gnssgo/gui/resources/map/map.js").read_text(encoding="utf-8")
    assert "station.lat === null" in js
    assert "station.lon === null" in js


def test_sirgas_map_payload_hides_stale_out_of_region_coordinate() -> None:
    station = Station(
        id="BAD100BOL",
        latitude=-80.0,
        longitude=30.0,
        country="BOL",
        data_networks=["sirgas"],
        regional_sources=["sirgas_bolivia"],
    )
    payload = station_to_json(station, ["sirgas"])
    assert payload["lat"] is None
    assert payload["lon"] is None


def test_sirgas_map_payload_normalizes_legacy_360_longitude() -> None:
    station = Station(
        id="GOOD00BRA",
        latitude=-15.0,
        longitude=310.0,
        country="BRA",
        data_networks=["sirgas"],
        regional_sources=["sirgas_brazil"],
    )
    payload = station_to_json(station, ["sirgas"])
    assert payload["lat"] == -15.0
    assert payload["lon"] == -50.0


def test_leaflet_recreates_station_layers_when_network_changes() -> None:
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "src/gnssgo/gui/resources/map/map.js").read_text(encoding="utf-8")
    assert "resetStationLayers()" in js
    assert "this.cluster = this.createClusterLayer();" in js
