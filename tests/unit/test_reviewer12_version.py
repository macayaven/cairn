"""Trace the target connection, including the first schema read."""

import sqlite3
from contextlib import closing

import pytest
from test_repair_says_what_committed_unit import clean_31

from cairn.store import SchemaTooOld, Store


@pytest.mark.parametrize("newer", [True, False], ids=["current", "older"])
def test_the_first_version_read_is_already_inside_the_transaction(tmp_path, monkeypatch, newer):
    db = clean_31(tmp_path)
    if newer:
        Store(db).close()
    reads = []
    connect = sqlite3.connect

    def traced(path, *args, **kwargs):
        con = connect(path, *args, **kwargs)
        if str(path) != ":memory:":

            def trace(statement):
                if statement in ("PRAGMA user_version", "PRAGMA application_id"):
                    reads.append((statement, con.in_transaction))

            con.set_trace_callback(trace)
        return con

    monkeypatch.setattr(sqlite3, "connect", traced)
    if newer:
        with closing(Store.open_existing(db)):
            pass
    else:
        with pytest.raises(SchemaTooOld):
            Store.open_existing(db)
    assert reads == [("PRAGMA user_version", True), ("PRAGMA application_id", True)]
