"""Extraction statistics, field completeness and the error log."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .models import DedupResult, InsertStats, ParsedEmail, ParseFailure


class StatsCollector:
    """Accumulates counts while results stream in from the worker pool."""

    def __init__(self) -> None:
        raise NotImplementedError

    def add_parsed(self, email: ParsedEmail) -> None:
        """Count a parsed email: per-mailbox and per-field completeness."""
        raise NotImplementedError

    def add_failure(self, failure: ParseFailure) -> None:
        """Count a failure by reason slug (text before ':')."""
        raise NotImplementedError

    def add_insert_stats(self, stats: InsertStats) -> None:
        raise NotImplementedError

    def set_total_files(self, n: int) -> None:
        raise NotImplementedError

    def set_runtime(self, key: str, seconds: float) -> None:
        """Record a timing, e.g. ``extract_seconds``, ``workers_1_seconds``."""
        raise NotImplementedError

    def set_dedup(self, result: DedupResult) -> None:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        """total_files_found, parsed_ok, failed, failure_reasons,
        exact_duplicates_skipped, field_completeness (% per field), per_mailbox,
        empty_body_with_forward, date_tz_assumed, runtimes, dedup."""
        raise NotImplementedError

    def write_json(self, path: Path) -> None:
        raise NotImplementedError

    def print_summary(self) -> None:
        raise NotImplementedError


def write_error_log(path: Path, failures: Sequence[ParseFailure]) -> None:
    """One line per failure: ``<ISO ts> | <source_file> | <stage> | <reason>``."""
    raise NotImplementedError
