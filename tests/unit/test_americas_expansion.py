from __future__ import annotations

import asyncio
import time
from datetime import date

import pytest
from types import SimpleNamespace

import httpx

from gnssgo.data_networks import AutomationLevel, default_data_network_registry
from gnssgo.exceptions import ProviderError
from gnssgo.models import DateRange, ObservationRequest
from gnssgo.providers import default_registry
from gnssgo.providers.americas import (
    BoliviaSIRGASProvider,
    ChileCSNProvider,
    RAMSACArgentinaProvider,
    RGNAMexicoProvider,
    UruguaySIRGASProvider,
    _run_daemon_bounded,
)
from gnssgo.providers.regional_expansion import NOAANCNProvider
from gnssgo.regional_sources import default_regional_source_registry


def _request(station: str, day: date = date(2026, 1, 1)) -> ObservationRequest:
    return ObservationRequest(
        stations=[station],
        date_range=DateRange(start=day, end=day),
        sampling="30s",
        rinex="2",
    )


def test_sirgas_network_and_united_states_are_registered() -> None:
    networks = default_data_network_registry()
    sirgas = networks.get("sirgas")
    assert sirgas.automation_level == AutomationLevel.PARTIAL
    assert {"ramsac_ar", "sirgas_rbmc_br", "sirgas_cl", "rgna_mx"} <= set(sirgas.providers)
    # Only centres currently listed by SIRGAS as national data centres are
    # exposed as selectable national layers.
    assert not {"sirgas_py", "sirgas_ve", "sirgas_gy", "sirgas_sr"} & set(sirgas.providers)
    assert networks.get("united_states").automation_level == AutomationLevel.FULL
    assert networks.get("united_states").providers == ["noaa_ncn"]

    sources = {s.id: s.provider for s in default_regional_source_registry().all("sirgas")}
    assert sources["sirgas_argentina"] == "ramsac_ar"
    assert sources["sirgas_brazil"] == "sirgas_rbmc_br"
    assert sources["sirgas_mexico"] == "rgna_mx"
    assert "sirgas_paraguay" not in sources


def test_registry_contains_americas_providers() -> None:
    registry = default_registry()
    for name in (
        "ramsac_ar", "sirgas_rbmc_br", "sirgas_cl", "rgna_mx",
        "sirgas_bo", "sirgas_co", "sirgas_ec", "sirgas_pe", "sirgas_uy",
        "sirgas_cr", "sirgas_pa", "sirgas_py", "sirgas_ve", "sirgas_gy", "sirgas_sr",
        "noaa_ncn",
    ):
        assert registry.get(name).name == name


def test_chile_csn_bundled_kml_builds_full_map_catalog() -> None:
    provider = ChileCSNProvider()
    stations = asyncio.run(provider.fetch_station_catalog())
    by_code = {station.id[:4]: station for station in stations}

    assert len(stations) == 125
    assert provider.last_station_catalog_stats["mapped_station_count"] == 125
    assert provider.last_station_catalog_stats["csn_catalog_format"] == "KML"
    assert all(station.latitude is not None for station in stations)
    assert all(station.longitude is not None for station in stations)
    assert round(by_code["PTRE"].latitude, 6) == -18.194288
    assert round(by_code["PTRE"].longitude, 6) == -69.574329


def test_uruguay_short_rnx_zip_matches_explicit_rinex_versions() -> None:
    day = date(2025, 1, 1)
    for rinex in ("2", "3", "auto"):
        request = ObservationRequest(
            stations=["UYRI00URY"],
            date_range=DateRange(start=day, end=day),
            sampling="30s",
            rinex=rinex,
        )
        assert UruguaySIRGASProvider._uruguay_file_for_day(
            "uyri0010.rnx.zip",
            station4="UYRI",
            day=day,
            request=request,
        )


