"""Body text extraction, forwarded/quoted splitting, headings, attachments."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from email.message import Message


@dataclass(frozen=True, slots=True)
class BodyParts:
    body: str  # primary body, trimmed ("" allowed)
    forwarded_content: str | None
    quoted_content: str | None
    headings: str | None  # newline-joined, None if none


# --------------------------------------------------------------------------- text
_TAG = re.compile(r"<[^>]+>")
_BLOCK_TAG = re.compile(r"<\s*(br|/p|/div|/tr|/li|/h\d)\b[^>]*>", re.I)
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)


def _decode_bytes(payload: bytes, charset: str | None) -> str:
    """Decode with part charset, then cp1252, then latin-1 (never fails)."""
    for enc in (charset, "cp1252"):
        if not enc:
            continue
        try:
            return payload.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("latin-1")


def _part_text(part: Message) -> str:
    """Decoded text of one non-multipart part."""
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    if isinstance(payload, str):
        return payload
    return _decode_bytes(payload, part.get_content_charset())


def _strip_html(text: str) -> str:
    """Very small HTML -> text conversion (no external deps)."""
    text = _SCRIPT.sub(" ", text)
    text = _BLOCK_TAG.sub("\n", text)
    return html.unescape(_TAG.sub("", text))


def _is_attachment_part(part: Message) -> bool:
    disp = (part.get("Content-Disposition") or "").lower()
    return "attachment" in disp or bool(part.get_filename())


def extract_text(msg: Message) -> str:
    """Return the decoded text body: text/plain parts (multipart-aware), falling
    back to tag-stripped text/html; charset fallback part charset -> cp1252 ->
    latin-1. Never raises."""
    try:
        plain: list[str] = []
        htmls: list[str] = []
        for part in msg.walk():
            if part.is_multipart() or (part is not msg and _is_attachment_part(part)):
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                plain.append(_part_text(part))
            elif ctype == "text/html":
                htmls.append(_strip_html(_part_text(part)))
        chunks = plain or htmls
        if not chunks and not msg.is_multipart():
            chunks = [_part_text(msg)]
        text = "\n".join(chunks)
        return text.replace("\r\n", "\n").replace("\r", "\n")
    except Exception:  # noqa: BLE001 - never raise
        return ""


# --------------------------------------------------------------------------- split
_FORWARD_MARKERS = re.compile(
    r"^\s*(?:-{2,}\s*Original Message\s*-{2,}"
    r"|-{3,}\s*Forwarded by"
    r"|-{3,}\s*Forwarded Message\s*-{3,}"
    r"|Begin forwarded message:)",
    re.I,
)
_OUTLOOK_FROM = re.compile(r"^\s*From:\s*\S", re.I)
_OUTLOOK_NEXT = re.compile(r"^\s*(Sent|To|Subject):", re.I)
_QUOTE_LINE = re.compile(r"^\s*>")
_WROTE = re.compile(r"^\s*On\s.{4,200}\bwrote:\s*$", re.I)


def _forward_index(lines: list[str]) -> int | None:
    """Index of the first forwarded-content marker line, or None."""
    for i, line in enumerate(lines):
        if _FORWARD_MARKERS.match(line):
            return i
        if _OUTLOOK_FROM.match(line) and any(
            _OUTLOOK_NEXT.match(nxt) for nxt in lines[i + 1 : i + 5]
        ):
            return i
    return None


def _split_quotes(lines: list[str]) -> tuple[list[str], list[str]]:
    """Separate ``>``-quoted lines (and ``On ... wrote:`` lead-ins)."""
    primary: list[str] = []
    quoted: list[str] = []
    prev_quoted = False
    for line in lines:
        is_q = bool(_QUOTE_LINE.match(line) or _WROTE.match(line))
        if is_q:
            if quoted and not prev_quoted:
                quoted.append("")  # blank line between separate quote blocks
            quoted.append(line)
        else:
            primary.append(line)
        prev_quoted = is_q
    return primary, quoted


def _joined_or_none(lines: list[str]) -> str | None:
    text = "\n".join(lines).strip()
    return text or None


def split_body(text: str) -> BodyParts:
    """Split raw body text into primary body / forwarded / quoted content and
    extract headings from the primary body (plan section 4.3)."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    idx = _forward_index(lines)
    forwarded = None
    if idx is not None:
        forwarded = _joined_or_none(lines[idx:])
        lines = lines[:idx]
    primary, quoted = _split_quotes(lines)
    body = "\n".join(primary).strip()
    return BodyParts(
        body=body,
        forwarded_content=forwarded,
        quoted_content=_joined_or_none(quoted),
        headings=extract_headings(body),
    )


# --------------------------------------------------------------------------- headings
_HEADER_LIKE = re.compile(r"^\s*(to|from|subject|cc|bcc|sent|date|re|fw|fwd)\s*:", re.I)
_UNDERLINE = re.compile(r"^\s*(={3,}|-{3,})\s*$")
_NUMBERED = re.compile(r"^\s*(?:\d{1,2}\.|[IVXivx]{1,4}\.|[A-Za-z]\))\s+(?P<title>.+)$")
_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with", "&", "-"}
_URLISH = re.compile(r"(https?:|www\.|@|\[image\])", re.I)
_TABLE = re.compile(r"\S {3,}\S")  # column-aligned table rows
_CAPS_STOP = {"FYI", "OK", "IMAGE", "PS", "NB", "ASAP", "THANKS", "HI", "TIA"}


