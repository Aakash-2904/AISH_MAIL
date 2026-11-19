# MCP discovery: Descope Gmail MCP (Phase 1)

Status: discovery complete. `read_gmail_inbox` verified against real Gmail. `send_gmail_email` **not yet exercised** (no send calls made during discovery).
Redactions: `<PROJECT_ID>` = Descope project ID, `<MCP_SERVER_ID>` = Agentic Identity Hub MCP server ID, `<CLIENT_ID>` = OAuth client ID. No tokens are recorded here.

## 1. Server entry and transport

| Item | Value |
|---|---|
| Claude Code entry | `descope-gmail` (user scope) |
| Transport | `http` (streamable HTTP; server also allows SSE, `disableSse: false`) |
| URL | `https://descope-gmail-mcp.vercel.app/api/mcp` |
| Auth | OAuth, handled by Claude Code (`/mcp` → authenticate) |
| Implementation | Next.js on Vercel, `mcp-handler` (`createMcpHandler` + `withMcpAuth`, `required: true`), Descope node SDK for outbound tokens. Source: `../descope-gmail-mcp/app/api/[transport]/route.ts` |
| Protected-resource metadata | `GET /.well-known/oauth-protected-resource` → `{"resource":"https://descope-gmail-mcp.vercel.app","authorization_servers":["https://api.descope.com/v1/apps/agentic/<PROJECT_ID>/<MCP_SERVER_ID>"]}` |
| Gmail connection | Descope outbound app / Connection id `gmail` (env `GMAIL_CONNECTION_ID`), Google scopes `gmail.readonly` (read) and `gmail.send` (send), requested per call via `fetchTokenByScopes` |

## 2. Tools

### `read_gmail_inbox`
- Input: `{ max_results?: integer 1..20 }` (default 5).
- Needs Gmail scope `gmail.readonly` on the Descope connection.
- Success: text content with JSON `{"emails":[{"from","subject","preview"}]}` (`preview` = Gmail snippet).
- Gmail list error: `isError: true`, text `Error: Gmail API <status>: <message>`.

### `send_gmail_email`
- Input schema (zod):
  - `to`: string, min length 3
  - `subject`: string
  - `body`: string (plain text)
  - `references`: string, optional (a Message-ID such as `<abc@host>`)
- Behaviour, in order:
  1. Missing auth info → `Error: missing authentication.`
  2. MCP token scope must include `google-send`, else `Error: MCP token lacks the google-send scope. Re-authorise the MCP server with google-send.`
  3. CR/LF in `to`, `subject` or `references` → `Error: to/subject/references must not contain line breaks.` (header-injection guard; `body` may contain newlines).
  4. Fetches a Gmail token with `gmail.send` (may return `insufficient_scope`, below).
  5. Builds an RFC 2822 message: `To`, `Subject` (RFC 2047 `=?UTF-8?B?...?=` if non-ASCII), and when `references` is set **both** `References: <ref>` and `In-Reply-To: <ref>`; `MIME-Version: 1.0`, `Content-Type: text/plain; charset="UTF-8"`, `Content-Transfer-Encoding: base64`. Body is base64, 76-char lines. Sent via `POST gmail/v1/users/me/messages/send` with `raw` (base64url).
  6. No `From`, `Date`, `Message-ID`, `Cc`/`Bcc` or arbitrary headers: Gmail sets From/Date/Message-ID.
- Success: `{"status":"sent","gmail_message_id":"...","thread_id":"..."}`.
- Gmail error: `isError: true`, `Error: Gmail API <status>: <message>`. Any other exception: `Error: <message>`.

## 3. Result outcomes (for `SendResult` mapping)

| Outcome | Shape | Suggested `SendResult.status` |
|---|---|---|
| Sent | JSON `status: "sent"` | `sent` |
| Gmail grant missing | `isError: true`, JSON `{"error":"insufficient_scope","message":"Open authorization_url to grant Gmail access, then retry.","authorization_url":"<descope connect url>"}` (after grant the browser lands on `/connection-complete`, "Gmail connected ... retry the tool call") | `error` (surface URL; user grants once, then retry) |
| Connect flow could not start | `Error: could not start the Gmail connection flow (status N).` | `error` |
| Header/scope/auth rejection | `Error: ...` strings above | `error` |
| Gmail API failure | `Error: Gmail API <status>: <message>` | `error` |

There is **no Enchanted Link / `NEEDS_APPROVAL` human-in-the-loop flow** on this server: a send with a valid grant goes out immediately. `pending_approval` / `approval_timeout` are therefore never produced by this server (keep them in the contract but they will be unused).

