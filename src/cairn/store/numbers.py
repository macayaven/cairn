"""The install's numbers for its operator (Q1 C; cairn.web.stats puts them together): counts and
times only, by workspace, one part at a time. Each part is one statement over every
workspace, grouped by the workspace's id, that reads no note's text, no page's body, and no
name, title, label, address or reason. `part_rows(con, part, now)` runs one on any
connection, a read-only one included (`python -m cairn.server stats`); Store.numbers_part
runs it inside one read of the store, so a server holds the store no longer than each count.

The parts (PARTS): `accounts` (every workspace, with None), `projects`, `notes` (every note
stored), `pages`, `page_versions`, `waiting` (notes stored and not yet filed on a page),
`keys`, `agents` (connected agents: grants with a refresh token alive at `now`),
`newest_note` (of each project's most recently stored note, the latest time stored; the
notes' own order, so it reads one row per project rather than every note), and `sources`
(rows of workspace, kind, how many are on, how many's last run failed). One part is the
install's own, not a workspace's: `compaction` (Privacy 1), the rows of the store's record of
compaction (cairn.store.base: COMPACT_DUE and COMPACTED_AT), each as (name, value,
updated_at), which `compaction_of` reads as Store.compaction_state does."""

import sqlite3

from cairn.store.base import COMPACT_DUE, COMPACTED_AT, DEFAULT_ACCOUNT_ID, REWRITTEN, StoreBase
from cairn.store.models import CompactionState

_QUERIES = {
    "accounts": "SELECT id, NULL FROM accounts ORDER BY seq",
    "projects": "SELECT account_id, COUNT(*) FROM projects GROUP BY account_id",
    "notes": (
        "SELECT b.account_id, COUNT(*) FROM notes AS c INDEXED BY notes_by_text"
        " JOIN projects AS b ON b.id = c.project_id GROUP BY b.account_id"
    ),
    "pages": (
        "SELECT b.account_id, COUNT(*) FROM page_heads AS h JOIN projects AS b"
        " ON b.id = h.project_id GROUP BY b.account_id"
    ),
    "page_versions": (
        "SELECT b.account_id, SUM(n) FROM (SELECT project_id, COUNT(*) AS n FROM page_versions"
        " GROUP BY project_id) AS v JOIN projects AS b ON b.id = v.project_id GROUP BY b.account_id"
    ),
    "waiting": (
        "SELECT b.account_id, COUNT(*) FROM waiting AS w JOIN projects AS b ON b.id = w.project_id"
        " GROUP BY b.account_id"
    ),
    "keys": "SELECT account_id, COUNT(*) FROM api_keys GROUP BY account_id",
    "agents": (
        "SELECT g.account_id, COUNT(*) FROM oauth_grants AS g WHERE EXISTS (SELECT 1 FROM"
        " oauth_tokens WHERE grant_id = g.id AND kind = 'refresh' AND expires_at > :now)"
        " GROUP BY g.account_id"
    ),
    "newest_note": (
        "SELECT b.account_id, MAX(c.created_at) FROM (SELECT MAX(seq) AS seq FROM notes"
        " INDEXED BY notes_by_text GROUP BY project_id) AS m"
        " JOIN notes AS c ON c.seq = m.seq JOIN projects AS b ON b.id = c.project_id"
        " GROUP BY b.account_id"
    ),
    "sources": (
        "SELECT account_id, kind, SUM(enabled), SUM(last_error IS NOT NULL) FROM sources"
        " GROUP BY account_id, kind ORDER BY account_id, kind"
    ),
    "compaction": (
        f"SELECT name, value, updated_at FROM settings WHERE account_id = '{DEFAULT_ACCOUNT_ID}'"
        f" AND name IN ('{COMPACT_DUE}', '{COMPACTED_AT}')"
    ),
}
PARTS = tuple(_QUERIES)


def part_rows(con: sqlite3.Connection, part: str, now: str) -> list[tuple]:
    """The rows of one part (PARTS) on `con`, one statement; `now` (an ISO time as the store
    writes one) is what a connected agent's refresh token must outlive. ValueError: no such
    part."""
    if part not in _QUERIES:
        raise ValueError(f"no such part of the numbers: {part!r}")
    query = _QUERIES[part]
    return con.execute(query, {"now": now} if ":now" in query else {}).fetchall()


def compaction_of(rows: list[tuple]) -> CompactionState:
    """The install's record of compaction from the rows of the part `compaction`, read as
    Store.compaction_state reads them."""
    found = {name: (value, updated) for name, value, updated in rows}
    due, done = found.get(COMPACT_DUE), found.get(COMPACTED_AT)
    return CompactionState(
        deleted_at=due[0] if due else None,
        compacted_at=done[0] if done else None,
        rewritten=bool(due) and due[1] == REWRITTEN,
    )


class NumbersMethods(StoreBase):
    def numbers_part(self, part: str) -> list[tuple]:
        """One part of the install's numbers (cairn.store.numbers.PARTS), every workspace's,
        in one read of its own. The operator's: refused on a view acting for a person or
        narrowed (cairn.store.reach). ValueError: no such part."""
        if part not in PARTS:
            raise ValueError(f"no such part of the numbers: {part!r}")
        with self._read() as con:
            return part_rows(con, part, self._stamp())

    def database_file(self) -> str:
        """The path of the database file this store opened, as SQLite names it; "" for a
        database in memory. The operator's, as numbers_part."""
        with self._read() as con:
            rows = con.execute("PRAGMA database_list").fetchall()
        return next((row[2] or "" for row in rows if row[1] == "main"), "")
