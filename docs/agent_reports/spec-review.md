# spec-review: audit against "R2 - Take Home Assignment.docx"

> **Note:** this is a mid-build snapshot (repo at `799bdb3`, 191 tests). Every FAIL and AT RISK item below was later fixed or documented as a deliberate choice:
> - Notices are now plain 7bit/8bit text with no quoted-printable or base64.
> - `duplicates_report.csv` has exactly the 7 required columns.
> - `output/send_log.csv` holds 3 live sends.
> - The README and AI_USAGE explain that `send_gmail_email` is the `send_email` tool.
> - The redirected and capped live sends are explained in the README and AI_USAGE.
>
> The final state is in [README.md](../../README.md) (Results) and [AI_USAGE.md](../../AI_USAGE.md). The final suite is 214 tests passing.

Reviewer: `spec-reviewer` (read-only; this file is the only one written).
Inputs: the assignment text (extracted from `word/document.xml`), `plan.md`, and the repo at HEAD `799bdb3` plus the untracked README.md, AI_USAGE.md and outputs.
Outputs inspected: the files from the 19:46 run (`extraction_stats.json`, `duplicates_report.csv`, `error_log.txt`, `output/replies/`). A newer full run is in progress, so re-check the post-run items in Section 4.
Test suite: `pytest -q` gave **191 passed, 0 failed** (about 15 s).
`sample_queries.sql`: `executescript` on an empty DB built from `schema.sql` runs without errors. I did not run it against the live DB because the run is writing to it.

Legend: PASS = meets the assignment. FAIL = does not meet it now. AT RISK = meets it only partly, depends on a choice or doc wording a grader may question, or cannot be verified until the run finishes.

---

## 1. Summary table

