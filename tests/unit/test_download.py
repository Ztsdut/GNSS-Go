import pytest

from gnssgo.download.resume import part_path
from gnssgo.download.retry import backoff_seconds, retry_after_seconds, should_retry_status
from gnssgo.download.validator import partial_file_is_invalid, validate_download_content
from gnssgo.exceptions import InvalidRemoteContent
from gnssgo.models import RemoteFile


def test_retry_policy() -> None:
    assert should_retry_status(503) is True
    assert should_retry_status(404) is False
    assert backoff_seconds(4) == 8
    assert retry_after_seconds("5") == 5
    assert retry_after_seconds("not a date") is None


def test_part_path(tmp_path) -> None:
    assert part_path(tmp_path / "a.rnx").name == "a.rnx.part"


def test_validate_download_rejects_html_payload(tmp_path) -> None:
    path = tmp_path / "file.rnx.gz"
    path.write_text("<html>login</html>", encoding="utf-8")
    remote = RemoteFile(
        provider="test",
        url="https://example.test/file.rnx.gz",
        filename=path.name,
        data_type="nav",
    )
    with pytest.raises(InvalidRemoteContent):
        validate_download_content(path, remote, {"content-type": "text/html"})


def test_partial_file_is_invalid_for_html(tmp_path) -> None:
    path = tmp_path / "file.rnx.gz.part"
    path.write_text("<html>login</html>", encoding="utf-8")
    assert partial_file_is_invalid(path) is True