## 4. MCP access token

Observed claims of the token Claude Code holds (values redacted):
- `iss` = `https://api.descope.com/v1/apps/agentic/<PROJECT_ID>/<MCP_SERVER_ID>`
- `aud` includes `<PROJECT_ID>`, the server URL, and the Claude Code client `<CLIENT_ID>`
- `scope` = `profile email google-read google-send`
- `sub` = Descope user ID (used by the server as `userId` for the Gmail token lookup); `azp` = client ID.

Server-side validation: RS256 signature checked with WebCrypto against `https://api.descope.com/<PROJECT_ID>/.well-known/jwks.json`, `exp`/`nbf` with 5 s skew, `iss` must be in an allow-list that includes the agentic issuer.

Authorization server metadata (`GET <issuer>/.well-known/oauth-authorization-server`, fetched read-only):

| Field | Value |
|---|---|
| `authorization_endpoint` | `https://api.descope.com/oauth2/v1/apps/agentic/<PROJECT_ID>/<MCP_SERVER_ID>/authorize` |
| `token_endpoint` | `https://api.descope.com/oauth2/v1/apps/agentic/<PROJECT_ID>/<MCP_SERVER_ID>/token` |
| `registration_endpoint` | `https://api.descope.com/v1/mgmt/mcp/client/<PROJECT_ID>/<MCP_SERVER_ID>/register` (dynamic client registration supported) |
| `code_challenge_methods_supported` | `S256` (PKCE) |
| `token_endpoint_auth_methods_supported` | `none`, `client_secret_basic`, `client_secret_post` (public clients OK) |
| `scopes_supported` | `google-read`, `google-send`, `offline_access` |
| `grant_types_supported` | `authorization_code`, `refresh_token`, `client_credentials`, CIBA, jwt-bearer, token-exchange |
| `response_types_supported` | `code` |

## 5. Answers to plan Section 2 questions

- **(a) Send tool:** `send_gmail_email(to, subject, body, references?)` on server `descope-gmail`.
- **(b) Extra headers:** only `References` via `references` (also copied to `In-Reply-To`). No `Date` control: Gmail sets `Date`, `From`, `Message-ID`. The notice must carry the original date in the body, not a header.
- **(c) Special results:** returns `insufficient_scope` + `authorization_url` when the Gmail grant is missing. Does **not** return `NEEDS_APPROVAL`.
- **(d) Token outside Claude Code:** the Python client needs its own OAuth authorization-code + PKCE (S256) flow against the agentic endpoints above: register a public client via the registration endpoint (or use a console-created client), open the authorize URL with `scope=google-read google-send` (+ `offline_access` for a refresh token), redirect to a localhost callback, exchange the code at the token endpoint. The Claude Code token is not exportable in a supported way.

## 6. Recommendation

- **Live sends:** use `--send-live --via-claude-code` (plan 8.3). Claude Code already holds a working token with `google-send`, and the first live send happens at Checkpoint 2 by the lead anyway.
- **Python client (primary-path implementation, plan 8.1):** keep it, reading the bearer token from env `DESCOPE_MCP_ACCESS_TOKEN`; a `scripts/descope_login.py` PKCE helper is optional and only if the user approves. Map outcomes per Section 3; treat `insufficient_scope` as a retryable user action, not a crash.
- User still to confirm at Checkpoint 1: server/tool choice, `NOTIFY_OVERRIDE_TO` address, token supply method.

## 7. Issues & fixes during setup

1. **Descope node-sdk `validateSession` rejected agentic tokens.** It requires the last path segment of `iss` to equal the project ID; agentic tokens end in `<MCP_SERVER_ID>`. Fix: replaced with WebCrypto JWT verification against the project JWKS, with an issuer allow-list including the agentic issuer.
2. **Descope `E113301` "Fetching outbound app tokens is not allowed according to access control rules".** Fix: adjusted the access rules on the `gmail` connection in the Descope console, then re-authenticated.
3. **Google `Error 400: redirect_uri_mismatch`.** Fix: added `https://api.descope.com/v1/outbound/oauth/callback` to the Google OAuth web client's authorized redirect URIs.
4. **Gmail API 403 "has not been used in project ... or it is disabled".** It was hidden at first because the read tool returned an empty list on API errors. Fix: enabled the Gmail API in Google Cloud. The tool now surfaces errors as `Error: Gmail API <status>: <message>`.

Verified: `read_gmail_inbox` returned real messages. `send_gmail_email` not yet tested.
