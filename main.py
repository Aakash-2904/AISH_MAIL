"""Single entry point for the Enron extraction / dedup / notification pipeline.

Default run (dry-run, no email is sent)::

    python main.py [--fresh] [--mailboxes a,b] [--workers N] [--limit N]

On a DB that already has emails, pass ``--fresh`` (rebuild) or ``--skip-extract``
(reuse); a plain re-run is refused (exit 2) so the stats are never skewed.
Send state is restored from output/send_log.csv after every dedup and before
every live send, so ``--fresh`` never causes a notice to be sent twice.

Stages: dataset check -> discover -> parallel parse -> SQLite load -> stats +
error log -> dedup + duplicates_report.csv -> dry-run .eml files -> summary.

Live sending (always redirected to NOTIFY_OVERRIDE_TO, capped by --max-send)::

    python main.py --send-live [--max-send 3] [--via-claude-code]
    python main.py --record-send <message_id> --status sent [--error ...]
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
import sys
import time
import traceback
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from enron_pipeline import config  # noqa: E402

log = logging.getLogger("enron_pipeline.main")

EXIT_USAGE = 2

SEND_STATUSES = ("sent", "pending_approval", "approval_timeout", "error", "dry_run")


class StageError(RuntimeError):
    """A pipeline stage failed; ``stage`` names it for the console message."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"[{stage}] {message}")
        self.stage = stage


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    """argparse definition for every flag in plan Section 9."""
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Enron email extraction, fuzzy dedup and duplicate notices (dry-run by default).",
    )
    p.add_argument("--maildir", type=Path, default=config.MAILDIR,
                   help="folder containing <mailbox>/ subfolders (default: data/maildir)")
    p.add_argument("--mailboxes", default=",".join(config.DEFAULT_MAILBOXES),
                   help="comma-separated mailbox names")
    p.add_argument("--db", type=Path, default=config.DB_PATH, help="SQLite DB path")
    p.add_argument("--fresh", action="store_true", help="drop and recreate the DB first")
    p.add_argument("--limit", type=int, default=None, metavar="N",
                   help="(dev) parse only the first N discovered files")
    p.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS, metavar="N",
                   help=f"parse/dedup worker processes (default {config.DEFAULT_WORKERS})")
    p.add_argument("--skip-extract", action="store_true", help="reuse the existing DB; skip parse/load")
    p.add_argument("--skip-dedup", action="store_true", help="skip dedup, report and .eml output")
    p.add_argument("--send-live", action="store_true",
                   help="send notices via the Descope Gmail MCP (to NOTIFY_OVERRIDE_TO only)")
    p.add_argument("--max-send", type=int, default=config.DEFAULT_MAX_SEND, metavar="N",
                   help=f"cap on live sends (default {config.DEFAULT_MAX_SEND})")
    p.add_argument("--via-claude-code", action="store_true",
                   help="with --send-live: write output/send_queue.jsonl and stop")
    p.add_argument("--record-send", metavar="MESSAGE_ID",
                   help="record the outcome of a Claude-Code-driven send")
    p.add_argument("--status", choices=SEND_STATUSES, help="status for --record-send")
    p.add_argument("--error", default=None, help="error text for --record-send")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging + tracebacks")
    return p


def parse_mailboxes(value: str) -> list[str]:
    """``"a, b,,c"`` -> ``["a", "b", "c"]`` (order kept, duplicates removed)."""
    seen: dict[str, None] = {}
    for part in value.split(","):
        if part.strip():
            seen.setdefault(part.strip(), None)
    return list(seen)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse and cross-validate arguments (exits with code 2 on misuse)."""
    p = build_parser()
    args = p.parse_args(argv)
    args.mailbox_list = parse_mailboxes(args.mailboxes)
    if not args.mailbox_list:
        p.error("--mailboxes must name at least one mailbox")
    if args.workers < 1:
        p.error("--workers must be >= 1")
    if args.limit is not None and args.limit < 0:
        p.error("--limit must be >= 0")
    if args.max_send < 0:
        p.error("--max-send must be >= 0")
    if args.via_claude_code and not args.send_live:
        p.error("--via-claude-code requires --send-live")
    if args.record_send and not args.status:
        p.error("--record-send requires --status")
    if args.status and not args.record_send:
        p.error("--status is only valid with --record-send")
    if args.record_send and args.send_live:
        p.error("--record-send cannot be combined with --send-live")
    return args


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Re-raise any exception inside a stage as StageError(name, ...)."""
    try:
        yield
    except StageError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to a clean message
        raise StageError(name, f"{type(exc).__name__}: {exc}") from exc


