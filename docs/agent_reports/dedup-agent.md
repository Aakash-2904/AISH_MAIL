# dedup-agent report

## Files changed
- `src/enron_pipeline/dedup.py`: implemented `load_blocks`, `cluster_block`, `find_duplicates`, `write_report`. Signatures and `REPORT_COLUMNS` are unchanged. Private helpers: `_length_allows`, `_match_score`, `_cluster_batch` (worker entry point), `_batch_blocks`, `_run_clustering`, `_group_sizes`, `_email_meta`.
- `tests/test_dedup.py`: new, 11 tests.
- I did not edit any contract files (`schema.sql`, `models.py`, `normalize.py`, `config.py`).

## Tests
Command: `.venv\Scripts\python.exe -m pytest tests/test_dedup.py -v`. Result: **11 passed in 2.80s**.

Each test builds a temp DB from `schema.sql` and inserts rows with raw SQL. `normalized_subject` comes from `normalize.normalize_subject`. Nothing depends on `db.py`.

| Test | What it checks |
|---|---|
| test_crafted_ratios | Confirms the actual `fuzz.ratio` of the synthetic bodies is exactly 92.0 and 85.0. The base text is 100 chars, and k evenly spaced chars are replaced with `#`, which gives ratio = 100-k. |
| test_re_prefix_identical_body_is_duplicate | "Gas report" vs "RE: Gas report" with the same body in different case and whitespace → duplicate, score 100.0, primary. |
| test_92_percent_is_duplicate | 92% → flagged with score 92.0. |
| test_85_percent_is_not_duplicate | 85% → not flagged. |
| test_same_body_different_sender_not_duplicate | Different `from_address` → different block → not flagged. |
| test_group_of_three_latest_is_primary | Rows are inserted out of date order. Two are flagged against the earliest; only the latest is primary; stats are groups=1, flagged=2, max=3, avg=3.0. |
| test_empty_bodies_excluded_and_counted | Empty and whitespace-only bodies are excluded and counted (3). A block that drops to 1 member after exclusion is not compared. |
| test_load_blocks_ordering | Blocks are sorted by size descending; members are sorted by date. |
| test_cluster_block_anchor_not_chaining | B is ≥90 vs A and C is ≥90 vs B, but C is <90 vs A. Only B is flagged, so there is no transitive chaining. |
| test_workers_identical | workers=1 and workers=2 give identical flags and stats. It monkeypatches `_BATCH_MEMBERS=5` so the pool really gets more than 2 tasks. |
| test_report_columns_and_order | Header equals `REPORT_COLUMNS` exactly, with `is_primary_target` last. Rows are sorted by original date, and the full row contents are asserted. |

## Failures hit
- No test failures and no errors; the suite passed on the first run.
- I found one weakness myself, not through a failure. With the default batch size of 2000 members, all the test blocks fit in a single pool task, so the workers=1 vs workers=2 test would not really exercise distribution across processes. Fix: `_batch_blocks` now reads the module-level `_BATCH_MEMBERS` at call time (it used to be a default argument bound at definition time), and the test monkeypatches it to 5 and asserts there are more than 2 batches. Batching happens in the parent process, so the monkeypatch takes effect even though Windows workers are spawned.

## Algorithm notes (for README)
- **Blocking (SQL):** emails are keyed by `(from_address, COALESCE(normalized_subject, ''))`. A CTE keeps only keys with ≥2 rows. Rows stream in `ORDER BY from_address, nsubj, date_utc, id` and are grouped in Python with `itertools.groupby`.
- **Empty bodies:** `normalize_body(body)` is computed per row. Rows whose result is empty are dropped *before* block size is decided, and are counted in `excluded_empty_body`. This count covers only empty-body emails in (from, subject) keys that had ≥2 rows in SQL. An empty-body email that is alone in its key could never be compared, so it is not counted. Without this exclusion, every empty-body email sharing a subject would be a "100% match".
- **Block order:** size descending, then (first member's date_utc, id). This sends the biggest blocks to the pool first.
- **Anchor clustering:** members are walked in (date_utc, id) order. Each one is compared only with the *original* (earliest member) of each existing cluster, and it joins the first cluster where `fuzz.ratio ≥ threshold` (default 90). So `similarity_score` is always measured against the original, and there is no chaining drift.
- **Pruning:** a comparison is skipped when `2*min(len)/(len_a+len_b)*100 < threshold`. `fuzz.ratio(..., score_cutoff=threshold)` returns 0 below the cutoff.
- **Flags:** in each cluster of size ≥2, every non-original member gets a `DuplicateFlag` with the score rounded to 2 decimals. Only the latest member (the last by (date_utc, id)) has `is_primary_target=True`.
- **Parallelism:** with `workers > 1`, blocks are packed greedily, biggest first, into batches of about 2000 members (`_BATCH_MEMBERS`). Huge blocks therefore go alone, and tiny blocks don't each pay an IPC round-trip. The batches run through `ProcessPoolExecutor.map`. The final flags are sorted by `(original_message_id, duplicate_message_id)`, so the output is identical for any worker count.
- **Stats:** `groups_found` and the size stats come from the flags (group size = flags per original + 1). `blocks_compared` is the number of blocks with ≥2 non-empty members. `runtime_seconds` covers load plus clustering.
- **Report:** `subject` and `from_address` are the *duplicate's* (same sender by construction; the subject may carry `Re:`). `similarity_score` has 1 decimal. `is_primary_target` is written as `1`/`0`, matching the DB convention. Rows are sorted by (original_date, original_id, duplicate_date, duplicate_id). Metadata is fetched with chunked `IN (...)` queries of 500 ids. The parent directory is created if missing.

## Open questions / requests for lead
1. The `subject` column in the report uses the duplicate's subject. Switch to the original's if you prefer.
2. `is_primary_target` in the CSV is `1`/`0`. Say if you want `True`/`False`.
3. Memory: `load_blocks` holds the normalised bodies of all candidate emails at once, as the return type requires. That should be fine for the 5 default mailboxes, but watch it on the full corpus on the 8 GB machine.
4. No changes are needed to `normalize.py`. Boilerplate and disclaimer stripping is not implemented there (the plan says "only if trivially detectable"). It could improve recall later.