def _is_title_case(text: str) -> bool:
    words = [w for w in text.split() if any(c.isalpha() for c in w)]
    if not words or not words[0][0].isupper():
        return False
    return all(w[0].isupper() or w.lower() in _SMALL_WORDS for w in words)


def _is_all_caps(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    non_space = [c for c in line if not c.isspace()]
    return (
        len(letters) >= 2
        and line.upper() == line
        and len(letters) >= 0.5 * len(non_space)
    )


def _caps_heading(line: str) -> bool:
    """ALL-CAPS rule, minus common false positives seen in the Enron sample:
    FYI/OK/[IMAGE] tokens, sentence-ending lines and lone 2-letter table cells."""
    if not _is_all_caps(line) or line.endswith((".", "!", "?")):
        return False
    if line.strip("[]().,!-: ").upper() in _CAPS_STOP:
        return False
    words = line.split()
    return not (len(words) == 1 and sum(c.isalpha() for c in line) < 3)


def _colon_heading(line: str, nxt: str, first_line: bool) -> bool:
    """``Title:`` followed by content; skips salutations (first line of the
    body, e.g. ``Daren:``), lowercase sentence fragments and long phrases."""
    if not line.endswith(":") or len(line) < 2 or not nxt.strip() or first_line:
        return False
    if not line[0].isupper() or "," in line or len(line.split()) > 6:
        return False
    return not _WROTE.match(line)


def _is_heading(line: str, nxt: str, first_line: bool = False) -> bool:
    """Plan 4.3 heading heuristic for one stripped line + the following line."""
    if not line or len(line) > 80 or len(line.split()) > 10 or _TABLE.search(line):
        return False
    if _HEADER_LIKE.match(line) or _URLISH.search(line) or _QUOTE_LINE.match(line):
        return False
    if _caps_heading(line):
        return True
    if _UNDERLINE.match(nxt) and any(c.isalpha() for c in line):
        return True
    m = _NUMBERED.match(line)
    if m and _is_title_case(m.group("title")) and not line.endswith("."):
        return True
    return _colon_heading(line, nxt, first_line)


def extract_headings(text: str) -> str | None:
    """Heading heuristic (plan 4.3); newline-joined headings or None."""
    raw_lines = (text or "").splitlines()
    lines = [ln.strip() for ln in raw_lines]
    first = next((i for i, ln in enumerate(lines) if ln), -1)
    found: list[str] = []
    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        is_table = "\t" in raw_lines[i].strip()
        if not is_table and _is_heading(line, nxt, i == first) and line not in found:
            found.append(line)
    return "\n".join(found) or None


# --------------------------------------------------------------------------- attachments
_EXT = r"(?:docx?|xlsx?|pptx?|pdf|zip|txt|rtf|csv|wpd|gif|jpe?g|tiff?|msg|htm)"
_ANGLE_REF = re.compile(r"<<[^<>\n]{1,200}\." + _EXT + r"\s*>>", re.I)
_FILE_REF = re.compile(r"<<\s*File:[^<>\n]+>>", re.I)
_DASH_LINE = re.compile(r"^\s*-\s+[^\s].{0,150}\." + _EXT + r"\s*$", re.I | re.M)
_NEAR_EXT = re.compile(r"\b[\w\-]+\.(?:docx?|xlsx?|pptx?|pdf|zip)\b", re.I)
_ATTACH_WORD = re.compile(r"attach", re.I)
_WINDOW = 200


def _body_mentions_attachment(text: str) -> bool:
    if _ANGLE_REF.search(text) or _FILE_REF.search(text) or _DASH_LINE.search(text):
        return True
    for m in _NEAR_EXT.finditer(text):
        window = text[max(0, m.start() - _WINDOW) : m.end() + _WINDOW]
        if _ATTACH_WORD.search(window):
            return True
    return False


_NAME_ANGLE = re.compile(r"<<\s*(?:File:\s*)?([^<>\n]{1,200}?\." + _EXT + r")\s*>>", re.I)
_NAME_DASH = re.compile(r"^\s*-\s+([^\s].{0,150}?\." + _EXT + r")\s*$", re.I | re.M)


def attachment_names(text: str | None) -> frozenset[str]:
    """Casefolded attachment filenames referenced in raw (un-normalised) text:
    ``<<name.ext>>``, ``<< File: name.ext >>`` and `` - name.ext`` lines.
    Used by dedup to keep apart templated emails that differ only in the
    file they carry (e.g. weekly newsletter issues)."""
    if not text:
        return frozenset()
    found = (m.group(1) for rx in (_NAME_ANGLE, _NAME_DASH) for m in rx.finditer(text))
    return frozenset(" ".join(n.split()).casefold() for n in found)


def detect_attachment(msg: Message, text: str) -> bool:
    """True if any MIME part is an attachment / has a filename, or the body
    references one (``<<budget.xls>>``, ``<< File: ... >>``, attached x.doc...)."""
    try:
        if msg is not None and any(_is_attachment_part(p) for p in msg.walk() if not p.is_multipart()):
            return True
    except Exception:  # noqa: BLE001
        pass
    return _body_mentions_attachment(text or "")
