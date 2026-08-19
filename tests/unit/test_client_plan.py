from gnssgo import GNSSGo
from gnssgo.config import load_settings
from gnssgo.models import NavigationRequest, ObservationRequest, RemoteFile
from gnssgo.providers.base import GNSSProvider, ProviderCapabilities
from gnssgo.providers.registry import ProviderRegistry
from gnssgo.stations import StationCatalog
from gnssgo.stations.catalog import seed_stations


class FakeNavProvider(GNSSProvider):
    def __init__(self, name: str, *, has_nav: bool = True) -> None:
        self.name = name
        self.has_nav = has_nav

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(navigation=True)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        return []

    async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
        if not self.has_nav:
            return []
        day = request.date_range.start
        return [
            RemoteFile(
                provider=self.name,
                url=f"https://example.test/{self.name}/BRDC00IGS_R_{day:%Y%j}0000_01D_MN.rnx.gz",
                filename=f"BRDC00IGS_R_{day:%Y%j}0000_01D_MN.rnx.gz",
                compression=".gz",
                data_type="nav",
                date=day,
                metadata={"logical_id": f"nav:{day}:mixed"},
            )
        ]

    async def search_products(self, request) -> list[RemoteFile]:
        return []


class FakeObservationProvider(GNSSProvider):
    name = "obs"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        station = (request.stations or [""])[0]
        day = request.date_range.start
        return [
            RemoteFile(
                provider=self.name,
                url=f"https://example.test/{station}.crx.gz",
                filename=f"{station}.crx.gz",
                compression=".gz",
                data_type="obs",
                station=station,
                date=day,
                metadata={"logical_id": f"obs:{station}:{day}"},
            )
        ]

    async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
        return []

    async def search_products(self, request) -> list[RemoteFile]:
        return []


def fake_nav_client(*providers: FakeNavProvider) -> GNSSGo:
    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)
    return GNSSGo(registry=registry)


def fake_obs_client(tmp_path) -> GNSSGo:
    registry = ProviderRegistry()
    registry.register(FakeObservationProvider())
    catalog_path = tmp_path / "stations.sqlite"
    StationCatalog(catalog_path, seed_if_empty=False).upsert_many(seed_stations())
    settings = load_settings(
        {
            "provider": {"priority": ["obs"]},
            "stations": {"catalog_path": catalog_path, "auto_seed": False},
        }
    )
    return GNSSGo(settings=settings, registry=registry)


def test_dry_run_plan_for_observation(tmp_path) -> None:
    client = fake_obs_client(tmp_path)
    plan = client.plan_observations(
        stations=["WUH200CHN"],
        start="2026-213",
        end="2026-213",
        output=tmp_path,
    )
    assert plan.remote_files
    assert plan.download_tasks
    destination = plan.download_tasks[0].destination
    assert destination.parts[-3:] == ("213", "obs", destination.name)
    assert plan.download_tasks[0].decompress is False


def test_existing_processed_rnx_skips_download(tmp_path) -> None:
    existing = tmp_path / "2026" / "213" / "nav" / "BRDC00WRD_R_20262130000_01D_MN.rnx"
    existing.parent.mkdir(parents=True)
    existing.write_text(
        "     3.05           NAVIGATION DATA     M                   RINEX VERSION / TYPE\n",
        encoding="ascii",
    )
    client = GNSSGo(settings=load_settings({"archive": {"auto_extract": True}}))
    plan = client.plan_navigation(
        start="2026-08-01",
        end="2026-08-01",
        provider="bkg",
        output=tmp_path,
    )
    assert len(plan.existing_files) == 1
    assert not plan.download_tasks


def test_plan_remembers_output_root(tmp_path) -> None:
    client = GNSSGo()
    plan = client.plan_navigation(
        start="2026-08-01",
        end="2026-08-01",
        provider="bkg",
        output=tmp_path,
    )
    assert plan.archive_root == tmp_path


class PartialObservationProvider(FakeObservationProvider):
    name = "partial_obs"

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
        station = (request.stations or [""])[0]
        day = request.date_range.start
        if station.upper().startswith("BJFS"):
            return []
        return [
            RemoteFile(
                provider=self.name,
                url=f"https://example.test/{station}_{day:%Y%j}.crx.gz",
                filename=f"{station}_{day:%Y%j}.crx.gz",
                compression=".gz",
                data_type="obs",
                station=station,
                date=day,
                metadata={"logical_id": f"obs:{station}:{day}"},
            )
        ]


