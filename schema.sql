-- Enron email pipeline schema (SQLite).
-- from_address stays on `emails` (single-valued, indexed); to/cc/bcc are
-- normalised into `addresses` + `email_recipients`.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS emails (
    id                 INTEGER PRIMARY KEY,
    message_id         TEXT    NOT NULL UNIQUE,
    date_utc           TEXT    NOT NULL,              -- ISO-8601 UTC
    from_address       TEXT    NOT NULL,
    subject            TEXT    NOT NULL,
    normalized_subject TEXT,
    body               TEXT    NOT NULL,
    source_file        TEXT    NOT NULL UNIQUE,
    mailbox            TEXT,
    x_from             TEXT,
    x_to               TEXT,
    x_cc               TEXT,
    x_bcc              TEXT,
    x_folder           TEXT,
    x_origin           TEXT,
    content_type       TEXT,
    has_attachment     INTEGER NOT NULL DEFAULT 0 CHECK (has_attachment IN (0, 1)),
    forwarded_content  TEXT,
    quoted_content     TEXT,
    headings           TEXT,
    is_duplicate       INTEGER NOT NULL DEFAULT 0 CHECK (is_duplicate IN (0, 1)),
    duplicate_of       TEXT REFERENCES emails(message_id),
    similarity_score   REAL,
    is_primary_target  INTEGER NOT NULL DEFAULT 0 CHECK (is_primary_target IN (0, 1)),
    notification_sent  INTEGER NOT NULL DEFAULT 0 CHECK (notification_sent IN (0, 1)),
    notification_date  TEXT,
    ingested_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS addresses (
    id      INTEGER PRIMARY KEY,
    email   TEXT    NOT NULL UNIQUE,
    is_smtp INTEGER NOT NULL DEFAULT 1 CHECK (is_smtp IN (0, 1))
);

CREATE TABLE IF NOT EXISTS email_recipients (
    email_id       INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    address_id     INTEGER NOT NULL REFERENCES addresses(id),
    recipient_type TEXT    NOT NULL CHECK (recipient_type IN ('to', 'cc', 'bcc')),
    position       INTEGER NOT NULL,
    PRIMARY KEY (email_id, recipient_type, address_id)
);

CREATE TABLE IF NOT EXISTS parse_failures (
    id          INTEGER PRIMARY KEY,
    source_file TEXT NOT NULL,
    stage       TEXT NOT NULL CHECK (stage IN ('read', 'parse', 'validate', 'insert')),
    reason      TEXT NOT NULL,
    logged_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS duplicate_groups (
    group_id            INTEGER PRIMARY KEY,
    original_message_id TEXT    NOT NULL UNIQUE REFERENCES emails(message_id),
    size                INTEGER NOT NULL CHECK (size >= 2)
);

CREATE INDEX IF NOT EXISTS idx_emails_date         ON emails (date_utc);
CREATE INDEX IF NOT EXISTS idx_emails_from         ON emails (from_address);
CREATE INDEX IF NOT EXISTS idx_emails_subject      ON emails (subject);
CREATE INDEX IF NOT EXISTS idx_emails_norm_subject ON emails (normalized_subject);
CREATE INDEX IF NOT EXISTS idx_emails_is_dup       ON emails (is_duplicate);
CREATE INDEX IF NOT EXISTS idx_emails_dup_of       ON emails (duplicate_of);
CREATE INDEX IF NOT EXISTS idx_recip_address       ON email_recipients (address_id);
