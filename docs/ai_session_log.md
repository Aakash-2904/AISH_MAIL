# AI session log

Running log of prompts, outputs, failures and fixes (source for AI_USAGE.md).
Tool: Claude Code (Opus 5.5). Entries are recorded as they happen.

---

## 2026-11-18: Plan review and environment check
- **Prompt (user):** "can u read the plan.md first completely and discuss the plan and all the things that u will require prior to u running"
- **Produced:** Claude read `plan.md` and the assignment .docx, then checked the machine. Python 3.11 OK. 194 GB disk free. Only ~2 GB of 7.9 GB RAM free. No `make` or `sqlite3` CLI.
- **Problems found before any code was written:**
  - No Descope Gmail MCP server was registered in Claude Code (`claude mcp list` showed only claude.ai connectors).
  - The working folder sat inside a git repo rooted at the user's home directory, so the project needs its own `git init`.
  - Conflicts with the user's standing preferences: `.claude/agents/` must not be committed, and work should stop for a report after each stage.

## 2026-11-18: Descope Gmail MCP server
- **Prompt (user):** "could u help me setup the descope MCP server ?" then "u do it urself"
- **Research:** Descope blog "claude-gmail-agent" and the `descope/ai` sample repo.
- **Failure 1:** the template URL from the blog/agent summary did not exist.
  - Error: `git clone https://github.com/descope/mcp-with-next-js-and-descope.git` → `remote: Repository not found.`
  - Fix: listed `descope/ai` with `gh api repos/descope/ai/contents/examples` and found `examples/nextjs-vercel-mcp-server`. Sparse-cloned that folder.
- **Adaptation:** the Gmail sample passes user identity in `_meta` (custom Node agent). Claude Code and a Python MCP client send a Bearer token instead. So the `send_gmail_email` and `read_gmail_inbox` tools were rewritten to take `userId` and the token from `extra.authInfo` (set by `withMcpAuth`).
  - Added an optional `references` argument (sets the References/In-Reply-To headers).
  - Return `{"error":"insufficient_scope","authorization_url":...}` when Gmail is not yet connected.
  - Enchanted-link approval left out for now (recommended: get plain send working first).
- **Failure 2:** `pnpm install` was started twice (the first one looked stalled because output was buffered). Claude killed the second install mid-way.
  - Error on build: `Error: Cannot find module '...\node_modules\next\dist\bin\next'` (MODULE_NOT_FOUND).
  - Fix: deleted `node_modules` and ran one clean `pnpm install --frozen-lockfile` (23 s from cache).
  - Lesson: check for running processes before re-running an install.

## 2026-11-18: Dataset download (background subagent)
- **Prompt (user):** "use another agent to download the dataset meanwhile"
- **Result:** 443,254,787-byte tarball. Five mailboxes extracted by streaming the tar: lay-k 5,937, skilling-j 4,139, kaminski-v 28,465, farmer-d 13,032, beck-s 11,830. **Total 63,403**. Took 338 s.
- **Issue:** every Enron filename ends in `.` (e.g. `45.`), which Windows silently strips. The extractor writes them as `45_` instead. `discover.relative_source` maps them back to `maildir/<box>/<folder>/45`.
- **Issue:** `tarfile` `filter="data"` is not available in Python 3.11.0 (added in 3.11.4). The extractor enforces the same path-safety checks manually.

## 2026-11-18: Wave 0 (lead)
- **Prompt (user):** "use multiple agents to get things done faster"
- **Produced:** scaffold, `models.py` contracts, `schema.sql`, `config.py` (default workers capped at 3 for the 8 GB laptop), `normalize.py`, typed stubs for every module, `.gitignore` (data, secrets, `.claude/`), `.env.example`, a Python 3.11 venv, and pinned `requirements.txt`.
- **Note:** the installed `mcp` SDK is **2.3.0**. The plan assumed the 1.x `streamablehttp_client` API, so this needs checking in Wave 2.
- **Commit:** `76ef179 chore: scaffold project, data contracts, schema and module stubs`
- **Wave 1 launched:** parser-agent, db-agent, dedup-agent, notify-agent, cli-agent, all in parallel. mcp-scout was deferred until the MCP server is deployed.

