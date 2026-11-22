"""Shared test helpers extracted from test_schema_step32_unit."""

import json
from contextlib import closing
from pathlib import Path

import cairn.store.schema as schema
from cairn.store import Claim, Store
from cairn.store.base import _index_name
from cairn.store.memory import _write_page_version
from support.store.hold_to_own_projects import two_workspaces
from support.store.hosted_accounts import v27_database
from support.store.store import connect, rows, user_version

NAMES = ("desk", "east", "west", "north", "yard", "out")


def made(db: Path):
    """two_workspaces in a new file at this build's schema. (the second workspace's id, the
    passages kept by page, the tables each word was in before any page cited it.)"""
    with closing(Store(db)) as store:
        other, kept, before = two_workspaces(store)
        return other._account_id, kept, before


def stamped_31(db: Path) -> None:
    with connect(db) as con:
        con.execute("PRAGMA user_version = 31")
        con.execute("UPDATE oldest_build SET version = 31")
        con.commit()
    assert user_version(db) == 31 and floor(db) == [(31,)]


def at_28(db: Path, source: Path) -> None:
    """A file at schema 28 as cairn's own steps left it (built from history), holding the
    workspaces, projects, notes, passages and topic keys of `source`, and each of its page
    versions written as the store writes one, with its search rows and statistics: the
    tables they live in are the same at 28."""
    v27_database(db)
    with connect(db) as con:
        con.execute("BEGIN")
        schema._MIGRATIONS[schema._CREDENTIAL_TOKENS](con)
        con.execute("PRAGMA user_version = 28")
        con.commit()
    with connect(db) as con:
        con.execute("ATTACH DATABASE ? AS made", (str(source),))
        con.execute("BEGIN")
        for table in ("accounts", "projects", "notes", "spans", "page_keys"):
            named = [row[1] for row in con.execute(f"PRAGMA main.table_info({table})")]
            columns = ", ".join(n for n in named if n != "seq")  # an account's: made anew
            con.execute(
                f"INSERT OR IGNORE INTO main.{table} ({columns}) SELECT {columns} FROM made.{table}"
            )
        versions = con.execute(
            "SELECT projects.account_id, p.* FROM made.page_versions AS p"
            " JOIN made.projects ON projects.id = p.project_id ORDER BY 2, 3, 4"
        ).fetchall()
        for account, *row in versions:
            claims = [Claim(**claim) for claim in json.loads(row[5])]
            _write_page_version(con, account, _index_name(account), tuple(row), claims)
        con.commit()
    names = rows(db, "SELECT name FROM projects WHERE account_id != 'acct_aaaaaaaaaaaa'")
    assert sorted(names) == sorted((n,) for n in NAMES) and user_version(db) == 28


def floor(db: Path) -> list[tuple]:
    return rows(db, "SELECT version FROM oldest_build")


def table_rows(db: Path) -> dict[str, list]:
    """Every row of every table but the floor's, as SQLite reads it."""
    with connect(db) as con:
        names = [n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {
            name: sorted(map(repr, con.execute(f'SELECT * FROM "{name}"')))
            for name in names
            if name != "oldest_build"
        }
