import { createMcpHandler, withMcpAuth } from "mcp-handler";
import { z } from "zod";
import DescopeClient from "@descope/node-sdk";

const DESCOPE_PROJECT_ID = process.env.NEXT_PUBLIC_DESCOPE_PROJECT_ID;
const DESCOPE_BASE_URL =
  process.env.NEXT_PUBLIC_DESCOPE_BASE_URL || "https://api.descope.com";

// Descope Connection ID for Gmail (Agentic Identity Hub > Connections).
const GMAIL_CONNECTION_ID = process.env.GMAIL_CONNECTION_ID || "gmail";

// Public URL of this deployment, used as the redirect after the user grants Gmail access.
const PUBLIC_BASE_URL =
  process.env.PUBLIC_BASE_URL ||
  (process.env.VERCEL_PROJECT_PRODUCTION_URL
    ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`
    : "http://localhost:3000");

const GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly";
const GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send";

if (!DESCOPE_PROJECT_ID) {
  throw new Error("DESCOPE_PROJECT_ID environment variable is required");
}

type ToolResult = {
  content: { type: "text"; text: string }[];
  isError?: boolean;
};

const text = (t: string, isError = false): ToolResult => ({
  content: [{ type: "text", text: t }],
  ...(isError ? { isError: true } : {}),
});

/**
 * Descope client that acts on behalf of the calling user: the user's MCP access
 * token (not a management key) authorises fetching their Gmail token.
 */
function userScopedClient(userToken: string) {
  return DescopeClient({
    projectId: DESCOPE_PROJECT_ID!,
    baseUrl: DESCOPE_BASE_URL,
    hooks: {
      beforeRequest: (requestConfig: any) => {
        requestConfig.headers = {
          ...requestConfig.headers,
          Authorization: `Bearer ${DESCOPE_PROJECT_ID}:${userToken}`,
        };
        return requestConfig;
      },
    },
  });
}

/**
 * Returns a Gmail access token for the user, or a tool result carrying an
 * `insufficient_scope` error with the URL the user must open to grant access.
 */
async function getGmailToken(
  userToken: string,
  userId: string,
  scopes: string[]
): Promise<{ token: string } | { result: ToolResult }> {
  const client = userScopedClient(userToken);
  const tokenResult =
    await client.management.outboundApplication.fetchTokenByScopes(
      GMAIL_CONNECTION_ID,
      userId,
      scopes
    );
  const accessToken = tokenResult.ok ? tokenResult.data?.accessToken : undefined;
  if (accessToken) return { token: accessToken };

  const connectRes = await fetch(`${DESCOPE_BASE_URL}/v1/mgmt/outbound/app/connect`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${DESCOPE_PROJECT_ID}:${userToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      appId: GMAIL_CONNECTION_ID,
      options: { redirectUrl: `${PUBLIC_BASE_URL}/connection-complete`, scopes },
    }),
  });
  const connectBody = await connectRes.text();
  if (!connectRes.ok) console.error(`outbound connect ${connectRes.status}:`, connectBody);
  let connectData: any = {};
  try {
    connectData = connectRes.ok ? JSON.parse(connectBody) : {};
  } catch {}
  if (typeof connectData?.url !== "string" || !connectData.url) {
    return {
      result: text(
        `Error: could not start the Gmail connection flow (status ${connectRes.status}).`,
        true
      ),
    };
  }
  return {
    result: text(
      JSON.stringify({
        error: "insufficient_scope",
        message: "Open authorization_url to grant Gmail access, then retry.",
        authorization_url: connectData.url,
      }),
      true
    ),
  };
}

const hasCrLf = (s: string) => /[\r\n]/.test(s);

/** RFC 2047-encode a header value if it contains non-ASCII characters. */
function encodeHeader(value: string): string {
  return /^[\x20-\x7e]*$/.test(value)
    ? value
    : `=?UTF-8?B?${Buffer.from(value, "utf8").toString("base64")}?=`;
}

/** Build a base64url RFC 2822 message for the Gmail API `raw` field. */
function buildRawMessage(
  to: string,
  subject: string,
  body: string,
  references?: string
): string {
  const headers = [
    `To: ${to}`,
    `Subject: ${encodeHeader(subject)}`,
    ...(references ? [`References: ${references}`, `In-Reply-To: ${references}`] : []),
    "MIME-Version: 1.0",
    'Content-Type: text/plain; charset="UTF-8"',
    "Content-Transfer-Encoding: base64",
  ];
  const encodedBody = Buffer.from(body, "utf8")
    .toString("base64")
    .replace(/.{1,76}/g, "$&\r\n");
  return Buffer.from(`${headers.join("\r\n")}\r\n\r\n${encodedBody}`, "utf8").toString(
    "base64url"
  );
}

function authOf(extra: any): { userToken: string; userId: string; scopes: string[] } | null {
  const auth = extra?.authInfo;
  const userId = auth?.extra?.userId as string | undefined;
  if (!auth?.token || !userId) return null;
  return { userToken: auth.token, userId, scopes: auth.scopes ?? [] };
}

const handler = createMcpHandler(
  (server) => {
    server.tool(
      "read_gmail_inbox",
      "Read the latest emails (sender, subject, preview) from the user's Gmail inbox.",
      { max_results: z.number().int().min(1).max(20).optional() },
      async ({ max_results }, extra) => {
        const auth = authOf(extra);
        if (!auth) return text("Error: missing authentication.", true);
        try {
          const got = await getGmailToken(auth.userToken, auth.userId, [GMAIL_READ]);
          if ("result" in got) return got.result;
          const h = { Authorization: `Bearer ${got.token}` };
          const listRes = await fetch(
            `https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=${max_results ?? 5}`,
            { headers: h }
          );
          const list: any = await listRes.json();
          if (!listRes.ok) {
            return text(
              `Error: Gmail API ${listRes.status}: ${list?.error?.message ?? JSON.stringify(list)}`,
              true
            );
          }
          const emails = [];
          for (const m of list.messages ?? []) {
            const msg: any = await (
              await fetch(
                `https://gmail.googleapis.com/gmail/v1/users/me/messages/${m.id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject`,
                { headers: h }
              )
            ).json();
            const hdr = (n: string) =>
              msg.payload?.headers?.find((x: any) => x.name === n)?.value ?? "";
            emails.push({ from: hdr("From"), subject: hdr("Subject"), preview: msg.snippet });
          }
          return text(JSON.stringify({ emails }, null, 2));
        } catch (e: any) {
          return text(`Error: ${e?.message ?? String(e)}`, true);
        }
      }
    );

    server.tool(
      "send_gmail_email",
      "Send a plain-text email from the user's Gmail account. Optional `references` sets the References/In-Reply-To headers (a Message-ID such as <abc@host>).",
      {
        to: z.string().min(3),
        subject: z.string(),
        body: z.string(),
        references: z.string().optional(),
      },
      async ({ to, subject, body, references }, extra) => {
        const auth = authOf(extra);
        if (!auth) return text("Error: missing authentication.", true);
        if (!auth.scopes.includes("google-send")) {
          return text(
            "Error: MCP token lacks the google-send scope. Re-authorise the MCP server with google-send.",
            true
          );
        }
        if ([to, subject, references ?? ""].some(hasCrLf)) {
          return text("Error: to/subject/references must not contain line breaks.", true);
        }
        try {
          const got = await getGmailToken(auth.userToken, auth.userId, [GMAIL_SEND]);
          if ("result" in got) return got.result;
          const res = await fetch(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            {
              method: "POST",
              headers: {
                Authorization: `Bearer ${got.token}`,
                "Content-Type": "application/json",
              },
              body: JSON.stringify({ raw: buildRawMessage(to, subject, body, references) }),
            }
          );
          const data: any = await res.json().catch(() => ({}));
          if (!res.ok) {
            return text(
              `Error: Gmail API ${res.status}: ${data?.error?.message ?? "send failed"}`,
              true
            );
          }
          return text(
            JSON.stringify({ status: "sent", gmail_message_id: data.id, thread_id: data.threadId })
          );
        } catch (e: any) {
          return text(`Error: ${e?.message ?? String(e)}`, true);
        }
      }
    );
  },
  undefined,
  {
    // basePath is the path to the MCP server. Incorrectly setting this will affect tool discovery by the client.
    basePath: "/api",
    verboseLogs: true,
    maxDuration: 60,
    disableSse: false,
  }
);

