"""Schema step 32: the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3
from functools import partial

from cairn.store.schema.shared import _AFTER, _STAMP


def _hold_to_own_projects(con: sqlite3.Connection) -> None:
    """Schema _OLDEST_BUILD to _OWN_PASSAGES: every page that cites, in any version, a passage
    that no note of its own project holds (another project's, of its workspace or another, or
    one whose note another workspace forgot after the page cited it) is held to its own
    project's notes as a forget holds a page (cairn.store.erasure.hold_to_own_projects,
    over the whole database): one new version of the claims its own project's notes hold up,
    every earlier version deleted, its search rows rewritten; a page left with no claim is
    removed. Only an earlier build's save_page, or a program that wrote the tables directly,
    made such a page. It adds and changes no table, and it raises the floor (not in
    _ONLY_ADDS): a build before it may save such a citation again. On a database with no such
    page it makes one search and writes nothing. It writes no audit event; the store's log
    says, for each workspace that had any, how many pages were rewritten and removed, once
    the step has committed (_AFTER), never before. When a page changed, a compaction is
    marked due in the step's transaction, as a forget marks one: the versions it deleted held
    another project's text."""
    from cairn.store.erasure import (  # the store builds on the schema
        _compaction_due,
        _log_repaired,
        hold_to_own_projects,
    )

    said = _AFTER.get([])  # run alone, outside _upgrade (a test of the step): said to no one
    done = hold_to_own_projects(
        con, None, _STAMP.get()(), lambda *counts: said.append(partial(_log_repaired, *counts))
    )
    if done["rewritten"] or done["removed"]:
        _compaction_due(con, _STAMP.get()())
