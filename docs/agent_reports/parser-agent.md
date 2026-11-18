# parser-agent report

## Files changed
- `src/enron_pipeline/dates.py`: `parse_date`, `to_iso_utc` (implemented the stubs, signatures unchanged)
- `src/enron_pipeline/addresses.py`: `normalize_address`, `parse_address_list`, `parse_single_address`, `is_smtp`
- `src/enron_pipeline/body.py`: `extract_text`, `split_body`, `extract_headings`, `detect_attachment`
- `src/enron_pipeline/parser.py`: `parse_bytes`, `parse_file`, plus the public helper `decode_header_value`
- `src/enron_pipeline/discover.py`: `iter_email_files`, `mailbox_of`, `relative_source`
- `tests/test_parser.py`, `tests/test_dates.py`, `tests/test_addresses.py`, `tests/test_body.py`
- `tests/fixtures/maildir/test-a/...` and `test-b/...`: 16 hand-made raw emails, named `N_` like the real extracted files. A one-off generator wrote them (kept in the session scratchpad, not in the repo) so I could control CRLF and binary bytes.

## Tests
Command: `.venv\Scripts\python.exe -m pytest tests/test_parser.py tests/test_dates.py tests/test_addresses.py tests/test_body.py`
Final result: **71 passed** (about 1.4 to 3.6 s). This includes `test_process_pool_matches_serial`: `ProcessPoolExecutor(max_workers=2)` gives results identical to serial parsing over all 16 fixtures (11 ParsedEmail, 5 ParseFailure).

The fixtures cover every item in plan 4.5, plus a few extras:
- **Date zones:** PDT comment with a numeric offset, plus CDT, EST and naive dates (fixtures 1, 2, 11, 14). An unparseable date is fixture 15.
- **Folded To:** 22 recipients plus a duplicate, in fixture 2.
- **Missing headers:** missing Message-ID (3), Date (4), To (10) and Subject (12). An empty Subject is allowed (11).
- **Bad encodings:** garbage or non-UTF-8 bytes in the body and an 8-bit Subject (5).
- **Addresses:** Exchange-style From and To (6), and the `<.name@enron.com>`, `'jeff'@enron.com` and `e-mail <.'john.doe'@enron.com>` forms (7).
- **Body splitting:** a nested forward inside a forward (8) and a `>` quoted reply with an `On ... wrote:` lead-in (9).
- **Attachments:** a `<<budget.xls>>` reference (1) and a multipart message with a real attachment part (13).
- **Other formats:** an HTML-only body with an RFC 2047 encoded subject (16), and an ALL-CAPS heading (2).

## Failures hit during development (exact)
1. **First run.** All 69 tests passed. That did not mean everything was right: the garbage-bytes subject assertion was too loose (`== ... or startswith("Caf")`). Inspecting the result showed the subject was `'Café \x93quotes\x94'`. The 8-bit header had been decoded as latin-1, which turns the cp1252 smart quotes into C1 control characters. Fix: header decoding now goes declared charset -> utf-8 -> cp1252 -> latin-1, and the test now asserts the exact value `"Café “quotes”"`.
2. **Test file corrupted.** While patching that test through a bash heredoc that ran Python `str.replace`, escape sequences were written as real characters, including a NUL byte. pytest then failed collection with `ValueError: source code string cannot contain null bytes` (`ERROR tests/test_parser.py`). Fix: I rewrote the two lines with explicit backslash escapes built via `chr(92)`.
3. **Probe script shadowed a stdlib module.** My smoke-probe script was named `inspect.py`, which shadows the stdlib `inspect` module that `dataclasses` imports. Error: `ImportError: cannot import name 'parse_file' from partially initialized module 'enron_pipeline.parser' (most likely due to a circular import)`. Fix: renamed it to `probe_sample.py`. This only affected the scratch script, not project code.
4. **Real-data finding: one valid email failed as `missing_to`.** The smoke test reported 52 `missing_to`; 51 had no To header at all. The 52nd had `To: <"the.desk":@enron.com>`, which `getaddresses` splits into `'"the.desk"'` and `'@enron.com'`. Fix: a pre-clean regex rewrites `"local":@domain` to `local@domain`, and a test was added. Result: 52 -> 51 failures.
5. **Real-data finding: noisy headings.** 13.5% of the sample had headings, and the top values were `FYI` (43), `[IMAGE]` (11), `FYI.`, `AL`/`DP`/`OR` (2-letter table cells), salutations like `Daren:`, column-aligned table rows, and boilerplate such as `To unsubscribe ..., go to:`. Fix: tightened the heuristic (see Design decisions) and added a regression test built from these exact lines. Result: 6.1%. The remaining top values look like real section titles (`SUBSCRIPTION SERVICES`, `ENRON BROADBAND SERVICES`, `CONTENTS:`, `COMMITTEE STRUCTURE`).