| # | Requirement (assignment section) | Status | Evidence |
|---|---|---|---|
| 1 | ≥5 mailboxes, ≥10k emails, selection documented (2) | PASS | `config.py:26` five mailboxes; stats: 63,403 files, 61,556 parsed; README "Mailbox selection" |
| 2 | Recursive traversal of the maildir (T1) | PASS | `discover.py:10-34` (`os.walk`, sorted) |
| 3 | `message_id` mandatory, unique (3.1) | PASS | `parser.py:74-76`; `schema.sql:8` `UNIQUE` |
| 4 | `date` converted to UTC; tz abbreviations and offsets (3.1) | PASS | `dates.py:13-20,59-84`. Naming: the DB column is `date_utc`, not `date` (see AR-9) |
| 5 | `from_address`: address only (3.1) | PASS | `addresses.py:parse_single_address`, `parser.py:84-86` |
| 6 | `to_addresses`: list, folded lines, mixed formats (3.1) | PASS | `addresses.py:parse_address_list`; missing To fails as `missing_to` (1,845 rows in error_log) |
| 7 | `subject`: keeps Re:/Fwd: (3.1) | PASS | `parser.py:90-92` stores the raw subject. Empty subject is accepted (AR-8) |
| 8 | `body` separated from forwarded/quoted text (3.1) | PASS / AT RISK | `body.py:132-148`. Empty bodies are accepted (10.75% of rows, AR-8) |
| 9 | `source_file` like `maildir/lay-k/inbox/45` (3.1) | PASS | `discover.py:53-66` (maps `45_` back to `45`) |
| 10 | Mandatory field missing → logged as a failed parse with the reason (3.1) | PASS | `parser.py:72-92`, `stats.py:192-199` |
| 11 | Optional fields cc/bcc/x_*/content_type/has_attachment/forwarded/quoted/headings (3.2) | PASS | `parser.py:105-117`; all 13 fields reported in `field_completeness` |
| 12 | Graceful edge cases, no crash on bad input (T1, 5) | PASS | `parser.py:123-152` (never raises); per-row SAVEPOINT in `db.py:138-150`; per-item try in `notify.py:147-154` |
| 13 | Separate error log with path and reason (T1) | PASS | `error_log.txt`: `<ts> \| <source_file> \| <stage> \| <reason>` |
| 14 | Extraction stats: found / parsed / failed / per-field completeness (T1) | PASS | `stats.py:127-145`; `extraction_stats.json` |
| 15 | SQLite + schema file (T2) | PASS | `schema.sql` |
| 16 | to/cc/bcc normalised into a related table (T2) | PASS | `schema.sql:36-48` (`addresses`, `email_recipients`) |
| 17 | UNIQUE `message_id` (T2) | PASS | `schema.sql:8`; `ON CONFLICT DO NOTHING` at `db.py:41-45` |
| 18 | Indexes on date, from_address, subject (T2) | PASS | `schema.sql:64-66` |
| 19 | schema has `is_duplicate`, `duplicate_of`, `notification_sent`, `notification_date` (7) | PASS | `schema.sql:27-32` |
| 20 | ≥3 sample queries with expected-output descriptions (T2, 7) | PASS / AT RISK | 6 queries, each with an "Expected shape" comment. The "Actual output (filled after full run):" lines are still empty (AR-7) |
| 21 | Dedup key: same from + normalised subject + body ≥90% fuzzy (T3) | PASS / AT RISK | `dedup.py:289-302,337-371`, `normalize.py`. Empty bodies are excluded (AR-3) |
| 22 | Latest = duplicate, earliest = original; groups >2 flag all but the earliest (T3) | PASS | `dedup.py:356-371` (anchor = earliest; `is_primary_target` = last) |
| 23 | `is_duplicate=1` and `duplicate_of=<original id>` written (T3) | PASS | `db.py:188-209` |
| 24 | `duplicates_report.csv` columns in the exact order (T3) | AT RISK | First 7 columns match exactly; an 8th `is_primary_target` column is appended (`dedup.py:276-285`) (AR-6) |
| 25 | Dedup stats: groups, flagged, average group size (T3) | PASS | `extraction_stats.json.dedup`; console via `print_summary` |
| 26 | Notice template matches character for character (4.2) | **FAIL** (1,455 of 32,248 files) | `notify.py:32-52` matches the template exactly (indentation checked with `repr`). But `notify.py:101` lets `set_content` choose QP encoding, so long lines get soft-wrapped in the .eml (FAIL-1) |
| 27 | Header lines To / Subject `[Duplicate Notice] Re: <orig>` / Date / References (4.2) | PASS | `notify.py:93-102` |
| 28 | Dry-run `.eml` for every flagged duplicate (7) | PASS | 32,248 files = 32,248 flagged (`notify.write_dry_run`) |
| 29 | Live send only with `--send-live` (5) | PASS | `main.py:72,449-455` |
| 30 | Live notice goes to the sender of the latest duplicate (4.2) | AT RISK (deliberate) | Sends are redirected to `NOTIFY_OVERRIDE_TO` with a banner line (`notify.py:176-178`); documented in README "Safety" (AR-5) |
| 31 | One notice per duplicate group, live (4.2) | AT RISK (deliberate) | Primary targets only, capped by `--max-send` (default 3) (AR-5) |
| 32 | `output/send_log.csv`: timestamp, recipient, subject, status, error (7) | PASS (code) / **FAIL (file missing)** | `notify.py:24,157-165`; no `output/send_log.csv` yet (FAIL-2) |
| 33 | MCP server with a `send_email` tool (5) | AT RISK | Tool is `send_gmail_email`; the docs never connect it to the assignment's "send_email" wording (AR-4) |
| 34 | `mcp_config.json.example` with placeholders; config committed with secrets redacted (4.1) | PASS / AT RISK | Example is fine. The server source and config are in a separate, uncommitted project (AR-2) |
| 35 | AI_USAGE.md MCP section: choice, setup, prompts, issues, live-send evidence (4.3) | **FAIL (pending)** | `AI_USAGE.md:184-195` still has `{{screenshot_path}}` / `{{send_log_excerpt}}`; `docs/screenshots/` is empty (FAIL-2) |
| 36 | AI_USAGE.md: tool+version, prompting with 3-5 prompts, ≥2 debugging cases, wrote-vs-AI %, lessons (6) | AT RISK | All sections exist; "What I wrote vs AI" is all `TODO` (meant for the author) (FAIL-3) |
| 37 | README: setup incl. MCP, how to run, architecture (7) | PASS | README sections + Mermaid diagram (line 13); Results still has `{{...}}` placeholders (Section 4) |
| 38 | Single command runs the full pipeline (5) | PASS / AT RISK | `make run` = `main.py --fresh`. Plain `python main.py` on a populated DB skews the stats (AR-1) |
| 39 | requirements.txt, no paid libraries (5) | PASS / minor | Pinned. `httpx2` is imported directly (`mcp_client.py:52`) but not listed; `httpx` is listed but unused (AR-10) |
| 40 | Python 3.10+ (5) | AT RISK | `mcp_client.py:88` uses `BaseExceptionGroup`, which only exists in 3.11+; README says "3.10+ required" (AR-11) |
| 41 | Fuzzy library recognised and documented (5) | PASS | `rapidfuzz.fuzz.ratio`; README "Duplicate detection" |
| 42 | Secrets hygiene | PASS / AT RISK | `git grep` finds no JWTs, Descope IDs or real Gmail addresses (only `me@gmail.com` in tests). `output/send_queue.jsonl` is not gitignored and holds the real override address (AR-12) |
| 43 | Deliverables list (7) | AT RISK | Everything is present except `output/send_log.csv` and the screenshot. Outputs, README and AI_USAGE are untracked: commit them |

