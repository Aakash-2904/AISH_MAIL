"""Address extraction and Enron-specific clean-up."""

from __future__ import annotations


def normalize_address(raw: str) -> str | None:
    """Clean one address: lowercase, strip quotes/leading dots/brackets
    (``<.lay@enron.com>`` -> ``lay@enron.com``, ``'jeff'@enron.com`` ->
    ``jeff@enron.com``). Exchange-style values (``/O=ENRON/OU=...``) are kept
    as-is (lowercased). Returns None if nothing usable remains."""
    raise NotImplementedError


def parse_address_list(value: str | None) -> list[str]:
    """Parse a (possibly folded) To/Cc/Bcc header into bare normalised
    addresses, de-duplicated with order preserved. Never raises."""
    raise NotImplementedError


def parse_single_address(value: str | None) -> str | None:
    """Parse a From header into one normalised address, or None."""
    raise NotImplementedError


def is_smtp(address: str) -> bool:
    """True for ``local@domain`` addresses, False for Exchange/X.400 values."""
    raise NotImplementedError
