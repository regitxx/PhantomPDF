"""Tests for the forensic verification and cleanup module."""

from datetime import datetime, timezone, timedelta

import pytest

from phantompdf.forensics import parse_pdf_date


class TestParsePdfDate:
    """Test PDF date string parsing."""

    def test_full_date_with_timezone(self):
        dt = parse_pdf_date("D:20260403085752+03'00'")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 3
        assert dt.hour == 8
        assert dt.minute == 57
        assert dt.second == 52
        tz = timezone(timedelta(hours=3))
        assert dt.tzinfo == tz

    def test_negative_timezone(self):
        dt = parse_pdf_date("D:20260101120000-05'00'")
        assert dt is not None
        tz = timezone(timedelta(hours=-5))
        assert dt.tzinfo == tz

    def test_utc_timezone(self):
        dt = parse_pdf_date("D:20260101120000+00'00'")
        assert dt is not None
        assert dt.tzinfo == timezone(timedelta(0))

    def test_no_timezone(self):
        dt = parse_pdf_date("D:20260403085752")
        assert dt is not None
        assert dt.year == 2026
        assert dt.tzinfo is None

    def test_minutes_only(self):
        dt = parse_pdf_date("D:202604030857")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 3

    def test_empty_string(self):
        assert parse_pdf_date("") is None

    def test_none_input(self):
        assert parse_pdf_date(None) is None

    def test_invalid_format(self):
        assert parse_pdf_date("not a date") is None

    def test_d_prefix_stripped(self):
        dt = parse_pdf_date("D:20260101000000")
        assert dt is not None
        assert dt.year == 2026
