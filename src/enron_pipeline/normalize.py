"""Subject/body normalisation shared by parser (normalized_subject) and dedup."""

from __future__ import annotations

import re

# Leading reply/forward prefixes: re:, fw:, fwd:, aw:, re[2]:, [fwd: ...] style brackets.
_PREFIX = re.compile(r"^\s*[\[(]?\s*(?:re|fw|fwd|aw)\s*(?:\[\d+\]|\(\d+\))?\s*:\s*", re.IGNORECASE)
_WS = re.compile(r"\s+")


def strip_subject_prefixes(subject: str) -> str:
    """Repeatedly remove leading Re:/Fw:/Fwd:/Aw: prefixes; keeps original casing."""
    s = subject or ""
    while True:
        new = _PREFIX.sub("", s, count=1)
        if new == s:
            break
        s = new
    s = s.strip()
    # drop a trailing bracket left over from "[Fwd: subject]"
    if s.endswith(("]", ")")) and not any(c in s for c in "[("):
        s = s[:-1].rstrip()
    return _WS.sub(" ", s)


def normalize_subject(subject: str | None) -> str:
    """Prefix-stripped, whitespace-collapsed, casefolded subject (dedup key)."""
    return strip_subject_prefixes(subject or "").casefold().strip()


def normalize_body(body: str | None) -> str:
    """Casefolded, whitespace-collapsed body used for fuzzy comparison."""
    return _WS.sub(" ", (body or "").casefold()).strip()
