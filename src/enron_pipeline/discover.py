"""Walk the maildir and yield email file paths."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path


def iter_email_files(maildir: Path, mailboxes: Sequence[str] | None = None) -> Iterator[Path]:
    """Yield every regular file under ``maildir/<mailbox>/`` (recursively), in a
    deterministic (sorted) order. ``mailboxes=None`` means all mailboxes.
    Missing mailbox folders are skipped (caller reports them)."""
    raise NotImplementedError


def mailbox_of(path: Path, maildir: Path) -> str:
    """Return the mailbox name (first path component under ``maildir``)."""
    raise NotImplementedError


def relative_source(path: Path, dataset_root: Path) -> str:
    """POSIX path relative to the dataset root, e.g. ``maildir/lay-k/inbox/45``.

    Enron filenames end in '.' (``45.``), which Windows cannot store, so the
    extractor saved them as ``45_``. One trailing '_' or '.' is removed from the
    final component so ``source_file`` matches the canonical Enron id
    (``.../inbox/45``) on every OS."""
    raise NotImplementedError
