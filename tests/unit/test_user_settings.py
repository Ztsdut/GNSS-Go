from pathlib import Path

from gnssgo.config import load_user_settings, save_user_settings


def test_user_settings_roundtrip(tmp_path) -> None:
    path = tmp_path / "settings.json"
    settings = load_user_settings(path=path)
    settings.appearance.theme = "dark"
    settings.download.workers = 9
    settings.archive.auto_extract = True
    settings.archive.keep_compressed = True
    save_user_settings(settings, path=path)

    loaded = load_user_settings(path=path)
    assert loaded.appearance.theme == "dark"
    assert loaded.download.workers == 9
    assert loaded.archive.auto_extract is True
    assert loaded.archive.keep_compressed is True
    assert loaded.products.provider_priority


def test_default_archive_mode_keeps_download_compressed() -> None:
    settings = load_user_settings(path=Path("/definitely/not/a/real/settings.json"))
    assert settings.archive.auto_extract is False


def test_proxy_settings_roundtrip(tmp_path) -> None:
    path = tmp_path / "settings.json"
    settings = load_user_settings(path=path)
    settings.network.mode = "socks5"
    settings.network.host = "127.0.0.1"
    settings.network.port = 1080
    settings.network.username = "alice"
    settings.network.password = "secret"
    settings.network.use_for_http = True
    settings.network.use_for_sftp = True
    settings.network.use_for_ftp = False
    save_user_settings(settings, path=path)

    loaded = load_user_settings(path=path)
    assert loaded.network.mode == "socks5"
    assert loaded.network.host == "127.0.0.1"
    assert loaded.network.port == 1080
    assert loaded.network.username == "alice"
    assert loaded.network.password == "secret"
    assert loaded.network.use_for_sftp is True
    assert loaded.network.use_for_ftp is False