def test_uruguay_historical_sftp_finds_uyri_zip(monkeypatch) -> None:
    import sys

    connected: dict[str, object] = {}
    listed: list[str] = []

    class FakeChannel:
        def settimeout(self, _timeout):
            return None

    class FakeSFTP:
        def get_channel(self):
            return FakeChannel()

        def listdir(self, path):
            listed.append(path)
            if path == "/sftpserver/2025/01/01/UYRI":
                return ["uyri0010.rnx.zip"]
            raise OSError(path)

    class FakeSSHClient:
        def set_missing_host_key_policy(self, _policy):
            return None

        def connect(self, host, **kwargs):
            connected["host"] = host
            connected.update(kwargs)

        def open_sftp(self):
            return FakeSFTP()

        def close(self):
            return None

    fake_paramiko = SimpleNamespace(
        SSHClient=FakeSSHClient,
        AutoAddPolicy=lambda: object(),
    )
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)

    day = date(2025, 1, 1)
    request = ObservationRequest(
        stations=["UYRI00URY"],
        date_range=DateRange(start=day, end=day),
        sampling="30s",
        rinex="3",
    )
    files = UruguaySIRGASProvider()._search_archives_sync(request)

    assert connected["host"] == "sftp.igm.gub.uy"
    assert connected["port"] == 2222
    assert listed == ["/sftpserver/2025/01/01/UYRI"]
    assert len(files) == 1
    assert files[0].filename == "uyri0010.rnx.zip"
    assert str(files[0].url) == (
        "sftp://sftp.igm.gub.uy:2222/sftpserver/2025/01/01/UYRI/uyri0010.rnx.zip"
    )


def test_ramsac_station_status_table_builds_catalog() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        if "EstacionesPermanentes" in str(req.url):
            return httpx.Response(
                200,
                text="""
                <table><tr><th>Estación</th><th>Último archivo</th><th>Intervalo</th><th>Estado</th></tr>
                <tr><td>AGGO</td><td>aggo0010.26d.gz</td><td>30</td><td>Operativa</td></tr></table>
                """,
            )
        # SIRGAS coordinate merge is optional in this unit test.
        return httpx.Response(500)

    provider = RAMSACArgentinaProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    stations = asyncio.run(provider.fetch_station_catalog())
    assert [s.id for s in stations] == ["AGGO00ARG"]
    assert stations[0].data_networks == ["argentina", "sirgas"]
    assert stations[0].regional_sources == ["sirgas_argentina"]


