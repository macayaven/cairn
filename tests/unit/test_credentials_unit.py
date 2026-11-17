"""Credentials (R2): an account at a service, registered once by the operator.

A credential is one row of the workspace's settings (`credential:<name>`), written by the
store alone, holding names and addresses only. Registering one reads nothing and makes no
source; a pass never opens it while no source is made from it. Every name here is made up.
"""

import asyncio
import io
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from cairn.sources import sync_all
from cairn.store import Store
from cairn.store.sources import CREDENTIAL_PREFIX, NOT_YOURS_TO_DISCONNECT

ISSUER = "https://id.example.test/pool-r2c"
FEED_URL = "https://ledger.example.test/cairn/feed"
GMAIL = {"credentials_env": "R2_GOOGLE", "subject": "ada@example.test"}
FEED = {"url": FEED_URL, "secret_env": "R2_FEED_SECRET"}


def dump(db: Path, *tables: str) -> str:
    con = sqlite3.connect(db)
    try:
        if not tables:
            return "\n".join(con.iterdump())
        return repr([con.execute(f"SELECT * FROM {t}").fetchall() for t in tables])
    finally:
        con.close()


def setting_names(db: Path) -> list[str]:
    con = sqlite3.connect(db)
    try:
        return [n for (n,) in con.execute("SELECT name FROM settings ORDER BY name")]
    finally:
        con.close()


@pytest.fixture
def world(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db)
    people = {}
    for name, role in (("Olive", "owner"), ("Ada", "admin"), ("Brook", None)):
        signed = store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER,
            join="default",
        )  # fmt: skip
        if role:
            store.set_role(signed.user.id, role)
        people[name] = SimpleNamespace(
            id=signed.user.id, view=store.for_account(signed.account_id, signed.user.id)
        )
    store.create_project("ledger")
    yield SimpleNamespace(store=store, db=db, **people)
    store.close()


