"""MCP client for the Descope Gmail MCP server.

Sync wrapper over the official ``mcp`` SDK **2.x** (installed: 2.3.0). In 2.x the
streamable-HTTP transport is ``mcp.client.streamable_http.streamable_http_client``
(the 1.x name ``streamablehttp_client`` no longer exists) and it takes a
pre-configured ``httpx2.AsyncClient`` for headers/auth instead of a ``headers=``
argument. Result models use snake_case attributes (``is_error``,
``input_schema``).

Each ``send`` opens its own short-lived session (connect -> initialize ->
list_tools -> verify tool -> call_tool -> close). Live runs send at most a few
notices, so this costs little and means callers (``main.py``) never have to
close anything. ``send`` never raises: every failure becomes
``SendResult("error", ...)``.

Server behaviour handled (from the server source, ``app/api/[transport]/route.ts``):

* success: text ``{"status":"sent","gmail_message_id":...,"thread_id":...}``
* missing Gmail grant: ``isError`` text ``{"error":"insufficient_scope",
  "authorization_url":...}`` -> print the URL, retry until ``scope_wait_seconds``
* other failures: ``isError`` text such as ``Error: Gmail API 403: ...``
* ``NEEDS_APPROVAL:`` prefix (Enchanted-Link style approval, plan 8.1): **not used
  by this server**; handled anyway -> retry until ``approval_wait_seconds``, then
  ``approval_timeout``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from .models import SendResult

log = logging.getLogger(__name__)

NEEDS_APPROVAL_PREFIX = "NEEDS_APPROVAL:"
REFERENCES_ARG = "references"

SessionFactory = Callable[[], AbstractAsyncContextManager[Any]]
SleepFn = Callable[[float], Awaitable[None] | None]


@asynccontextmanager
async def _http_session(url: str, access_token: str, timeout: float = 30.0) -> AsyncIterator[Any]:
    """Real session: streamable HTTP + ``Authorization: Bearer <token>``."""
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(timeout, read=300)) as http:
        async with streamable_http_client(url, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                yield session


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    """First present attribute (2.x snake_case, then 1.x camelCase, then dict key)."""
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _result_text(result: Any) -> str:
    parts = [_attr(c, "text", default="") for c in (_attr(result, "content", default=None) or [])]
    return "\n".join(p for p in parts if p).strip()


def _as_json(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _describe(exc: BaseException) -> str:
    """Readable message; unwraps anyio/asyncio exception groups to the root cause."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    msg = f"{type(exc).__name__}: {exc}"
    if "401" in str(exc):
        msg += " (MCP access token missing/expired: refresh DESCOPE_MCP_ACCESS_TOKEN)"
    return msg


