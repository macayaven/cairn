"""R1 acceptance tests: an owner sees what a source would bring before switching it on.

A preview opens a source that already exists as a run would (its kind's `open`, the app's
HTTP client, the process's environment) and pulls once: an off source from no cursor, from
`days` ago (1 to 30, 7 unless given); an on source from its stored cursor and its own switch-on,
so it shows the next run. It answers the count and the titles (at most 20), how many of those
items the source already wrote, and how many are for a project the caller does not reach. It
takes no claim, moves no cursor, records no item, writes no audit event and no note: the
database is the same byte for byte. Who may preview a source is exactly who may switch it on
today; an agent never may. A title that holds a credential is shown as held. A source whose
service cannot be read says why, and a source whose credential variable is unset sends nothing.

Every name here is made up; every request goes to an httpx.MockTransport.
"""

import asyncio
import base64
import hashlib
import html as html_text
import json
import logging
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind
from cairn.sources.runner import HELD_TITLE, preview_source
from cairn.store import Store
from cairn.store.sources import NOT_A_SOURCE_MANAGER, OWNER_DECIDES
from cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
ISSUER = "https://id.example.test/pool-r1"
REDIRECT = "http://127.0.0.1:9/callback"
URL = "https://ledger.example.test/cairn/feed"
SECRET_ENV = "R1_FEED_SECRET"
SECRET = "r1" + "-made-up-" + "feed-" + "secret-7c2e4b"  # never a real secret
GH_ENV = "R1_GH_TOKEN"
NOW = datetime.now(UTC).replace(microsecond=0)  # the web preview reads the real time
DAY, MINUTE = timedelta(days=1), timedelta(minutes=1)
ANSWER = {"source", "days", "read", "count", "titles", "more", "written_before", "out_of_reach"}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def z(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def entry(n: int, at: datetime, **fields) -> dict:
    """An item as the ledger's connector gives it."""
    given = {
        "id": f"LED-{n}",
        "text": f"# Ledger entry {n}\n\nEntry {n} closes the month's account {n}.\n",
        "title": f"Ledger entry {n}",
        "at": z(at),
    }
    return {**given, **fields}


class Feed:
    """A connector of protocol 1, served through httpx.MockTransport: it checks the bearer,
    gives the items at or after the request's `since`, all in one answer, and keeps every
    request it gets."""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.requests: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if request.headers.get("authorization") != f"Bearer {SECRET}":
            return httpx.Response(401, json={"error": "the secret was refused"})
        since = datetime.fromisoformat(body["since"].replace("Z", "+00:00"))
        given = [
            i for i in self.items if datetime.fromisoformat(i["at"].replace("Z", "+00:00")) >= since
        ]
        return httpx.Response(
            200, json={"protocol": 1, "items": given, "cursor": "after:all", "more": False}
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


def never_sent(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a preview sent a request it must not: {request.url}")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A workspace with three projects, a connector source "ledger" (off, naming "accounts"),
    and the people who may or may not preview it."""
    monkeypatch.setenv(SECRET_ENV, SECRET)
    monkeypatch.delenv(GH_ENV, raising=False)
    clock = Clock(NOW)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    for name in ("accounts", "treasury"):
        store.create_project(name)
    store.create_project("board", everyone="none")  # an admin does not reach it
    feed = Feed()
    store.add_source(
        "ledger", "connector", {"url": URL, "secret_env": SECRET_ENV, "projects": ["accounts"]}
    )
    yield SimpleNamespace(store=store, db=db, clock=clock, feed=feed)
    store.close()


def person(world, name: str, role: str | None = None) -> SimpleNamespace:
    signed = world.store.sign_in(
        "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER, join="default"
    )
    if role is not None:
        world.store.set_role(signed.user.id, role)
    view = world.store.for_account(signed.account_id, signed.user.id)
    return SimpleNamespace(
        id=signed.user.id,
        name=name,
        cookie={"cookie": f"{session_cookie()}={signed.token}"},
        view=view.acting_as("session", name, signed.user.id),
    )


def agent_token(world, who) -> str:
    client = world.store.register_client("r1-laptop", [REDIRECT])
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    code = who.view.create_code(client.id, REDIRECT, challenge)
    return world.store.redeem_code(code, client.id).access_token


@pytest.fixture
def web(world):
    """The app, its sources read through the stand-in feed."""
    world.ada = person(world, "Ada")  # the owner
    world.brook = person(world, "Brook")  # a member
    world.cy = person(world, "Cy", role="admin")
    app = create_app(world.store, public_url=BASE, source_http=world.feed.client)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, app=app)


def preview(web, name: str, headers: dict, body: dict | None = None) -> httpx.Response:
    return web.client.post(
        f"/api/integrations/{name}/preview", headers=headers, json={} if body is None else body
    )


def page_text(markup: str) -> str:
    return " ".join(html_text.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


# The six tests


def test_preview_shows_count_and_titles_and_stores_nothing(world, web, caplog):
    # The source ran once while it was on and wrote entry 1; then it was switched off.
    world.clock.now = NOW - 5 * DAY
    world.store.enable_source("ledger")
    world.feed.items = [entry(1, NOW - 4 * DAY)]
    world.clock.now = NOW - 4 * DAY + MINUTE
    ran = asyncio.run(_run(world))
    assert ran.saved == 1
    world.store.disable_source("ledger")
    world.feed.items += [entry(n, NOW - DAY + n * MINUTE) for n in range(2, 25)]  # 23 new ones
    world.feed.requests.clear()
    key = world.store.create_key("desk")  # a key acting for no person: the operator's

    before = dump(world.db)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        r = preview(web, "ledger", {"x-api-key": key})
    assert r.status_code == 200, r.text
    (record,) = [r for r in caplog.records if r.name == "cairn.access"]  # one, and no title
    assert (record.action, record.status) == ("source_preview", 200)
    assert "Ledger entry" not in repr(sorted(record.__dict__.items())) + record.getMessage()
    said = r.json()
    assert set(said) == ANSWER
    assert (said["source"], said["days"], said["read"]) == ("ledger", 7, True)
    assert said["count"] == 23 and said["written_before"] == 1 and said["out_of_reach"] == 0
    assert said["titles"] == [f"Ledger entry {n}" for n in range(2, 22)]  # at most 20
    assert said["more"] == 3
    assert dump(world.db) == before  # no claim, cursor, item, audit event or note
    assert world.feed.requests and world.feed.requests[0]["cursor"] is None
    sent_since = datetime.fromisoformat(world.feed.requests[0]["since"].replace("Z", "+00:00"))
    assert NOW - 7 * DAY - MINUTE <= sent_since <= datetime.now(UTC) - 7 * DAY

    # The runner itself, as the command line uses it: the same answer, nothing stored.
    world.clock.now = NOW
    shown = asyncio.run(_preview(world, world.store))
    assert (shown.read, shown.count, shown.written_before, shown.more) == (True, 23, 1, 3)
    assert dump(world.db) == before
    assert world.store.get_source("ledger").enabled is False


def test_preview_reads_the_window_not_the_switch_on(world):
    world.feed.items = [entry(2, NOW - 2 * DAY), entry(4, NOW - 4 * DAY)]
    three = asyncio.run(_preview(world, world.store, days=3))
    assert three.titles == ["Ledger entry 2"] and three.count == 1
    assert world.feed.requests[-1]["since"] == z(NOW - 3 * DAY)
    five = asyncio.run(_preview(world, world.store, days=5))
    assert five.titles == ["Ledger entry 2", "Ledger entry 4"]
    assert asyncio.run(_preview(world, world.store)).days == 7  # unless given
    for wrong in (0, 31, True, 2.5, "3"):
        with pytest.raises(ValueError, match="1 to 30"):
            asyncio.run(_preview(world, world.store, days=wrong))

    # An on source shows its next run: from its stored cursor and its own switch-on.
    world.clock.now = NOW - 3 * DAY - MINUTE
    world.store.enable_source("ledger")
    world.clock.now = NOW - DAY
    assert asyncio.run(_run(world)).saved == 1  # entry 2
    world.feed.items.append(entry(5, NOW - DAY + MINUTE))
    world.clock.now = NOW
    before = dump(world.db)
    nxt = asyncio.run(_preview(world, world.store, days=1))
    assert world.feed.requests[-1]["cursor"] == "after:all"  # where the last run stopped
    assert world.feed.requests[-1]["since"] == z(NOW - 3 * DAY - MINUTE)
    assert (nxt.count, nxt.written_before, nxt.titles) == (1, 1, ["Ledger entry 5"])
    assert dump(world.db) == before


def test_preview_is_for_who_may_switch_it_on(world, web):
    world.feed.items = [entry(1, NOW - DAY)]
    world.store.add_source(
        "minutes", "connector", {"url": URL, "secret_env": SECRET_ENV, "projects": ["board"]}
    )
    cy_key = world.cy.view.create_key("cy-desk")
    ada_key = world.ada.view.create_key("ada-desk")
    accounts = world.store.get_project("accounts").id
    narrowed = world.store.create_key("narrow", projects=[accounts])
    reading = world.store.create_key("reading", access="read")
    token = agent_token(world, world.ada)
    before = dump(world.db)

    refusals = [
        ({"x-api-key": cy_key}, "minutes", 403, OWNER_DECIDES),  # writes where Cy does not reach
        ({"x-api-key": narrowed}, "ledger", 403, None),
        ({"x-api-key": reading}, "ledger", 403, None),
        ({"x-api-key": ada_key}, "nope", 404, None),
        ({"authorization": f"Bearer {token}"}, "ledger", 401, None),  # never an agent
    ]
    for headers, name, status, error in refusals:
        r = preview(web, name, headers)
        assert r.status_code == status, (name, r.status_code, r.text)
        if error is not None:
            assert r.json() == {"error": error}
    assert world.feed.requests == []
    member = web.client.post(
        "/integrations/ledger/preview", headers={**SAME_ORIGIN, **world.brook.cookie}
    )
    assert member.status_code == 403
    assert world.feed.requests == [] and dump(world.db) == before
    with pytest.raises(PermissionError, match=NOT_A_SOURCE_MANAGER):
        asyncio.run(_preview(world, world.brook.view))
    with pytest.raises(PermissionError):
        asyncio.run(_preview(world, world.store.narrow([accounts])))

    # Who may switch it on may preview it: the owner, an admin where every member reaches.
    for headers in ({"x-api-key": ada_key}, {"x-api-key": cy_key}):
        r = preview(web, "ledger", headers, {"days": 3})
        assert r.status_code == 200 and r.json()["read"] is True, r.text
    assert preview(web, "minutes", {"x-api-key": ada_key}).status_code == 200
    assert dump(world.db) == before


def test_preview_never_shows_a_credential(world, web, monkeypatch):
    stand_in = SourceKind(check=_stand_in_check, open=_stand_in_open, via="r1", starts_off=True)
    monkeypatch.setitem(KINDS, "r1_stand_in", stand_in)
    world.store.add_source("desk", "r1_stand_in", {"projects": [], "token_env": SECRET_ENV})
    key = world.store.create_key("desk")
    r = preview(web, "desk", {"x-api-key": key})
    assert r.status_code == 200, r.text
    said = r.json()
    assert said["count"] == 3 and said["titles"][0] == "Plain title"
    assert said["titles"][1:] == [HELD_TITLE, HELD_TITLE]  # in the title, and in the text
    assert SECRET not in r.text and SECRET[:20] not in r.text
    assert HELD_TITLE == "held: it holds a credential"
    direct = asyncio.run(_preview(world, world.store, name="desk"))
    assert direct.titles == said["titles"]
    page = web.client.post(
        "/integrations/desk/preview", headers={**SAME_ORIGIN, **world.ada.cookie}
    )
    assert (
        page.status_code == 200 and SECRET not in page.text and HELD_TITLE in page_text(page.text)
    )


def test_preview_of_an_unreadable_service_says_why(world, web):
    world.store.add_source(
        "code", "github", {"token_env": GH_ENV, "repo": "quillworks/quill", "projects": []}
    )
    key = world.store.create_key("desk")
    before = dump(world.db)
    http = httpx.AsyncClient(transport=httpx.MockTransport(never_sent))
    unset = asyncio.run(preview_source(world.store, "code", http=http, env={}, clock=world.clock))
    assert (unset.read, unset.count, unset.titles) == (False, 0, [])
    assert GH_ENV in unset.said
    r = preview(web, "code", {"x-api-key": key})  # through the app's client: nothing leaves
    assert r.status_code == 200
    said = r.json()
    assert said == {"source": "code", "days": 7, "read": False, "said": said["said"]}
    assert GH_ENV in said["said"]
    assert world.feed.requests == [] and dump(world.db) == before

    # A service that refuses: read false, its reason in words, never the secret.
    wrong = asyncio.run(
        preview_source(
            world.store, "ledger", http=world.feed.client(), env={SECRET_ENV: "not-it"},
            clock=world.clock,
        )
    )  # fmt: skip
    assert wrong.read is False and wrong.said and SECRET_ENV in wrong.said
    assert "not-it" not in wrong.said and dump(world.db) == before


UNSET = "R1_UNSET_CREDENTIAL"  # a variable no test sets
NINE_KINDS = {
    "langfuse": {
        "base_url": "https://langfuse.example.test", "projects": ["accounts"],
        "public_key_env": UNSET, "secret_key_env": UNSET,
    },
    "granola": {"key_env": UNSET, "projects": []},
    "google_meet": {"credentials_env": UNSET, "projects": []},
    "google_drive": {"credentials_env": UNSET, "folder_id": "fold-r1-ledger", "projects": []},
    "google_calendar": {"credentials_env": UNSET, "calendar_id": "primary", "projects": []},
    "gmail": {"credentials_env": UNSET, "label": "to-ledger", "projects": []},
    "linear": {"key_env": UNSET, "teams": ["LED"], "projects": []},
    "github": {"token_env": UNSET, "repo": "quillworks/quill", "projects": []},
    "connector": {"url": URL, "secret_env": UNSET, "projects": []},
}  # fmt: skip


@pytest.mark.parametrize("kind", sorted(NINE_KINDS))
def test_preview_with_its_credential_unset_sends_nothing_for_each_kind(world, kind, monkeypatch):
    monkeypatch.delenv(UNSET, raising=False)
    assert set(NINE_KINDS) == set(KINDS)
    world.store.add_source("each", kind, NINE_KINDS[kind])
    before = dump(world.db)
    http = httpx.AsyncClient(transport=httpx.MockTransport(never_sent))
    said = asyncio.run(preview_source(world.store, "each", http=http, env={}, clock=world.clock))
    assert said.read is False and UNSET in said.said, said
    assert dump(world.db) == before


def test_preview_on_the_page_by_keyboard(world, web):
    world.feed.items = [entry(n, NOW - DAY + n * MINUTE) for n in (1, 2)]
    page = web.client.get("/sources", headers=world.ada.cookie)
    assert page.status_code == 200
    form = re.search(
        r'(<form\b[^>]*\baction="/integrations/ledger/preview(#[^"]*)?"[^>]*>)(.*?)</form>',
        page.text,
        re.S,
    )
    assert form, "no form posts to /integrations/ledger/preview"
    opening, fragment, inside = form.groups()
    assert 'method="post"' in opening and fragment == "#preview-ledger"
    assert re.search(r'<button\b[^>]*type="submit"[^>]*>\s*Preview\s*</button>', inside)
    days = re.search(r'<input\b[^>]*name="days"[^>]*>', inside)
    assert days and 'id="' in days.group(0)
    field = re.search(r'id="([^"]+)"', days.group(0)).group(1)
    assert f'<label for="{field}"' in inside
    assert "stores nothing" in page_text(page.text).lower()

    before = dump(world.db)
    shown = web.client.post(
        "/integrations/ledger/preview",
        data={"days": "3"},
        headers={**SAME_ORIGIN, **world.ada.cookie},
    )
    assert shown.status_code == 200
    region = re.search(
        r'(<[^>]*\bid="preview-ledger-region"[^>]*>)(.*?)</section>', shown.text, re.S
    )
    assert region, "no result region"
    tag, inside = region.groups()
    assert 'aria-live="polite"' in tag and 'aria-labelledby="preview-ledger"' in tag
    heading = re.search(r'<h3\b[^>]*\bid="preview-ledger"[^>]*>', inside)
    assert heading and 'tabindex="-1"' in heading.group(0)
    text = page_text(inside)
    assert "Ledger entry 1" in text and "Ledger entry 2" in text and "2" in text
    assert dump(world.db) == before
    refused = web.client.post(
        "/integrations/ledger/preview",
        data={"days": "40"},
        headers={**SAME_ORIGIN, **world.ada.cookie},
    )
    assert refused.status_code == 400 and 'role="alert"' in refused.text
    cross = web.client.post(
        "/integrations/ledger/preview", headers={"sec-fetch-site": "cross-site", **world.ada.cookie}
    )
    assert cross.status_code == 403 and dump(world.db) == before


# Helpers


async def _run(world):
    from cairn.sources import run_source

    async with world.feed.client() as http:
        return await run_source(world.store, "ledger", http=http, env={SECRET_ENV: SECRET},
                                clock=world.clock)  # fmt: skip


async def _preview(world, view, *, name: str = "ledger", **options):
    async with world.feed.client() as http:
        return await preview_source(
            view, name, http=http, env={SECRET_ENV: SECRET}, clock=world.clock, **options
        )


def _stand_in_check(config: object) -> dict:
    assert isinstance(config, dict)
    return {"projects": list(config.get("projects", [])), "token_env": config["token_env"]}


def _stand_in_open(name: str, config: dict, **options):
    return _StandIn(options["env"][config["token_env"]])


class _StandIn:
    name = "desk"

    def __init__(self, token: str) -> None:
        self.token = token

    async def pull(self, cursor: str | None) -> Pulled:
        def draft(title: str, text: str) -> NoteDraft:
            return NoteDraft(project="", title=title, text=text, source_ref="desk:1", label="d")

        return Pulled(
            drafts=[
                draft("Plain title", "# Plain\n\nNothing to hide.\n"),
                draft(f"Key {self.token}", "# Key\n\nA key in the title.\n"),
                draft("Quiet title", f"# Quiet\n\nThe key {self.token} in the text.\n"),
            ],
            cursor="c1",
        )
