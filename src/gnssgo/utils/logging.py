from __future__ import annotations

import logging

from rich.logging import RichHandler

SECRET_KEYS = ("password", "authorization", "cookie", "token")


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for key in SECRET_KEYS:
            redacted = redacted.replace(key, "[redacted-key]")
        record.msg = redacted
        record.args = ()
        return True


def configure_logging(verbosity: int = 0, log_file: str | None = None) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG

    handlers: list[logging.Handler] = [RichHandler(markup=True, show_time=False)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=level, handlers=handlers, format="%(message)s", force=True)
    for handler in logging.getLogger().handlers:
        handler.addFilter(SecretFilter())