## 2026-11-18: Wave 1 results (5 parallel subagents)
- **Agents:** parser-agent, db-agent, dedup-agent, notify-agent, cli-agent, each limited to its own files (plan 1A.2). Reports are in `docs/agent_reports/`.
- **Tests per agent:** parser 71, db+stats 18, dedup 11, notify 30, cli 48. The full suite is 178 passed.
- **Real failures (copied from the reports):**
  - **parser-agent: the first green run hid a wrong value.** An 8-bit subject decoded to `'Café \x93quotes\x94'`: latin-1 turned the cp1252 smart quotes into C1 control characters.
    - Fix: header decoding now tries declared charset → utf-8 → cp1252 → latin-1. The assertion was tightened to the exact value.
  - **parser-agent: test file corrupted by a heredoc edit.** Running a Python `str.replace` through a heredoc wrote a NUL byte into the file. pytest error: `ValueError: source code string cannot contain null bytes`.
    - Fix: rewrote the lines with explicit escapes.
  - **parser-agent: scratch probe shadowed a stdlib module.** The probe was named `inspect.py`. Error: `ImportError: cannot import name 'parse_file' from partially initialized module 'enron_pipeline.parser'`.
    - Fix: renamed it to `probe_sample.py`.
  - **parser-agent: valid email rejected as `missing_to`.** In the real data, `To: <"the.desk":@enron.com>` was split by `getaddresses` into two broken parts.
    - Fix: a pre-clean regex plus a regression test. The 2,000-file sample went from 52 to 51 failures.
  - **parser-agent: headings heuristic too noisy.** On real data it matched 13.5% of emails (`FYI`, `[IMAGE]`, two-letter table cells, salutations).
    - Fix: tightened the rules and added a regression test. Matches dropped to 6.1%.
  - **dedup-agent: the workers-equivalence test was not really testing anything.** All the blocks fit in one batch.
    - Fix: the batch size is now read at call time, and the test monkeypatches it so more than 2 batches are made.
  - **cli-agent: tooling problems.**
    - `ls -R` over the 63k-file maildir timed out after 120 s.
    - `grep -P` failed in Git Bash with `-P supports only unibyte and UTF-8 locales`. Used `cat -A` instead.
    - Tab-indented assignments inside the Makefile's `ifeq` blocks were changed to spaces.
- **Commits:** 812027c, 6af76e9, df8a36c, 71832e1, c361e7d, bc8bff3.

## 2026-11-18: Descope MCP server fixed and verified (lead)
- **Prompt (user):** "/mcp … again ?" (re-auth kept failing). This was followed by several "check again" rounds.
- **Failure 1: every sign-in rejected.** Claude Code reported `descope-gmail rejected them on reconnect`.
  - Root cause: the token's `iss` is `…/v1/apps/agentic/<project>/<mcp_server>`. `@descope/node-sdk` `validateSession` requires the last path segment of the issuer to equal the project ID, so it throws `unexpected "iss" claim value`.
  - Fix: verify the JWT with WebCrypto against the project JWKS, and accept the project issuer and the agentic issuer. Redeployed to Vercel.
- **Failure 2: Descope blocked the Gmail token fetch.** `E113301 "Fetching outbound app tokens is not allowed according to access control rules"`. Found by adding logging of Descope's error replies.
  - Fix (user, in the Descope console): changed the access rules on the gmail connection, then re-authenticated.
- **Failure 3: Google consent failed.** Google returned `Error 400: redirect_uri_mismatch`.
  - Fix (user): added `https://api.descope.com/v1/outbound/oauth/callback` to the Google OAuth web client.
- **Failure 4: the inbox read came back empty.** The tool swallowed Gmail API errors. After changing the tool to report them, the real error was `Gmail API 403: Gmail API has not been used in project … or it is disabled`.
  - Fix (user): enabled the Gmail API.
- **Verified:** `read_gmail_inbox` returned 3 real messages.

## 2026-11-18/19: Wave 2 integration run + live sends (lead)
- **Full run:** `python main.py --fresh` (3 workers). Finished in 846.5 s with exit 0.
  - 63,403 files; 61,556 parsed; 1,847 failed (1,845 `missing_to`, 2 `missing_from`); 0 exact Message-ID duplicates.
  - Dedup: 13,962 groups, 32,248 flagged, 6,192 empty-body emails excluded, 160 s.
