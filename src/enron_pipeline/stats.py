"""Extraction statistics, field completeness and the error log."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import DedupResult, InsertStats, ParsedEmail, ParseFailure

MANDATORY_FIELDS: tuple[str, ...] = (
    "message_id",
    "date_utc",
    "from_address",
    "to_addresses",
    "subject",
    "body",
)
OPTIONAL_FIELDS: tuple[str, ...] = (
    "cc_addresses",
    "bcc_addresses",
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
COMPLETENESS_FIELDS: tuple[str, ...] = MANDATORY_FIELDS + OPTIONAL_FIELDS


def _is_present(value: object) -> bool:
    """Presence rule: None/""/empty sequence/False are absent; anything else present.

    ``has_attachment`` (bool) therefore reports the share of emails with an attachment.
    """
    if value is None:
        return False
    if isinstance(value, (str, tuple, list)):
        return len(value) > 0
    if isinstance(value, bool):
        return value
    return True


def failure_slug(reason: str) -> str:
    """Reason slug: text before the first ':' (stripped)."""
    return reason.split(":", 1)[0].strip() or "unknown"


class StatsCollector:
    """Accumulates counts while results stream in from the worker pool."""

    def __init__(self) -> None:
        self.total_files: int = 0
        self.parsed_ok: int = 0
        self.failed: int = 0
        self.failure_reasons: Counter[str] = Counter()
        self.failures_by_stage: Counter[str] = Counter()
        self.inserted: int = 0
        self.exact_duplicates_skipped: int = 0
        self.field_present: Counter[str] = Counter()
        self.per_mailbox: Counter[str] = Counter()
        self.empty_body_with_forward: int = 0
        self.date_tz_assumed: int = 0
        self.runtimes: dict[str, float] = {}
        self.dedup: dict[str, Any] | None = None

    def add_parsed(self, email: ParsedEmail) -> None:
        """Count a parsed email: per-mailbox and per-field completeness."""
        self.parsed_ok += 1
        self.per_mailbox[email.mailbox] += 1
        for name in COMPLETENESS_FIELDS:
            if _is_present(getattr(email, name)):
                self.field_present[name] += 1
        if email.body == "" and _is_present(email.forwarded_content):
            self.empty_body_with_forward += 1
        if email.date_tz_assumed:
            self.date_tz_assumed += 1

    def add_failure(self, failure: ParseFailure) -> None:
        """Count a failure by reason slug (text before ':')."""
        self.failed += 1
        self.failure_reasons[failure_slug(failure.reason)] += 1
        self.failures_by_stage[failure.stage] += 1

    def add_insert_stats(self, stats: InsertStats) -> None:
        """Add one insert_batch result. Its ``failures`` are counted here via
        add_failure, so callers must NOT also pass them to add_failure."""
        self.inserted += stats.inserted
        self.exact_duplicates_skipped += stats.exact_duplicates_skipped
        for failure in stats.failures:
            self.add_failure(failure)

    def set_total_files(self, n: int) -> None:
        """Record the number of files discovered."""
        self.total_files = n

    def set_runtime(self, key: str, seconds: float) -> None:
        """Record a timing, e.g. ``extract_seconds``, ``workers_1_seconds``."""
        self.runtimes[key] = round(float(seconds), 3)

    def set_dedup(self, result: DedupResult) -> None:
        """Store the dedup summary (flags list itself is not kept)."""
        summary = asdict(result)
        summary.pop("flags", None)
        self.dedup = summary

    def field_completeness(self) -> dict[str, float]:
        """Percent of parsed emails (0-100, 2 dp) where each field is present."""
        if self.parsed_ok == 0:
            return {name: 0.0 for name in COMPLETENESS_FIELDS}
        return {
            name: round(100.0 * self.field_present[name] / self.parsed_ok, 2)
            for name in COMPLETENESS_FIELDS
        }

    def to_dict(self) -> dict[str, Any]:
        """total_files_found, parsed_ok, failed, failure_reasons,
        exact_duplicates_skipped, field_completeness (% per field), per_mailbox,
        empty_body_with_forward, date_tz_assumed, runtimes, dedup."""
        return {
            "total_files_found": self.total_files,
            "parsed_ok": self.parsed_ok,
            "failed": self.failed,
            "failure_reasons": dict(self.failure_reasons.most_common()),
            "failures_by_stage": dict(self.failures_by_stage.most_common()),
            "inserted": self.inserted,
            "exact_duplicates_skipped": self.exact_duplicates_skipped,
            "field_completeness": self.field_completeness(),
            "per_mailbox": dict(sorted(self.per_mailbox.items())),
            "empty_body_with_forward": self.empty_body_with_forward,
            "date_tz_assumed": self.date_tz_assumed,
            "runtimes": dict(self.runtimes),
            "dedup": self.dedup,
        }

    def write_json(self, path: Path) -> None:
        """Write ``to_dict()`` as pretty UTF-8 JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    def summary_lines(self) -> list[str]:
        """Console summary as a list of lines (used by print_summary)."""
        d = self.to_dict()
        lines = [
            "=== Extraction summary ===",
            f"{'files found':<28}{d['total_files_found']:>10}",
            f"{'parsed ok':<28}{d['parsed_ok']:>10}",
            f"{'failed':<28}{d['failed']:>10}",
            f"{'inserted':<28}{d['inserted']:>10}",
            f"{'exact duplicates skipped':<28}{d['exact_duplicates_skipped']:>10}",
            f"{'empty body w/ forward':<28}{d['empty_body_with_forward']:>10}",
            f"{'date tz assumed':<28}{d['date_tz_assumed']:>10}",
        ]
        if d["failure_reasons"]:
            lines.append("--- failure reasons ---")
            lines += [f"  {k:<26}{v:>10}" for k, v in d["failure_reasons"].items()]
        lines.append("--- field completeness (%) ---")
        lines += [f"  {k:<26}{v:>10.2f}" for k, v in d["field_completeness"].items()]
        if d["per_mailbox"]:
            lines.append("--- per mailbox ---")
            lines += [f"  {k:<26}{v:>10}" for k, v in d["per_mailbox"].items()]
        if d["runtimes"]:
            lines.append("--- runtimes (s) ---")
            lines += [f"  {k:<26}{v:>10.3f}" for k, v in d["runtimes"].items()]
        if d["dedup"]:
            lines.append("--- dedup ---")
            lines += [f"  {k:<26}{v:>10}" for k, v in d["dedup"].items()]
        return lines

    def print_summary(self) -> None:
        """Print a concise console table of the stats."""
        print("\n".join(self.summary_lines()))


def _one_line(text: str) -> str:
    """Collapse CR/LF so each failure stays on a single log line."""
    return " ".join(text.splitlines()).strip()


def write_error_log(path: Path, failures: Sequence[ParseFailure]) -> None:
    """One line per failure: ``<ISO ts> | <source_file> | <stage> | <reason>``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for f in failures:
            fh.write(f"{ts} | {_one_line(f.source_file)} | {f.stage} | {_one_line(f.reason)}\n")
