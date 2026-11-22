"""Shared test helpers extracted from test_reviewer12_publication."""

from contextlib import closing

from cairn.store import DEFAULT_ACCOUNT_ID, Store
from cairn.web import cli


def archive(tmp_path):
    source = tmp_path / "in.zip"
    with closing(Store(":memory:")) as store, source.open("wb") as out:
        store.create_project("orchard")
        store.export(out)
    return source


def restore(db, source):
    return cli.main(
        ["import", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--in", str(source)]
    )