// Tokens from the Agentic Identity Hub MCP server carry
// iss = .../v1/apps/agentic/<project>/<mcpServerId>. The node SDK's
// validateSession() only accepts an iss whose last path segment is the project
// ID, so it rejects these tokens. Verify them ourselves against the project's
// JWKS (the same keys sign both kinds of token) and accept either issuer.
const DESCOPE_MCP_SERVER_ID = process.env.DESCOPE_MCP_SERVER_ID;
const ALLOWED_ISSUERS = new Set(
  [
    DESCOPE_PROJECT_ID,
    `${DESCOPE_BASE_URL}/${DESCOPE_PROJECT_ID}`,
    `${DESCOPE_BASE_URL}/v1/apps/${DESCOPE_PROJECT_ID}`,
    DESCOPE_MCP_SERVER_ID &&
      `${DESCOPE_BASE_URL}/v1/apps/agentic/${DESCOPE_PROJECT_ID}/${DESCOPE_MCP_SERVER_ID}`,
  ].filter(Boolean) as string[]
);

let jwksCache: { keys: Map<string, CryptoKey>; fetchedAt: number } | null = null;

const getSigningKey = async (kid: string): Promise<CryptoKey> => {
  const fresh = jwksCache && Date.now() - jwksCache.fetchedAt < 10 * 60_000;
  if (!fresh || !jwksCache!.keys.has(kid)) {
    const res = await fetch(`${DESCOPE_BASE_URL}/${DESCOPE_PROJECT_ID}/.well-known/jwks.json`);
    if (!res.ok) throw new Error(`JWKS fetch failed: ${res.status}`);
    const { keys } = (await res.json()) as { keys: (JsonWebKey & { kid: string })[] };
    const map = new Map<string, CryptoKey>();
    for (const jwk of keys) {
      map.set(
        jwk.kid,
        await crypto.subtle.importKey(
          "jwk",
          jwk,
          { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
          false,
          ["verify"]
        )
      );
    }
    jwksCache = { keys: map, fetchedAt: Date.now() };
  }
  const key = jwksCache!.keys.get(kid);
  if (!key) throw new Error("no matching signing key");
  return key;
};

const b64urlDecode = (s: string) => Buffer.from(s, "base64url");

const verifyDescopeJwt = async (jwt: string): Promise<Record<string, unknown>> => {
  const [h, p, sig] = jwt.split(".");
  if (!h || !p || !sig) throw new Error("malformed JWT");
  const header = JSON.parse(b64urlDecode(h).toString("utf8"));
  if (header.alg !== "RS256") throw new Error(`unsupported alg ${header.alg}`);
  const ok = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    await getSigningKey(header.kid),
    b64urlDecode(sig),
    new TextEncoder().encode(`${h}.${p}`)
  );
  if (!ok) throw new Error("bad signature");
  const payload = JSON.parse(b64urlDecode(p).toString("utf8"));
  const now = Math.floor(Date.now() / 1000);
  if (typeof payload.exp === "number" && payload.exp + 5 < now) throw new Error("token expired");
  if (typeof payload.nbf === "number" && payload.nbf - 5 > now) throw new Error("token not yet valid");
  if (!ALLOWED_ISSUERS.has(payload.iss)) throw new Error(`unexpected iss ${payload.iss}`);
  return payload;
};

const verifyToken = async (req: Request, bearerToken?: string) => {
  if (!bearerToken) return undefined;

  try {
    const token = await verifyDescopeJwt(bearerToken);

    const scopeString = token.scope as string;
    const scopes = scopeString ? scopeString.split(" ") : [];
    const clientId = token.azp as string;

    return {
      token: bearerToken,
      scopes: scopes,
      clientId: clientId,
      extra: {
        userId: token.sub,
      },
    };
  } catch (error) {
    console.error("Token validation failed:", error);
    return undefined;
  }
};

const authHandler = withMcpAuth(handler, verifyToken, {
  required: true,
  resourceMetadataPath: "/.well-known/oauth-protected-resource",
});

export { authHandler as GET, authHandler as POST };
