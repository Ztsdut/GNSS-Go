from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BrowserAutomationBackend(ABC):
    """Future hook for data portals that require browser-assisted downloads."""

    @abstractmethod
    async def open_page(self, url: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def login(self, username: str | None = None, password: str | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def fill_form(self, fields: dict[str, str]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def click(self, selector: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def wait_download(self, timeout_seconds: int = 300) -> Path:
        raise NotImplementedError

    @abstractmethod
    async def capture_file(self, destination: Path) -> Path:
        raise NotImplementedError


DEFAULT_BROWSER_BACKEND: BrowserAutomationBackend | None = None
