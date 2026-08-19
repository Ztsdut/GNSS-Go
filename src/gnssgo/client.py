from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from gnssgo.archive import ArchiveLayout, Manifest
from gnssgo.config import Settings, load_settings
from gnssgo.data_networks import default_data_network_registry
from gnssgo.download import DownloadManager, make_task
from gnssgo.download.events import CancellationToken, EventCallback
from gnssgo.exceptions import ConfigurationError, GNSSGoError, PostProcessError
from gnssgo.models import (
    BatchDownloadResult,
    DateRange,
    DownloadPlan,
    LocalFile,
    NavigationRequest,
    ObservationRequest,
    ProductRequest,
    ProductTier,
    ProductType,
    ProviderAttempt,
    RemoteFile,
)
from gnssgo.products import ProductResolver, validate_product_file
from gnssgo.providers import ProviderRegistry, default_registry
from gnssgo.regional_sources import default_regional_source_registry
from gnssgo.rinex.detect import detect_compression, is_compact_rinex
from gnssgo.rinex.postprocess import PostProcessor
from gnssgo.stations import StationCatalog
from gnssgo.utils.checksum import file_checksum


class GNSSGo:
    def __init__(
        self,
        settings: Settings | None = None,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.registry = registry or default_registry()
        # Providers normally own their discovery clients.  Give regional
        # providers read-only access to the effective network settings so
        # national day-directory discovery (notably Chile CSN) can use the same
        # proxy route as the downloader instead of silently bypassing it.
        for provider_name in self.registry.names():
            try:
                setattr(self.registry.get(provider_name), "_network_settings", self.settings.network)
            except Exception:
                pass
        self.layout = ArchiveLayout(self.settings.archive.root, self.settings.archive.layout)
        self.product_resolver = ProductResolver(
            center_priority=self.settings.products.center_priority,
            multi_gnss_center_priority=self.settings.products.multi_gnss_center_priority,
            allow_mixed_center=self.settings.products.allow_mixed_center,
        )

    async def plan_observations_async(
        self,
        stations: list[str] | None = None,
        start: str = "",
        end: str = "",
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
        overwrite: bool | None = None,
        keep_compressed: bool | None = None,
    ) -> DownloadPlan:
        data_networks, regional_sources = _resolve_regional_source_filters(
            data_networks,
            regional_sources,
        )
        # Full-provider discovery intentionally bypasses the local station catalogue.
        # For RBMC this lets the official IBGE YYYY/DOY directory define what is
        # actually downloadable on that date instead of limiting the plan to the
        # currently selected/mapped IGS subset.  Explicit station/spatial inputs
        # always win and disable this mode.
        use_provider_discovery = bool(
            discover_available
            and not (stations or station_file or bbox or center or network or country)
        )
        if use_provider_discovery:
            station_codes, matched = [], []
        else:
            station_codes, matched = self._resolve_observation_stations(
                stations=stations or [],
                station_file=station_file,
                bbox=bbox,
                center=center,
                radius=radius,
                network=network,
                data_networks=data_networks,
                regional_sources=regional_sources,
                country=country,
            )
        request = ObservationRequest(
            stations=station_codes,
            date_range=DateRange(start=start, end=end),
            provider=provider,
            sampling=sampling,
            rinex=rinex,
            network=network,
            data_networks=data_networks,
            regional_sources=regional_sources,
            discover_available=use_provider_discovery,
        )
        plan = await self._build_plan(
            request=request,
            provider=provider,
            search=lambda p: p.search_observations(request),
            output=output,
            overwrite=overwrite,
            keep_compressed=keep_compressed,
            per_station_auto=True,
        )
        if use_provider_discovery:
            plan.matched_stations = sorted(
                {remote.station for remote in plan.remote_files if remote.station}
            )
        else:
            plan.matched_stations = [station.id for station in matched] or station_codes
        return plan

    def plan_observations(self, *args, **kwargs) -> DownloadPlan:
        return asyncio.run(self.plan_observations_async(*args, **kwargs))

    async def plan_navigation_async(
        self,
        start: str,
        end: str,
        nav_type: str = "mixed",
        provider: str = "auto",
        output: str | Path | None = None,
        overwrite: bool | None = None,
        keep_compressed: bool | None = None,
    ) -> DownloadPlan:
        request = NavigationRequest(
            date_range=DateRange(start=start, end=end),
            provider=provider,
            nav_type=nav_type,
        )
        return await self._build_plan(
            request=request,
            provider=provider,
            search=lambda p: p.search_navigation(request),
            output=output,
            overwrite=overwrite,
            keep_compressed=keep_compressed,
        )

    def plan_navigation(self, *args, **kwargs) -> DownloadPlan:
        return asyncio.run(self.plan_navigation_async(*args, **kwargs))

    async def plan_products_async(
        self,
        product_types: list[str],
        start: str,
        end: str,
        provider: str = "auto",
        center: str = "auto",
        tier: str = "auto",
        system: str = "auto",
        sampling: str | None = None,
        output: str | Path | None = None,
        overwrite: bool | None = None,
        keep_compressed: bool | None = None,
    ) -> DownloadPlan:
        request = ProductRequest(
            date_range=DateRange(start=start, end=end),
            provider=provider,
            product_types=[ProductType(item) for item in product_types],
            center=center,
            tier=tier,
            system=system,
            sampling=sampling,
        )
        return await self._build_plan(
            request=request,
            provider=provider,
            search=lambda p: p.search_products(request),
            output=output,
            overwrite=overwrite,
            keep_compressed=keep_compressed,
        )

    def plan_products(self, *args, **kwargs) -> DownloadPlan:
        return asyncio.run(self.plan_products_async(*args, **kwargs))

    async def execute_plan_async(
        self,
        plan: DownloadPlan,
        *,
        event_callback: EventCallback | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> list:
        manager = DownloadManager(
            workers=self.settings.download.workers,
            per_provider_workers=self.settings.download.per_provider_workers,
            retries=self.settings.download.retries,
            connect_timeout=self.settings.download.connect_timeout,
            read_timeout=self.settings.download.read_timeout,
            resume=self.settings.download.resume,
            proxy=self.settings.network.proxy,
            network_settings=self.settings.network,
            postprocessor=self._postprocess_download,
            event_callback=event_callback,
            cancellation_token=cancellation_token,
            task_id=plan.task_id,
        )
        manifest_root = plan.archive_root or Path(self.settings.archive.root)
        manifest = Manifest(Path(manifest_root) / "manifest.jsonl")
        try:
            results = await manager.run(plan.download_tasks)
            for result in results:
                manifest.append_result(result, plan=plan)
            return results
        finally:
            await manager.close()

    def execute_plan(self, plan: DownloadPlan, **kwargs) -> list:
        return asyncio.run(self.execute_plan_async(plan, **kwargs))

    def summarize_results(self, results: list) -> BatchDownloadResult:
        summary = BatchDownloadResult(total=len(results))
        for item in results:
            remote = item.task.remote
            summary.provider_stats[remote.provider] = (
                summary.provider_stats.get(remote.provider, 0) + 1
            )
            if item.status == "failed":
                summary.failed += 1
            elif item.status == "cancelled":
                summary.cancelled += 1
            elif item.status == "skipped":
                summary.skipped += 1
            else:
                summary.downloaded += 1
            if item.local_file and item.local_file.size:
                summary.bytes_downloaded += item.local_file.size
        return summary

    def download_observations(self, stations: list[str], start: str, end: str, **kwargs) -> list:
        plan = self.plan_observations(stations, start, end, **kwargs)
        return self.execute_plan(plan)

    def search_stations(
        self,
        query: str | None = None,
        network: list[str] | None = None,
        data_networks: list[str] | None = None,
        country: str | None = None,
        provider: str | None = None,
        regional_sources: list[str] | None = None,
        continents: list[str] | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        center: tuple[float, float] | None = None,
        radius: float | None = None,
    ):
        data_networks, regional_sources = _resolve_regional_source_filters(
            data_networks,
            regional_sources,
        )
        catalog = self._station_catalog()
        if bbox:
            return catalog.search_bbox(
                *bbox,
                network=network,
                data_networks=data_networks,
                regional_sources=regional_sources,
                country=country,
                provider=provider,
                continents=continents,
            )
        if center and radius is not None:
            return catalog.search_radius(
                center[0],
                center[1],
                radius,
                network=network,
                data_networks=data_networks,
                regional_sources=regional_sources,
                country=country,
                provider=provider,
                continents=continents,
            )
        return catalog.search(
            query=query,
            network=network,
            data_networks=data_networks,
            regional_sources=regional_sources,
            country=country,
            provider=provider,
            continents=continents,
        )

    def _postprocess_download(self, task, path: Path) -> LocalFile:
        expected_type = _expected_rinex_type(task.remote.data_type)

        # Terras bulk download can contain several stations/days.  Keep the
        # provider-created ZIP intact; the normal observation postprocessor is
        # intentionally single-RINEX-file oriented and would otherwise retain
        # only one member of the bundle.
        if task.remote.metadata.get("multi_station_bundle") == "1":
            return LocalFile(
                path=path,
                size=path.stat().st_size if path.exists() else None,
                checksum=file_checksum(path) if path.exists() else None,
                status="validated",
                remote=task.remote,
                processed_path=path,
                processed_at=datetime.utcnow(),
            )

        # By default GNSS Go keeps the provider archive exactly as downloaded.
        # The transport layer has already validated HTTP/content-length/checksum
        # before this hook is called, so compressed archives do not need to be
        # unpacked merely to be considered a successful download.
        if not task.decompress and (detect_compression(path) or is_compact_rinex(path)):
            return LocalFile(
                path=path,
                size=path.stat().st_size if path.exists() else None,
                checksum=file_checksum(path) if path.exists() else None,
                status="validated",
                remote=task.remote,
                processed_path=path,
                processed_at=datetime.utcnow(),
            )

        try:
            result = PostProcessor().process(
                path,
                keep_compressed=task.keep_compressed,
                expected_rinex_type=expected_type,
                validate_rinex=expected_type is not None,
            )
        except PostProcessError as exc:
            # Download success must not be reported as a transfer failure merely
            # because an optional decompression backend (unlzw3 / hatanaka) is
            # absent in the user's environment.  Keep the verified provider
            # archive and surface the reason as metadata instead.
            if "requires optional dependency" not in str(exc).lower():
                raise
            task.remote.metadata["postprocess_warning"] = str(exc)
            return LocalFile(
                path=path,
                size=path.stat().st_size if path.exists() else None,
                checksum=file_checksum(path) if path.exists() else None,
                status="validated",
                remote=task.remote,
                processed_path=path,
                processed_at=datetime.utcnow(),
            )
        rinex = result.rinex
        local = LocalFile(
            path=result.output_path,
            size=result.output_path.stat().st_size if result.output_path.exists() else None,
            checksum=file_checksum(result.output_path) if result.output_path.exists() else None,
            status=result.status,
            remote=task.remote,
            processed_path=result.output_path,
            processed_at=datetime.utcnow(),
            rinex_version=rinex.version if rinex else None,
            rinex_type=rinex.rinex_type if rinex else None,
        )
        if expected_type is None and task.remote.data_type in {item.value for item in ProductType}:
            validation = validate_product_file(local.path, task.remote.data_type)
            task.remote.metadata.update(
                {
                    "validation_valid": str(validation.valid),
                    "validation_error": validation.error or "",
                    **{
                        f"validation_{key}": value
                        for key, value in validation.metadata.items()
                    },
                }
            )
            if not validation.valid:
                raise GNSSGoError(validation.error or "Product validation failed.")
            local.status = "validated"
        return local

    async def _build_plan(
        self,
        request: object,
        provider: str,
        search: Callable[[object], Awaitable[list[RemoteFile]]],
        output: str | Path | None,
        overwrite: bool | None,
        keep_compressed: bool | None,
        per_station_auto: bool = False,
    ) -> DownloadPlan:
        root = Path(output) if output else self.settings.archive.root
        layout = ArchiveLayout(root, self.settings.archive.layout)
        if (
            provider == "auto"
            and isinstance(request, ObservationRequest | NavigationRequest)
        ):
            remotes, attempts = await self._search_logical_files(request)
        elif isinstance(request, ProductRequest):
            remotes, attempts = await self._search_products_with_provider(request, provider)
        else:
            remotes, attempts = await self._search_with_provider(provider, search)
        remote_files = _deduplicate_logical_files(remotes)
        if isinstance(request, ProductRequest):
            resolution = self.product_resolver.select_bundle(request, remote_files)
            selected_urls = {item.url for item in resolution.selected if item.url}
            remote_files = [remote for remote in remote_files if str(remote.url) in selected_urls]
            for remote in remote_files:
                remote.metadata["resolution_trace"] = " | ".join(resolution.trace[-20:])
                remote.metadata["resolution_warnings"] = " | ".join(resolution.warnings)
        if isinstance(request, ObservationRequest):
            self._annotate_observation_sources(remote_files)
        plan = DownloadPlan(
            requests=[request],
            remote_files=remote_files,
            provider_requested=provider,
            attempted_providers=attempts,
            archive_root=root,
            task_id=str(uuid4()),
        )
        if isinstance(request, ObservationRequest):
            missing = _observation_missing_entries(request, remote_files)
            plan.unavailable = missing
            plan.missing = list(missing)
        if isinstance(request, ProductRequest):
            plan.unavailable = resolution.unavailable
            plan.missing = list(resolution.unavailable)
        plan.estimated_size = sum(remote.size or 0 for remote in remote_files) or None
        for remote in plan.remote_files:
            plan.provider_stats[remote.provider] = plan.provider_stats.get(remote.provider, 0) + 1
        use_overwrite = self.settings.download.overwrite if overwrite is None else overwrite
        use_keep = (
            self.settings.archive.keep_compressed
            if keep_compressed is None
            else keep_compressed
        )
        use_decompress = self.settings.archive.auto_extract
        for remote in plan.remote_files:
            destination = layout.destination_for(remote)
            # In compressed-output mode the archive itself is the canonical local
            # file.  In extraction mode the restored/decompressed RINEX is canonical.
            existing = _existing_processed_path(destination) if use_decompress else destination
            if existing.exists() and not use_overwrite:
                plan.existing_files.append(
                    LocalFile(
                        path=existing,
                        size=existing.stat().st_size,
                        status="skipped",
                        remote=remote,
                    )
                )
            else:
                plan.download_tasks.append(
                    make_task(
                        remote,
                        destination,
                        use_overwrite,
                        use_keep,
                        decompress=use_decompress,
                    )
                )
        return plan

    async def _search_logical_files(
        self,
        request: ObservationRequest | NavigationRequest,
    ) -> tuple[list[RemoteFile], list[ProviderAttempt]]:
        semaphore = asyncio.Semaphore(self.settings.download.workers)
        provider_semaphores = {
            provider.name: asyncio.Semaphore(self.settings.download.per_provider_workers)
            for provider in self.registry.ordered(self.settings.provider.priority)
        }

        if isinstance(request, ObservationRequest) and request.discover_available:
            return await self._resolve_observation_network_discovery(
                request, semaphore, provider_semaphores
            )

        observation_routes = (
            self._observation_provider_routes(request)
            if isinstance(request, ObservationRequest)
            else {}
        )
        files: list[RemoteFile] = []
        attempts: list[ProviderAttempt] = []

        # Some regional providers can resolve all selected stations/days in one
        # call.  This is important for directory/SFTP-backed archives: expanding
        # station x day would repeat the same remote listing or SSH handshake many
        # times and makes the GUI appear stuck in PLAN.  Passthrough providers may
        # also legitimately return multiple files for one station/day (Mexico RGNA
        # hourly ZIPs), so their results must not be collapsed to one candidate.
        passthrough_groups: dict[str, list[str]] = {}
        epn_stations: list[str] = []
        if isinstance(request, ObservationRequest):
            registered = set(self.registry.names())
            for station in request.stations or []:
                route = observation_routes.get(station.upper())
                if not route or len(route[0]) != 1:
                    continue
                provider_name = route[0][0]
                if provider_name == "epn":
                    epn_stations.append(station)
                    continue
                if provider_name not in registered:
                    continue
                provider_obj = self.registry.get(provider_name)
                if getattr(provider_obj, "batch_observation_passthrough", False):
                    passthrough_groups.setdefault(provider_name, []).append(station)

        for provider_name, batch_stations in passthrough_groups.items():
            batch_request = request.model_copy(update={"stations": batch_stations})
            batch_files, batch_attempts = await self._resolve_observation_passthrough_batch(
                batch_request,
                provider_name,
                semaphore,
                provider_semaphores,
            )
            files.extend(batch_files)
            attempts.extend(batch_attempts)

        # EPN is special: ROB can resolve a whole station/date range in one
        # provider call, but keeps the traditional one-file-per-station/day
        # semantics after discovery.
        if epn_stations:
            epn_request = request.model_copy(update={"stations": epn_stations})
            batch_files, batch_attempts = await self._resolve_epn_batch(
                epn_request, semaphore, provider_semaphores
            )
            files.extend(batch_files)
            attempts.extend(batch_attempts)

        items = _expand_logical_requests(request)
        batched_ids = {
            station.upper()
            for stations in passthrough_groups.values()
            for station in stations
        }
        batched_ids.update(station.upper() for station in epn_stations)
        if batched_ids:
            items = [
                item
                for item in items
                if not (
                    isinstance(item, ObservationRequest)
                    and (item.stations or [""])[0].upper() in batched_ids
                )
            ]

        results = await asyncio.gather(
            *[
                self._resolve_logical_file(
                    item,
                    semaphore,
                    provider_semaphores,
                    provider_route=(
                        observation_routes.get((item.stations or [""])[0].upper())
                        if isinstance(item, ObservationRequest)
                        else None
                    ),
                )
                for item in items
            ]
        )
        for selected, item_attempts, missing_key in results:
            attempts.extend(item_attempts)
            if selected:
                files.append(selected)
            elif missing_key:
                key = ProviderAttempt(provider="auto", status="missing", message=missing_key)
                attempts.append(key)
        return files, attempts

    async def _resolve_observation_network_discovery(
        self,
        request: ObservationRequest,
        global_semaphore: asyncio.Semaphore,
        provider_semaphores: dict[str, asyncio.Semaphore],
    ) -> tuple[list[RemoteFile], list[ProviderAttempt]]:
        """Discover a whole regional day directory in one provider call.

        This is deliberately provider-owned discovery.  It is required for RBMC:
        the IBGE RINEX3 day directory often contains many more downloadable RBMC
        stations than the IGS-derived/map selection that happened to be active in
        the GUI.  Directory discovery also avoids issuing one identical directory
        listing request per catalogue station.
        """
        source_registry = default_regional_source_registry()
        network_registry = default_data_network_registry()
        provider_names: list[str] = []

        for source_id in request.regional_sources or []:
            try:
                provider_name = source_registry.get(source_id).provider
            except ConfigurationError:
                continue
            if provider_name not in provider_names:
                provider_names.append(provider_name)

        if not provider_names:
            for provider_name in network_registry.providers_for(request.data_networks or []):
                if provider_name not in provider_names:
                    provider_names.append(provider_name)

        registered = set(self.registry.names())
        candidates = []
        for name in provider_names:
            if name not in registered:
                continue
            provider = self.registry.get(name)
            if getattr(provider, "network_directory_discovery", False):
                candidates.append(provider)

        if len(candidates) != 1:
            scope = ",".join(request.regional_sources or request.data_networks or [])
            message = (
                f"provider-discovery:{scope}: expected one directory-backed provider, "
                f"found {len(candidates)}"
            )
            return [], [ProviderAttempt(provider="auto", status="failed", message=message)]

        provider = candidates[0]
        provider_semaphore = provider_semaphores.setdefault(
            provider.name, asyncio.Semaphore(self.settings.download.per_provider_workers)
        )
        scope = ",".join(request.regional_sources or request.data_networks or [])
        try:
            async with global_semaphore, provider_semaphore:
                files = await provider.search_observations(
                    request.model_copy(update={"stations": []})
                )
        except (GNSSGoError, OSError) as exc:
            return [], [
                ProviderAttempt(
                    provider=provider.name,
                    status="failed",
                    message=f"provider-discovery:{scope}: {exc}",
                )
            ]

        for remote in files:
            remote.metadata["provider_route"] = "regional_directory_discovery"
            remote.metadata["provider_route_candidates"] = provider.name
            remote.metadata["attempted_providers"] = provider.name
            remote.metadata["availability_scope"] = "official_day_directory"

        status = "success" if files else "not_found"
        return files, [
            ProviderAttempt(
                provider=provider.name,
                status=status,
                message=f"provider-discovery:{scope}:{len(files)} files",
            )
        ]

    async def _resolve_observation_passthrough_batch(
        self,
        request: ObservationRequest,
        provider_name: str,
        global_semaphore: asyncio.Semaphore,
        provider_semaphores: dict[str, asyncio.Semaphore],
    ) -> tuple[list[RemoteFile], list[ProviderAttempt]]:
        """Resolve a regional observation batch without collapsing its files.

        Providers opting into ``batch_observation_passthrough`` own the internal
        station/day traversal.  The core therefore makes one provider call and
        preserves every returned RemoteFile (for example 24 hourly RGNA ZIPs).
        """
        provider = self.registry.get(provider_name)
        provider_semaphore = provider_semaphores.setdefault(
            provider_name,
            asyncio.Semaphore(self.settings.download.per_provider_workers),
        )
        batch_label = (
            f"obs-batch:{provider_name}:{len(request.stations or [])}stations:"
            f"{request.date_range.start.isoformat()}..{request.date_range.end.isoformat()}"
        )
        try:
            async with global_semaphore, provider_semaphore:
                discovered = await provider.search_observations(request)
        except Exception as exc:
            # Planning errors must be represented in the plan instead of escaping
            # the worker thread and terminating the Qt application.
            return [], [
                ProviderAttempt(
                    provider=provider_name,
                    status="failed",
                    message=f"{batch_label}: {type(exc).__name__}: {exc}",
                )
            ]

        for remote in discovered:
            station = (remote.station or "").upper()
            day = remote.date
            logical_key = (
                f"obs:{station}:{day.isoformat()}:{request.sampling or ''}:{request.rinex.value}"
                if station and day is not None
                else batch_label
            )
            remote.metadata["logical_request_key"] = logical_key
            remote.metadata["provider_route"] = "regional_passthrough_batch"
            remote.metadata["provider_route_candidates"] = provider_name
            remote.metadata["attempted_providers"] = provider_name

        status = "success" if discovered else "not_found"
        return discovered, [
            ProviderAttempt(
                provider=provider_name,
                status=status,
                message=f"{batch_label}:{len(discovered)} files",
            )
        ]

    async def _resolve_epn_batch(
        self,
        request: ObservationRequest,
        global_semaphore: asyncio.Semaphore,
        provider_semaphores: dict[str, asyncio.Semaphore],
    ) -> tuple[list[RemoteFile], list[ProviderAttempt]]:
        provider = self.registry.get("epn")
        provider_semaphore = provider_semaphores.setdefault(
            "epn", asyncio.Semaphore(self.settings.download.per_provider_workers)
        )
        batch_label = (
            f"obs-batch:epn:{len(request.stations or [])}stations:"
            f"{request.date_range.start.isoformat()}..{request.date_range.end.isoformat()}"
        )
        try:
            async with global_semaphore, provider_semaphore:
                discovered = await provider.search_observations(request)
        except (GNSSGoError, OSError) as exc:
            return [], [
                ProviderAttempt(provider="epn", status="failed", message=f"{batch_label}: {exc}")
            ]

        # A batched provider may return multiple RINEX variants.  Preserve the
        # same semantics as the generic resolver by selecting one logical file
        # for each station/day while keeping its provider-internal mirror chain.
        grouped: dict[tuple[str, object], list[RemoteFile]] = {}
        for remote in discovered:
            if not remote.station or remote.date is None:
                continue
            grouped.setdefault((remote.station.upper(), remote.date), []).append(remote)

        selected_files: list[RemoteFile] = []
        for (station, day), candidates in grouped.items():
            selected = _preferred_logical_candidate(candidates)
            selected.metadata["logical_request_key"] = (
                f"obs:{station}:{day.isoformat()}:{request.sampling or ''}:{request.rinex.value}"
            )
            selected.metadata["provider_route"] = "regional_station_provider"
            selected.metadata["provider_route_candidates"] = "epn"
            selected.metadata["attempted_providers"] = "epn"
            selected_files.append(selected)

        status = "success" if selected_files else "not_found"
        return selected_files, [ProviderAttempt(provider="epn", status=status, message=batch_label)]

    async def _resolve_logical_file(
        self,
        request: ObservationRequest | NavigationRequest,
        global_semaphore: asyncio.Semaphore,
        provider_semaphores: dict[str, asyncio.Semaphore],
        provider_route: tuple[list[str], str] | None = None,
    ) -> tuple[RemoteFile | None, list[ProviderAttempt], str | None]:
        attempts: list[ProviderAttempt] = []
        selected: RemoteFile | None = None
        logical_key = _logical_request_key(request)
        if isinstance(request, ObservationRequest):
            priority, route_mode = provider_route or self._observation_provider_route(request)
        else:
            priority = self.settings.provider.priority
            route_mode = "navigation_mirrors"
        if isinstance(request, ObservationRequest):
            # Observation routing is intentional.  Do not let ProviderRegistry.ordered()
            # append every unlisted provider, otherwise a region-owned station would
            # still fall through into unrelated sources and make Planning slow again.
            registered = set(self.registry.names())
            candidates = [
                self.registry.get(name)
                for name in priority
                if name.lower() in registered
            ]
        else:
            candidates = self.registry.ordered(priority)

        for candidate in candidates:
            if (
                isinstance(request, ObservationRequest)
                and not candidate.capabilities().observations
            ):
                continue
            if isinstance(request, NavigationRequest) and not candidate.capabilities().navigation:
                continue
            try:
                provider_semaphore = provider_semaphores.setdefault(
                    candidate.name,
                    asyncio.Semaphore(self.settings.download.per_provider_workers),
                )
                async with global_semaphore, provider_semaphore:
                    files = (
                        await candidate.search_observations(request)
                        if isinstance(request, ObservationRequest)
                        else await candidate.search_navigation(request)
                    )
            except (GNSSGoError, OSError) as exc:
                attempts.append(
                    ProviderAttempt(
                        provider=candidate.name,
                        status="failed",
                        message=f"{logical_key}: {exc}",
                    )
                )
                continue
            if files:
                # Planning must stop at the first provider that can satisfy this
                # station/day request.  The previous implementation continued
                # probing every lower-priority regional and global mirror even
                # after (for example) CACS had already found the requested file.
                # With several selected stations this made the GUI appear stuck
                # in "Planning..." while it waited for unrelated mirrors to
                # timeout.  Provider-internal variants are already carried by
                # ``fallback_candidates`` (for example CACS MO -> legacy d), so
                # they are preserved without scanning the rest of the registry.
                selected = _preferred_logical_candidate(files)
                selected.metadata["logical_request_key"] = logical_key
                selected.metadata["provider_route"] = route_mode
                selected.metadata["provider_route_candidates"] = ",".join(priority)
                attempts.append(
                    ProviderAttempt(
                        provider=candidate.name,
                        status="success",
                        message=logical_key,
                    )
                )
                break
            attempts.append(
                ProviderAttempt(
                    provider=candidate.name,
                    status="not_found",
                    message=logical_key,
                )
            )
        if selected:
            selected.metadata["attempted_providers"] = ",".join(
                attempt.provider for attempt in attempts
            )
            return selected, attempts, None
        return None, attempts, logical_key

    async def _search_products_with_provider(
        self,
        request: ProductRequest,
        provider: str,
    ) -> tuple[list[RemoteFile], list[ProviderAttempt]]:
        if provider != "auto":
            return await self._search_with_provider(
                provider,
                lambda p: p.search_products(request),
            )

        attempts: list[ProviderAttempt] = []
        collected: list[RemoteFile] = []
        for tier in self.product_resolver.tier_order(request):
            tier_request = request.model_copy(update={"tier": tier})
            tier_files: list[RemoteFile] = []
            for candidate in self.registry.ordered(self.settings.products.provider_priority):
                try:
                    files = await candidate.search_products(tier_request)
                except (GNSSGoError, OSError) as exc:
                    attempts.append(
                        ProviderAttempt(
                            provider=candidate.name,
                            status=f"failed_{tier.value}",
                            message=str(exc),
                        )
                    )
                    continue
                if files:
                    tier_files.extend(files)
                    attempts.append(
                        ProviderAttempt(
                            provider=candidate.name,
                            status=f"found_{tier.value}",
                        )
                    )
                else:
                    attempts.append(
                        ProviderAttempt(
                            provider=candidate.name,
                            status=f"not_found_{tier.value}",
                        )
                    )
            tier_files = _deduplicate_logical_files(tier_files)
            collected.extend(tier_files)
            resolution = self.product_resolver.select_bundle(tier_request, tier_files)
            if len(resolution.selected) == len(tier_request.product_types):
                return tier_files, attempts
            if request.tier != ProductTier.AUTO:
                return tier_files, attempts
        return _deduplicate_logical_files(collected), attempts

    def _resolve_observation_stations(
        self,
        stations: list[str],
        station_file: str | Path | None,
        bbox: tuple[float, float, float, float] | None,
        center: tuple[float, float] | None,
        radius: float | None,
        network: list[str] | None,
        data_networks: list[str] | None,
        regional_sources: list[str] | None,
        country: str | None,
    ) -> tuple[list[str], list]:
        explicit = [item.upper() for item in stations]
        if station_file:
            explicit.extend(_read_station_file(Path(station_file)))

        catalog = self._station_catalog()
        matched = catalog.search(
            network=network,
            data_networks=data_networks,
            regional_sources=regional_sources,
            country=country,
        )
        if bbox:
            matched = catalog.search_bbox(
                *bbox,
                network=network,
                data_networks=data_networks,
                regional_sources=regional_sources,
                country=country,
            )
        if center and radius is not None:
            matched = catalog.search_radius(
                center[0],
                center[1],
                radius,
                network=network,
                data_networks=data_networks,
                regional_sources=regional_sources,
                country=country,
            )
        if explicit:
            resolved = [catalog.get(code) for code in explicit]
            explicit_stations = [station for station in resolved if station is not None]
            missing = [
                code
                for code, station in zip(explicit, resolved, strict=False)
                if station is None
            ]
            if (
                bbox
                or center
                or network is not None
                or data_networks is not None
                or regional_sources is not None
                or country
            ):
                matched_ids = {station.id for station in matched}
                explicit_stations = [
                    station for station in explicit_stations if station.id in matched_ids
                ]
            station_codes = [station.id for station in explicit_stations]
            station_codes.extend(missing)
            return _unique_station_codes(station_codes), explicit_stations
        return _unique_station_codes([station.id for station in matched]), matched

    def _station_catalog(self) -> StationCatalog:
        return StationCatalog(
            self.settings.stations.catalog_path,
            seed_if_empty=self.settings.stations.auto_seed,
        )

    def _observation_provider_routes(
        self,
        request: ObservationRequest,
    ) -> dict[str, tuple[list[str], str]]:
        """Resolve one provider route per selected station before network discovery.

        Regional observations are source-owned: a CACS station should be queried at
        CACS, an RBMC station at RBMC, a GA station at GA, and so on.  Only a
        station that is being used as an IGS/global station should walk the global
        mirror priority list.  Resolving this once per station avoids N(days)
        repeated catalog lookups and, more importantly, avoids serially probing
        unrelated regional/global providers during Planning.
        """
        routes: dict[str, tuple[list[str], str]] = {}
        for station_id in request.stations or []:
            station_request = request.model_copy(update={"stations": [station_id]})
            routes[station_id.upper()] = self._observation_provider_route(station_request)
        return routes

    def _observation_provider_route(
        self,
        request: ObservationRequest,
    ) -> tuple[list[str], str]:
        selected_networks = [
            item.lower().replace("-", "_") for item in (request.data_networks or [])
        ]
        network_registry = default_data_network_registry()
        selected_regional = [
            item
            for item in selected_networks
            if item != "igs" and network_registry.get(item).category == "regional"
        ]

        # No regional network is active: this is the classic IGS/global-mirror case.
        if not selected_regional:
            return list(self.settings.provider.priority), "igs_mirrors"

        station_id = (request.stations or [""])[0].upper()
        catalog = self._station_catalog()
        station = catalog.get(station_id) if station_id else None
        if station is None and len(station_id) >= 4:
            station = catalog.get(station_id[:4])

        regional_provider_order = network_registry.providers_for(selected_regional)
        regional_provider_set = set(regional_provider_order)
        source_registry = default_regional_source_registry()
        requested_sources = {item for item in (request.regional_sources or [])}

        # 1) Strongest signal: the station carries an explicit regional-source
        # membership (for example cacs_ca / chain_ca or an Australian GA source).
        source_providers: list[str] = []
        if station is not None:
            for source_id in station.regional_sources:
                if requested_sources and source_id not in requested_sources:
                    continue
                try:
                    source = source_registry.get(source_id)
                except ConfigurationError:
                    # Older caches sometimes stored the provider id directly as
                    # regional_sources.  Accept it only when it belongs to the
                    # currently selected regional network.
                    if source_id in regional_provider_set:
                        provider_name = source_id
                    else:
                        continue
                else:
                    if source.data_network not in selected_regional:
                        continue
                    provider_name = source.provider
                if provider_name in regional_provider_set and provider_name not in source_providers:
                    source_providers.append(provider_name)

        if source_providers:
            chosen = _first_in_priority(source_providers, regional_provider_order)
            return [chosen], "regional_source"

        # 2) Provider provenance from StationCatalog is also authoritative.  This
        # covers regional networks that do not yet expose a second-level source
        # selector (RBMC, EPN, DPGA, RING, Rénag/RGP, KASI/NGII, etc.).
        if station is not None:
            provenance = [
                provider
                for provider in station.providers
                if provider in regional_provider_set
            ]
            if provenance:
                chosen = _first_in_priority(provenance, regional_provider_order)
                return [chosen], "regional_station_provider"

            # A station may be geographically inside the selected region only
            # because the IGS catalog inferred its country membership.  Such a
            # station has no regional-source/provider provenance, so it remains
            # an IGS station and may use the configured global mirrors.
            if "igs" in {item.lower() for item in station.data_networks}:
                return list(self.settings.provider.priority), "regional_igs_station"

        # 3) If the regional network has exactly one data provider, route directly
        # to it even when an old StationCatalog entry lacks provider provenance.
        if len(regional_provider_order) == 1:
            return [regional_provider_order[0]], "regional_network_provider"

        # 4) Multi-source regional networks must never walk every source just to
        # discover who owns the selected station.  If provenance is unavailable,
        # choose the configured primary regional provider deterministically and
        # record the route mode for diagnostics.  A subsequent catalog refresh can
        # make this exact on the next request.
        if regional_provider_order:
            return [regional_provider_order[0]], "regional_primary_provider"

        return list(self.settings.provider.priority), "igs_mirrors"

    def data_network_provider_priority(
        self,
        data_networks: list[str] | None = None,
    ) -> list[str]:
        if not data_networks:
            return self.settings.provider.priority
        registry = default_data_network_registry()
        providers = registry.providers_for(data_networks)
        fallback = [
            item for item in self.settings.provider.priority if item not in providers
        ]
        return [*providers, *fallback]

    def _annotate_observation_sources(self, remote_files: list[RemoteFile]) -> None:
        catalog = self._station_catalog()
        for remote in remote_files:
            if not remote.station:
                continue
            station = catalog.get(remote.station)
            if not station and len(remote.station) >= 4:
                station = catalog.get(remote.station[:4])
            if station and station.regional_sources:
                remote.metadata["station_regional_sources"] = ",".join(station.regional_sources)

    async def _search_with_provider(
        self,
        provider: str,
        search: Callable[[object], Awaitable[list[RemoteFile]]],
    ) -> tuple[list[RemoteFile], list[ProviderAttempt]]:
        if provider != "auto":
            try:
                files = await search(self.registry.get(provider))
            except GNSSGoError as exc:
                return [], [ProviderAttempt(provider=provider, status="failed", message=str(exc))]
            status = "success" if files else "not_found"
            return files, [ProviderAttempt(provider=provider, status=status)]

        attempts: list[ProviderAttempt] = []
        for candidate in self.registry.ordered(self.settings.provider.priority):
            try:
                files = await search(candidate)
            except (GNSSGoError, OSError) as exc:
                attempts.append(
                    ProviderAttempt(
                        provider=candidate.name,
                        status="failed",
                        message=str(exc),
                    )
                )
                continue
            if files:
                attempts.append(ProviderAttempt(provider=candidate.name, status="success"))
                return files, attempts
            attempts.append(ProviderAttempt(provider=candidate.name, status="not_found"))
        return [], attempts


def _first_in_priority(values: list[str], priority: list[str]) -> str:
    wanted = set(values)
    for item in priority:
        if item in wanted:
            return item
    return values[0]


def _expected_rinex_type(data_type: str) -> str | None:
    if data_type == "obs":
        return "observation"
    if data_type == "nav":
        return "navigation"
    return None


def _existing_processed_path(destination: Path) -> Path:
    name = destination.name
    for suffix in (".gz", ".Z", ".z"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    if name.lower().endswith(".crx"):
        name = name[:-4] + ".rnx"
    elif name.lower().endswith(".d"):
        name = name[:-2] + ".o"
    return destination.with_name(name)


def _deduplicate_logical_files(remotes: list[RemoteFile]) -> list[RemoteFile]:
    seen: set[tuple[str | None, object, str, str | None]] = set()
    unique: list[RemoteFile] = []
    for remote in remotes:
        key = (
            remote.station.upper() if remote.station else None,
            remote.date,
            remote.data_type,
            remote.metadata.get("logical_id") or remote.filename,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(remote)
    return unique


def _observation_missing_entries(
    request: ObservationRequest,
    remote_files: list[RemoteFile],
) -> list[str]:
    """Return selected station/day pairs for which no observation file was found.

    Providers are not required to emit a RemoteFile for a miss, so the plan must
    derive coverage from the user's request.  This is especially important for
    batched regional APIs such as GA: a successful HTTP response may contain data
    for only some of the requested stations/days.
    """
    available: set[tuple[str, object]] = set()
    available_short: set[tuple[str, object]] = set()
    for remote in remote_files:
        if remote.data_type != "obs":
            continue

        # Interactive providers such as Japan GEONET return one ZIP RemoteFile
        # for several selected stations/days.  The generic one-file/one-station
        # coverage check used to interpret that bundle as covering none of the
        # requested stations, which produced the misleading red
        # "Unavailable station/date combinations" box in PLAN.  Expand the
        # bundle metadata into logical station/day coverage before evaluating
        # missing entries.
        if remote.metadata.get("multi_station_bundle") == "1":
            raw_ids = (
                remote.metadata.get("geonet_station_ids")
                or remote.metadata.get("bundle_station_ids")
                or ""
            )
            station_ids = [
                value.strip().upper()
                for value in str(raw_ids).replace(",", "␟").split("␟")
                if value.strip()
            ]
            start_text = str(remote.metadata.get("geonet_start") or "").strip()
            end_text = str(remote.metadata.get("geonet_end") or "").strip()
            try:
                bundle_start = date.fromisoformat(start_text) if start_text else remote.date
                bundle_end = date.fromisoformat(end_text) if end_text else remote.date
            except ValueError:
                bundle_start = remote.date
                bundle_end = remote.date
            if station_ids and bundle_start is not None and bundle_end is not None:
                for station_id in station_ids:
                    current = bundle_start
                    while current <= bundle_end:
                        available.add((station_id, current))
                        available_short.add((station_id[:4], current))
                        current += timedelta(days=1)
                continue

        if not remote.station or remote.date is None:
            continue
        station = remote.station.upper()
        available.add((station, remote.date))
        available_short.add((station[:4], remote.date))

    missing: list[str] = []
    for station in request.stations or []:
        station_upper = station.upper()
        station_short = station_upper[:4]
        for day in request.date_range.days():
            if (station_upper, day) in available or (station_short, day) in available_short:
                continue
            missing.append(f"{station_upper} — {day.isoformat()}")
    return missing


def _expand_logical_requests(
    request: ObservationRequest | NavigationRequest,
) -> list[ObservationRequest | NavigationRequest]:
    if isinstance(request, NavigationRequest):
        return [
            request.model_copy(
                update={"date_range": DateRange(start=day, end=day)},
            )
            for day in request.date_range.days()
        ]

    items: list[ObservationRequest | NavigationRequest] = []
    for station in request.stations or []:
        for day in request.date_range.days():
            items.append(
                request.model_copy(
                    update={
                        "stations": [station],
                        "date_range": DateRange(start=day, end=day),
                    },
                )
            )
    return items


def _logical_request_key(request: ObservationRequest | NavigationRequest) -> str:
    day = request.date_range.start.isoformat()
    if isinstance(request, NavigationRequest):
        return f"nav:{day}:{request.nav_type.value}"
    station = (request.stations or [""])[0].upper()
    return f"obs:{station}:{day}:{request.sampling or ''}:{request.rinex.value}"


def _preferred_logical_candidate(files: list[RemoteFile]) -> RemoteFile:
    return sorted(files, key=_logical_candidate_sort_key)[0]


def _logical_candidate_sort_key(remote: RemoteFile) -> tuple[int, str]:
    name = remote.filename
    upper = name.upper()
    lower = name.lower()
    priority = 50
    if upper.startswith("BRDC00IGS"):
        priority = 0
    elif upper.startswith("BRDC00WRD"):
        priority = 1
    elif upper.startswith(("BRDM", "BRD4")):
        priority = 2
    elif lower.endswith((".crx.gz", ".crx", ".d.z", ".d.gz")):
        priority = 0
    elif lower.endswith((".rnx.gz", ".rnx", ".o.z", ".o.gz")):
        priority = 1
    return priority, name


def _read_station_file(path: Path) -> list[str]:
    stations: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.split("#", 1)[0].strip()
        if value:
            stations.append(value.upper())
    return stations


def _unique_station_codes(stations: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for station in stations:
        key = station.upper()
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _resolve_regional_source_filters(
    data_networks: list[str] | None,
    regional_sources: list[str] | None,
) -> tuple[list[str] | None, list[str] | None]:
    if regional_sources is None:
        return data_networks, None
    source_registry = default_regional_source_registry()
    normalized_sources = source_registry.normalize_many(regional_sources)
    source_networks = {
        source_registry.get(source_id).data_network
        for source_id in normalized_sources
    }
    normalized_networks = [
        item.lower().replace("-", "_") for item in (data_networks or [])
    ]
    if data_networks is not None:
        missing = source_networks.difference(normalized_networks)
        if missing:
            source_name = source_registry.get(normalized_sources[0]).name
            network_name = ", ".join(normalized_networks)
            raise ConfigurationError(f"{source_name} is not a source of {network_name}.")
        return normalized_networks, normalized_sources
    return sorted(source_networks), normalized_sources
