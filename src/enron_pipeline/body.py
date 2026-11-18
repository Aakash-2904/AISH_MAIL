"""Body text extraction, forwarded/quoted splitting, headings, attachments."""

from __future__ import annotations

from dataclasses import dataclass
from email.message import Message


@dataclass(frozen=True, slots=True)
class BodyParts:
    body: str  # primary body, trimmed ("" allowed)
    forwarded_content: str | None
    quoted_content: str | None
    headings: str | None  # newline-joined, None if none


def extract_text(msg: Message) -> str:
    """Return the decoded text body: text/plain parts (multipart-aware), falling
    back to tag-stripped text/html; charset fallback part charset -> cp1252 ->
    latin-1. Never raises."""
    raise NotImplementedError


def split_body(text: str) -> BodyParts:
    """Split raw body text into primary body / forwarded / quoted content and
    extract headings from the primary body (plan section 4.3)."""
    raise NotImplementedError


def extract_headings(text: str) -> str | None:
    """Heading heuristic (plan 4.3); newline-joined headings or None."""
    raise NotImplementedError


def detect_attachment(msg: Message, text: str) -> bool:
    """True if any MIME part is an attachment / has a filename, or the body
    references one (``<<budget.xls>>``, ``<< File: ... >>``, attached x.doc...)."""
    raise NotImplementedError
