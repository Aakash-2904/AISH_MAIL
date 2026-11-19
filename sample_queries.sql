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
-- Actual output (full run, 2026-11-19; long values truncated to 60 chars):
--   from_address | email_count
--   vince.kaminski@enron.com | 14303
--   sally.beck@enron.com | 4234
--   daren.farmer@enron.com | 2812
--   j.kaminski@enron.com | 1231
--   mjones7@txu.com | 1063
--   shirley.crenshaw@enron.com | 920
--   rosalee.fleming@enron.com | 868
--   sherri.sera@enron.com | 694
--   enron.announcements@enron.com | 632
--   ami.chokshi@enron.com | 547


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
-- Actual output (full run, 2026-11-19; long values truncated to 60 chars):
--   emails_in_range
--   3566
--
--   date_utc | from_address | subject
--   2001-12-31T23:08:03+00:00 | beth.apollo@enron.com | Re: FW: information for bert stromquist
--   2001-12-31T22:07:00+00:00 | e27062@ok.ru | Animals In Action!!!!                         27619
--   2001-12-31T21:47:13+00:00 | industrial_engr@yahoo.com | Happy New Year
--   2001-12-31T21:37:25+00:00 | sally.beck@enron.com | 2002 Budget for Enron Net Works
--   2001-12-31T21:19:06+00:00 | lawndale64@hotmail.com | We Run Background and Asset Searches
--   2001-12-31T21:11:59+00:00 | rnau@mail.duke.edu | Recommendation for Chonawee Supatgiat?
--   2001-12-31T21:11:59+00:00 | rnau@mail.duke.edu | Recommendation for Chonawee Supatgiat?
--   2001-12-31T21:05:39+00:00 | ipayit@enron.com | Action Requested:  Invoice Requires Coding/Issue Resolution/
--   2001-12-31T20:32:06+00:00 | c..williams@enron.com | RE: E-mail Retention
--   2001-12-31T20:21:50+00:00 | c..williams@enron.com | RE: E-mail Retention


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
-- Actual output (full run, 2026-11-19; long values truncated to 60 chars):
--   emails_with_cc
--   20614
--
--   message_id | subject | cc_count
--   <2953721.1075856650766.JavaMail.evans@thyme> | Quantitative Finance Update from FinMath.com @ Chicago | 447
--   <32044581.1075856889410.JavaMail.evans@thyme> | Quantitative Finance Update from FinMath.com @ Chicago | 447
--   <9374309.1075857054368.JavaMail.evans@thyme> | Quantitative Finance Update from FinMath.com @ Chicago | 447
--   <12620326.1075852668211.JavaMail.evans@thyme> | Program Date Change | 171
--   <25284684.1075856270890.JavaMail.evans@thyme> | Message from Anjam S Ahmad | 108
--   <8227879.1075856579026.JavaMail.evans@thyme> | Message from Anjam S Ahmad | 108
--   <21269711.1075856362968.JavaMail.evans@thyme> | Message from Anjam S Ahmad | 108
--   <22225939.1075845190510.JavaMail.evans@thyme> | Analyst & Associate Program - Summer Interns | 102
--   <23710114.1075840150053.JavaMail.evans@thyme> | YPO 49'ers | 86
--   <18735506.1075845488681.JavaMail.evans@thyme> | Potential YPO Member | 84


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
-- Actual output (full run, 2026-11-19; long values truncated to 60 chars):
--   message_id | recipient_type | position | email | is_smtp
--   <2953721.1075856650766.JavaMail.evans@thyme> | to | 0 | alex@finmath.com | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 0 | zeljko.kecman@us.arthurandersen.com | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 1 | ykung@rfc.com | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 2 | yuanjian@loki.stuart.iit.edu | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 3 | yash@loki.stuart.iit.edu | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 4 | yaping@loki.stuart.iit.edu | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 5 | xxu05@sears.com | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 6 | wtmuth@mcs.net | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 7 | falloon@netnitco.net | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 8 | wbonewe@rfc.com | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 9 | wei-chen@uiowa.edu | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 10 | walter.french@usbank.com | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 11 | walid@loki.stuart.iit.edu | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 12 | pvfafat@gsbphd.uchicago.edu | 1
--   <2953721.1075856650766.JavaMail.evans@thyme> | cc | 13 | vasilios.koures@wdr.com | 1
--   ... (880 more rows omitted)


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
-- Actual output (full run, 2026-11-19; long values truncated to 60 chars):
--   dup_message_id | dup_date_utc | from_address | subject | orig_message_id | orig_date_utc | similarity_score | is_primary_target | group_size
--   <18188947.1075860811513.JavaMail.evans@thyme> | 2002-01-30T19:45:03+00:00 | randymonk@hotmail.com | Demand Ken Lay Donate Proceeds from Enron Stock Sales | <15933150.1075860811678.JavaMail.evans@thyme> | 2002-01-30T19:45:01+00:00 | 99.0 | 1 | 2
--   <19076571.1075860812689.JavaMail.evans@thyme> | 2002-01-30T19:42:01+00:00 | lyow@indiana.edu | Demand Ken Lay Donate Proceeds from Enron Stock Sales | <13686879.1075860810869.JavaMail.evans@thyme> | 2002-01-30T19:39:07+00:00 | 99.53 | 1 | 2
--   <5697724.1075860813887.JavaMail.evans@thyme> | 2002-01-30T19:33:08+00:00 | aprilhoover@msn.com | Demand Ken Lay Donate Proceeds from Enron Stock Sales | <4533680.1075860813617.JavaMail.evans@thyme> | 2002-01-30T19:33:07+00:00 | 98.43 | 1 | 3
--   <3372832.1075860813752.JavaMail.evans@thyme> | 2002-01-30T19:33:07+00:00 | aprilhoover@msn.com | Demand Ken Lay Donate Proceeds from Enron Stock Sales | <4533680.1075860813617.JavaMail.evans@thyme> | 2002-01-30T19:33:07+00:00 | 99.45 | 0 | 3
--   <20407272.1075860829200.JavaMail.evans@thyme> | 2002-01-30T17:51:18+00:00 | doctorbonner@altavista.com | Demand Ken Lay Donate Proceeds from Enron Stock Sales | <3078898.1075860829042.JavaMail.evans@thyme> | 2002-01-30T17:51:16+00:00 | 99.77 | 1 | 2
--   <10130677.1075860822727.JavaMail.evans@thyme> | 2002-01-30T17:45:11+00:00 | erun@webaccess.net | Demand Ken Lay Donate Proceeds from Enron Stock Sales | <30648609.1075860822003.JavaMail.evans@thyme> | 2002-01-30T17:45:07+00:00 | 99.21 | 1 | 2
--   <20592783.1075860821911.JavaMail.evans@thyme> | 2002-01-30T17:45:03+00:00 | skinnerj@central.edu | Demand Ken Lay Donate Proceeds from Enron Stock Sales | <5623066.1075860821821.JavaMail.evans@thyme> | 2002-01-30T17:45:01+00:00 | 99.65 | 1 | 2
--   <2429258.1075840782650.JavaMail.evans@thyme> | 2002-01-30T06:07:46+00:00 | ipayit@enron.com | Action Requested:  Past Due Invoice for User: WINCENTY KAMIN | <24998607.1075840750215.JavaMail.evans@thyme> | 2002-01-28T06:07:34+00:00 | 100.0 | 1 | 2
--   <2711562.1075860815540.JavaMail.evans@thyme> | 2002-01-29T23:48:00+00:00 | gladdy@earthlink.net | Demand Ken Lay Donate Proceeds from Enron Stock Sales | <6304687.1075860815562.JavaMail.evans@thyme> | 2002-01-29T23:39:01+00:00 | 99.33 | 1 | 2
--   <10739452.1075840788561.JavaMail.evans@thyme> | 2002-01-29T18:35:13+00:00 | j.kaminski@enron.com | FW: Retention of Documents | <22518744.1075840789378.JavaMail.evans@thyme> | 2002-01-24T04:56:05+00:00 | 98.02 | 1 | 2
--   <31826988.1075840750170.JavaMail.evans@thyme> | 2002-01-28T06:55:45+00:00 | info@pmaconference.com | Merchant Power Monthly   Free Sample | <8713844.1075862450880.JavaMail.evans@thyme> | 2001-11-21T06:24:57+00:00 | 95.27 | 1 | 3
--   <14040275.1075840750911.JavaMail.evans@thyme> | 2002-01-26T00:11:40+00:00 | subs@btu.net | Btu's Weekly Power Report | <32795618.1075840767502.JavaMail.evans@thyme> | 2002-01-11T23:58:48+00:00 | 98.36 | 1 | 3
--   <20137786.1075840750934.JavaMail.evans@thyme> | 2002-01-26T00:06:33+00:00 | subs@btu.net | Btu Weekly | <13852199.1075840767587.JavaMail.evans@thyme> | 2002-01-11T23:46:05+00:00 | 99.14 | 1 | 2
--   <27178486.1075840783092.JavaMail.evans@thyme> | 2002-01-25T22:07:42+00:00 | jr..legal@enron.com | Subpoena Response Team | <2522300.1075840348687.JavaMail.evans@thyme> | 2002-01-25T22:00:56+00:00 | 100.0 | 1 | 2
--   <19637583.1075840783143.JavaMail.evans@thyme> | 2002-01-25T19:06:41+00:00 | erin@fea.com | FEA Announces the Release of @GLOBAL 5.0.3 | <24701265.1075840751396.JavaMail.evans@thyme> | 2002-01-25T19:06:39+00:00 | 100.0 | 1 | 2
--   <32551238.1075840783190.JavaMail.evans@thyme> | 2002-01-25T02:53:53+00:00 | info@pmaconference.com | Electric and Gas Industry Maps- Special! | <25993699.1075840764754.JavaMail.evans@thyme> | 2002-01-15T03:59:39+00:00 | 100.0 | 1 | 2
--   <393602.1075840753050.JavaMail.evans@thyme> | 2002-01-24T16:06:11+00:00 | ipayit@enron.com | Action Requested:  Invoice Requires Coding/Issue Resolution/ | <6915692.1075855468513.JavaMail.evans@thyme> | 2001-12-31T21:05:39+00:00 | 100.0 | 1 | 2
--   <24784555.1075840783584.JavaMail.evans@thyme> | 2002-01-24T03:26:05+00:00 | jr..legal@enron.com | Cooperation with the FBI | <2101733.1075840354994.JavaMail.evans@thyme> | 2002-01-24T03:26:05+00:00 | 100.0 | 1 | 2
--   <21901614.1075840753930.JavaMail.evans@thyme> | 2002-01-24T02:10:47+00:00 | chairman.enron@enron.com | Message from Ken Lay | <25054359.1075840348910.JavaMail.evans@thyme> | 2002-01-24T02:10:47+00:00 | 100.0 | 1 | 2
--   <6405193.1075840754245.JavaMail.evans@thyme> | 2002-01-23T20:51:34+00:00 | announcements.enron@enron.com | IMPORTANT MESSAGE TO ALL EMPLOYEES PARTICIPATING IN THE ENRO | <13059400.1075840349107.JavaMail.evans@thyme> | 2002-01-23T20:51:34+00:00 | 100.0 | 1 | 2


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
-- Actual output (full run, 2026-11-19; long values truncated to 60 chars):
--   total_emails | pct_cc | pct_bcc | pct_x_from | pct_x_to | pct_x_cc | pct_x_bcc | pct_x_folder | pct_x_origin | pct_content_type | pct_has_attachment | pct_forwarded | pct_quoted | pct_headings
--   61556 | 33.49 | 33.49 | 100.0 | 100.0 | 33.64 | 0.06 | 100.0 | 100.0 | 100.0 | 12.59 | 39.18 | 1.8 | 7.02

