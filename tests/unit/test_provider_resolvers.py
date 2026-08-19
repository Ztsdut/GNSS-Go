from datetime import date

import httpx
import pytest

from gnssgo.models import DateRange, NavigationType, ObservationRequest, ProductType
from gnssgo.providers.bkg import BKGPathResolver, BKGProvider
from gnssgo.providers.mirrors import (
    bdsmart_provider,
    bkgftp_provider,
    esa_provider,
    ign_provider,
    kasi_provider,
    noaa_provider,
    sopac_provider,
)
from gnssgo.providers.whu import WHUPathResolver, WHUProvider


def test_bkg_navigation_candidates() -> None:
    urls = BKGPathResolver().navigation_candidates(date(2026, 8, 1), NavigationType.MIXED)
    assert any("BRDC" in url or "brdc" in url for url in urls)
    assert all("brdc2130.26n" not in url for url in urls)


def test_whu_navigation_candidates_use_domestic_ftp_archive() -> None:
    urls = WHUPathResolver().navigation_candidates(date(2026, 8, 1), NavigationType.MIXED)
    assert any("BRDC00IGS_R_20262130000_01D_MN.rnx.gz" in url for url in urls)
    assert any("brdc2130.26n" in url for url in urls)
    assert all(url.startswith("ftp://igs.gnsswhu.cn/pub/gps") for url in urls)


@pytest.mark.parametrize(
    ("provider_factory", "expected_prefix"),
    [
        (kasi_provider, "ftp://nfs.kasi.re.kr/gps"),
        (esa_provider, "ftp://gssc.esa.int/gnss/data/daily/2026/213/"),
        (bkgftp_provider, "ftp://igs-ftp.bkg.bund.de/IGS/BRDC/2026/213/"),
        (bdsmart_provider, "https://data.bdsmart.cn/pub/data/igs/2026/213/"),
        (ign_provider, "ftp://igs.ign.fr/pub/igs/data/2026/213/"),
        (sopac_provider, "https://garner.ucsd.edu/pub/nav/2026/213/"),
    ],
)
def test_igs_mirror_navigation_candidates(provider_factory, expected_prefix) -> None:
    provider = provider_factory(check_existing=False)
    urls = provider.resolver.navigation_candidates(date(2026, 8, 1), NavigationType.MIXED)
    assert urls
    assert any(url.startswith(expected_prefix) for url in urls)


@pytest.mark.anyio
async def test_whu_observation_candidates() -> None:
    urls = WHUPathResolver().observation_candidates("WUH200CHN", date(2026, 8, 1))
    assert len(urls) == 3

    provider = WHUProvider(check_existing=False)
    request = ObservationRequest(
        stations=["WUH200CHN"],
        date_range=DateRange(start="2026-08-01", end="2026-08-01"),
        provider="whu",
    )
    files = await provider.search_observations(request)
    assert len(files) == 1
    assert files[0].provider == "whu"
    assert files[0].url.startswith("ftp://igs.gnsswhu.cn/pub/gps/data/daily/2026/213/26d/")


@pytest.mark.anyio
async def test_noaa_observation_uses_station_subdirectory() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/corsdata/rinex/2026/213/wuhn/")
        return httpx.Response(200, text='<a href="wuhn2130.26d.gz">obs</a>')

    provider = noaa_provider(
        check_existing=False,
    )
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    request = ObservationRequest(
        stations=["WUHN00CHN"],
        date_range=DateRange(start="2026-08-01", end="2026-08-01"),
        provider="noaa",
    )
    files = await provider.search_observations(request)
    assert len(files) == 1
    assert files[0].url == "https://www.ngs.noaa.gov/corsdata/rinex/2026/213/wuhn/wuhn2130.26d.gz"


@pytest.mark.anyio
async def test_bkg_obs_listing_discovery() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/IGS/obs/2026/213/")
        return httpx.Response(
            200,
            text='<a href="WUH200CHN_R_20262130000_01D_30S_MO.crx.gz">obs</a>',
        )

    provider = BKGProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    request = ObservationRequest(
        stations=["WUH200CHN"],
        date_range=DateRange(start="2026-08-01", end="2026-08-01"),
        provider="bkg",
    )
    files = await provider.search_observations(request)
    assert len(files) == 1
    assert files[0].filename == "WUH200CHN_R_20262130000_01D_30S_MO.crx.gz"