def test_mexico_coordinate_catalog_and_current_sftp_metadata() -> None:
    async def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
            <table><tr><th>Estación</th><th>Latitud</th><th>Longitud</th></tr>
            <tr><td>INEG</td><td>21.8560 N</td><td>102.2840 W</td></tr></table>
            """,
        )

    provider = RGNAMexicoProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    stations = asyncio.run(provider.fetch_station_catalog())
    by_id = {station.id: station for station in stations}
    assert "INEG00MEX" in by_id
    assert round(by_id["INEG00MEX"].latitude, 3) == 21.856
    assert round(by_id["INEG00MEX"].longitude, 3) == -102.284
    assert provider.sftp_host == "geodesia2.inegi.org.mx"
    assert provider.sftp_user == "rgnasftp"
    assert provider.sftp_password == "rgnasftp"


def test_mexico_sftp_walker_matches_requested_day() -> None:
    class FakeSFTP:
        def listdir_attr(self, path):
            if path == "/home/rgna/INEG":
                return [SimpleNamespace(filename="2026", st_mode=0o040755)]
            if path == "/home/rgna/INEG/2026":
                return [SimpleNamespace(filename="ineg0010.26d.gz", st_mode=0o100644)]
            return []

    found = list(
        RGNAMexicoProvider._walk_matching_day(
            FakeSFTP(), "/home/rgna/INEG", "INEG", date(2026, 1, 1)
        )
    )
    assert found == [("/home/rgna/INEG/2026/ineg0010.26d.gz", "ineg0010.26d.gz")]


def test_noaa_ncn_builds_user_proven_ngs_primary_and_aws_fallback_without_listing() -> None:
    provider = NOAANCNProvider()
    files = asyncio.run(provider.search_observations(_request("ABCD")))
    assert len(files) == 1
    primary = files[0]
    assert str(primary.url) == (
        "https://geodesy.noaa.gov/corsdata/rinex/2026/001/abcd/abcd0010.26d.gz"
    )
    assert str(primary.fallback_candidates[0].url) == (
        "https://noaa-cors-pds.s3.amazonaws.com/rinex/2026/001/abcd/abcd0010.26d.gz"
    )


def test_sirgas_country_parser_does_not_clone_other_country_rows() -> None:
    provider = BoliviaSIRGASProvider()
    html = """
    <table>
      <tr><td>BOGT00COL</td><td>Colombia</td><td>4.640 N</td><td>74.080 W</td></tr>
      <tr><td>LPZ100BOL</td><td>Bolivia</td><td>16.500 S</td><td>68.150 W</td></tr>
      <tr><td>AREQ00PER</td><td>Peru</td><td>16.466 S</td><td>71.493 W</td></tr>
    </table>
    """
    stations = provider._parse_sirgas_station_rows(html)
    assert [station.id for station in stations] == ["LPZ100BOL"]
    assert round(stations[0].latitude, 3) == -16.5
    assert round(stations[0].longitude, 3) == -68.15


def test_sirgas_country_parser_can_use_country_name_for_legacy_4char_rows() -> None:
    provider = BoliviaSIRGASProvider()
    html = """
    <table>
      <tr><td>BOGT</td><td>Colombia</td><td>4.640 N</td><td>74.080 W</td></tr>
      <tr><td>LPZ1</td><td>Bolivia</td><td>16.500 S</td><td>68.150 W</td></tr>
    </table>
    """
    stations = provider._parse_sirgas_station_rows(html)
    assert [station.id for station in stations] == ["LPZ100BOL"]


def test_ramsac_kml_coordinates_are_parsed() -> None:
    kml = """
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>AGGO</name><Point><coordinates>-58.1395,-34.8739,42.1</coordinates></Point></Placemark>
    </Document></kml>
    """
    coords = RAMSACArgentinaProvider._parse_ramsac_kml(kml)
    assert coords["AGGO"] == (-34.8739, -58.1395, 42.1)


def test_rbmc_xml_catalog_carries_map_coordinates() -> None:
    from gnssgo.providers.americas import SIRGASRBMCProvider

    provider = SIRGASRBMCProvider()
    xml = """
    <estacoes>
      <estacao><codigo>UFPR</codigo><latitude>-25.448</latitude><longitude>-49.231</longitude><status>Ativa</status></estacao>
      <estacao><codigo>RECF</codigo><lat>8 3 0 S</lat><lon>34 57 0 W</lon></estacao>
    </estacoes>
    """
    stations = provider._parse_station_xml(xml)
    by_id = {station.id: station for station in stations}
    assert round(by_id["UFPR00BRA"].latitude, 3) == -25.448
    assert round(by_id["UFPR00BRA"].longitude, 3) == -49.231
    assert round(by_id["RECF00BRA"].latitude, 3) == -8.05
    assert round(by_id["RECF00BRA"].longitude, 3) == -34.95


def test_sirgas_crd_parser_extracts_ecef_coordinates() -> None:
    from gnssgo.providers.americas import _parse_sirgas_crd_text

    # Approximate ECEF coordinates for a point in South America.  The parser is
    # intentionally layout agnostic; an operational CRD line can include extra
    # sequence/epoch columns before XYZ.
    text = "  17 BOGT00COL 2026.0 1744517.3 -6116052.1 512581.4 0.001 0.001 0.001\n"
    coords = _parse_sirgas_crd_text(text)
    assert "BOGT00COL" in coords
    lat, lon, _ = coords["BOGT00COL"]
    assert -90.0 <= lat <= 90.0
    assert -180.0 <= lon <= 180.0


def test_sirgas_https_archive_enriches_country_catalog(monkeypatch) -> None:
    import gnssgo.providers.americas as americas

    # Ensure an earlier failed FTP/HTTPS attempt cannot mask this test.
    americas._SIRGAS_COORD_CACHE = None
    americas._SIRGAS_COORD_CACHE_SOURCE = ""
    americas._SIRGAS_COORD_CACHE_AT = 0.0

    # Create enough synthetic CRD entries to satisfy the production sanity gate.
    crd_lines = []
    for idx in range(21):
        code = f"B{idx:03d}"[-4:]
        station = f"{code}00BOL"
        crd_lines.append(f"{station} 1744517.3 -6116052.1 512581.4")
    crd = "\n".join(crd_lines)

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.rstrip("/").endswith("/SIRGAS"):
            return httpx.Response(200, text='<a href="2403/">2403/</a>')
        if url.rstrip("/").endswith("/SIRGAS/2403"):
            return httpx.Response(200, text='<a href="sir26P2403.crd">sir26P2403.crd</a>')
        if url.endswith("sir26P2403.crd"):
            return httpx.Response(200, text=crd)
        return httpx.Response(404)

    provider = BoliviaSIRGASProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    coords, source = asyncio.run(americas._sirgas_weekly_coordinates_for(provider))
    assert len(coords) >= 21
    assert source.endswith("/2403/sir26P2403.crd")


def test_ramsac_kml_extended_data_code_is_parsed() -> None:
    kml = """
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark>
        <ExtendedData><Data name="codigo"><value>ABRA</value></Data></ExtendedData>
        <Point><coordinates>-65.486,-23.215,1012.0</coordinates></Point>
      </Placemark>
    </Document></kml>
    """
    coords = RAMSACArgentinaProvider._parse_ramsac_kml(kml, known_codes={"ABRA"})
    assert coords["ABRA"] == (-23.215, -65.486, 1012.0)


def test_rbmc_weekly_coordinates_fill_missing_map_points(monkeypatch) -> None:
    import gnssgo.providers.americas as americas
    from gnssgo.providers.americas import SIRGASRBMCProvider

    async def base_catalog(self):
        return [
            self._rbmc_station("ALAR", None, None, source="test"),
            self._rbmc_station("BRAZ", -15.9, -47.9, source="test"),
        ]

    async def weekly(_provider):
        return {"ALAR": (-9.75, -36.65, 250.0)}, "https://www.sirgas.org/archive/test.crd"

    monkeypatch.setattr(americas.RBMCProvider, "fetch_station_catalog", base_catalog)
    monkeypatch.setattr(americas, "_sirgas_weekly_coordinates_for", weekly)
    provider = SIRGASRBMCProvider()
    stations = asyncio.run(provider.fetch_station_catalog())
    by_code = {station.id[:4]: station for station in stations}
    assert round(by_code["ALAR"].latitude, 2) == -9.75
    assert round(by_code["ALAR"].longitude, 2) == -36.65
    assert provider.last_station_catalog_stats["mapped_station_count"] == 2


def test_sirgas_country_parser_rejects_unrelated_numeric_columns_as_coordinates() -> None:
    provider = BoliviaSIRGASProvider()
    html = """
    <table>
      <tr><td>LPZ100BOL</td><td>Bolivia</td><td>2026</td><td>228</td><td>12</td><td>30</td></tr>
    </table>
    """
    stations = provider._parse_sirgas_station_rows(html)
    assert [station.id for station in stations] == ["LPZ100BOL"]
    assert stations[0].latitude is None
    assert stations[0].longitude is None


def test_sirgas_coordinate_lookup_repairs_bad_table_coordinate() -> None:
    from gnssgo.models import Station
    from gnssgo.providers.americas import _apply_coordinate_lookup

    station = Station(
        id="LPZ100BOL",
        latitude=12.0,
        longitude=30.0,
        country="BOL",
        data_networks=["sirgas"],
        regional_sources=["sirgas_bolivia"],
    )
    mapped = _apply_coordinate_lookup(
        [station],
        {"LPZ100BOL": (-16.50, -68.15, 3650.0)},
        source="test-crd",
    )
    assert mapped == 1
    assert station.latitude == -16.50
    assert station.longitude == -68.15
    assert station.metadata["coordinate_source"] == "test-crd"


def test_mexico_hourly_zip_matcher_distinguishes_rinex2_and_rinex3() -> None:
    day = date(2021, 6, 1)  # DOY 152, matches INEG's documented naming example.
    request_v2 = ObservationRequest(
        stations=["INEG00MEX"],
        date_range=DateRange(start=day, end=day),
        sampling="1s",
        rinex="2",
    )
    request_v3 = request_v2.model_copy(update={"rinex": "3"})

    assert RGNAMexicoProvider._mexico_file_for_day(
        "INEG152a.zip", station4="INEG", day=day, request=request_v2
    )
    assert not RGNAMexicoProvider._mexico_file_for_day(
        "INEG152a_304.zip", station4="INEG", day=day, request=request_v2
    )
    assert RGNAMexicoProvider._mexico_file_for_day(
        "INEG152a_304.zip", station4="INEG", day=day, request=request_v3
    )
    assert not RGNAMexicoProvider._mexico_file_for_day(
        "INEG152a.zip", station4="INEG", day=day, request=request_v3
    )


def test_chile_plan_for_selected_stations_uses_official_day_listing() -> None:
    calls: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(
            200,
            text=(
                '<a href="ptre0010.26d.Z">ptre0010.26d.Z</a>'
                '<a href="achs0010.26d.Z">achs0010.26d.Z</a>'
                '<a href="other0010.26d.Z">other0010.26d.Z</a>'
            ),
        )

    provider = ChileCSNProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    request = ObservationRequest(
        stations=["PTRE00CHL", "ACHS00CHL"],
        date_range=DateRange(start=date(2026, 1, 1), end=date(2026, 1, 1)),
        sampling="1s",
        rinex="2",
    )
    files = asyncio.run(provider.search_observations(request))

    assert {item.station for item in files} == {"PTRE00CHL", "ACHS00CHL"}
    assert calls == ["https://gps.csn.uchile.cl/data/2026/001/"]
    assert {item.filename for item in files} == {"ptre0010.26d.Z", "achs0010.26d.Z"}
    assert all(item.metadata["max_parallel_downloads"] == "1" for item in files)
    assert all(item.metadata["min_interval_seconds"] == "5.0" for item in files)
    assert all(item.metadata["http_transport"] == "chromedriver" for item in files)
    assert all(item.metadata["csn_browser_get"] == "1" for item in files)
    assert all(item.metadata["discovery"] == "official_day_directory" for item in files)
    assert all("http_prime_url" not in item.metadata for item in files)
    assert all("http_referer" not in item.metadata for item in files)


def test_daemon_bounded_discovery_has_hard_wall_clock_timeout() -> None:
    started = time.monotonic()
    with pytest.raises(ProviderError):
        _run_daemon_bounded(
            lambda: time.sleep(1.0),
            timeout=0.05,
            timeout_message="test timeout",
        )
    assert time.monotonic() - started < 0.5


def test_chile_accepts_real_csn_short_hatanaka_name_at_1hz() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='<a href="antc0010.26d.Z">antc0010.26d.Z</a>',
        )

    provider = ChileCSNProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    request = ObservationRequest(
        stations=["ANTC00CHL"],
        date_range=DateRange(start=date(2026, 1, 1), end=date(2026, 1, 1)),
        sampling="1s",
        rinex="2",
    )
    files = asyncio.run(provider.search_observations(request))
    assert len(files) == 1
    assert files[0].filename == "antc0010.26d.Z"
    assert str(files[0].url) == (
        "https://gps.csn.uchile.cl/data/2026/001/antc0010.26d.Z"
    )
    assert files[0].metadata["sampling"] == "01S"


def test_uruguay_plan_is_network_free_and_builds_known_uyri_path() -> None:
    day = date(2025, 1, 1)
    request = ObservationRequest(
        stations=["UYRI00URY"],
        date_range=DateRange(start=day, end=day),
        sampling="30s",
        rinex="auto",
    )
    provider = UruguaySIRGASProvider()
    files = asyncio.run(provider.search_observations(request))
    assert len(files) == 1
    assert files[0].filename == "uyri0010.rnx.zip"
    assert str(files[0].url) == (
        "sftp://sftp.igm.gub.uy:2222/sftpserver/2025/01/01/UYRI/uyri0010.rnx.zip"
    )
    assert files[0].metadata["discovery"] == "deterministic_official_layout"


def test_mexico_plan_is_network_free_and_builds_hourly_candidates() -> None:
    day = date(2026, 1, 1)
    request = ObservationRequest(
        stations=["INEG00MEX"],
        date_range=DateRange(start=day, end=day),
        sampling="15s",
        rinex="3",
    )
    provider = RGNAMexicoProvider()
    files = asyncio.run(provider.search_observations(request))
    assert len(files) == 24
    assert files[0].filename == "INEG001a_304.zip"
    assert str(files[0].url) == (
        "sftp://geodesia2.inegi.org.mx:22/home/rgna/INEG/01ENE/INEG001a_304.zip"
    )
    assert files[-1].filename == "INEG001x_304.zip"
    assert len(files[0].fallback_candidates) == 1
    assert str(files[0].fallback_candidates[0].url).startswith("ftp://geodesia.inegi.org.mx/")
    assert len(files[0].metadata["sftp_path_candidates"].split("|")) == 6


def test_mexico_auto_prefers_single_rinex3_copy_per_hour() -> None:
    day = date(2025, 1, 1)
    request = ObservationRequest(
        stations=["CHET00MEX", "INEG00MEX", "MEXI00MEX"],
        date_range=DateRange(start=day, end=day),
        sampling="15s",
        rinex="auto",
    )
    files = asyncio.run(RGNAMexicoProvider().search_observations(request))

    assert len(files) == 72  # 3 stations x 24 hourly sessions, not x2 RINEX versions
    assert all(item.filename.endswith("_304.zip") for item in files)
    assert not any(
        item.filename.endswith(".zip") and not item.filename.endswith("_304.zip")
        for item in files
    )


def test_mexico_default_auto_uses_one_official_daily_file_per_station() -> None:
    day = date(2025, 1, 1)
    request = ObservationRequest(
        stations=["CHET00MEX", "INEG00MEX"],
        date_range=DateRange(start=day, end=day),
        sampling=None,
        rinex="auto",
    )
    files = asyncio.run(RGNAMexicoProvider().search_observations(request))
    assert len(files) == 2
    assert {item.filename for item in files} == {"chet0010.25o.gz", "ineg0010.25o.gz"}
    assert all(str(x.url).startswith("sftp://geodesia2.inegi.org.mx:22/") for x in files)
    assert all("/01ENE/" in str(x.url) for x in files)
    assert all(len(x.fallback_candidates) == 3 for x in files)
    assert all(str(x.fallback_candidates[0].url).startswith("ftp://geodesia.inegi.org.mx/") for x in files)
    assert all(x.fallback_candidates[1].filename.endswith(".25d.gz") for x in files)
    assert all(str(x.fallback_candidates[2].url).startswith("ftp://geodesia.inegi.org.mx/") for x in files)
    assert all(x.metadata["sampling"] == "30S" for x in files)


def test_uruguay_sftp_candidate_carries_all_chroot_paths() -> None:
    day = date(2025, 1, 1)
    request = ObservationRequest(
        stations=["UYRI00URY"],
        date_range=DateRange(start=day, end=day),
        sampling="30s",
        rinex="auto",
    )
    item = asyncio.run(UruguaySIRGASProvider().search_observations(request))[0]
    paths = item.metadata["sftp_path_candidates"].split("|")
    assert "/sftpserver/2025/01/01/UYRI/uyri0010.rnx.zip" in paths
    assert "/2025/01/01/UYRI/uyri0010.rnx.zip" in paths
    assert item.metadata["curl_fallback"] == "1"


def test_uruguay_current_ftp_prefers_documented_regna_station_root() -> None:
    from ftplib import error_perm

    tried: list[str] = []

    class FakeFTP:
        def cwd(self, path: str):
            tried.append(path)
            if path in {"/", "/regna/UYLA"}:
                return None
            raise error_perm("550 no such directory")

    root = UruguaySIRGASProvider._find_current_ftp_station_root(FakeFTP(), "UYLA")
    assert root == "/regna/UYLA"
    assert tried[0] == "/regna/UYLA"


def test_uruguay_current_year_never_falls_back_to_historical_sftp(monkeypatch) -> None:
    from gnssgo.models import RemoteFile

    current_day = date(date.today().year, 1, 18)
    request = ObservationRequest(
        stations=["UYLA00URY"],
        date_range=DateRange(start=current_day, end=current_day),
        sampling="30s",
        rinex="auto",
    )
    provider = UruguaySIRGASProvider()
    item = RemoteFile(
        provider=provider.name,
        url=(
            f"ftp://pp.igm.gub.uy/hatanaka/{current_day.year}/018/"
            f"uyla0180.{current_day.year % 100:02d}d.gz"
        ),
        filename=f"uyla0180.{current_day.year % 100:02d}d.gz",
        data_type="obs",
        station="UYLA00URY",
        date=current_day,
    )
    monkeypatch.setattr(provider, "_search_current_hatanaka_sync", lambda _request: [item])
    files = asyncio.run(provider.search_observations(request))
    assert [str(remote.url) for remote in files] == [str(item.url)]
    assert not any(str(remote.url).startswith("sftp://") for remote in files)


def test_uruguay_current_hatanaka_day_listing_supports_short_and_long_names(monkeypatch) -> None:
    import gnssgo.providers.americas as americas

    day = date(date.today().year, 1, 18)
    yy = day.year % 100
    directory = f"/hatanaka/{day.year}/018"

    class FakeFTP:
        def __init__(self, _host, timeout=0):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def login(self):
            return None

        def nlst(self, path=None):
            assert path == directory
            return [
                f"{directory}/uyar0180.{yy:02d}d.gz",
                f"{directory}/UYFD00XXX_S_{day.year}0180000_01D_15S_MO.crx.gz",
                f"{directory}/UYFD00XXX_S_{day.year}0180000_01D_15S_MN.rnx.gz",
            ]

    monkeypatch.setattr(americas, "FTP", FakeFTP)
    request = ObservationRequest(
        stations=["UYAR00URY", "UYFD00XXX"],
        date_range=DateRange(start=day, end=day),
        sampling=None,
        rinex="auto",
    )
    files = UruguaySIRGASProvider()._search_current_hatanaka_sync(request)
    assert {x.filename for x in files} == {
        f"uyar0180.{yy:02d}d.gz",
        f"UYFD00XXX_S_{day.year}0180000_01D_15S_MO.crx.gz",
    }
    assert all("/hatanaka/" in str(x.url) for x in files)


def test_uruguay_current_hatanaka_has_deterministic_shortname_fallback(monkeypatch) -> None:
    day = date(date.today().year, 1, 18)
    request = ObservationRequest(
        stations=["UYAR00URY"],
        date_range=DateRange(start=day, end=day),
        sampling=None,
        rinex="auto",
    )
    provider = UruguaySIRGASProvider()

    def fail_listing(_request):
        raise OSError("FTP listing unavailable")

    monkeypatch.setattr(provider, "_search_current_hatanaka_sync", fail_listing)
    files = asyncio.run(provider.search_observations(request))
    assert len(files) == 1
    assert files[0].filename == f"uyar0180.{day.year % 100:02d}d.gz"
    assert str(files[0].url) == (
        f"ftp://pp.igm.gub.uy/hatanaka/{day.year}/018/"
        f"uyar0180.{day.year % 100:02d}d.gz"
    )


def test_uruguay_current_ftp_walk_treats_symlink_like_entries_as_directories() -> None:
    day = date(2026, 1, 18)
    request = ObservationRequest(
        stations=["UYAR00URY"],
        date_range=DateRange(start=day, end=day),
        sampling="30s",
        rinex="2",
    )

    class FakeFTP:
        def __init__(self):
            self.cwd_path = "/"

        def mlsd(self, path):
            if path == "/regna/UYAR":
                return [("Diarios", {"type": "OS.unix=slink"})]
            if path == "/regna/UYAR/Diarios":
                return [("2026", {"type": "dir"})]
            if path == "/regna/UYAR/Diarios/2026":
                return [("uyar0180.26d.Z", {"type": "file"})]
            return []

        def cwd(self, path):
            if path in {"/", "/regna/UYAR/Diarios"}:
                self.cwd_path = path
                return None
            raise error_perm("550")

    provider = UruguaySIRGASProvider()
    found = list(provider._walk_current_ftp_station_day(
        FakeFTP(), "/regna/UYAR", "UYAR", day, request
    ))
    assert found == [("/regna/UYAR/Diarios/2026/uyar0180.26d.Z", "uyar0180.26d.Z")]


def test_chile_selected_station_falls_back_to_exact_candidate_when_day_listing_is_blocked() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    provider = ChileCSNProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    request = ObservationRequest(
        stations=["ARJF00CHL"],
        date_range=DateRange(start=date(2026, 1, 18), end=date(2026, 1, 18)),
        sampling="1s",
        rinex="2",
    )
    files = asyncio.run(provider.search_observations(request))

    assert len(files) == 1
    assert files[0].station == "ARJF00CHL"
    assert files[0].filename == "arjf0180.26d.Z"
    assert str(files[0].url) == (
        "https://gps.csn.uchile.cl/data/2026/018/arjf0180.26d.Z"
    )
    assert files[0].metadata["discovery"] == "explicit_station_candidate"
    assert files[0].metadata["availability"] == "verify_on_download"
    assert files[0].metadata["http_transport"] == "chromedriver"
