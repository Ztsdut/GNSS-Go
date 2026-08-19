from __future__ import annotations

import gzip
import zipfile

from gnssgo.exceptions import ValidationError
from gnssgo.rinex.postprocess import PostProcessor


def test_gzip_rinex_validation_pass_becomes_validated(tmp_path) -> None:
    path = tmp_path / "TEST00NZL_R_20262130000_01D_30S_MO.rnx.gz"
    rinex = (
        "     3.05           OBSERVATION DATA    M                   RINEX VERSION / TYPE\n"
        "                                                            END OF HEADER\n"
    )
    with gzip.open(path, "wb") as handle:
        handle.write(rinex.encode("ascii"))

    result = PostProcessor().process(path, expected_rinex_type="observation")

    assert result.status == "validated"
    assert result.rinex is not None
    assert result.rinex.version == "3.05"
    assert result.rinex.rinex_type == "observation"


def test_gzip_rinex_validation_fail_raises(tmp_path) -> None:
    path = tmp_path / "bad.rnx.gz"
    with gzip.open(path, "wb") as handle:
        handle.write(b"not rinex")

    try:
        PostProcessor().process(path, expected_rinex_type="observation")
    except ValidationError:
        return
    raise AssertionError("Expected ValidationError")


def test_client_compressed_output_mode_keeps_archive_as_final_file(tmp_path) -> None:
    from gnssgo import GNSSGo
    from gnssgo.download import make_task
    from gnssgo.models import RemoteFile

    path = tmp_path / "TEST00NZL_R_20262130000_01D_30S_MO.rnx.gz"
    rinex = (
        "     3.05           OBSERVATION DATA    M                   RINEX VERSION / TYPE\n"
        "                                                            END OF HEADER\n"
    )
    with gzip.open(path, "wb") as handle:
        handle.write(rinex.encode("ascii"))
    remote = RemoteFile(
        provider="unit",
        url="https://example.test/" + path.name,
        filename=path.name,
        data_type="obs",
    )
    task = make_task(remote, path, decompress=False)

    local = GNSSGo()._postprocess_download(task, path)

    assert local.path == path
    assert path.exists()
    assert not (tmp_path / path.name.removesuffix(".gz")).exists()


def test_keep_compressed_preserves_original_when_auto_extracting(tmp_path) -> None:
    from gnssgo import GNSSGo
    from gnssgo.download import make_task
    from gnssgo.models import RemoteFile

    path = tmp_path / "TEST00NZL_R_20262130000_01D_30S_MO.rnx.gz"
    rinex = (
        "     3.05           OBSERVATION DATA    M                   RINEX VERSION / TYPE\n"
        "                                                            END OF HEADER\n"
    )
    with gzip.open(path, "wb") as handle:
        handle.write(rinex.encode("ascii"))
    remote = RemoteFile(
        provider="unit",
        url="https://example.test/" + path.name,
        filename=path.name,
        data_type="obs",
    )
    task = make_task(remote, path, decompress=True, keep_compressed=True)

    local = GNSSGo()._postprocess_download(task, path)

    assert path.exists(), "Keep compressed must retain the original .gz file"
    assert local.path.exists()
    assert local.path.suffix == ".rnx"


def test_zip_extracts_observation_member_from_mexico_rinex3_bundle(tmp_path) -> None:
    path = tmp_path / "INEG001a_304.zip"
    obs_name = "INEG00MEX_R_20250010000_01H_15S_MO.rnx"
    nav_name = "INEG00MEX_R_20250010000_01H_15S_MN.rnx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(obs_name, "observation payload")
        archive.writestr(nav_name, "navigation payload")

    result = PostProcessor().process(
        path,
        expected_rinex_type="observation",
        validate_rinex=False,
    )

    assert result.output_path.name == obs_name
    assert result.output_path.read_text() == "observation payload"
    assert "zip" in result.steps
    assert not path.exists()


def test_zip_extracts_legacy_observation_member_from_mexico_rinex2_bundle(tmp_path) -> None:
    path = tmp_path / "INEG001a.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ineg001a.25o", "observation payload")
        archive.writestr("ineg001a.25n", "gps navigation")
        archive.writestr("ineg001a.25g", "glonass navigation")

    result = PostProcessor().process(
        path,
        expected_rinex_type="observation",
        validate_rinex=False,
    )

    assert result.output_path.name == "ineg001a.25o"
    assert result.output_path.read_text() == "observation payload"