def _progress(iterable: Iterable[Any], total: int, desc: str) -> Iterable[Any]:
    """tqdm progress bar (falls back to the bare iterable if tqdm is missing)."""
    try:
        from tqdm import tqdm
    except ImportError:
        return iterable
    return tqdm(iterable, total=total, desc=desc, unit="file", mininterval=0.5)


# --------------------------------------------------------------------------- stages


def ensure_dataset(maildir: Path, mailboxes: Sequence[str]) -> None:
    """Download/extract any selected mailbox that is missing under ``maildir``."""
    import download_dataset  # scripts/download_dataset.py

    missing = download_dataset.missing_mailboxes(maildir, mailboxes)
    if not missing:
        log.info("dataset present for %d mailbox(es)", len(mailboxes))
        return
    print(f"missing mailbox data: {', '.join(missing)}; fetching dataset ...")
    download_dataset.ensure_dataset(missing, data_dir=maildir.parent)


def discover_files(maildir: Path, mailboxes: Sequence[str], limit: int | None) -> list[Path]:
    """Sorted email paths for the selected mailboxes, truncated to ``limit``."""
    from enron_pipeline import discover

    paths = list(discover.iter_email_files(maildir, mailboxes))
    return paths[:limit] if limit is not None else paths


def iter_parsed(paths: Sequence[Path], root: Path, workers: int) -> Iterator[Any]:
    """Yield ``ParsedEmail | ParseFailure`` in input order (pool when workers > 1)."""
    from enron_pipeline.parser import parse_file

    func = partial(parse_file, root=root)
    if workers <= 1:
        yield from map(func, paths)
        return
    from concurrent.futures import ProcessPoolExecutor

    pool = ProcessPoolExecutor(max_workers=workers)
    try:
        yield from pool.map(func, paths, chunksize=config.PARSE_CHUNKSIZE)
    finally:
        # On an error downstream, drop queued chunks instead of parsing them all.
        pool.shutdown(wait=True, cancel_futures=True)


def load_results(conn: sqlite3.Connection, results: Iterable[Any], collector: Any,
                 batch_size: int = config.INSERT_BATCH_SIZE) -> list[Any]:
    """Single-writer load: buffer ParsedEmail, insert per batch, collect failures."""
    from enron_pipeline import db
    from enron_pipeline.models import ParseFailure

    failures: list[ParseFailure] = []
    buffer: list[Any] = []

    def flush() -> None:
        if buffer:
            ins = db.insert_batch(conn, buffer)
            collector.add_insert_stats(ins)  # also counts ins.failures
            failures.extend(ins.failures)
            buffer.clear()

    for item in results:
        if isinstance(item, ParseFailure):
            collector.add_failure(item)
            failures.append(item)
            continue
        collector.add_parsed(item)
        buffer.append(item)
        if len(buffer) >= batch_size:
            flush()
    flush()
    return failures


