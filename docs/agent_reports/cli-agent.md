# cli-agent report

## Files changed
- `main.py`: the full CLI. It has every flag from plan Section 9.
  - **Default run:**
    1. Dataset check, which calls the functions in `scripts/download_dataset.py`.
    2. Discover the files.
    3. Parse them. With `--workers 1` this runs serially. Otherwise it uses `ProcessPoolExecutor.map(partial(parse_file, root=...), chunksize=PARSE_CHUNKSIZE)`.
    4. The main process is the only writer. It buffers emails and calls `db.insert_batch` every `INSERT_BATCH_SIZE`.
    5. Write the failures with `db.log_failures` and `stats.write_error_log`.
    6. Write `extraction_stats.json`.
    7. Dedup: `clear_duplicate_flags`, `find_duplicates(workers=...)`, `apply_duplicate_flags`, then `write_report`.
    8. Rewrite the stats with the `dedup` section.
    9. Dry-run: delete the old `*.eml` files, then `fetch_notice_inputs` (every flagged email) and `notify.write_dry_run`.
    10. Print the summary.
  - **`--send-live`:** checks `NOTIFY_OVERRIDE_TO` first and refuses before opening the DB if it is unset. It also refuses if the DB has no emails.
    - With `--via-claude-code` it writes `output/send_queue.jsonl` and stops. Each line has `message_id`, `to` (the override address), `intended_recipient`, `subject`, `body` (built with `notify.live_body`) and `headers.References`. The targets match `send_live`: primary only, unsent only, capped by `--max-send`.
    - Without that flag it needs `DESCOPE_MCP_URL` and `DESCOPE_MCP_ACCESS_TOKEN`. It then builds `McpGmailClient(url, token, send_tool)` and calls `notify.send_live`.
  - **`--record-send`:** calls `notify.record_send` after the same override check.
  - **Error handling:** each stage is wrapped by `stage(name)`. A failure prints `error: [stage] ExcType: msg` and `(re-run with -v for the full traceback)`, then exits 1. `-v` also prints the traceback and turns on DEBUG logging. Bad flag combinations exit 2 through argparse.
  - **Progress bar:** tqdm.
  - **Windows spawn:** guarded by `if __name__ == "__main__"`. `src/` and `scripts/` are added to `sys.path`.
- `scripts/download_dataset.py`: stdlib only, adapted from the scratchpad reference.
  - `download(url, dest)`: skips without any network call when `dest` is exactly the known CMU size (443,254,787 bytes). Otherwise it does a HEAD request and a streaming download into `.part`, resuming with Range (and restarting if the server ignores Range). It shows a progress bar, retries up to 8 times and checks the size before moving the file into place.
  - `extract(tarball, dest_root, mailboxes)`: one streaming pass with `r|gz`. It writes regular files only, and skips directories and links.
    - A trailing `.` or space becomes `_` on every OS.
    - It rejects absolute paths, drive letters, backslashes, `..`, `.` and empty path components, and checks that the target stays inside the destination with `commonpath`. It does not use `filter=`.
  - Also includes `missing_mailboxes`, `ensure_dataset` (only extracts mailboxes that are missing or empty) and a CLI (`--mailboxes`, `--data-dir`, `--url`, `--force-extract`).
- `scripts/mailbox_sizes.py`: prints a file-count table per mailbox with a TOTAL row, plus `OK` or `WARNING` against 10,000. It exits 1 when the total is under 10,000. Flags: `--maildir`, `--mailboxes`, `--all`.
- `Makefile`: targets `setup`, `download`, `sizes`, `run` (`main.py --fresh`), `run-live` (`--send-live --max-send 3`), `test` and `clean`.
  - The Python path is `.venv/Scripts/python.exe` on Windows and `.venv/bin/python` elsewhere. The interpreter used to create the venv is `py -3.11` on Windows and `python3` elsewhere; override it with `BOOTSTRAP=`.
  - `clean` only removes generated files: the DB, `.eml` files, the send queue and caches. It keeps `data/` and the committed deliverables.
  - I could not run it because `make` is not installed here.
- `tests/test_cli.py`: 48 tests.

## Tests
`.venv\Scripts\python.exe -m pytest tests/test_cli.py` gives **48 passed in 1.94s**.

