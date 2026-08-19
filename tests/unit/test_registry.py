import pytest

from gnssgo.exceptions import ConfigurationError
from gnssgo.providers import BKGProvider, ProviderRegistry, default_registry


def test_registry_gets_provider() -> None:
    registry = ProviderRegistry()
    registry.register(BKGProvider())
    assert registry.get("BKG").name == "bkg"


def test_registry_unknown_provider() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ConfigurationError):
        registry.get("missing")


def test_default_registry_includes_whu() -> None:
    assert "whu" in default_registry().names()


def test_default_registry_includes_additional_igs_mirrors() -> None:
    names = default_registry().names()
    for name in ["bdsmart", "bkgftp", "esa", "ign", "kasi", "noaa", "sopac"]:
        assert name in names
