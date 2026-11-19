import {
  protectedResourceHandler,
  metadataCorsOptionsRequestHandler,
} from "mcp-handler";

const DESCOPE_PROJECT_ID = process.env.NEXT_PUBLIC_DESCOPE_PROJECT_ID;
const DESCOPE_BASE_URL =
  process.env.NEXT_PUBLIC_DESCOPE_BASE_URL || "https://api.descope.com";
// Agentic Identity Hub MCP Server ID. When set, clients are sent to the MCP
// server's own authorization server (issuer .../v1/apps/agentic/<project>/<server>),
// which carries the google-read / google-send scopes.
const DESCOPE_MCP_SERVER_ID = process.env.DESCOPE_MCP_SERVER_ID;

const authServerUrl = DESCOPE_MCP_SERVER_ID
  ? `${DESCOPE_BASE_URL}/v1/apps/agentic/${DESCOPE_PROJECT_ID}/${DESCOPE_MCP_SERVER_ID}`
  : `${DESCOPE_BASE_URL}/v1/apps/${DESCOPE_PROJECT_ID}`;

const handler = protectedResourceHandler({
  authServerUrls: [authServerUrl],
});

export const GET = handler;
export const OPTIONS = metadataCorsOptionsRequestHandler();
