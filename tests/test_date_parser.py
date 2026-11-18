from datetime import date

from src.parsers.date_parser import parse_posted_date


def test_parse_date_with_offset():
    ref = date(2026, 8, 24)
    result = parse_posted_date("2026-07-30T17:54:22+08:00", ref)
    assert result["posted_date"] == "2026-07-30"
    assert result["job_age_days"] == 25   # calendar days: 2026-07-30 -> 2026-08-24


def test_parse_date_plain():
    ref = date(2026, 8, 24)
    result = parse_posted_date("2026-08-24", ref)
    assert result["posted_date"] == "2026-08-24"
    assert result["job_age_days"] == 0


def test_parse_date_empty():
    result = parse_posted_date("", date(2026, 8, 24))
    assert result["posted_date"] is None
    assert result["job_age_days"] is None


def test_parse_date_invalid():
    result = parse_posted_date("not-a-date", date(2026, 8, 24))
    assert result["posted_date"] is None
