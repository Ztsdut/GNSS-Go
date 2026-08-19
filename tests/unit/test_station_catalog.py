from gnssgo.models import Station
from gnssgo.stations.catalog import StationCatalog, seed_stations


def test_station_model_accepts_legacy_code() -> None:
    station = Station(code="wuh200chn", network="igs")
    assert station.id == "wuh200chn"
    assert station.code == "wuh200chn"
    assert station.network == ["igs"]


def test_catalog_alias_and_filters(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [
            Station(
                id="TEST00JPN",
                aliases=["TST0"],
                latitude=35.0,
                longitude=140.0,
                country="JPN",
                network=["igs"],
                providers=["sopac"],
            )
        ],
        provider="sopac",
        source="unit",
    )

    assert catalog.get("TST0").id == "TEST00JPN"
    assert catalog.search(network=["igs"], country="JPN", provider="sopac")
    assert catalog.search_bbox(128, 30, 146, 46, country="JPN")
    assert catalog.search_radius(35.68, 139.76, 500, network=["igs"])


def test_catalog_does_not_merge_by_four_char_only(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [
            Station(id="ABCD00AAA", aliases=["ABCD"], latitude=0, longitude=0),
            Station(id="ABCD00BBB", latitude=10, longitude=10),
        ]
    )

    assert catalog.get("ABCD00AAA").id == "ABCD00AAA"
    assert catalog.get("ABCD00BBB").id == "ABCD00BBB"


