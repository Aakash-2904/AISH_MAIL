"""Date header parsing -> timezone-aware UTC."""

from __future__ import annotations

from datetime import datetime


def parse_date(value: str | None) -> tuple[datetime, bool]:
    """Parse an email Date header into an aware UTC datetime.

    Returns ``(dt_utc, tz_assumed)`` where ``tz_assumed`` is True when the value
    had no zone info and -0800 was assumed. Raises ``ValueError`` if missing or
    unparseable. Handles numeric offsets, tz abbreviations (PST/PDT/MST/MDT/
    CST/CDT/EST/EDT/GMT/UT/UTC) and trailing comments like ``(PDT)``.
    """
    raise NotImplementedError


def to_iso_utc(dt: datetime) -> str:
    """Format an aware datetime as ``YYYY-MM-DDTHH:MM:SS+00:00`` in UTC."""
    raise NotImplementedError
