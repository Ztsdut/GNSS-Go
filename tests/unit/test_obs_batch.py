from gnssgo import GNSSGo
from gnssgo.config import load_settings
from gnssgo.models import RemoteFile
from gnssgo.providers.base import GNSSProvider, ProviderCapabilities
from gnssgo.providers.registry import ProviderRegistry
from gnssgo.stations import StationCatalog
from gnssgo.stations.catalog import seed_stations


class FakeObsProvider(GNSSProvider):
    name = "fake"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True)

    async def search_observations(self, request) -> list[RemoteFile]:
        return [
            RemoteFile(
                provider=self.name,
                url=f"https://example.test/{station}.crx.gz",
                filename=f"{station}.crx.gz",
                compression=".gz",
                data_type="obs",
                station=station,
                date=request.date_range.start,
                metadata={"logical_id": f"obs:{station}:{request.date_range.start}"},
            )
            for station in request.stations or []
        ]

    async def search_navigation(self, request) -> list[RemoteFile]:
        return []

    async def search_products(self, request) -> list[RemoteFile]:
        return []


def fake_client(tmp_path) -> GNSSGo:
    registry = ProviderRegistry()
    registry.register(FakeObsProvider())
    catalog_path = tmp_path / "stations.sqlite"
    StationCatalog(catalog_path, seed_if_empty=False).upsert_many(
        seed_stations(),
        provider="builtin",
        source="unit",
    )
    settings = load_settings({"stations": {"catalog_path": catalog_path, "auto_seed": False}})
    return GNSSGo(settings=settings, registry=registry)


def test_station_file_parsing_and_batch_plan(tmp_path) -> None:
    station_file = tmp_path / "stations.txt"
    station_file.write_text("WUH200CHN\n# comment\nBJFS00CHN\n\n", encoding="utf-8")

    plan = fake_client(tmp_path).plan_observations(
        stations=[],
        station_file=station_file,
        start="2026-08-01",
        end="2026-08-01",
        provider="fake",
        rinex="3",
        output=tmp_path,
    )

    assert plan.matched_stations == ["WUH200CHN", "BJFS00CHN"]
    assert {remote.station for remote in plan.remote_files} <= {"WUH200CHN", "BJFS00CHN"}


def test_bbox_observation_plan_uses_catalog(tmp_path) -> None:
    plan = fake_client(tmp_path).plan_observations(
        bbox=(128, 30, 146, 46),
        network=["igs"],
        start="2026-08-01",
        end="2026-08-01",
        provider="fake",
        rinex="3",
        output=tmp_path,
    )

    assert {"TSKB00JPN", "AIRA00JPN", "MIZU00JPN"}.issubset(set(plan.matched_stations))


def test_radius_observation_plan_uses_catalog(tmp_path) -> None:
    plan = fake_client(tmp_path).plan_observations(
        center=(35.68, 139.76),
        radius=500,
        start="2026-08-01",
        end="2026-08-01",
        provider="fake",
        rinex="3",
        output=tmp_path,
    )

    assert "TSKB00JPN" in plan.matched_stations
