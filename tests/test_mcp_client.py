"""McpGmailClient against a fake MCP session (no network, no real waiting)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from enron_pipeline.mcp_client import McpGmailClient
from enron_pipeline.models import Sender, SendResult

URL = "https://example.invalid/api/mcp"
SEND_SCHEMA_WITH_REFS = {
    "type": "object",
    "properties": {"to": {"type": "string"}, "subject": {"type": "string"},
                   "body": {"type": "string"}, "references": {"type": "string"}},
    "required": ["to", "subject", "body"],
}
SEND_SCHEMA_NO_REFS = {
    "type": "object",
    "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
}


def text_result(text: str, is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], is_error=is_error)


SENT = text_result(json.dumps({"status": "sent", "gmail_message_id": "g-123", "thread_id": "t-1"}))
SCOPE = text_result(json.dumps({"error": "insufficient_scope", "message": "Open authorization_url",
                                "authorization_url": "https://accounts.google.com/o/oauth2/auth?x=1"}), True)


class FakeSession:
    def __init__(self, results, tools=None, fail_on=None):
        self.results = list(results)
        self.tools = tools if tools is not None else [
            SimpleNamespace(name="read_gmail_inbox", input_schema={"type": "object"}),
            SimpleNamespace(name="send_gmail_email", input_schema=SEND_SCHEMA_WITH_REFS),
        ]
        self.fail_on = fail_on
        self.calls: list[tuple[str, dict]] = []
        self.initialized = False

    async def initialize(self):
        if self.fail_on == "initialize":
            raise ConnectionError("boom")
        self.initialized = True

    async def list_tools(self):
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.results.pop(0)


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:  # sync sleep is accepted too
        self.sleeps.append(seconds)
        self.t += seconds


def make_client(session: FakeSession, clock: FakeClock | None = None, **kw) -> tuple[McpGmailClient, list[str]]:
    clock = clock or FakeClock()
    printed: list[str] = []

    @asynccontextmanager
    async def factory():
        yield session

    client = McpGmailClient(URL, "tok", "send_gmail_email", session_factory=factory,
                            sleep=clock.sleep, clock=clock, poll_interval=5.0,
                            announce=printed.append, **kw)
    return client, printed


HEADERS = {"References": "<1.JavaMail.evans@thyme>"}


def test_satisfies_sender_protocol_and_constructor_signature():
    client = McpGmailClient(URL, "tok", "send_gmail_email")
    assert isinstance(client, McpGmailClient)
    _: Sender = client  # structural typing
    with client as c:  # context manager is a cheap no-op
        assert c is client


def test_success_and_references_arg_mapped():
    s = FakeSession([SENT])
    client, _ = make_client(s)
    r = client.send("me@example.com", "Subj", "Body", HEADERS)
    assert r == SendResult(status="sent", detail="g-123")
    assert s.initialized
    name, args = s.calls[0]
    assert name == "send_gmail_email"
    assert args == {"to": "me@example.com", "subject": "Subj", "body": "Body",
                    "references": "<1.JavaMail.evans@thyme>"}
    assert client.tool_schema == SEND_SCHEMA_WITH_REFS


def test_references_prepended_to_body_when_schema_lacks_it():
    tools = [SimpleNamespace(name="send_gmail_email", input_schema=SEND_SCHEMA_NO_REFS)]
    s = FakeSession([SENT], tools=tools)
    client, _ = make_client(s)
    assert client.send("me@example.com", "Subj", "Body", HEADERS).status == "sent"
    args = s.calls[0][1]
    assert "references" not in args
    assert args["body"] == "References: <1.JavaMail.evans@thyme>\n\nBody"


def test_no_references_header_leaves_args_plain():
    s = FakeSession([SENT])
    client, _ = make_client(s)
    client.send("me@example.com", "S", "B", {})
    assert s.calls[0][1] == {"to": "me@example.com", "subject": "S", "body": "B"}


def test_insufficient_scope_then_success():
    s = FakeSession([SCOPE, SCOPE, SENT])
    clock = FakeClock()
    client, printed = make_client(s, clock)
    r = client.send("me@example.com", "S", "B", HEADERS)
    assert r.status == "sent"
    assert len(s.calls) == 3
    assert clock.sleeps == [5.0, 5.0]
    assert len(printed) == 1 and "https://accounts.google.com/o/oauth2/auth?x=1" in printed[0]


def test_insufficient_scope_timeout():
    s = FakeSession([SCOPE] * 50)
    clock = FakeClock()
    client, printed = make_client(s, clock, scope_wait_seconds=20.0)
    r = client.send("me@example.com", "S", "B", HEADERS)
    assert r.status == "error"
    assert "insufficient_scope" in r.error
    assert r.detail == "https://accounts.google.com/o/oauth2/auth?x=1"
    assert len(s.calls) == 5  # t=0,5,10,15,20
    assert len(printed) == 1


def test_is_error_text_becomes_error():
    s = FakeSession([text_result("Error: Gmail API 403: Insufficient Permission", True)])
    client, _ = make_client(s)
    r = client.send("me@example.com", "S", "B", HEADERS)
    assert r == SendResult(status="error", error="Error: Gmail API 403: Insufficient Permission")


def test_exception_never_raises():
    s = FakeSession([], fail_on="initialize")
    client, _ = make_client(s)
    r = client.send("me@example.com", "S", "B", HEADERS)
    assert r.status == "error" and "ConnectionError: boom" in r.error


def test_exception_group_unwrapped():
    @asynccontextmanager
    async def factory():
        raise ExceptionGroup("tg", [RuntimeError("HTTP 401 Unauthorized")])
        yield  # pragma: no cover

    client = McpGmailClient(URL, "tok", session_factory=factory)
    r = client.send("me@example.com", "S", "B", HEADERS)
    assert r.status == "error"
    assert r.error.startswith("RuntimeError: HTTP 401") and "DESCOPE_MCP_ACCESS_TOKEN" in r.error


def test_missing_tool():
    s = FakeSession([SENT], tools=[SimpleNamespace(name="read_gmail_inbox", input_schema={})])
    client, _ = make_client(s)
    r = client.send("me@example.com", "S", "B", HEADERS)
    assert r.status == "error" and "'send_gmail_email' not found" in r.error and "read_gmail_inbox" in r.error
    assert s.calls == []


def test_check_verifies_tool_without_sending():
    s = FakeSession([])
    client, _ = make_client(s)
    assert client.check().status == "dry_run"
    assert s.calls == []


def test_needs_approval_then_sent_and_timeout():
    s = FakeSession([text_result("NEEDS_APPROVAL: link sent"), SENT])
    client, printed = make_client(s)
    assert client.send("me@example.com", "S", "B", HEADERS).status == "sent"
    assert "check me@example.com" in printed[0]

    s2 = FakeSession([text_result("NEEDS_APPROVAL: link sent")] * 10)
    client2, _ = make_client(s2, approval_wait_seconds=10.0)
    assert client2.send("me@example.com", "S", "B", HEADERS).status == "approval_timeout"


def test_real_sdk_names_importable():
    """Guards against the 1.x API name: 2.x exposes streamable_http_client."""
    pytest.importorskip("mcp")
    from mcp import ClientSession  # noqa: F401
    from mcp.client.streamable_http import streamable_http_client  # noqa: F401
