from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest

from gnssgo.data_networks import AutomationLevel, default_data_network_registry
from gnssgo.models import DateRange, ObservationRequest
from gnssgo.provider_info import provider_info
from gnssgo.providers import default_registry
from gnssgo.providers.regional_expansion import (
    CACSCanadaProvider,
    CHAINCanadaProvider,
    DPGANetherlandsProvider,
    NSGINetherlandsProvider,
    APOSAustriaProvider,
    BelgiumGNSSProvider,
    ReNEPPortugalProvider,
    NOAANCNProvider,
    NOAGreeceProvider,
    ItalyEPOSProvider,
    PolandEPOSProvider,
    RomaniaEPOSProvider,
    UnitedKingdomEPOSProvider,
    SwedenEPOSProvider,
    FinlandEPOSProvider,
    SwitzerlandEPOSProvider,
    RENAGFranceProvider,
    SatRefHKProvider,
)


def request(
    station: str,
    *,
    sampling: str = "30s",
    rinex: str = "auto",
    day: date = date(2026, 8, 1),
) -> ObservationRequest:
    return ObservationRequest(
        stations=[station],
        date_range=DateRange(start=day, end=day),
        sampling=sampling,
        rinex=rinex,
    )


def test_registry_contains_new_regional_networks_and_providers() -> None:
    networks = default_data_network_registry()
    providers = default_registry()

    assert networks.get("netherlands").providers == ["nsgi_nl", "dpga_nl"]
    assert networks.get("italy").providers == ["epos_it"]
    assert networks.get("canada").providers == ["cacs_ca", "chain_ca"]
    assert networks.get("france").providers == ["renag_fr", "rgp_fr"]
    assert networks.get("mongolia").automation_level == AutomationLevel.AUTH_REQUIRED
    assert networks.get("korea").providers == ["kasi_kr", "ngii_kr"]
    assert networks.get("united_states").providers == ["noaa_ncn"]

    for provider_id in (
        "dpga_nl",
        "nsgi_nl",
        "apos_at",
        "renep_pt",
        "ring_it",
        "epos_it",
        "epos_pl",
        "epos_ro",
        "epos_uk",
        "epos_se",
        "epos_fi",
        "epos_ch",
        "chain_ca",
        "cacs_ca",
        "renag_fr",
        "monpos_mn",
        "satref_hk",
        "trignet_za",
        "kasi_kr",
        "ngii_kr",
        "noaa_ncn",
        "epos_hu",
        "epos_cz",
        "epos_mt",
        "epos_me",
        "ramsac_ar",
        "sirgas_rbmc_br",
        "sirgas_cl",
        "rgna_mx",
        "sirgas_py",
        "sirgas_ve",
    ):
        assert providers.get(provider_id).name == provider_id




def test_canada_uses_cacsa_as_primary_source_and_hides_wcda_from_network() -> None:
    networks = default_data_network_registry()
    canada = networks.get("canada")
    assert canada.providers == ["cacs_ca", "chain_ca"]

    cacs = provider_info("cacs_ca")
    assert cacs.url == "https://cacsa.nrcan.gc.ca/"
    assert "30-second" in cacs.description
    assert "1-second" in cacs.description
    assert "RINEX 3/4" in cacs.description

    chain = provider_info("chain_ca")
    assert "chain-new.chain-project.net" in chain.url

