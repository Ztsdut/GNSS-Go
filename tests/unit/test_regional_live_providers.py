from __future__ import annotations

import asyncio
from datetime import date

import httpx

from gnssgo.exceptions import ProviderProtocolError
from gnssgo.models import DateRange, ObservationRequest
from gnssgo.providers.cache import ProviderHealthCache, ProviderHealthStatus
from gnssgo.providers.regional_live import (
    EPNProvider,
    GAProvider,
    GeoNetNZProvider,
    RBMCProvider,
)


def request(station: str, rinex: str = "3", sampling: str = "30s") -> ObservationRequest:
    return ObservationRequest(
        stations=[station],
        date_range=DateRange(start=date(2026, 8, 1), end=date(2026, 8, 1)),
        rinex=rinex,
        sampling=sampling,
    )


def test_ga_provider_parses_station_catalog_and_rinex_api() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        if "corsNetworks" in str(req.url):
            return httpx.Response(
                200,
                json={
                    "_embedded": {
                        "corsNetworks": [
                            {"id": 101, "name": "AUSCOPE"},
                            {"id": 201, "name": "CORSNET-NSW"},
                            {"id": 251, "name": "GEONET"},
                        ]
                    },
                    "page": {"number": 0},
                },
            )
        if "corsSites" in str(req.url):
            page = req.url.params.get("page")
            return httpx.Response(
                200,
                json={
                    "_embedded": {
                        "corsSites": [
                            {
                                "name": "Alice Springs",
                                "fourCharacterId": "ALIC",
                                "domesNumber": "50137M001",
                                "approximatePosition": {
                                    "coordinates": [-23.67, 133.88, 600.0]
                                },
                                "networkTenancies": [{"corsNetworkId": 201}],
                            },
                            {
                                "name": "New Zealand",
                                "fourCharacterId": "NZST",
                                "networkTenancies": [{"corsNetworkId": 251}],
                            },
                        ]
                    },
                    "_links": {},
                    "page": {"number": int(page or "0")},
                },
            )
        return httpx.Response(
            200,
            json=[
                {
                    "stationId": "alic",
                    "rinexVersion": "3",
                    "fileLocation": (
                        "https://example.test/"
                        "ALIC00AUS_R_20262130000_01D_30S_MO.crx.gz"
                    ),
                }
            ],
        )

    provider = GAProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    stations = asyncio.run(provider.fetch_station_catalog())
    files = asyncio.run(provider.search_observations(request("ALIC00AUS")))

    assert stations[0].id == "ALIC00AUS"
    assert stations[0].data_networks == ["australia"]
    assert stations[0].regional_sources == ["corsnet_nsw"]
    assert "NZST00NZL" not in {station.id for station in stations}
    assert all(station.regional_sources == ["corsnet_nsw"] for station in stations)
    assert files[0].provider == "ga"
    assert files[0].filename == "ALIC00AUS_R_20262130000_01D_30S_MO.crx.gz"


def test_ga_provider_batches_station_and_date_queries() -> None:
    calls: list[httpx.Request] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(
            200,
            json=[
                {
                    "siteId": "alic",
                    "startDate": "2026-08-01T00:00:00Z",
                    "rinexVersion": "3",
                    "filePeriod": "01D",
                    "fileLocation": (
                        "https://example.test/"
                        "ALIC00AUS_R_20262130000_01D_30S_MO.crx.gz"
                    ),
                },
                {
                    "siteId": "admn",
                    "startDate": "2026-08-03T00:00:00Z",
                    "rinexVersion": "3",
                    "filePeriod": "01D",
                    "fileLocation": (
                        "https://example.test/"
                        "ADMN00AUS_R_20262150000_01D_30S_MO.crx.gz"
                    ),
                },
            ],
        )

    provider = GAProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    multi_request = ObservationRequest(
        stations=["ALIC00AUS", "ADMN00AUS"],
        date_range=DateRange(start=date(2026, 8, 1), end=date(2026, 8, 3)),
        rinex="auto",
        sampling="30s",
    )

    files = asyncio.run(provider.search_observations(multi_request))

    assert len(calls) == 1
    assert calls[0].url.params.get("stationId") == "ALIC,ADMN"
    assert calls[0].url.params.get("startDate") == "2026-08-01T00:00:00Z"
    assert calls[0].url.params.get("endDate") == "2026-08-03T23:59:59Z"
    assert calls[0].url.params.get("rinexVersion") == "3"
    assert [(item.station, item.date) for item in files] == [
        ("ALIC00AUS", date(2026, 8, 1)),
        ("ADMN00AUS", date(2026, 8, 3)),
    ]