## Smoke test (real data, read-only, serial, one process, no DB)
Sample: every 31st file of the sorted file list for the 5 mailboxes (63,403 files total), first 2,000. Runtime was about 4 to 10 s.

| metric | value |
|---|---|
| parsed_ok | 1,949 (97.45%) |
| failed | 51, all `missing_to`, each with no `To:` header at all (calendar, contacts and draft items) |
| per-mailbox ok | kaminski-v 896, farmer-d 416, beck-s 374, lay-k 183, skilling-j 80 |

The mandatory fields are 100% present for parsed rows, by construction. Beyond that, 1,731 of 1,949 rows (88.8%) have a non-empty body; 215 rows have an empty body plus forwarded content, almost all `Forwarded by Vince J Kaminski` forwards with no comment. 1,899 rows (97.4%) have a non-empty subject.

Optional field completeness (% of parsed rows):

| field | % |
|---|---|
| cc | 36.0 |
| bcc | 36.0 |
| x_from | 100 |
| x_to | 100 |
| x_cc | 36.0 |
| x_bcc | 0.1 |
| x_folder | 100 |
| x_origin | 100 |
| content_type | 100 |
| forwarded_content | 39.7 |
| quoted_content | 1.4 |
| headings | 6.1 |

Other counts:

| count | value |
|---|---|
| has_attachment | 252 |
| date_tz_assumed | 0 |
| from_is_smtp == False | 0 |

**Three tricky cases fixed:**
1. `<"the.desk":@enron.com>` (kaminski-v): was a `missing_to` failure, now parses to `the.desk@enron.com`.
2. 8-bit raw Subject bytes such as `\x93...\x94`: decoded as C1 control characters with latin-1, now cp1252 smart quotes.
3. `maildir/lay-k/inbox/1003_`, whose To header is `e-mail <.ben@enron.com>, e-mail <.c.@enron.com>, ...`. It parses to `ben@enron.com, c@enron.com, ...`: leading and trailing dots are stripped from the local part, the folded header is unfolded, and `source_file` is `maildir/lay-k/inbox/1003` with the trailing `_` stripped.

