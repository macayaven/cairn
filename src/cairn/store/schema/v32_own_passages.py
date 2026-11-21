"""Schema step 32: the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

from cairn.store.schema.shared import _STAMP


def _hold_to_own_projects(con: sqlite3.Connection) -> None:
    """Schema _OLDEST_BUILD to _OWN_PASSAGES: every page that cites, in any version, a passage
    the store holds of a note of another project, of its workspace or another, is held to its
    own project's notes as a forget holds a page (cairn.store.erasure.hold_to_own_projects,
    over the whole database): one new version of the claims its own project's notes hold up,
    every earlier version deleted, its search rows rewritten; a page left with no claim is
    removed. Only an earlier build's save_page, or a program that wrote the tables directly,
    made such a page. It adds and changes no table, and it raises the floor (not in
    _ONLY_ADDS): a build before it may save such a citation again. On a database with no such
    page it makes one search and writes nothing. It writes no audit event; the store's log
    says, for each workspace that had any, how many pages were rewritten and removed."""
    from cairn.store.erasure import hold_to_own_projects  # the store builds on the schema

    hold_to_own_projects(con, None, _STAMP.get()())
