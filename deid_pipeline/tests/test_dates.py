from datetime import datetime

from deid.core.dates import parse_date, parse_date_with_format, shift_date


def test_parse_common_formats():
    assert parse_date("03/14/1958") == datetime(1958, 3, 14)
    assert parse_date("1958-02-19") == datetime(1958, 2, 19)
    assert parse_date("February 6, 2025") == datetime(2025, 2, 6)
    assert parse_date("6 February 2025") == datetime(2025, 2, 6)


def test_hl7_compact_date_not_misparsed():
    # %Y%m%d must win over flexible-width datetime formats
    assert parse_date("19731214") == datetime(1973, 12, 14)


def test_parse_rejects_non_dates():
    assert parse_date("not a date") is None
    assert parse_date("") is None
    assert parse_date(None) is None


def test_parse_returns_matched_format():
    dt, fmt = parse_date_with_format(" 19580314 ")  # stripped
    assert (dt, fmt) == (datetime(1958, 3, 14), "%Y%m%d")


def test_shift_preserves_format():
    assert shift_date("03/14/1958", 10) == "03/24/1958"
    assert shift_date("19580314", 1) == "19580315"
    assert shift_date("Mar 14, 1958", 1) == "Mar 15, 1958"
    assert shift_date("garbage", 5) is None
