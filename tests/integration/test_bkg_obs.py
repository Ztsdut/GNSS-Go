from __future__ import annotations

import os

import pytest

from gnssgo import GNSSGo


def require_live_network() -> None:
    if os.environ.get("GNSSGO_LIVE_TESTS") != "1":
        pytest.skip("Set GNSSGO_LIVE_TESTS=1 to run live data-center tests.")


@pytest.mark.integration
def test_bkg_obs_live(tmp_path) -> None:
    require_live_network()
    client = GNSSGo()
    plan = client.plan_observations(
        stations=["WUH200CHN"],
        start="2026-08-01",
        end="2026-08-01",
        provider="bkg",
        output=tmp_path,
        keep_compressed=True,
    )
    assert plan.download_tasks
    results = client.execute_plan(plan)
    assert all(result.status != "failed" for result in results)
    assert results[0].local_file
    assert results[0].local_file.rinex_type == "observation"
