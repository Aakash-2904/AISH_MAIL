"""Walk the maildir and yield email file paths."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath


def _walk_sorted(folder: Path) -> Iterator[Path]:
    """Recursive walk with sorted directories and files (deterministic)."""
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_file():
                yield p


def iter_email_files(maildir: Path, mailboxes: Sequence[str] | None = None) -> Iterator[Path]:
    """Yield every regular file under ``maildir/<mailbox>/`` (recursively), in a
    deterministic (sorted) order. ``mailboxes=None`` means all mailboxes.
    Missing mailbox folders are skipped (caller reports them)."""
    maildir = Path(maildir)
    if mailboxes is None:
        if not maildir.is_dir():
            return
        names = sorted(p.name for p in maildir.iterdir() if p.is_dir())
    else:
        names = sorted(set(mailboxes))
    for name in names:
        folder = maildir / name
        if folder.is_dir():
            yield from _walk_sorted(folder)


def _relative_parts(path: Path, base: Path) -> tuple[str, ...]:
    """Parts of ``path`` relative to ``base`` (tries resolved paths too)."""
    try:
        return Path(path).relative_to(base).parts
    except ValueError:
        return Path(path).resolve().relative_to(Path(base).resolve()).parts


def mailbox_of(path: Path, maildir: Path) -> str:
    """Return the mailbox name (first path component under ``maildir``)."""
    parts = _relative_parts(path, maildir)
    if not parts:
        raise ValueError(f"{path} is the maildir itself")
    return parts[0]


def relative_source(path: Path, dataset_root: Path) -> str:
    """POSIX path relative to the dataset root, e.g. ``maildir/lay-k/inbox/45``.

    Enron filenames end in '.' (``45.``), which Windows cannot store, so the
    extractor saved them as ``45_``. One trailing '_' or '.' is removed from the
    final component so ``source_file`` matches the canonical Enron id
    (``.../inbox/45``) on every OS."""
    parts = list(_relative_parts(path, dataset_root))
    if not parts:
        raise ValueError(f"{path} is the dataset root itself")
    last = parts[-1]
    if len(last) > 1 and last[-1] in "_.":
        parts[-1] = last[:-1]
    return str(PurePosixPath(*parts))