def run_extract(conn: sqlite3.Connection, args: argparse.Namespace, collector: Any) -> None:
    """Discover, parse (parallel) and load; write error log + stats JSON."""
    from enron_pipeline import db, stats

    with stage("discover"):
        paths = discover_files(args.maildir, args.mailbox_list, args.limit)
        collector.set_total_files(len(paths))
    print(f"discovered {len(paths)} files in {len(args.mailbox_list)} mailbox(es); "
          f"parsing with {args.workers} worker(s)")
    with stage("extract"):
        started = time.perf_counter()
        results = iter_parsed(paths, args.maildir.parent, args.workers)
        failures = load_results(conn, _progress(results, len(paths), "parse+load"), collector)
        elapsed = time.perf_counter() - started
        collector.set_runtime("extract_seconds", elapsed)
        collector.set_runtime(f"extract_workers_{args.workers}_seconds", elapsed)
    with stage("error-log"):
        db.log_failures(conn, failures)
        stats.write_error_log(config.ERROR_LOG, failures)
        write_stats(collector, args.workers)


def write_stats(collector: Any, workers: int) -> None:
    """Write extraction_stats.json, adding the worker count."""
    data = collector.to_dict()
    data["workers"] = workers
    _write_json(config.STATS_PATH, data)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def merge_dedup_into_stats(dedup_summary: dict[str, Any]) -> None:
    """--skip-extract: keep the previous extraction stats, replace only ``dedup``."""
    try:
        data = json.loads(config.STATS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data["dedup"] = dedup_summary
    _write_json(config.STATS_PATH, data)


def run_dedup(conn: sqlite3.Connection, args: argparse.Namespace, collector: Any) -> Any:
    """Clear old flags, find duplicates, write flags + duplicates_report.csv."""
    from enron_pipeline import db, dedup

    with stage("dedup"):
        db.clear_duplicate_flags(conn)
        result = dedup.find_duplicates(conn, threshold=config.DEDUP_THRESHOLD, workers=args.workers)
        db.apply_duplicate_flags(conn, result.flags)
        rows = dedup.write_report(conn, result.flags, config.DUP_REPORT)
        collector.set_dedup(result)
    print(f"dedup: {result.groups_found} groups, {result.emails_flagged} flagged "
          f"({rows} report rows) in {result.runtime_seconds:.1f}s")
    return result


def clear_replies(out_dir: Path) -> int:
    """Delete old ``*.eml`` files in ``out_dir``; returns how many were removed."""
    removed = 0
    if out_dir.is_dir():
        for old in out_dir.glob("*.eml"):
            old.unlink()
            removed += 1
    return removed


def run_dry_run(conn: sqlite3.Connection) -> int:
    """Write one .eml per flagged duplicate to output/replies/."""
    from enron_pipeline import db, notify

    with stage("dry-run"):
        clear_replies(config.REPLIES_DIR)
        notices = db.fetch_notice_inputs(conn)
        written = notify.write_dry_run(notices, config.REPLIES_DIR, datetime.now(timezone.utc))
    print(f"dry-run: wrote {written} .eml file(s) to {config.REPLIES_DIR}")
    return written


def open_db(db_path: Path, fresh: bool) -> sqlite3.Connection:
    """Connect (optionally after deleting the DB) and apply schema.sql."""
    from enron_pipeline import db

    with stage("db"):
        if fresh:
            db.reset_db(db_path)
        conn = db.connect(db_path)
        db.init_db(conn, config.SCHEMA_PATH)
    return conn


def email_count(conn: sqlite3.Connection) -> int:
    """Rows in ``emails`` (0 if the table is missing)."""
    try:
        return int(conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0])
    except sqlite3.Error:
        return 0


# --------------------------------------------------------------------------- send state


def read_sent_log(log_path: Path) -> dict[str, str]:
    """``{message_id: timestamp}`` of the first ``status=sent`` row per id.

    A missing log is normal (nothing sent yet). An unreadable or malformed log,
    or malformed rows, produce a warning and are skipped; this never raises.
    """
    sent: dict[str, str] = {}
    try:
        if not log_path.is_file():
            return sent
        with log_path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fields = set(reader.fieldnames or ())
            if not {"status", "message_id"} <= fields:
                print(f"warning: {log_path} has no status/message_id header; send state not restored",
                      file=sys.stderr)
                return sent
            bad = 0
            for row in reader:
                status = (row.get("status") or "").strip()
                mid = (row.get("message_id") or "").strip()
                if None in row or not status or not mid:
                    bad += 1
                    continue
                if status == "sent":
                    sent.setdefault(mid, (row.get("timestamp") or "").strip())
            if bad:
                print(f"warning: skipped {bad} malformed row(s) in {log_path}", file=sys.stderr)
    except (OSError, csv.Error, UnicodeDecodeError, ValueError) as exc:
        print(f"warning: could not read {log_path} ({type(exc).__name__}: {exc}); "
              "send state not restored", file=sys.stderr)
        return {}
    return sent


