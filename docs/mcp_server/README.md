# MCP server configuration (credentials redacted)

Assignment section 4.1 asks for the MCP server configuration to be included in the project. This folder holds it.

The Gmail MCP server is a **separate Next.js app**, not Python code in this repo:

| Item | Value |
|---|---|
| Source | [`descope-gmail-mcp/`](../../descope-gmail-mcp/README.md) (in this repo), based on Descope's `descope/ai` sample `examples/nextjs-vercel-mcp-server` |
| Deployment | Vercel, endpoint `https://descope-gmail-mcp.vercel.app/api/mcp` |
| Protected-resource metadata | `GET https://descope-gmail-mcp.vercel.app/.well-known/oauth-protected-resource` |
| Tools | `send_gmail_email(to, subject, body, references?)` and `read_gmail_inbox(max_results?)` |
| Server env template | [`env.example`](env.example) (a copy of the server's `.env.example`, placeholders only) |

`send_gmail_email` is the "send_email tool" the assignment refers to. The pipeline's Python client finds it through `MCP_SEND_TOOL` (default `send_gmail_email`).

## Server environment variables

Set these in Vercel (Project → Settings → Environment Variables) or in a local `.env.local` for `pnpm dev`. Real values are never committed.

| Variable | Required | Meaning |
|---|---|---|
| `NEXT_PUBLIC_DESCOPE_PROJECT_ID` | yes | Descope project ID |
| `NEXT_PUBLIC_DESCOPE_BASE_URL` | no | Descope API base, default `https://api.descope.com` |
| `DESCOPE_MCP_SERVER_ID` | yes | Agentic Identity Hub → MCP Servers → your server. Used to build the agentic issuer URL |
| `GMAIL_CONNECTION_ID` | no | Descope Connection that holds the Google tokens, default `gmail` |
| `PUBLIC_BASE_URL` | no | Public URL of the deployment. On Vercel, `VERCEL_PROJECT_PRODUCTION_URL` is used automatically |

## Descope console setup

1. **Google Cloud.**
   - Create an OAuth 2.0 **web** client.
   - Add `https://api.descope.com/v1/outbound/oauth/callback` as an authorized redirect URI. Without it, Google returns `Error 400: redirect_uri_mismatch`.
   - Enable the **Gmail API** in the same Google Cloud project. Without it, calls fail with `Gmail API 403: Gmail API has not been used in project … or it is disabled`.
2. **Connection `gmail`** (Descope outbound app, Google provider).
   - Use the Google client ID and secret from step 1.
   - Scopes: `https://www.googleapis.com/auth/gmail.readonly` and `https://www.googleapis.com/auth/gmail.send`.
   - **Access rules:** allow the user's token to be fetched by the MCP server. Without this, Descope returns `E113301 "Fetching outbound app tokens is not allowed according to access control rules"`.
3. **MCP Server** (Agentic Identity Hub).
   - Create an MCP Server. Its ID goes into `DESCOPE_MCP_SERVER_ID`.
   - Define the agent-facing scopes `google-read` and `google-send`, mapped to `gmail.readonly` and `gmail.send` on the `gmail` Connection.
   - The server supports OAuth 2.1 with PKCE and dynamic client registration, which Claude Code's `/mcp` flow uses.
4. **Deploy** the Next.js app to Vercel with the env vars above, then register it with Claude Code:
   ```bash
   claude mcp add --transport http --scope user descope-gmail https://descope-gmail-mcp.vercel.app/api/mcp
   ```
   Run `/mcp` → `descope-gmail` → authenticate. The first tool call may return `{"error":"insufficient_scope","authorization_url":...}`. Open that URL once to grant Gmail access, then retry.

## Changes made to the Descope sample

- **Token verification.** The sample used `@descope/node-sdk` `validateSession`. Descope agentic (MCP) tokens have an `iss` of the form `…/v1/apps/agentic/<project>/<mcp_server>`. `validateSession` requires the last path segment of the issuer to equal the project ID, so every token was rejected with `unexpected "iss" claim value`. The server now verifies the JWT itself with WebCrypto against the project JWKS (RS256, `exp`/`nbf` with a 5 s skew), with an issuer allow-list that holds both the project issuer and the agentic issuer.
- **Identity from the Bearer token.** The sample read user identity from `_meta` (custom Node agent). The tools now take the user ID and token from `extra.authInfo`, which `withMcpAuth` sets from the Bearer token that Claude Code and the Python client send.
- **`references` argument** on `send_gmail_email`. It sets both `References` and `In-Reply-To`.
- **Header-injection guard.** Line breaks in `to`, `subject` and `references` are rejected.
- **Errors are reported, not swallowed.** Gmail failures return `Error: Gmail API <status>: <message>`. A missing Gmail grant returns `insufficient_scope` with an `authorization_url`.
- Descope's Enchanted Link per-email approval is **not** implemented.

Client-side configuration for this repo is in [`../../mcp_config.json.example`](../../mcp_config.json.example) and [`../../.env.example`](../../.env.example). Discovery notes (token claims, result shapes, IDs redacted) are in [`../mcp_discovery.md`](../mcp_discovery.md).
