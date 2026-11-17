"""H10 acceptance tests (frozen): the operator lists the workspaces.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstring of src/cairn/web/cli.py.

Why: `plan-set` and `usage` take a workspace's id, and on an install where people sign in
every workspace has an id nobody chose. Nothing told the operator what the ids are.

- `python -m cairn.server workspace-list --db PATH` prints a JSON array on stdout, one object
  per workspace in the order they were made (the default one first), each with exactly:
  `account` (its id), `name`, `created_at`, `members` (how many people belong to it), `plan`
  (the plan it is held to now, by the rule of `plan_in_force`: the one the operator set with
  `plan-set`; null for the server's default).
- It prints no email address: a workspace is named and its people are counted.
- It changes nothing: the database is as it was.
- Exit 0. With no database at PATH: exit 1, one line on stderr, and none is made. An option
  it does not have: exit 2.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from cairn import server
from cairn.store import Store

GITHUB = "https://github.com"
FIELDS = {"account", "name", "created_at", "members", "plan"}


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def person(store: Store, name: str):
    email = f"{name.split()[0].lower()}@example.test"
    return store.sign_in("github", f"sub-{name}", email, name, issuer=GITHUB, join="personal")


@pytest.fixture
def install(tmp_path):
    """Four workspaces: the default one, Ada's (the operator put it on Solo, two people),
    Brook's (the operator put it on a plan) and Cleo's (on no plan)."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        ada, brook, cleo = (person(store, n) for n in ("Ada Lovelace", "Brook Ng", "Cleo Park"))
        store.set_plan(ada.account_id, "solo")
        store.set_plan(brook.account_id, "team")
        invite = store.for_account(ada.account_id, ada.user.id).invite("dev@example.test")
        dev = person(store, "Dev Patel")
        store.accept_invite(invite.token, dev.user.id)
        accounts = store.list_accounts()
        ids = {"ada": ada.account_id, "brook": brook.account_id, "cleo": cleo.account_id,
               "dev": dev.account_id}  # fmt: skip
    finally:
        store.close()
    return db, accounts, ids


def listed(db: Path, capsys) -> list[dict]:
    capsys.readouterr()
    assert server.main(["workspace-list", "--db", str(db)]) == 0
    said = capsys.readouterr()
    assert said.err == ""
    rows = json.loads(said.out)
    assert isinstance(rows, list)
    return rows


def test_every_workspace_is_listed_with_its_id_in_the_order_it_was_made(install, capsys):
    db, accounts, _ = install
    rows = listed(db, capsys)
    assert [row["account"] for row in rows] == [account.id for account in accounts]
    assert rows[0]["account"] == "acct_default" and len(rows) == 5
    for row, account in zip(rows, accounts, strict=True):
        assert set(row) == FIELDS
        assert (row["name"], row["created_at"]) == (account.name, account.created_at)


def test_each_row_says_the_plan_in_force_and_how_many_people_belong(install, capsys):
    db, _, ids = install
    by_id = {row["account"]: row for row in listed(db, capsys)}

    def held(who: str) -> tuple:
        row = by_id[ids[who]]
        return row["plan"], row["members"]

    assert held("ada") == ("solo", 2)  # the operator's word; Dev joined her
    assert held("brook") == ("team", 1)  # the operator's word
    assert held("cleo") == (None, 1)
    assert held("dev") == (None, 1)
    default = by_id["acct_default"]
    assert (default["plan"], default["members"]) == (None, 0)


def test_it_names_no_address_and_no_provider_reference_and_changes_nothing(install, capsys):
    db, _, _ = install
    before = dump(db)
    capsys.readouterr()
    assert server.main(["workspace-list", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "@" not in out and "example.test" not in out
    assert dump(db) == before


def test_without_a_database_it_says_so_and_makes_none(tmp_path, capsys):
    missing = tmp_path / "nowhere" / "cairn.db"
    assert server.main(["workspace-list", "--db", str(missing)]) == 1
    said = capsys.readouterr()
    assert said.out == "" and len(said.err.strip().splitlines()) == 1
    assert not missing.exists() and not missing.parent.exists()


def test_it_takes_no_other_option(install, capsys):
    db, _, _ = install
    for extra in (["--account", "acct_default"], ["--plans", "plans.yaml"], ["--emails"]):
        with pytest.raises(SystemExit) as stopped:
            server.main(["workspace-list", "--db", str(db), *extra])
        assert stopped.value.code == 2
    capsys.readouterr()
    with pytest.raises(SystemExit) as stopped:
        server.main(["workspace-list"])  # --db is required
    assert stopped.value.code == 2
    capsys.readouterr()