---

## 2. FAIL items (fix before submission)

### FAIL-1: 1,455 dry-run .eml files are quoted-printable, so the raw text does not match the template
- Evidence: `grep -L "Content-Transfer-Encoding: 7bit" output/replies/*.eml` lists 1,455 files (none are base64). Example: `output/replies/10022337.1075854345936.JavaMail.evans_thyme.eml` has `Subject:     Same Day Change - Revision #1 - Enron Nom for February 22, 2=` followed by `001` on the next line.
- Cause: `notify.py:101` calls `msg.set_content(render_body(n), charset="utf-8")` without a `cte`. The stdlib switches to quoted-printable (or base64) when a line is longer than 78 characters or contains non-ASCII text, usually because of a long or 8-bit `Subject:` / `Message-ID:` line.
- Fix (`notify.py:101`), which keeps the golden test's `7bit` header unchanged:
  ```python
  body = render_body(n)
  needs_8bit = any(len(l) > 78 for l in body.splitlines()) or not body.isascii()
  msg.set_content(body, charset="utf-8", cte="8bit" if needs_8bit else None)
  ```
  Checked in the venv: `cte="8bit"` writes long and non-ASCII lines unwrapped with `BytesGenerator(policy=default)`. Add a test that uses a subject over 80 characters and a non-ASCII subject, and assert that the rendered .eml contains the `Subject:     <subject>` line unbroken.

### FAIL-2: Live-send evidence is missing (`output/send_log.csv`, screenshot, AI_USAGE excerpt)
- `output/` has only `replies/` and `send_queue.jsonl`. `docs/screenshots/` is empty. `AI_USAGE.md:184-195` still has `{{screenshot_path}}` and `{{send_log_excerpt}}`.
- The assignment (4.3, 7) requires at least one successful live send with a screenshot or log excerpt.
- Fix: Checkpoint 2. Run `python main.py --send-live --max-send 1`, or use `--via-claude-code` plus `--record-send`. Confirm a `status=sent` row, save the screenshot under `docs/screenshots/`, and fill in the placeholders.

### FAIL-3: Author-only sections of AI_USAGE.md are still TODO
- `AI_USAGE.md:40,91,97-111,126`. The assignment (6) requires the percentage breakdown and the manual vs AI sections. Plan 10.3 leaves these to the author, so this is for the user, not an agent. List it in the final checklist so it is not submitted as TODO.

---

## 3. AT RISK items (most severe first)