def never_sent(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a request left: {request.url}")


def test_registering_a_credential_writes_and_reads_nothing(world):
    before = dump(world.db, "notes", "sources", "source_items")
    info = world.store.add_credential("ada-mail", "gmail", GMAIL, owner="ada@example.test")
    world.store.add_credential("feed", "connector", FEED)
    assert dump(world.db, "notes", "sources", "source_items") == before
    assert (info.name, info.kind, info.owner, info.owner_name) == (
        "ada-mail",
        "gmail",
        world.Ada.id,
        "Ada",
    )
    assert info.account == "ada@example.test" and info.sources == 0
    assert [c.name for c in world.store.list_credentials()] == ["ada-mail", "feed"]
    assert world.store.list_sources() == []
    assert sorted(n for n in setting_names(world.db) if n.startswith(CREDENTIAL_PREFIX)) == [
        "credential:ada-mail",
        "credential:feed",
    ]


def test_a_credential_without_a_source_is_never_opened(world):
    world.store.add_credential("feed", "connector", FEED)
    world.store.add_credential("ada-mail", "gmail", GMAIL, owner="ada@example.test")
    before = dump(world.db)

    async def passed():
        async with httpx.AsyncClient(transport=httpx.MockTransport(never_sent)) as http:
            return await sync_all(world.store, http=http, env={"R2_FEED_SECRET": "x" * 20})

    assert asyncio.run(passed()) == []
    assert dump(world.db) == before


def test_a_credential_holds_names_and_addresses_only(world):
    before = dump(world.db)
    refusals = [
        (("m", "gmail", {**GMAIL, "label": "to-team"}), "selector"),
        (("m", "gmail", {**GMAIL, "projects": ["ledger"]}), "projects"),
        (("m", "gmail", {**GMAIL, "lookback_days": 3}), "lookback_days"),
        (("m", "gmail", {"subject": "a@example.test"}), "credentials_env"),  # the kind's check
        (("m", "github", {"token_env": "ghp_" + "a1B2" * 9}), "looks like"),  # a key, not a name
        (("m", "nope", GMAIL), "no kind"),
        (("bad name", "gmail", GMAIL), "letters, digits"),
    ]
    for args, said in refusals:
        with pytest.raises(ValueError, match=said):
            world.store.add_credential(*args)
    secret = "r2-made-up-" + "secret-value-9f1e"
    with pytest.raises(ValueError, match="holds a credential"):
        world.store.add_credential(
            "lf", "langfuse",
            {"base_url": f"https://lf.example.test/{secret}", "public_key_env": "R2_P",
             "secret_key_env": "R2_S"},
            secrets=(secret,),
        )  # fmt: skip
    with pytest.raises(ValueError, match="no member"):
        world.store.add_credential("m", "gmail", GMAIL, owner="nobody@example.test")
    assert dump(world.db) == before
    world.store.add_credential("m", "gmail", GMAIL)
    with pytest.raises(ValueError, match="already"):
        world.store.add_credential("M", "gmail", GMAIL)


def test_only_the_command_line_registers_a_credential(world):
    before = dump(world.db)
    for who in (world.Olive, world.Ada, world.Brook):
        with pytest.raises(PermissionError):
            who.view.add_credential("m", "gmail", GMAIL)
    with pytest.raises(PermissionError):
        world.store.narrow(access="read").add_credential("m", "gmail", GMAIL)
    assert dump(world.db) == before


def test_who_sees_which_credentials(world):
    world.store.add_credential("ada-mail", "gmail", GMAIL, owner="ada@example.test")
    world.store.add_credential("bk-mail", "gmail", GMAIL, owner="brook@example.test")
    world.store.add_credential("feed", "connector", FEED)
    every = ["ada-mail", "bk-mail", "feed"]
    assert [c.name for c in world.Olive.view.list_credentials()] == every
    assert [c.name for c in world.Ada.view.list_credentials()] == every
    assert [c.name for c in world.Brook.view.list_credentials()] == ["bk-mail"]
    with pytest.raises(PermissionError):
        world.store.narrow(access="read").list_credentials()
    # A member finds no other's, and does not disconnect their own (R4: the operator
    # registered it for them; an owner disconnects it).
    with pytest.raises(KeyError):
        world.Brook.view.remove_credential("ada-mail")
    with pytest.raises(PermissionError, match=NOT_YOURS_TO_DISCONNECT):
        world.Ada.view.remove_credential("bk-mail")  # an admin, not its owner
    with pytest.raises(PermissionError, match="an owner of the workspace disconnects"):
        world.Brook.view.remove_credential("bk-mail")
    assert world.Olive.view.remove_credential("bk-mail") == []
    assert [c.name for c in world.store.list_credentials()] == ["ada-mail", "feed"]


def test_a_credential_row_is_no_setting_and_never_leaves(world):
    world.store.add_credential("ada-mail", "gmail", GMAIL, owner="ada@example.test")
    world.Ada.view.add_source_from("mail", "ada-mail", {"label": "x"}, {}, None, new_project="m")
    for name in ("credential:ada-mail", "source.credential:mail"):
        with pytest.raises(ValueError):
            world.store.get_setting(name)
        with pytest.raises(ValueError):
            world.store.set_setting(name, "{}")
    out = io.BytesIO()
    world.store.export(out)
    with zipfile.ZipFile(io.BytesIO(out.getvalue())) as archive:
        held = b"".join(archive.read(n) for n in archive.namelist())
    for word in (b"credential", b"R2_GOOGLE", b"ada@example.test"):
        assert word not in held, word


def test_deleting_the_workspace_leaves_no_credential_row(world):
    account = world.store.create_account("Other")
    other = world.store.for_account(account.id)
    other.add_credential("feed", "connector", FEED)
    other.create_project("x")
    other.add_source_from("feed-src", "feed", {}, {}, "x")
    assert world.store.delete_account(account.id) is True
    con = sqlite3.connect(world.db)
    try:
        left = con.execute(
            "SELECT name FROM settings WHERE account_id = ?", (account.id,)
        ).fetchall()
    finally:
        con.close()
    assert left == []


# The command line


def cli(capsys, *argv: str) -> tuple[int, str, str]:
    from cairn.web.cli import main

    code = main(list(argv))
    said = capsys.readouterr()
    return code, said.out, said.err


SOURCE_COMMANDS = {  # each source-* command's arguments, as they were before R2
    "source-add": "ed7727bb00253bed",  # its --config help lists Meet's meeting_code
    "source-disable": "4e3cc837f13cde49",
    "source-enable": "5e5742aec8c8fc81",
    "source-list": "fb3760653800f8cc",
    "source-preview": "c25d211f86cabc1c",
    "source-remove": "d7a024ff4b281f99",
    "source-sync": "a3f614b3f51db203",
}


def test_the_command_line_is_unchanged():
    """The source-* commands take what they took (question 2: their defaults stay); the
    frozen tests S2 and C2 to C11 pin what they do."""
    import hashlib

    from cairn.web import cli as module

    choices = module._parser()._subparsers._group_actions[0].choices
    found = {}
    for name in sorted(c for c in choices if c.startswith("source-")):
        actions = [
            (a.option_strings, a.dest, a.required, a.default, a.help, a.nargs,
             getattr(a.type, "__name__", None))
            for a in choices[name]._actions
        ]  # fmt: skip
        found[name] = hashlib.sha256(repr(actions).encode()).hexdigest()[:16]
    assert found == SOURCE_COMMANDS
    assert {"credential-add", "credential-list", "credential-remove"} <= set(choices)


def test_credential_add_list_and_remove_on_the_command_line(world, capsys, monkeypatch):
    db = str(world.db)
    before = dump(world.db, "notes", "sources", "source_items")
    code, out, err = cli(
        capsys, "credential-add", "--db", db, "--name", "ada-mail", "--kind", "gmail",
        "--config", '{"credentials_env": "R2_GOOGLE", "subject": "ada@example.test"}',
        "--for", "ada@example.test",
    )  # fmt: skip
    assert (code, err) == (0, ""), err
    assert "ada-mail" in out and "reads nothing" in out
    code, out, err = cli(
        capsys, "credential-add", "--db", db, "--name", "feed", "--kind", "connector",
        "--config", f'{{"url": "{FEED_URL}", "secret_env": "R2_FEED_SECRET"}}',
    )  # fmt: skip
    assert code == 0, err
    assert dump(world.db, "notes", "sources", "source_items") == before

    code, out, err = cli(capsys, "credential-list", "--db", db)
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0].startswith("ada-mail: gmail, Ada's") and "ada@example.test" in lines[0]
    assert lines[1].startswith("feed: connector, the workspace's")
    assert "R2_GOOGLE" not in out and "R2_FEED_SECRET" not in out and FEED_URL not in out

    # Refused: a selector, projects, a secret's value, an unknown person; nothing written.
    secret = "r2-made-up-" + "token-value-0b7d"
    monkeypatch.setenv("R2_LEAKY", secret)
    leaky = (
        f'{{"base_url": "https://lf.example.test/{secret}", "public_key_env": "R2_LEAKY",'
        ' "secret_key_env": "R2_LEAKY"}'
    )
    for kind, config, said in (
        ("gmail", '{"credentials_env": "R2_GOOGLE", "label": "to-team"}', "selector"),
        ("gmail", '{"credentials_env": "R2_GOOGLE", "projects": ["ledger"]}', "projects"),
        ("langfuse", leaky, "holds a credential"),
    ):
        code, out, err = cli(
            capsys, "credential-add", "--db", db, "--name", "x", "--kind", kind,
            "--config", config,
        )  # fmt: skip
        assert code == 1 and said in err and secret not in err, err
    code, out, err = cli(
        capsys, "credential-add", "--db", db, "--name", "x", "--kind", "gmail", "--config",
        '{"credentials_env": "R2_GOOGLE"}', "--for", "nobody@example.test",
    )  # fmt: skip
    assert code == 1 and "no member" in err

    code, out, err = cli(capsys, "credential-remove", "--db", db, "--name", "ada-mail")
    assert code == 0 and "ada-mail" in out, err
    code, out, err = cli(capsys, "credential-remove", "--db", db, "--name", "ada-mail")
    assert code == 1 and "no credential named 'ada-mail'" in err
    code, out, err = cli(capsys, "credential-list", "--db", db)
    assert len(out.splitlines()) == 1 and out.startswith("feed:")
