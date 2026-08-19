from __future__ import annotations

import os

import pytest

from gnssgo import GNSSGo


def require_live_network() -> None:
    if os.environ.get("GNSSGO_LIVE_TESTS") != "1":
        pytest.skip("Set GNSSGO_LIVE_TESTS=1 to run live data-center tests.")


@pytest.mark.integration
def test_auto_fallback_live(tmp_path, monkeypatch) -> None:
    require_live_network()
    monkeypatch.delenv("EARTHDATA_USERNAME", raising=False)
    monkeypatch.delenv("EARTHDATA_PASSWORD", raising=False)
    client = GNSSGo()
    plan = client.plan_navigation(
        start="2026-08-01",
        end="2026-08-01",
        provider="auto",
        output=tmp_path,
    )
    assert plan.provider_used == "bkg"
    assert plan.attempted_providers
