# notify-agent report

## Files changed
- `src/enron_pipeline/notify.py`: all stubs implemented (signatures unchanged). New public helpers: `live_body(n)`, constants `SUBJECT_PREFIX`, `REDIRECT_LINE`, `MAX_STEM_LEN`.
- `tests/test_notify.py`: new, 30 tests.
- `tests/golden/notice_body_expected.txt`: hand-typed from plan Section 7 with fixed values.
- `tests/golden/notice_expected.eml`: full expected message (headers + the same body).

## Tests
`.venv\Scripts\python.exe -m pytest tests/test_notify.py -v` gives **30 passed in 8.25s**.

What the tests cover:
- Golden body and golden full `.eml`, compared character for character. CRLF is normalised to LF on read, so a git autocrlf checkout can't break the test.
- Headers, including To override, Date, References and charset.
- `format_display_date`, including a non-UTC offset and unparseable input.
- Subject prefix stripping: `Re:`, `RE: re: FW: Fwd:`, `[Fwd: ...]` and empty.
- Similarity rounding to one decimal.
- A non-ASCII subject survives the round trip.
- `safe_filename`: charset, empty input, over-long IDs, Windows reserved names, hashing.
- Dry-run: count, filename collisions (including names that differ only by case), and per-item skip.
- `send_live` with a fake Sender and a monkeypatched `db.fetch_notice_inputs` / `db.mark_notified`:
  - sent path: body, headers, DB mark and exact log row
  - idempotent rerun
  - error result, raised exception and `approval_timeout`
  - cap at 1 and 0
  - refusal when the override is empty or blank: nothing is fetched, sent or logged
  - a `mark_notified` failure is survived
- `record_send`: mark and log, already-sent items are not re-marked, unknown ID, missing override.

## Failures hit
None. The suite passed on its first run, including the hand-written golden `.eml`, whose header order (`Content-Type`, `Content-Transfer-Encoding`, `MIME-Version`) I wrote from memory of `EmailMessage.set_content` behaviour before running. No errors to report.

## Design decisions
- **Dates in the body**: `format_display_date` uses `email.utils.format_datetime` on the UTC datetime, giving e.g. `Mon, 14 May 2001 23:39:00 +0000`. This matches the `Date` header format. Unparseable input is returned unchanged rather than raising.
- **Subject header**: `[Duplicate Notice] Re: ` + `strip_subject_prefixes(orig_subject)`. This uses the original's subject, as the template says `<original_subject>`. Casing is kept.
- **Body `Subject:` line**: the duplicate's subject as stored, prefixes kept.
- **Similarity**: `f"{score:.1f}%"`.
- **Message format**: `EmailMessage(policy=email.policy.default)` with `set_content(..., charset="utf-8")`. ASCII bodies come out as `7bit` and non-ASCII as `8bit`/base64 (stdlib's choice). Files are written with `BytesGenerator` and LF line endings.
- **File names**:
  - Unsafe runs of characters become `_`, and leading/trailing `._-` are stripped, so `<123.JavaMail.evans@thyme>` becomes `123.JavaMail.evans_thyme`.
  - Empty input becomes `message_<sha1[:8]>`.
  - Windows reserved names get a `_` prefix.
  - Stems longer than 120 characters are truncated and given a hash suffix.
  - Within one run, a collision (case-insensitive, for Windows) gets `_<sha1[:8]>` appended, so nothing is overwritten. Reruns are deterministic: the same ID gives the same file, which is overwritten in place.
- **`send_live`**:
  - Refuses with `ValueError` when the override is empty or whitespace, before calling the DB or the sender.
  - Calls `db.fetch_notice_inputs(conn, primary_only=True, unsent_only=True, limit=max_send)` and slices the result again as a guard. `max_send <= 0` returns `[]`.
  - Sender exceptions become `SendResult("error", "<ExcType>: msg")`.
  - Only `sent` calls `mark_notified`, with `notification_date` set to ISO UTC `now`.
  - If `mark_notified` raises, the result stays `sent` with the failure recorded in the `error` field, because the email really went out. A failed log write is reported through `logging`. Nothing raises per item.
- **Log timestamp**: ISO UTC (`2026-11-18T12:00:00+00:00`). `recipient` is the override address, `intended_recipient` is the Enron sender. The header is written only when the file is new or empty.
- **`record_send`**:
  - Raises `ValueError` if the override is empty or the `message_id` is not a flagged duplicate.
  - If the item was already notified, it still logs the reported status, with `error="already notified; DB not changed"`, and does not touch `notification_date` (idempotent).
  - It does not require the item to be a primary target.

## Open questions / requests for the lead
1. `record_send` takes `status: str`. Statuses outside `SendStatus` pass through unchecked, so `main.py` should restrict `--status` choices to the `SendStatus` literals.
2. Rule 0.4 says to refuse when `NOTIFY_OVERRIDE_TO` is unset. `send_live` enforces this itself, but `main.py` should also check it before building the MCP client, so the user gets a clean message rather than a traceback.
3. The `db.fetch_notice_inputs` contract is assumed to support `unsent_only` and `primary_only` together, and `limit`. The tests use a fake, so the real implementation is not exercised here.
4. Dry-run file names are deduplicated within a run only. Stale `.eml` files from an earlier run with a different duplicate set are not removed. `main.py` may want to clear `output/replies/` before a dry run.
