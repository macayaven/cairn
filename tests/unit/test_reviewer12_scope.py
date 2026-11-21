"""Database construction belongs to the command, outside a restricted store view."""

from contextlib import closing

from cairn.store import Store


def test_a_read_only_view_has_no_database_opener():
    with closing(Store(":memory:")) as owner:
        reader = owner.narrow(None, "read")
        assert not hasattr(reader, "open_existing")