def test_dpga_station_catalog_converts_official_ecef_coordinates() -> None:
    async def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
            <html><body><pre>
            AMEL 13540M001 3787664.3131 382392.2606 5100339.6132
            DELF 13502M004 3924687.7159 301132.7668 5001910.7812
            </pre></body></html>
            """,
        )

    provider = DPGANetherlandsProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    stations = asyncio.run(provider.fetch_station_catalog())
    amel = next(station for station in stations if station.id == "AMEL00NLD")

    assert amel.country == "NLD"
    assert amel.data_networks == ["netherlands"]
    assert amel.providers == ["dpga_nl"]
    assert amel.domes == "13540M001"
    assert amel.latitude == pytest.approx(53.45, abs=0.3)
    assert amel.longitude == pytest.approx(5.68, abs=0.3)


def test_dpga_daily_and_highrate_directory_discovery() -> None:
    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if "/highrate/" in str(req.url):
            filename = "AMEL00NLD_R_20262130000_15M_01S_MO.crx.gz"
        else:
            filename = "AMEL00NLD_R_20262130000_01D_30S_MO.crx.gz"
        return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')

    provider = DPGANetherlandsProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    daily = asyncio.run(provider.search_observations(request("AMEL00NLD", rinex="3")))
    highrate = asyncio.run(
        provider.search_observations(request("AMEL00NLD", sampling="01S", rinex="3"))
    )

    assert daily[0].filename.endswith("_01D_30S_MO.crx.gz")
    assert highrate[0].filename.endswith("_15M_01S_MO.crx.gz")
    assert any("/dpga/rinex/2026/213/" in url for url in seen)
    assert any("/dpga/rinex/highrate/2026/213/" in url for url in seen)


def test_renag_uses_rinex3_30s_and_1s_archives() -> None:
    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        sampling = "01S" if "rinex3_1s" in str(req.url) else "30S"
        duration = "01H" if sampling == "01S" else "01D"
        filename = f"ABMF00GLP_R_20262130000_{duration}_{sampling}_MO.crx.gz"
        return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')

    provider = RENAGFranceProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    daily = asyncio.run(provider.search_observations(request("ABMF00GLP", rinex="3")))
    highrate = asyncio.run(
        provider.search_observations(request("ABMF00GLP", rinex="3", sampling="01S"))
    )

    assert daily and highrate
    assert any("/pub/rinex3/2026/213/" in url for url in seen)
    assert any("/pub/rinex3_1s/2026/213/" in url for url in seen)


def test_chain_station_catalog_and_legacy_daily_discovery() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "stations" in url:
            return httpx.Response(
                200,
                text="""
                <table>
                <tr><th>Name</th><th>Abbr</th><th>Lat</th><th>Lon</th><th>Instrument</th><th>Model</th><th>Status</th><th>Real-time</th><th>Complete</th></tr>
                <tr><td>Resolute</td><td>RES</td><td>74.6908</td><td>265.106</td><td>GISTM/GPS</td><td>GSV4004B</td><td>Active</td><td>Complete</td><td>Near Real-time</td></tr>
                <tr><td>Ionosonde-only</td><td>RB</td><td>Ionosonde</td><td>CADI</td></tr>
                </table>
                """,
            )
        filename = "res2130.26d.gz"
        return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')

    provider = CHAINCanadaProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    stations = asyncio.run(provider.fetch_station_catalog())
    files = asyncio.run(provider.search_observations(request("RES", rinex="2")))

    assert len(stations) == 1
    assert stations[0].id == "RES"
    assert stations[0].longitude == pytest.approx(-94.894, abs=0.001)
    assert stations[0].data_networks == ["canada"]
    assert stations[0].regional_sources == ["chain_ca"]
    assert stations[0].sampling_rates == ["01S", "30S"]
    assert stations[0].rinex_versions == ["2", "3"]
    assert stations[0].metadata["station_name"] == "Resolute"
    assert files[0].filename == "res2130.26d.gz"
    assert files[0].provider == "chain_ca"
    assert files[0].metadata["chain_dataset"] == "GPS_RINEX2"


def test_chain_auto_prefers_modern_rinex3_and_keeps_legacy_fallback() -> None:
    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen.append(url)
        if "/gnss/data/daily/" in url:
            filename = "RES000CAN_R_20262130000_01D_30S_MO.crx.gz"
            return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')
        if "/gps/data/daily/" in url and url.rstrip("/").endswith("26d"):
            filename = "res2130.26d.gz"
            return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')
        return httpx.Response(200, text="")

    provider = CHAINCanadaProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    files = asyncio.run(provider.search_observations(request("RES", rinex="auto")))

    assert len(files) == 1
    assert files[0].filename.endswith("_MO.crx.gz")
    assert files[0].metadata["chain_dataset"] == "GNSS_RINEX3"
    assert len(files[0].fallback_candidates) == 1
    assert files[0].fallback_candidates[0].filename == "res2130.26d.gz"
    assert files[0].fallback_candidates[0].metadata["chain_dataset"] == "GPS_RINEX2"
    assert any("/gnss/data/daily/2026/213/" in url for url in seen)
    assert any("/gps/data/daily/2026/213/26d/" in url for url in seen)


def test_chain_explicit_rinex3_and_rinex4_rules() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        if "/gnss/data/daily/" in str(req.url):
            filename = "RES000CAN_R_20262130000_01D_30S_MO.crx.gz"
            return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')
        return httpx.Response(200, text="")

    provider = CHAINCanadaProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    rinex3 = asyncio.run(provider.search_observations(request("RES", rinex="3")))
    rinex4 = asyncio.run(provider.search_observations(request("RES", rinex="4")))

    assert len(rinex3) == 1
    assert rinex3[0].metadata["rinex_family"] == "3"
    assert rinex4 == []


def test_chain_highrate_uses_modern_and_legacy_one_second_trees() -> None:
    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen.append(url)
        if "/gnss/data/highrate/" in url:
            filename = "RES000CAN_R_20262130000_01H_01S_MO.crx.gz"
            return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')
        if "/gps/data/highrate/" in url:
            filename = "res213a.26o.gz"
            return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')
        return httpx.Response(200, text="")

    provider = CHAINCanadaProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    files = asyncio.run(
        provider.search_observations(request("RES", rinex="auto", sampling="01S"))
    )

    assert files
    assert files[0].metadata["chain_dataset"] == "GNSS_RINEX3"
    assert any("/gnss/data/highrate/2026/213/" in url for url in seen)
    assert any("/gps/data/highrate/2026/213/" in url for url in seen)


def test_satref_uses_sampling_subdirectory_and_builds_catalog_from_day_index() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/hkkt/5s/" in url:
            filename = "HKKT00HKG_R_20262130000_01H_05S_MO.crx.gz"
            return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')
        if url.rstrip("/").endswith("/2026/213"):
            return httpx.Response(200, text='<a href="hkkt/">hkkt/</a><a href="hkqt/">hkqt/</a>')
        # Catalog probes the current/recent date. Returning two station directories is
        # sufficient because the provider only needs the day index for station IDs.
        return httpx.Response(200, text='<a href="hkkt/">hkkt/</a><a href="hkqt/">hkqt/</a>')

    provider = SatRefHKProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    files = asyncio.run(
        provider.search_observations(request("HKKT00HKG", sampling="05S", rinex="3"))
    )
    stations = asyncio.run(provider.fetch_station_catalog())

    assert files[0].filename.endswith("_01H_05S_MO.crx.gz")
    station_ids = {station.id for station in stations}
    # The live day index is merged into the packaged official SatRef coordinate
    # snapshot, so current directory stations must be present without discarding
    # the full offline map catalog.
    assert {"HKKT00HKG", "HKQT00HKG"}.issubset(station_ids)
    assert len(stations) >= 19
    assert all(station.data_networks == ["hong_kong"] for station in stations)


def test_noaa_ncn_filters_us_station_list_and_discovers_daily_rinex2() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        if "dates_sites.txt" in str(req.url):
            return httpx.Response(
                200,
                text=(
                    "US TX Corpus Christi CORC 2003016 NULL 30 60 TXDOT Operational\n"
                    "CA ON Canadian Station ALBH 2001001 NULL 30 60 NRCan Operational\n"
                ),
            )
        return httpx.Response(200, text='<a href="corc2130.26d.gz">obs</a>')

    provider = NOAANCNProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    stations = asyncio.run(provider.fetch_station_catalog())
    files = asyncio.run(provider.search_observations(request("CORC", rinex="2")))

    assert [station.id for station in stations] == ["CORC"]
    assert stations[0].country == "USA"
    assert stations[0].data_networks == ["united_states"]
    assert files[0].filename == "corc2130.26d.gz"
    assert files[0].url.endswith("/2026/213/corc/corc2130.26d.gz")


def test_cacs_daily_directory_uses_yydoy_yyd_layout() -> None:
    async def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="")

    provider = CACSCanadaProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    assert provider._daily_directory(date(2026, 1, 1)).endswith("/26001/26d/")
    assert provider._daily_directory(date(2025, 12, 31)).endswith("/25365/25d/")


def test_cacs_auto_prefers_mo_and_keeps_d_as_same_station_fallback() -> None:
    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(
            200,
            text="""
            <a href="ALGO00CAN_R_20260010000_01D_30S_MO.crx.gz">modern</a>
            <a href="algo0010.26d.gz">legacy</a>
            <a href="STJO00CAN_R_20260010000_01D_30S_MO.crx.gz">other modern</a>
            <a href="pacl0010.26d.gz">legacy only</a>
            """,
        )

    provider = CACSCanadaProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    files = asyncio.run(
        provider.search_observations(
            request("ALGO00CAN", day=date(2026, 1, 1), rinex="auto")
        )
    )

    assert seen == ["https://cacsa.nrcan.gc.ca/gps/data/gpsdata/26001/26d/"]
    assert len(files) == 1
    assert files[0].filename == "ALGO00CAN_R_20260010000_01D_30S_MO.crx.gz"
    assert files[0].metadata["cacs_variant"] == "rinex34"
    assert len(files[0].fallback_candidates) == 1
    assert files[0].fallback_candidates[0].filename == "algo0010.26d.gz"
    assert (
        files[0].metadata["logical_id"]
        == files[0].fallback_candidates[0].metadata["logical_id"]
    )

    stats = provider.last_discovery_stats["2026-01-01"]
    assert stats == {
        "rinex34_stations": 2,
        "rinex2_stations": 2,
        "overlap_stations": 1,
        "rinex34_only": 1,
        "rinex2_only": 1,
        "unique_stations": 3,
    }


def test_cacs_rinex_selection_allows_unequal_mo_and_d_station_sets() -> None:
    async def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
            <a href="ALGO00CAN_R_20260010000_01D_30S_MO.crx.gz">modern</a>
            <a href="algo0010.26d.Z">legacy</a>
            <a href="PACL00CAN_R_20260010000_01D_30S_MO.crx.gz">modern only</a>
            <a href="stjo0010.26d.Z">legacy only</a>
            """,
        )

    provider = CACSCanadaProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    v2 = asyncio.run(
        provider.search_observations(
            request("ALGO00CAN", day=date(2026, 1, 1), rinex="2")
        )
    )
    v3 = asyncio.run(
        provider.search_observations(
            request("ALGO00CAN", day=date(2026, 1, 1), rinex="3")
        )
    )
    auto_modern_only = asyncio.run(
        provider.search_observations(
            request("PACL00CAN", day=date(2026, 1, 1), rinex="auto")
        )
    )
    auto_legacy_only = asyncio.run(
        provider.search_observations(
            request("STJO", day=date(2026, 1, 1), rinex="auto")
        )
    )

    assert [item.filename for item in v2] == ["algo0010.26d.Z"]
    assert [item.filename for item in v3] == [
        "ALGO00CAN_R_20260010000_01D_30S_MO.crx.gz"
    ]
    assert [item.filename for item in auto_modern_only] == [
        "PACL00CAN_R_20260010000_01D_30S_MO.crx.gz"
    ]
    assert [item.filename for item in auto_legacy_only] == ["stjo0010.26d.Z"]


