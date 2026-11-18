"""Tests for dates.parse_date / to_iso_utc."""
from datetime import datetime, timezone

import pytest

from enron_pipeline.dates import parse_date, to_iso_utc


def iso(value: str) -> str:
    return to_iso_utc(parse_date(value)[0])


@pytest.mark.parametrize(
    "value, expected",
    [
        ("Tue, 29 May 2001 07:17:11 -0700 (PDT)", "2001-05-29T14:17:11+00:00"),
        ("Mon, 14 May 2001 10:00:00 CDT", "2001-05-14T15:00:00+00:00"),
        ("Mon, 14 May 2001 10:00:00 PST", "2001-05-14T18:00:00+00:00"),
        ("Mon, 14 May 2001 10:00:00 MDT", "2001-05-14T16:00:00+00:00"),
        ("Mon, 14 May 2001 10:00:00 EST", "2001-05-14T15:00:00+00:00"),
        ("Mon, 14 May 2001 10:00:00 GMT", "2001-05-14T10:00:00+00:00"),
        ("Mon, 14 May 2001 10:00:00 UT", "2001-05-14T10:00:00+00:00"),
        ("Mon, 14 May 2001 10:00:00 +0530", "2001-05-14T04:30:00+00:00"),
        ("14 May 2001 10:00 -0800", "2001-05-14T18:00:00+00:00"),
    ],
)
def test_zones(value: str, expected: str) -> None:
    assert iso(value) == expected
    assert parse_date(value)[1] is False


def test_comment_only_zone_is_used() -> None:
    # No numeric offset, zone only in trailing comment.
    assert iso("Mon, 14 May 2001 10:00:00 (CDT)") == "2001-05-14T15:00:00+00:00"


def test_dateutil_fallback_with_abbreviation() -> None:
    assert iso("May 14, 2001 10:00 AM CST") == "2001-05-14T16:00:00+00:00"


def test_naive_assumes_minus_0800() -> None:
    dt, assumed = parse_date("Wed, 9 Jan 2002 13:00:00")
    assert assumed is True
    assert to_iso_utc(dt) == "2002-01-09T21:00:00+00:00"


def test_result_is_aware_utc() -> None:
    dt, _ = parse_date("Tue, 29 May 2001 07:17:11 -0700")
    assert dt.utcoffset() is not None and dt.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("value", [None, "", "   ", "sometime next week"])
def test_bad_dates_raise_value_error(value) -> None:
    with pytest.raises(ValueError):
        parse_date(value)


def test_to_iso_utc_format_and_naive_rejected() -> None:
    dt = datetime(2001, 1, 2, 3, 4, 5, 999, tzinfo=timezone.utc)
    assert to_iso_utc(dt) == "2001-01-02T03:04:05+00:00"
    with pytest.raises(ValueError):
        to_iso_utc(datetime(2001, 1, 1))