def restore_send_state(conn: sqlite3.Connection, log_path: Path,
                       sent: dict[str, str] | None = None) -> int:
    """Re-apply notification_sent/notification_date from ``sent`` log rows.

    Only flagged duplicates that are not already marked are updated. Returns the
    number restored; DB errors are warned about, never raised.
    """
    from enron_pipeline import db

    sent = read_sent_log(log_path) if sent is None else sent
    if not sent:
        return 0
    restored = 0
    try:
        rows = conn.execute(
            "SELECT message_id FROM emails WHERE is_duplicate = 1 AND notification_sent = 0"
        ).fetchall()
        for (mid,) in rows:
            if mid in sent:
                when = sent[mid] or datetime.now(timezone.utc).isoformat()
                db.mark_notified(conn, mid, when)
                restored += 1
    except sqlite3.Error as exc:
        print(f"warning: could not restore send state ({type(exc).__name__}: {exc})", file=sys.stderr)
        return restored
    print(f"send state: restored {restored} notification flag(s) from {log_path} "
          f"({len(sent)} sent row(s) in log)")
    return restored


def run_pipeline(args: argparse.Namespace) -> int:
    """Default (dry-run) pipeline."""
    from enron_pipeline.stats import StatsCollector

    started = time.perf_counter()
    if not args.skip_extract and not args.fresh and Path(args.db).is_file():
        conn = open_db(args.db, fresh=False)
        try:
            populated = email_count(conn)
        finally:
            conn.close()
        if populated:
            print(f"error: {args.db} already has {populated} emails. Re-extracting would count "
                  "every row as an exact duplicate and overwrite extraction_stats.json.\n"
                  "  pass --fresh to rebuild the DB (send state is restored from the send log), or\n"
                  "  pass --skip-extract to reuse it and only re-run dedup.", file=sys.stderr)
            return EXIT_USAGE
    if not args.skip_extract:
        with stage("dataset"):
            ensure_dataset(args.maildir, args.mailbox_list)
    collector = StatsCollector()
    conn = open_db(args.db, args.fresh)
    try:
        if not args.skip_extract:
            run_extract(conn, args, collector)
        elif not email_count(conn):
            raise StageError("extract", f"--skip-extract given but {args.db} has no emails")
        if not args.skip_dedup:
            run_dedup(conn, args, collector)
            with stage("send-state"):
                restore_send_state(conn, config.SEND_LOG)
            if args.skip_extract:
                merge_dedup_into_stats(collector.dedup or {})
            else:
                write_stats(collector, args.workers)
            run_dry_run(conn)
    finally:
        conn.close()
    if not args.skip_extract:
        collector.print_summary()
    print(f"done in {time.perf_counter() - started:.1f}s")
    return 0


# --------------------------------------------------------------------------- live sending


def require_override(settings: Any) -> str:
    """Return NOTIFY_OVERRIDE_TO or raise StageError (plan rule 0.4)."""
    target = (settings.notify_override_to or "").strip()
    if not target:
        raise StageError(
            "send-live",
            "NOTIFY_OVERRIDE_TO is not set. Refusing to send: live notices must go to your own "
            "address, never to real Enron senders. Set it in .env (see .env.example).",
        )
    return target


