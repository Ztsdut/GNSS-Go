from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from gnssgo import GNSSGo
from gnssgo.archive import ArchiveLayout
from gnssgo.data_networks import default_data_network_registry
from gnssgo.models import DateRange, DownloadPlan, ProductRequest, ProductType
from gnssgo.products import ProductNamingRegistry, ProductPresetRegistry, parse_product_filename
from gnssgo.stations import StationCatalog
from gnssgo.stations.catalog import seed_stations
from gnssgo.stations.snapshot import load_bundled_station_snapshot, snapshot_metadata


_EUROPE_CATALOG_VERSIONS = {
    "epn": 5,
    "rgp_fr": 7,
    "gref_de": 5,
    "redgae_es": 3,
    "nsgi_nl": 3,
    "apos_at": 2,
    "renep_pt": 5,
    "belgium_be": 2,
    "noa_gr": 2,
    "epos_it": 1,
    "epos_pl": 1,
    "epos_ro": 1,
    "epos_uk": 1,
    "epos_se": 1,
    "epos_fi": 1,
    "epos_ch": 1,
    "epos_hu": 1, "epos_cz": 1, "epos_si": 1, "epos_ie": 1, "epos_is": 1,
    "epos_hr": 1, "epos_no": 1, "epos_dk": 1, "epos_ee": 1, "epos_lv": 1,
    "epos_lt": 1, "epos_sk": 1, "epos_bg": 1, "epos_cy": 1, "epos_rs": 1,
    "epos_tr": 1, "epos_lu": 1, "epos_al": 1, "epos_ba": 1, "epos_mk": 1,
    "epos_md": 1, "epos_ua": 1, "epos_mt": 1, "epos_me": 1,
}


_SIRGAS_CATALOG_VERSIONS = {
    # Brazil uses the bundled full RBMC_2024.kmz, repairs stale user-level KMZ
    # mirrors, and builds the map catalog directly from the official placemarks.
    "ramsac_ar": 6,
    "sirgas_rbmc_br": 11,
    "sirgas_cl": 11,
    "rgna_mx": 9,
    "sirgas_bo": 6,
    "sirgas_co": 11,
    "sirgas_ec": 6,
    "sirgas_pe": 6,
    "sirgas_uy": 9,
    "sirgas_cr": 6,
    "sirgas_pa": 6,
}


_REGIONAL_CATALOG_VERSIONS = {
    # Local authoritative/curated catalogs added after earlier cache versions.
    # Bump these when the parser or bundled station catalog changes so existing
    # stations.sqlite files refresh automatically.
    "noaa_ncn": 2,
    "kasi_kr": 2,
    "ngii_kr": 2,
    "geonet_jp": 2,
    "gdms_tw": 1,
    "cmonoc_cn": 1,
    "geonet_nz": 2,
    "satref_hk": 1,
    "epn": 6,
}


