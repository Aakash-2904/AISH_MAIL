"""Tests for notify: template golden test, subjects, file names, dry-run, live-send orchestration."""

from __future__ import annotations

import csv
import email
from dataclasses import replace
from datetime import datetime, timezone
from email import policy
from pathlib import Path

import pytest

from enron_pipeline import db, notify
from enron_pipeline.models import NoticeInput, SendResult

GOLDEN = Path(__file__).resolve().parent / "golden"
NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)

NOTICE = NoticeInput(
    dup_message_id="<12345.1075855687451.JavaMail.evans@thyme>",
    dup_date_utc="2001-05-14T23:39:00+00:00",
    dup_subject="Re: Re: Q2 forecast",
    dup_from="phillip.allen@enron.com",
    orig_message_id="<11111.1075855600000.JavaMail.evans@thyme>",
    orig_date_utc="2001-05-10T08:05:00+00:00",
    orig_subject="RE: Fwd: Q2 forecast",
    similarity_score=97.456,
    is_primary_target=True,
)


def _golden(name: str) -> str:
    """Golden text with line endings normalised (git may check out CRLF)."""
    return (GOLDEN / name).read_bytes().decode("utf-8").replace("\r\n", "\n")


def _notice(i: int, **kw: object) -> NoticeInput:
    """A distinct notice per index."""
    base = replace(NOTICE, dup_message_id=f"<{i}.JavaMail.evans@thyme>", dup_from=f"user{i}@enron.com")
    return replace(base, **kw) if kw else base


# ---------------------------------------------------------------- rendering

def test_render_body_matches_golden() -> None:
    assert notify.render_body(NOTICE) == _golden("notice_body_expected.txt")


def test_render_notice_matches_golden_eml() -> None:
    msg = notify.render_notice(NOTICE, NOW)
    assert msg.as_string() == _golden("notice_expected.eml")


def test_render_notice_headers_and_override_to() -> None:
    msg = notify.render_notice(NOTICE, NOW, to="me@example.com")
    assert msg["To"] == "me@example.com"
    assert msg["Date"] == "Wed, 18 Nov 2026 12:00:00 +0000"
    assert msg["References"] == NOTICE.dup_message_id
    assert msg.get_content_type() == "text/plain"
    assert msg.get_content_charset() == "utf-8"


def test_format_display_date() -> None:
    assert notify.format_display_date("2001-05-14T23:39:00+00:00") == "Mon, 14 May 2001 23:39:00 +0000"
    assert notify.format_display_date("2001-05-14T16:39:00-07:00") == "Mon, 14 May 2001 23:39:00 +0000"
    assert notify.format_display_date("not a date") == "not a date"


@pytest.mark.parametrize(
    ("orig", "expected"),
    [
        ("Q2 forecast", "[Duplicate Notice] Re: Q2 forecast"),
        ("Re: Q2 forecast", "[Duplicate Notice] Re: Q2 forecast"),
        ("RE: re: FW: Fwd: Q2 forecast", "[Duplicate Notice] Re: Q2 forecast"),
        ("[Fwd: Q2 forecast]", "[Duplicate Notice] Re: Q2 forecast"),
        ("", "[Duplicate Notice] Re: "),
    ],
)
def test_notice_subject_strips_prefixes(orig: str, expected: str) -> None:
    assert notify.notice_subject(replace(NOTICE, orig_subject=orig)) == expected


@pytest.mark.parametrize(("score", "shown"), [(100.0, "100.0%"), (85.04, "85.0%"), (90.06, "90.1%")])
def test_similarity_one_decimal(score: float, shown: str) -> None:
    assert f"  Similarity Score: {shown}\n" in notify.render_body(replace(NOTICE, similarity_score=score))


def test_non_ascii_subject_survives_round_trip(tmp_path: Path) -> None:
    n = replace(NOTICE, dup_subject="Café meeting – Zürich")
    assert notify.write_dry_run([n], tmp_path, NOW) == 1
    (eml,) = tmp_path.glob("*.eml")
    parsed = email.message_from_bytes(eml.read_bytes(), policy=policy.default)
    assert "Subject:     Café meeting – Zürich" in parsed.get_content()


# ---------------------------------------------------------------- file names

@pytest.mark.parametrize(
    ("mid", "expected"),
    [
        ("<123.JavaMail.evans@thyme>", "123.JavaMail.evans_thyme"),
        ("<a/b\\c:d*e?f>", "a_b_c_d_e_f"),
        ("<CON>", "_CON"),
    ],
)
def test_safe_filename(mid: str, expected: str) -> None:
    assert notify.safe_filename(mid) == expected


def test_safe_filename_edge_cases() -> None:
    import re

    for mid in ["", "<>", "@@@", "<" + "x" * 500 + "@host>", "<é ü @ ö>"]:
        stem = notify.safe_filename(mid)
        assert stem and re.fullmatch(r"[A-Za-z0-9._-]+", stem), mid
        assert len(stem) <= notify.MAX_STEM_LEN + 9
    long_a = notify.safe_filename("<" + "x" * 500 + "a@host>")
    long_b = notify.safe_filename("<" + "x" * 500 + "b@host>")
    assert long_a != long_b


