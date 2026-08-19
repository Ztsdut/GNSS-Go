import httpx

from gnssgo.providers.bkg import IGS_NETWORK_JSON, BKGProvider


async def _handler(request: httpx.Request) -> httpx.Response:
    assert str(request.url) == IGS_NETWORK_JSON
    return httpx.Response(
        200,
        json={
            "WUH200CHN": {
                "CountryOrRegion": "CHN",
                "Latitude": "30.532",
                "Longitude": "114.357",
                "Height": "28.166",
                "Receiver": {
                    "Name": "JAVAD TR_2S",
                    "SatelliteSystem": "GPS+GLO+GAL+BDS+QZSS",
                },
                "Antenna": {"Name": "JAVRINGANT_G5T", "Radome": "NONE"},
                "LastData": "2026-08-11",
            }
        },
    )


def test_bkg_fetch_station_catalog_parses_igs_network_json() -> None:
    transport = httpx.MockTransport(_handler)
    client = httpx.AsyncClient(transport=transport, follow_redirects=True)
    provider = BKGProvider(client=client)

    import asyncio

    stations = asyncio.run(provider.fetch_station_catalog())

    assert len(stations) == 1
    station = stations[0]
    assert station.id == "WUH200CHN"
    assert station.marker_name == "WUH2"
    assert station.latitude == 30.532
    assert station.longitude == 114.357
    assert station.country == "CHN"
    assert station.receiver == "JAVAD TR_2S"
    assert station.antenna == "JAVRINGANT_G5T NONE"
    assert station.constellations == ["G", "R", "E", "C", "J"]
    assert station.data_availability == "2026-08-11"
