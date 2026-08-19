from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir
from pydantic import BaseModel, Field

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:  # pragma: no cover - exercised only in minimal local environments.
    BaseSettings = BaseModel  # type: ignore[misc, assignment]

    def SettingsConfigDict(**kwargs):  # type: ignore[no-untyped-def]
        return kwargs

from gnssgo.config.defaults import DEFAULT_CONFIG


class DownloadSettings(BaseModel):
    workers: int = 4
    per_provider_workers: int = 3
    retries: int = 5
    connect_timeout: float = 20
    read_timeout: float = 120
    resume: bool = True
    overwrite: bool = False


class ArchiveSettings(BaseModel):
    root: Path = Path("./data")
    layout: str = "year_doy"
    # Keep the provider's compressed file as the default final output.
    # Users can opt into automatic extraction in Settings.
    auto_extract: bool = False
    # When auto_extract=True, retain the original compressed archive as well.
    keep_compressed: bool = False


class ProviderSettings(BaseModel):
    priority: list[str] = Field(default_factory=lambda: ["whu", "bkg"])


class ProductSettings(BaseModel):
    default_tier: str = "auto"
    default_system: str = "auto"
    center_priority: list[str] = Field(
        default_factory=lambda: ["IGS", "COD", "GFZ", "ESA", "GRG", "WUM"]
    )
    multi_gnss_center_priority: list[str] = Field(
        default_factory=lambda: ["IGS", "WUM", "GFZ", "COD", "ESA"]
    )
    provider_priority: list[str] = Field(
        default_factory=lambda: ["esa", "ign", "whu", "bkgftp", "bkg", "igsfiles"]
    )
    prefer_same_center: bool = True
    prefer_same_tier: bool = True
    allow_mixed_center: bool = True
    availability_cache: bool = True


class NetworkSettings(BaseModel):
    # ``proxy`` is retained for compatibility with older settings files.
    proxy: str | None = None
    mode: str = "system"  # direct | system | http | socks5
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""
    use_for_http: bool = True
    use_for_sftp: bool = True
    use_for_ftp: bool = True
    chromedriver_path: str = ""


class StationSettings(BaseModel):
    catalog_path: Path | None = None
    auto_seed: bool = False


class LoggingSettings(BaseModel):
    level: str = "INFO"


class AppearanceSettings(BaseModel):
    theme: str = "system"
    language: str = "en"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GNSSGO_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    download: DownloadSettings = Field(default_factory=DownloadSettings)
    archive: ArchiveSettings = Field(default_factory=ArchiveSettings)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    products: ProductSettings = Field(default_factory=ProductSettings)
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    stations: StationSettings = Field(default_factory=StationSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    appearance: AppearanceSettings = Field(default_factory=AppearanceSettings)


def load_settings(overrides: dict[str, Any] | None = None) -> Settings:
    """Load deterministic defaults plus explicit overrides.

    This function intentionally does not read the desktop GUI preference file so
    library/CLI tests remain deterministic.  The desktop application uses
    :func:`load_user_settings` instead.
    """

    data = _deep_merge(DEFAULT_CONFIG, overrides or {})
    return Settings.model_validate(data)


def user_settings_path() -> Path:
    return Path(user_config_dir("GNSS Go", "GNSS Go")) / "settings.json"


def load_user_settings(
    overrides: dict[str, Any] | None = None,
    *,
    path: Path | None = None,
) -> Settings:
    """Load persisted desktop preferences, preserving defaults for new fields."""

    config_path = path or user_settings_path()
    persisted: dict[str, Any] = {}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            persisted = raw
    except (OSError, json.JSONDecodeError, TypeError):
        persisted = {}
    data = _deep_merge(DEFAULT_CONFIG, persisted)
    data = _deep_merge(data, overrides or {})
    return Settings.model_validate(data)


def save_user_settings(settings: Settings, *, path: Path | None = None) -> Path:
    """Persist desktop preferences atomically.

    Proxy credentials, when supplied, are stored in the local GNSS Go settings
    file so desktop proxy configuration survives restarts.
    """

    config_path = path or user_settings_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    payload = settings.model_dump(mode="json")
    temp_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temp_path.replace(config_path)
    return config_path


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key, value in base.items():
        if isinstance(value, dict):
            merged[key] = _deep_merge(value, {})
        elif isinstance(value, list):
            merged[key] = list(value)
        else:
            merged[key] = value
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif isinstance(value, list):
            merged[key] = list(value)
        else:
            merged[key] = value
    return merged
