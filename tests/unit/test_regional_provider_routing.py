from datetime import date

from gnssgo import GNSSGo
from gnssgo.config import load_settings
from gnssgo.models import ObservationRequest, RemoteFile, Station
from gnssgo.providers.base import GNSSProvider, ProviderCapabilities
from gnssgo.providers.registry import ProviderRegistry
from gnssgo.stations import StationCatalog


class CountingProvider(GNSSProvider):
    def __init__(self, name: str, available: set[tuple[str, date]]) -> None:
        self.name = name
        self.available = available
        self.calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, navigation=False)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        self.calls += 1
        station = (request.stations or [""])[0]
        day = request.date_range.start
        if (station, day) not in self.available:
            return []
        return [
            RemoteFile(
                provider=self.name,
                url=f"https://example.test/{self.name}/{station}-{day}.crx.gz",
                filename=f"{station}-{day}.crx.gz",
                compression=".gz",
                data_type="obs",
                station=station,
                date=day,
                metadata={"logical_id": f"obs:{station}:{day}"},
            )
        ]

    async def search_navigation(self, request):
        return []

    async def search_products(self, request):
        return []


def _client(tmp_path, providers: list[CountingProvider]) -> GNSSGo:
    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)
    settings = load_settings(
        {
            "provider": {"priority": ["whu", "esa"]},
            "stations": {"catalog_path": tmp_path / "stations.sqlite", "auto_seed": False},
        }
    )
    return GNSSGo(settings=settings, registry=registry)


def test_canada_cacs_station_uses_only_cacs(tmp_path) -> None:
    day = date(2026, 1, 3)
    cacs = CountingProvider("cacs_ca", {("CRMR00CAN", day)})
    chain = CountingProvider("chain_ca", {("CRMR00CAN", day)})
    whu = CountingProvider("whu", {("CRMR00CAN", day)})
    client = _client(tmp_path, [cacs, chain, whu])
    StationCatalog(client.settings.stations.catalog_path, seed_if_empty=False).upsert_many(
        [
            Station(
                id="CRMR00CAN",
                country="CAN",
                data_networks=["canada"],
                regional_sources=["cacs_ca"],
                providers=["cacs_ca"],
            )
        ]
    )

    plan = client.plan_observations(
        stations=["CRMR00CAN"],
        start="2026-01-03",
        end="2026-01-03",
        provider="auto",
        data_networks=["canada"],
        output=tmp_path,
    )

    assert [item.provider for item in plan.remote_files] == ["cacs_ca"]
    assert cacs.calls == 1
    assert chain.calls == 0
    assert whu.calls == 0
    assert plan.remote_files[0].metadata["provider_route"] == "regional_source"


def test_canada_chain_station_uses_only_chain(tmp_path) -> None:
    day = date(2026, 1, 3)
    cacs = CountingProvider("cacs_ca", {("CHAIN00CAN", day)})
    chain = CountingProvider("chain_ca", {("CHAIN00CAN", day)})
    whu = CountingProvider("whu", {("CHAIN00CAN", day)})
    client = _client(tmp_path, [cacs, chain, whu])
    StationCatalog(client.settings.stations.catalog_path, seed_if_empty=False).upsert_many(
        [
            Station(
                id="CHAIN00CAN",
                country="CAN",
                data_networks=["canada"],
                regional_sources=["chain_ca"],
                providers=["chain_ca"],
            )
        ]
    )

    plan = client.plan_observations(
        stations=["CHAIN00CAN"],
        start="2026-01-03",
        end="2026-01-03",
        provider="auto",
        data_networks=["canada"],
        output=tmp_path,
    )

    assert [item.provider for item in plan.remote_files] == ["chain_ca"]
    assert cacs.calls == 0
    assert chain.calls == 1
    assert whu.calls == 0


def test_single_provider_region_routes_directly(tmp_path) -> None:
    day = date(2026, 1, 3)
    rbmc = CountingProvider("rbmc_br", {("BRAZ00BRA", day)})
    whu = CountingProvider("whu", {("BRAZ00BRA", day)})
    client = _client(tmp_path, [rbmc, whu])
    StationCatalog(client.settings.stations.catalog_path, seed_if_empty=False).upsert_many(
        [
            Station(
                id="BRAZ00BRA",
                country="BRA",
                data_networks=["brazil"],
                providers=["rbmc_br"],
            )
        ]
    )

    plan = client.plan_observations(
        stations=["BRAZ00BRA"],
        start="2026-01-03",
        end="2026-01-03",
        provider="auto",
        data_networks=["brazil"],
        output=tmp_path,
    )

    assert [item.provider for item in plan.remote_files] == ["rbmc_br"]
    assert rbmc.calls == 1
    assert whu.calls == 0


