"""Duplicate-notice rendering, dry-run .eml output and live-send orchestration."""

from __future__ import annotations

import csv
import hashlib
import logging
import re
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from email import policy
from email.generator import BytesGenerator
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from . import db
from .models import NoticeInput, Sender, SendResult
from .normalize import strip_subject_prefixes

log = logging.getLogger(__name__)

SEND_LOG_COLUMNS = ("timestamp", "recipient", "subject", "status", "error", "intended_recipient", "message_id")

SUBJECT_PREFIX = "[Duplicate Notice] Re: "
REDIRECT_LINE = "[Redirected for testing; intended recipient: {recipient}]"
MAX_STEM_LEN = 120
SEVEN_BIT_MAX_LINE = 78
# Generation policy for notices: no header folding (max_line_length=None) and
# raw UTF-8 headers (utf8=True) so the .eml shows every template line verbatim.
NOTICE_POLICY = policy.default.clone(max_line_length=None, utf8=True)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

_BODY_TEMPLATE = """\
This is an automated notification from the Email Deduplication System.

Your email has been identified as a potential duplicate:

  Your Email (Flagged):
    Message-ID:  {dup_id}
    Date Sent:   {dup_date}
    Subject:     {dup_subject}

  Original Email on Record:
    Message-ID:  {orig_id}
    Date Sent:   {orig_date}

  Similarity Score: {score}%

If this was NOT a duplicate and you intended to send this email,
please reply with CONFIRM to restore it to active status.

No action is required if this is indeed a duplicate.
"""


def _to_utc(now: datetime) -> datetime:
    """Treat naive datetimes as UTC; convert aware ones to UTC."""
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def _iso_utc(now: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SS+00:00`` for DB/log timestamps."""
    return _to_utc(now).isoformat(timespec="seconds")


def format_display_date(iso_utc: str) -> str:
    """ISO UTC string -> RFC 2822 (``Mon, 14 May 2001 23:39:00 +0000``).

    Unparseable input is returned unchanged so rendering never fails on a date.
    """
    try:
        return format_datetime(_to_utc(datetime.fromisoformat(iso_utc)))
    except (TypeError, ValueError):
        return iso_utc


def notice_subject(n: NoticeInput) -> str:
    """``[Duplicate Notice] Re: <original subject without Re:/Fwd: prefixes>``."""
    return SUBJECT_PREFIX + strip_subject_prefixes(n.orig_subject)


def render_body(n: NoticeInput) -> str:
    """Body text exactly matching the assignment template (score: 1 decimal)."""
    return _BODY_TEMPLATE.format(
        dup_id=n.dup_message_id,
        dup_date=format_display_date(n.dup_date_utc),
        dup_subject=n.dup_subject,
        orig_id=n.orig_message_id,
        orig_date=format_display_date(n.orig_date_utc),
        score=f"{n.similarity_score:.1f}",
    )


def body_cte(body: str) -> str:
    """``"8bit"`` when a line is longer than 78 chars or non-ASCII, else
    ``"7bit"``. Always explicit so set_content never picks quoted-printable or
    base64 (and never consults NOTICE_POLICY.max_line_length, which is None);
    the body stays character-for-character in the raw .eml."""
    too_long = any(len(line) > SEVEN_BIT_MAX_LINE for line in body.splitlines())
    return "8bit" if too_long or not body.isascii() else "7bit"


def render_notice(n: NoticeInput, now: datetime, to: str | None = None) -> EmailMessage:
    """Full message: To (default n.dup_from), Subject, Date (RFC 2822 of now),
    References (n.dup_message_id), body from render_body (7bit or 8bit, never
    QP/base64; headers unfolded, see NOTICE_POLICY)."""
    msg = EmailMessage(policy=NOTICE_POLICY)
    msg["To"] = to if to is not None else n.dup_from
    msg["Subject"] = notice_subject(n)
    msg["Date"] = format_datetime(_to_utc(now))
    msg["References"] = n.dup_message_id
    body = render_body(n)
    msg.set_content(body, charset="utf-8", cte=body_cte(body))
    return msg


def _short_hash(text: str) -> str:
    """8 hex chars of sha1, used to disambiguate file names."""
    return hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()[:8]


def safe_filename(message_id: str) -> str:
    """Message-ID -> ``[A-Za-z0-9._-]`` file stem (non-empty, <= MAX_STEM_LEN + 9 chars).

    ``<123.JavaMail.evans@thyme>`` -> ``123.JavaMail.evans_thyme``. Over-long
    stems are truncated and suffixed with a hash so they stay distinct.
    """
    stem = _UNSAFE.sub("_", message_id or "").strip("._-")
    if not stem:
        return "message_" + _short_hash(message_id or "")
    if stem.split(".")[0].upper() in _WIN_RESERVED:
        stem = "_" + stem
    if len(stem) > MAX_STEM_LEN:
        stem = stem[:MAX_STEM_LEN].rstrip("._-") + "_" + _short_hash(message_id)
    return stem


def _unique_stem(message_id: str, used: set[str]) -> str:
    """safe_filename, plus a hash suffix if the (case-insensitive) stem is taken."""
    stem = safe_filename(message_id)
    if stem.lower() in used:
        stem = f"{stem}_{_short_hash(message_id)}"
    used.add(stem.lower())
    return stem


def _write_eml(msg: EmailMessage, path: Path) -> None:
    """Serialise one message to ``path`` with BytesGenerator."""
    with path.open("wb") as fh:
        BytesGenerator(fh, policy=msg.policy).flatten(msg)


def write_dry_run(notices: Sequence[NoticeInput], out_dir: Path, now: datetime) -> int:
    """Write one .eml per notice to out_dir; returns count. Per-item errors are
    caught and skipped."""
    out_dir.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    written = 0
    for n in notices:
        try:
            path = out_dir / f"{_unique_stem(n.dup_message_id, used)}.eml"
            _write_eml(render_notice(n, now), path)
            written += 1
        except Exception as exc:  # noqa: BLE001 - never crash on one item
            log.warning("dry-run: skipped %r: %s: %s", getattr(n, "dup_message_id", n), type(exc).__name__, exc)
    return written


def append_send_log(path: Path, row: dict[str, str]) -> None:
    """Append one row (SEND_LOG_COLUMNS), writing the header if the file is new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SEND_LOG_COLUMNS, extrasaction="ignore", restval="")
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def _require_override(override_to: str) -> str:
    """Return the stripped override address or refuse (plan rule 0.4)."""
    target = (override_to or "").strip()
    if not target:
        raise ValueError("NOTIFY_OVERRIDE_TO is not set; refusing to send to real Enron addresses")
    return target


