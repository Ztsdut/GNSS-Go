from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, datetime, timedelta

from gnssgo.exceptions import ConfigurationError

DATE_RE = re.compile(r"^(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})$")
DOY_RE = re.compile(r"^(?P<year>\d{4})-(?P<doy>\d{1,3})$")


def parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = value.strip()
    if match := DATE_RE.match(text):
        return date(int(match["year"]), int(match["month"]), int(match["day"]))
    if match := DOY_RE.match(text):
        return doy_to_date(int(match["year"]), int(match["doy"]))
    raise ConfigurationError(f"Unsupported date format: {value!r}. Use YYYY-MM-DD or YYYY-DDD.")


def doy_to_date(year: int, doy: int) -> date:
    if doy < 1:
        raise ConfigurationError("Day-of-year must be >= 1.")
    first = date(year, 1, 1)
    result = first + timedelta(days=doy - 1)
    if result.year != year:
        raise ConfigurationError(f"Day-of-year {doy} is out of range for {year}.")
    return result


def datetime_to_doy(value: date | datetime) -> int:
    day = value.date() if isinstance(value, datetime) else value
    return int(day.strftime("%j"))


def doy_to_datetime(year: int, doy: int) -> datetime:
    return datetime.combine(doy_to_date(year, doy), datetime.min.time())


def iter_dates(start: str | date | datetime, end: str | date | datetime) -> Iterator[date]:
    current = parse_date(start)
    final = parse_date(end)
    if final < current:
        raise ConfigurationError("End date must be on or after start date.")
    while current <= final:
        yield current
        current += timedelta(days=1)