def test_ga_provider_rejects_repeated_next_url() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "_embedded": {"corsSites": []},
                "_links": {"next": {"href": str(req.url)}},
                "page": {"number": 0},
            },
        )

    provider = GAProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    try:
        asyncio.run(provider.fetch_station_catalog())
    except ProviderProtocolError:
        return
    raise AssertionError("Expected ProviderProtocolError")


def test_epn_provider_catalog_has_coordinates_and_bev_download() -> None:
    requested: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        requested.append(url)
        if url == "https://gnss.be/epndata.php":
            return httpx.Response(
                200,
                text=(
                    "<table>"
                    "<tr><th>Station</th><th>City</th><th>Country</th><th>Lat</th><th>Lon</th></tr>"
                    "<tr><td><a>BRUX00BEL</a></td><td>Brussels</td><td>Belgium</td><td>50.7981</td><td>4.3586</td></tr>"
                    "<tr><td>ACOR00ESP</td><td>A Coruna</td><td>Spain</td><td>43.3644</td><td>-8.3989</td></tr>"
                    "<tr><td>GRAS00FRA</td><td>Grasse</td><td>France</td><td>43.7547</td><td>6.9206</td></tr>"
                    "</table>"
                ),
            )
        if "datadistributor.6409a3893fe0d347d90261de" in url:
            return httpx.Response(200, text="BEV BRUX00BEL ACOR00ESP GRAS00FRA")
        if "datadistributor.6409a3893fe0d347d90261df" in url:
            return httpx.Response(200, text="BKG ACOR00ESP GRAS00FRA")
        if "datadistributor.6409a3893fe0d347d90261e0" in url:
            return httpx.Response(200, text="BKG BRUX00BEL")
        if url == "https://rgp.ign.fr/STATIONS/liste.php":
            return httpx.Response(
                200,
                text=(
                    "<table><tr><th>Site</th><th>Name</th></tr>"
                    "<tr><td>GRAS</td><td>Grasse</td></tr>"
                    "<tr><td>TLSE</td><td>Toulouse</td></tr></table>"
                ),
            )
        if "/api/v1/epn/station-data/BRUX00BEL" in url:
            filename = "BRUX00BEL_R_20262130000_01D_30S_MO.crx.gz"
            return httpx.Response(
                200,
                json=[{
                    "stationId": "BRUX00BEL",
                    "filename": filename,
                    "url": f"https://epncb.oma.be/pub/RINEX/2026/213/{filename}",
                    "rinexVersion": "3.04",
                    "date": "2026-08-01",
                    "year": 2026,
                    "DOY": 213,
                }],
            )
        if "gnss.bev.gv.at" in url:
            filename = "BRUX00BEL_R_20262130000_01D_30S_MO.crx.gz"
            return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')
        if "igs.bkg.bund.de/root_ftp/EUREF/obs" in url:
            filename = "ACOR00ESP_R_20262130000_01D_30S_MO.crx.gz"
            return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')
        raise AssertionError(f"unexpected EPN URL: {url}")

    provider = EPNProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    files = asyncio.run(provider.search_observations(request("BRUX00BEL")))
    stations = asyncio.run(provider.fetch_station_catalog())

    assert files[0].provider == "epn"
    assert files[0].station == "BRUX00BEL"
    assert files[0].metadata["regional_source"] == "epn_bkg"
    assert files[0].metadata["data_center"] == "BKG"
    assert files[0].metadata["discovery_source"] == "ROB / EPN API"
    assert [item.metadata["data_center"] for item in files[0].fallback_candidates] == [
        "BEV", "ROB / EPN", "IGN"
    ]
    assert all("_MO." in item.filename for item in files)
    brux = next(station for station in stations if station.id == "BRUX00BEL")
    assert brux.data_networks == ["europe"]
    assert brux.latitude == 50.7981
    assert brux.longitude == 4.3586
    assert brux.regional_sources == ["europe_epn"]
    assert brux.metadata["epn_data_centres"] == ["epn_rob", "epn_bev", "epn_bkg"]
    acor = next(station for station in stations if station.id == "ACOR00ESP")
    assert acor.regional_sources == ["europe_epn"]
    assert acor.metadata["epn_data_centres"] == ["epn_rob", "epn_bev", "epn_bkg"]
    gras = next(station for station in stations if station.id == "GRAS00FRA")
    assert gras.regional_sources == ["europe_epn"]
    assert gras.metadata["epn_data_centres"] == ["epn_rob", "epn_bev", "epn_bkg", "epn_ign"]
    assert provider.last_station_catalog_stats["epn_catalog_version"] == 6
    assert provider.last_station_catalog_stats["regional_source_counts"] == {
        "epn_rob": 3,
        "epn_bev": 3,
        "epn_bkg": 3,
        "epn_ign": 1,
    }

    acor_request = request("ACOR00ESP")
    acor_request.regional_sources = list(provider.all_source_ids)
    acor_files = asyncio.run(provider.search_observations(acor_request))
    assert len(acor_files) == 1
    assert acor_files[0].metadata["regional_source"] == "epn_bkg"
    assert acor_files[0].metadata["data_center"] == "BKG"
    assert any("EUREF/obs/2026/213/" in url for url in requested)


