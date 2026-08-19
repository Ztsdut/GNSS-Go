from __future__ import annotations

__all__ = ["main"]


def main() -> int:
    from gnssgo.gui.app import main as run

    return run()
