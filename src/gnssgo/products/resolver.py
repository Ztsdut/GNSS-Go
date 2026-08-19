from __future__ import annotations

from collections.abc import Iterable

from gnssgo.models import (
    ProductCandidate,
    ProductDescriptor,
    ProductLogicalKey,
    ProductRequest,
    ProductResolution,
    ProductRule,
    ProductSystem,
    ProductTier,
    ProductType,
    RemoteFile,
)
from gnssgo.products.naming import parse_product_filename

QUALITY_TIERS = [ProductTier.FINAL, ProductTier.RAPID, ProductTier.ULTRA]


class ProductResolver:
    def __init__(
        self,
        center_priority: Iterable[str] | None = None,
        multi_gnss_center_priority: Iterable[str] | None = None,
        allow_mixed_center: bool = True,
    ) -> None:
        self.center_priority = [item.upper() for item in (center_priority or ["IGS", "COD", "GFZ"])]
        self.multi_gnss_center_priority = [
            item.upper() for item in (multi_gnss_center_priority or ["IGS", "WUM", "GFZ", "COD"])
        ]
        self.allow_mixed_center = allow_mixed_center

    def resolve(self, request: ProductRequest) -> list[ProductRule]:
        """Compatibility shim for the Phase 1/2 API."""
        rules: list[ProductRule] = []
        for product_type in request.product_types:
            available = not (
                product_type == ProductType.SINEX and request.tier == ProductTier.ULTRA
            )
            rules.append(
                ProductRule(
                    product_type=product_type,
                    center=request.center,
                    tier=request.tier,
                    available=available,
                    reason=None if available else "SINEX is not available as an ultra product.",
                )
            )
        return rules

    def tier_order(self, request: ProductRequest) -> list[ProductTier]:
        if request.tier == ProductTier.AUTO:
            return QUALITY_TIERS
        return [request.tier]

    def center_order(self, request: ProductRequest) -> list[str]:
        if request.center.lower() != "auto":
            return [request.center.upper()]
        if request.system in {ProductSystem.MULTI, ProductSystem.AUTO}:
            return self.multi_gnss_center_priority
        return self.center_priority

    def select_bundle(
        self,
        request: ProductRequest,
        candidates: list[RemoteFile],
    ) -> ProductResolution:
        resolution = ProductResolution()
        grouped = _group_candidates(candidates)
        requested = list(dict.fromkeys(request.product_types))

        # A complete same-tier, same-center bundle is preferred over a higher-tier partial bundle.
        for tier in self.tier_order(request):
            for center in self.center_order(request):
                selected = [
                    _best_remote(grouped.get((product_type, center, tier), []))
                    for product_type in requested
                ]
                if all(selected):
                    return _resolution_from_remotes(request, selected, resolution)
                resolution.trace.append(
                    f"{center} {tier.value}: "
                    f"{sum(1 for item in selected if item)}/{len(requested)} products found"
                )

        if self.allow_mixed_center:
            mixed = self._select_mixed_complete(request, grouped, requested, resolution)
            if mixed:
                centers = sorted(
                    {remote.metadata.get("analysis_center", "") for remote in mixed}
                )
                tiers = sorted({remote.metadata.get("product_tier", "") for remote in mixed})
                if len(centers) > 1:
                    resolution.warnings.append(
                        "No fully compatible single-center bundle was available; "
                        "selected a mixed-center bundle."
                    )
                if len(tiers) > 1:
                    resolution.warnings.append(
                        "No same-tier complete bundle was available; selected a mixed-tier bundle."
                    )
                return _resolution_from_remotes(request, mixed, resolution)

        partial = self._select_best_partial(request, grouped, requested, resolution)
        missing = [
            product_type.value
            for product_type in requested
            if product_type.value not in {remote.data_type for remote in partial}
        ]
        resolution.unavailable.extend(missing)
        resolution.trace.append(f"partial selection: missing {', '.join(missing) or 'none'}")
        return _resolution_from_remotes(request, partial, resolution)

    def _select_mixed_complete(
        self,
        request: ProductRequest,
        grouped: dict[tuple[ProductType, str, ProductTier], list[RemoteFile]],
        requested: list[ProductType],
        resolution: ProductResolution,
    ) -> list[RemoteFile]:
        for tier in self.tier_order(request):
            selected: list[RemoteFile] = []
            for product_type in requested:
                remotes = [
                    remote
                    for (ptype, _center, ptier), files in grouped.items()
                    if (
                        ptype == product_type
                        and ptier == tier
                        and _center in self.center_order(request)
                    )
                    for remote in files
                ]
                best = _best_remote(remotes)
                if best:
                    selected.append(best)
            if len(selected) == len(requested):
                return selected
            resolution.trace.append(
                f"mixed centers {tier.value}: {len(selected)}/{len(requested)} products found"
            )
        return []

    def _select_best_partial(
        self,
        request: ProductRequest,
        grouped: dict[tuple[ProductType, str, ProductTier], list[RemoteFile]],
        requested: list[ProductType],
        resolution: ProductResolution,
    ) -> list[RemoteFile]:
        selected: list[RemoteFile] = []
        for product_type in requested:
            remotes = [
                remote
                for tier in self.tier_order(request)
                for center in self.center_order(request)
                for remote in grouped.get((product_type, center, tier), [])
            ]
            best = _best_remote(remotes)
            if best:
                selected.append(best)
            else:
                resolution.trace.append(f"{product_type.value}: not found")
        return selected