def test_epn_provider_rob_source_uses_official_station_api() -> None:
    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(
            200,
            json=[
                {
                    "stationId": "BRUX00BEL",
                    "filename": "BRUX00BEL_R_20262130000_01D_30S_MO.crx.gz",
                    "url": (
                        "https://epncb.oma.be/pub/RINEX/2026/213/"
                        "BRUX00BEL_R_20262130000_01D_30S_MO.crx.gz"
                    ),
                    "rinexVersion": "3.04",
                    "date": "2026-08-01",
                    "year": 2026,
                    "DOY": 213,
                }
            ],
        )

    provider = EPNProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    req = request("BRUX00BEL")
    req.regional_sources = ["epn_rob"]
    files = asyncio.run(provider.search_observations(req))

    assert len(files) == 1
    assert files[0].metadata["regional_source"] == "epn_rob"
    assert files[0].metadata["data_center"] == "ROB / EPN"
    assert "/api/v1/epn/station-data/BRUX00BEL" in seen[0]
    assert "startDate=2026-08-01" in seen[0]
    assert "endDate=2026-08-01" in seen[0]


def test_epn_provider_bkg_source_filters_navigation_files() -> None:
    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        text = (
            '<a href="ACOR00ESP_R_20262130000_01D_30S_MO.crx.gz">obs</a>'
            '<a href="ACOR00ESP_R_20262130000_01D_EN.rnx.gz">gal-nav</a>'
            '<a href="ACOR00ESP_R_20262130000_01D_GN.rnx.gz">gps-nav</a>'
        )
        return httpx.Response(200, text="".join(text))

    provider = EPNProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    req = request("ACOR00ESP")
    req.regional_sources = ["epn_bkg"]
    files = asyncio.run(provider.search_observations(req))

    assert [item.filename for item in files] == [
        "ACOR00ESP_R_20262130000_01D_30S_MO.crx.gz"
    ]
    assert files[0].metadata["data_center"] == "BKG"
    assert seen == ["https://igs.bkg.bund.de/root_ftp/EUREF/obs/2026/213/"]


