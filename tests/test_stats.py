"""Tests for enron_pipeline.stats (synthetic data only)."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from enron_pipeline.models import DedupResult, DuplicateFlag, InsertStats, ParsedEmail, ParseFailure
from enron_pipeline.stats import COMPLETENESS_FIELDS, StatsCollector, failure_slug, write_error_log


def make_email(n: int, **overrides: object) -> ParsedEmail:
    base = ParsedEmail(
        message_id=f"<{n}@x>",
        date_utc="2001-10-01T12:00:00+00:00",
        from_address="a@enron.com",
        to_addresses=("b@enron.com",),
        subject=f"s{n}",
        body="hello",
        source_file=f"maildir/lay-k/inbox/{n}",
        mailbox="lay-k",
        normalized_subject=f"s{n}",
    )
    return replace(base, **overrides)


def test_completeness_math() -> None:
    sc = StatsCollector()
    sc.add_parsed(make_email(1, cc_addresses=("c@enron.com",), x_folder="Inbox", subject=""))
    sc.add_parsed(make_email(2, cc_addresses=(), x_folder=""))
    sc.add_parsed(make_email(3, x_folder=None, has_attachment=True))
    sc.add_parsed(make_email(4, mailbox="skilling-j", headings="SUMMARY"))
    comp = sc.to_dict()["field_completeness"]
    assert set(comp) == set(COMPLETENESS_FIELDS)
    assert comp["message_id"] == 100.0
    assert comp["to_addresses"] == 100.0
    assert comp["subject"] == 75.0  # "" counts as missing
    assert comp["cc_addresses"] == 25.0  # empty tuple counts as missing
    assert comp["x_folder"] == 25.0  # "" and None both missing
    assert comp["has_attachment"] == 25.0
    assert comp["headings"] == 25.0
    assert comp["bcc_addresses"] == 0.0


def test_completeness_rounding_and_empty() -> None:
    assert all(v == 0.0 for v in StatsCollector().field_completeness().values())
    sc = StatsCollector()
    for i in range(3):
        sc.add_parsed(make_email(i, x_origin="Lay-K" if i == 0 else None))
    assert sc.field_completeness()["x_origin"] == pytest.approx(33.33)


def test_counts_and_sections(tmp_path: Path) -> None:
    sc = StatsCollector()
    sc.set_total_files(10)
    sc.add_parsed(make_email(1, body="", forwarded_content="---- Forwarded ----"))
    sc.add_parsed(make_email(2, body="", forwarded_content=None))
    sc.add_parsed(make_email(3, mailbox="beck-s", date_tz_assumed=True))
    sc.add_failure(ParseFailure("f1", "parse", "missing_message_id"))
    sc.add_failure(ParseFailure("f2", "parse", "bad_date: Thu, 32 Foo"))
    sc.add_failure(ParseFailure("f3", "validate", "bad_date: x: y"))
    sc.add_insert_stats(
        InsertStats(
            inserted=2,
            exact_duplicates_skipped=1,
            exact_duplicate_ids=("<1@x>",),
            failures=(ParseFailure("f4", "insert", "db_error: IntegrityError: UNIQUE"),),
        )
    )
    sc.set_runtime("extract_seconds", 1.23456)
    flags = [DuplicateFlag("<2@x>", "<1@x>", 95.0, True)]
    sc.set_dedup(DedupResult(flags=flags, groups_found=1, emails_flagged=1, avg_group_size=2.0))
    d = sc.to_dict()
    assert d["total_files_found"] == 10
    assert d["parsed_ok"] == 3
    assert d["failed"] == 4
    assert d["failure_reasons"] == {"bad_date": 2, "missing_message_id": 1, "db_error": 1}
    assert d["exact_duplicates_skipped"] == 1
    assert d["inserted"] == 2
    assert d["per_mailbox"] == {"beck-s": 1, "lay-k": 2}
    assert d["empty_body_with_forward"] == 1
    assert d["date_tz_assumed"] == 1
    assert d["runtimes"] == {"extract_seconds": 1.235}
    assert d["dedup"]["groups_found"] == 1 and "flags" not in d["dedup"]

    out = tmp_path / "stats.json"
    sc.write_json(out)
    assert json.loads(out.read_text(encoding="utf-8")) == d


def test_failure_slug() -> None:
    assert failure_slug("missing_to") == "missing_to"
    assert failure_slug("bad_date: Foo: bar") == "bad_date"


def test_print_summary(capsys: pytest.CaptureFixture[str]) -> None:
    sc = StatsCollector()
    sc.add_parsed(make_email(1))
    sc.add_failure(ParseFailure("f", "read", "unreadable: x"))
    sc.set_dedup(DedupResult(groups_found=2))
    sc.print_summary()
    out = capsys.readouterr().out
    assert "parsed ok" in out and "unreadable" in out and "groups_found" in out


def test_error_log_format(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "error_log.txt"
    failures = [
        ParseFailure("maildir/lay-k/inbox/1", "parse", "missing_message_id"),
        ParseFailure("maildir/lay-k/inbox/ü2", "insert", "db_error: line1\nline2"),
    ]
    write_error_log(path, failures)
    lines = path.read_text(encoding="utf-8").split("\n")
    assert lines[-1] == ""  # trailing newline
    lines = lines[:-1]
    assert len(lines) == 2
    pattern = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00 \| (.+) \| (\w+) \| (.+)$")
    m1, m2 = pattern.match(lines[0]), pattern.match(lines[1])
    assert m1 and m1.groups() == ("maildir/lay-k/inbox/1", "parse", "missing_message_id")
    assert m2 and m2.groups() == ("maildir/lay-k/inbox/ü2", "insert", "db_error: line1 line2")
