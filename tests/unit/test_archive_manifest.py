from datetime import date

from gnssgo.archive import ArchiveLayout, Manifest
from gnssgo.archive.manifest import ManifestRecord
from gnssgo.models import (
    DateRange,
    DownloadResult,
    DownloadTask,
    ObservationRequest,
    ProviderAttempt,
    RemoteFile,
)
from gnssgo.models.results import DownloadPlan


def test_archive_layout_year_doy(tmp_path) -> None:
    remote = RemoteFile(
        provider="bkg",
        url="https://example.test/file.rnx.gz",
        filename="file.rnx.gz",
        data_type="obs",
        date=date(2026, 8, 1),
    )
    path = ArchiveLayout(tmp_path).destination_for(remote)
    assert path == tmp_path / "2026" / "213" / "obs" / "file.rnx.gz"


def test_manifest_append_and_read(tmp_path) -> None:
    manifest = Manifest(tmp_path / "manifest.jsonl")
    manifest.append(
        ManifestRecord(
            provider="bkg",
            url="https://example.test/file",
            filename="file",
            local_path="file",
            status="downloaded",
            data_type="obs",
        )
    )
    records = manifest.records()
    assert records[0]["provider"] == "bkg"


def test_manifest_records_provider_attempts(tmp_path) -> None:
    remote = RemoteFile(
        provider="bkg",
        url="https://example.test/file.rnx.gz",
        filename="file.rnx.gz",
        data_type="nav",
        date=date(2026, 8, 1),
    )
    task = DownloadTask(
        remote=remote,
        destination=tmp_path / "file.rnx.gz",
        temporary_path=tmp_path / "file.rnx.gz.part",
    )
    plan = DownloadPlan(
        remote_files=[remote],
        provider_requested="auto",
        attempted_providers=[
            ProviderAttempt(provider="whu", status="not_found"),
            ProviderAttempt(provider="bkg", status="success"),
        ],
    )
    manifest = Manifest(tmp_path / "manifest.jsonl")
    manifest.append_result(DownloadResult(task=task, status="failed"), plan=plan)
    record = manifest.records()[0]
    assert record["provider_requested"] == "auto"
    assert record["provider_used"] == "bkg"
    assert record["attempted_providers"][0]["provider"] == "whu"


def test_manifest_records_regional_fallback(tmp_path) -> None:
    remote = RemoteFile(
        provider="esa",
        url="https://example.test/file.rnx.gz",
        filename="file.rnx.gz",
        data_type="obs",
        date=date(2026, 8, 1),
    )
    task = DownloadTask(
        remote=remote,
        destination=tmp_path / "file.rnx.gz",
        temporary_path=tmp_path / "file.rnx.gz.part",
    )
    request = ObservationRequest(
        stations=["ALIC00AUS"],
        date_range=DateRange(start=date(2026, 8, 1), end=date(2026, 8, 1)),
        data_networks=["australia"],
    )
    plan = DownloadPlan(
        requests=[request],
        remote_files=[remote],
        provider_requested="auto",
    )

    manifest = Manifest(tmp_path / "manifest.jsonl")
    manifest.append_result(DownloadResult(task=task, status="downloaded"), plan=plan)

    record = manifest.records()[0]
    assert record["data_network_requested"] == "australia"
    assert record["regional_fallback"] is True


def test_manifest_redacts_signed_url_query_and_records_sources(tmp_path) -> None:
    remote = RemoteFile(
        provider="ga",
        url="https://example.test/file.crx.gz?X-Amz-Security-Token=secret",
        filename="file.crx.gz",
        data_type="obs",
        station="ADMN00AUS",
        metadata={"station_regional_sources": "corsnet_nsw"},
    )
    task = DownloadTask(
        remote=remote,
        destination=tmp_path / "file.crx.gz",
        temporary_path=tmp_path / "file.crx.gz.part",
    )
    request = ObservationRequest(
        stations=["ADMN00AUS"],
        date_range=DateRange(start=date(2026, 8, 1), end=date(2026, 8, 1)),
        data_networks=["australia"],
        regional_sources=["corsnet_nsw"],
    )
    plan = DownloadPlan(requests=[request], remote_files=[remote], provider_requested="ga")

    manifest = Manifest(tmp_path / "manifest.jsonl")
    manifest.append_result(DownloadResult(task=task, status="failed"), plan=plan)

    record = manifest.records()[0]
    assert record["url"] == "https://example.test/file.crx.gz"
    assert record["regional_sources_requested"] == ["corsnet_nsw"]
    assert record["station_regional_sources"] == ["corsnet_nsw"]
