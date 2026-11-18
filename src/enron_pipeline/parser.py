"""Raw email file -> ParsedEmail | ParseFailure. Safe to run in worker processes."""

from __future__ import annotations

from pathlib import Path

from .models import ParsedEmail, ParseFailure


def parse_file(path: Path, root: Path) -> ParsedEmail | ParseFailure:
    """Parse one raw email file. ``root`` is the dataset root (the folder that
    contains ``maildir/``). Never raises: any exception becomes a ParseFailure
    (stage ``read`` for I/O errors, ``parse``/``validate`` otherwise).

    Must be a top-level picklable function (used with ProcessPoolExecutor.map).
    """
    raise NotImplementedError


def parse_bytes(raw: bytes, source_file: str, mailbox: str) -> ParsedEmail | ParseFailure:
    """Parse already-read bytes (used by parse_file and by tests)."""
    raise NotImplementedError