def live_body(n: NoticeInput) -> str:
    """Redirect banner line, a blank line, then the template body."""
    return REDIRECT_LINE.format(recipient=n.dup_from) + "\n\n" + render_body(n)


def _safe_send(sender: Sender, to: str, n: NoticeInput) -> SendResult:
    """Call sender.send; any exception becomes an ``error`` SendResult."""
    try:
        return sender.send(to, notice_subject(n), live_body(n), {"References": n.dup_message_id})
    except Exception as exc:  # noqa: BLE001 - never raise per item
        return SendResult(status="error", error=f"{type(exc).__name__}: {exc}")


def _finish(conn: sqlite3.Connection, n: NoticeInput, result: SendResult,
            to: str, log_path: Path, now: datetime, mark: bool = True) -> SendResult:
    """Mark the DB on ``sent`` (if ``mark``) and append the log row; never raises."""
    if mark and result.status == "sent":
        try:
            db.mark_notified(conn, n.dup_message_id, _iso_utc(now))
        except Exception as exc:  # noqa: BLE001
            result = SendResult(status="sent", error=f"mark_notified failed: {type(exc).__name__}: {exc}",
                                detail=result.detail)
    row = {
        "timestamp": _iso_utc(now), "recipient": to, "subject": notice_subject(n),
        "status": result.status, "error": result.error or "",
        "intended_recipient": n.dup_from, "message_id": n.dup_message_id,
    }
    try:
        append_send_log(log_path, row)
    except Exception as exc:  # noqa: BLE001
        log.error("send log write failed for %s: %s", n.dup_message_id, exc)
    return result


def send_live(
    conn: sqlite3.Connection,
    sender: Sender,
    override_to: str,
    max_send: int,
    log_path: Path,
    now: datetime,
) -> list[SendResult]:
    """Send notices for primary targets with notification_sent=0 (latest first,
    capped by max_send) to ``override_to``; body starts with
    ``[Redirected for testing; intended recipient: <enron sender>]``. Marks
    sent rows in the DB and logs every attempt. Never raises per item."""
    to = _require_override(override_to)
    if max_send <= 0:
        return []
    targets = db.fetch_notice_inputs(conn, primary_only=True, unsent_only=True, limit=max_send)
    results: list[SendResult] = []
    for n in targets[:max_send]:
        results.append(_finish(conn, n, _safe_send(sender, to, n), to, log_path, now))
    return results


def record_send(
    conn: sqlite3.Connection,
    message_id: str,
    status: str,
    error: str | None,
    override_to: str,
    log_path: Path,
    now: datetime,
) -> None:
    """``--record-send`` path (Claude-Code-driven fallback): same DB/log effect as send_live.

    Raises ValueError if override_to is empty or message_id is not a flagged
    duplicate. An already-notified item is logged but not re-marked.
    """
    to = _require_override(override_to)
    unsent = {n.dup_message_id: n for n in db.fetch_notice_inputs(conn, unsent_only=True)}
    result = SendResult(status=status, error=error)  # type: ignore[arg-type]
    if message_id in unsent:
        _finish(conn, unsent[message_id], result, to, log_path, now)
        return
    known = {n.dup_message_id: n for n in db.fetch_notice_inputs(conn)}
    if message_id not in known:
        raise ValueError(f"unknown duplicate message_id: {message_id}")
    note = "already notified; DB not changed" + (f" ({error})" if error else "")
    _finish(conn, known[message_id], SendResult(status=status, error=note), to, log_path, now,  # type: ignore[arg-type]
            mark=False)