class McpGmailClient:
    """Sync wrapper over the official ``mcp`` SDK (streamable HTTP + Bearer token).

    ``session_factory``, ``sleep`` and ``clock`` are injectable so tests run
    with a fake session and no real waiting.
    """

    def __init__(
        self,
        url: str,
        access_token: str,
        send_tool: str = "send_gmail_email",
        *,
        session_factory: SessionFactory | None = None,
        scope_wait_seconds: float = 60.0,
        approval_wait_seconds: float = 300.0,
        poll_interval: float = 5.0,
        sleep: SleepFn | None = None,
        clock: Callable[[], float] = time.monotonic,
        announce: Callable[[str], None] = print,
    ) -> None:
        self.url = url
        self.send_tool = send_tool or "send_gmail_email"
        self._session_factory = session_factory or (lambda: _http_session(url, access_token))
        self.scope_wait_seconds = scope_wait_seconds
        self.approval_wait_seconds = approval_wait_seconds
        self.poll_interval = poll_interval
        self._sleep = sleep or asyncio.sleep
        self._clock = clock
        self._announce = announce
        self.tool_schema: dict[str, Any] | None = None
        self._tool_names: list[str] = []

    # -- lifecycle (sessions are per call, so these are cheap no-ops) --------
    def close(self) -> None:
        """Nothing persistent to release; kept for symmetry / ``with`` usage."""

    def __enter__(self) -> McpGmailClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API -----------------------------------------------------------
    def check(self) -> SendResult:
        """Connect, initialize, list tools and verify the send tool (no send)."""
        try:
            return asyncio.run(self._check())
        except Exception as exc:  # noqa: BLE001 - never raise
            return SendResult(status="error", error=_describe(exc))

    def send(self, to: str, subject: str, body: str, headers: dict[str, str]) -> SendResult:
        try:
            return asyncio.run(self._send(to, subject, body, headers or {}))
        except Exception as exc:  # noqa: BLE001 - never raise per item
            return SendResult(status="error", error=_describe(exc))

    # -- internals ------------------------------------------------------------
    async def _check(self) -> SendResult:
        async with self._session_factory() as session:
            schema = await self._prepare(session)
        if schema is None:
            return self._missing_tool_result()
        return SendResult(status="dry_run", detail=f"tool {self.send_tool} available")

    async def _prepare(self, session: Any) -> dict[str, Any] | None:
        """initialize + list_tools; return the send tool's input schema or None."""
        await session.initialize()
        listed = await session.list_tools()
        tools = _attr(listed, "tools", default=None) or []
        self._tool_names = [_attr(t, "name", default="?") for t in tools]
        for t in tools:
            if _attr(t, "name") == self.send_tool:
                schema = _attr(t, "input_schema", "inputSchema", default=None) or {}
                if self.tool_schema is None:
                    log.info("MCP send tool %s schema: %s", self.send_tool, json.dumps(schema, sort_keys=True))
                self.tool_schema = schema
                return schema
        return None

    def _missing_tool_result(self) -> SendResult:
        names = ", ".join(self._tool_names) or "none"
        return SendResult(status="error", error=f"MCP tool {self.send_tool!r} not found (server tools: {names})")

    def _build_args(self, schema: dict[str, Any], to: str, subject: str, body: str,
                    headers: dict[str, str]) -> dict[str, Any]:
        args: dict[str, Any] = {"to": to, "subject": subject, "body": body}
        ref = (headers.get("References") or headers.get("In-Reply-To") or "").strip()
        ref = " ".join(ref.split())  # the server rejects CR/LF in references
        if ref:
            if REFERENCES_ARG in (schema.get("properties") or {}):
                args[REFERENCES_ARG] = ref
            else:  # tool only takes to/subject/body: carry the id in the body
                args["body"] = f"References: {ref}\n\n{body}"
        return args

    async def _wait(self) -> None:
        r = self._sleep(self.poll_interval)
        if inspect.isawaitable(r):
            await r

    async def _send(self, to: str, subject: str, body: str, headers: dict[str, str]) -> SendResult:
        async with self._session_factory() as session:
            schema = await self._prepare(session)
            if schema is None:
                return self._missing_tool_result()
            args = self._build_args(schema, to, subject, body, headers)
            start = self._clock()
            announced: set[str] = set()
            pending_approval = False
            while True:
                result = await session.call_tool(self.send_tool, args)
                text = _result_text(result)
                is_error = bool(_attr(result, "is_error", "isError", default=False))
                data = _as_json(text)

                if text.startswith(NEEDS_APPROVAL_PREFIX):
                    # Not emitted by this server; kept per plan 8.1.
                    pending_approval = True
                    if "approval" not in announced:
                        announced.add("approval")
                        self._announce(f"Approval needed: check {to} for the approval link "
                                       f"({text[len(NEEDS_APPROVAL_PREFIX):].strip()})")
                    if self._clock() - start >= self.approval_wait_seconds:
                        return SendResult(status="approval_timeout",
                                          error=f"approval not granted within {self.approval_wait_seconds:.0f}s",
                                          detail=text)
                    await self._wait()
                    continue

                if is_error and data and data.get("error") == "insufficient_scope":
                    url = str(data.get("authorization_url") or "")
                    if "scope" not in announced:
                        announced.add("scope")
                        self._announce("Gmail access not granted yet. Open this URL to grant it, "
                                       f"then wait (retrying up to {self.scope_wait_seconds:.0f}s):\n  {url}")
                    if self._clock() - start >= self.scope_wait_seconds:
                        return SendResult(status="error",
                                          error=f"insufficient_scope: Gmail access not granted within "
                                                f"{self.scope_wait_seconds:.0f}s; open authorization_url and rerun",
                                          detail=url or None)
                    await self._wait()
                    continue

                if is_error:
                    msg = (data.get("message") or data.get("error")) if data else None
                    return SendResult(status="error", error=str(msg or text or "MCP tool returned isError"))

                gmail_id = data.get("gmail_message_id") if data else None
                detail = str(gmail_id) if gmail_id else (text[:200] or None)
                if pending_approval:
                    log.info("approval granted for %s", subject)
                return SendResult(status="sent", detail=detail)