def test_cacs_public_daily_adapter_is_registered_not_auth_placeholder() -> None:
    provider = default_registry().get("cacs_ca")
    assert isinstance(provider, CACSCanadaProvider)
    caps = provider.capabilities()
    assert caps.observations is True
    assert caps.authentication_required is False
    assert caps.station_metadata is True
    assert provider_info("cacs_ca").access == "Public HTTPS archive / Web"


def test_cacs_station_catalog_uses_official_station_logs_and_coordinates() -> None:
    log_algo = """
Four Character ID                     : ALGO
Nine Character ID                     : ALGO00CAN
IERS DOMES Number                     : 40104M002
Site Name                             : Algonquin Radio Observatory
X coordinate (m)                      : 918129.0
Y coordinate (m)                      : -4346071.0
Z coordinate (m)                      : 4561978.0
"""
    log_bake = """
Four Character ID                     : BAKE
Nine Character ID                     : BAKE00CAN
IERS DOMES Number                     : 40110M001
Site Name                             : Baker Lake
X coordinate (m)                      : 2649223.0
Y coordinate (m)                      : -727903.0
Z coordinate (m)                      : 5733074.0
"""

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.endswith('/gps/station_logs/'):
            return httpx.Response(
                200,
                text=(
                    '<a href="algo_20260101.log">algo</a>'
                    '<a href="bake_20260101.log">bake</a>'
                ),
            )
        if url.endswith('algo_20260101.log'):
            return httpx.Response(200, text=log_algo)
        if url.endswith('bake_20260101.log'):
            return httpx.Response(200, text=log_bake)
        if '/gps/data/gpsdata/' in url:
            return httpx.Response(
                200,
                text=(
                    '<a href="ALGO00CAN_R_20260010000_01D_30S_MO.crx.gz">algo</a>'
                    '<a href="bake0010.26d.Z">bake</a>'
                ),
            )
        return httpx.Response(404)

    provider = CACSCanadaProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    stations = asyncio.run(provider.fetch_station_catalog())

    assert [station.id for station in stations] == ['ALGO00CAN', 'BAKE00CAN']
    assert all(station.data_networks == ['canada'] for station in stations)
    assert all(station.providers == ['cacs_ca'] for station in stations)
    assert all(station.latitude is not None for station in stations)
    assert all(station.longitude is not None for station in stations)
    assert provider.last_station_catalog_stats['station_logs_parsed'] == 2


