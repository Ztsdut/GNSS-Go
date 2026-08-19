from __future__ import annotations

import gzip

import pytest

from gnssgo.rinex.postprocess import PostProcessor


@pytest.mark.integration
def test_postprocess_rnx_gz(tmp_path) -> None:
    rnx = (
        "     3.04           NAVIGATION DATA     M                   RINEX VERSION / TYPE\n"
        "                                                            END OF HEADER\n"
    )
    gz_path = tmp_path / "test.rnx.gz"
    with gzip.open(gz_path, "wb") as handle:
        handle.write(rnx.encode("ascii"))
    result = PostProcessor().process(
        gz_path,
        keep_compressed=True,
        expected_rinex_type="navigation",
    )
    assert result.output_path.name == "test.rnx"
    assert result.rinex
    assert result.rinex.rinex_type == "navigation"