### AR-1: `python main.py` run twice on the same DB skews the stats, and `--fresh` erases notification state (medium)
- `main.py:322-325`: without `--fresh`, every row is re-inserted and counted as `exact_duplicates_skipped` (about 61k), `inserted` becomes 0, and `extraction_stats.json` is overwritten with those numbers. The assignment's example single command is `python main.py`.
- `make run` passes `--fresh`, and `db.reset_db` (`db.py:75-79`) deletes `notification_sent` / `notification_date`. After a live send, `make run` followed by `make run-live` would send the same notices again. The README "Safety" section says re-running never resends; that holds only until the next `--fresh`.
- Fix, either of:
  - (a) In `send_live` and `queue_items`, also skip message_ids that already have `status=sent` in `output/send_log.csv`.
  - (b) After `--fresh` + dedup, re-apply `notification_sent=1` / `notification_date` from the `sent` rows in `send_log.csv`.

  Separately, when the DB already has emails and `--fresh` is not given, either default to `--skip-extract`, or keep the warning and do not overwrite the extraction section of `extraction_stats.json`.

### AR-2: The MCP server's own configuration and source are not in the repo (medium)
- Assignment 4.1: "Include your MCP server configuration in the project (with credentials redacted)." Assignment 5 allows building a custom server.
- The server is a custom Next.js app (`../descope-gmail-mcp`), and README line 100 says it "is not part of this repo". Only the client-side `mcp_config.json.example` is committed.
- Fix: add the server's redacted config to this repo (for example `mcp_server/.env.example` with `DESCOPE_PROJECT_ID=`, `GMAIL_CONNECTION_ID=gmail`, …, plus the `route.ts` tool definitions or a link to the public repo or commit). At minimum, put a "Server-side configuration" block in README/AI_USAGE listing every env var name with placeholder values.

### AR-3: Empty-body emails are excluded from dedup (6,192 rows) (medium, assignment vs plan)
- The plan (6.1) chose this. The assignment has no such exclusion: same from + subject + body ≥90% is a duplicate, and two empty bodies are 100% similar.
- Many of these rows are pure forwards. `empty_body_with_forward` = 6,575, because the forwarded text is moved out of `body`. So a forward sent twice is never detected.
- Fix (preferred): in `dedup.load_blocks` (`dedup.py:310-328`), when the normalised `body` is empty, compare `normalize_body(forwarded_content)` (select it in `_BLOCK_SQL`). Exclude only rows where both are empty. Keep the `excluded_empty_body` counter and document the rule in README.

### AR-4: Tool name `send_gmail_email` vs the assignment's "send_email tool" (low-medium)
- Assignment 4.3 and 5 say "send_email tool". The docs never mention the name difference.
- Fix: add one sentence in README "MCP setup" and in the AI_USAGE MCP section, for example: "The server's send tool is `send_gmail_email(to, subject, body, references?)`, which is the `send_email` tool the assignment refers to. The name is configurable through `MCP_SEND_TOOL`."

### AR-5: Live notices go to `NOTIFY_OVERRIDE_TO`, are capped at 3, and the body has an extra banner line (low, deliberate deviation)
- Assignment 4.2: "send a notification email to the sender of the latest (duplicate) email". The code sends to the override address (`notify.py:222-229`), adds `[Redirected for testing; intended recipient: …]` above the template (`notify.py:176-178`), and only sends the first `--max-send` groups.
- This is the right call, since the senders are real Enron addresses. README "Safety" covers it.
- Fix: also state it in the AI_USAGE MCP section. Say there that the dry-run `.eml` files (32k, one per flagged duplicate) carry the exact template addressed to the real sender, and that the live copy differs only in the To: address and the banner line.

### AR-6: `duplicates_report.csv` has an extra 8th column (low)
- The required 7 columns are in the exact order. `is_primary_target` is appended (`dedup.py:276-285`); the plan allows this.
- A grader checking the header for an exact match would flag it.
- Fix (safest): drop the column from the CSV (it is still in the DB and in `sample_queries.sql` Q5), or keep it and leave the README note at line 259. My recommendation is to drop it: the assignment overrides the plan, and its column list is written as exact.

