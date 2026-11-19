# fix-cli: AR-1 (send state survives `--fresh`, no skewed re-run stats)

## Files changed
- `main.py`
- `tests/test_cli.py`
- `Makefile`: not changed. `run` uses `--fresh`. `run-live` is `main.py --send-live --max-send 3`, with no `--fresh`. A new test checks both.

`db.py` and `notify.py` were not touched. The existing `db.mark_notified` was enough, so nothing is missing.

## Changes in main.py
1. **`read_sent_log(path)`** returns `{message_id: timestamp}` for the first `status=sent` row of each id in `output/send_log.csv`.
   - A missing log means nothing was sent yet, so the function returns an empty dict.
   - These cases print a warning to stderr and never raise:
     - a file that cannot be read or is not UTF-8
     - a header without `status`/`message_id`
     - rows with missing fields or extra columns (these are skipped and counted)
2. **`restore_send_state(conn, path, sent=None)`** runs after dedup. For every row with `is_duplicate=1 AND notification_sent=0` whose message_id has a `sent` log row, it calls `db.mark_notified(conn, mid, <log timestamp>)`.
   - It prints `send state: restored N notification flag(s) from <log> (M sent row(s) in log)`.
   - DB errors produce a warning and are not raised.
3. **Pipeline**: the restore runs on every dedup, both after `--fresh` and after `--skip-extract`, before the dry-run `.eml` output.
4. **Send paths**: `run_send_live` runs the restore before either path.
   - On the `--via-claude-code` path, `queue_items(..., exclude=sent_ids)` also leaves out ids logged as `sent`. It fetches `max_send + len(exclude)` so the cap can still be filled. This holds even if the DB flag could not be restored, for example when the id is no longer flagged.
   - On the direct `--send-live` path, `notify.send_live` already selects only `unsent_only` targets. The restore before it means those are never re-sent.
5. **Plain re-run refused**: if `--fresh` and `--skip-extract` are both absent and the DB file exists with emails, the pipeline prints a clear message naming both flags and returns exit code **2**.
   - This happens before the dataset check, extraction or any stats write, so `extraction_stats.json` is left as it was.
   - The old "note: ... re-inserted rows count as exact duplicates" path is removed.
   - The DB file is only opened for this check if it already exists, so no empty DB is created.
6. The module docstring describes the new rules.

## Tests (`.venv\Scripts\python.exe -m pytest tests/test_cli.py`, Python 3.11.0)
Result: **57 passed** (48 existing + 9 new), 0 failed.

- New autouse fixture `_isolate_paths` points `config.DB_PATH` and `config.SEND_LOG` into `tmp_path`. Before this, `test_stage_failure_*` (`cli.main([])`) would have opened the real `data/enron.db` through the new populated-DB check, and the send tests would have read the real `output/send_log.csv`.
- New tests:
  - `test_fresh_run_restores_sent_flags_from_log`: the log has two `sent` rows for one id (the first timestamp is kept), an `error` row and a `sent` row for an id not in the DB. After `--fresh`, exactly 1 flag is restored with the right date. `--skip-extract` keeps the flag and restores 0.
  - `test_queue_excludes_ids_logged_as_sent`: calls `queue_items(exclude=...)` directly, and also runs `--send-live --via-claude-code` end to end with a `sent` log row. The id is not queued and its DB flag is restored.
  - `test_malformed_send_log_is_tolerated` (4 cases: empty, non-UTF-8, wrong header, bad rows): the run exits 0, nothing is restored and no traceback appears.
  - `test_missing_send_log_and_bad_db_never_crash`
  - `test_populated_db_without_fresh_or_skip_extract_exits_2`: exits 2, the message names both flags, the dataset check is not reached and the stats file is unchanged.
  - `test_makefile_run_live_never_fresh`

## Failures hit and fixes
- No test failures on the first run.
- A first attempt to apply the edit through a bash heredoc failed with a shell quoting error, before anything was written. I re-applied it with a Python script.
- Design catch before running: the populated-DB check would have opened the real default DB in the existing tests. I fixed this with the `_isolate_paths` fixture and the `is_file()` guard.

## Notes for the lead
- `python main.py` with no flags now exits 2 on a populated DB. `make run` (`--fresh`) is unaffected. README wording about "single command `python main.py`" may need a line about this: on a fresh checkout the plain command still works, because there is no DB yet.
- I did not run `main.py` against data/ and did not touch output/.