class CoreService:
    def __init__(self, client: GNSSGo | None = None) -> None:
        self.client = client or GNSSGo()
        # Repair malformed SIRGAS coordinates from older parser/cache versions at
        # startup.  This removes Antarctica/Africa/Asia ghost points immediately
        # without asking the user to delete stations.sqlite by hand.
        try:
            self.client._station_catalog().sanitize_sirgas_coordinates()
        except Exception:
            # Catalog cleanup is defensive; GUI startup must never depend on it.
            pass

        # Load the release-time station-position snapshot before constructing any
        # GUI page.  This gives the map thousands of usable coordinates immediately
        # instead of exposing a nearly empty view while provider refreshes run.
        # Live provider catalogs still refresh silently in the background and are
        # merged into this local baseline; the snapshot never blocks newer metadata.
        try:
            snapshot_rows = load_bundled_station_snapshot()
            if snapshot_rows:
                self.client._station_catalog().upsert_many(
                    snapshot_rows,
                    source="bundled:station-position-snapshot",
                    source_type="bundled_snapshot",
                    metadata=snapshot_metadata(),
                )
        except Exception:
            # A damaged/missing optional snapshot must not prevent startup.  The
            # provider-specific seeds and live refresh logic below remain fallbacks.
            pass

        # The Observations page opens with IGS selected.  Older user caches can
        # contain many regional stations but no IGS rows (auto_seed used to be
        # disabled once the database was non-empty), leaving the default map blank.
        # Seed a tiny built-in IGS subset immediately; a background BKG refresh on
        # the Observations page replaces/extends it with the live IGS catalog.
        try:
            catalog = self.client._station_catalog()
            igs_rows = catalog.search(data_networks=["igs"])
            has_mapped_igs = any(
                station.latitude is not None and station.longitude is not None
                for station in igs_rows
            )
            if not has_mapped_igs:
                stations = seed_stations()
                catalog.upsert_many(
                    stations,
                    provider="builtin",
                    source="builtin:igs-startup-seed",
                    data_network="igs",
                    source_type="bundled_seed",
                    metadata={
                        "catalog_complete": False,
                        "station_count": len(stations),
                        "mapped_station_count": len(stations),
                        "startup_igs_seed": True,
                    },
                )
        except Exception:
            pass

        # Brazil RBMC is special: the official 2024 KMZ is bundled with the app
        # and mirrored to ~/.gnssgo/RBMC_2024.kmz.  Parse/upsert that local file
        # immediately on every GUI startup so the map never depends on an online
        # station-metadata request or on a stale MAP=34 SQLite cache.
        try:
            provider = self.client.registry.get("sirgas_rbmc_br")
            stations, source = provider._local_cartogram_station_catalog()
            if stations:
                for station in stations:
                    station.data_networks = sorted({*station.data_networks, "brazil", "sirgas"})
                    station.regional_sources = sorted(
                        {*station.regional_sources, "sirgas_brazil"}
                    )
                    station.providers = sorted({*station.providers, provider.name})
                    station.network = sorted({*station.network, "SIRGAS"})
                metadata = {
                    "catalog_complete": True,
                    "station_count": len(stations),
                    "mapped_station_count": len(stations),
                    "local_rbmc_kmz": True,
                    "local_rbmc_kmz_station_count": len(stations),
                    "catalog_source_used": source,
                    "sirgas_catalog_version": _SIRGAS_CATALOG_VERSIONS[provider.name],
                }
                self.client._station_catalog().upsert_many(
                    stations,
                    provider=provider.name,
                    source=source,
                    data_network="sirgas",
                    source_type=getattr(provider, "source_type", None),
                    metadata=metadata,
                )
        except Exception:
            # The packaged KMZ is an acceleration/reliability path; a damaged or
            # unwritable user directory must not prevent GNSS Go from starting.
            pass

        # Seed catalog-only / login-gated Asian networks from the bundled official
        # snapshots on every startup.  This makes their map layers available even
        # when the network is offline.  Taiwan is refreshed from GDMS in a GUI
        # background worker after startup; a failed refresh leaves this snapshot
        # untouched.
        for provider_name, network_id in (("gdms_tw", "taiwan"), ("cmonoc_cn", "china"), ("satref_hk", "hong_kong"), ("epn", "europe")):
            try:
                provider = self.client.registry.get(provider_name)
                stations = list(provider.bundled_station_catalog())
                if not stations:
                    continue
                metadata = {
                    "catalog_complete": True,
                    "station_count": len(stations),
                    "mapped_station_count": len(stations),
                    "bundled_startup_seed": True,
                    "regional_catalog_version": _REGIONAL_CATALOG_VERSIONS[provider_name],
                }
                self.client._station_catalog().upsert_many(
                    stations,
                    provider=provider_name,
                    source=getattr(provider, "station_catalog_source", provider_name),
                    data_network=network_id,
                    source_type=getattr(provider, "source_type", None),
                    metadata=metadata,
                )
            except Exception:
                pass

    def plan_observations(
        self,
        *,
        stations: list[str] | None = None,
        start: str,
        end: str,
        provider: str = "auto",
        sampling: str | None = "30s",
        rinex: str = "auto",
        station_file: str | Path | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        center: tuple[float, float] | None = None,
        radius: float | None = None,
        network: list[str] | None = None,
        data_networks: list[str] | None = None,
        regional_sources: list[str] | None = None,
        discover_available: bool = False,
        country: str | None = None,
        output: str | Path | None = None,
    ) -> DownloadPlan:
        return self.client.plan_observations(
            stations=stations or [],
            start=start,
            end=end,
            provider=provider,
            sampling=sampling,
            rinex=rinex,
            station_file=station_file,
            bbox=bbox,
            center=center,
            radius=radius,
            network=network,
            data_networks=data_networks,
            regional_sources=regional_sources,
            discover_available=discover_available,
            country=country,
            output=output,
        )

    def plan_navigation(
        self,
        *,
        start: str,
        end: str,
        nav_type: str = "mixed",
        provider: str = "auto",
        output: str | Path | None = None,
    ) -> DownloadPlan:
        return self.client.plan_navigation(
            start=start,
            end=end,
            nav_type=nav_type,
            provider=provider,
            output=output,
        )

    def plan_products(
        self,
        *,
        product_types: list[str],
        start: str,
        end: str,
        provider: str = "auto",
        center: str = "auto",
        tier: str = "auto",
        system: str = "auto",
        sampling: str | None = None,
        sampling_by_product: dict[str, str | None] | None = None,
        output: str | Path | None = None,
    ) -> DownloadPlan:
        if not sampling_by_product:
            return self.client.plan_products(
                product_types=product_types,
                start=start,
                end=end,
                provider=provider,
                center=center,
                tier=tier,
                system=system,
                sampling=sampling,
                output=output,
            )

        plans: list[DownloadPlan] = []
        for product_type in product_types:
            plans.append(
                self.client.plan_products(
                    product_types=[product_type],
                    start=start,
                    end=end,
                    provider=provider,
                    center=center,
                    tier=tier,
                    system=system,
                    sampling=sampling_by_product.get(product_type),
                    output=output,
                )
            )
        return _merge_product_plans(plans, provider=provider)

    def product_types_for_preset(self, preset: str) -> list[str]:
        return [item.value for item in ProductPresetRegistry().get(preset).product_types]

    def product_interval_options(
        self,
        *,
        product_type: str,
        day: str,
        center: str = "auto",
        tier: str = "auto",
        system: str = "auto",
    ) -> list[tuple[str, str]]:
        """Return temporal intervals supported by the product naming layer.

        The Products GUI exposes temporal resolution only. Product types and
        analysis centers can publish different intervals, so candidate filenames
        remain the source of truth and multiple valid temporal resolutions are
        returned as user-selectable options.
        """
        parsed_day = DateRange(start=day, end=day).start
        parsed_type = ProductType(product_type)
        if parsed_type == ProductType.ANTEX:
            return []
        centers = [center]
        if parsed_type == ProductType.IONEX and center.lower() == "auto":
            # The IGS combined GIM and IAAC products do not necessarily share
            # one temporal resolution.  Aggregate the modeled center-specific
            # intervals so the GUI can offer the user a real temporal choice.
            centers = ["IGS", "COD"]

        values: list[str] = []
        registry = ProductNamingRegistry()
        for candidate_center in centers:
            request = ProductRequest(
                date_range=DateRange(start=parsed_day, end=parsed_day),
                product_types=[parsed_type],
                center=candidate_center,
                tier=tier,
                system=system,
                sampling=None,
            )
            for filename in registry.candidates(parsed_day, parsed_type, request):
                descriptor = parse_product_filename(filename)
                sampling = descriptor.sampling if descriptor else None
                if sampling and sampling not in values:
                    values.append(sampling)
        return [(_format_product_interval(value), value) for value in values]


    def provider_catalog_needs_refresh(
        self,
        network_id: str,
        provider_name: str,
        *,
        max_age_days: int = 30,
        failure_retry_minutes: int = 10,
    ) -> bool:
        if network_id == "igs":
            return False
        provider = self.client.registry.get(provider_name)
        if not provider.capabilities().station_metadata:
            return False
        catalog = self.client._station_catalog()
        record = catalog.metadata_record(provider_name)
        if record is None:
            return True

        status = str(record.get("status") or "success").lower()
        if network_id == "europe":
            desired = _EUROPE_CATALOG_VERSIONS.get(provider_name)
            recorded_version = int(
                record.get("europe_catalog_version") or record.get("epn_catalog_version") or 0
            )
        elif network_id == "sirgas":
            desired = _SIRGAS_CATALOG_VERSIONS.get(provider_name)
            recorded_version = int(record.get("sirgas_catalog_version") or 0)
        else:
            desired = _REGIONAL_CATALOG_VERSIONS.get(provider_name)
            recorded_version = int(record.get("regional_catalog_version") or 0)
        # A parser/source upgrade should get one immediate retry even if the old
        # cache record was a recent failure.  Once the new code attempts it, the
        # failure record stores the new version and the normal cooldown applies.
        if desired is not None and recorded_version < desired:
            return True

        if status != "success":
            # Do not hammer a slow/broken national portal on every checkbox or
            # search keystroke.  Keep the warning visible and retry after a short
            # cooldown instead.
            stamp = str(record.get("updated_at") or "").strip()
            if stamp:
                try:
                    updated = datetime.fromisoformat(stamp)
                    if datetime.utcnow() - updated < timedelta(minutes=failure_retry_minutes):
                        return False
                except ValueError:
                    pass
            return True

        age = catalog.metadata_age_days(provider_name)
        if age is None or age > max_age_days:
            return True
        return False

    def network_catalog_needs_refresh(
        self,
        network_id: str,
        *,
        max_age_days: int = 30,
    ) -> bool:
        if network_id == "igs":
            return False
        network = default_data_network_registry().get(network_id)
        return any(
            self.provider_catalog_needs_refresh(
                network_id, provider_name, max_age_days=max_age_days
            )
            for provider_name in network.providers
        )

    def update_station_network(self, network_id: str) -> list[dict]:
        """Refresh all stale station-metadata providers for one data network."""
        network = default_data_network_registry().get(network_id)
        provider_names = [
            name
            for name in network.providers
            if self.client.registry.get(name).capabilities().station_metadata
            and self.provider_catalog_needs_refresh(network_id, name)
        ]
        return self._update_station_providers(network_id, provider_names)

    def update_station_provider(self, network_id: str, provider_name: str) -> list[dict]:
        """Refresh exactly one provider instead of the whole regional network.

        The Observations page uses this when a single national source such as
        Chile CSN, Mexico RGNA, or Uruguay IGM is selected.  Previously selecting
        any one SIRGAS country launched refreshes for every SIRGAS provider; on a
        slow national portal that could occupy the GUI's global worker pool and
        leave Review Plan apparently stuck in ``Planning``.
        """
        network = default_data_network_registry().get(network_id)
        if provider_name not in network.providers:
            raise ValueError(f"{provider_name} is not a provider of {network_id}")
        provider = self.client.registry.get(provider_name)
        if not provider.capabilities().station_metadata:
            return []
        if not self.provider_catalog_needs_refresh(network_id, provider_name):
            return []
        return self._update_station_providers(network_id, [provider_name])

    def force_update_station_provider(self, network_id: str, provider_name: str) -> list[dict]:
        """Refresh one station catalog regardless of cache age.

        Used for lightweight official catalogs that should be checked once per
        desktop session (currently Taiwan GDMS).  The provider itself retains a
        bundled/offline fallback, so a network failure never erases the map.
        """
        network = default_data_network_registry().get(network_id)
        if provider_name not in network.providers:
            raise ValueError(f"{provider_name} is not a provider of {network_id}")
        provider = self.client.registry.get(provider_name)
        if not provider.capabilities().station_metadata:
            return []
        return self._update_station_providers(network_id, [provider_name])

    def _update_station_providers(
        self, network_id: str, provider_names: list[str]
    ) -> list[dict]:
        """Fetch and persist a bounded set of station-metadata providers."""
        catalog = self.client._station_catalog()
        providers = [self.client.registry.get(name) for name in provider_names]

        async def fetch_all():
            semaphore = asyncio.Semaphore(4)

            async def fetch_one(provider):
                async with semaphore:
                    try:
                        return provider, await provider.fetch_station_catalog(), None
                    except Exception as exc:  # one provider must not abort the region
                        return provider, None, exc

            return await asyncio.gather(*(fetch_one(provider) for provider in providers))

        fetched = asyncio.run(fetch_all()) if providers else []
        results: list[dict] = []
        for provider, stations, error in fetched:
            source = (
                getattr(provider, "station_catalog_source", None)
                or getattr(provider, "portal_url", None)
                or provider.name
            )
            metadata = dict(getattr(provider, "last_station_catalog_stats", {}) or {})
            if network_id == "europe" and provider.name in _EUROPE_CATALOG_VERSIONS:
                metadata["europe_catalog_version"] = _EUROPE_CATALOG_VERSIONS[provider.name]
            elif network_id == "sirgas" and provider.name in _SIRGAS_CATALOG_VERSIONS:
                metadata["sirgas_catalog_version"] = _SIRGAS_CATALOG_VERSIONS[provider.name]
            if provider.name in _REGIONAL_CATALOG_VERSIONS:
                metadata["regional_catalog_version"] = _REGIONAL_CATALOG_VERSIONS[provider.name]
            if error is not None:
                catalog.record_metadata_status(
                    provider=provider.name,
                    source=source,
                    data_network=network_id,
                    source_type=getattr(provider, "source_type", None),
                    status="failed",
                    error=str(error),
                    metadata=metadata,
                )
                results.append({
                    "provider": provider.name,
                    "status": "failed",
                    "error": str(error),
                    "fetched": 0,
                })
                continue

            stations = list(stations or [])
            if not stations:
                message = "Station metadata source returned no stations."
                catalog.record_metadata_status(
                    provider=provider.name,
                    source=source,
                    data_network=network_id,
                    source_type=getattr(provider, "source_type", None),
                    status="empty",
                    error=message,
                    metadata={**metadata, "station_count": 0},
                )
                results.append({
                    "provider": provider.name,
                    "status": "empty",
                    "error": message,
                    "fetched": 0,
                })
                continue

            summary = catalog.upsert_many(
                stations,
                provider=provider.name,
                source=source,
                data_network=network_id,
                source_type=getattr(provider, "source_type", None),
                metadata=metadata,
            )
            results.append(summary.model_dump())
        return results

    def providers_for(self, capability: str, *, include_auto: bool = True) -> list[str]:
        providers = []
        for provider in self.client.registry.ordered(self.client.settings.provider.priority):
            caps = provider.capabilities()
            enabled = False
            if capability == "observations":
                enabled = caps.observations
            elif capability == "navigation":
                enabled = caps.navigation
            elif capability == "products":
                enabled = bool(caps.products)
            elif capability == "station_metadata":
                enabled = caps.station_metadata
            if enabled:
                providers.append(provider.name)
        return (["auto"] if include_auto else []) + providers

    def observation_sampling_options(
        self,
        *,
        provider: str = "auto",
        data_networks: list[str] | None = None,
        regional_sources: list[str] | None = None,
    ) -> list[tuple[str, str]]:
        if provider != "auto":
            self.client.registry.get(provider)

        # When one national Latin-America source is selected, expose that
        # source's native archive sampling instead of the broad SIRGAS union.
        source_sampling = {
            "sirgas_chile": {"01S"},
            "sirgas_mexico": {"15S", "30S"},
            "sirgas_uruguay": {"01S", "15S", "30S"},
            "sirgas_brazil": {"01S", "15S", "30S"},
        }
        selected_sources = {str(x).lower() for x in (regional_sources or [])}
        if len(selected_sources) == 1:
            source_id = next(iter(selected_sources))
            values = set(source_sampling.get(source_id, set()))
        else:
            values = set()

        if not values:
            registry = default_data_network_registry()
            selected = data_networks if data_networks is not None else ["igs"]
            for network_id in selected:
                values.update(registry.get(network_id).sampling)
        order = ["30S", "15S", "05S", "01S"]
        labels = {
            "30S": "30 s",
            "15S": "15 s",
            "05S": "5 s",
            "01S": "1 s",
        }
        normalized = {_normalize_sampling(value) for value in values}
        return [(labels[item], item.lower()) for item in order if item in normalized]

    def execute_plan(self, plan: DownloadPlan, **kwargs) -> list:
        return self.client.execute_plan(plan, **kwargs)

    def search_stations(
        self,
        *,
        query: str | None = None,
        network: list[str] | None = None,
        data_networks: list[str] | None = None,
        regional_sources: list[str] | None = None,
        continents: list[str] | None = None,
        country: str | None = None,
        provider: str | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        center: tuple[float, float] | None = None,
        radius: float | None = None,
    ):
        return self.client.search_stations(
            query=query,
            network=network,
            data_networks=data_networks,
            regional_sources=regional_sources,
            continents=continents,
            country=country,
            provider=provider,
            bbox=bbox,
            center=center,
            radius=radius,
        )

    def station_catalog_count(self) -> int:
        return len(
            StationCatalog(
                self.client.settings.stations.catalog_path,
                seed_if_empty=self.client.settings.stations.auto_seed,
            ).search()
        )

    def preview_destination(self, plan: DownloadPlan) -> str | None:
        if not plan.remote_files:
            return None
        root = plan.archive_root or self.client.settings.archive.root
        layout = ArchiveLayout(root, self.client.settings.archive.layout)
        return str(layout.destination_for(plan.remote_files[0]).parent)


