"""Duplicate-notice rendering, dry-run .eml output and live-send orchestration."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from .models import NoticeInput, Sender, SendResult

SEND_LOG_COLUMNS = ("timestamp", "recipient", "subject", "status", "error", "intended_recipient", "message_id")


def format_display_date(iso_utc: str) -> str:
    """ISO UTC string -> RFC 2822 (``Mon, 14 May 2001 23:39:00 +0000``)."""
    raise NotImplementedError


def notice_subject(n: NoticeInput) -> str:
    """``[Duplicate Notice] Re: <original subject without Re:/Fwd: prefixes>``."""
    raise NotImplementedError


def render_body(n: NoticeInput) -> str:
    """Body text exactly matching the assignment template (score: 1 decimal)."""
    raise NotImplementedError


def render_notice(n: NoticeInput, now: datetime, to: str | None = None) -> EmailMessage:
    """Full message: To (default n.dup_from), Subject, Date (RFC 2822 of now),
    References (n.dup_message_id), body from render_body."""
    raise NotImplementedError


def safe_filename(message_id: str) -> str:
    """Message-ID -> ``[A-Za-z0-9._-]`` file stem."""
    raise NotImplementedError


def write_dry_run(notices: Sequence[NoticeInput], out_dir: Path, now: datetime) -> int:
    """Write one .eml per notice to out_dir; returns count. Per-item errors are
    caught and skipped."""
    raise NotImplementedError


def append_send_log(path: Path, row: dict[str, str]) -> None:
    """Append one row (SEND_LOG_COLUMNS), writing the header if the file is new."""
    raise NotImplementedError


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
    raise NotImplementedError


def record_send(
    conn: sqlite3.Connection,
    message_id: str,
    status: str,
    error: str | None,
    override_to: str,
    log_path: Path,
    now: datetime,
) -> None:
    """``--record-send`` path (Claude-Code-driven fallback): same DB/log effect as send_live."""
    raise NotImplementedError
