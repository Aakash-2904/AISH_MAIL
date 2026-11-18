"""Tests for address parsing and Enron clean-up."""
import pytest

from enron_pipeline.addresses import (
    is_smtp,
    normalize_address,
    parse_address_list,
    parse_single_address,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("<.lay@enron.com>", "lay@enron.com"),
        ("'jeff'@enron.com", "jeff@enron.com"),
        (".'john.doe'@enron.com", "john.doe@enron.com"),
        ("e-mail <.'john.doe'@enron.com>", "john.doe@enron.com"),
        (".c.@enron.com", "c@enron.com"),
        ("Kenneth.Lay@ENRON.com", "kenneth.lay@enron.com"),
        ("/O=ENRON/OU=NA/CN=RECIPIENTS/CN=KLAY", "/o=enron/ou=na/cn=recipients/cn=klay"),
        ("undisclosed-recipients:;", None),
        ("", None),
    ],
)
def test_normalize_address(raw: str, expected) -> None:
    assert normalize_address(raw) == expected


def test_folded_list_dedup_preserves_order() -> None:
    value = "b@enron.com,\r\n\ta@enron.com,\n C@Enron.com, b@enron.com"
    assert parse_address_list(value) == ["b@enron.com", "a@enron.com", "c@enron.com"]


def test_display_names_with_commas() -> None:
    value = '"Lay, Kenneth" <kenneth.lay@enron.com>, e-mail <.ben@enron.com>'
    assert parse_address_list(value) == ["kenneth.lay@enron.com", "ben@enron.com"]


def test_mixed_exchange_and_smtp_list() -> None:
    value = "jeff@enron.com, /O=ENRON/OU=NA/CN=RECIPIENTS/CN=JSKILLI"
    assert parse_address_list(value) == ["jeff@enron.com", "/o=enron/ou=na/cn=recipients/cn=jskilli"]


def test_quoted_route_style_address() -> None:
    # Seen in kaminski-v: To: <"the.desk":@enron.com>
    assert parse_address_list('<"the.desk":@enron.com>, <"ft.com.users":@enron.com>') == [
        "the.desk@enron.com",
        "ft.com.users@enron.com",
    ]


def test_empty_and_none() -> None:
    assert parse_address_list(None) == []
    assert parse_address_list("") == []
    assert parse_single_address(None) is None
    assert parse_single_address("undisclosed-recipients:;") is None


def test_single_address() -> None:
    assert parse_single_address('"Lay, Kenneth" <Kenneth.Lay@enron.com>') == "kenneth.lay@enron.com"


def test_is_smtp() -> None:
    assert is_smtp("a@enron.com")
    assert not is_smtp("/o=enron/ou=na/cn=recipients/cn=klay")
    assert not is_smtp("")
