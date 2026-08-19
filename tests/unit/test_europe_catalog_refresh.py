from __future__ import annotations

from dataclasses import dataclass

from gnssgo.gui.services.core_service import CoreService
from gnssgo.models import Station
from gnssgo.providers.base import ProviderCapabilities
from gnssgo.stations import StationCatalog


@dataclass
class FakeProvider:
    name: str
    source_id: str
    station_id: str | None = None
    fail: bool = False
    source_type: str = "test"

    @property
    def station_catalog_source(self) -> str:
        return f"https://example.test/{self.name}"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(station_metadata=True)

    async def fetch_station_catalog(self):
        if self.fail:
            raise RuntimeError(f"{self.name} unavailable")
        if self.station_id is None:
            return []
        return [
            Station(
                id=self.station_id,
                latitude=50.0,
                longitude=8.0,
                data_networks=["europe"],
                regional_sources=[self.source_id],
                providers=[self.name],
            )
        ]


def _all_europe_fake_providers(*, rgp_fail: bool = False):
    from gnssgo.data_networks import default_data_network_registry
    from gnssgo.regional_sources import default_regional_source_registry

    sources = {s.provider: s.id for s in default_regional_source_registry().all("europe")}
    providers = []
    for index, name in enumerate(default_data_network_registry().get("europe").providers):
        source_id = sources[name]
        # Valid 9-char-ish ids are not required by this unit path; Station accepts arbitrary ids.
        station_id = f"T{index:03d}00EUR"
        providers.append(FakeProvider(name, source_id, station_id, fail=(name == "rgp_fr" and rgp_fail)))
    return providers


class FakeRegistry:
    def __init__(self, providers):
        self.providers = {provider.name: provider for provider in providers}

    def get(self, name: str):
        return self.providers[name]


class FakeClient:
    def __init__(self, catalog, providers):
        self._catalog_obj = catalog
        self.registry = FakeRegistry(providers)

    def _station_catalog(self):
        return self._catalog_obj


def test_europe_refresh_isolates_one_provider_failure(tmp_path):
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    providers = _all_europe_fake_providers(rgp_fail=True)
    core = CoreService(FakeClient(catalog, providers))

    results = core.update_station_network("europe")

    assert len(results) == len(providers)
    assert catalog.search(regional_sources=["europe_gref"])
    assert catalog.search(regional_sources=["europe_redgae"])
    assert catalog.search(regional_sources=["europe_nsgi"])
    assert catalog.search(regional_sources=["europe_apos"])
    assert catalog.search(regional_sources=["europe_renep"])
    assert catalog.metadata_record("rgp_fr")["status"] == "failed"
    assert "unavailable" in catalog.metadata_record("rgp_fr")["error"]


def test_failed_refresh_keeps_last_good_station_cache(tmp_path):
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    catalog.upsert_many(
        [
            Station(
                id="OLD100FRA",
                latitude=48.0,
                longitude=2.0,
                data_networks=["europe"],
                regional_sources=["europe_rgp"],
                providers=["rgp_fr"],
            )
        ],
        provider="rgp_fr",
        data_network="europe",
        metadata={"station_count": 1},
    )
    providers = _all_europe_fake_providers(rgp_fail=True)
    core = CoreService(FakeClient(catalog, providers))

    core.update_station_network("europe")

    assert [s.id for s in catalog.search(regional_sources=["europe_rgp"])] == ["OLD100FRA"]
    assert catalog.metadata_record("rgp_fr")["status"] == "failed"


def test_failed_europe_provider_has_retry_cooldown(tmp_path):
    catalog = StationCatalog(tmp_path / "stations.sqlite", seed_if_empty=False)
    providers = _all_europe_fake_providers(rgp_fail=True)
    core = CoreService(FakeClient(catalog, providers))
    core.update_station_network("europe")
    assert catalog.metadata_record("rgp_fr")["status"] == "failed"
    assert core.provider_catalog_needs_refresh("europe", "rgp_fr") is False
