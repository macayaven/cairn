"""Unit tests for cairn.sources.names: the one check every connector runs on a display name
before writing it. Every name and address here is made up."""

import pytest

from cairn.sources import names


@pytest.mark.parametrize(
    "name",
    [
        "ada@example.test",
        "Ada Park (ada@example.test)",
        "ada\uff20example.test",  # FULLWIDTH COMMERCIAL AT
        "ada\ufe6bexample.test",  # SMALL COMMERCIAL AT
        "@",
    ],
)
def test_a_name_holding_an_at_sign_of_any_width_holds_an_address(name):
    assert names.holds_an_address(name)


@pytest.mark.parametrize(
    "name",
    [
        "Ada Park",
        "\uff22\uff52\uff4f\uff4f\uff4b \uff2c\uff41\uff4e\uff45",
        "Cy Moss (by phone)",
        "+1 555-xxx-xx42",
        "",
    ],
)
def test_a_name_without_one_does_not(name):
    assert not names.holds_an_address(name)


def test_the_check_never_changes_the_name_it_is_given():
    """NFKC is for the check only: a name that passes is written as the source gave it."""
    wide = "\uff22\uff52\uff4f\uff4f\uff4b \uff2c\uff41\uff4e\uff45"
    assert names.shown(wide) == wide
    assert names.shown("ada\uff20example.test") is None
    assert names.shown("") is None and names.shown(None) is None
