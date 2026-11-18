"""Tests for enron_pipeline.db and sample_queries.sql (synthetic data only)."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import ROOT, SCHEMA
from enron_pipeline import db
from enron_pipeline.models import DuplicateFlag, ParsedEmail


def make_email(n: int, **overrides: object) -> ParsedEmail:
    """Synthetic ParsedEmail number ``n``."""
    base = ParsedEmail(
        message_id=f"<{n}.JavaMail.evans@thyme>",
        date_utc=f"2001-10-{n:02d}T12:00:00+00:00",
        from_address="kenneth.lay@enron.com",
        to_addresses=("a@enron.com", "b@enron.com"),
        subject=f"Subject {n}",
        body=f"body {n}",
        source_file=f"maildir/lay-k/inbox/{n}",
        mailbox="lay-k",
        normalized_subject=f"subject {n}",
    )
    return replace(base, **overrides)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "test.db")
    db.init_db(c, SCHEMA)
    yield c
    c.close()


def count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def test_connect_pragmas(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert conn.row_factory is sqlite3.Row


def test_init_db_idempotent(conn: sqlite3.Connection) -> None:
    db.init_db(conn, SCHEMA)  # second run must not fail
    assert count(conn, "SELECT COUNT(*) FROM emails") == 0


def test_reset_db_removes_files(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    c = db.connect(path)
    db.init_db(c, SCHEMA)
    db.insert_batch(c, [make_email(1)])
    c.close()
    Path(f"{path}-wal").touch()
    Path(f"{path}-shm").touch()
    db.reset_db(path)
    assert not path.exists()
    assert not Path(f"{path}-wal").exists() and not Path(f"{path}-shm").exists()
    db.reset_db(path)  # missing files are fine


def test_insert_and_columns(conn: sqlite3.Connection) -> None:
    email = make_email(1, x_folder="Inbox", has_attachment=True, forwarded_content="fwd")
    stats = db.insert_batch(conn, [email])
    assert stats.inserted == 1 and stats.exact_duplicates_skipped == 0 and not stats.failures
    row = conn.execute("SELECT * FROM emails").fetchone()
    assert row["message_id"] == email.message_id
    assert row["has_attachment"] == 1
    assert row["x_folder"] == "Inbox"
    assert row["forwarded_content"] == "fwd"
    assert row["mailbox"] == "lay-k"
    assert row["is_duplicate"] == 0


def test_reinsert_counts_exact_duplicates(conn: sqlite3.Connection) -> None:
    batch = [make_email(1), make_email(2)]
    db.insert_batch(conn, batch)
    stats = db.insert_batch(conn, batch)
    assert stats.inserted == 0
    assert stats.exact_duplicates_skipped == 2
    assert set(stats.exact_duplicate_ids) == {e.message_id for e in batch}
    assert not stats.failures
    assert count(conn, "SELECT COUNT(*) FROM emails") == 2
    assert count(conn, "SELECT COUNT(*) FROM email_recipients") == 4


def test_duplicate_message_id_within_batch(conn: sqlite3.Connection) -> None:
    a = make_email(1)
    b = make_email(2, message_id=a.message_id)  # same Message-ID, other file
    stats = db.insert_batch(conn, [a, b])
    assert stats.inserted == 1
    assert stats.exact_duplicate_ids == (a.message_id,)


def test_recipients_normalised_and_ordered(conn: sqlite3.Connection) -> None:
    e1 = make_email(
        1,
        to_addresses=("z@enron.com", "a@enron.com", "z@enron.com"),  # repeat within type
        cc_addresses=("a@enron.com", "/o=enron/ou=na/cn=recipients/cn=jdoe"),
        bcc_addresses=("a@enron.com",),
    )
    e2 = make_email(2, to_addresses=("a@enron.com",))
    stats = db.insert_batch(conn, [e1, e2])
    assert stats.inserted == 2 and not stats.failures
    # each distinct address stored once
    assert count(conn, "SELECT COUNT(*) FROM addresses") == 3
    assert count(conn, "SELECT is_smtp FROM addresses WHERE email LIKE '/o=%'") == 0
    assert count(conn, "SELECT is_smtp FROM addresses WHERE email = 'a@enron.com'") == 1
    rows = conn.execute(
        "SELECT r.recipient_type, r.position, a.email FROM email_recipients r "
        "JOIN addresses a ON a.id = r.address_id JOIN emails e ON e.id = r.email_id "
        "WHERE e.message_id = ? ORDER BY r.recipient_type DESC, r.position",
        (e1.message_id,),
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("to", 0, "z@enron.com"),
        ("to", 1, "a@enron.com"),
        ("cc", 0, "a@enron.com"),
        ("cc", 1, "/o=enron/ou=na/cn=recipients/cn=jdoe"),
        ("bcc", 0, "a@enron.com"),
    ]


def test_insert_failure_does_not_sink_batch(conn: sqlite3.Connection) -> None:
    good1 = make_email(1)
    bad = make_email(2, source_file=good1.source_file)  # violates source_file UNIQUE
    good2 = make_email(3)
    stats = db.insert_batch(conn, [good1, bad, good2])
    assert stats.inserted == 2
    assert len(stats.failures) == 1
    failure = stats.failures[0]
    assert failure.stage == "insert" and failure.source_file == bad.source_file
    assert failure.reason.startswith("db_error:")
    assert "UNIQUE" in failure.reason
    assert count(conn, "SELECT COUNT(*) FROM emails") == 2
    # the failed row's recipients/addresses were rolled back with it
    assert count(conn, "SELECT COUNT(*) FROM email_recipients") == 4
    db.log_failures(conn, stats.failures)
    row = conn.execute("SELECT source_file, stage, reason FROM parse_failures").fetchone()
    assert (row["source_file"], row["stage"]) == (bad.source_file, "insert")


def _flags() -> list[DuplicateFlag]:
    orig = make_email(1).message_id
    return [
        DuplicateFlag(make_email(2).message_id, orig, 95.5, False),
        DuplicateFlag(make_email(3).message_id, orig, 97.0, True),
    ]


def test_apply_and_clear_flags(conn: sqlite3.Connection) -> None:
    db.insert_batch(conn, [make_email(i) for i in range(1, 5)])
    db.apply_duplicate_flags(conn, _flags())
    rows = conn.execute(
        "SELECT message_id, is_duplicate, duplicate_of, similarity_score, is_primary_target "
        "FROM emails ORDER BY id"
    ).fetchall()
    orig = make_email(1).message_id
    assert [tuple(r)[1:] for r in rows] == [
        (0, None, None, 0),
        (1, orig, 95.5, 0),
        (1, orig, 97.0, 1),
        (0, None, None, 0),
    ]
    group = conn.execute("SELECT original_message_id, size FROM duplicate_groups").fetchall()
    assert [tuple(g) for g in group] == [(orig, 3)]

    db.mark_notified(conn, make_email(3).message_id, "2026-11-18T00:00:00+00:00")
    db.clear_duplicate_flags(conn)
    assert count(conn, "SELECT COUNT(*) FROM emails WHERE is_duplicate = 1") == 0
    assert count(conn, "SELECT COUNT(*) FROM emails WHERE duplicate_of IS NOT NULL") == 0
    assert count(conn, "SELECT COUNT(*) FROM emails WHERE is_primary_target = 1") == 0
    assert count(conn, "SELECT COUNT(*) FROM duplicate_groups") == 0
    # notification columns survive the clear
    row = conn.execute(
        "SELECT notification_sent, notification_date FROM emails WHERE message_id = ?",
        (make_email(3).message_id,),
    ).fetchone()
    assert tuple(row) == (1, "2026-11-18T00:00:00+00:00")
    # re-applying after clear works (no UNIQUE clash on duplicate_groups)
    db.apply_duplicate_flags(conn, _flags())
    db.apply_duplicate_flags(conn, _flags())
    assert count(conn, "SELECT size FROM duplicate_groups") == 3


def test_fetch_notice_inputs_ordering_and_filters(conn: sqlite3.Connection) -> None:
    emails = [make_email(i) for i in range(1, 6)]
    # emails 4 and 5 share a date so the id tie-break is exercised
    emails[4] = replace(emails[4], date_utc=emails[3].date_utc)
    db.insert_batch(conn, emails)
    orig = emails[0].message_id
    flags = [
        DuplicateFlag(emails[1].message_id, orig, 91.0, False),
        DuplicateFlag(emails[2].message_id, orig, 92.0, False),
        DuplicateFlag(emails[4].message_id, orig, 94.0, True),
        DuplicateFlag(emails[3].message_id, orig, 93.0, False),
    ]
    db.apply_duplicate_flags(conn, flags)

    notices = db.fetch_notice_inputs(conn)
    assert [n.dup_message_id for n in notices] == [
        emails[3].message_id,  # date 10-04, lower id
        emails[4].message_id,  # date 10-04, higher id
        emails[2].message_id,
        emails[1].message_id,
    ]
    first = notices[0]
    assert first.orig_message_id == orig
    assert first.orig_date_utc == emails[0].date_utc
    assert first.orig_subject == emails[0].subject
    assert first.dup_from == emails[3].from_address
    assert first.similarity_score == 93.0 and first.is_primary_target is False

    primary = db.fetch_notice_inputs(conn, primary_only=True)
    assert [n.dup_message_id for n in primary] == [emails[4].message_id]
    assert primary[0].is_primary_target is True

    assert len(db.fetch_notice_inputs(conn, limit=2)) == 2

    db.mark_notified(conn, emails[3].message_id, "2026-11-18T00:00:00+00:00")
    unsent = db.fetch_notice_inputs(conn, unsent_only=True)
    assert emails[3].message_id not in [n.dup_message_id for n in unsent]
    assert len(unsent) == 3


# ---------- sample_queries.sql ----------


def split_sql(script: str) -> list[str]:
    """Split a script into complete statements, dropping comment-only chunks."""
    statements: list[str] = []
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            code = "\n".join(
                ln for ln in buf.splitlines() if not ln.strip().startswith("--")
            ).strip()
            if code:
                statements.append(code)
            buf = ""
    return statements


def test_sample_queries_run() -> None:
    c = db.connect(Path(":memory:"))
    db.init_db(c, SCHEMA)
    emails = [
        make_email(1, cc_addresses=("c@enron.com",)),
        make_email(2, x_folder="Inbox", date_utc="2001-12-31T23:00:00+00:00"),
        make_email(3, bcc_addresses=("d@enron.com",), date_utc="2002-01-01T00:00:00+00:00"),
    ]
    db.insert_batch(c, emails)
    db.apply_duplicate_flags(
        c, [DuplicateFlag(emails[1].message_id, emails[0].message_id, 96.0, True)]
    )
    script = (ROOT / "sample_queries.sql").read_text(encoding="utf-8")
    statements = split_sql(script)
    assert len(statements) >= 6
    results = [c.execute(s).fetchall() for s in statements]
    # Q2 count: 10-01 and 12-31 are in range, 2002-01-01 is not
    assert results[1][0][0] == 2
    # Q3 count of emails with CC
    assert results[3][0][0] == 1
    # Q5 duplicate overview has the one flagged row with group size 2
    q5 = results[6]
    assert len(q5) == 1 and q5[0]["group_size"] == 2
    # Q6 completeness: 1 of 3 emails has cc
    assert results[7][0]["pct_cc"] == pytest.approx(33.33)
    c.close()


def test_sample_queries_on_empty_db(tmp_path: Path) -> None:
    c = db.connect(tmp_path / "empty.db")
    db.init_db(c, SCHEMA)
    for s in split_sql((ROOT / "sample_queries.sql").read_text(encoding="utf-8")):
        c.execute(s).fetchall()
    c.close()
