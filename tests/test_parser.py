"""Tests for parser.parse_file / parse_bytes and discover helpers (fixtures)."""
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

import pytest

from conftest import FIXTURES
from enron_pipeline.discover import iter_email_files, mailbox_of, relative_source
from enron_pipeline.models import ParsedEmail, ParseFailure
from enron_pipeline.parser import parse_bytes, parse_file

MAILDIR = FIXTURES / "maildir"


def fx(rel: str) -> ParsedEmail | ParseFailure:
    return parse_file(MAILDIR / rel, FIXTURES)


def ok(rel: str) -> ParsedEmail:
    result = fx(rel)
    assert isinstance(result, ParsedEmail), result
    return result


def fail(rel: str) -> ParseFailure:
    result = fx(rel)
    assert isinstance(result, ParseFailure), result
    return result


# ------------------------------------------------------------------ discover
def test_iter_email_files_sorted_and_filtered() -> None:
    all_files = list(iter_email_files(MAILDIR))
    assert len(all_files) == 16
    assert all_files == list(iter_email_files(MAILDIR))  # deterministic
    only_b = list(iter_email_files(MAILDIR, ["test-b", "missing-box"]))
    assert len(only_b) == 9 and all(mailbox_of(p, MAILDIR) == "test-b" for p in only_b)


def test_relative_source_strips_one_trailing_underscore_or_dot(tmp_path: Path) -> None:
    assert relative_source(MAILDIR / "test-a" / "inbox" / "1_", FIXTURES) == "maildir/test-a/inbox/1"
    assert relative_source(tmp_path / "maildir" / "x" / "45.", tmp_path) == "maildir/x/45"
    assert relative_source(tmp_path / "maildir" / "x" / "45__", tmp_path) == "maildir/x/45_"
    assert relative_source(tmp_path / "maildir" / "x" / "45", tmp_path) == "maildir/x/45"


# ------------------------------------------------------------------ happy paths
def test_basic_fields_crlf_pdt_and_attachment_ref() -> None:
    e = ok("test-a/inbox/1_")
    assert e.message_id == "<1001.JavaMail.evans@thyme>"
    assert e.date_utc == "2001-05-29T14:17:11+00:00"
    assert e.from_address == "kenneth.lay@enron.com"
    assert e.to_addresses == ("jeff.skilling@enron.com",)
    assert e.subject == "Re: Budget for Q3"
    assert e.normalized_subject == "budget for q3"
    assert e.source_file == "maildir/test-a/inbox/1"
    assert e.mailbox == "test-a"
    assert e.has_attachment is True
    assert e.body.startswith("Jeff,") and e.body.endswith("Ken")
    assert e.content_type == "text/plain"
    assert e.x_folder == "\\Lay, Kenneth\\Inbox"
    assert e.x_origin == "LAY-K"
    assert e.x_cc is None  # blank X-cc stored as None
    assert e.date_tz_assumed is False and e.from_is_smtp is True


def test_cdt_folded_to_22_recipients_heading_cc_bcc() -> None:
    e = ok("test-a/inbox/2_")
    assert e.date_utc == "2001-05-14T15:00:00+00:00"
    assert len(e.to_addresses) == 22  # duplicate user01 removed
    assert e.to_addresses[0] == "user01@enron.com" and e.to_addresses[-1] == "user22@enron.com"
    assert e.cc_addresses == ("cc.one@enron.com", "cc.two@enron.com")
    assert e.bcc_addresses == e.cc_addresses
    assert e.from_address == "kenneth.lay@enron.com"
    assert e.headings is not None
    assert e.headings.split("\n") == ["QUARTERLY RESULTS", "Action items:", "1. Trading Update"]
    assert e.has_attachment is False


def test_garbage_bytes_body_and_8bit_subject() -> None:
    e = ok("test-a/inbox/5_")
    assert e.subject == "Caf\u00e9 \u201cquotes\u201d"  # 8-bit header -> cp1252
    # bogus charset + byte 0x81 (undefined in cp1252) -> latin-1 last resort
    assert e.body == "Smart \x93quotes\x94 and \x81\xff\xfe garbage bytes \x00 here."
    assert e.date_utc == "2002-01-02T14:30:00+00:00"


