"""Tests for main.py, scripts/download_dataset.py and scripts/mailbox_sizes.py.

Pipeline modules are monkeypatched where they would otherwise need real data.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load("enron_main", ROOT / "main.py")
dl = _load("download_dataset", ROOT / "scripts" / "download_dataset.py")
sizes = _load("mailbox_sizes", ROOT / "scripts" / "mailbox_sizes.py")

from enron_pipeline import config, db, notify  # noqa: E402
from enron_pipeline.models import InsertStats, NoticeInput, ParsedEmail, ParseFailure  # noqa: E402


def _settings(override: str | None, url: str | None = None, token: str | None = None) -> config.Settings:
    return config.Settings(
        notify_override_to=override, mcp_url=url, mcp_access_token=token,
        mcp_send_tool="send_gmail_email", descope_project_id=None, descope_mcp_server_id=None,
        descope_client_id=None, descope_client_secret=None,
    )


def _notice(i: int, sender: str = "jeff@enron.com") -> NoticeInput:
    return NoticeInput(
        dup_message_id=f"<dup{i}@thyme>", dup_date_utc="2001-05-14T23:39:00+00:00",
        dup_subject="Re: Budget", dup_from=sender, orig_message_id=f"<orig{i}@thyme>",
        orig_date_utc="2001-05-10T10:00:00+00:00", orig_subject="Budget",
        similarity_score=95.25, is_primary_target=True,
    )


def _email(i: int, body: str = "hello there") -> ParsedEmail:
    return ParsedEmail(
        message_id=f"<m{i}@x>", date_utc=f"2001-01-{i + 1:02d}T00:00:00+00:00",
        from_address="a@enron.com", to_addresses=("b@enron.com",), subject="Report",
        body=body, source_file=f"maildir/lay-k/inbox/{i}", mailbox="lay-k",
        normalized_subject="report",
    )


@pytest.fixture
def conn_with_emails():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE emails (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO emails DEFAULT VALUES")
    return conn


# --------------------------------------------------------------------------- argument parsing


def test_defaults():
    args = cli.parse_args([])
    assert args.mailbox_list == list(config.DEFAULT_MAILBOXES)
    assert args.workers == config.DEFAULT_WORKERS
    assert args.max_send == 3
    assert args.db == config.DB_PATH and args.maildir == config.MAILDIR
    assert not (args.send_live or args.fresh or args.skip_extract or args.skip_dedup or args.verbose)
    assert cli.dispatch(args) is cli.run_pipeline


def test_all_flags():
    args = cli.parse_args([
        "--maildir", "x/maildir", "--mailboxes", " lay-k, beck-s,,lay-k", "--db", "t.db", "--fresh",
        "--limit", "10", "--workers", "1", "--skip-extract", "--skip-dedup", "-v",
    ])
    assert args.mailbox_list == ["lay-k", "beck-s"]
    assert args.maildir == Path("x/maildir") and args.db == Path("t.db")
    assert args.limit == 10 and args.workers == 1
    assert args.fresh and args.skip_extract and args.skip_dedup and args.verbose


def test_send_flags_dispatch():
    assert cli.dispatch(cli.parse_args(["--send-live", "--max-send", "1"])) is cli.run_send_live
    args = cli.parse_args(["--record-send", "<a@b>", "--status", "sent", "--error", "x"])
    assert cli.dispatch(args) is cli.run_record_send
    assert (args.record_send, args.status, args.error) == ("<a@b>", "sent", "x")


@pytest.mark.parametrize("argv", [
    ["--workers", "0"],
    ["--limit", "-1"],
    ["--max-send", "-1"],
    ["--mailboxes", " , "],
    ["--via-claude-code"],
    ["--record-send", "<a@b>"],
    ["--status", "sent"],
    ["--record-send", "<a@b>", "--status", "bogus"],
    ["--record-send", "<a@b>", "--status", "sent", "--send-live"],
])
def test_invalid_flag_combinations(argv):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2


# --------------------------------------------------------------------------- live-send guards


def test_send_live_refuses_without_override(monkeypatch, capsys):
    monkeypatch.setattr(config, "load_settings", lambda *a, **k: _settings(None))
    monkeypatch.setattr(cli, "open_db", lambda *a, **k: pytest.fail("DB must not be opened"))
    assert cli.main(["--send-live"]) == 1
    err = capsys.readouterr().err
    assert "NOTIFY_OVERRIDE_TO is not set" in err and "Traceback" not in err


def test_record_send_refuses_without_override(monkeypatch, capsys):
    monkeypatch.setattr(config, "load_settings", lambda *a, **k: _settings("   "))
    monkeypatch.setattr(cli, "open_db", lambda *a, **k: pytest.fail("DB must not be opened"))
    assert cli.main(["--record-send", "<a@b>", "--status", "sent"]) == 1
    assert "NOTIFY_OVERRIDE_TO" in capsys.readouterr().err


def test_send_live_requires_mcp_settings(monkeypatch, capsys, conn_with_emails):
    monkeypatch.setattr(config, "load_settings", lambda *a, **k: _settings("me@gmail.com"))
    monkeypatch.setattr(cli, "open_db", lambda *a, **k: conn_with_emails)
    monkeypatch.setattr(cli, "_make_client", lambda s: pytest.fail("no client without settings"))
    assert cli.main(["--send-live"]) == 1
    assert "DESCOPE_MCP_URL" in capsys.readouterr().err


def test_send_live_calls_notify(monkeypatch, conn_with_emails, tmp_path):
    from enron_pipeline.models import SendResult

    monkeypatch.setattr(config, "load_settings", lambda *a, **k: _settings("me@gmail.com", "https://x/mcp", "tok"))
    monkeypatch.setattr(config, "SEND_LOG", tmp_path / "send_log.csv")
    monkeypatch.setattr(cli, "open_db", lambda *a, **k: conn_with_emails)
    fake_client = object()
    monkeypatch.setattr(cli, "_make_client", lambda s: fake_client)
    calls = {}

    def fake_send_live(conn, sender, override_to, max_send, log_path, now):
        calls.update(sender=sender, to=override_to, max_send=max_send, log=log_path)
        return [SendResult(status="sent")]

    monkeypatch.setattr(notify, "send_live", fake_send_live)
    assert cli.main(["--send-live", "--max-send", "1"]) == 0
    assert calls == {"sender": fake_client, "to": "me@gmail.com", "max_send": 1, "log": tmp_path / "send_log.csv"}


def test_via_claude_code_writes_queue(monkeypatch, conn_with_emails, tmp_path, capsys):
    queue = tmp_path / "out" / "send_queue.jsonl"
    queue.parent.mkdir()
    queue.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(config, "load_settings", lambda *a, **k: _settings("me@gmail.com"))
    monkeypatch.setattr(config, "SEND_QUEUE", queue)
    monkeypatch.setattr(cli, "open_db", lambda *a, **k: conn_with_emails)
    seen = {}

    def fake_fetch(conn, *, primary_only=False, unsent_only=False, limit=None):
        seen.update(primary_only=primary_only, unsent_only=unsent_only, limit=limit)
        return [_notice(i) for i in range(5)]  # more than the cap on purpose

    monkeypatch.setattr(db, "fetch_notice_inputs", fake_fetch)
    monkeypatch.setattr(cli, "_make_client", lambda s: pytest.fail("queue path must not send"))
    monkeypatch.setattr(notify, "send_live", lambda *a, **k: pytest.fail("queue path must not send"))

    assert cli.main(["--send-live", "--via-claude-code", "--max-send", "2"]) == 0
    assert seen == {"primary_only": True, "unsent_only": True, "limit": 2}
    items = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines()]
    assert len(items) == 2
    first = items[0]
    assert set(first) == {"message_id", "to", "intended_recipient", "subject", "body", "headers"}
    assert first["to"] == "me@gmail.com"
    assert first["intended_recipient"] == "jeff@enron.com"
    assert first["message_id"] == "<dup0@thyme>"
    assert first["headers"] == {"References": "<dup0@thyme>"}
    assert first["subject"] == "[Duplicate Notice] Re: Budget"
    assert first["body"].startswith("[Redirected for testing; intended recipient: jeff@enron.com]")
    assert "Similarity Score: 95.2%" in first["body"] or "Similarity Score: 95.3%" in first["body"]
    assert "--record-send" in capsys.readouterr().out


def test_send_live_refuses_empty_db(monkeypatch, capsys):
    empty = sqlite3.connect(":memory:")
    monkeypatch.setattr(config, "load_settings", lambda *a, **k: _settings("me@gmail.com"))
    monkeypatch.setattr(cli, "open_db", lambda *a, **k: empty)
    assert cli.main(["--send-live", "--via-claude-code"]) == 1
    assert "run `python main.py` first" in capsys.readouterr().err


def test_record_send_calls_notify(monkeypatch, conn_with_emails, tmp_path):
    monkeypatch.setattr(config, "load_settings", lambda *a, **k: _settings("me@gmail.com"))
    monkeypatch.setattr(config, "SEND_LOG", tmp_path / "log.csv")
    monkeypatch.setattr(cli, "open_db", lambda *a, **k: conn_with_emails)
    calls = []
    monkeypatch.setattr(notify, "record_send",
                        lambda conn, mid, status, error, to, log_path, now: calls.append((mid, status, error, to, log_path)))
    assert cli.main(["--record-send", "<d@x>", "--status", "error", "--error", "boom"]) == 0
    assert calls == [("<d@x>", "error", "boom", "me@gmail.com", tmp_path / "log.csv")]


def test_record_send_unknown_id_is_clean_error(monkeypatch, conn_with_emails, capsys):
    monkeypatch.setattr(config, "load_settings", lambda *a, **k: _settings("me@gmail.com"))
    monkeypatch.setattr(cli, "open_db", lambda *a, **k: conn_with_emails)

    def boom(*a, **k):
        raise ValueError("unknown duplicate message_id: <z@x>")

    monkeypatch.setattr(notify, "record_send", boom)
    assert cli.main(["--record-send", "<z@x>", "--status", "sent"]) == 1
    err = capsys.readouterr().err
    assert "[record-send] ValueError: unknown duplicate message_id" in err
    assert "Traceback" not in err


# --------------------------------------------------------------------------- pipeline orchestration


def test_stage_failure_is_one_clean_line(monkeypatch, capsys):
    def broken(*a, **k):
        raise OSError("disk on fire")

    monkeypatch.setattr(cli, "ensure_dataset", broken)
    assert cli.main([]) == 1
    err = capsys.readouterr().err
    assert "error: [dataset] OSError: disk on fire" in err
    assert "Traceback" not in err and "-v" in err


def test_stage_failure_verbose_shows_traceback(monkeypatch, capsys):
    monkeypatch.setattr(cli, "ensure_dataset", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert cli.main(["-v"]) == 1
    assert "Traceback" in capsys.readouterr().err


class _Collector:
    def __init__(self):
        self.parsed, self.failed, self.inserts = 0, [], []

    def add_parsed(self, email):
        self.parsed += 1

    def add_failure(self, failure):
        self.failed.append(failure)

    def add_insert_stats(self, stats):
        self.inserts.append(stats)
        self.failed.extend(stats.failures)


def test_load_results_batches_and_collects_failures(monkeypatch):
    batches = []

    def fake_insert(conn, emails):
        batches.append([e.message_id for e in emails])
        bad = tuple(ParseFailure(e.source_file, "insert", "db_error: x") for e in emails if e.message_id == "<m4@x>")
        return InsertStats(inserted=len(emails) - len(bad), failures=bad)

    monkeypatch.setattr(db, "insert_batch", fake_insert)
    results = [_email(0), ParseFailure("maildir/lay-k/a", "parse", "bad_date"), _email(1),
               _email(2), _email(3), _email(4)]
    col = _Collector()
    failures = cli.load_results(None, results, col, batch_size=2)
    assert batches == [["<m0@x>", "<m1@x>"], ["<m2@x>", "<m3@x>"], ["<m4@x>"]]
    assert col.parsed == 5
    assert [f.stage for f in failures] == ["parse", "insert"]
    assert [f.reason for f in col.failed] == ["bad_date", "db_error: x"]


def _fake_parse(path, root):
    """Top-level so it is picklable; turns a file name into a ParsedEmail."""
    i = int(Path(path).name)
    if i == 3:
        return ParseFailure(f"maildir/lay-k/inbox/{i}", "validate", "missing_to")
    return _email(i, body="the quarterly report body " + ("x" if i == 2 else ""))


def test_full_dry_run_pipeline(monkeypatch, tmp_path):
    """Default run with discover/parse faked and real db/dedup/notify/stats."""
    from enron_pipeline import discover, parser

    for name, value in {"ERROR_LOG": tmp_path / "error_log.txt", "STATS_PATH": tmp_path / "stats.json",
                        "DUP_REPORT": tmp_path / "dups.csv", "REPLIES_DIR": tmp_path / "replies"}.items():
        monkeypatch.setattr(config, name, value)
    (tmp_path / "replies").mkdir()
    (tmp_path / "replies" / "old.eml").write_text("stale", encoding="utf-8")
    monkeypatch.setattr(cli, "ensure_dataset", lambda *a, **k: None)
    monkeypatch.setattr(discover, "iter_email_files",
                        lambda maildir, mailboxes=None: iter(Path(f"m/lay-k/inbox/{i}") for i in range(5)))
    monkeypatch.setattr(parser, "parse_file", _fake_parse)

    dbp = tmp_path / "t.db"
    assert cli.main(["--db", str(dbp), "--fresh", "--workers", "1", "--maildir", str(tmp_path / "m")]) == 0

    stats = json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))
    assert stats["total_files_found"] == 5 and stats["parsed_ok"] == 4 and stats["failed"] == 1
    assert stats["workers"] == 1 and "extract_workers_1_seconds" in stats["runtimes"]
    assert stats["dedup"]["emails_flagged"] == 3
    assert "missing_to" in (tmp_path / "error_log.txt").read_text(encoding="utf-8")
    emls = sorted(p.name for p in (tmp_path / "replies").glob("*.eml"))
    assert len(emls) == 3 and "old.eml" not in emls
    assert (tmp_path / "dups.csv").read_text(encoding="utf-8").count("\n") == 4

    # --skip-extract keeps extraction stats and only refreshes dedup.
    assert cli.main(["--db", str(dbp), "--skip-extract", "--workers", "1"]) == 0
    again = json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))
    assert again["parsed_ok"] == 4 and again["dedup"]["emails_flagged"] == 3


def test_skip_extract_on_empty_db_fails(monkeypatch, tmp_path, capsys):
    assert cli.main(["--db", str(tmp_path / "empty.db"), "--skip-extract"]) == 1
    assert "has no emails" in capsys.readouterr().err


def test_clear_replies(tmp_path):
    (tmp_path / "a.eml").write_text("x")
    (tmp_path / "keep.txt").write_text("x")
    assert cli.clear_replies(tmp_path) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["keep.txt"]
    assert cli.clear_replies(tmp_path / "missing") == 0


# --------------------------------------------------------------------------- download / extract


def _add(tf: tarfile.TarFile, name: str, data: bytes = b"x", kind: bytes = tarfile.REGTYPE, link: str = "") -> None:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = link
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    tf.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)


def _tarball(path: Path, members: list[tuple]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for m in members:
            _add(tf, *m)
    return path


def test_extract_selected_mailboxes_and_name_mapping(tmp_path):
    tb = _tarball(tmp_path / "e.tar.gz", [
        ("maildir/lay-k", b"", tarfile.DIRTYPE),
        ("maildir/lay-k/inbox/1.", b"one"),
        ("maildir/lay-k/sent/22.", b"two"),
        ("./maildir/lay-k/notes/plain", b"three"),
        ("maildir/lay-k/inbox/link.", b"", tarfile.SYMTYPE, "/etc/passwd"),
        ("maildir/lay-kx/inbox/9.", b"prefix lookalike"),
        ("maildir/beck-s/inbox/5.", b"other"),
    ])
    out = tmp_path / "out"
    extracted, renamed = dl.extract(tb, out, ["lay-k"])
    assert (extracted, renamed) == (3, 2)
    assert (out / "maildir/lay-k/inbox/1_").read_bytes() == b"one"
    assert (out / "maildir/lay-k/sent/22_").read_bytes() == b"two"
    assert (out / "maildir/lay-k/notes/plain").read_bytes() == b"three"
    assert not (out / "maildir/lay-k/inbox/link_").exists()
    assert not (out / "maildir/lay-kx").exists() and not (out / "maildir/beck-s").exists()


@pytest.mark.parametrize("name, expected", [
    ("45.", "45_"), ("45", "45"), ("a..", "a__"), ("x. ", "x__"), ("inbox", "inbox"),
])
def test_windows_safe_component(name, expected):
    assert dl.windows_safe_component(name) == expected


@pytest.mark.parametrize("name", [
    "/etc/passwd", "maildir/lay-k/../../evil", "C:/evil", "maildir/lay-k/a\\b",
    "maildir//lay-k/x", "maildir/lay-k/./x", "",
])
def test_member_relpath_rejects_unsafe(name):
    with pytest.raises(dl.UnsafeMemberError):
        dl.member_relpath(name)


def test_safe_target_rejects_escape(tmp_path):
    with pytest.raises(dl.UnsafeMemberError):
        dl.safe_target(tmp_path / "root", ["..", "evil"])
    assert dl.safe_target(tmp_path / "root", ["maildir", "a"]).name == "a"


def test_extract_refuses_traversal_member(tmp_path):
    tb = _tarball(tmp_path / "bad.tar.gz", [("maildir/lay-k/../../evil.txt", b"pwned")])
    with pytest.raises(dl.UnsafeMemberError):
        dl.extract(tb, tmp_path / "out", ["lay-k"])
    assert not (tmp_path / "evil.txt").exists()


def test_download_skips_complete_file_without_network(monkeypatch, tmp_path):
    dest = tmp_path / "e.tar.gz"
    dest.write_bytes(b"0123456789")
    monkeypatch.setattr(dl, "KNOWN_SIZE", 10)
    monkeypatch.setattr(dl, "remote_size", lambda *a, **k: pytest.fail("no HEAD request expected"))
    monkeypatch.setattr(dl.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no network expected"))
    assert dl.download("https://example.invalid/e.tar.gz", dest) == dest


def test_download_resumes_with_range(monkeypatch, tmp_path):
    dest = tmp_path / "e.tar.gz"
    (tmp_path / "e.tar.gz.part").write_bytes(b"01234")
    monkeypatch.setattr(dl, "remote_size", lambda *a, **k: 10)
    seen = {}

    class Resp(io.BytesIO):
        status = 206

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        seen["range"] = req.get_header("Range")
        return Resp(b"56789")

    monkeypatch.setattr(dl.urllib.request, "urlopen", fake_urlopen)
    dl.download("https://example.invalid/e.tar.gz", dest)
    assert seen["range"] == "bytes=5-"
    assert dest.read_bytes() == b"0123456789"
    assert not (tmp_path / "e.tar.gz.part").exists()


def test_ensure_dataset_skips_when_present(monkeypatch, tmp_path):
    (tmp_path / "maildir" / "lay-k" / "inbox").mkdir(parents=True)
    (tmp_path / "maildir" / "lay-k" / "inbox" / "1_").write_text("x")
    monkeypatch.setattr(dl, "download", lambda *a, **k: pytest.fail("must not download"))
    assert dl.ensure_dataset(["lay-k"], data_dir=tmp_path) == []
    assert dl.missing_mailboxes(tmp_path / "maildir", ["lay-k", "beck-s"]) == ["beck-s"]


def test_ensure_dataset_extracts_missing(monkeypatch, tmp_path):
    tb = _tarball(tmp_path / config.TARBALL.name, [("maildir/beck-s/inbox/1.", b"x")])
    monkeypatch.setattr(dl, "download", lambda url, dest: tb)
    assert dl.ensure_dataset(["beck-s"], data_dir=tmp_path) == ["beck-s"]
    assert (tmp_path / "maildir/beck-s/inbox/1_").exists()


def test_main_dataset_check_uses_download_script(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(dl, "ensure_dataset", lambda missing, data_dir: calls.append((missing, data_dir)))
    (tmp_path / "maildir" / "lay-k").mkdir(parents=True)
    (tmp_path / "maildir" / "lay-k" / "f").write_text("x")
    cli.ensure_dataset(tmp_path / "maildir", ["lay-k", "beck-s"])
    assert calls == [(["beck-s"], tmp_path)]


# --------------------------------------------------------------------------- mailbox sizes


def test_mailbox_sizes_table(tmp_path):
    for mb, n in (("a-x", 3), ("b-y", 2)):
        for i in range(n):
            (tmp_path / mb / "inbox").mkdir(parents=True, exist_ok=True)
            (tmp_path / mb / "inbox" / f"{i}_").write_text("x")
    rows = sizes.mailbox_counts(tmp_path, ["a-x", "b-y", "missing"])
    assert rows == [("a-x", 3), ("b-y", 2), ("missing", 0)]
    table = sizes.format_table(rows)
    assert "TOTAL" in table and "5" in table and "WARNING" in table and "(missing)" in table
    assert "OK: total" in sizes.format_table([("big", 10_000)])


def test_iter_parsed_pool_matches_serial():
    """Real parse_file over the hand-made fixtures: 2 worker processes give the
    same results, in the same order, as the serial path (plan 4.4b)."""
    fixtures = ROOT / "tests" / "fixtures"
    paths = sorted(p for p in (fixtures / "maildir").rglob("*") if p.is_file())
    assert paths
    serial = list(cli.iter_parsed(paths, fixtures, workers=1))
    pooled = list(cli.iter_parsed(paths, fixtures, workers=2))
    assert pooled == serial