# ---------------------------------------------------------------- dry run

def test_write_dry_run_one_file_per_notice(tmp_path: Path) -> None:
    notices = [_notice(i, is_primary_target=(i == 0)) for i in range(5)]
    out = tmp_path / "replies"
    assert notify.write_dry_run(notices, out, NOW) == 5
    files = sorted(out.glob("*.eml"))
    assert len(files) == 5
    parsed = email.message_from_bytes(files[0].read_bytes(), policy=policy.default)
    assert parsed["Subject"] == "[Duplicate Notice] Re: Q2 forecast"


def test_write_dry_run_collisions_do_not_overwrite(tmp_path: Path) -> None:
    notices = [_notice(0, dup_message_id="<a@b>"), _notice(1, dup_message_id="<a_b>"),
               _notice(2, dup_message_id="<A@B>")]
    assert notify.write_dry_run(notices, tmp_path, NOW) == 3
    assert len(list(tmp_path.glob("*.eml"))) == 3


def test_write_dry_run_skips_bad_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = notify.render_notice

    def flaky(n: NoticeInput, now: datetime, to: str | None = None):  # type: ignore[no-untyped-def]
        if n.dup_message_id == "<1.JavaMail.evans@thyme>":
            raise RuntimeError("boom")
        return real(n, now, to)

    monkeypatch.setattr(notify, "render_notice", flaky)
    assert notify.write_dry_run([_notice(i) for i in range(3)], tmp_path, NOW) == 2


# ---------------------------------------------------------------- live send

