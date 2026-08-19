from datetime import date
from pathlib import Path

from gnssgo.client import GNSSGo
from gnssgo.models import DownloadResult, DownloadTask, LocalFile, RemoteFile


def _result(status: str, provider: str = "esa", size: int = 0) -> DownloadResult:
    remote = RemoteFile(
        provider=provider,
        url=f"https://example.test/{provider}.bin",
        filename=f"{provider}.bin",
        data_type="product",
        date=date(2026, 8, 1),
    )
    task = DownloadTask(
        remote=remote,
        destination=Path(f"{provider}.bin"),
        temporary_path=Path(f"{provider}.bin.part"),
    )
    local = (
        LocalFile(path=Path(f"{provider}.bin"), size=size, status=status, remote=remote)
        if status != "failed"
        else None
    )
    return DownloadResult(task=task, status=status, local_file=local)


def test_batch_result_state_partial() -> None:
    summary = GNSSGo().summarize_results([
        _result("validated", "esa", 10),
        _result("skipped", "whu", 20),
        _result("failed", "bkg"),
    ])
    assert summary.total == 3
    assert summary.downloaded == 1
    assert summary.skipped == 1
    assert summary.failed == 1
    assert summary.state == "partial"
    assert summary.bytes_downloaded == 30
