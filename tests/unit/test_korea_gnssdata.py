from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import date
from urllib.parse import parse_qs

import httpx

from gnssgo.download.events import CancellationToken
from gnssgo.download.http import HttpDownloader
from gnssgo.models import DateRange, DownloadTask, ObservationRequest
from gnssgo.providers.korea import KoreaNationalCatalogProvider


def _request(station: str, *, rinex: str = "auto", sampling: str = "30s") -> ObservationRequest:
    return ObservationRequest(
        stations=[station],
        date_range=DateRange(start=date(2026, 4, 1), end=date(2026, 4, 1)),
        sampling=sampling,
        rinex=rinex,
    )


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("donm0910.26o", "     2.11           OBSERVATION DATA    G                   RINEX VERSION / TYPE\n")
    return buffer.getvalue()


def test_ngii_provider_plans_public_session_zip_without_creating_key() -> None:
    provider = KoreaNationalCatalogProvider()
    files = asyncio.run(provider.search_observations(_request("DONM00KOR")))

    assert len(files) == 1
    remote = files[0]
    assert remote.provider == "ngii_kr"
    assert remote.station == "DONM00KOR"
    assert remote.date == date(2026, 4, 1)
    assert remote.filename == "KOREA_NGII_DONM_20260401_30S.zip"
    assert remote.metadata["http_transport"] == "ngii_session_zip"
    assert remote.metadata["ngii_station"] == "DONM"
    assert remote.metadata["ngii_start"] == "20260401"
    assert remote.metadata["ngii_end"] == "20260401"
    assert remote.metadata["ngii_data_type"] == "30"
    assert "ngii_last_zip_key" not in remote.metadata


def test_ngii_provider_does_not_silently_return_wrong_sampling_or_rinex() -> None:
    provider = KoreaNationalCatalogProvider()
    assert asyncio.run(provider.search_observations(_request("DONM00KOR", sampling="1s"))) == []
    assert asyncio.run(provider.search_observations(_request("DONM00KOR", rinex="3"))) == []


def test_ngii_transport_reproduces_verified_session_key_zip_flow(tmp_path) -> None:
    provider = KoreaNationalCatalogProvider()
    remote = asyncio.run(provider.search_observations(_request("DONM00KOR")))[0]
    payload = _zip_bytes()
    calls: list[tuple[str, str, dict[str, list[str]], str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode()) if request.content else {}
        cookie = request.headers.get("cookie", "")
        calls.append((request.method, request.url.path, form, cookie))

        if request.url.path == "/download/getDownloadView.do":
            return httpx.Response(
                200,
                text="<html>download</html>",
                headers={"set-cookie": "JSESSIONID=TESTSESSION; Path=/; HttpOnly"},
            )
        assert "JSESSIONID=TESTSESSION" in cookie

        if request.url.path == "/poll/add.json":
            assert form["purp"] == ["M2"]
            assert form["route"] == ["B1"]
            return httpx.Response(200, json={"result": True})
        if request.url.path == "/downLog/set.json":
            assert form["corsId"] == ["DONM"]
            assert form["mngrCde"] == ["RZ"]
            return httpx.Response(200, json={"result": True})
        if request.url.path == "/download/createToZip.json":
            assert form["corsId"] == ["DONM"]
            assert form["obsStDay"] == ["20260401"]
            assert form["obsEdDay"] == ["20260401"]
            assert form["dataTyp"] == ["30"]
            assert len(form["regDat"][0]) == 14
            return httpx.Response(
                200,
                json={"result": True, "gnssDataDwnVo": "", "key": 1317384},
            )
        if request.url.path == "/download/getZip.do":
            assert request.url.params["key"] == "1317384"
            return httpx.Response(
                200,
                content=payload,
                headers={
                    "content-type": "application/zip;charset=UTF-8",
                    "content-disposition": 'attachment; filename="20260819213332_1787142812954.zip"',
                    "content-length": str(len(payload)),
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def run() -> None:
        downloader = HttpDownloader()
        await downloader.client.aclose()
        downloader.client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        try:
            task = DownloadTask(
                remote=remote,
                destination=tmp_path / remote.filename,
                temporary_path=tmp_path / (remote.filename + ".part"),
                decompress=False,
            )
            result = await downloader.download(
                task,
                resume=False,
                cancellation_token=CancellationToken(),
            )
            assert result == task.temporary_path
            assert result.read_bytes() == payload
            assert remote.metadata["ngii_last_zip_key"] == "1317384"
        finally:
            await downloader.close()

    asyncio.run(run())
    assert [path for _method, path, _form, _cookie in calls] == [
        "/download/getDownloadView.do",
        "/poll/add.json",
        "/downLog/set.json",
        "/download/createToZip.json",
        "/download/getZip.do",
    ]


def test_ngii_national_station_routes_into_download_plan(tmp_path) -> None:
    from gnssgo import GNSSGo
    from gnssgo.config import load_settings
    from gnssgo.models import Station
    from gnssgo.providers.registry import ProviderRegistry
    from gnssgo.stations import StationCatalog

    registry = ProviderRegistry()
    registry.register(KoreaNationalCatalogProvider())
    settings = load_settings(
        {
            "stations": {"catalog_path": tmp_path / "stations.sqlite", "auto_seed": False},
            "archive": {"root": tmp_path / "archive"},
        }
    )
    client = GNSSGo(settings=settings, registry=registry)
    StationCatalog(settings.stations.catalog_path, seed_if_empty=False).upsert_many(
        [
            Station(
                id="DONM00KOR",
                marker_name="DONM",
                latitude=37.0,
                longitude=127.0,
                country="KOR",
                data_networks=["korea"],
                regional_sources=["korea_national"],
                providers=["ngii_kr"],
            )
        ]
    )

    plan = client.plan_observations(
        stations=["DONM00KOR"],
        start="2026-04-01",
        end="2026-04-01",
        data_networks=["korea"],
        regional_sources=["korea_national"],
        output=tmp_path / "downloads",
        keep_compressed=True,
    )

    assert len(plan.remote_files) == 1
    assert plan.remote_files[0].provider == "ngii_kr"
    assert plan.remote_files[0].metadata["provider_route"] == "regional_source"
    assert len(plan.download_tasks) == 1
    assert plan.missing == []
