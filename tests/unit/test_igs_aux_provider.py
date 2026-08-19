from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from gnssgo.models import DateRange, ProductRequest, ProductType
from gnssgo.providers.igs_aux import IGSAuxiliaryProvider


def _request(product_type: ProductType, day: str | None = None) -> ProductRequest:
    request_day = day or datetime.now(timezone.utc).date().isoformat()
    return ProductRequest(
        date_range=DateRange(start=request_day, end=request_day),
        product_types=[product_type],
        center="IGS",
        tier="final",
    )


def test_igs_aux_antex_uses_public_current_igs20_file() -> None:
    provider = IGSAuxiliaryProvider()
    files = asyncio.run(provider.search_products(_request(ProductType.ANTEX)))
    assert len(files) == 1
    assert files[0].filename == "igs20.atx.gz"
    assert str(files[0].url).startswith("https://files.igs.org/pub/station/general/")


def test_igs_aux_sinex_uses_public_current_station_file() -> None:
    provider = IGSAuxiliaryProvider()
    files = asyncio.run(provider.search_products(_request(ProductType.SINEX)))
    assert len(files) == 1
    assert files[0].filename == "igs.snx.gz"
    assert files[0].metadata["analysis_center"] == "IGS"


def test_igs_aux_does_not_claim_non_igs_center() -> None:
    provider = IGSAuxiliaryProvider()
    request = _request(ProductType.ANTEX).model_copy(update={"center": "COD"})
    assert asyncio.run(provider.search_products(request)) == []


def test_igs_aux_sinex_current_file_is_not_used_for_historical_request() -> None:
    provider = IGSAuxiliaryProvider()
    request = _request(ProductType.SINEX, "2020-01-01")
    assert asyncio.run(provider.search_products(request)) == []