def logical_key(remote: RemoteFile) -> ProductLogicalKey:
    parsed = parse_product_filename(remote.filename)
    if parsed:
        return ProductLogicalKey(
            product_type=parsed.product_type,
            center=parsed.center,
            tier=parsed.tier,
            system=parsed.system,
            date=parsed.date or remote.date,
            duration=parsed.duration,
            sampling=parsed.sampling,
            campaign=parsed.campaign,
        )
    return ProductLogicalKey(
        product_type=ProductType(remote.data_type),
        center=remote.metadata.get("analysis_center", "UNKNOWN"),
        tier=ProductTier(remote.metadata.get("product_tier", "auto")),
        system=ProductSystem(remote.metadata.get("product_system", "auto")),
        date=remote.date,
        duration=remote.metadata.get("duration"),
        sampling=remote.metadata.get("sampling"),
        campaign=remote.metadata.get("campaign"),
    )


def _group_candidates(
    candidates: list[RemoteFile],
) -> dict[tuple[ProductType, str, ProductTier], list[RemoteFile]]:
    grouped: dict[tuple[ProductType, str, ProductTier], list[RemoteFile]] = {}
    for remote in candidates:
        try:
            product_type = ProductType(remote.data_type)
            tier = ProductTier(remote.metadata.get("product_tier", "auto"))
        except ValueError:
            continue
        center = remote.metadata.get("analysis_center", "UNKNOWN").upper()
        grouped.setdefault((product_type, center, tier), []).append(remote)
    return grouped


def _best_remote(remotes: list[RemoteFile]) -> RemoteFile | None:
    return remotes[0] if remotes else None


def _resolution_from_remotes(
    request: ProductRequest,
    remotes: list[RemoteFile | None],
    resolution: ProductResolution,
) -> ProductResolution:
    selected_remotes = [remote for remote in remotes if remote is not None]
    for remote in selected_remotes:
        descriptor = parse_product_filename(remote.filename) or ProductDescriptor(
            product_type=ProductType(remote.data_type),
            center=remote.metadata.get("analysis_center", "UNKNOWN"),
            tier=ProductTier(remote.metadata.get("product_tier", "auto")),
            system=ProductSystem(remote.metadata.get("product_system", "auto")),
            date=remote.date,
            filename=remote.filename,
        )
        resolution.logical_products.append(descriptor)
        resolution.selected.append(
            ProductCandidate(
                descriptor=descriptor,
                provider=remote.provider,
                url=str(remote.url),
                filename=remote.filename,
                availability="found",
            )
        )
        resolution.trace.append(
            f"{descriptor.product_type.value}: selected {descriptor.center} "
            f"{descriptor.tier.value} @ {remote.provider}"
        )
    found_types = {remote.data_type for remote in selected_remotes}
    for product_type in request.product_types:
        if (
            product_type.value not in found_types
            and product_type.value not in resolution.unavailable
        ):
            resolution.unavailable.append(product_type.value)
    return resolution
