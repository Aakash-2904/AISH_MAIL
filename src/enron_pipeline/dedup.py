"""Fuzzy duplicate detection (blocking + anchor clustering) and CSV report."""

from __future__ import annotations

import csv
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from itertools import groupby
from pathlib import Path

from rapidfuzz import fuzz

from .models import BlockMember, DedupResult, DuplicateFlag
from .normalize import normalize_body

REPORT_COLUMNS = (
    "duplicate_message_id",
    "original_message_id",
    "subject",
    "from_address",
    "duplicate_date",
    "original_date",
    "similarity_score",
)

# Candidate rows: only (from, normalized_subject) groups that have >= 2 emails
# before body filtering; ordered so each block is contiguous and pre-sorted.
_BLOCK_SQL = """
WITH keyed AS (
    SELECT id, message_id, date_utc, body, forwarded_content, from_address,
           COALESCE(normalized_subject, '') AS nsubj
    FROM emails
),
multi AS (
    SELECT from_address, nsubj FROM keyed
    GROUP BY from_address, nsubj HAVING COUNT(*) >= 2
)
SELECT k.from_address, k.nsubj, k.id, k.message_id, k.date_utc, k.body, k.forwarded_content
FROM keyed k JOIN multi m ON k.from_address = m.from_address AND k.nsubj = m.nsubj
ORDER BY k.from_address, k.nsubj, k.date_utc, k.id
"""

# A batch sent to one worker task holds roughly this many members, so tiny
# blocks don't pay one IPC round-trip each while big blocks still go alone.
_BATCH_MEMBERS = 2000
_IN_CHUNK = 500


@dataclass(slots=True)
class BlockLoadStats:
    """Counters from load_blocks."""

    excluded_empty_body: int = 0  # body AND forwarded_content both normalise to ""
    compared_on_forwarded: int = 0  # body empty, so forwarded_content is compared


@dataclass(slots=True)
class ForwardAwareDedupResult(DedupResult):
    """DedupResult plus the compared_on_forwarded counter (AR-3).

    Kept here because models.py is outside this change; asdict() in
    stats.set_dedup picks the extra field up automatically."""

    compared_on_forwarded: int = 0


def comparison_text(body: str | None, forwarded: str | None) -> tuple[str, bool]:
    """Text used for fuzzy comparison and whether it came from the forward.

    The normalised primary body is used when non-empty; otherwise the
    normalised forwarded_content (same normalisation). ("", False) means
    both are empty and the row is excluded."""
    body_norm = normalize_body(body)
    if body_norm:
        return body_norm, False
    fwd_norm = normalize_body(forwarded)
    return fwd_norm, bool(fwd_norm)


def load_blocks(conn: sqlite3.Connection) -> tuple[list[list[BlockMember]], BlockLoadStats]:
    """Group emails by (from_address, normalized_subject); return blocks with
    >= 2 comparable members, sorted by size desc, members sorted by
    (date_utc, id). Each member's body_norm is comparison_text(); rows whose
    body and forwarded_content are both empty are excluded and counted."""
    blocks: list[list[BlockMember]] = []
    stats = BlockLoadStats()
    rows = conn.execute(_BLOCK_SQL)
    for _key, group in groupby(rows, key=lambda r: (r[0], r[1])):
        members: list[BlockMember] = []
        for _f, _s, row_id, message_id, date_utc, body, forwarded in group:
            text, from_forward = comparison_text(body, forwarded)
            if not text:
                stats.excluded_empty_body += 1
                continue
            stats.compared_on_forwarded += from_forward
            members.append(BlockMember(row_id, message_id, date_utc, text))
        if len(members) >= 2:
            blocks.append(members)
    blocks.sort(key=lambda b: (-len(b), b[0].date_utc, b[0].id))
    return blocks, stats


def _length_allows(len_a: int, len_b: int, threshold: float) -> bool:
    """False when the length ratio alone makes fuzz.ratio >= threshold impossible."""
    total = len_a + len_b
    return total > 0 and 2 * min(len_a, len_b) / total * 100 >= threshold


def _match_score(a: str, b: str, threshold: float) -> float:
    """fuzz.ratio(a, b) if it reaches threshold, else 0.0 (with length pruning)."""
    if not _length_allows(len(a), len(b), threshold):
        return 0.0
    return fuzz.ratio(a, b, score_cutoff=threshold)


