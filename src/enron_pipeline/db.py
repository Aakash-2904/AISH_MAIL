"""SQLite schema init, batched inserts and queries. Main process is the only writer."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from .models import DuplicateFlag, InsertStats, NoticeInput, ParsedEmail, ParseFailure


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the DB with foreign_keys=ON, WAL journal, synchronous=NORMAL, Row factory."""
    raise NotImplementedError


def init_db(conn: sqlite3.Connection, schema_path: Path) -> None:
    """Execute schema.sql (idempotent)."""
    raise NotImplementedError


def reset_db(db_path: Path) -> None:
    """Delete the DB file (and -wal/-shm) for ``--fresh``."""
    raise NotImplementedError


def insert_batch(conn: sqlite3.Connection, emails: Sequence[ParsedEmail]) -> InsertStats:
    """Insert emails + recipients in ONE transaction using
    ``INSERT ... ON CONFLICT(message_id) DO NOTHING``; exact Message-ID
    collisions are counted, not failures. Other per-row errors become
    ParseFailure(stage="insert") and the rest of the batch still commits."""
    raise NotImplementedError


def log_failures(conn: sqlite3.Connection, failures: Sequence[ParseFailure]) -> None:
    """Insert rows into parse_failures."""
    raise NotImplementedError


def clear_duplicate_flags(conn: sqlite3.Connection) -> None:
    """Reset is_duplicate/duplicate_of/similarity_score/is_primary_target and
    duplicate_groups (keeps notification_sent/notification_date)."""
    raise NotImplementedError


def apply_duplicate_flags(conn: sqlite3.Connection, flags: Sequence[DuplicateFlag]) -> None:
    """Write all flags + duplicate_groups in one transaction."""
    raise NotImplementedError


def fetch_notice_inputs(
    conn: sqlite3.Connection,
    *,
    primary_only: bool = False,
    unsent_only: bool = False,
    limit: int | None = None,
) -> list[NoticeInput]:
    """Flagged duplicates joined to their originals. Order: duplicate date
    descending (latest first), tie-break by id."""
    raise NotImplementedError


def mark_notified(conn: sqlite3.Connection, message_id: str, when_iso_utc: str) -> None:
    """Set notification_sent=1, notification_date=when for one email."""
    raise NotImplementedError
