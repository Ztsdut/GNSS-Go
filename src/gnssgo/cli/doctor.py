from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path

from rich.table import Table

from gnssgo.cli.display import console
from gnssgo.config import load_settings
from gnssgo.version import __version__


def doctor() -> None:
    settings = load_settings()
    table = Table(title="GNSS Go Doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")

    table.add_row(
        "Python",
        "OK" if sys.version_info >= (3, 11) else "FAIL",
        platform.python_version(),
    )
    table.add_row("GNSS Go version", "OK", __version__)
    root = Path(settings.archive.root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        table.add_row("Output directory", "OK", str(root.resolve()))
    except OSError as exc:
        table.add_row("Output directory", "FAIL", str(exc))

    table.add_row(
        "Hatanaka support",
        "OK" if _has_module("hatanaka") else "WARN",
        "optional gnss-go[hatanaka]",
    )
    table.add_row(
        ".Z support",
        "OK" if _has_module("unlzw3") else "WARN",
        "optional gnss-go[unix-z]",
    )
    table.add_row("BKG", "OK", "provider resolver configured")
    console.print(table)


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None
