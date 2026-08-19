from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


def should_retry_status(status_code: int) -> bool:
    return status_code in RETRY_STATUS_CODES


def backoff_seconds(attempt: int) -> float:
    return float(2 ** max(attempt - 1, 0))


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return max(0.0, (target - datetime.now(UTC)).total_seconds())
