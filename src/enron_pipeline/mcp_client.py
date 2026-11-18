"""MCP client for the Descope Gmail MCP server (built in Wave 2)."""

from __future__ import annotations

from .models import SendResult


class McpGmailClient:
    """Sync wrapper over the official ``mcp`` SDK (streamable HTTP + Bearer token)."""

    def __init__(self, url: str, access_token: str, send_tool: str = "send_gmail_email") -> None:
        raise NotImplementedError

    def send(self, to: str, subject: str, body: str, headers: dict[str, str]) -> SendResult:
        raise NotImplementedError
