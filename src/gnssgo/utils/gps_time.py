from __future__ import annotations

from datetime import date, datetime, timedelta

GPS_EPOCH = date(1980, 1, 6)


def datetime_to_gpsweek(value: date | datetime) -> tuple[int, int]:
    day = value.date() if isinstance(value, datetime) else value
    delta = day - GPS_EPOCH
    week, dow = divmod(delta.days, 7)
    return week, dow


def gpsweek_to_datetime(week: int, day_of_week: int = 0) -> datetime:
    if week < 0:
        raise ValueError("GPS week must be non-negative.")
    if not 0 <= day_of_week <= 6:
        raise ValueError("GPS day-of-week must be between 0 and 6.")
    day = GPS_EPOCH + timedelta(weeks=week, days=day_of_week)
    return datetime.combine(day, datetime.min.time())
