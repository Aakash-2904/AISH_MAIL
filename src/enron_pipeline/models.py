"""Shared data contracts for the pipeline.

Every module codes against these types. Subagents must not change them; a needed
change is requested from the lead. All types are picklable (used across
ProcessPoolExecutor workers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

FailureStage = Literal["read", "parse", "validate", "insert"]
SendStatus = Literal["sent", "pending_approval", "approval_timeout", "error", "dry_run"]


@dataclass(frozen=True, slots=True)
class ParsedEmail:
    """One successfully parsed email (assignment Section 3 fields + extras).

    Dates are ISO-8601 UTC strings: ``YYYY-MM-DDTHH:MM:SS+00:00``.
    Address fields hold bare lowercased addresses (no display names).
    """

    # Mandatory
    message_id: str  # stripped, angle brackets kept: "<123.JavaMail.evans@thyme>"
    date_utc: str
    from_address: str
    to_addresses: tuple[str, ...]  # non-empty, de-duplicated, order preserved
    subject: str  # decoded, Re:/Fwd: preserved; "" allowed if header present but empty
    body: str  # primary body only; "" allowed (see stats)
    source_file: str  # POSIX path relative to dataset root: "maildir/lay-k/inbox/45"
    # Extras
    mailbox: str  # "lay-k"
    normalized_subject: str  # normalize.normalize_subject(subject)
    # Optional
    cc_addresses: tuple[str, ...] = ()
    bcc_addresses: tuple[str, ...] = ()
    x_from: str | None = None
    x_to: str | None = None
    x_cc: str | None = None
    x_bcc: str | None = None
    x_folder: str | None = None
    x_origin: str | None = None
    content_type: str | None = None
    has_attachment: bool = False
    forwarded_content: str | None = None
    quoted_content: str | None = None
    headings: str | None = None
    # Diagnostics (not stored as columns)
    date_tz_assumed: bool = False  # naive date, -0800 assumed
    from_is_smtp: bool = True  # False for Exchange-style /O=ENRON/OU=... values


@dataclass(frozen=True, slots=True)
class ParseFailure:
    """A file that could not become a ParsedEmail. ``reason`` is a short slug
    (e.g. ``missing_message_id``, ``missing_to``, ``bad_date``) optionally
    followed by ``: detail``; stats count failures by the slug before ':'."""

    source_file: str
    stage: FailureStage
    reason: str


@dataclass(frozen=True, slots=True)
class InsertStats:
    """Result of one db.insert_batch call."""

    inserted: int = 0
    exact_duplicates_skipped: int = 0  # same Message-ID already present
    exact_duplicate_ids: tuple[str, ...] = ()
    failures: tuple[ParseFailure, ...] = ()  # stage="insert"


@dataclass(frozen=True, slots=True)
class BlockMember:
    """One email inside a dedup block (same from_address + normalized_subject)."""

    id: int
    message_id: str
    date_utc: str
    body_norm: str  # normalize.normalize_body(body); never empty here
    attachments: frozenset[str] = frozenset()  # body.attachment_names() of the compared text


@dataclass(frozen=True, slots=True)
class DuplicateFlag:
    """One flagged (non-original) email of a duplicate cluster."""

    duplicate_message_id: str
    original_message_id: str
    similarity_score: float  # 0-100, vs. the cluster original
    is_primary_target: bool  # True only for the latest member of the cluster


@dataclass(slots=True)
class DedupResult:
    flags: list[DuplicateFlag] = field(default_factory=list)
    groups_found: int = 0
    emails_flagged: int = 0
    avg_group_size: float = 0.0
    max_group_size: int = 0
    excluded_empty_body: int = 0
    blocks_compared: int = 0
    runtime_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class NoticeInput:
    """Everything needed to render one duplicate notice (decoupled from SQL)."""

    dup_message_id: str
    dup_date_utc: str
    dup_subject: str
    dup_from: str  # intended recipient (Enron sender)
    orig_message_id: str
    orig_date_utc: str
    orig_subject: str
    similarity_score: float
    is_primary_target: bool


@dataclass(frozen=True, slots=True)
class SendResult:
    status: SendStatus
    error: str | None = None
    detail: str | None = None  # e.g. gmail message id, authorization_url


class Sender(Protocol):
    """Anything that can deliver a notice (McpGmailClient, or a fake in tests)."""

    def send(self, to: str, subject: str, body: str, headers: dict[str, str]) -> SendResult: ...
