"""Tests for body extraction / splitting / headings / attachments."""
import email
import email.policy

from enron_pipeline.body import detect_attachment, extract_headings, extract_text, split_body


def msg_from(raw: bytes):
    return email.message_from_bytes(raw, policy=email.policy.compat32)


def test_forward_marker_splits_and_nested_forward_stays_together() -> None:
    text = (
        "FYI\n\n"
        "---------------------- Forwarded by Sally Beck/HOU/ECT on 01/07/2002 ---------\n"
        "inner text\n"
        " -----Original Message-----\n"
        "From: x\nSent: y\n\ndeep\n"
    )
    parts = split_body(text)
    assert parts.body == "FYI"
    assert parts.forwarded_content.startswith("---------------------- Forwarded by")
    assert "-----Original Message-----" in parts.forwarded_content
    assert parts.forwarded_content.rstrip().endswith("deep")


def test_original_message_marker() -> None:
    parts = split_body("ok\n-----Original Message-----\nFrom: a\nold")
    assert parts.body == "ok"
    assert "old" in parts.forwarded_content


def test_outlook_header_block_without_marker() -> None:
    parts = split_body("Reply here.\n\nFrom: Jones, Tana\nSent: Monday\nTo: Mark\n\nold text")
    assert parts.body == "Reply here."
    assert parts.forwarded_content.startswith("From: Jones, Tana")


def test_begin_forwarded_message() -> None:
    parts = split_body("see below\nBegin forwarded message:\n\nstuff")
    assert parts.body == "see below"
    assert parts.forwarded_content.startswith("Begin forwarded message:")


def test_quoted_reply_with_wrote_leadin() -> None:
    text = "Sounds good.\n\nOn Mon, 7 Jan 2002, Sally wrote:\n> Can we?\n  > Yes.\n\nVince"
    parts = split_body(text)
    assert parts.body == "Sounds good.\n\n\nVince"
    assert parts.quoted_content == "On Mon, 7 Jan 2002, Sally wrote:\n> Can we?\n  > Yes."
    assert parts.forwarded_content is None


def test_only_forwarded_gives_empty_body() -> None:
    parts = split_body("-----Original Message-----\nFrom: a\nhi")
    assert parts.body == ""
    assert parts.forwarded_content is not None


def test_headings_heuristic() -> None:
    text = (
        "QUARTERLY RESULTS\n"
        "revenue up\n"
        "Overview\n"
        "========\n"
        "1. Trading Update\n"
        "2. this is not title case\n"
        "Action items:\n"
        "do it\n"
        "To: someone\n"
        "Dangling colon:\n"
        "\n"
        "OK\n"
        "$1,000,000 USD\n"
        "SUPERSTARS!\n"
    )
    assert extract_headings(text).split("\n") == [
        "QUARTERLY RESULTS",
        "Overview",
        "1. Trading Update",
        "Action items:",
    ]


def test_headings_false_positives_from_real_sample() -> None:
    text = (
        "Daren:\n"  # salutation on the first line
        "Please see below.\n"
        "FYI\n"
        "[IMAGE]\n"
        "AL\n"
        "DATE      NOM       FLOW - MCF\n"  # table row
        "EES NA\t$7MM\t$7MM\n"
        "the project will deliver the following:\n"  # lowercase fragment
        "To change your e-mail address, go to:\n"
        "x\n"
        "ENRON WHOLESALE SERVICES\n"
        "Agenda:\n"
        "item\n"
    )
    assert extract_headings(text).split("\n") == ["ENRON WHOLESALE SERVICES", "Agenda:"]


def test_headings_none_when_absent() -> None:
    assert extract_headings("just a plain sentence.\nanother one.") is None
    assert extract_headings("") is None


def test_headings_skip_long_lines() -> None:
    long_caps = "THIS IS A VERY LONG ALL CAPS LINE WITH WAY MORE THAN TEN WORDS IN IT OK"
    assert extract_headings(long_caps) is None


def test_attachment_body_references() -> None:
    assert detect_attachment(None, "see <<budget.xls>> thanks")
    assert detect_attachment(None, "<< File: Q3 plan.doc >>")
    assert detect_attachment(None, "files:\n - deal_summary.doc\n")
    assert detect_attachment(None, "I have attached the latest model.xls for you.")
    assert not detect_attachment(None, "the file model.xls is on the share")
    assert not detect_attachment(None, "plain text, nothing here")


def test_attachment_mime_part_and_text_extraction() -> None:
    raw = (
        b'Content-Type: multipart/mixed; boundary="B"\n\n'
        b"--B\nContent-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: quoted-printable\n\n"
        b"r=C3=A9sum=C3=A9\n"
        b'--B\nContent-Type: application/pdf; name="a.pdf"\nContent-Disposition: attachment; filename="a.pdf"\n'
        b"Content-Transfer-Encoding: base64\n\nJVBERi0xLjQK\n--B--\n"
    )
    msg = msg_from(raw)
    assert extract_text(msg).strip() == "résumé"
    assert detect_attachment(msg, extract_text(msg))


def test_charset_fallbacks() -> None:
    msg = msg_from(b"Content-Type: text/plain; charset=bogus\n\n\x93hi\x94")
    assert extract_text(msg) == "“hi”"  # cp1252 smart quotes
    msg = msg_from(b"Content-Type: text/plain; charset=us-ascii\n\n\x81x")  # invalid cp1252
    assert extract_text(msg) == "\x81x"  # latin-1 last resort


def test_html_fallback() -> None:
    msg = msg_from(b"Content-Type: text/html\n\n<p>Hello&nbsp;<b>team</b></p><p>Bye</p>")
    assert extract_text(msg).split() == ["Hello", "team", "Bye"]


def test_crlf_normalised() -> None:
    msg = msg_from(b"Subject: x\r\n\r\nline1\r\nline2\r\n")
    assert extract_text(msg) == "line1\nline2\n"


def test_attachment_names() -> None:
    from enron_pipeline.body import attachment_names

    text = "see <<Budget Q3.xls>> and << File: notes.DOC >>\n - wg091100.pdf\nno file here.pdf mention"
    assert attachment_names(text) == frozenset({"budget q3.xls", "notes.doc", "wg091100.pdf"})
    assert attachment_names("") == frozenset() and attachment_names(None) == frozenset()