def test_europe_uses_logical_networks_not_epn_file_servers() -> None:
    from gnssgo.regional_sources import default_regional_source_registry

    networks = default_data_network_registry()
    europe = networks.get("europe")
    assert europe.providers[:16] == [
        "epn", "rgp_fr", "gref_de", "redgae_es", "nsgi_nl", "apos_at",
        "renep_pt", "belgium_be", "noa_gr", "epos_it", "epos_pl",
        "epos_ro", "epos_uk", "epos_se", "epos_fi", "epos_ch",
    ]
    assert {
        "epos_hu", "epos_cz", "epos_si", "epos_ie", "epos_is", "epos_hr",
        "epos_no", "epos_dk", "epos_ee", "epos_lv", "epos_lt", "epos_sk",
        "epos_bg", "epos_cy", "epos_rs", "epos_tr", "epos_lu", "epos_al",
        "epos_ba", "epos_mk", "epos_md", "epos_ua", "epos_mt", "epos_me",
    } <= set(europe.providers)

    sources = default_regional_source_registry()
    mapped = {s.id: s.provider for s in sources.all("europe")}
    assert mapped["europe_epn"] == "epn"
    assert mapped["europe_rgp"] == "rgp_fr"
    assert mapped["europe_gref"] == "gref_de"
    assert mapped["europe_malta"] == "epos_mt"
    assert mapped["europe_montenegro"] == "epos_me"
    # Old saved server-level filters remain compatible but collapse to EPN.
    for legacy in ("epn_rob", "epn_bev", "epn_bkg", "epn_bkge", "epn_bkgi", "epn_ign"):
        assert sources.normalize(legacy) == "europe_epn"