def test_epn_provider_ign_uses_https_archive_and_daily_file_only() -> None:
    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        text = (
            '<a href="GRAS00FRA_R_20262130000_01D_30S_MO.crx.gz">daily</a>'
            '<a href="GRAS00FRA_R_20262130000_01H_30S_MO.crx.gz">hourly</a>'
            '<a href="GRAS00FRA_R_20262130000_01D_GN.rnx.gz">nav</a>'
        )
        return httpx.Response(200, text=text)

    provider = EPNProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    req = request("GRAS00FRA")
    req.regional_sources = ["epn_ign"]
    files = asyncio.run(provider.search_observations(req))

    assert [item.filename for item in files] == [
        "GRAS00FRA_R_20262130000_01D_30S_MO.crx.gz"
    ]
    assert files[0].metadata["regional_source"] == "epn_ign"
    assert files[0].metadata["data_center"] == "IGN"
    assert seen == ["https://rgpdata.ign.fr/pub/data_v3/2026/213/data_30/"]


def test_epn_legacy_source_ids_fold_into_new_servers() -> None:
    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        filename = "ACOR00ESP_R_20262130000_01D_30S_MO.crx.gz"
        return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')

    provider = EPNProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    req = request("ACOR00ESP")
    req.regional_sources = ["epn_bkgi"]
    files = asyncio.run(provider.search_observations(req))

    assert files[0].metadata["regional_source"] == "epn_bkg"
    assert files[0].metadata["data_center"] == "BKG"

def test_geonet_nz_provider_uses_aws_archive_for_historical_day_once() -> None:
    calls = 0
    requested_urls: list[str] = []

    async def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        requested_urls.append(str(_req.url))
        return httpx.Response(
            200,
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                '<IsTruncated>false</IsTruncated>'
                '<Contents><Key>gnss/rinex/2026/213/'
                'AHTI00NZL_R_20262130000_01D_30S_MO.rnx.gz</Key></Contents>'
                '</ListBucketResult>'
            ),
        )

    provider = GeoNetNZProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    first = asyncio.run(provider.search_observations(request("AHTI00NZL")))
    second = asyncio.run(provider.search_observations(request("AHTI00NZL")))

    assert first[0].provider == "geonet_nz"
    assert first[0].metadata["discovery_source"] == "aws_open_data"
    assert first[0].url.startswith(
        "https://geonet-open-data.s3-ap-southeast-2.amazonaws.com/gnss/rinex/"
    )
    assert second[0].filename == first[0].filename
    assert calls == 1
    assert "list-type=2" in requested_urls[0]
    assert "prefix=gnss%2Frinex%2F2026%2F213%2F" in requested_urls[0]


def test_geonet_nz_historical_empty_archive_returns_immediately() -> None:
    calls = 0
    requested_urls: list[str] = []

    async def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        requested_urls.append(str(_req.url))
        return httpx.Response(
            200,
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                '<IsTruncated>false</IsTruncated>'
                '</ListBucketResult>'
            ),
        )

    provider = GeoNetNZProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    missing_request = ObservationRequest(
        stations=["AHTI00NZL"],
        date_range=DateRange(start=date(2026, 1, 1), end=date(2026, 1, 1)),
        rinex="3",
        sampling="30s",
    )
    files = asyncio.run(provider.search_observations(missing_request))

    assert files == []
    assert calls == 1
    assert "prefix=gnss%2Frinex%2F2026%2F001%2F" in requested_urls[0]
    assert all("data.geonet.org.nz" not in url for url in requested_urls)


