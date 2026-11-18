"""Date header parsing -> timezone-aware UTC."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from dateutil import parser as du_parser
from dateutil.tz import tzoffset

_H = 3600
TZINFOS: dict[str, int] = {
    "PST": -8 * _H, "PDT": -7 * _H,
    "MST": -7 * _H, "MDT": -6 * _H,
    "CST": -6 * _H, "CDT": -5 * _H,
    "EST": -5 * _H, "EDT": -4 * _H,
    "GMT": 0, "UT": 0, "UTC": 0, "Z": 0,
}
ASSUMED_TZ = timezone(timedelta(hours=-8))

_COMMENT = re.compile(r"\(([^()]*)\)")
_TZ_WORD = re.compile(r"\b(" + "|".join(sorted(TZINFOS, key=len, reverse=True)) + r")\b", re.I)
_NUM_OFFSET = re.compile(r"[+-]\d{4}\b")


def _comment_tz(value: str) -> str | None:
    """Return a known tz abbreviation found inside a ``(...)`` comment."""
    for inner in _COMMENT.findall(value):
        m = _TZ_WORD.search(inner)
        if m:
            return m.group(1).upper()
    return None


def _stdlib_parse(value: str) -> datetime | None:
    """RFC 2822 parse via the stdlib; None on failure."""
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _dateutil_parse(value: str) -> datetime:
    """Lenient fallback parse; raises ValueError on failure."""
    tzinfos = {k: tzoffset(k, v) for k, v in TZINFOS.items()}
    try:
        return du_parser.parse(value, tzinfos=tzinfos, fuzzy=True)
    except (du_parser.ParserError, ValueError, OverflowError, TypeError) as exc:
        raise ValueError(f"unparseable date {value!r}") from exc


def _has_zone(value: str) -> bool:
    """True if the cleaned header text carries any zone information."""
    return bool(_NUM_OFFSET.search(value) or _TZ_WORD.search(value))


def parse_date(value: str | None) -> tuple[datetime, bool]:
    """Parse an email Date header into an aware UTC datetime.

    Returns ``(dt_utc, tz_assumed)`` where ``tz_assumed`` is True when the value
    had no zone info and -0800 was assumed. Raises ``ValueError`` if missing or
    unparseable. Handles numeric offsets, tz abbreviations (PST/PDT/MST/MDT/
    CST/CDT/EST/EDT/GMT/UT/UTC) and trailing comments like ``(PDT)``.
    """
    if value is None or not value.strip():
        raise ValueError("empty date")
    raw = " ".join(value.split())
    cleaned = _COMMENT.sub(" ", raw).strip()
    comment_tz = _comment_tz(raw)
    if comment_tz and not _has_zone(cleaned):
        cleaned = f"{cleaned} {comment_tz}"
    dt = _stdlib_parse(cleaned)
    if dt is None or (dt.tzinfo is None and _has_zone(cleaned)):
        dt = _dateutil_parse(cleaned)
    tz_assumed = False
    if dt.tzinfo is None or dt.utcoffset() is None:
        dt = dt.replace(tzinfo=ASSUMED_TZ)
        tz_assumed = True
    try:
        return dt.astimezone(timezone.utc), tz_assumed
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"date out of range {value!r}") from exc


def to_iso_utc(dt: datetime) -> str:
    """Format an aware datetime as ``YYYY-MM-DDTHH:MM:SS+00:00`` in UTC."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