def test_rgp_france_catalog_and_daily_download() -> None:
    from gnssgo.providers.regional_expansion import RGPFranceProvider

    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen.append(url)
        if "coordRGP.php" in url:
            return httpx.Response(
                200,
                text=(
                    "Site;X;Y;Z\n"
                    "GRAS;4581691.0;556114.0;4389360.0\n"
                    "ABMF;2919786.0;-5383745.0;1774604.0\n"
                ),
            )
        if "coordonnees.php" in url:
            return httpx.Response(500)
        filename = "GRAS00FRA_R_20262130000_01D_30S_MO.crx.gz"
        return httpx.Response(
            200,
            text=(
                f'<a href="{filename}">{filename}</a>'
                '<a href="GRAS00FRA_R_20262130000_01H_30S_MO.crx.gz">hourly</a>'
            ),
        )

    provider = RGPFranceProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    stations = asyncio.run(provider.fetch_station_catalog())
    files = asyncio.run(provider.search_observations(request("GRAS00FRA", rinex="3")))

    assert [s.id for s in stations] == ["GRAS00FRA"]  # overseas ABMF is excluded from Europe
    assert stations[0].regional_sources == ["europe_rgp"]
    assert stations[0].network == ["RGP"]
    assert [f.filename for f in files] == ["GRAS00FRA_R_20262130000_01D_30S_MO.crx.gz"]
    assert files[0].metadata["regional_source"] == "europe_rgp"
    assert any("/pub/data_v3/2026/213/data_30/" in url for url in seen)


