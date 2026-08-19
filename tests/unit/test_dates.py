from datetime import date

from gnssgo.utils.dates import datetime_to_doy, doy_to_date, parse_date
from gnssgo.utils.gps_time import datetime_to_gpsweek, gpsweek_to_datetime


def test_parse_calendar_and_doy_dates() -> None:
    assert parse_date("2026-08-01") == date(2026, 8, 1)
    assert parse_date("2026-213") == date(2026, 8, 1)


def test_leap_year_doy() -> None:
    assert doy_to_date(2024, 60) == date(2024, 2, 29)
    assert datetime_to_doy(date(2024, 12, 31)) == 366


def test_gps_week_roundtrip() -> None:
    week, dow = datetime_to_gpsweek(date(1980, 1, 6))
    assert (week, dow) == (0, 0)
    assert gpsweek_to_datetime(week, dow).date() == date(1980, 1, 6)