def test_rbmc_provider_parses_rinex3_and_1s_listing() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "dados_RINEX3_1s" in url and url.rstrip("/").endswith("/213"):
            return httpx.Response(200, text='<a href="00/">00</a>')
        if "dados_RINEX3_1s" in url and url.rstrip("/").endswith("/00"):
            return httpx.Response(
                200,
                text='<a href="BRAZ00BRA_R_20262130000_15M_01S_MO.crx.gz">hr</a>',
            )
        return httpx.Response(
            200,
            text='<a href="BRAZ00BRA_R_20262130000_01D_15S_MO.crx.gz">daily</a>',
        )

    provider = RBMCProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    daily = asyncio.run(provider.search_observations(request("BRAZ00BRA")))
    high_rate = asyncio.run(
        provider.search_observations(request("BRAZ00BRA", sampling="01S"))
    )

    assert daily[0].filename.endswith("_01D_15S_MO.crx.gz")
    assert high_rate[0].metadata["sampling"] == "01S"
    assert high_rate[0].metadata["duration"] == "15M"
    assert "/213/00/" in high_rate[0].url


def test_provider_health_cache_circuit_breaker_rules() -> None:
    cache = ProviderHealthCache(failure_threshold=2)

    cache.record_failure("ga", status_code=404)
    assert cache.state("ga").status == ProviderHealthStatus.UNKNOWN

    cache.record_failure("ga", status_code=429)
    assert cache.state("ga").status == ProviderHealthStatus.DEGRADED

    cache.record_failure("epn")
    cache.record_failure("epn")
    assert cache.state("epn").status == ProviderHealthStatus.UNHEALTHY
    assert not cache.can_attempt("epn")

    cache.record_success("epn")
    assert cache.state("epn").status == ProviderHealthStatus.HEALTHY


def test_provider_health_cache_separates_host_and_service() -> None:
    cache = ProviderHealthCache(failure_threshold=1)

    cache.record_failure("ga", host="files.example", service="download")

    assert (
        cache.state("ga", host="files.example", service="download").status
        == ProviderHealthStatus.UNHEALTHY
    )
    assert (
        cache.state("ga", host="metadata.example", service="catalog").status
        == ProviderHealthStatus.UNKNOWN
    )


def test_regional_provider_sync_http_client_is_safe_across_asyncio_run_calls() -> None:
    calls = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="ok")

    provider = GeoNetNZProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    assert asyncio.run(provider._get_text("https://example.test/one")) == "ok"
    assert asyncio.run(provider._get_text("https://example.test/two")) == "ok"
    assert calls == 2


def test_rbmc_provider_can_discover_all_stations_from_day_directory() -> None:
    async def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
            <a href="ALAR00BRA_R_20262130000_01D_15S_MO.crx.gz">ALAR</a>
            <a href="BRAZ00BRA_R_20262130000_01D_15S_MO.crx.gz">BRAZ</a>
            <a href="UFPR00BRA_R_20262130000_01D_15S_MO.crx.gz">UFPR</a>
            """,
        )

    provider = RBMCProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    request_all = ObservationRequest(
        stations=None,
        date_range=DateRange(start=date(2026, 8, 1), end=date(2026, 8, 1)),
        data_networks=["brazil"],
        discover_available=True,
        rinex="3",
        sampling="15s",
    )
    files = asyncio.run(provider.search_observations(request_all))

    assert [item.station for item in files] == ["ALAR00BRA", "BRAZ00BRA", "UFPR00BRA"]
    assert all("dados_RINEX3/2026/213/" in str(item.url) for item in files)


def test_rbmc_site_log_coordinate_parses_itrf_xyz() -> None:
    text = """
3.   Monumentation
     Approximate Position (ITRF)
        X coordinate (m) :  4115014.0000
        Y coordinate (m) : -4550641.9000
        Z coordinate (m) : -1741444.0000
