from __future__ import annotations

from gnssgo.config import Settings, load_settings


class SettingsService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()

    def provider_priority(self) -> list[str]:
        return list(self.settings.provider.priority)

    def product_provider_priority(self) -> list[str]:
        values = list(self.settings.products.provider_priority)
        if "igsfiles" not in values:
            values.append("igsfiles")
        return values