def test_igs_only_station_inside_region_uses_global_mirrors(tmp_path) -> None:
    day = date(2026, 1, 3)
    cacs = CountingProvider("cacs_ca", {("ALGO00CAN", day)})
    chain = CountingProvider("chain_ca", {("ALGO00CAN", day)})
    whu = CountingProvider("whu", {("ALGO00CAN", day)})
    esa = CountingProvider("esa", set())
    client = _client(tmp_path, [cacs, chain, whu, esa])
    StationCatalog(client.settings.stations.catalog_path, seed_if_empty=False).upsert_many(
        [
            Station(
                id="ALGO00CAN",
                country="CAN",
                data_networks=["igs", "canada"],
                providers=["whu"],
            )
        ]
    )

    plan = client.plan_observations(
        stations=["ALGO00CAN"],
        start="2026-01-03",
        end="2026-01-03",
        provider="auto",
        data_networks=["igs", "canada"],
        output=tmp_path,
    )

    assert [item.provider for item in plan.remote_files] == ["whu"]
    assert whu.calls == 1
    assert esa.calls == 0  # stop after first global mirror success
    assert cacs.calls == 0
    assert chain.calls == 0
    assert plan.remote_files[0].metadata["provider_route"] == "regional_igs_station"


def test_igs_mode_does_not_probe_regional_providers(tmp_path) -> None:
    day = date(2026, 1, 3)
    whu = CountingProvider("whu", set())
    esa = CountingProvider("esa", {("WUH200CHN", day)})
    cacs = CountingProvider("cacs_ca", {("WUH200CHN", day)})
    client = _client(tmp_path, [whu, esa, cacs])
    StationCatalog(client.settings.stations.catalog_path, seed_if_empty=False).upsert_many(
        [Station(id="WUH200CHN", data_networks=["igs"], providers=["whu", "esa"])]
    )

    plan = client.plan_observations(
        stations=["WUH200CHN"],
        start="2026-01-03",
        end="2026-01-03",
        provider="auto",
        data_networks=["igs"],
        output=tmp_path,
    )

    assert [item.provider for item in plan.remote_files] == ["esa"]
    assert whu.calls == 1
    assert esa.calls == 1
    assert cacs.calls == 0


def test_europe_logical_source_routes_to_selected_network_provider(tmp_path) -> None:
    day = date(2026, 1, 3)
    epn = CountingProvider("epn", {("GRAS00FRA", day)})
    rgp = CountingProvider("rgp_fr", {("GRAS00FRA", day)})
    gref = CountingProvider("gref_de", set())
    redgae = CountingProvider("redgae_es", set())
    client = _client(tmp_path, [epn, rgp, gref, redgae])
    StationCatalog(client.settings.stations.catalog_path, seed_if_empty=False).upsert_many(
        [
            Station(
                id="GRAS00FRA",
                country="FRA",
                data_networks=["europe"],
                regional_sources=["europe_epn", "europe_rgp"],
                providers=["epn", "rgp_fr"],
            )
        ]
    )

    plan = client.plan_observations(
        stations=["GRAS00FRA"],
        start="2026-01-03",
        end="2026-01-03",
        provider="auto",
        data_networks=["europe"],
        regional_sources=["europe_rgp"],
        output=tmp_path,
    )

    assert [item.provider for item in plan.remote_files] == ["rgp_fr"]
    assert rgp.calls == 1
    assert epn.calls == 0
    assert gref.calls == 0
    assert redgae.calls == 0
    assert plan.remote_files[0].metadata["provider_route"] == "regional_source"


class DirectoryDiscoveryProvider(GNSSProvider):
    network_directory_discovery = True

    def __init__(self, name: str, stations: list[str]) -> None:
        self.name = name
        self.stations = stations
        self.calls = 0
        self.received_stations: list[str] | None = None

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, navigation=False)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        self.calls += 1
        self.received_stations = list(request.stations or [])
        day = request.date_range.start
        return [
            RemoteFile(
                provider=self.name,
                url=f"https://example.test/{station}_{day}.crx.gz",
                filename=f"{station}_R_{day.year}{day.timetuple().tm_yday:03d}0000_01D_15S_MO.crx.gz",
                compression=".gz",
                data_type="obs",
                station=station,
                date=day,
            )
            for station in self.stations
        ]

    async def search_navigation(self, request):
        return []

    async def search_products(self, request):
        return []