def queue_items(conn: sqlite3.Connection, override_to: str, max_send: int,
                exclude: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Queue entries for the --via-claude-code path (same targets as send_live).

    ``exclude`` holds message_ids already logged as ``sent``; they are never queued
    even if their DB flag was lost.
    """
    from enron_pipeline import db, notify

    if max_send <= 0:
        return []
    skip = set(exclude)
    targets = db.fetch_notice_inputs(conn, primary_only=True, unsent_only=True,
                                     limit=max_send + len(skip))
    targets = [n for n in targets if n.dup_message_id not in skip]
    return [
        {
            "message_id": n.dup_message_id,
            "to": override_to,
            "intended_recipient": n.dup_from,
            "subject": notify.notice_subject(n),
            "body": notify.live_body(n),
            "headers": {"References": n.dup_message_id},
        }
        for n in targets[:max_send]
    ]


def write_send_queue(path: Path, items: Sequence[dict[str, Any]]) -> None:
    """Write one JSON object per line (UTF-8), replacing any old queue."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")


def _require_populated(conn: sqlite3.Connection, db_path: Path) -> None:
    if not email_count(conn):
        raise StageError("send-live", f"{db_path} has no emails; run `python main.py` first")


def run_send_live(args: argparse.Namespace) -> int:
    """--send-live: queue for Claude Code, or send through the Python MCP client."""
    from enron_pipeline import notify

    settings = config.load_settings()
    override = require_override(settings)
    conn = open_db(args.db, fresh=False)
    try:
        _require_populated(conn, args.db)
        with stage("send-state"):
            sent_log = read_sent_log(config.SEND_LOG)
            restore_send_state(conn, config.SEND_LOG, sent_log)
        if args.via_claude_code:
            with stage("send-queue"):
                items = queue_items(conn, override, args.max_send, exclude=sent_log)
                write_send_queue(config.SEND_QUEUE, items)
            print(f"wrote {len(items)} item(s) to {config.SEND_QUEUE}. For each, call the MCP send tool, "
                  "then: python main.py --record-send <message_id> --status <status> [--error ...]")
            return 0
        if not settings.mcp_url or not settings.mcp_access_token:
            raise StageError("send-live", "DESCOPE_MCP_URL and DESCOPE_MCP_ACCESS_TOKEN must be set "
                             "(or use --via-claude-code)")
        with stage("send-live"):
            results = notify.send_live(conn, _make_client(settings), override, args.max_send,
                                       config.SEND_LOG, datetime.now(timezone.utc))
    finally:
        conn.close()
    for r in results:
        print(f"  {r.status}" + (f": {r.error}" if r.error else ""))
    sent = sum(r.status == "sent" for r in results)
    print(f"live send: {sent}/{len(results)} sent to {override}; log: {config.SEND_LOG}")
    return 0 if all(r.status == "sent" for r in results) else 1


def _make_client(settings: Any) -> Any:
    """Build the MCP Gmail client (isolated for tests)."""
    from enron_pipeline.mcp_client import McpGmailClient

    return McpGmailClient(settings.mcp_url, settings.mcp_access_token, settings.mcp_send_tool)


def run_record_send(args: argparse.Namespace) -> int:
    """--record-send: same DB + send_log effect as a live send."""
    from enron_pipeline import notify

    settings = config.load_settings()
    override = require_override(settings)
    conn = open_db(args.db, fresh=False)
    try:
        with stage("record-send"):
            notify.record_send(conn, args.record_send, args.status, args.error, override,
                               config.SEND_LOG, datetime.now(timezone.utc))
    finally:
        conn.close()
    print(f"recorded {args.status} for {args.record_send} in {config.SEND_LOG}")
    return 0


# --------------------------------------------------------------------------- main


def dispatch(args: argparse.Namespace) -> Callable[[argparse.Namespace], int]:
    """Pick the action for the parsed arguments."""
    if args.record_send:
        return run_record_send
    if args.send_live:
        return run_send_live
    return run_pipeline


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return dispatch(args)(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - one clean line unless -v
        if args.verbose:
            traceback.print_exc()
        msg = str(exc) if isinstance(exc, StageError) else f"{type(exc).__name__}: {exc}"
        print(f"error: {msg}", file=sys.stderr)
        if not args.verbose:
            print("(re-run with -v for the full traceback)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
