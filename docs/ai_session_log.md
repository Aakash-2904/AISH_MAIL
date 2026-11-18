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
