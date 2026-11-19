# Fix report: notify + dedup (FAIL-1, AR-6, AR-3)

## Files changed
- `src/enron_pipeline/notify.py`
- `src/enron_pipeline/dedup.py`
- `tests/test_notify.py`
- `tests/test_dedup.py`
- `tests/golden/*`: not changed. The golden `.eml` still shows `Content-Transfer-Encoding: 7bit`.

## FAIL-1: raw .eml must contain the template verbatim
- New `NOTICE_POLICY = policy.default.clone(max_line_length=None, utf8=True)` is used for every notice.
  - `max_line_length=None` stops headers being folded, so a long `Subject:` stays on one line.
  - `utf8=True` writes non-ASCII headers as raw UTF-8 instead of RFC 2047 `=?utf-8?q?...?=` encoded words.
- New `body_cte(body) -> str` returns `"8bit"` when any line is longer than 78 characters or the body is not ASCII. Otherwise it returns `"7bit"`.
- `render_notice` now calls `set_content(body, charset="utf-8", cte=body_cte(body))`.
- Gotcha found while testing: passing `cte=None` together with `max_line_length=None` makes the stdlib raise `TypeError: '<=' not supported between instances of 'int' and 'NoneType'`. The stdlib's raw_data_manager compares line lengths against the policy limit. This broke 6 tests on the first run. Returning an explicit `"7bit"` avoids that code path, and the output is byte-identical to before for short ASCII notices.
- I checked the written bytes in the venv: the long and non-ASCII subjects appear unwrapped in both the header and the body, with `Content-Transfer-Encoding: 8bit`.
- Behaviour note: live sends do not use `render_notice`. They pass subject and body strings to the MCP sender, so this change does not affect them.

## AR-6: CSV columns
- `REPORT_COLUMNS` now has exactly these 7 columns: `duplicate_message_id, original_message_id, subject, from_address, duplicate_date, original_date, similarity_score`.
- `write_report` no longer writes `is_primary_target`. The field is still on `DuplicateFlag` and in the DB.

## AR-3: compare empty-body rows on forwarded_content
- `_BLOCK_SQL` now also selects `forwarded_content`. Blocking, ordering, anchor clustering and the pool are unchanged.
- New `comparison_text(body, forwarded) -> (text, from_forward)`:
  - If the normalised body is non-empty, the row is compared on its body.
  - If the body is empty, the row is compared on `normalize_body(forwarded_content)`.
  - If both are empty, the row is excluded.
- Rule for mixed blocks: each row is compared on its own text. A row that has a body never falls back to its forward. So a body-only row and a forward-only row are flagged when body vs forward is at least 90% similar. This is tested.
- `load_blocks` now returns `(blocks, BlockLoadStats)` instead of `(blocks, int)`. `BlockLoadStats` has two fields:
  - `excluded_empty_body`: rows where both the body and the forward are empty.
  - `compared_on_forwarded`: rows compared on the forward.
- `find_duplicates` returns `ForwardAwareDedupResult`, a dataclass subclass of `DedupResult` that adds `compared_on_forwarded: int = 0`.
  - `models.py` was outside my allowed files, which is why I used a subclass.
  - `stats.set_dedup` uses `asdict`, so `extraction_stats.json.dedup` now picks up `compared_on_forwarded` automatically. A test checks this.

## Tests
Command: `.venv\Scripts\python.exe -m pytest tests/test_notify.py tests/test_dedup.py`. Result: all 55 passed.

New or changed tests:
- **notify**
  - `test_raw_eml_contains_template_verbatim`, run with a long subject, a non-ASCII subject, and a subject that is both. It checks that the body bytes equal `render_body`, every template line is present, both Subject lines are unwrapped, the encoding is 8bit, and there is no QP, base64 or encoded word.
  - `test_short_ascii_notice_stays_7bit`
  - `test_body_cte`, which covers the 78/79-character boundary and non-ASCII text.
- **dedup**
  - `test_empty_bodies_excluded_and_counted`, which now also covers a forward that is only whitespace.
  - `test_empty_body_identical_forwards_are_duplicates`
  - `test_empty_body_different_forwards_not_duplicates`
  - `test_mixed_body_and_forward_compare_respective_text`
  - `test_comparison_text`
  - `test_dedup_stats_include_compared_on_forwarded`
  - `test_workers_identical_with_forwards`, which checks that 1 worker and 2 workers give the same result.
  - `test_report_columns_and_order`, which now expects the exact 7-column header and 7-field rows.

## Follow-ups for the lead (outside my scope)
- README line 252 still says empty bodies are excluded. Update it to describe the forwarded-content fallback, and add `compared_on_forwarded` next to the `{{excluded_empty_body}}` row (line 338).
- README line 259 still lists `is_primary_target` as a CSV column. Remove it.
- Optionally move `compared_on_forwarded` into `models.DedupResult` and drop the `ForwardAwareDedupResult` subclass.
- Re-run the pipeline. `excluded_empty_body` (6,192 last run) should drop sharply, because only rows with neither a body nor a forward are excluded now. `emails_flagged` and the `.eml` count will rise. Then check that `grep -l "quoted-printable\|base64" output/replies/*.eml` returns nothing.
