from pathlib import Path

import pytest

from gnssgo.client import GNSSGo
from gnssgo.config import load_settings
from gnssgo.exceptions import ConfigurationError
from gnssgo.models import Station
from gnssgo.regional_sources import default_regional_source_registry
from gnssgo.stations import StationCatalog


def test_australia_source_allowlist_and_normalization() -> None:
    registry = default_regional_source_registry()

    assert [source.name for source in registry.all("australia")] == [
        "AUSCOPE",
        "CORSNET-NSW",
        "GPSNET",
        "RTKNETWEST",
        "SUNPOZ",
        "NTCORS",
        "QLD_TMR",
        "IPS",
        "UPG",
        "RPS",
        "SMARTNET",
    ]
    assert registry.normalize("CORSNET-NSW") == "corsnet_nsw"
    assert registry.normalize("corsnet_nsw") == "corsnet_nsw"
    assert registry.normalize("CORSNET NSW") == "corsnet_nsw"
    assert not registry.contains("GEONET", data_network="australia")
    assert not registry.contains("JAXA", data_network="australia")
    assert not registry.contains("HKSAR", data_network="australia")
    assert not registry.contains("POSITIONZ", data_network="australia")


def test_station_catalog_merges_network_and_source_union(tmp_path: Path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite")
    catalog.upsert_many([Station(id="ALIC00AUS", network=["igs"], data_networks=["igs"])])
    catalog.upsert_many(
        [
            Station(
                id="ALIC00AUS",
                data_networks=["australia"],
                regional_sources=["auscope", "gpsnet"],
                providers=["ga"],
            )
        ],
        provider="ga",
        data_network="australia",
    )

    station = catalog.get("ALIC00AUS")
    assert station is not None
    assert station.data_networks == ["australia", "igs"]
    assert station.regional_sources == ["auscope", "gpsnet"]


def test_station_catalog_source_filter_intersects_bbox(tmp_path: Path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite")
    catalog.upsert_many(
        [
            Station(
                id="NSW100AUS",
                latitude=-33,
                longitude=151,
                data_networks=["australia"],
                regional_sources=["corsnet_nsw"],
            ),
            Station(
                id="GPS100AUS",
                latitude=-37,
                longitude=145,
                data_networks=["australia"],
                regional_sources=["gpsnet"],
            ),
        ]
    )

    stations = catalog.search_bbox(
        140,
        -40,
        160,
        -30,
        data_networks=["australia"],
        regional_sources=["CORSNET-NSW"],
    )

    assert [station.id for station in stations] == ["NSW100AUS"]


def test_regional_source_without_network_infers_australia(tmp_path: Path) -> None:
    catalog_path = tmp_path / "stations.sqlite"
    catalog = StationCatalog(catalog_path)
    catalog.upsert_many(
        [
            Station(
                id="NSW100AUS",
                data_networks=["australia"],
                regional_sources=["corsnet_nsw"],
            )
        ]
    )
    client = GNSSGo(settings=load_settings({"stations": {"catalog_path": str(catalog_path)}}))

    stations = client.search_stations(regional_sources=["CORSNET-NSW"])

    assert [station.id for station in stations] == ["NSW100AUS"]


def test_invalid_source_for_network_raises(tmp_path: Path) -> None:
    client = GNSSGo(
        settings=load_settings({"stations": {"catalog_path": str(tmp_path / "stations.sqlite")}})
    )

    with pytest.raises(ConfigurationError, match="not a source of europe"):
        client.search_stations(data_networks=["europe"], regional_sources=["CORSNET-NSW"])


def test_sqlite_schema_version_is_migrated(tmp_path: Path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite")

    with catalog._connect() as db:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])

    assert version >= 2



def test_europe_logical_networks_are_registered() -> None:
    registry = default_regional_source_registry()
    sources = {source.id: source for source in registry.all("europe")}

    expected = {
        "europe_epn": ("EPN", "epn"),
        "europe_rgp": ("France · RGP", "rgp_fr"),
        "europe_gref": ("Germany · GREF", "gref_de"),
        "europe_redgae": ("Spain · redGAE", "redgae_es"),
        "europe_nsgi": ("Netherlands · AGRS/NETPOS", "nsgi_nl"),
        "europe_apos": ("Austria · APOS", "apos_at"),
        "europe_renep": ("Portugal · ReNEP", "renep_pt"),
        "europe_belgium": ("Belgium · GNSS.be", "belgium_be"),
        "europe_greece": ("Greece · NOA/EPOS", "noa_gr"),
        "europe_hungary": ("Hungary · EPOS/GLASS", "epos_hu"),
        "europe_czechia": ("Czechia · EPOS/GLASS", "epos_cz"),
        "europe_malta": ("Malta · EPOS/GLASS", "epos_mt"),
        "europe_montenegro": ("Montenegro · EPOS/GLASS", "epos_me"),
    }
    for source_id, (name, provider) in expected.items():
        assert source_id in sources
        assert (sources[source_id].name, sources[source_id].provider) == (name, provider)

    # Europe now includes the original logical networks plus country-level EPOS/GLASS layers.
    assert len(sources) >= 40
    # Physical EPN data-centre names and old ids are compatibility aliases only.
    assert registry.normalize("ROB / EPN") == "europe_epn"
    assert registry.normalize("BEV") == "europe_epn"
    assert registry.normalize("BKG") == "europe_epn"
    assert registry.normalize("IGN") == "europe_epn"
    assert registry.normalize("epn_hdc") == "europe_epn"
    assert registry.normalize("epn_bkgi") == "europe_epn"
    assert registry.normalize("epn_bkge") == "europe_epn"
    assert registry.contains("France · RGP", data_network="europe")


def test_sirgas_logical_networks_are_registered() -> None:
    registry = default_regional_source_registry()
    sources = {source.id: source for source in registry.all("sirgas")}
    assert sources["sirgas_argentina"].provider == "ramsac_ar"
    assert sources["sirgas_brazil"].provider == "sirgas_rbmc_br"
    assert sources["sirgas_mexico"].provider == "rgna_mx"
    assert "sirgas_paraguay" not in sources
    assert "sirgas_venezuela" not in sources
    assert "sirgas_guyana" not in sources
    assert "sirgas_suriname" not in sources

def test_canada_regional_sources_are_registered() -> None:
    registry = default_regional_source_registry()

    assert [(source.id, source.name) for source in registry.all("canada")] == [
        ("cacs_ca", "NRCan CACS"),
        ("chain_ca", "UNB CHAIN"),
    ]


def test_canada_source_is_inferred_from_provider_for_old_cache(tmp_path: Path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite")
    catalog.upsert_many(
        [
            Station(
                id="TEST00CAN",
                country="CAN",
                data_networks=["canada"],
                providers=["cacs_ca"],
            )
        ]
    )

    station = catalog.get("TEST00CAN")
    assert station is not None
    assert "cacs_ca" in station.regional_sources
    assert [item.id for item in catalog.search(regional_sources=["cacs_ca"])] == [
        "TEST00CAN"
    ]


def test_successful_europe_refresh_prunes_stale_source_only_stations(tmp_path: Path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite")
    catalog.upsert_many(
        [
            Station(
                id="BADD00ESP",
                latitude=-5.0,
                longitude=105.0,
                country="ESP",
                network=["redGAE"],
                data_networks=["europe"],
                regional_sources=["europe_redgae"],
                providers=["redgae_es"],
            ),
            Station(
                id="GOOD00ESP",
                latitude=40.0,
                longitude=-3.0,
                country="ESP",
                network=["redGAE"],
                data_networks=["europe"],
                regional_sources=["europe_redgae"],
                providers=["redgae_es"],
            ),
        ],
        provider="redgae_es",
        data_network="europe",
    )

    catalog.upsert_many(
        [
            Station(
                id="GOOD00ESP",
                latitude=40.1,
                longitude=-3.1,
                country="ESP",
                network=["redGAE"],
                data_networks=["europe"],
                regional_sources=["europe_redgae"],
                providers=["redgae_es"],
            )
        ],
        provider="redgae_es",
        data_network="europe",
    )

    assert catalog.get("BADD00ESP") is None
    good = catalog.get("GOOD00ESP")
    assert good is not None
    assert good.latitude == pytest.approx(40.1)
    assert good.longitude == pytest.approx(-3.1)


def test_europe_refresh_preserves_shared_station_when_one_provider_drops_it(tmp_path: Path) -> None:
    catalog = StationCatalog(tmp_path / "stations.sqlite")
    catalog.upsert_many(
        [
            Station(
                id="SHAR00ESP",
                latitude=40.0,
                longitude=-3.0,
                country="ESP",
                network=["igs"],
                data_networks=["igs"],
                providers=["bkg"],
            )
        ],
        provider="bkg",
        data_network="igs",
    )
    catalog.upsert_many(
        [
            Station(
                id="SHAR00ESP",
                latitude=40.0,
                longitude=-3.0,
                country="ESP",
                network=["redGAE"],
                data_networks=["europe"],
                regional_sources=["europe_redgae"],
                providers=["redgae_es"],
            )
        ],
        provider="redgae_es",
        data_network="europe",
    )

    catalog.upsert_many([], provider="redgae_es", data_network="europe")

    station = catalog.get("SHAR00ESP")
    assert station is not None
    assert "bkg" in station.providers
    assert "redgae_es" not in station.providers
    assert "europe_redgae" not in station.regional_sources