def test_rgp_france_catalog_uses_glass_when_ign_portal_is_slow() -> None:
    from gnssgo.providers.regional_expansion import RGPFranceProvider

    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen.append(url)
        if "stations/network/RGP/short/json" in url:
            return httpx.Response(
                200,
                json=[
                    {
                        "marker_long_name": "GRAS00FRA",
                        "latitude": 43.7547,
                        "longitude": 6.9206,
                        "altitude": 1319.0,
                        "network": "RGP",
                    },
                    {
                        "marker_long_name": "ABMF00GLP",
                        "latitude": 16.262,
                        "longitude": -61.527,
                        "altitude": -25.0,
                        "network": "RGP",
                    },
                ],
            )
        if "rgp.ign.fr/STATIONS" in url:
            raise httpx.ReadTimeout("simulated slow IGN portal", request=req)
        return httpx.Response(404)

    provider = RGPFranceProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    stations = asyncio.run(provider.fetch_station_catalog())

    assert [s.id for s in stations] == ["GRAS00FRA"]
    assert stations[0].metadata["source_type"] == "epos_glass_api"
    assert any("stations/network/RGP/short/json" in url for url in seen)
    assert not any("rgp.ign.fr/STATIONS" in url for url in seen)


def test_gref_germany_catalog_and_daily_download() -> None:
    from gnssgo.providers.regional_expansion import GREFGermanyProvider

    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen.append(url)
        if "/api/collections/stations/items" in url:
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "id": "POTS00DEU",
                            "properties": {
                                "NineCharID": "POTS00DEU",
                                "Country": "DEU",
                                "Networks": ["GREF", "EUREF"],
                                "X Coordinate": 3800689.0,
                                "Y Coordinate": 882077.0,
                                "Z Coordinate": 5028791.0,
                            },
                        },
                        {
                            "id": "NOTG00DEU",
                            "properties": {
                                "NineCharID": "NOTG00DEU",
                                "Country": "DEU",
                                "Networks": ["EUREF"],
                                "X Coordinate": 3800689.0,
                                "Y Coordinate": 882077.0,
                                "Z Coordinate": 5028791.0,
                            },
                        },
                    ]
                },
            )
        filename = "POTS00DEU_R_20262130000_01D_30S_MO.crx.gz"
        return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')

    provider = GREFGermanyProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    stations = asyncio.run(provider.fetch_station_catalog())
    files = asyncio.run(provider.search_observations(request("POTS00DEU", rinex="3")))

    assert len(stations) == 1
    assert stations[0].id == "POTS00DEU"
    assert "GREF" in stations[0].network
    assert stations[0].regional_sources == ["europe_gref"]
    assert 45 < stations[0].latitude < 56.5
    assert [f.filename for f in files] == ["POTS00DEU_R_20262130000_01D_30S_MO.crx.gz"]
    assert any("/root_ftp/GREF/obs/2026/213/" in url for url in seen)


def test_redgae_spain_catalog_and_central_daily_download() -> None:
    from gnssgo.providers.regional_expansion import RedGAESpainProvider

    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen.append(url)
        if url == "https://redgae.ign.es/estaciones":
            return httpx.Response(
                200,
                text="""
                <table>
                  <tr><th>Lugar</th><th>Código IDN</th><th>X</th><th>Y</th><th>Z</th></tr>
                  <tr><td>Alicante</td><td>ACNS 13400M001</td><td>5100000.0</td><td>-100000.0</td><td>3800000.0</td></tr>
                </table>
                """,
            )
        filename = "ACNS00ESP_R_20262130000_01D_30S_MO.crx.gz"
        return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')

    provider = RedGAESpainProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    stations = asyncio.run(provider.fetch_station_catalog())
    files = asyncio.run(provider.search_observations(request("ACNS00ESP", rinex="3")))

    assert len(stations) == 1
    assert stations[0].id == "ACNS00ESP"
    assert stations[0].network == ["redGAE"]
    assert stations[0].regional_sources == ["europe_redgae"]
    assert [f.filename for f in files] == ["ACNS00ESP_R_20262130000_01D_30S_MO.crx.gz"]
    assert files[0].metadata["integration_scope"] == "central_ergnss_daily_archive"
    assert seen[-1] == "https://datos-geodesia.ign.es/ERGNSS/diario_30s/2026/20260801/"