def test_rbmc_full_directory_discovery_bypasses_catalog_station_subset(tmp_path) -> None:
    provider = DirectoryDiscoveryProvider(
        "sirgas_rbmc_br", ["ALAR00BRA", "BRAZ00BRA", "UFPR00BRA"]
    )
    registry = ProviderRegistry()
    registry.register(provider)
    settings = load_settings(
        {
            "provider": {"priority": ["sirgas_rbmc_br"]},
            "stations": {"catalog_path": tmp_path / "stations.sqlite", "auto_seed": False},
        }
    )
    client = GNSSGo(settings=settings, registry=registry)

    # Seed only one IGS-like Brazilian station to reproduce the old failure mode:
    # provider discovery must still return every station in the official directory.
    StationCatalog(client.settings.stations.catalog_path, seed_if_empty=False).upsert_many(
        [Station(id="BRAZ00BRA", country="BRA", data_networks=["igs", "sirgas"])]
    )

    plan = client.plan_observations(
        stations=[],
        start="2026-01-03",
        end="2026-01-03",
        provider="auto",
        data_networks=["sirgas"],
        regional_sources=["sirgas_brazil"],
        discover_available=True,
        output=tmp_path,
    )

    assert provider.calls == 1
    assert provider.received_stations == []
    assert [item.station for item in plan.remote_files] == [
        "ALAR00BRA", "BRAZ00BRA", "UFPR00BRA"
    ]
    assert plan.matched_stations == ["ALAR00BRA", "BRAZ00BRA", "UFPR00BRA"]
    assert plan.unavailable == []
    assert all(
        item.metadata["availability_scope"] == "official_day_directory"
        for item in plan.remote_files
    )


class PassthroughBatchProvider(GNSSProvider):
    batch_observation_passthrough = True

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0
        self.received_stations: list[str] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, navigation=False)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        self.calls += 1
        self.received_stations = list(request.stations or [])
        day = request.date_range.start
        files: list[RemoteFile] = []
        for station in request.stations or []:
            # Two files for the same station/day must both survive core planning.
            for hour in ("a", "b"):
                files.append(
                    RemoteFile(
                        provider=self.name,
                        url=f"sftp://example.test/{station}_{hour}.zip",
                        filename=f"{station[:4]}001{hour}_304.zip",
                        compression=".zip",
                        data_type="obs",
                        station=station,
                        date=day,
                    )
                )
        return files

    async def search_navigation(self, request):
        return []

    async def search_products(self, request):
        return []


def test_regional_passthrough_batches_stations_and_preserves_multiple_files(tmp_path) -> None:
    provider = PassthroughBatchProvider("rgna_mx")
    registry = ProviderRegistry()
    registry.register(provider)
    settings = load_settings(
        {
            "provider": {"priority": ["rgna_mx"]},
            "stations": {"catalog_path": tmp_path / "stations.sqlite", "auto_seed": False},
        }
    )
    client = GNSSGo(settings=settings, registry=registry)
    stations = ["INEG00MEX", "CHET00MEX"]
    StationCatalog(client.settings.stations.catalog_path, seed_if_empty=False).upsert_many(
        [
            Station(
                id=station,
                country="MEX",
                data_networks=["sirgas"],
                regional_sources=["sirgas_mexico"],
                providers=["rgna_mx"],
            )
            for station in stations
        ]
    )

    plan = client.plan_observations(
        stations=stations,
        start="2026-01-01",
        end="2026-01-01",
        provider="auto",
        sampling="15s",
        rinex="3",
        data_networks=["sirgas"],
        regional_sources=["sirgas_mexico"],
        output=tmp_path,
    )

    assert provider.calls == 1
    assert provider.received_stations == stations
    assert len(plan.remote_files) == 4
    assert len(plan.download_tasks) == 4
    assert all(
        item.metadata["provider_route"] == "regional_passthrough_batch"
        for item in plan.remote_files
    )


def test_chile_directory_discovery_uses_one_provider_call(tmp_path) -> None:
    provider = DirectoryDiscoveryProvider("sirgas_cl", ["PTRE00CHL", "ACHS00CHL"])
    registry = ProviderRegistry()
    registry.register(provider)
    settings = load_settings(
        {
            "provider": {"priority": ["sirgas_cl"]},
            "stations": {"catalog_path": tmp_path / "stations.sqlite", "auto_seed": False},
        }
    )
    client = GNSSGo(settings=settings, registry=registry)

    plan = client.plan_observations(
        stations=[],
        start="2026-01-01",
        end="2026-01-01",
        provider="auto",
        sampling="15s",
        rinex="auto",
        data_networks=["sirgas"],
        regional_sources=["sirgas_chile"],
        discover_available=True,
        output=tmp_path,
    )

    assert provider.calls == 1
    assert provider.received_stations == []
    assert {item.station for item in plan.remote_files} == {"PTRE00CHL", "ACHS00CHL"}