def _merge_product_plans(plans: list[DownloadPlan], *, provider: str) -> DownloadPlan:
    if not plans:
        return DownloadPlan(provider_requested=provider)
    merged = DownloadPlan(
        provider_requested=provider,
        archive_root=plans[0].archive_root,
        task_id=plans[0].task_id,
    )
    seen_remote: set[str] = set()
    seen_task: set[str] = set()
    seen_existing: set[str] = set()
    for plan in plans:
        merged.requests.extend(plan.requests)
        merged.attempted_providers.extend(plan.attempted_providers)
        merged.unavailable.extend(
            item for item in plan.unavailable if item not in merged.unavailable
        )
        merged.missing.extend(item for item in plan.missing if item not in merged.missing)
        for remote in plan.remote_files:
            key = str(remote.url)
            if key not in seen_remote:
                seen_remote.add(key)
                merged.remote_files.append(remote)
        for task in plan.download_tasks:
            key = str(task.destination)
            if key not in seen_task:
                seen_task.add(key)
                merged.download_tasks.append(task)
        for local in plan.existing_files:
            key = str(local.path)
            if key not in seen_existing:
                seen_existing.add(key)
                merged.existing_files.append(local)
        for name, count in plan.provider_stats.items():
            merged.provider_stats[name] = merged.provider_stats.get(name, 0) + count
    sizes = [plan.estimated_size for plan in plans if plan.estimated_size]
    merged.estimated_size = sum(sizes) if sizes else None
    return merged


def _format_product_interval(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) >= 3 and normalized[:-1].isdigit():
        amount = int(normalized[:-1])
        unit = normalized[-1]
        labels = {"S": "s", "M": "min", "H": "h", "D": "day"}
        label = labels.get(unit)
        if label:
            if unit == "D" and amount != 1:
                label = "days"
            return f"{amount} {label}"
    return normalized


def _normalize_sampling(value: str) -> str:
    normalized = value.strip().upper().replace(" ", "")
    if normalized.endswith("S") and len(normalized) == 2:
        return f"0{normalized}"
    return normalized