### AR-7: `sample_queries.sql` "Actual output" placeholders are empty (low)
- Lines 15, 31, 47, 70, 93 and 119 read `-- Actual output (filled after full run):` with nothing after them. The assignment only asks for expected-output descriptions, which are present, but empty placeholders look unfinished.
- Fix: after the run, paste the top rows or counts as comments, or delete the placeholder lines.

### AR-8: Mandatory `subject` and `body` may be empty strings (low, documented)
- Assignment 3.1: mandatory fields "must be present for every record". The code accepts a `Subject:` header that is present but empty (2.53% of rows) and an empty primary body (10.75%; most of these have forwarded content).
- README line 228 documents the subject rule. Check that README also states the body rule explicitly: the body is present but can be `""` after splitting off forwarded or quoted text.

### AR-9: The column is named `date_utc`, but the assignment field and index are named `date` (low)
- Assignment 3.1 field `date`; T2 "indexes on date". The schema uses `date_utc` (`schema.sql:9,64`).
- Fix: add one README line mapping assignment field names to columns (`date` → `date_utc`, `to_addresses`/`cc_addresses`/`bcc_addresses` → `email_recipients`). Optionally add `CREATE VIEW emails_v AS SELECT *, date_utc AS date FROM emails;`.

### AR-10: requirements.txt does not match the imports (low)
- `mcp_client.py:52` imports `httpx2` (2.16.2 installed; it is a transitive dependency of `mcp==2.3.0`) but `httpx2` is not pinned. `httpx==0.28.1` is pinned but nothing imports it.
- Fix: add `httpx2==2.16.2`. Drop `httpx`, or keep it with a comment.

### AR-11: Python 3.10 compatibility (low)
- `mcp_client.py:88` uses `BaseExceptionGroup`, which exists only in 3.11+. On 3.10 the error path itself raises `NameError`, and `send()` lets it escape. README line 53 says "Python 3.10+ is required".
- Fix: either `BaseExceptionGroup = getattr(builtins, "BaseExceptionGroup", ())` with an `isinstance` guard, or change README to "Python 3.11+" (also confirm `mcp==2.3.0` supports 3.10).

### AR-12: Secrets and privacy (low)
- `output/send_queue.jsonl` exists, is untracked and is **not** gitignored. Each line contains `"to": <real NOTIFY_OVERRIDE_TO>` plus the Enron recipient. Add `output/send_queue.jsonl` to `.gitignore`.
- `output/send_log.csv` (a deliverable) will contain the user's real Gmail address in the `recipient` column. That is the user's decision. Mention it so they can mask it (for example `a****@gmail.com`) if they prefer.
- `git grep` on tracked files found no tokens, JWTs (`eyJ…`), Descope project or server IDs or client secrets. The `.env*` and `mcp_config.json` patterns are ignored. `docs/mcp_discovery.md` names the public Vercel host `descope-gmail-mcp.vercel.app`; that is not a secret, but README uses `<your-mcp-host>`, so pick one style.
- `.claude/` is gitignored. The plan suggested committing `.claude/agents/` as evidence of the multi-agent workflow, which is optional. If AI_USAGE cites those files, either commit them or remove the citation.

### AR-13: Repo size for `output/replies/` (low)
- 32,248 `.eml` files (about 1 KB each, plus 6.6 MB for `duplicates_report.csv`). The assignment requires all of them, so commit them, but expect a slow `git add` and push on this laptop. Do not zip them unless README explains why.

### AR-14: `mcp_client.py` (being written; clear problems only)
- `mcp_client.py:244-248`: any non-error tool result counts as `sent`, even if the JSON lacks `"status":"sent"`. Consider `status="error"` (with the text in `error`) when `data` is a dict whose `status` is not `"sent"`.
- `_describe` (line 88): the 3.10 issue in AR-11.
- No other problems found. Header injection is handled (line 184 collapses CR/LF in references), every path returns a `SendResult`, and the references argument is passed only when the schema declares it.