def test_default_catalog_does_not_seed_when_empty(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    assert catalog.count() == 0
    assert catalog.search() == []


def test_seed_fixture_can_be_injected(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    summary = catalog.upsert_many(seed_stations(), provider="builtin", source="unit")
    assert summary.fetched > 0
    assert catalog.get("WUH2").id == "WUH200CHN"


def test_catalog_alias_conflict_is_not_reassigned(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [Station(id="AAAA00AAA", aliases=["DUPL"])],
        provider="one",
        source="unit",
    )
    summary = catalog.upsert_many(
        [Station(id="BBBB00BBB", aliases=["DUPL"])],
        provider="two",
        source="unit",
    )

    assert catalog.get("DUPL").id == "AAAA00AAA"
    assert summary.alias_conflicts == [
        {
            "alias": "DUPL",
            "existing_station_id": "AAAA00AAA",
            "incoming_station_id": "BBBB00BBB",
        }
    ]


def test_catalog_empty_data_network_filter_is_explicit_zero(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [Station(id="AAAA00AAA", latitude=0, longitude=0, data_networks=["igs"])],
        provider="unit",
        source="unit",
    )

    assert catalog.search(data_networks=None)
    assert catalog.search(data_networks=[]) == []


def test_catalog_regional_source_filters_only_matching_regional_network(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [
            Station(id="IGS000AAA", latitude=0, longitude=0, data_networks=["igs"]),
            Station(
                id="IGSA00AUS",
                latitude=-31,
                longitude=149,
                country="AUS",
                data_networks=["igs"],
            ),
            Station(
                id="ALBY00AUS",
                latitude=-35,
                longitude=117,
                country="AUS",
                data_networks=["australia"],
                regional_sources=["auscope"],
            ),
            Station(
                id="NSW000AUS",
                latitude=-34,
                longitude=151,
                country="AUS",
                data_networks=["australia"],
                regional_sources=["corsnet_nsw"],
            ),
        ],
        provider="unit",
        source="unit",
    )

    result = catalog.search(
        data_networks=["igs", "australia"],
        regional_sources=["auscope"],
    )

    # IGS is clipped to the selected regional scope.  The Australian IGS
    # station remains visible, but the unrelated global IGS station does not.
    assert {station.id for station in result} == {"IGSA00AUS", "ALBY00AUS"}
    assert {
        station.id
        for station in catalog.search(
            data_networks=["igs", "australia"],
            regional_sources=[],
        )
    } == {"IGSA00AUS"}


def test_igs_plus_canada_is_scoped_to_canada(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [
            Station(
                id="GLOB00USA",
                country="USA",
                data_networks=["igs"],
            ),
            Station(
                id="IGSC00CAN",
                country="CAN",
                data_networks=["igs"],
            ),
            Station(
                id="CORS00CAN",
                country="CAN",
                data_networks=["canada"],
            ),
        ],
        provider="unit",
        source="unit",
    )

    result = catalog.search(data_networks=["igs", "canada"])
    assert {station.id for station in result} == {"IGSC00CAN", "CORS00CAN"}

    igs_only = catalog.search(data_networks=["igs"])
    assert {station.id for station in igs_only} == {"GLOB00USA", "IGSC00CAN"}


def test_igs_plus_multiple_regions_uses_union_of_selected_regions(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [
            Station(id="CAN100CAN", country="CAN", data_networks=["igs"]),
            Station(id="AUS100AUS", country="AUS", data_networks=["igs"]),
            Station(id="JPN100JPN", country="JPN", data_networks=["igs"]),
            Station(
                id="CORS10CAN",
                country="CAN",
                data_networks=["canada"],
            ),
        ],
        provider="unit",
        source="unit",
    )

    result = catalog.search(data_networks=["igs", "canada", "australia"])
    assert {station.id for station in result} == {
        "CAN100CAN",
        "AUS100AUS",
        "CORS10CAN",
    }


def test_catalog_fast_regional_source_counts(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [
            Station(id="AAAA00ESP", regional_sources=["europe_redgae"], data_networks=["europe"]),
            Station(id="BBBB00ESP", regional_sources=["europe_redgae"], data_networks=["europe"]),
            Station(id="CCCC00AUT", regional_sources=["europe_apos"], data_networks=["europe"]),
        ],
        provider="unit",
        source="unit",
    )
    assert catalog.regional_source_counts(["europe_redgae", "europe_apos", "europe_rgp"]) == {
        "europe_redgae": 2,
        "europe_apos": 1,
        "europe_rgp": 0,
    }


def test_catalog_fast_mappable_source_counts(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [
            Station(id="AAAA00ARG", latitude=-34.0, longitude=-58.0, regional_sources=["sirgas_argentina"]),
            Station(id="BBBB00ARG", regional_sources=["sirgas_argentina"]),
            Station(id="CCCC00BRA", latitude=-20.0, longitude=-45.0, regional_sources=["sirgas_brazil"]),
        ],
        provider="unit",
        source="unit",
    )
    assert catalog.regional_source_mappable_counts(["sirgas_argentina", "sirgas_brazil"]) == {
        "sirgas_argentina": 1,
        "sirgas_brazil": 1,
    }


def test_sirgas_mappable_count_excludes_points_outside_latin_america(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [
            Station(
                id="GOOD00BOL",
                latitude=-16.5,
                longitude=-68.15,
                country="BOL",
                data_networks=["sirgas"],
                regional_sources=["sirgas_bolivia"],
            ),
            Station(
                id="BAD100BOL",
                latitude=-80.0,
                longitude=30.0,
                country="BOL",
                data_networks=["sirgas"],
                regional_sources=["sirgas_bolivia"],
            ),
        ],
        provider="unit",
        source="unit",
    )
    assert catalog.regional_source_mappable_counts(["sirgas_bolivia"]) == {
        "sirgas_bolivia": 1,
    }


def test_sirgas_cache_sanitizer_clears_ghost_points_and_normalizes_longitude(tmp_path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [
            Station(
                id="BAD200BOL",
                latitude=-80.0,
                longitude=30.0,
                country="BOL",
                data_networks=["sirgas"],
                regional_sources=["sirgas_bolivia"],
            ),
            Station(
                id="GOOD00BRA",
                latitude=-15.0,
                longitude=310.0,
                country="BRA",
                data_networks=["sirgas"],
                regional_sources=["sirgas_brazil"],
            ),
        ],
        provider="unit",
        source="unit",
    )
    assert catalog.sanitize_sirgas_coordinates() == 2
    bad = catalog.get("BAD200BOL")
    good = catalog.get("GOOD00BRA")
    assert bad is not None and bad.latitude is None and bad.longitude is None
    assert good is not None and good.longitude == -50.0