- **Spot checks:**
  - 5 random DB rows compared with their source files. My first check script reported 1 MISMATCH (`kaminski-v/resumes/237`). Cause: the script split headers on `\n\n` and kept the last value for each name, so a `Subject:` line inside the forwarded body overwrote the real header. The DB row was correct. This was a bug in the check script, not the pipeline.
  - 5 random dup groups: every one was the same message filed in `_sent_mail`/`all_documents`/`discussion_threads`/`sent` with different Message-IDs, scoring 100.
- **Python MCP client:** mcp-agent built it against mcp 2.3.0. The 1.x `streamablehttp_client` no longer exists; the client uses `streamable_http_client` with `httpx2`. 13 mock tests pass.
  - No way to get a token outside Claude Code, so live sends used the plan 8.3 path (`--send-live --via-claude-code` + `--record-send`).
- **Live send 1:** the first `send_gmail_email` call returned `{"error":"insufficient_scope", ...}` with a Google consent URL for `gmail.send`. The user approved it, and the retry returned `{"status":"sent"}`.
- **Live sends 2-3:** user approved ("yes send 2 more"). Both returned `sent`.
- `output/send_log.csv` has 3 `sent` rows, all to the user's own address, with the Enron senders listed only as `intended_recipient`. The DB has `notification_sent=1` for all three.
- **Prompt used** (Claude Code as the MCP client): read `output/send_queue.jsonl`, call `send_gmail_email` for each item with `to`/`subject`/`body`/`references`, then run `python main.py --record-send <message_id> --status sent`.

## 2026-11-19: Wave 3/4 spec review, fixes, docs (parallel agents + lead)
- **spec-reviewer** compared the code with the assignment field by field (`docs/agent_reports/spec-review.md`).
  - **FAIL-1:** 1,455 of 32,248 `.eml` files broke the template. `set_content` without `cte` made long or non-ASCII lines quoted-printable, which soft-wraps them (`Subject: ... February 22, 2=` / `001`).
  - **AT RISK:** `--fresh` wiped `notification_sent`, so `make run` then `make run-live` could re-send; empty-body pure forwards were never compared; the CSV had an extra `is_primary_target` column; and smaller doc gaps.
- **fix agents (parallel, separate files):**
  - **notify/dedup:**
    - Notices now use `policy.default.clone(max_line_length=None, utf8=True)` and an explicit `cte`.
    - First attempt failed with `TypeError: '<=' not supported between instances of 'int' and 'NoneType'` (6 tests): with `max_line_length=None` and no `cte`, the stdlib comparison breaks. Fixed by always passing `cte`.
    - The CSV now has exactly 7 columns.
    - Empty-body rows now fall back to comparing on `forwarded_content`.
  - **cli:** send state is restored from `send_log.csv` after every dedup and before queueing. A plain re-run on a populated DB now exits 2.
  - **lead:** pinned `httpx2` (the real import) instead of `httpx`; gitignored `output/send_queue.jsonl`; the MCP client now requires `status == "sent"`.
- **Re-run:** `python main.py --skip-extract` gave 15,317 groups, 36,844 flagged (6,154 compared on forwarded content, only 38 excluded), 36,844 `.eml` files, 0 quoted-printable/base64.
  - The restore step reported `restored 0` because the DB flags had survived the re-flag. A direct DB check confirmed all 3 sent rows still had `notification_sent=1`.
- **Lead tooling failures while filling `sample_queries.sql`:**
  - Splitting on `;` also split comments that contain `;`, giving `sqlite3.OperationalError: near "cc": syntax error`.
  - A `sed`/heredoc edit of the helper script turned `\n` into a real newline, giving `SyntaxError: unterminated string literal`. Rewrote the script with the Write tool.
- **Docs:** docs-agent applied the review's doc fixes and added `docs/mcp_server/` (server setup, placeholders only). The lead filled every result placeholder from `extraction_stats.json`.
- **Screenshot:** the user saved `docs/screenshots/live_send.png`, showing the 3 notices in Gmail.
- **Tests:** 214 passed.
