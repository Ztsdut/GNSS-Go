from __future__ import annotations

from gnssgo.exceptions import ConfigurationError
from gnssgo.providers.base import GNSSProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, GNSSProvider] = {}

    def register(self, provider: GNSSProvider) -> None:
        self._providers[provider.name.lower()] = provider

    def get(self, name: str) -> GNSSProvider:
        try:
            return self._providers[name.lower()]
        except KeyError as exc:
            available = ", ".join(sorted(self._providers)) or "none"
            raise ConfigurationError(
                f"Unknown provider {name!r}. Available providers: {available}."
            ) from exc

    def names(self) -> list[str]:
        return sorted(self._providers)

    def ordered(self, priority: list[str] | None = None) -> list[GNSSProvider]:
        if not priority:
            return [self._providers[name] for name in self.names()]
        result: list[GNSSProvider] = []
        for name in priority:
            if name.lower() in self._providers:
                result.append(self._providers[name.lower()])
        for name in self.names():
            if self._providers[name] not in result:
                result.append(self._providers[name])
        return result