def test_nsgi_catalog_and_daily_download() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.endswith("current_metadata.html"):
            return httpx.Response(200, text="""
            ID Site Name Date Installed Network RD Number IERS DOMES number CRS X Y Z Latitude Longitude Elevation
            AMEL00NLD Ameland 2014-06-15 AGRS.NL+NETPOS 029309-03 13540M001 ETRF2000 3787664.3179 382392.2630 5100339.6159 53.446461328 5.764892667 60.6299 Receiver...
            AUA100ABW Aruba 1980-01-06 ETRF2000 2129138.2150 -5852473.0930 1372378.1500 12.508522252 -70.008539688 12.0983 Receiver...
            """)
        filename = "AMEL00NLD_R_20262130000_01D_30S_MO.crx.gz"
        return httpx.Response(200, text=f'<a href="{filename}">{filename}</a>')

    provider = NSGINetherlandsProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    stations = asyncio.run(provider.fetch_station_catalog())
    files = asyncio.run(provider.search_observations(request("AMEL00NLD", rinex="3")))
    assert [station.id for station in stations] == ["AMEL00NLD"]
    assert stations[0].regional_sources == ["europe_nsgi"]
    assert "AGRS.NL" in stations[0].network and "NETPOS" in stations[0].network
    assert files and files[0].metadata["regional_source"] == "europe_nsgi"
    assert "/data/daily/2026/213/" in str(files[0].url)


def test_apos_catalog_is_official_and_download_fails_fast_to_geoportal() -> None:
    async def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="""
        CODE Designation In operation since TP Number Latitude [°] Longitude [°] Ell. Height [m]
        AMST Amstetten 2005 639-053 L1 48,12224 14,87098 347
        HKB2 Hauser Kaibling (Neuerrichtung) 2024 47,37733 13,77134 1918
        """)
    provider = APOSAustriaProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    stations = asyncio.run(provider.fetch_station_catalog())
    assert {station.id for station in stations} == {"AMST00AUT", "HKB200AUT"}
    assert all(station.regional_sources == ["europe_apos"] for station in stations)
    with pytest.raises(Exception, match="Geoportal"):
        asyncio.run(provider.search_observations(request("AMST00AUT", rinex="3")))


def test_renep_uses_glass_for_catalog_and_daily_download() -> None:
    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen.append(url)
        if "/stations/v2/network/ReNEP/" in url:
            return httpx.Response(
                200,
                json=[{
                    "marker_long_name": "CASC00PRT",
                    "latitude": 38.693,
                    "longitude": -9.419,
                    "altitude": 100.0,
                    "country": "Portugal",
                    "network": "ReNEP",
                }],
            )
        if "/files/combination/" in url:
            return httpx.Response(
                200,
                json=[{
                    "marker_long_name": "CASC00PRT",
                    "file_name": "CASC00PRT_R_20262130000_01D_30S_MO.crx.gz",
                    "url": "https://glass.c4g-pt.eu/files/CASC00PRT_R_20262130000_01D_30S_MO.crx.gz",
                    "reference_date": "2026-08-01",
                    "data_center_acronym": "C4G",
                }],
            )
        return httpx.Response(404)

    provider = ReNEPPortugalProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    stations = asyncio.run(provider.fetch_station_catalog())
    files = asyncio.run(provider.search_observations(request("CASC00PRT", rinex="3")))

    assert [station.id for station in stations] == ["CASC00PRT"]
    assert stations[0].regional_sources == ["europe_renep"]
    assert files and files[0].filename.endswith("_01D_30S_MO.crx.gz")
    assert files[0].metadata["distribution"] == "EPOS GLASS"
    assert any("network/ReNEP" in url for url in seen)
    assert any("/files/combination/" in url for url in seen)


def test_belgium_uses_gnss_be_api_and_file_server() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/stations/v2/location/country/Belgium/" in url:
            return httpx.Response(200, json=[{
                "marker_long_name": "BRUX00BEL",
                "latitude": 50.798,
                "longitude": 4.359,
                "altitude": 150.0,
                "country": "Belgium",
            }])
        if "/api/v1/belgium/station-data/BRUX00BEL" in url:
            return httpx.Response(200, json=[{
                "DOY": 213,
                "date": "2026-08-01",
                "filename": "BRUX00BEL_R_20262130000_01D_30S_MO.crx.gz",
                "rinexVersion": "3.04",
                "stationId": "BRUX00BEL",
                "url": "https://gnss.be/pub/RINEX/2026/213/BRUX00BEL_R_20262130000_01D_30S_MO.crx.gz",
                "year": 2026,
            }])
        return httpx.Response(404)

    provider = BelgiumGNSSProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    stations = asyncio.run(provider.fetch_station_catalog())
    files = asyncio.run(provider.search_observations(request("BRUX00BEL", rinex="3")))
    assert [station.id for station in stations] == ["BRUX00BEL"]
    assert stations[0].regional_sources == ["europe_belgium"]
    assert files[0].url == "https://gnss.be/pub/RINEX/2026/213/BRUX00BEL_R_20262130000_01D_30S_MO.crx.gz"


