from gnssgo.rinex.detect import detect_compression, is_compact_rinex
from gnssgo.rinex.naming import parse_rinex_filename


def test_rinex2_observation_compact_z() -> None:
    info = parse_rinex_filename("wuh22130.26d.Z")
    assert info.station == "WUH2"
    assert info.year == 2026
    assert info.doy == 213
    assert info.file_type == "observation"
    assert info.rinex_version_family == "2"
    assert info.compression == ".Z"
    assert info.compact is True


def test_rinex3_observation_gz() -> None:
    info = parse_rinex_filename("WUH200CHN_R_20262130000_01D_30S_MO.crx.gz")
    assert info.station == "WUH200CHN"
    assert info.year == 2026
    assert info.doy == 213
    assert info.file_type == "observation"
    assert info.rinex_version_family == "3/4"
    assert info.compression == ".gz"
    assert info.compact is True


def test_compression_detection() -> None:
    assert detect_compression("file.rnx.gz") == ".gz"
    assert detect_compression("file.crx.Z") == ".Z"
    assert is_compact_rinex("file.crx.gz") is True
