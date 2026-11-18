"""Tests for fuzzy duplicate detection (plan 6.4). Uses a temp SQLite DB built
from schema.sql with raw-SQL inserts (no dependency on db.py)."""

from __future__ import annotations

import csv
import itertools
import sqlite3
from pathlib import Path

import pytest
from conftest import SCHEMA
from rapidfuzz import fuzz

from enron_pipeline import dedup
from enron_pipeline.dedup import REPORT_COLUMNS, cluster_block, find_duplicates, load_blocks, write_report
from enron_pipeline.models import BlockMember
from enron_pipeline.normalize import normalize_body, normalize_subject

BASE = (
    "the quarterly gas trading report shows strong volume growth across all desks please review "
    "the quarterly gas trading report"
)[:100]


def mutate(text: str, k: int) -> str:
    """Replace k evenly spaced chars with '#' (absent from text): ratio = (len-k)/len."""
    chars = list(text)
    for i in range(k):
        chars[i * (len(text) // k)] = "#"
    return "".join(chars)


_counter = itertools.count(1)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Fresh DB from schema.sql."""
    c = sqlite3.connect(tmp_path / "t.db")
    c.executescript(SCHEMA.read_text(encoding="utf-8"))
    yield c
    c.close()


def add(conn: sqlite3.Connection, sender: str, subject: str, body: str, date: str, mid: str | None = None) -> str:
    """Insert one synthetic email with raw SQL; return its message_id."""
    n = next(_counter)
    mid = mid or f"<{n}.test@enron>"
    conn.execute(
        "INSERT INTO emails (message_id, date_utc, from_address, subject, normalized_subject, body, source_file)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (mid, date, sender, subject, normalize_subject(subject), body, f"maildir/t/{n}"),
    )
    return mid


def d(day: int) -> str:
    return f"2001-05-{day:02d}T10:00:00+00:00"


def flagged(conn: sqlite3.Connection, workers: int = 1) -> dict[str, tuple[str, float, bool]]:
    res = find_duplicates(conn, 90.0, workers)
    return {f.duplicate_message_id: (f.original_message_id, f.similarity_score, f.is_primary_target) for f in res.flags}


def test_crafted_ratios() -> None:
    assert fuzz.ratio(BASE, mutate(BASE, 8)) == pytest.approx(92.0)
    assert fuzz.ratio(BASE, mutate(BASE, 15)) == pytest.approx(85.0)


def test_re_prefix_identical_body_is_duplicate(conn: sqlite3.Connection) -> None:
    orig = add(conn, "a@enron.com", "Gas report", BASE, d(1))
    dup = add(conn, "a@enron.com", "RE: Gas report", "  " + BASE.upper() + "\n", d(2))
    out = flagged(conn)
    assert out == {dup: (orig, 100.0, True)}


def test_92_percent_is_duplicate(conn: sqlite3.Connection) -> None:
    orig = add(conn, "a@enron.com", "Gas", BASE, d(1))
    dup = add(conn, "a@enron.com", "Gas", mutate(BASE, 8), d(2))
    assert flagged(conn) == {dup: (orig, 92.0, True)}


def test_85_percent_is_not_duplicate(conn: sqlite3.Connection) -> None:
    add(conn, "a@enron.com", "Gas", BASE, d(1))
    add(conn, "a@enron.com", "Gas", mutate(BASE, 15), d(2))
    assert flagged(conn) == {}


def test_same_body_different_sender_not_duplicate(conn: sqlite3.Connection) -> None:
    add(conn, "a@enron.com", "Gas", BASE, d(1))
    add(conn, "b@enron.com", "Gas", BASE, d(2))
    assert flagged(conn) == {}


def test_group_of_three_latest_is_primary(conn: sqlite3.Connection) -> None:
    # Inserted out of date order to prove sorting by date_utc.
    mid = add(conn, "a@enron.com", "Gas", mutate(BASE, 2), d(5))
    late = add(conn, "a@enron.com", "Fw: Gas", mutate(BASE, 4), d(9))
    orig = add(conn, "a@enron.com", "Gas", BASE, d(1))
    res = find_duplicates(conn)
    assert res.groups_found == 1 and res.emails_flagged == 2 and res.max_group_size == 3
    assert res.avg_group_size == 3.0
    out = {f.duplicate_message_id: f for f in res.flags}
    assert set(out) == {mid, late}
    assert all(f.original_message_id == orig for f in res.flags)
    assert out[late].is_primary_target and not out[mid].is_primary_target
    assert out[mid].similarity_score == 98.0 and out[late].similarity_score == 96.0


def test_empty_bodies_excluded_and_counted(conn: sqlite3.Connection) -> None:
    add(conn, "a@enron.com", "Empty", "", d(1))
    add(conn, "a@enron.com", "Empty", "   \n\t ", d(2))
    add(conn, "a@enron.com", "Mixed", "", d(3))
    add(conn, "a@enron.com", "Mixed", BASE, d(4))  # alone after exclusion -> no block
    blocks, excluded = load_blocks(conn)
    assert blocks == [] and excluded == 3
    res = find_duplicates(conn)
    assert res.flags == [] and res.excluded_empty_body == 3 and res.blocks_compared == 0


def test_load_blocks_ordering(conn: sqlite3.Connection) -> None:
    add(conn, "a@enron.com", "Small", BASE, d(2))
    add(conn, "a@enron.com", "Small", BASE, d(1))
    for day in (3, 1, 2):
        add(conn, "b@enron.com", "Big", BASE, d(day))
    blocks, _ = load_blocks(conn)
    assert [len(b) for b in blocks] == [3, 2]
    for b in blocks:
        assert [m.date_utc for m in b] == sorted(m.date_utc for m in b)
        assert all(m.body_norm == normalize_body(BASE) for m in b)


def test_cluster_block_anchor_not_chaining() -> None:
    # B is 92% vs A; C is 92% vs B but ~84% vs A -> C starts its own cluster.
    a = BASE
    b = mutate(BASE, 8)
    c = "".join("%" if i % 12 == 6 else ch for i, ch in enumerate(b))
    assert fuzz.ratio(b, c) >= 90 > fuzz.ratio(a, c)
    block = [BlockMember(i, f"<{i}>", d(i), s) for i, s in enumerate((a, b, c), start=1)]
    flags = cluster_block(block)
    assert [(f.duplicate_message_id, f.original_message_id) for f in flags] == [("<2>", "<1>")]


def _populate_many(conn: sqlite3.Connection) -> None:
    for s in range(12):
        for j in range(4):
            add(conn, f"user{s}@enron.com", f"Topic {s % 3}", mutate(BASE, 1 + (j * s) % 14), d(1 + j))


def test_workers_identical(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _populate_many(conn)
    monkeypatch.setattr(dedup, "_BATCH_MEMBERS", 5)  # force several pool tasks
    assert len(dedup._batch_blocks(load_blocks(conn)[0])) > 2
    one = find_duplicates(conn, 90.0, workers=1)
    two = find_duplicates(conn, 90.0, workers=2)
    assert one.flags and one.flags == two.flags
    assert (one.groups_found, one.emails_flagged, one.max_group_size, one.avg_group_size) == (
        two.groups_found, two.emails_flagged, two.max_group_size, two.avg_group_size)


def test_report_columns_and_order(conn: sqlite3.Connection, tmp_path: Path) -> None:
    o1 = add(conn, "a@enron.com", "Gas", BASE, d(3))
    d1 = add(conn, "a@enron.com", "Re: Gas", mutate(BASE, 8), d(4))
    o2 = add(conn, "b@enron.com", "Power", BASE, d(1))
    d2 = add(conn, "b@enron.com", "Power", BASE, d(2))
    res = find_duplicates(conn)
    out = tmp_path / "out" / "duplicates_report.csv"
    assert write_report(conn, res.flags, out) == 2
    with out.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert tuple(rows[0]) == REPORT_COLUMNS
    assert REPORT_COLUMNS[-1] == "is_primary_target"
    assert rows[1] == [d2, o2, "Power", "b@enron.com", d(2), d(1), "100.0", "1"]
    assert rows[2] == [d1, o1, "Re: Gas", "a@enron.com", d(4), d(3), "92.0", "1"]
