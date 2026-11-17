"""Unit tests for the account pages (cairn.web.account): who may export and delete a
workspace, what the download carries, and what the logs say. The inner loop under
tests/acceptance/test_h5_data.py."""

import asyncio
import io
import logging
import sqlite3
import zipfile

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SESSION_COOKIE, SignIn, create_app
from cairn.store import DEFAULT_ACCOUNT_ID, Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
ISSUER = "https://id.example.test"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def person(store: Store, who: str, join: str) -> tuple[str, str]:
    """A person signed in through a provider: (their session token, their workspace)."""
    signed = store.sign_in(
        "sso", f"subject-{who}", f"{who}@example.test", who.title(), issuer=ISSUER, join=join
    )
    return signed.token, signed.account_id


def client_for(store: Store, token: str) -> TestClient:
    client = TestClient(create_app(store, public_url=BASE), base_url=BASE)
    client.cookies.set(SESSION_COOKIE, token)
    return client


def test_a_member_may_neither_export_nor_delete_the_workspace(store):
    person(store, "olive", "default")  # the default workspace's first person owns it
    token, account = person(store, "mira", "default")  # the next one is a member
    store.create_project("ops")
    with client_for(store, token) as client:
        page = client.get("/account")
        assert page.status_code == 200
        assert 'href="/account/export"' not in page.text
        assert 'action="/account/delete"' not in page.text
        assert client.get("/account/export").status_code == 403
        name = store.get_account(account).name
        r = client.post("/account/delete", data={"confirm": name}, headers=SAME_ORIGIN)
        assert r.status_code == 403 and 'role="alert"' in r.text
    assert [b.name for b in store.list_projects()] == ["ops"]


def test_the_owner_of_a_personal_workspace_deletes_it(store, caplog, monkeypatch):
    def no_compaction(self):
        raise AssertionError("the web view never compacts")

    monkeypatch.setattr(Store, "compact", no_compaction)
    token, account = person(store, "rowan", "personal")
    name = store.get_account(account).name
    with client_for(store, token) as client:
        page = client.get("/account")
        assert 'action="/account/delete"' in page.text and name in page.text
        with caplog.at_level(logging.INFO, logger="cairn.access"):
            r = client.post(
                "/account/delete",
                data={"confirm": name},
                headers=SAME_ORIGIN,
                follow_redirects=False,
            )
        assert r.status_code == 303 and r.headers["location"] == "/"
        assert SESSION_COOKIE in r.headers["set-cookie"]  # the cookie is cleared
    assert store.get_account(account) is None
    (record,) = [r for r in caplog.records if r.action == "workspace.deleted"]
    assert record.account_id == account and record.actor_kind == "session"
    assert account in record.getMessage() and "compact" not in record.getMessage()


def test_an_export_download_is_named_by_the_workspace_id_and_never_cached(store, caplog):
    account = store.create_account('Quote" and\nnewline')
    view = store.for_account(account.id)
    view.create_project("ops")
    key = view.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        with caplog.at_level(logging.INFO, logger="cairn.access"):
            r = client.get("/api/account/export", headers={"x-api-key": key})
        assert r.status_code == 200
        assert r.headers["content-disposition"] == (
            f'attachment; filename="cairn-{account.id}.zip"'
        )
        assert r.headers["cache-control"] == "no-store"
        assert zipfile.ZipFile(io.BytesIO(r.content)).read("projects/ops/notes.jsonl") == b""
    (record,) = [r for r in caplog.records if r.action == "workspace.exported"]
    assert record.actor == "laptop" and record.status == 200


@pytest.mark.parametrize(
    ("brand", "slug"), [("Knowledge Base", "knowledge-base"), ("***", "memory")]
)
def test_under_a_brand_an_export_is_named_by_its_slug(store, brand, slug):
    """B3: the file a signed-in person downloads starts with the brand's slug."""
    view = store.for_account(store.create_account("Field").id)
    key = view.create_key("laptop")
    offline = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    sign_in = SignIn(providers=(GitHub("client-id", "made-up"),), http=offline, join="personal")
    app = create_app(store, public_url=BASE, sign_in=sign_in, brand_name=brand)
    with TestClient(app, base_url=BASE) as client:
        r = client.get("/api/account/export", headers={"x-api-key": key})
    assert r.status_code == 200
    assert r.headers["content-disposition"] == (
        f'attachment; filename="{slug}-{view.account_id}.zip"'
    )


def test_the_default_workspace_page_says_it_cannot_be_deleted(store):
    key = store.create_key("owner")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        page = client.get("/account")
        assert page.status_code == 200 and 'action="/account/delete"' not in page.text
        assert "cannot be deleted" in page.text
        assert 'href="/account/export"' in page.text
    assert store.get_account(DEFAULT_ACCOUNT_ID) is not None