def cluster_block(block: Sequence[BlockMember], threshold: float = 90.0) -> list[DuplicateFlag]:
    """Anchor clustering of one block (plan 6.2). Pure and picklable."""
    # Each cluster: list of (member, score vs. anchor); index 0 is the anchor.
    clusters: list[list[tuple[BlockMember, float]]] = []
    for member in block:
        for cluster in clusters:
            score = _match_score(cluster[0][0].body_norm, member.body_norm, threshold)
            if score >= threshold:
                cluster.append((member, score))
                break
        else:
            clusters.append([(member, 100.0)])
    flags: list[DuplicateFlag] = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        original = cluster[0][0]
        last = len(cluster) - 1
        for i, (member, score) in enumerate(cluster[1:], start=1):
            flags.append(
                DuplicateFlag(
                    duplicate_message_id=member.message_id,
                    original_message_id=original.message_id,
                    similarity_score=round(score, 2),
                    is_primary_target=i == last,
                )
            )
    return flags


def _cluster_batch(batch: list[list[BlockMember]], threshold: float) -> list[DuplicateFlag]:
    """Worker entry point: cluster several blocks, return all flags."""
    return [f for block in batch for f in cluster_block(block, threshold)]


def _batch_blocks(blocks: Sequence[list[BlockMember]], max_members: int | None = None) -> list[list[list[BlockMember]]]:
    """Greedily pack consecutive blocks (biggest first) into batches of ~max_members."""
    max_members = max_members or _BATCH_MEMBERS
    batches: list[list[list[BlockMember]]] = []
    current: list[list[BlockMember]] = []
    size = 0
    for block in blocks:
        current.append(block)
        size += len(block)
        if size >= max_members:
            batches.append(current)
            current, size = [], 0
    if current:
        batches.append(current)
    return batches


def _run_clustering(blocks: list[list[BlockMember]], threshold: float, workers: int) -> list[DuplicateFlag]:
    """Cluster all blocks serially or across a process pool."""
    if workers <= 1 or len(blocks) < 2:
        return _cluster_batch(blocks, threshold)
    batches = _batch_blocks(blocks)
    flags: list[DuplicateFlag] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(_cluster_batch, batches, [threshold] * len(batches)):
            flags.extend(part)
    return flags


def _group_sizes(flags: Iterable[DuplicateFlag]) -> list[int]:
    """Cluster sizes (flagged members + original) keyed by original."""
    counts = Counter(f.original_message_id for f in flags)
    return [n + 1 for n in counts.values()]


def find_duplicates(conn: sqlite3.Connection, threshold: float = 90.0, workers: int = 1) -> ForwardAwareDedupResult:
    """Run blocking + clustering (ProcessPoolExecutor when workers > 1).
    Deterministic: identical output for any worker count. Does not write."""
    start = time.perf_counter()
    blocks, load_stats = load_blocks(conn)
    flags = _run_clustering(blocks, threshold, workers)
    flags.sort(key=lambda f: (f.original_message_id, f.duplicate_message_id))
    sizes = _group_sizes(flags)
    return ForwardAwareDedupResult(
        flags=flags,
        groups_found=len(sizes),
        emails_flagged=len(flags),
        avg_group_size=round(sum(sizes) / len(sizes), 2) if sizes else 0.0,
        max_group_size=max(sizes, default=0),
        excluded_empty_body=load_stats.excluded_empty_body,
        compared_on_forwarded=load_stats.compared_on_forwarded,
        blocks_compared=len(blocks),
        runtime_seconds=round(time.perf_counter() - start, 3),
    )


def _email_meta(conn: sqlite3.Connection, message_ids: Iterable[str]) -> dict[str, tuple[str, str, str]]:
    """message_id -> (subject, from_address, date_utc), queried in chunks."""
    ids = sorted(set(message_ids))
    meta: dict[str, tuple[str, str, str]] = {}
    for i in range(0, len(ids), _IN_CHUNK):
        chunk = ids[i : i + _IN_CHUNK]
        marks = ",".join("?" * len(chunk))
        sql = f"SELECT message_id, subject, from_address, date_utc FROM emails WHERE message_id IN ({marks})"
        for mid, subject, sender, date in conn.execute(sql, chunk):
            meta[mid] = (subject, sender, date)
    return meta


def write_report(conn: sqlite3.Connection, flags: Sequence[DuplicateFlag], path: Path) -> int:
    """Write duplicates_report.csv with exactly the 7 REPORT_COLUMNS (the
    is_primary_target flag stays in the DB only), sorted by original date then
    duplicate date. Returns rows written."""
    meta = _email_meta(conn, [m for f in flags for m in (f.duplicate_message_id, f.original_message_id)])
    rows = []
    for f in flags:
        subject, sender, dup_date = meta.get(f.duplicate_message_id, ("", "", ""))
        orig_date = meta.get(f.original_message_id, ("", "", ""))[2]
        rows.append(
            (f.duplicate_message_id, f.original_message_id, subject, sender,
             dup_date, orig_date, f"{f.similarity_score:.1f}")
        )
    rows.sort(key=lambda r: (r[5], r[1], r[4], r[0]))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(REPORT_COLUMNS)
        writer.writerows(rows)
    return len(rows)