### Minor notes (no action needed)
- Assignment section 4.1 says "You may use any of the following MCP servers:" but the list is empty in the .docx, so the choice is free. Worth one line in AI_USAGE ("no list was given; built a Descope-backed server because …").
- Assignment 6 says "as described in Section 7"; that is a typo in the brief, which means Section 6.
- `error_log.txt` is written once, at the end of extraction (`main.py:225-227`). If the process were killed mid-run, the log would be lost. This is acceptable because every per-file error is caught.
- The live email cannot carry the template's `Date:` header: Gmail sets it (discovery 2(b)). The `References` header is set through the tool's `references` argument. This is fine and already documented in mcp_discovery.

---

## 4. Verify after the current run finishes

**`duplicates_report.csv`**
- The header is exactly `duplicate_message_id,original_message_id,subject,from_address,duplicate_date,original_date,similarity_score`, plus `,is_primary_target` only if AR-6 is not applied.
- The number of rows (excluding the header) equals `extraction_stats.json` → `dedup.emails_flagged`. For each `original_message_id`, the row count + 1 equals the group size, and the number of distinct originals equals `groups_found`.
- Every `similarity_score` is ≥ 90.0 and ≤ 100.0. No row has `duplicate_date < original_date`.
- If the column is kept: exactly one `is_primary_target=1` per original, and it is the row with the latest `duplicate_date`.
- About 1980-01-01 dates (the first rows of the old report): check how many rows have `original_date` or `duplicate_date` starting with `1980-` (bogus Enron dates), and mention them in README "Known limitations", because they decide which email counts as the "original".

**`error_log.txt`**
- Every line has 4 ` | ` fields: ISO timestamp, a `maildir/...` path without a trailing `_`, a stage in {read, parse, validate, insert}, and a reason.
- The line count equals `extraction_stats.json.failed` (the last run had 1,847 = 1,845 `missing_to` + 2 `missing_from`).
- Spot-check 3 `missing_to` files to confirm they really have no usable To: (for example To missing, `undisclosed-recipients:;`, or only an X-To).

**`extraction_stats.json`**
- `total_files_found` = `parsed_ok` + `failed` (holds unless there are insert failures; `failures_by_stage` should show only `validate`).
- 5 mailboxes in `per_mailbox`, with a sum ≥ 10,000 that equals `parsed_ok`. `exact_duplicates_skipped` = 0 on a `--fresh` run (if not 0, see AR-1).
- `field_completeness` has all 6 mandatory and 13 optional fields. Mandatory fields other than `subject` and `body` are 100.0.
- `dedup` has `groups_found`, `emails_flagged`, `avg_group_size`, `max_group_size`, `excluded_empty_body`. This last value changes if AR-3 is applied.
- The README `{{...}}` placeholders in "Results" match these numbers exactly.

**`output/replies/`**
- The `.eml` count equals `emails_flagged` (old run: 32,248 = 32,248).
- After FAIL-1: `grep -L "Content-Transfer-Encoding: 7bit" output/replies/*.eml | xargs grep -l "quoted-printable\|base64"` returns nothing.
- Compare 2 random files byte for byte against `tests/golden/notice_body_expected.txt`, ignoring the values. In particular check the 2/4/2-space indents, the two spaces after `Message-ID:`, three after `Date Sent:`, five after `Subject:`, a `%` after a 1-decimal score, and the closing line `No action is required if this is indeed a duplicate.`
- Headers: `To:` is the Enron sender (dry run), `Subject: [Duplicate Notice] Re: <original subject without Re:/Fwd:>`, `References:` is the duplicate's Message-ID.

**`output/send_log.csv`** (after Checkpoint 2)
- The header starts `timestamp,recipient,subject,status,error`. There is at least 1 row with `status=sent`.
- `recipient` is the override address and `intended_recipient` is the Enron sender.
- The matching DB row has `notification_sent=1` and `notification_date` set.
- `output/send_queue.jsonl` is not committed (AR-12).
