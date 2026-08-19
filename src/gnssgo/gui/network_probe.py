from __future__ import annotations

import httpx

from gnssgo.network import ProxyConfig


_OSM_PROBE_URL = "https://tile.openstreetmap.org/0/0/0.png"


def openstreetmap_available(*, timeout: float = 1.8, network_settings=None) -> bool:
    """Return True when an OpenStreetMap tile is reachable through app networking.

    The probe follows the same direct/system/custom-proxy choice configured in
    GNSS Go.  It performs only one tiny tile request and converts every
    network/TLS/proxy failure into ``False`` so offline startup always remains
    usable with the bundled basemap.
    """

    config = ProxyConfig.from_settings(network_settings)
    proxy = config.proxy_url(protocol="https") if config.enabled_for("https") else None
    trust_env = config.mode == "system" and config.use_for_http and proxy is None
    headers = {
        "User-Agent": "GNSS-Go/0.1 (desktop station-map connectivity probe)",
        "Accept": "image/png,*/*;q=0.8",
    }
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=timeout),
            follow_redirects=True,
            proxy=proxy,
            trust_env=trust_env,
            headers=headers,
        ) as client:
            response = client.get(_OSM_PROBE_URL)
        return 200 <= response.status_code < 400 and bool(response.content)
    except Exception:
        # Connectivity probing must never prevent the desktop application from starting.
        return False