class FakeSender:
    """Records calls; returns scripted results or raises."""

    def __init__(self, outcomes: list[SendResult | Exception] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[tuple[str, str, str, dict[str, str]]] = []

    def send(self, to: str, subject: str, body: str, headers: dict[str, str]) -> SendResult:
        self.calls.append((to, subject, body, headers))
        outcome = self.outcomes.pop(0) if self.outcomes else SendResult(status="sent")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeDb:
    """In-memory stand-in for db.fetch_notice_inputs / db.mark_notified."""

    def __init__(self, notices: list[NoticeInput]) -> None:
        self.notices = notices  # assumed already latest-first
        self.sent: dict[str, str] = {}
        self.fetch_calls: list[dict[str, object]] = []

    def fetch(self, conn: object, *, primary_only: bool = False, unsent_only: bool = False,
              limit: int | None = None) -> list[NoticeInput]:
        self.fetch_calls.append({"primary_only": primary_only, "unsent_only": unsent_only, "limit": limit})
        rows = [n for n in self.notices
                if (not primary_only or n.is_primary_target)
                and (not unsent_only or n.dup_message_id not in self.sent)]
        return rows[:limit] if limit is not None else rows

    def mark(self, conn: object, message_id: str, when_iso_utc: str) -> None:
        self.sent[message_id] = when_iso_utc


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> FakeDb:
    fdb = FakeDb([_notice(i, is_primary_target=(i % 2 == 0)) for i in range(10)])
    monkeypatch.setattr(db, "fetch_notice_inputs", fdb.fetch)
    monkeypatch.setattr(db, "mark_notified", fdb.mark)
    return fdb


def _log_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_send_live_success_path(fake_db: FakeDb, tmp_path: Path) -> None:
    log_path = tmp_path / "send_log.csv"
    sender = FakeSender()
    results = notify.send_live(None, sender, "me@example.com", 3, log_path, NOW)  # type: ignore[arg-type]
    assert [r.status for r in results] == ["sent"] * 3
    assert fake_db.fetch_calls[0] == {"primary_only": True, "unsent_only": True, "limit": 3}
    to, subject, body, headers = sender.calls[0]
    assert to == "me@example.com"
    assert subject == "[Duplicate Notice] Re: Q2 forecast"
    assert headers == {"References": "<0.JavaMail.evans@thyme>"}
    expected = "[Redirected for testing; intended recipient: user0@enron.com]\n\n" + notify.render_body(_notice(0))
    assert body == expected
    assert list(fake_db.sent) == [f"<{i}.JavaMail.evans@thyme>" for i in (0, 2, 4)]
    assert set(fake_db.sent.values()) == {"2026-11-18T12:00:00+00:00"}
    rows = _log_rows(log_path)
    assert list(rows[0].keys()) == list(notify.SEND_LOG_COLUMNS)
    assert rows[0] == {
        "timestamp": "2026-11-18T12:00:00+00:00", "recipient": "me@example.com",
        "subject": "[Duplicate Notice] Re: Q2 forecast", "status": "sent", "error": "",
        "intended_recipient": "user0@enron.com", "message_id": "<0.JavaMail.evans@thyme>",
    }


def test_send_live_is_idempotent(fake_db: FakeDb, tmp_path: Path) -> None:
    log_path = tmp_path / "send_log.csv"
    notify.send_live(None, FakeSender(), "me@example.com", 3, log_path, NOW)  # type: ignore[arg-type]
    second = FakeSender()
    notify.send_live(None, second, "me@example.com", 3, log_path, NOW)  # type: ignore[arg-type]
    sent_ids = [c[3]["References"] for c in second.calls]
    assert sent_ids == ["<6.JavaMail.evans@thyme>", "<8.JavaMail.evans@thyme>"]
    assert len(_log_rows(log_path)) == 5  # header written once


def test_send_live_error_and_exception_paths(fake_db: FakeDb, tmp_path: Path) -> None:
    log_path = tmp_path / "send_log.csv"
    sender = FakeSender([SendResult(status="error", error="insufficient_scope"),
                         RuntimeError("connection reset"),
                         SendResult(status="approval_timeout")])
    results = notify.send_live(None, sender, "me@example.com", 5, log_path, NOW)  # type: ignore[arg-type]
    assert [r.status for r in results] == ["error", "error", "approval_timeout", "sent", "sent"]
    assert results[1].error == "RuntimeError: connection reset"
    assert list(fake_db.sent) == ["<6.JavaMail.evans@thyme>", "<8.JavaMail.evans@thyme>"]
    rows = _log_rows(log_path)
    assert [r["status"] for r in rows] == ["error", "error", "approval_timeout", "sent", "sent"]
    assert rows[0]["error"] == "insufficient_scope"


def test_send_live_cap(fake_db: FakeDb, tmp_path: Path) -> None:
    sender = FakeSender()
    assert len(notify.send_live(None, sender, "me@example.com", 1, tmp_path / "l.csv", NOW)) == 1  # type: ignore[arg-type]
    assert notify.send_live(None, sender, "me@example.com", 0, tmp_path / "l.csv", NOW) == []  # type: ignore[arg-type]
    assert len(sender.calls) == 1


@pytest.mark.parametrize("override", ["", "   "])
def test_send_live_refuses_without_override(fake_db: FakeDb, tmp_path: Path, override: str) -> None:
    sender = FakeSender()
    with pytest.raises(ValueError, match="NOTIFY_OVERRIDE_TO"):
        notify.send_live(None, sender, override, 3, tmp_path / "l.csv", NOW)  # type: ignore[arg-type]
    assert sender.calls == [] and fake_db.fetch_calls == []
    assert not (tmp_path / "l.csv").exists()


def test_send_live_survives_mark_failure(fake_db: FakeDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_a: object) -> None:
        raise RuntimeError("db locked")

    monkeypatch.setattr(db, "mark_notified", broken)
    results = notify.send_live(None, FakeSender(), "me@example.com", 1, tmp_path / "l.csv", NOW)  # type: ignore[arg-type]
    assert results[0].status == "sent" and "db locked" in (results[0].error or "")


# ---------------------------------------------------------------- record-send

def test_record_send_marks_and_logs(fake_db: FakeDb, tmp_path: Path) -> None:
    log_path = tmp_path / "send_log.csv"
    notify.record_send(None, "<2.JavaMail.evans@thyme>", "sent", None, "me@example.com", log_path, NOW)  # type: ignore[arg-type]
    assert fake_db.sent == {"<2.JavaMail.evans@thyme>": "2026-11-18T12:00:00+00:00"}
    notify.record_send(None, "<4.JavaMail.evans@thyme>", "error", "timeout", "me@example.com", log_path, NOW)  # type: ignore[arg-type]
    assert "<4.JavaMail.evans@thyme>" not in fake_db.sent
    rows = _log_rows(log_path)
    assert [(r["message_id"], r["status"], r["error"]) for r in rows] == [
        ("<2.JavaMail.evans@thyme>", "sent", ""), ("<4.JavaMail.evans@thyme>", "error", "timeout")]
    assert rows[0]["intended_recipient"] == "user2@enron.com"


def test_record_send_already_sent_not_remarked(fake_db: FakeDb, tmp_path: Path) -> None:
    fake_db.sent["<2.JavaMail.evans@thyme>"] = "earlier"
    notify.record_send(None, "<2.JavaMail.evans@thyme>", "sent", None, "me@example.com", tmp_path / "l.csv", NOW)  # type: ignore[arg-type]
    assert fake_db.sent["<2.JavaMail.evans@thyme>"] == "earlier"
    assert "already notified" in _log_rows(tmp_path / "l.csv")[0]["error"]


def test_record_send_rejects_unknown_and_missing_override(fake_db: FakeDb, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown"):
        notify.record_send(None, "<nope>", "sent", None, "me@example.com", tmp_path / "l.csv", NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="NOTIFY_OVERRIDE_TO"):
        notify.record_send(None, "<2.JavaMail.evans@thyme>", "sent", None, "", tmp_path / "l.csv", NOW)  # type: ignore[arg-type]
