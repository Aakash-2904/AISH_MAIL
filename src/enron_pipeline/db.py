"""SQLite schema init, batched inserts and queries. Main process is the only writer.

Connections are opened in autocommit mode (``isolation_level=None``) and every
writing function here manages its own explicit ``BEGIN``/``COMMIT`` so that the
"one transaction per call" rule is visible in the code rather than implied by
the sqlite3 module's implicit transaction handling.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from .models import DuplicateFlag, InsertStats, NoticeInput, ParsedEmail, ParseFailure

_EMAIL_COLUMNS: tuple[str, ...] = (
    "message_id",
    "date_utc",
    "from_address",
    "subject",
    "normalized_subject",
    "body",
    "source_file",
    "mailbox",
    "x_from",
    "x_to",
    "x_cc",
    "x_bcc",
    "x_folder",
    "x_origin",
    "content_type",
    "has_attachment",
    "forwarded_content",
    "quoted_content",
    "headings",
)

_INSERT_EMAIL_SQL = (
    f"INSERT INTO emails ({', '.join(_EMAIL_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _EMAIL_COLUMNS)}) "
    "ON CONFLICT(message_id) DO NOTHING"
)

_UPSERT_ADDRESS_SQL = (
    "INSERT INTO addresses (email, is_smtp) VALUES (?, ?) "
    "ON CONFLICT(email) DO UPDATE SET email = excluded.email RETURNING id"
)

_INSERT_RECIPIENT_SQL = (
    "INSERT OR IGNORE INTO email_recipients "
    "(email_id, address_id, recipient_type, position) VALUES (?, ?, ?, ?)"
)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the DB with foreign_keys=ON, WAL journal, synchronous=NORMAL, Row factory."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(conn: sqlite3.Connection, schema_path: Path) -> None:
    """Execute schema.sql (idempotent)."""
    conn.executescript(Path(schema_path).read_text(encoding="utf-8"))


def reset_db(db_path: Path) -> None:
    """Delete the DB file (and -wal/-shm) for ``--fresh``."""
    base = Path(db_path)
    for path in (base, Path(f"{base}-wal"), Path(f"{base}-shm")):
        path.unlink(missing_ok=True)


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """BEGIN ... COMMIT, rolling back on any exception."""
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _is_smtp(address: str) -> bool:
    """True for a normal SMTP address, False for Exchange-style ``/o=...`` values."""
    return "@" in address and not address.lower().startswith("/o=")


def _address_id(conn: sqlite3.Connection, address: str) -> int:
    """Upsert one address and return its id."""
    row = conn.execute(_UPSERT_ADDRESS_SQL, (address, int(_is_smtp(address)))).fetchall()[0]
    return int(row[0])


def _email_values(email: ParsedEmail) -> tuple[object, ...]:
    """Column values for ``_INSERT_EMAIL_SQL`` in ``_EMAIL_COLUMNS`` order."""
    values = [getattr(email, col) for col in _EMAIL_COLUMNS]
    values[_EMAIL_COLUMNS.index("has_attachment")] = int(email.has_attachment)
    return tuple(values)


def _insert_recipients(conn: sqlite3.Connection, email_id: int, email: ParsedEmail) -> None:
    """Insert to/cc/bcc rows with 0-based positions; repeats within a type are ignored."""
    by_type = (("to", email.to_addresses), ("cc", email.cc_addresses), ("bcc", email.bcc_addresses))
    for recipient_type, addresses in by_type:
        for position, address in enumerate(addresses):
            address_id = _address_id(conn, address)
            conn.execute(_INSERT_RECIPIENT_SQL, (email_id, address_id, recipient_type, position))


def _insert_one(conn: sqlite3.Connection, email: ParsedEmail) -> bool:
    """Insert one email + recipients. Returns False on an exact Message-ID collision."""
    cur = conn.execute(_INSERT_EMAIL_SQL, _email_values(email))
    if cur.rowcount == 0:
        return False
    _insert_recipients(conn, int(cur.lastrowid), email)
    return True


def insert_batch(conn: sqlite3.Connection, emails: Sequence[ParsedEmail]) -> InsertStats:
    """Insert emails + recipients in ONE transaction using
    ``INSERT ... ON CONFLICT(message_id) DO NOTHING``; exact Message-ID
    collisions are counted, not failures. Other per-row errors become
    ParseFailure(stage="insert") and the rest of the batch still commits."""
    inserted = 0
    dup_ids: list[str] = []
    failures: list[ParseFailure] = []
    with _transaction(conn):
        for email in emails:
            conn.execute("SAVEPOINT row_insert")
            try:
                if _insert_one(conn, email):
                    inserted += 1
                else:
                    dup_ids.append(email.message_id)
            except Exception as exc:  # noqa: BLE001 - one bad row must not sink the batch
                conn.execute("ROLLBACK TO row_insert")
                reason = f"db_error: {type(exc).__name__}: {exc}"
                failures.append(ParseFailure(email.source_file, "insert", reason))
            conn.execute("RELEASE row_insert")
    return InsertStats(
        inserted=inserted,
        exact_duplicates_skipped=len(dup_ids),
        exact_duplicate_ids=tuple(dup_ids),
        failures=tuple(failures),
    )


def log_failures(conn: sqlite3.Connection, failures: Sequence[ParseFailure]) -> None:
    """Insert rows into parse_failures."""
    if not failures:
        return
    rows = [(f.source_file, f.stage, f.reason) for f in failures]
    with _transaction(conn):
        conn.executemany(
            "INSERT INTO parse_failures (source_file, stage, reason) VALUES (?, ?, ?)", rows
        )


def _clear_flags_sql(conn: sqlite3.Connection) -> None:
    """Statements behind clear_duplicate_flags (no transaction handling)."""
    conn.execute("DELETE FROM duplicate_groups")
    conn.execute(
        "UPDATE emails SET is_duplicate = 0, duplicate_of = NULL, "
        "similarity_score = NULL, is_primary_target = 0 "
        "WHERE is_duplicate = 1 OR duplicate_of IS NOT NULL "
        "OR similarity_score IS NOT NULL OR is_primary_target = 1"
    )


def clear_duplicate_flags(conn: sqlite3.Connection) -> None:
    """Reset is_duplicate/duplicate_of/similarity_score/is_primary_target and
    duplicate_groups (keeps notification_sent/notification_date)."""
    with _transaction(conn):
        _clear_flags_sql(conn)


def apply_duplicate_flags(conn: sqlite3.Connection, flags: Sequence[DuplicateFlag]) -> None:
    """Write all flags + duplicate_groups in one transaction.

    ``duplicate_groups.size`` = number of flags pointing at an original + 1 (the
    original itself). Existing group rows for the same original are overwritten.
    """
    rows = [
        (f.original_message_id, float(f.similarity_score), int(f.is_primary_target), f.duplicate_message_id)
        for f in flags
    ]
    sizes = Counter(f.original_message_id for f in flags)
    with _transaction(conn):
        conn.executemany(
            "UPDATE emails SET is_duplicate = 1, duplicate_of = ?, similarity_score = ?, "
            "is_primary_target = ? WHERE message_id = ?",
            rows,
        )
        conn.executemany(
            "INSERT INTO duplicate_groups (original_message_id, size) VALUES (?, ?) "
            "ON CONFLICT(original_message_id) DO UPDATE SET size = excluded.size",
            [(orig, n + 1) for orig, n in sizes.items()],
        )


def fetch_notice_inputs(
    conn: sqlite3.Connection,
    *,
    primary_only: bool = False,
    unsent_only: bool = False,
    limit: int | None = None,
) -> list[NoticeInput]:
    """Flagged duplicates joined to their originals. Order: duplicate date
    descending (latest first), tie-break by id."""
    sql = (
        "SELECT d.message_id, d.date_utc, d.subject, d.from_address, "
        "o.message_id, o.date_utc, o.subject, d.similarity_score, d.is_primary_target "
        "FROM emails d JOIN emails o ON o.message_id = d.duplicate_of "
        "WHERE d.is_duplicate = 1"
    )
    params: list[object] = []
    if primary_only:
        sql += " AND d.is_primary_target = 1"
    if unsent_only:
        sql += " AND d.notification_sent = 0"
    sql += " ORDER BY d.date_utc DESC, d.id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [
        NoticeInput(
            dup_message_id=r[0],
            dup_date_utc=r[1],
            dup_subject=r[2],
            dup_from=r[3],
            orig_message_id=r[4],
            orig_date_utc=r[5],
            orig_subject=r[6],
            similarity_score=float(r[7]) if r[7] is not None else 0.0,
            is_primary_target=bool(r[8]),
        )
        for r in conn.execute(sql, params)
    ]


def mark_notified(conn: sqlite3.Connection, message_id: str, when_iso_utc: str) -> None:
    """Set notification_sent=1, notification_date=when for one email."""
    with _transaction(conn):
        conn.execute(
            "UPDATE emails SET notification_sent = 1, notification_date = ? WHERE message_id = ?",
            (when_iso_utc, message_id),
        )
