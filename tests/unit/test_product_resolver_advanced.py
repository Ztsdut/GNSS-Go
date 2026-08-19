from datetime import date

from gnssgo.models import DateRange, ProductRequest, RemoteFile
from gnssgo.products import ProductResolver


def _remote(filename: str, data_type: str, provider: str = "esa") -> RemoteFile:
    from gnssgo.providers.mirrors import _remote as make_remote

    return make_remote(
        provider,
        f"ftp://example.test/products/2430/{filename}",
        data_type,
        day=date(2026, 8, 1),
    )


def test_bundle_prefers_complete_rapid_over_partial_final() -> None:
    request = ProductRequest(
        date_range=DateRange(start="2026-08-01", end="2026-08-01"),
        product_types=["orbit", "clock"],
        tier="auto",
        center="IGS",
    )
    remotes = [
        _remote("IGS0OPSFIN_20262130000_01D_15M_ORB.SP3.gz", "orbit"),
        _remote("IGS0OPSRAP_20262130000_01D_15M_ORB.SP3.gz", "orbit"),
        _remote("IGS0OPSRAP_20262130000_01D_30S_CLK.CLK.gz", "clock"),
    ]
    resolution = ProductResolver().select_bundle(request, remotes)
    assert len(resolution.selected) == 2
    assert {item.descriptor.tier.value for item in resolution.selected} == {"rapid"}


def test_fixed_center_disallows_center_fallback() -> None:
    request = ProductRequest(
        date_range=DateRange(start="2026-08-01", end="2026-08-01"),
        product_types=["orbit", "clock"],
        tier="final",
        center="GFZ",
    )
    remotes = [
        _remote("COD0OPSFIN_20262130000_01D_15M_ORB.SP3.gz", "orbit"),
        _remote("COD0OPSFIN_20262130000_01D_30S_CLK.CLK.gz", "clock"),
    ]
    resolution = ProductResolver().select_bundle(request, remotes)
    assert not resolution.selected
    assert sorted(resolution.unavailable) == ["clock", "orbit"]


def test_mixed_center_bundle_warns_when_needed() -> None:
    request = ProductRequest(
        date_range=DateRange(start="2026-08-01", end="2026-08-01"),
        product_types=["orbit", "clock"],
        tier="final",
    )
    remotes = [
        _remote("GFZ0OPSFIN_20262130000_01D_15M_ORB.SP3.gz", "orbit"),
        _remote("COD0OPSFIN_20262130000_01D_30S_CLK.CLK.gz", "clock"),
    ]
    resolution = ProductResolver().select_bundle(request, remotes)
    assert len(resolution.selected) == 2
    assert resolution.warnings
