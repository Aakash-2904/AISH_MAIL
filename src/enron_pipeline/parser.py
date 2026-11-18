"""Raw email file -> ParsedEmail | ParseFailure. Safe to run in worker processes."""

from __future__ import annotations

import email
import email.policy
import re
from email.header import Header, decode_header
from email.message import Message
from pathlib import Path

from . import addresses, body, dates
from .discover import mailbox_of, relative_source
from .models import ParsedEmail, ParseFailure
from .normalize import normalize_subject

_FOLD = re.compile(r"\r?\n[ \t]*")


def _decode_chunk(chunk: bytes | str, charset: str | None) -> str:
    """Decode one decode_header chunk: declared charset -> utf-8 -> cp1252 -> latin-1."""
    if isinstance(chunk, str):
        return chunk
    if charset and charset.lower() != "unknown-8bit":
        try:
            return chunk.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    for enc in ("utf-8", "cp1252"):
        try:
            return chunk.decode(enc)
        except UnicodeDecodeError:
            continue
    return chunk.decode("latin-1")


def decode_header_value(value: str | Header | None) -> str | None:
    """Decode RFC 2047 words / raw 8-bit bytes and unfold; None if absent."""
    if value is None:
        return None
    try:
        parts = decode_header(value if isinstance(value, Header) else str(value))
        text = "".join(_decode_chunk(c, cs) for c, cs in parts)
    except Exception:  # noqa: BLE001 - malformed encoded words
        raw = str(value)
        text = raw.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    text = _FOLD.sub(" ", text).replace("\r", " ").replace("\n", " ")
    return text.strip()


def _header(msg: Message, name: str) -> str | None:
    """Decoded header value or None if the header is absent."""
    return decode_header_value(msg.get(name))


def _optional(msg: Message, name: str) -> str | None:
    """Decoded header, or None when absent or blank."""
    value = _header(msg, name)
    return value or None


def _content_type(msg: Message) -> str | None:
    if msg.get("Content-Type") is None:
        return None
    return msg.get_content_type()


def _fail(source: str, stage: str, reason: str) -> ParseFailure:
    return ParseFailure(source_file=source, stage=stage, reason=reason)  # type: ignore[arg-type]


def _build(msg: Message, source_file: str, mailbox: str) -> ParsedEmail | ParseFailure:
    """Validate mandatory fields and assemble a ParsedEmail."""
    message_id = _header(msg, "Message-ID")
    if not message_id:
        return _fail(source_file, "validate", "missing_message_id")
    date_raw = _header(msg, "Date")
    if not date_raw:
        return _fail(source_file, "validate", "missing_date")
    try:
        dt, tz_assumed = dates.parse_date(date_raw)
    except ValueError as exc:
        return _fail(source_file, "validate", f"bad_date: {exc}")
    from_address = addresses.parse_single_address(_header(msg, "From"))
    if not from_address:
        return _fail(source_file, "validate", "missing_from")
    to_addresses = addresses.parse_address_list(_header(msg, "To"))
    if not to_addresses:
        return _fail(source_file, "validate", "missing_to")
    subject = _header(msg, "Subject")
    if subject is None:
        return _fail(source_file, "validate", "missing_subject")
    text = body.extract_text(msg)
    parts = body.split_body(text)
    return ParsedEmail(
        message_id=message_id,
        date_utc=dates.to_iso_utc(dt),
        from_address=from_address,
        to_addresses=tuple(to_addresses),
        subject=subject,
        body=parts.body,
        source_file=source_file,
        mailbox=mailbox,
        normalized_subject=normalize_subject(subject),
        cc_addresses=tuple(addresses.parse_address_list(_header(msg, "Cc"))),
        bcc_addresses=tuple(addresses.parse_address_list(_header(msg, "Bcc"))),
        x_from=_optional(msg, "X-From"),
        x_to=_optional(msg, "X-To"),
        x_cc=_optional(msg, "X-cc"),
        x_bcc=_optional(msg, "X-bcc"),
        x_folder=_optional(msg, "X-Folder"),
        x_origin=_optional(msg, "X-Origin"),
        content_type=_content_type(msg),
        has_attachment=body.detect_attachment(msg, text),
        forwarded_content=parts.forwarded_content,
        quoted_content=parts.quoted_content,
        headings=parts.headings,
        date_tz_assumed=tz_assumed,
        from_is_smtp=addresses.is_smtp(from_address),
    )


def parse_bytes(raw: bytes, source_file: str, mailbox: str) -> ParsedEmail | ParseFailure:
    """Parse already-read bytes (used by parse_file and by tests)."""
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    except Exception as exc:  # noqa: BLE001
        return _fail(source_file, "parse", f"parse_error: {type(exc).__name__}: {exc}")
    try:
        return _build(msg, source_file, mailbox)
    except Exception as exc:  # noqa: BLE001 - never raise
        return _fail(source_file, "parse", f"parse_error: {type(exc).__name__}: {exc}")


def parse_file(path: Path, root: Path) -> ParsedEmail | ParseFailure:
    """Parse one raw email file. ``root`` is the dataset root (the folder that
    contains ``maildir/``). Never raises: any exception becomes a ParseFailure
    (stage ``read`` for I/O errors, ``parse``/``validate`` otherwise).

    Must be a top-level picklable function (used with ProcessPoolExecutor.map).
    """
    try:
        source_file = relative_source(Path(path), Path(root))
        mailbox = mailbox_of(Path(path), Path(root) / "maildir")
    except Exception:  # noqa: BLE001 - path outside root: still never raise
        source_file = Path(path).as_posix()
        mailbox = ""
    try:
        raw = Path(path).read_bytes()
    except Exception as exc:  # noqa: BLE001
        return _fail(source_file, "read", f"read_error: {type(exc).__name__}: {exc}")
    return parse_bytes(raw, source_file, mailbox)
