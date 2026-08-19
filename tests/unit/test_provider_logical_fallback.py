from datetime import date

from gnssgo import GNSSGo
from gnssgo.config import load_settings
from gnssgo.models import NavigationRequest, ObservationRequest, RemoteFile
from gnssgo.providers.base import GNSSProvider, ProviderCapabilities
from gnssgo.providers.registry import ProviderRegistry


class PartialObservationProvider(GNSSProvider):
    def __init__(self, name: str, available: set[tuple[str, date]]) -> None:
        self.name = name
        self.available = available

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(observations=True, navigation=False)

    async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
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

    async def search_navigation(self, request: NavigationRequest) -> list[RemoteFile]:
        return []

    async def search_products(self, request) -> list[RemoteFile]:
        return []


def test_auto_observation_fallback_resolves_each_station_day(tmp_path) -> None:
    registry = ProviderRegistry()
    day1 = date(2026, 8, 1)
    day2 = date(2026, 8, 2)
    registry.register(PartialObservationProvider("first", {("WUH200CHN", day1)}))
    registry.register(
        PartialObservationProvider("second", {("WUH200CHN", day1), ("WUH200CHN", day2)})
    )
    settings = load_settings(
        {
            "provider": {"priority": ["first", "second"]},
            "stations": {"catalog_path": tmp_path / "stations.sqlite", "auto_seed": False},
        }
    )

    plan = GNSSGo(settings=settings, registry=registry).plan_observations(
        stations=["WUH200CHN"],
        start="2026-08-01",
        end="2026-08-02",
        provider="auto",
        rinex="3",
        output=tmp_path,
    )

    assert [(remote.date, remote.provider) for remote in plan.remote_files] == [
        (day1, "first"),
        (day2, "second"),
    ]
    # Planning stops after the first provider that satisfies each logical file.
    # Lower-priority mirrors are only consulted when the preferred provider has
    # no file, avoiding long GUI planning delays.
    assert plan.remote_files[0].fallback_candidates == []


def test_auto_planning_stops_after_first_provider_success(tmp_path) -> None:
    class CountingProvider(PartialObservationProvider):
        def __init__(self, name: str, available: set[tuple[str, date]]) -> None:
            super().__init__(name, available)
            self.calls = 0

        async def search_observations(self, request: ObservationRequest) -> list[RemoteFile]:
            self.calls += 1
            return await super().search_observations(request)

    day = date(2026, 1, 3)
    first = CountingProvider("cacs_ca", {("CRMR00CAN", day)})
    second = CountingProvider("chain_ca", {("CRMR00CAN", day)})
    global_mirror = CountingProvider("whu", {("CRMR00CAN", day)})
    registry = ProviderRegistry()
    registry.register(first)
    registry.register(second)
    registry.register(global_mirror)
    settings = load_settings(
        {
            "provider": {"priority": ["whu"]},
            "stations": {"catalog_path": tmp_path / "stations.sqlite", "auto_seed": False},
        }
    )

    # Use an explicit provider-priority override here because this lightweight test
    # registry does not install the full DataNetwork registry provider set.
    client = GNSSGo(settings=settings, registry=registry)
    client.data_network_provider_priority = lambda _ids: ["cacs_ca", "chain_ca", "whu"]
    plan = client.plan_observations(
        stations=["CRMR00CAN"],
        start="2026-01-03",
        end="2026-01-03",
        provider="auto",
        rinex="3",
        data_networks=["canada"],
        output=tmp_path,
    )

    assert len(plan.remote_files) == 1
    assert plan.remote_files[0].provider == "cacs_ca"
    assert first.calls == 1
    assert second.calls == 0
    assert global_mirror.calls == 0
