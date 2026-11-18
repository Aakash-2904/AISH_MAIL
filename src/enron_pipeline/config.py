"""Paths, defaults and environment loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DATASET_ROOT = DATA_DIR  # contains maildir/; source_file paths are relative to this
MAILDIR = DATASET_ROOT / "maildir"
TARBALL = DATA_DIR / "enron_mail_20150507.tar.gz"
DATASET_URL = "https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz"
DB_PATH = DATA_DIR / "enron.db"
SCHEMA_PATH = PROJECT_ROOT / "schema.sql"

OUTPUT_DIR = PROJECT_ROOT / "output"
REPLIES_DIR = OUTPUT_DIR / "replies"
SEND_LOG = OUTPUT_DIR / "send_log.csv"
SEND_QUEUE = OUTPUT_DIR / "send_queue.jsonl"
ERROR_LOG = PROJECT_ROOT / "error_log.txt"
STATS_PATH = PROJECT_ROOT / "extraction_stats.json"
DUP_REPORT = PROJECT_ROOT / "duplicates_report.csv"

DEFAULT_MAILBOXES: tuple[str, ...] = ("lay-k", "skilling-j", "kaminski-v", "farmer-d", "beck-s")
DEDUP_THRESHOLD = 90.0
INSERT_BATCH_SIZE = 1000
PARSE_CHUNKSIZE = 200
# 8 GB laptop: cap default workers at 3 even on bigger CPUs.
DEFAULT_WORKERS = max(1, min(3, (os.cpu_count() or 2) - 1))
DEFAULT_MAX_SEND = 3


@dataclass(frozen=True)
class Settings:
    """Runtime secrets/settings from the environment (.env)."""

    notify_override_to: str | None
    mcp_url: str | None
    mcp_access_token: str | None
    mcp_send_tool: str
    descope_project_id: str | None
    descope_mcp_server_id: str | None
    descope_client_id: str | None
    descope_client_secret: str | None


def load_settings(env_file: Path | None = None) -> Settings:
    """Load settings from ``.env`` (if present) and the process environment."""
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
    except ImportError:  # dotenv optional at import time
        pass
    get = lambda k: (os.environ.get(k) or "").strip() or None  # noqa: E731
    return Settings(
        notify_override_to=get("NOTIFY_OVERRIDE_TO"),
        mcp_url=get("DESCOPE_MCP_URL"),
        mcp_access_token=get("DESCOPE_MCP_ACCESS_TOKEN"),
        mcp_send_tool=get("MCP_SEND_TOOL") or "send_gmail_email",
        descope_project_id=get("DESCOPE_PROJECT_ID"),
        descope_mcp_server_id=get("DESCOPE_MCP_SERVER_ID"),
        descope_client_id=get("DESCOPE_CLIENT_ID"),
        descope_client_secret=get("DESCOPE_CLIENT_SECRET"),
    )
