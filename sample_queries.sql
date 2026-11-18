-- sample_queries.sql: sample queries against data/enron.db (schema.sql).
-- Run with:  sqlite3 data/enron.db < sample_queries.sql
-- (or via Python: conn.executescript / execute each statement).

-- Q1. Emails per sender, top 10.
-- Groups emails by from_address (indexed column on `emails`) and returns the 10
-- busiest senders.
-- Expected shape: up to 10 rows, columns (from_address TEXT, email_count INTEGER),
-- ordered by email_count descending.
SELECT from_address, COUNT(*) AS email_count
FROM emails
GROUP BY from_address
ORDER BY email_count DESC, from_address
LIMIT 10;
-- Actual output (filled after full run):

-- Q2. Emails in a date range (2001-10-01 to 2001-12-31 UTC, inclusive).
-- date_utc is ISO-8601 UTC text, so lexical comparison = chronological order;
-- the upper bound '2002-01-01' (exclusive) covers the whole of Dec 31.
-- Expected shape: one row, column (emails_in_range INTEGER), followed by the
-- 10 latest emails in the range (date_utc, from_address, subject).
SELECT COUNT(*) AS emails_in_range
FROM emails
WHERE date_utc >= '2001-10-01' AND date_utc < '2002-01-01';

SELECT date_utc, from_address, subject
FROM emails
WHERE date_utc >= '2001-10-01' AND date_utc < '2002-01-01'
ORDER BY date_utc DESC
LIMIT 10;
-- Actual output (filled after full run):

-- Q3. Emails that have CC recipients (join via email_recipients).
-- Counts emails with at least one 'cc' row and lists 10 of them with their CC count.
-- Expected shape: one row (emails_with_cc INTEGER), then up to 10 rows
-- (message_id TEXT, subject TEXT, cc_count INTEGER) ordered by cc_count desc.
SELECT COUNT(DISTINCT email_id) AS emails_with_cc
FROM email_recipients
WHERE recipient_type = 'cc';

SELECT e.message_id, e.subject, COUNT(*) AS cc_count
FROM emails e
JOIN email_recipients r ON r.email_id = e.id AND r.recipient_type = 'cc'
GROUP BY e.id
ORDER BY cc_count DESC, e.id
LIMIT 10;
-- Actual output (filled after full run):

-- Q4. All recipients of a given message (shows normalisation working).
-- Picks the message with the most recipients (deterministic: ties broken by id)
-- and lists every recipient address via email_recipients -> addresses.
-- To inspect a specific email, replace the CTE with
--   SELECT id FROM emails WHERE message_id = '<...>'
-- Expected shape: one row per recipient, columns (message_id TEXT,
-- recipient_type 'to'|'cc'|'bcc', position INTEGER (0-based), email TEXT,
-- is_smtp 0|1), ordered by type then position.
WITH target AS (
    SELECT email_id AS id
    FROM email_recipients
    GROUP BY email_id
    ORDER BY COUNT(*) DESC, email_id
    LIMIT 1
)
SELECT e.message_id, r.recipient_type, r.position, a.email, a.is_smtp
FROM target t
JOIN emails e ON e.id = t.id
JOIN email_recipients r ON r.email_id = e.id
JOIN addresses a ON a.id = r.address_id
ORDER BY CASE r.recipient_type WHEN 'to' THEN 0 WHEN 'cc' THEN 1 ELSE 2 END, r.position;
-- Actual output (filled after full run):

-- Q5. Duplicate overview: flagged duplicates joined to their originals.
-- Every email with is_duplicate = 1, its original (duplicate_of -> message_id),
-- score, primary-target flag and the group size from duplicate_groups.
-- Expected shape: up to 20 rows, columns (dup_message_id, dup_date_utc,
-- from_address, subject, orig_message_id, orig_date_utc, similarity_score REAL
-- 0-100, is_primary_target 0|1, group_size INTEGER >= 2), latest duplicate first.
SELECT d.message_id      AS dup_message_id,
       d.date_utc        AS dup_date_utc,
       d.from_address,
       d.subject,
       o.message_id      AS orig_message_id,
       o.date_utc        AS orig_date_utc,
       d.similarity_score,
       d.is_primary_target,
       g.size            AS group_size
FROM emails d
JOIN emails o ON o.message_id = d.duplicate_of
LEFT JOIN duplicate_groups g ON g.original_message_id = o.message_id
WHERE d.is_duplicate = 1
ORDER BY d.date_utc DESC, d.id
LIMIT 20;
-- Actual output (filled after full run):

-- Q6. Field completeness check for optional fields.
-- Percentage of emails where each optional field is populated (non-NULL and
-- non-empty); cc/bcc completeness comes from email_recipients.
-- Expected shape: one row, columns total_emails INTEGER plus one REAL percentage
-- (0-100, 2 dp) per optional field.
SELECT COUNT(*) AS total_emails,
       ROUND(100.0 * SUM(EXISTS (SELECT 1 FROM email_recipients r
                                 WHERE r.email_id = e.id AND r.recipient_type = 'cc'))
             / MAX(COUNT(*), 1), 2) AS pct_cc,
       ROUND(100.0 * SUM(EXISTS (SELECT 1 FROM email_recipients r
                                 WHERE r.email_id = e.id AND r.recipient_type = 'bcc'))
             / MAX(COUNT(*), 1), 2) AS pct_bcc,
       ROUND(100.0 * SUM(COALESCE(e.x_from, '') <> '') / MAX(COUNT(*), 1), 2) AS pct_x_from,
       ROUND(100.0 * SUM(COALESCE(e.x_to, '') <> '') / MAX(COUNT(*), 1), 2) AS pct_x_to,
       ROUND(100.0 * SUM(COALESCE(e.x_cc, '') <> '') / MAX(COUNT(*), 1), 2) AS pct_x_cc,
       ROUND(100.0 * SUM(COALESCE(e.x_bcc, '') <> '') / MAX(COUNT(*), 1), 2) AS pct_x_bcc,
       ROUND(100.0 * SUM(COALESCE(e.x_folder, '') <> '') / MAX(COUNT(*), 1), 2) AS pct_x_folder,
       ROUND(100.0 * SUM(COALESCE(e.x_origin, '') <> '') / MAX(COUNT(*), 1), 2) AS pct_x_origin,
       ROUND(100.0 * SUM(COALESCE(e.content_type, '') <> '') / MAX(COUNT(*), 1), 2) AS pct_content_type,
       ROUND(100.0 * SUM(e.has_attachment) / MAX(COUNT(*), 1), 2) AS pct_has_attachment,
       ROUND(100.0 * SUM(COALESCE(e.forwarded_content, '') <> '') / MAX(COUNT(*), 1), 2) AS pct_forwarded,
       ROUND(100.0 * SUM(COALESCE(e.quoted_content, '') <> '') / MAX(COUNT(*), 1), 2) AS pct_quoted,
       ROUND(100.0 * SUM(COALESCE(e.headings, '') <> '') / MAX(COUNT(*), 1), 2) AS pct_headings
FROM emails e;
-- Actual output (filled after full run):