## Design decisions
- **Validation order and stages.** Checks run message_id -> date -> from -> to -> subject, and the first failure wins. Validation failures use `stage="validate"`. Exceptions inside parsing give `stage="parse"` with reason `parse_error: <Type>: <msg>`. File I/O errors give `stage="read"` with reason `read_error: <Type>: <msg>`. Bad dates give `bad_date: <detail>`. `parse_file` never raises; a path outside `root` falls back to `source_file = path.as_posix()` and `mailbox = ""`.
- **Header decoding.** Uses `decode_header` (which handles compat32 `Header` objects carrying unknown-8bit) with the charset order declared charset -> utf-8 -> cp1252 -> latin-1, then unfolds the value and strips it. The plan only says "latin-1"; I added utf-8 and cp1252 first because latin-1 mangles Windows smart quotes.
- **Blank optional headers.** `X-cc:`, `X-bcc:` and similar headers that are present but blank are stored as `None`, not `""`, so completeness stats stay meaningful.
- **Body decoding.** Body charset order is part charset -> cp1252 -> latin-1, as the plan specifies. The result is CRLF-normalized to `\n`. For multipart messages the parser joins `text/plain` parts that aren't attachments, and falls back to tag-stripped `text/html`.
- **Dates.** The `(...)` comment is removed. If the remaining text has no zone, a known zone abbreviation from the comment is appended. The stdlib `parsedate_to_datetime` is tried first, then `dateutil` with `tzinfos` and `fuzzy=True`. A naive result gets -0800 and `tz_assumed=True`. Unknown abbreviations such as `CET` are treated as no zone, so -0800 is assumed.
- **Addresses.** Values are lowercased. Quotes, angle brackets, and leading or trailing dots, commas and the like are stripped from the local part and the domain. Tokens without `@` that aren't Exchange values (for example bare display-name fragments or `undisclosed-recipients:;`) are dropped. Exchange `/O=...` values are kept lowercased and give `is_smtp=False`; in lists that contain them, the header is split on commas so the slashes aren't mangled. Duplicates are removed with order preserved.
- **Forwarded split.** The first marker line by position wins. The marker line itself goes into `forwarded_content`, along with everything after it, so a nested forward inside a forward stays in forwarded content. An Outlook header block is a `From:` line with a `Sent:`/`To:`/`Subject:` line within the next 4 lines.
- **Quoted content.** Lines starting with `>` (after optional whitespace) and `On ... wrote:` lead-in lines move to `quoted_content`. Separate quote blocks are joined with a blank line.
- **Headings heuristic.** This is the plan 4.3 rule plus the false-positive filters learned from the real sample.
  - **Always required:** a line of at most 80 characters and at most 10 words.
  - **Always excluded:** header-like lines (`To:/From:/Subject:/cc:/bcc:/Sent:/Date:/Re:/Fw:`), lines containing URLs, `@` or `[IMAGE]`, `>`-quoted lines, and table-like lines (3 or more consecutive spaces, or an internal tab).
  - **ALL CAPS:** at least 2 letters, and letters make up at least 50% of the non-space characters. Lines ending in `.`, `!` or `?` are excluded, as are stop tokens (FYI, OK, IMAGE, PS, NB, ASAP, THANKS, HI, TIA). A single-word line needs at least 3 letters, which filters `AL`/`DP` table cells.
  - **Underlined:** the next line is `===` or `---` (3 or more characters).
  - **Numbered:** `1.`, `IV.` or `A)` followed by a Title Case title; titles ending in `.` are excluded.
  - **Colon:** the line ends in `:` and the next line is non-blank. The line must start uppercase, contain no comma, have at most 6 words, and not be the first non-empty line of the body (that position is treated as a salutation, like `Daren:`).
  - Headings are de-duplicated in order and newline-joined, or `None` if there are none.
  - **Known remaining noise:** a salutation that isn't on the first line, such as `Sally:` after a forwarded block, and short ALL-CAPS name lines like `RICKY A. ARCHER`.
- **Attachments.** `has_attachment` is True for any MIME part with `Content-Disposition: attachment` or a filename. In the body text it is also True for `<<x.ext>>`, `<< File: ... >>`, a ` - name.ext` line, or a `.doc/.xls/.ppt/.pdf/.zip` filename within 200 characters of "attach". The detection runs on the full decoded text, so attachment references inside forwarded content count too.
- **Discovery.** `iter_email_files` sorts the mailbox names, then walks with `os.walk` using sorted directory and file names. `relative_source` strips exactly one trailing `_` or `.` from the last path component (`45__` -> `45_`). If a direct `relative_to` fails it retries with resolved paths, and it raises ValueError for the root itself.

## Open questions / contract requests
- No contract changes needed.
- For the lead and db-agent: empty-body rows (215 of 1,949 in the sample, all with forwarded content) are valid ParsedEmail rows with `body == ""`, so stats should count them (plan 4.3).
- For the lead: `missing_to` (no To header: calendar, contacts, drafts) is about 2.5% of files. The plan mandates rejecting these; the README should mention it.
- I did not extend `TZINFOS` beyond the plan list. If the full run shows a non-zero `date_tz_assumed`, check whether it comes from abbreviations like CET or BST.
