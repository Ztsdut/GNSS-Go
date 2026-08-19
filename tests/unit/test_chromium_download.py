import time
from pathlib import Path

from gnssgo.download import chromium


def test_find_chromium_browser_uses_path(monkeypatch, tmp_path: Path):
    exe = tmp_path / "msedge"
    exe.write_bytes(b"x")
    for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(
        chromium.shutil,
        "which",
        lambda name: str(exe) if name == "msedge" else None,
    )
    assert chromium.find_chromium_browser() == exe


def test_browser_proxy_argument_system_uses_windows_settings_implicitly():
    from gnssgo.network import ProxyConfig
    proxy = ProxyConfig(mode="system", use_for_http=True)
    assert chromium._browser_proxy_argument(proxy) is None


def test_browser_proxy_argument_custom_http():
    from gnssgo.network import ProxyConfig
    proxy = ProxyConfig(mode="http", host="127.0.0.1", port=7892, use_for_http=True)
    assert chromium._browser_proxy_argument(proxy) == "http://127.0.0.1:7892"


def test_csn_profile_is_persistent_and_markable(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(chromium, "_persistent_csn_profile_dir", lambda: tmp_path / "csn-profile")
    profile = chromium._persistent_csn_profile_dir()
    profile.mkdir(parents=True)
    assert chromium._profile_is_initialized(profile) is False
    chromium._mark_profile_initialized(profile)
    assert chromium._profile_is_initialized(profile) is True


def test_csn_browser_source_uses_headed_mode_and_no_ua_override():
    source = Path(chromium.__file__).read_text(encoding="utf-8")
    assert '"--headless=new"' not in source
    assert "Network.setUserAgentOverride" not in source
    assert '"--start-minimized"' in source
    assert "csn-browser-profile" in source


def test_browser_download_name_regex_accepts_chromium_duplicate_suffix():
    matcher = chromium._download_name_regex("arjf0180.26d.Z")
    assert matcher.match("arjf0180.26d.Z")
    assert matcher.match("arjf0180.26d (1).Z")
    assert not matcher.match("imch0180.26d.Z")


def test_user_browser_session_imports_new_download(monkeypatch, tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    output = tmp_path / "archive" / "arjf0180.26d.Z.part"

    monkeypatch.setattr(chromium.os, "name", "nt")
    monkeypatch.setattr(chromium, "_browser_download_directories", lambda: [downloads])

    def fake_open(url: str) -> None:
        assert url.endswith("/arjf0180.26d.Z")
        (downloads / "arjf0180.26d.Z").write_bytes(b"RINEX-DATA")

    monkeypatch.setattr(chromium, "_open_url_in_default_browser", fake_open)
    result = chromium.download_with_user_browser_session(
        "https://gps.csn.uchile.cl/data/2026/018/arjf0180.26d.Z",
        output,
        expected_filename="arjf0180.26d.Z",
        timeout=2.0,
    )
    assert result == output
    assert output.read_bytes() == b"RINEX-DATA"
    assert not (downloads / "arjf0180.26d.Z").exists()


def test_user_browser_session_ignores_old_file_and_imports_new_duplicate(monkeypatch, tmp_path: Path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    old = downloads / "arjf0180.26d.Z"
    old.write_bytes(b"OLD")
    old_time = time.time() - 30
    chromium.os.utime(old, (old_time, old_time))
    output = tmp_path / "archive" / "arjf0180.26d.Z.part"

    monkeypatch.setattr(chromium.os, "name", "nt")
    monkeypatch.setattr(chromium, "_browser_download_directories", lambda: [downloads])

    def fake_open(url: str) -> None:
        (downloads / "arjf0180.26d (1).Z").write_bytes(b"NEW")

    monkeypatch.setattr(chromium, "_open_url_in_default_browser", fake_open)
    chromium.download_with_user_browser_session(
        "https://gps.csn.uchile.cl/data/2026/018/arjf0180.26d.Z",
        output,
        expected_filename="arjf0180.26d.Z",
        timeout=2.0,
    )
    assert output.read_bytes() == b"NEW"
    assert old.read_bytes() == b"OLD"


def test_history_download_targets_finds_custom_target(monkeypatch, tmp_path: Path):
    import sqlite3

    history = tmp_path / "History"
    custom = tmp_path / "D-drive" / "browser-downloads"
    custom.mkdir(parents=True)
    target = custom / "arjf0190.26d.Z"
    target.write_bytes(b"CSN")

    conn = sqlite3.connect(history)
    conn.execute(
        "CREATE TABLE downloads (id INTEGER PRIMARY KEY, current_path TEXT, target_path TEXT, "
        "start_time INTEGER, end_time INTEGER, received_bytes INTEGER, total_bytes INTEGER, state INTEGER)"
    )
    conn.execute(
        "CREATE TABLE downloads_url_chains (id INTEGER, chain_index INTEGER, url TEXT)"
    )
    chrome_now = int((time.time() + 11644473600.0) * 1_000_000)
    conn.execute(
        "INSERT INTO downloads VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
        (str(target), str(target), chrome_now, chrome_now, 3, 3, 1),
    )
    url = "https://gps.csn.uchile.cl/data/2026/019/arjf0190.26d.Z"
    conn.execute("INSERT INTO downloads_url_chains VALUES (1, 0, ?)", (url,))
    conn.commit()
    conn.close()

    monkeypatch.setattr(chromium, "_chromium_history_files", lambda: [history])
    found = chromium._history_download_targets(
        url,
        "arjf0190.26d.Z",
        opened_at_ns=time.time_ns() - 2_000_000_000,
    )
    assert target in found


def test_user_browser_session_imports_from_chromium_history_custom_directory(
    monkeypatch, tmp_path: Path
):
    normal_downloads = tmp_path / "Downloads"
    normal_downloads.mkdir()
    custom = tmp_path / "custom-browser-folder"
    custom.mkdir()
    target = custom / "arjf0190.26d.Z"
    output = tmp_path / "gnssgo-output" / "arjf0190.26d.Z.part"
    url = "https://gps.csn.uchile.cl/data/2026/019/arjf0190.26d.Z"

    monkeypatch.setattr(chromium.os, "name", "nt")
    monkeypatch.setattr(chromium, "_browser_download_directories", lambda: [normal_downloads])
    monkeypatch.setattr(
        chromium,
        "_history_download_targets",
        lambda *args, **kwargs: [target] if target.exists() else [],
    )

    def fake_open(opened_url: str) -> None:
        assert opened_url == url
        target.write_bytes(b"REAL-CSN-DATA")

    monkeypatch.setattr(chromium, "_open_url_in_default_browser", fake_open)
    result = chromium.download_with_user_browser_session(
        url,
        output,
        expected_filename="arjf0190.26d.Z",
        timeout=3.0,
    )
    assert result == output
    assert output.read_bytes() == b"REAL-CSN-DATA"
    assert not target.exists()


def test_find_standalone_chromedriver_explicit(monkeypatch, tmp_path: Path):
    from gnssgo.download import chromedriver

    exe = tmp_path / "chromedriver.exe"
    exe.write_bytes(b"driver")
    assert chromedriver.find_chromedriver(exe) == exe.resolve()


def test_chromedriver_download_uses_gnssgo_folder(monkeypatch, tmp_path: Path):
    from gnssgo.download import chromedriver
    from gnssgo.network import ProxyConfig

    driver = tmp_path / "chromedriver.exe"
    chrome = tmp_path / "chrome.exe"
    driver.write_bytes(b"driver")
    chrome.write_bytes(b"chrome")
    output = tmp_path / "archive" / "arjf0200.26d.Z.part"
    expected = output.parent / "arjf0200.26d.Z"

    monkeypatch.setattr(chromedriver, "find_chromedriver", lambda explicit_path=None: driver)
    monkeypatch.setattr(chromedriver, "find_chrome_browser", lambda: chrome)
    monkeypatch.setattr(chromedriver, "_free_local_port", lambda: 9515)
    monkeypatch.setattr(chromedriver, "_wait_driver", lambda port, timeout=8.0: None)
    monkeypatch.setattr(chromedriver, "_create_session", lambda *args, **kwargs: "session-1")
    monkeypatch.setattr(chromedriver, "_execute_cdp", lambda *args, **kwargs: None)
    def fake_nav(port, session_id, url):
        expected.parent.mkdir(parents=True, exist_ok=True)
        expected.write_bytes(b"CSN-RINEX")
    monkeypatch.setattr(chromedriver, "_navigate", fake_nav)
    monkeypatch.setattr(chromedriver, "_delete_session", lambda *args, **kwargs: None)

    class FakeProcess:
        def wait(self, timeout=None): return 0
        def terminate(self): return None
        def kill(self): return None
    monkeypatch.setattr(chromedriver.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    result = chromedriver.download_with_chromedriver(
        "https://gps.csn.uchile.cl/data/2026/020/arjf0200.26d.Z",
        output,
        expected_filename="arjf0200.26d.Z",
        proxy=ProxyConfig(mode="system", use_for_http=True),
        timeout=2.0,
    )
    assert result == output
    assert output.read_bytes() == b"CSN-RINEX"
    assert not expected.exists()


def test_chromedriver_wait_uses_progress_not_legacy_120s(monkeypatch, tmp_path: Path):
    """A healthy slow download must survive well beyond the old 120 s wall clock."""
    from gnssgo.download import chromedriver

    expected = tmp_path / "arjf0200.26d.Z"
    partial = tmp_path / "arjf0200.26d.Z.crdownload"
    partial.write_bytes(b"x")

    clock = [0.0]
    sleeps = [0]
    progress: list[int] = []

    monkeypatch.setattr(chromedriver.time, "monotonic", lambda: clock[0])

    def fake_sleep(_seconds: float) -> None:
        sleeps[0] += 1
        clock[0] += 30.0
        # Keep making byte progress for 150 seconds, then let Chrome rename the
        # completed .crdownload at 180 seconds.  This would have failed under v19.
        if sleeps[0] <= 5:
            partial.write_bytes(partial.read_bytes() + b"x")
        elif sleeps[0] == 6:
            partial.replace(expected)

    monkeypatch.setattr(chromedriver.time, "sleep", fake_sleep)

    result = chromedriver._wait_for_download(
        tmp_path,
        expected.name,
        started_ns=0,
        timeout=1800.0,
        stall_timeout=90.0,
        progress_callback=progress.append,
    )

    assert result == expected
    assert clock[0] > 120.0
    assert progress
    assert progress[-1] == expected.stat().st_size


def test_chromedriver_wait_fails_only_after_no_progress(monkeypatch, tmp_path: Path):
    from gnssgo.download import chromedriver
    from gnssgo.exceptions import DownloadError

    partial = tmp_path / "arjf0200.26d.Z.crdownload"
    partial.write_bytes(b"stuck")
    clock = [0.0]

    monkeypatch.setattr(chromedriver.time, "monotonic", lambda: clock[0])

    def fake_sleep(_seconds: float) -> None:
        clock[0] += 31.0

    monkeypatch.setattr(chromedriver.time, "sleep", fake_sleep)

    import pytest
    with pytest.raises(DownloadError, match="no file-size progress for 90 s"):
        chromedriver._wait_for_download(
            tmp_path,
            "arjf0200.26d.Z",
            started_ns=0,
            timeout=1800.0,
            stall_timeout=90.0,
        )

    assert 90.0 <= clock[0] < 1800.0
