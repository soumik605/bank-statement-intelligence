# backend/tests/test_dates.py
import datetime as dt

import pytest

from app.utils.dates import parse_date

JAN_5 = dt.date(2026, 1, 5)


def test_day_month_order():
    # India is day-first: 05/01/2026 is 5 January, never 1 May.
    assert parse_date("05/01/2026") == JAN_5


def test_us_month_first_is_rejected():
    # 13 can only be a day, so a US-style date fails loudly instead of being swapped.
    with pytest.raises(ValueError):
        parse_date("01/13/2026")


@pytest.mark.parametrize("text", [
    "05/01/2026",
    "05-01-2026",
    "05.01.2026",
    "05/01/26",
    "05-01-26",
    "05-Jan-2026",
    "05-JAN-2026",
    "05-Jan-26",
    "05 Jan 2026",
    "05 Jan 26",
    "5 January 2026",
    "05/Jan/2026",
    "2026-01-05",
    "5/1/2026",
])
def test_supported_formats(text):
    assert parse_date(text) == JAN_5


def test_sept_spelling():
    assert parse_date("05-Sept-2026") == dt.date(2026, 9, 5)


def test_strips_time_and_extra_whitespace():
    assert parse_date("05/01/2026 14:32:10") == JAN_5
    assert parse_date("  05  Jan   2026 ") == JAN_5
    assert parse_date("05-Jan-2026 02:30 PM") == JAN_5


def test_passes_through_date_objects():
    assert parse_date(JAN_5) == JAN_5
    assert parse_date(dt.datetime(2026, 1, 5, 9, 30)) == JAN_5


@pytest.mark.parametrize("text", ["", "31/02/2026", "2026/31/01", "Jan 5 2026", "yesterday"])
def test_rejects_invalid(text):
    with pytest.raises(ValueError):
        parse_date(text)