"""
    coordinate = RBMCProvider._rbmc_site_log_coordinate(text)
    assert coordinate is not None
    latitude, longitude, height = coordinate
    assert round(latitude, 2) == -15.95
    assert round(longitude, 2) == -47.88
    assert height is not None


def test_rbmc_catalog_fills_missing_coordinates_from_latest_ibge_site_logs() -> None:
    requested: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        requested.append(url)
        if url.endswith("/relatorio/estacoes.xml"):
            return httpx.Response(
                200,
                text="""
                <estacoes>
                  <estacao><codigo>BRAZ</codigo><status>Ativa</status></estacao>
                  <estacao><codigo>UFPR</codigo><status>Ativa</status></estacao>
                </estacoes>
                """,
            )
        if url.endswith("/relatorio/log_sirgas/"):
            return httpx.Response(
                200,
                text="""
                <a href="braz00bra_20240101.log">old BRAZ</a>
                <a href="braz00bra_20250101.log">new BRAZ</a>
                <a href="ufpr00bra_20250101.log">UFPR</a>
                """,
            )
        if url.endswith("braz00bra_20250101.log"):
            return httpx.Response(
                200,
                text="""
                X coordinate (m) :  4115014.0000
                Y coordinate (m) : -4550641.9000
                Z coordinate (m) : -1741444.0000
                """,
            )
        if url.endswith("ufpr00bra_20250101.log"):
            return httpx.Response(
                200,
                text="""
                Latitude (N is +) : -25.448
                Longitude (E is +): -49.231
                Elevation (m,ellips.): 925.0
                """,
            )
        return httpx.Response(404)

    provider = RBMCProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    provider._local_cartogram_station_catalog = lambda: ([], "")
    stations = asyncio.run(provider.fetch_station_catalog())
    by_id = {station.id: station for station in stations}

    assert round(by_id["BRAZ00BRA"].latitude, 2) == -15.95
    assert round(by_id["BRAZ00BRA"].longitude, 2) == -47.88
    assert round(by_id["UFPR00BRA"].latitude, 3) == -25.448
    assert round(by_id["UFPR00BRA"].longitude, 3) == -49.231
    assert not any(url.endswith("braz00bra_20240101.log") for url in requested)
    assert provider.last_station_catalog_stats["mapped_station_count"] == 2
    assert provider.last_station_catalog_stats["ibge_site_log_mapped"] == 2


def _synthetic_rbmc_kmz() -> bytes:
    import io
    import zipfile

    kml = """
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>BRAZ</name><Point><coordinates>-47.877,-15.947,1100</coordinates></Point></Placemark>
      <Placemark><name>UFPR00BRA</name><Point><coordinates>-49.231,-25.448,925</coordinates></Point></Placemark>
      <Placemark><name>NOTRBMC</name><Point><coordinates>-50.0,-20.0,0</coordinates></Point></Placemark>
    </Document></kml>
    """.encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml)
    return buf.getvalue()


def test_rbmc_kmz_parser_uses_known_station_codes() -> None:
    coords = RBMCProvider._parse_rbmc_kmz(
        _synthetic_rbmc_kmz(), known_codes={"BRAZ", "UFPR"}
    )
    assert set(coords) == {"BRAZ", "UFPR"}
    assert coords["BRAZ"][:2] == (-15.947, -47.877)
    assert coords["UFPR"][:2] == (-25.448, -49.231)


def test_rbmc_catalog_prefers_latest_official_cartogram_for_map_coordinates() -> None:
    kmz = _synthetic_rbmc_kmz()
    requested: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        requested.append(url)
        if url.endswith("/relatorio/estacoes.xml"):
            return httpx.Response(
                200,
                text="""
                <estacoes>
                  <estacao><codigo>BRAZ</codigo><status>Ativa</status></estacao>
                  <estacao><codigo>UFPR</codigo><status>Ativa</status></estacao>
                </estacoes>
                """,
            )
        if url.endswith("/rbmc/cartogramas/"):
            return httpx.Response(
                200,
                text='''<a href="RBMC_2023.kmz">old</a><a href="RBMC_2024.kmz">new</a>''',
            )
        if url.endswith("/rbmc/cartogramas/RBMC_2024.kmz"):
            return httpx.Response(200, content=kmz)
        if url.endswith("/relatorio/log_sirgas/"):
            return httpx.Response(200, text="")
        return httpx.Response(404)

    provider = RBMCProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    provider._local_cartogram_station_catalog = lambda: ([], "")
    stations = asyncio.run(provider.fetch_station_catalog())
    by_id = {station.id: station for station in stations}

    assert round(by_id["BRAZ00BRA"].latitude, 3) == -15.947
    assert round(by_id["BRAZ00BRA"].longitude, 3) == -47.877
    assert round(by_id["UFPR00BRA"].latitude, 3) == -25.448
    assert round(by_id["UFPR00BRA"].longitude, 3) == -49.231
    assert provider.last_station_catalog_stats["ibge_cartogram_mapped"] == 2
    assert provider.last_station_catalog_stats["mapped_station_count"] == 2
    assert any(url.endswith("RBMC_2024.kmz") for url in requested)


def test_rbmc_angle_parser_keeps_detached_ibge_negative_sign() -> None:
    lat = RBMCProvider._rbmc_parse_angle('- 09º 27\' 55,00289"', limit=90.0)
    lon = RBMCProvider._rbmc_parse_angle('- 35º 49\' 35,54308"', limit=180.0)
    assert lat is not None and round(lat, 6) == round(-(9 + 27 / 60 + 55.00289 / 3600), 6)
    assert lon is not None and round(lon, 6) == round(-(35 + 49 / 60 + 35.54308 / 3600), 6)


def test_rbmc_xml_catalog_parses_official_ibge_spaced_dms_format() -> None:
    provider = RBMCProvider()
    xml = """
    <estacoes>
      <estacao>
        <codigo>ALMC</codigo>
        <latitude>- 09º 27' 55,00289\"</latitude>
        <longitude>- 35º 49' 35,54308\"</longitude>
        <status>Ativa</status>
      </estacao>
    </estacoes>
    """
    stations = provider._parse_station_xml(xml)
    assert len(stations) == 1
    station = stations[0]
    assert station.id == "ALMC00BRA"
    assert round(station.latitude, 5) == -9.46528
    assert round(station.longitude, 5) == -35.82654


def test_rbmc_brazilian_grouped_cartesian_number_is_parsed() -> None:
    assert RBMCProvider._rbmc_first_number("5.101.506,4602 m") == 5101506.4602
    assert RBMCProvider._rbmc_first_number("-3.682.915,9169 m") == -3682915.9169


def test_rbmc_cartogram_direct_url_does_not_depend_on_directory_listing() -> None:
    kmz = _synthetic_rbmc_kmz()
    requested: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        requested.append(url)
        if url.endswith("/relatorio/estacoes.xml"):
            return httpx.Response(
                200,
                text="""
                <estacoes>
                  <estacao><codigo>BRAZ</codigo><status>Ativa</status></estacao>
                  <estacao><codigo>UFPR</codigo><status>Ativa</status></estacao>
                </estacoes>
                """,
            )
        if url.endswith("/rbmc/cartogramas/RBMC_2024.kmz"):
            return httpx.Response(200, content=kmz)
        # Simulate the real-world failure mode: the directory index cannot be
        # decoded/fetched, but the binary KMZ itself is reachable.
        if url.endswith("/rbmc/cartogramas/"):
            return httpx.Response(500, text="bad index encoding")
        if url.endswith("/relatorio/log_sirgas/"):
            return httpx.Response(200, text="")
        return httpx.Response(404)

    provider = RBMCProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    provider._local_cartogram_station_catalog = lambda: ([], "")
    stations = asyncio.run(provider.fetch_station_catalog())
    by_id = {station.id: station for station in stations}

    assert round(by_id["BRAZ00BRA"].latitude, 3) == -15.947
    assert round(by_id["UFPR00BRA"].longitude, 3) == -49.231
    assert provider.last_station_catalog_stats["ibge_cartogram_mapped"] == 2
    assert any(url.endswith("/RBMC_2024.kmz") for url in requested)
    # Direct success means the HTML directory index is never needed.
    assert not any(url.endswith("/rbmc/cartogramas/") for url in requested)


def test_rbmc_packaged_kmz_contains_full_2024_map_snapshot() -> None:
    raw = RBMCProvider.bundled_cartogram_path().read_bytes()
    coords = RBMCProvider._parse_rbmc_kmz(raw)
    assert len(coords) == 153
    assert "SALU" in coords
    assert "BRAZ" in coords


def test_rbmc_local_kmz_is_seeded_and_used_without_network(tmp_path, monkeypatch) -> None:
    local = tmp_path / ".gnssgo" / "RBMC_2024.kmz"
    monkeypatch.setattr(
        RBMCProvider,
        "local_cartogram_path",
        classmethod(lambda cls: local),
    )

    requested: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        requested.append(str(req.url))
        return httpx.Response(500, text="network must not be used")

    provider = RBMCProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    stations = asyncio.run(provider.fetch_station_catalog())

    assert local.is_file()
    assert len(stations) == 153
    assert all(s.latitude is not None and s.longitude is not None for s in stations)
    assert provider.last_station_catalog_stats["local_rbmc_kmz"] is True
    assert provider.last_station_catalog_stats["mapped_station_count"] == 153
    assert requested == []


def test_rbmc_stale_user_kmz_is_repaired_from_more_complete_project_snapshot(tmp_path, monkeypatch) -> None:
    full = RBMCProvider.bundled_cartogram_path().read_bytes()
    full_coords = RBMCProvider._parse_rbmc_kmz(full)
    assert len(full_coords) == 153

    # Build a valid but incomplete old user copy with only 23 placemarks.
    import io as _io
    import zipfile as _zipfile
    import xml.etree.ElementTree as _ET

    with _zipfile.ZipFile(_io.BytesIO(full)) as archive:
        kml_name = next(name for name in archive.namelist() if name.lower().endswith('.kml'))
        root = _ET.fromstring(archive.read(kml_name))
    parent = {child: node for node in root.iter() for child in node}
    placemarks = [
        node for node in root.iter()
        if node.tag.rsplit('}', 1)[-1].lower() == 'placemark'
    ]
    for placemark in placemarks[23:]:
        parent[placemark].remove(placemark)
    buffer = _io.BytesIO()
    with _zipfile.ZipFile(buffer, 'w', _zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(kml_name, _ET.tostring(root, encoding='utf-8', xml_declaration=True))

    local = tmp_path / 'home' / '.gnssgo' / 'RBMC_2024.kmz'
    local.parent.mkdir(parents=True)
    local.write_bytes(buffer.getvalue())
    project = tmp_path / 'project' / '.gnssgo' / 'RBMC_2024.kmz'
    project.parent.mkdir(parents=True)
    project.write_bytes(full)

    monkeypatch.setattr(RBMCProvider, 'local_cartogram_path', classmethod(lambda cls: local))
    monkeypatch.setattr(RBMCProvider, 'project_cartogram_path', classmethod(lambda cls: project))

    provider = RBMCProvider()
    stations, source = provider._local_cartogram_station_catalog()

    assert len(stations) == 153
    assert RBMCProvider._cartogram_station_count(local) == 153
    assert source == str(local)


def test_epn_bundled_fallback_catalog_has_europe_coordinates() -> None:
    provider = EPNProvider()
    stations = provider.bundled_station_catalog()
    assert len(stations) >= 100
    assert all("europe" in station.data_networks for station in stations)
    assert all("europe_epn" in station.regional_sources for station in stations)
    assert any(station.country == "DEU" for station in stations)
    assert any(station.country == "FRA" for station in stations)