def test_a_wrong_confirmation_keeps_the_page_with_the_reason(store):
    view = store.for_account(store.create_account("Pine").id)
    key = view.create_key("owner")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        r = client.post("/account/delete", data={"confirm": "pine"}, headers=SAME_ORIGIN)
        assert r.status_code == 400 and 'role="alert"' in r.text
        assert 'action="/account/delete"' in r.text  # the form is there to try again
    assert store.get_account(view.account_id) is not None


def test_one_export_at_a_time_per_workspace_streamed_from_a_spooled_file(store, monkeypatch):
    """A second export of the same workspace while one runs is 429 with Retry-After; the
    download streams from a temporary file that spills to disk past a bound."""
    from cairn.memory import Memory
    from cairn.web import account as account_module

    token, _ = person(store, "rowan", "personal")
    real_export = Memory.export
    seen: dict[str, object] = {}

    def export_while_another_asks(self, out):
        seen["spool"] = out
        if "asked" not in seen:  # asked from inside the first, before it reads the store
            seen["asked"] = True
            seen["second"] = client.get("/account/export")
        return real_export(self, out)

    monkeypatch.setattr(Memory, "export", export_while_another_asks)
    with client_for(store, token) as client:
        first = client.get("/account/export")
        assert first.status_code == 200
        assert zipfile.ZipFile(io.BytesIO(first.content)).namelist()[0] == "export.json"
        assert first.headers["content-length"] == str(len(first.content))
        second = seen["second"]
        assert second.status_code == 429 and int(second.headers["retry-after"]) > 0
        assert client.get("/account/export").status_code == 200  # the first one let go
    spool = seen["spool"]
    assert spool._max_size == account_module.SPOOL_BYTES and spool.closed


def test_only_an_owner_exports_or_deletes_a_shared_workspace(store, tmp_path):
    owner_token, account = person(store, "rowan", "personal")
    joined = {}
    for who, role in (("ivo", "admin"), ("mira", "member")):
        token, _ = person(store, who, "personal")
        user_id = store.resolve_session(token).user_id
        with sqlite3.connect(tmp_path / "cairn.db") as con:  # until people can be invited
            con.execute(
                "INSERT INTO memberships (account_id, user_id, role, created_at)"
                " VALUES (?, ?, ?, '2026-11-11T09:00:00Z')",
                (account, user_id, role),
            )
            con.execute("UPDATE sessions SET account_id = ? WHERE user_id = ?", (account, user_id))
        con.close()
        assert store.for_account(account, user_id=user_id).role() == role
        joined[role] = token
    name = store.get_account(account).name
    for role, token in joined.items():
        with client_for(store, token) as client:
            account_page = client.get("/account").text
            assert 'action="/account/delete"' not in account_page
            # an archive holds every project, and an admin does not reach the private ones
            assert 'href="/account/export"' not in account_page, role
            assert "Only an owner of this workspace can export it." in account_page, role
            r = client.get("/account/export")
            assert r.status_code == 403 and "Only an owner" in r.text, role
            r = client.post("/account/delete", data={"confirm": name}, headers=SAME_ORIGIN)
            assert r.status_code == 403 and "Only an owner" in r.text, role
        assert store.get_account(account) is not None
    with client_for(store, owner_token) as client:
        r = client.post(
            "/account/delete", data={"confirm": name}, headers=SAME_ORIGIN, follow_redirects=False
        )
        assert r.status_code == 303
    assert store.get_account(account) is None


def test_a_download_the_client_leaves_lets_go_of_the_workspaces_export(store, monkeypatch):
    """The connection fails after the first chunk: the temporary file is closed and the
    workspace may export again, at once."""
    from cairn.memory import Memory

    token, _ = person(store, "rowan", "personal")
    real_export, spools = Memory.export, []

    def kept(self, out):
        spools.append(out)
        return real_export(self, out)

    monkeypatch.setattr(Memory, "export", kept)
    app = create_app(store, public_url=BASE)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/account/export",
        "raw_path": b"/account/export",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1:8765"),
            (b"cookie", f"{SESSION_COOKIE}={token}".encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8765),
    }
    sent = []

    async def receive():
        if not sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # the client says nothing more

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            raise OSError("connection reset")

    async def leave() -> None:
        with pytest.raises(Exception):  # noqa: B017 (Starlette's ClientDisconnect or OSError)
            await app(scope, receive, send)
        assert spools[0].closed  # at once, not when the loop ends and collects the stream

    asyncio.run(leave())
    assert sent[0]["status"] == 200
    assert spools and spools[0].closed
    with client_for(store, token) as client:
        assert client.get("/account/export").status_code == 200  # not 429