def test_exchange_from_kept_as_non_smtp() -> None:
    e = ok("test-a/sent_items/6_")
    assert e.from_address == "/o=enron/ou=na/cn=recipients/cn=klay"
    assert e.from_is_smtp is False
    assert e.to_addresses == ("jeff.skilling@enron.com", "/o=enron/ou=na/cn=recipients/cn=jskilli")


def test_enron_odd_addresses() -> None:
    e = ok("test-a/sent_items/7_")
    assert e.from_address == "lay@enron.com"
    assert e.to_addresses == (
        "ben@enron.com",
        "jeff@enron.com",
        "john.doe@enron.com",
        "palmer@enron.com",
        "c@enron.com",
    )
    assert e.cc_addresses == ("john.smith@enron.com",)
    assert e.date_tz_assumed is False


def test_nested_forward() -> None:
    e = ok("test-b/inbox/8_")
    assert e.body == "FYI see below."
    assert e.forwarded_content.startswith("---------------------- Forwarded by Sally Beck")
    assert "-----Original Message-----" in e.forwarded_content
    assert "Original schedule attached." in e.forwarded_content
    assert e.subject == "FW: FW: Gas schedule"
    assert e.normalized_subject == "gas schedule"


def test_quoted_reply() -> None:
    e = ok("test-b/inbox/9_")
    assert e.quoted_content.startswith("On Mon, 7 Jan 2002, Sally Beck wrote:")
    assert "> It needs sign-off." in e.quoted_content
    assert ">" not in e.body and "Vince" in e.body


def test_empty_subject_allowed() -> None:
    e = ok("test-b/inbox/11_")
    assert e.subject == "" and e.normalized_subject == ""


def test_multipart_with_attachment_part() -> None:
    e = ok("test-b/inbox/13_")
    assert e.has_attachment is True
    assert e.body == "Report résumé below."
    assert e.content_type == "multipart/mixed"


def test_naive_date_assumed() -> None:
    e = ok("test-b/inbox/14_")
    assert e.date_tz_assumed is True
    assert e.date_utc == "2002-01-09T21:00:00+00:00"


def test_html_only_and_encoded_subject() -> None:
    e = ok("test-b/inbox/16_")
    assert e.subject == "Présentation html"
    assert "Hello" in e.body and "<b>" not in e.body


# ------------------------------------------------------------------ failures
@pytest.mark.parametrize(
    "rel, reason",
    [
        ("test-a/inbox/3_", "missing_message_id"),
        ("test-a/inbox/4_", "missing_date"),
        ("test-b/inbox/10_", "missing_to"),
        ("test-b/inbox/12_", "missing_subject"),
    ],
)
def test_validation_failures(rel: str, reason: str) -> None:
    f = fail(rel)
    assert f.reason == reason
    assert f.stage == "validate"
    assert f.source_file == "maildir/" + rel[:-1]


def test_bad_date_failure() -> None:
    f = fail("test-b/inbox/15_")
    assert f.reason.startswith("bad_date: ")


def test_missing_from() -> None:
    raw = b"Message-ID: <x>\nDate: Mon, 14 May 2001 10:00:00 -0500\nTo: a@b.com\nSubject: s\n\nhi"
    f = parse_bytes(raw, "maildir/x/1", "x")
    assert isinstance(f, ParseFailure) and f.reason == "missing_from"


def test_read_error_never_raises(tmp_path: Path) -> None:
    f = parse_file(tmp_path / "maildir" / "x" / "nope_", tmp_path)
    assert isinstance(f, ParseFailure)
    assert f.stage == "read" and f.reason.startswith("read_error: ")
    assert f.source_file == "maildir/x/nope"


def test_total_garbage_never_raises() -> None:
    for raw in (b"", b"\x00\xff\xfe" * 100, b"::::\n\n\n", b"Message-ID: <a>\nDate: \xff\xff\n\n"):
        assert isinstance(parse_bytes(raw, "maildir/x/1", "x"), (ParsedEmail, ParseFailure))


# ------------------------------------------------------------------ parallel == serial
def _parse_all(workers: int) -> list:
    paths = list(iter_email_files(MAILDIR))
    fn = partial(parse_file, root=FIXTURES)
    if workers == 1:
        return [fn(p) for p in paths]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, paths, chunksize=3))


def test_process_pool_matches_serial() -> None:
    serial = _parse_all(1)
    parallel = _parse_all(2)
    assert serial == parallel
    assert sum(isinstance(r, ParsedEmail) for r in serial) == 11
    assert sum(isinstance(r, ParseFailure) for r in serial) == 5
