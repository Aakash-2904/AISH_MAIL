"""Count email files per mailbox under data/maildir and print a table.

Used to justify the mailbox selection: the assignment needs >= 10,000 emails.

    python scripts/mailbox_sizes.py [--maildir data/maildir] [--mailboxes a,b | --all]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from enron_pipeline import config  # noqa: E402

MIN_TOTAL = 10_000


def count_files(folder: Path) -> int:
    """Number of regular files under ``folder`` (recursive); 0 if missing."""
    return sum(len(files) for _, _, files in os.walk(folder))


def mailbox_counts(maildir: Path, mailboxes: Sequence[str]) -> list[tuple[str, int]]:
    """(mailbox, file count) for each mailbox, in the given order."""
    return [(name, count_files(Path(maildir) / name)) for name in mailboxes]


def format_table(rows: Sequence[tuple[str, int]], min_total: int = MIN_TOTAL) -> str:
    """Plain-text table with a TOTAL row and a warning if under ``min_total``."""
    width = max([len("mailbox"), len("TOTAL"), *(len(n) for n, _ in rows)])
    total = sum(n for _, n in rows)
    lines = [f"{'mailbox':<{width}}  {'emails':>8}", f"{'-' * width}  {'-' * 8}"]
    lines += [f"{name:<{width}}  {n:>8}" + ("  (missing)" if n == 0 else "") for name, n in rows]
    lines += [f"{'-' * width}  {'-' * 8}", f"{'TOTAL':<{width}}  {total:>8}"]
    if total < min_total:
        lines.append(f"WARNING: total {total} < {min_total}; add mailboxes until it is >= {min_total}")
    else:
        lines.append(f"OK: total {total} >= {min_total}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Exit code 1 when the total is below MIN_TOTAL."""
    p = argparse.ArgumentParser(description="Count emails per mailbox.")
    p.add_argument("--maildir", type=Path, default=config.MAILDIR)
    p.add_argument("--mailboxes", default=",".join(config.DEFAULT_MAILBOXES))
    p.add_argument("--all", action="store_true", help="count every mailbox folder present")
    args = p.parse_args(argv)
    if args.all:
        names = sorted(d.name for d in Path(args.maildir).iterdir() if d.is_dir())
    else:
        names = [m.strip() for m in args.mailboxes.split(",") if m.strip()]
    rows = mailbox_counts(args.maildir, names)
    print(format_table(rows))
    return 0 if sum(n for _, n in rows) >= MIN_TOTAL else 1


if __name__ == "__main__":
    raise SystemExit(main())
