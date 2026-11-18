"""Fuzzy duplicate detection (blocking + anchor clustering) and CSV report."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from .models import BlockMember, DedupResult, DuplicateFlag

REPORT_COLUMNS = (
    "duplicate_message_id",
    "original_message_id",
    "subject",
    "from_address",
    "duplicate_date",
    "original_date",
    "similarity_score",
    "is_primary_target",
)


def load_blocks(conn: sqlite3.Connection) -> tuple[list[list[BlockMember]], int]:
    """Group emails by (from_address, normalized_subject); return blocks with
    >= 2 non-empty-body members, sorted by size desc, members sorted by
    (date_utc, id). Second value = count excluded for empty normalised body."""
    raise NotImplementedError


def cluster_block(block: Sequence[BlockMember], threshold: float = 90.0) -> list[DuplicateFlag]:
    """Anchor clustering of one block (plan 6.2). Pure and picklable."""
    raise NotImplementedError


def find_duplicates(conn: sqlite3.Connection, threshold: float = 90.0, workers: int = 1) -> DedupResult:
    """Run blocking + clustering (ProcessPoolExecutor when workers > 1).
    Deterministic: identical output for any worker count. Does not write."""
    raise NotImplementedError


def write_report(conn: sqlite3.Connection, flags: Sequence[DuplicateFlag], path: Path) -> int:
    """Write duplicates_report.csv with REPORT_COLUMNS, sorted by original date
    then duplicate date. Returns rows written."""
    raise NotImplementedError
