from datetime import UTC, datetime, timedelta

import pytest

from faangscout.normalize import (
    collapse,
    detect_remote,
    detect_seniority,
    expand_role_terms,
    normalize_title,
    parse_relative,
    parse_timestamp,
    slugify,
    strip_html,
)


def test_slugify():
    assert slugify("Meta Platforms, Inc.") == "meta-platforms-inc"
    assert slugify("  Stripe  ") == "stripe"


def test_collapse():
    assert collapse("Meta Platforms") == "metaplatforms"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Sr. Software Engineer II, Backend (Remote)", "sr software engineer backend"),
        ("Software Engineer, New Grad", "software engineer"),
    ],
)
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected


def test_expand_role_terms_includes_synonyms():
    terms = expand_role_terms("backend engineer")
    assert "back end" in terms
    assert "sde" in terms or "software development engineer" in terms


def test_expand_role_terms_empty():
    assert expand_role_terms("") == []


def test_detect_seniority():
    assert "senior" in detect_seniority("Senior Software Engineer")
    assert "intern" in detect_seniority("Summer Internship - SWE")
    assert detect_seniority("Software Engineer") == set()


def test_detect_remote():
    assert detect_remote("Remote - US") is True
    assert detect_remote("On-site, San Francisco") is False
    assert detect_remote("San Francisco, CA") is None
    assert detect_remote("") is None


def test_strip_html():
    assert strip_html("<p>Hello &amp; welcome</p>") == "Hello & welcome"
    assert strip_html(None) == ""
    assert strip_html("<script>evil()</script>Safe text") == "Safe text"


class TestParseTimestamp:
    def test_iso_with_z(self):
        result = parse_timestamp("2026-09-02T10:00:00Z")
        assert result == datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)

    def test_epoch_seconds(self):
        result = parse_timestamp(1756900000)
        assert result.tzinfo is not None
        assert result.year == 2025

    def test_epoch_milliseconds(self):
        result_ms = parse_timestamp(1756900000000)
        result_s = parse_timestamp(1756900000)
        assert result_ms == result_s

    def test_human_date(self):
        result = parse_timestamp("March 5, 2026")
        assert result == datetime(2026, 3, 5, tzinfo=UTC)

    def test_none_and_empty(self):
        assert parse_timestamp(None) is None
        assert parse_timestamp("") is None

    def test_datetime_passthrough(self):
        naive = datetime(2026, 1, 1)
        assert parse_timestamp(naive).tzinfo is not None
        aware = datetime(2026, 1, 1, tzinfo=UTC)
        assert parse_timestamp(aware) == aware

    def test_garbage_returns_none(self):
        assert parse_timestamp("not a date at all") is None

    def test_relative_delegation(self):
        now = datetime(2026, 6, 1, tzinfo=UTC)
        result = parse_timestamp("Posted 3 Days Ago", now=now)
        assert result == now - timedelta(days=3)


class TestParseRelative:
    def test_today(self):
        now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        assert parse_relative("Posted Today", now=now) == now

    def test_yesterday(self):
        now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        assert parse_relative("Posted Yesterday", now=now) == now - timedelta(days=1)

    def test_n_days_ago(self):
        now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        assert parse_relative("Posted 30+ Days Ago", now=now) == now - timedelta(days=30)

    def test_hours_ago(self):
        now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        assert parse_relative("5 hours ago", now=now) == now - timedelta(hours=5)

    def test_unparseable(self):
        assert parse_relative("") is None
        assert parse_relative("some random text") is None