What the tests cover:
- **Argument parsing:** defaults, every flag, mailbox de-duplication, and 9 invalid combinations that must exit 2.
- **Override refusal:** for `--send-live` and `--record-send`, including whitespace-only values. The tests check that the DB is never opened.
- **Missing MCP settings:** a clear error is printed.
- **`send_live` wiring:** checked with a fake client.
- **`--via-claude-code` queue:** the stale queue is replaced, the cap is applied, and the fields and redirect line are correct. No sender is called.
- **Empty DB:** both the send path and `--skip-extract` refuse.
- **`--record-send`:** the arguments are passed through, and an unknown ID gives a clean error.
- **Stage failures:** a one-line message without `-v` and a traceback with `-v`.
- **`load_results`:** batching, and failure collection for both parse and insert failures.
- **End-to-end dry run in a temp directory:** discover and `parse_file` are faked; db, dedup, notify and stats are real. It checks the stats JSON (including `workers` and the runtime key), the error log, the CSV, that 3 `.eml` files are written and that old ones are removed. It also checks that `--skip-extract` keeps the extraction stats.
- **Process pool:** a 2-worker run of the real `parse_file` over `tests/fixtures` gives the same results as the serial path.
- **Extraction on a tiny tarball built in the test:**
  - name mapping: `1.` becomes `1_`
  - a `./` prefix is accepted
  - symlinks are skipped
  - `lay-kx` is not matched by the `lay-k` prefix
  - other mailboxes are skipped
- **Path safety:**
  - `member_relpath` rejects 7 kinds of bad names
  - `safe_target` rejects an escape
  - a tarball containing `maildir/lay-k/../../evil.txt` is refused and nothing is written
- **Download:** a complete file is skipped without any network call (`urlopen` and HEAD are patched to fail the test). Resume sends `Range: bytes=5-` and the parts are joined correctly.
- **`ensure_dataset`:** skips mailboxes that are present and extracts missing ones. `main.ensure_dataset` passes only the missing mailboxes and `maildir.parent`.
- **`mailbox_sizes`:** the table, the `(missing)` marker, and the WARNING and OK lines.

## mailbox_sizes output (real data, `.venv\Scripts\python.exe scripts/mailbox_sizes.py`)
```
mailbox       emails
----------  --------
lay-k           5937
skilling-j      4139
kaminski-v     28465
farmer-d       13032
beck-s         11830
----------  --------
TOTAL          63403
OK: total 63403 >= 10000
```
Exit code 0.

## Failures I hit (exact errors)
No code or test failures. All 48 tests passed on the first run, and so did the later process-pool test. There were two tooling problems, neither caused by the code:
1. My first look at the project used `ls -R enron-dedup-pipeline | grep -v "^data" | head -60`. It walked the 63k-file `data/maildir`, and the tool reported: `Command did not complete within its 120s timeout and was moved to the background`. I stopped the task with TaskStop and used targeted `ls` calls instead.
2. Checking the Makefile tabs with `grep -nP '^\t' Makefile` in Git Bash failed with `grep: -P supports only unibyte and UTF-8 locales`. I used `cat -A Makefile` instead. It showed the recipe lines are real tabs (`^I`). I then changed the tab-indented variable assignments inside `ifeq` to spaces, so GNU make cannot mistake them for recipe lines.

## Design decisions and notes for the lead
- **`run` uses `--fresh`.** Without `--fresh`, a second extract into a populated DB counts every row as an exact duplicate (`inserted=0`), which would make `extraction_stats.json` misleading. `main.py` prints a note in that case. `--fresh` also resets `notification_sent`, so `run-live` must not use `--fresh`. Do `make run` once, then `make run-live` as often as needed; it is idempotent through `notification_sent`.
- **`--send-live` does not re-run the pipeline.** It works on the existing DB and refuses if the DB is empty. This keeps live sends fast and stops a re-extract from disturbing the notification state.
- **Stats JSON:** `main.py` builds `collector.to_dict()` itself and adds a top-level `"workers": N`. Runtimes are stored as `extract_seconds` and `extract_workers_<N>_seconds`, so runs with different worker counts can be compared for the README. To get the 1-vs-N timing, the lead needs two runs, for example `--fresh --workers 1` and then `--fresh --workers 3`, and must copy the first timing by hand because each run rewrites the file. `stats.write_json` is not used, because of the extra `workers` key.
- **`--skip-extract`** keeps the existing `extraction_stats.json` and only replaces its `dedup` key.
- **Dry-run** writes one `.eml` per flagged duplicate (every flagged email, not only primaries), as the deliverables table requires. Old `*.eml` files are deleted first.
- **`--send-live` exit code:** 1 if any send result is not `sent`, for example `pending_approval`. Every attempt is still logged by `notify`.
- **Dataset check:** only triggered when a selected `maildir/<mailbox>` folder is missing or empty. It downloads into `maildir.parent`. With the current data present, no network call is made.

## Open questions / contract requests
- **No contract changes are needed.** Optional: a `workers: int` field or setter on `StatsCollector` would let `main.py` use `stats.write_json` instead of writing the dict itself.
- **`mcp_client.McpGmailClient` is still a stub.** `main.py` only builds it inside `_make_client`, and it calls `client.close()` nowhere. If mcp-agent adds a context manager or `close()`, `run_send_live` should call it. That is a one-line change in `main.py`.
- **`make setup` on Windows assumes the `py` launcher has 3.11.** Override with `make setup BOOTSTRAP=python` if needed.
- **Not run against the real dataset**, as instructed. The lead's integration run is `python main.py --fresh`.