def test_greece_uses_glass_country_catalog_and_file_query() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/stations/v2/location/country/Greece/" in url:
            return httpx.Response(200, json=[{
                "marker_long_name": "NOA100GRC",
                "latitude": 37.98,
                "longitude": 23.72,
                "altitude": 200.0,
                "country": "Greece",
                "network": "NOA",
            }])
        if "/files/combination/" in url:
            return httpx.Response(200, json=[{
                "marker_long_name": "NOA100GRC",
                "filename": "NOA100GRC_R_20262130000_01D_30S_MO.crx.gz",
                "url": "https://example.noa.gr/NOA100GRC_R_20262130000_01D_30S_MO.crx.gz",
                "reference_date": "2026-08-01",
            }])
        return httpx.Response(404)

    provider = NOAGreeceProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    stations = asyncio.run(provider.fetch_station_catalog())
    files = asyncio.run(provider.search_observations(request("NOA100GRC", rinex="3")))
    assert [station.id for station in stations] == ["NOA100GRC"]
    assert files and files[0].metadata["regional_source"] == "europe_greece"


def test_redgae_parser_does_not_treat_four_letter_place_as_station_code() -> None:
    from gnssgo.providers.regional_expansion import _redgae_coordinate_rows

    html = """
    <table>
      <tr><th>Localización</th><th>Código IDN</th><th>X</th><th>Y</th><th>Z</th></tr>
      <tr><td>Lugo</td><td>ACOR 13434M001</td><td>4594489.860</td><td>-678367.977</td><td>4357065.862</td></tr>
    </table>
    """
    rows = _redgae_coordinate_rows(html)
    assert len(rows) == 1
    marker, domes, lat, lon, _height = rows[0]
    assert marker == "ACOR"
    assert domes == "13434M001"
    assert 42.0 < lat < 44.5
    assert -10.0 < lon < -7.0


def test_glass_country_provider_paginates_and_keeps_only_country_code() -> None:
    seen: list[str] = []

    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen.append(url)
        if "page=0" in url:
            # Force a second page by returning a full page size for this provider.
            records = [
                {
                    "marker": f"I{i:03d}00ITA",
                    "latitude": 40.0 + i * 0.001,
                    "longitude": 12.0,
                    "country": "Italy",
                    "network": ["RING"],
                }
                for i in range(2)
            ]
        elif "page=1" in url:
            records = [
                {
                    "marker": "ROMA00ITA",
                    "latitude": 41.9,
                    "longitude": 12.5,
                    "country": "Italy",
                    "network": ["RING"],
                },
                {
                    "marker": "CROSS00FRA",
                    "latitude": 48.0,
                    "longitude": 2.0,
                    "country": "France",
                    "network": ["OTHER"],
                },
            ]
        else:
            records = []
        return httpx.Response(200, json=records)

    provider = ItalyEPOSProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    provider.glass_page_size = 2
    stations = asyncio.run(provider.fetch_station_catalog())

    assert {station.id for station in stations} == {"I00000ITA", "I00100ITA", "ROMA00ITA"}
    assert all(station.regional_sources == ["europe_italy"] for station in stations)
    assert any("page=1" in url for url in seen)


def test_uk_glass_country_alias_fallback() -> None:
    async def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "United%20Kingdom%20of%20Great%20Britain" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[{
                "marker": "ABEP00GBR",
                "latitude": 52.1394,
                "longitude": -4.5713,
                "country": "United Kingdom",
                "network": ["OSNET"],
            }],
        )

    provider = UnitedKingdomEPOSProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    stations = asyncio.run(provider.fetch_station_catalog())
    assert [station.id for station in stations] == ["ABEP00GBR"]
    assert stations[0].regional_sources == ["europe_uk"]
