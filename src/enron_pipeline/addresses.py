"""Address extraction and Enron-specific clean-up."""

from __future__ import annotations

import re
from email.utils import getaddresses

_EXCHANGE = re.compile(r"^/o=", re.I)
_EXCHANGE_IN_TEXT = re.compile(r"/o=[^,<>\s]+", re.I)
_SMTP = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ANGLE = re.compile(r"<([^<>]*)>")
# Enron oddity: <"the.desk":@enron.com> means the.desk@enron.com
_QUOTED_ROUTE = re.compile(r'"([^"@<>,]+)"\s*:\s*@')
_STRIP_CHARS =" \t\r\n\"'<>.,;:[]()"


def _unfold(value: str) -> str:
    """Join folded header lines into one line."""
    return re.sub(r"\r?\n[ \t]*", " ", value)


def is_smtp(address: str) -> bool:
    """True for ``local@domain`` addresses, False for Exchange/X.400 values."""
    return bool(address) and not _EXCHANGE.match(address) and bool(_SMTP.match(address))


def _clean_smtp(raw: str) -> str | None:
    """Clean a ``local@domain`` string (quotes, leading/trailing dots)."""
    local, _, domain = raw.rpartition("@")
    local = local.strip(_STRIP_CHARS)
    domain = domain.strip(_STRIP_CHARS)
    if not local or not domain:
        return None
    return f"{local}@{domain}".lower()


def normalize_address(raw: str) -> str | None:
    """Clean one address: lowercase, strip quotes/leading dots/brackets
    (``<.lay@enron.com>`` -> ``lay@enron.com``, ``'jeff'@enron.com`` ->
    ``jeff@enron.com``). Exchange-style values (``/O=ENRON/OU=...``) are kept
    as-is (lowercased). Returns None if nothing usable remains."""
    if not raw:
        return None
    s = _unfold(raw).strip()
    m = _ANGLE.search(s)
    if m and m.group(1).strip():
        s = m.group(1).strip()
    s = s.strip(" \t\"'<>")
    if not s:
        return None
    if _EXCHANGE.match(s):
        return s.lower()
    if "@" in s:
        return _clean_smtp(s)
    return None  # bare names / "undisclosed-recipients:;" are not addresses


def _split_exchange(value: str) -> tuple[list[str], str]:
    """Pull Exchange ``/O=...`` values out of a header (they may contain
    characters getaddresses mangles); return (exchange_values, remainder)."""
    found: list[str] = []

    def _take(m: re.Match[str]) -> str:
        found.append(m.group(0).strip())
        return " "

    remainder = _EXCHANGE_IN_TEXT.sub(_take, value)
    return found, remainder


def _candidates(value: str) -> list[str]:
    """Raw address strings from a header value, in order."""
    out: list[str] = []
    for name, addr in getaddresses([value]):
        if addr:
            out.append(addr)
        elif name and "@" in name:
            out.append(name)
    return out


def parse_address_list(value: str | None) -> list[str]:
    """Parse a (possibly folded) To/Cc/Bcc header into bare normalised
    addresses, de-duplicated with order preserved. Never raises."""
    if not value:
        return []
    try:
        text = _QUOTED_ROUTE.sub(r"\1@", _unfold(value))
        if "/o=" in text.lower():
            raw = []
            for part in text.split(","):
                exch, rest = _split_exchange(part)
                raw.extend(exch)
                raw.extend(_candidates(rest))
        else:
            raw = _candidates(text)
        result: list[str] = []
        seen: set[str] = set()
        for item in raw:
            addr = normalize_address(item)
            if addr and addr not in seen:
                seen.add(addr)
                result.append(addr)
        return result
    except Exception:  # noqa: BLE001 - must never raise
        return []


def parse_single_address(value: str | None) -> str | None:
    """Parse a From header into one normalised address, or None."""
    addrs = parse_address_list(value)
    return addrs[0] if addrs else None