def _partial_obs_client(tmp_path) -> GNSSGo:
    registry = ProviderRegistry()
    registry.register(PartialObservationProvider())
    catalog_path = tmp_path / "stations_partial.sqlite"
    StationCatalog(catalog_path, seed_if_empty=False).upsert_many(seed_stations())
    settings = load_settings(
        {
            "provider": {"priority": ["partial_obs"]},
            "stations": {"catalog_path": catalog_path, "auto_seed": False},
        }
    )
    return GNSSGo(settings=settings, registry=registry)


def test_observation_plan_reports_missing_station_day(tmp_path) -> None:
    client = _partial_obs_client(tmp_path)
    plan = client.plan_observations(
        stations=["WUH200CHN", "BJFS00CHN"],
        start="2026-08-15",
        end="2026-08-15",
        provider="auto",
        output=tmp_path,
    )
    assert len(plan.remote_files) == 1
    assert plan.unavailable == ["BJFS00CHN — 2026-08-15"]
    assert plan.missing == plan.unavailable


def test_direct_provider_empty_result_is_not_found_and_reports_all_missing(tmp_path) -> None:
    client = _partial_obs_client(tmp_path)
    plan = client.plan_observations(
        stations=["BJFS00CHN"],
        start="2026-08-15",
        end="2026-08-15",
        provider="partial_obs",
        output=tmp_path,
    )
    assert not plan.remote_files
    assert plan.unavailable == ["BJFS00CHN — 2026-08-15"]
    assert plan.attempted_providers[0].status == "not_found"


def test_default_compressed_archive_is_canonical_existing_file(tmp_path) -> None:
    client = fake_obs_client(tmp_path)
    archive = tmp_path / "2026" / "213" / "obs" / "WUH200CHN.crx.gz"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"already-downloaded")
    plan = client.plan_observations(
        stations=["WUH200CHN"],
        start="2026-213",
        end="2026-213",
        output=tmp_path,
    )
    assert len(plan.existing_files) == 1
    assert plan.existing_files[0].path == archive
    assert not plan.download_tasks


def test_epn_plan_keeps_stations_and_date_range_batched(tmp_path) -> None:
    from gnssgo.models import Station

    class FakeBatchEPN(GNSSProvider):
        name = "epn"

        def __init__(self) -> None:
            self.calls: list[tuple[list[str], object, object]] = []

        def capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(observations=True, station_metadata=True)

        async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
            self.calls.append((list(request.stations or []), request.date_range.start, request.date_range.end))
            files: list[RemoteFile] = []
            for station in request.stations or []:
                for day in request.date_range.days():
                    filename = f"{station}_R_{day:%Y%j}0000_01D_30S_MO.crx.gz"
                    files.append(
                        RemoteFile(
                            provider="epn",
                            url=f"https://igs.bkg.bund.de/root_ftp/EUREF/obs/{day:%Y}/{day:%j}/{filename}",
                            filename=filename,
                            compression=".gz",
                            data_type="obs",
                            station=station,
                            date=day,
                            metadata={
                                "logical_id": f"obs:{station}:{day}:{filename}",
                                "data_center": "BKG",
                            },
                        )
                    )
            return files

        async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
            return []

        async def search_products(self, request) -> list[RemoteFile]:
            return []

    provider = FakeBatchEPN()
    registry = ProviderRegistry()
    registry.register(provider)
    catalog_path = tmp_path / "stations_epn.sqlite"
    StationCatalog(catalog_path, seed_if_empty=False).upsert_many(
        [
            Station(
                id="BRUX00BEL",
                marker_name="Brussels",
                country="BEL",
                network=["EPN"],
                data_networks=["europe"],
                regional_sources=["europe_epn"],
                providers=["epn"],
            ),
            Station(
                id="ACOR00ESP",
                marker_name="A Coruna",
                country="ESP",
                network=["EPN"],
                data_networks=["europe"],
                regional_sources=["europe_epn"],
                providers=["epn"],
            ),
        ]
    )
    settings = load_settings(
        {
            "provider": {"priority": ["epn"]},
            "stations": {"catalog_path": catalog_path, "auto_seed": False},
        }
    )
    client = GNSSGo(settings=settings, registry=registry)
    plan = client.plan_observations(
        stations=["BRUX00BEL", "ACOR00ESP"],
        start="2026-08-01",
        end="2026-08-02",
        data_networks=["europe"],
        regional_sources=["europe_epn"],
        output=tmp_path,
    )

    assert len(provider.calls) == 1
    stations, start, end = provider.calls[0]
    assert stations == ["BRUX00BEL", "ACOR00ESP"]
    assert str(start) == "2026-08-01"
    assert str(end) == "2026-08-02"
    assert len(plan.remote_files) == 4
    assert not plan.unavailable
