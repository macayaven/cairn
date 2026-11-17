"""C9 acceptance tests (frozen): a connector of your own, spoken to over HTTPS by protocol 1.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/sources/connector.py (the kind),
src/cairn/sources/__init__.py (KINDS), the module that holds `connector-check` and
src/cairn/web/cli.py; the protocol itself is docs/connector-protocol.md, which says exactly
what "The protocol" below says.

Why: cairn reads some systems itself, each a module inside the server. A connector of the
operator's own is a small service, in any language, that answers one request; cairn asks it
for what is new and does everything else (the schedule, the switch, once-only, the bounds,
the guard against secrets, the deadlines, writing the notes).

The protocol, version 1
- The request: `POST` to the connector's one address (the config's `url`, exactly), with
  `Authorization: Bearer <secret>`, `Content-Type: application/json`, `Accept:
  application/json`, and a body that is exactly one JSON object with the keys `protocol` (1),
  `since` (RFC 3339 in UTC, "Z" or "+00:00": the source's last switch-on), `cursor` (null on
  the first request after a switch-on, else the cursor of the last answer whose items cairn
  finished writing) and `limit` (the config's, 1 to 200).
- The answer: status 200, a body of at most 4 MiB that is one JSON object: `protocol` 1 (any
  other value, or none: the run fails, and its reason says "version"), `items` (a list of at
  most `limit`), `cursor` (a string of at most 2,000 characters, or null; never one holding
  the secret) and `more` (true or false). Each item: `id` (required, a string of 1 to 200
  characters), `text` (required, a string), `title` and `topic` (optional strings of at most
  200 characters) and `source_ref` (optional, at most 500): a note's own bounds; `at`
  (optional, RFC 3339 with a zone; an `at` without a zone is not one). Any other key, of an
  item or of the answer, is ignored.
- An answer with `more` true moves on: it carries at least one item, or a cursor other than
  the one it was sent. One that does neither fails the run, and the cursor does not move.
- Asked the same request again (cairn does, after a run was cut off), a connector gives the
  items it gave before, in the same order: the same answer exactly when its `more` was true,
  possibly with newer items after them when it was false.
- Refusals: 401 or 403 (the run fails, nothing is asked again in that run); 429 with
  Retry-After (cairn stops the run, does not wait or ask again in it, and asks at the next
  run, with the same cursor); 5xx, a timeout, an answer that is not JSON or not this shape
  (the run fails, SourceError, and the cursor does not move). An error answer may carry
  `{"error": "a short sentence"}` of at most 200 characters: the run's reason shows it once
  it holds no credential (as written or encoded, as `holds_secret` looks); nothing else of an
  error's body is ever shown, nor a sentence over 200 characters.
- Transport: https, or http only to 127.0.0.1 or localhost; no user or password in the
  address; no redirect followed, whatever the HTTP client the kind is given would do; every
  request under cairn's per-request deadline (cairn.sources.timing).

The kind
- `KINDS["connector"]` (cairn.sources.connector). It starts off. Via "connector". It is
  `withheld` (M2): its notes go to no model unless the operator allows it.
- Config: `url` (required), `secret_env` (required: the variable's name; a value that looks
  like a credential is refused), `projects` (at most one; may be empty while off), `limit`
  (default 50, a whole number from 1 to 200). No other key. `check` returns the config with
  `projects` ([]) and `limit` filled in, and gives the same back when given it. A refusal is a
  ValueError naming the key at fault and never repeating a value that looks like a credential
  (in a key's name, in `secret_env`, in `url`'s host, path or query).
- `open` reads the secret from the environment (a missing or blank variable: SourceError
  naming the variable, never a value); the secret is in the source's `secrets`.
- `describe(config)` says in one line what a source reads; `switching_on()` says something;
  the project the Sources page and `source-add` propose for a source with no project is the
  source's own name.

What a run writes (all of it through `run_source`, as the frozen kinds are driven)
- Each item, in the connector's order, is a note in the config's project with the item's text,
  title, topic and `source_ref` (by default "<source name>:<id>"), written via
  "connector". An item whose `at` is before `since` is not written (times are compared as
  times, whatever their zone); one without `at` is.
- Once per id, per source, for as long as the source exists: an id seen again (in the same
  answer, in a later one, after the source was switched off and on, with other text, title,
  topic or source_ref) writes nothing, changes nothing and counts as `unchanged`. Two
  sources may each write the same id once. The kind keeps no record of this itself: each
  draft names its item (the id) and the runner and the store keep what a source wrote
  (I1); this file pins the behaviour through `run_source`, never where it is kept. An item
  is recorded only when its note was stored: one the guard refused, sent again clean under
  the same id, is written. The source's own stored state (config and cursor) does not grow
  with the items written: after 1,000 items it is at most 256 characters longer than after
  10.
- An answer's cursor is stored only once all its items were written or skipped: a run cut
  off by its time limit, by a failure while writing, or by the transport before a later
  answer, sends the old cursor again at its next run, and no item is written twice.
- An item that breaks the protocol is skipped and counted (`skipped`, with a reason); the
  others are written. An answer that breaks it as a whole fails the run. An item holding the
  secret in a field (plain or encoded) is skipped as HELD_CREDENTIALS; a cursor holding it
  fails the run; nothing holding it is stored, logged or shown.
- A run asks at most a fixed number of answers (fewer than 1,000): a connector that always
  says `more` (and moves on) makes a run that ends, worked (it does not raise), wrote what
  it got and stored the last answer's cursor, which the next run sends.

connector-check (`python -m cairn.server connector-check --url URL --secret-env NAME
[--since TIME] [--limit N]`)
- Opens no database, writes no file, talks only to --url with the secret from the
  environment (as the Bearer, never in a wrong-secret request it makes up).
- Prints one line per rule on stdout and nothing else there: each line holds the word "ok"
  or the word "FAILED" (with what it saw: an item by its `id`, never its text, never the
  secret). The rules, in the same order every time: the answer's shape and protocol; ids
  unique within an answer; every item within its bounds; no `at` before `since`; the same
  request twice gives the first answer's ids again in the same order (exactly, with the same
  cursor, when its `more` was true; possibly followed by newer ones when it was false);
  every answer with `more` true moves on; the cursor's size; a wrong secret refused with 401
  or 403 and no items; answers within the deadline. It follows `more` for at most 20
  answers: a longer backlog passes, and the line for moving on then says how far it
  followed (the number 20). A connector that breaks one rule fails that line alone; this
  file tells the lines apart by their place, so the order must not change between runs.
- Exit 0 when every rule holds, 1 when one fails, 2 for bad options (a missing or bad
  option, a url the transport rule refuses), and not 0 for a secret variable that is unset,
  with nothing sent. `--help` names what it is for and that the secret goes only to --url.

What ships beside the code (found from the package: `cairn.__file__`'s parents[2])
- docs/connector-protocol.md: the protocol, with JSON Schemas for the request and the answer
  (fenced ```json blocks in it, or *.json files whose name holds "connector" in docs/), a
  changelog and what a version 2 may change.
- examples/connector/connector.py: a reference connector, standard library only, at most 400
  lines of Python in the folder, run as `python examples/connector/connector.py --folder DIR
  --port PORT --secret-env NAME` (127.0.0.1, any path, its secret from that variable): the
  folder's *.md files as items, newest (by modification time) last, with a cursor and
  paging; examples/connector/README.md says how to run it, check it and add it as a source.
- The sources guide has a section "A connector of your own" naming connector-check and the
  specification (connector-protocol).
"""

import ast
import asyncio
import base64
import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest
from starlette.testclient import TestClient

import cairn
from cairn.arrange import arrange_page
from cairn.llm import Reply
from cairn.server import create_app, main
from cairn.sources import (
    HELD_CREDENTIALS,
    KINDS,
    SourceError,
    holds_secret,
    run_source,
    source_client,
    timing,
    withheld_via,
)
from cairn.store import Store

SECRET_ENV = "CONNECTOR_C9_SECRET"
SECRET = "c9" + "-made-up-" + "connector-" + "secret-5e1f9a"  # never a real secret
ENV = {SECRET_ENV: SECRET}
TOKEN = "ghp_" + "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zAaB3d"  # shaped like a credential
CANARY = "c9-" + "body-" + "canary-3f8a"  # in error bodies and keys cairn must not show
TEXT_MARK = "c9-" + "item-" + "text-91d2"  # in every item's text
URL = "https://erp.example.test/cairn/feed"
CRM_URL = "https://crm.example.test/feed"
SWITCH_ON = datetime(2099, 11, 14, 9, 0, tzinfo=UTC)
MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)
CHECK_SINCE = "2025-01-01T00:00:00Z"
ROOT = Path(cairn.__file__).resolve().parents[2]
SRC = Path(cairn.__file__).resolve().parents[1]
RFC3339_UTC = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|\+00:00)")


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def z(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parsed(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))


def b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def item(n: int, **fields) -> dict:
    """An invoice as the connector gives it; `fields` replace or add keys (None drops one)."""
    given = {
        "id": f"INV-{n}",
        "text": f"# Invoice {n}\n\nInvoice {n} is due on day {n % 28 + 1} ({TEXT_MARK}).\n",
        "title": f"Invoice {n}",
        "topic": f"Invoice {n}, 2099-11",
        "at": z(SWITCH_ON + n * MINUTE),
    }
    given.update(fields)
    return {key: value for key, value in given.items() if value is not None}


# The stand-in connector: one protocol core, served by httpx.MockTransport or a loopback server


class Connector:
    """A connector that speaks protocol 1 faithfully: it checks the bearer, honours `since`
    (unless `ignore_since`), gives at most `limit` items (at most `page` when set), says
    `more` while it has more, and its cursor ("after:N") gives the same answer to the same
    request. It can be told to send `forced` answers next (a (status, body, headers) tuple,
    or "timeout", "disconnect", "hang"), or to break one rule (`fault`). Every request is
    kept with the run it came in (`runs`, moved by the tests' `run`)."""

    def __init__(self, url: str = URL, secret: str = SECRET) -> None:
        self.url, self.secret = url, secret
        self.feed: list[dict] = []
        self.page: int | None = None
        self.ignore_since = False
        self.fault: str | None = None
        self.forced: list = []
        self.requests: list[SimpleNamespace] = []
        self.answers: list[dict] = []
        self.runs = 0
        self.counter = 0
        self.firsts = 0  # requests with no cursor, answered

    def of_run(self, run: int | None = None) -> list[SimpleNamespace]:
        run = self.runs if run is None else run
        return [r for r in self.requests if r.run == run]

    def give(self, method: str, url: str, headers: dict, raw: bytes):
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        record = SimpleNamespace(method=method, url=url, headers=headers, body=body, run=self.runs)
        self.requests.append(record)
        self.counter += 1
        if self.forced:
            return self.forced.pop(0)
        if self.counter > 5_000:  # a run that never stops would hang the test: this ends it
            return 500, {"error": "the stand-in gave up"}, {}
        authorized = headers.get("authorization") == f"Bearer {self.secret}"
        if not authorized and self.fault != "wrong secret answered":
            return 401, {"error": "the secret was refused", "detail": CANARY}, {}
        try:
            since, cursor, limit = parsed(body["since"]), body["cursor"], body["limit"]
            offset = 0 if cursor is None else int(cursor.split(";")[0].removeprefix("after:"))
        except (TypeError, KeyError, ValueError):
            return 400, {"error": "not a request of protocol 1", "detail": CANARY}, {}
        if body.get("protocol") != 1:
            return 400, {"error": "this connector speaks protocol 1"}, {}
        size = min(limit, self.page or limit)
        self.firsts += offset == 0
        if self.fault == "stuck" and offset > 0:  # says more, gives nothing new
            answer = {"protocol": 1, "items": [], "cursor": cursor, "more": True}
            self.answers.append(answer)
            return 200, answer, {}
        if self.fault == "endless":
            chunk = [item(10_000 + offset + k) for k in range(size)]
            nxt, more = offset + size, True
        else:
            eligible = [i for i in self.feed if self.ignore_since or self._since(i, since)]
            chunk = [
                dict(i) if isinstance(i, dict) else i for i in eligible[offset : offset + size]
            ]
            nxt = offset + len(chunk)
            more = nxt < len(eligible)
        if offset == 0 and chunk and self.fault == "duplicate id":
            chunk.append({**chunk[0], "text": f"Another text for {chunk[0]['id']} ({TEXT_MARK})"})
        if offset == 0 and self.fault == "before since":
            chunk.insert(0, item(9_999, at=z(since - DAY)))
        if offset == 0 and chunk and self.fault == "over bound":
            chunk[0]["title"] = "T" * 600
        if offset == 0 and self.firsts > 1 and self.fault == "reordered":
            chunk[:2] = chunk[1::-1]
        if offset == 0 and self.firsts > 1 and self.fault == "an earlier one gone":
            chunk = chunk[1:]
        if offset == 0 and self.firsts == 1 and self.fault == "filling":  # not a fault
            self.feed.append(item(len(self.feed) + 1))  # the last page gains one meanwhile
        cursor = f"after:{nxt}"
        if self.fault == "not idempotent":
            cursor += f";asked={self.counter}"
        if self.fault == "long cursor":
            cursor += ";" + "p" * (2_000 - len(cursor))
        answer = {"protocol": 1, "items": chunk, "cursor": cursor, "more": more}
        self.answers.append(answer)
        return 200, answer, {}

    def _since(self, given: object, since: datetime) -> bool:
        try:
            return given.get("at") is None or parsed(given["at"]) >= since  # type: ignore[union-attr]
        except (AttributeError, TypeError, ValueError):
            return True


def encoded(status: int, body, headers: dict) -> tuple[int, dict, bytes]:
    if isinstance(body, bytes):
        data = body
    elif isinstance(body, str):
        data = body.encode()
        headers = {"content-type": "text/html", **headers}
    else:
        data = json.dumps(body).encode()
        headers = {"content-type": "application/json", **headers}
    return status, headers, data


class Router:
    """httpx's mock transport for one or more stand-ins, by their exact address. A request to
    any other address (a redirect followed) is kept in `strays` and answered 404."""

    def __init__(self, *connectors: Connector) -> None:
        self.connectors = {c.url: c for c in connectors}
        self.strays: list[SimpleNamespace] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        raw = await request.aread()
        headers = {k.lower(): v for k, v in request.headers.items()}
        connector = self.connectors.get(str(request.url))
        if connector is None:
            self.strays.append(SimpleNamespace(url=str(request.url), headers=headers))
            return httpx.Response(404)
        got = connector.give(request.method, str(request.url), headers, raw)
        if got == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if got == "disconnect":
            raise httpx.RemoteProtocolError("the server disconnected", request=request)
        if got == "hang":
            await asyncio.Event().wait()
        status, headers, data = encoded(*got)
        return httpx.Response(status, headers=headers, content=data)


class Loopback:
    """The same stand-in on a real socket on 127.0.0.1, in a thread."""

    def __init__(self, connector: Connector) -> None:
        stand_in = connector

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                headers = {k.lower(): v for k, v in self.headers.items()}
                url = f"http://127.0.0.1:{self.server.server_address[1]}{self.path}"
                status, headers, data = encoded(*stand_in.give("POST", url, headers, raw))
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        connector.url = f"http://127.0.0.1:{self.server.server_address[1]}/feed"
        self.connector = connector

    def __enter__(self) -> "Loopback":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)


# The kind and its config


@pytest.fixture
def kind():
    return KINDS["connector"]


def config(**settings) -> dict:
    given = {"url": URL, "secret_env": SECRET_ENV, "projects": ["invoices"], **settings}
    return {key: value for key, value in given.items() if value is not None}


def test_the_kind_is_built_in_starts_off_and_fills_in_its_defaults(kind):
    assert "connector" in KINDS
    assert kind.starts_off is True and kind.via == "connector"
    assert kind.withheld is True and "connector" in withheld_via()
    checked = kind.check({"url": URL, "secret_env": SECRET_ENV})
    assert checked == {"url": URL, "secret_env": SECRET_ENV, "projects": [], "limit": 50}
    assert kind.check(checked) == checked
    given = config(limit=200)
    kept = kind.check(given)
    assert kept == given and kind.check(kept) == kept
    for accepted in (
        {"url": "http://127.0.0.1:8811/feed"},
        {"url": "http://localhost:8811/"},
        {"limit": 1},
        {"projects": []},
    ):
        assert kind.check(config(**accepted))["url"] == config(**accepted)["url"], accepted
    said = kind.describe(checked)
    assert isinstance(said, str) and said.strip() and "\n" not in said
    assert isinstance(kind.switching_on(), str) and kind.switching_on().strip()


@pytest.mark.parametrize(
    ("key", "settings"),
    [
        ("url", {"url": None}),
        ("url", {"url": 7}),
        ("url", {"url": ""}),
        ("url", {"url": "erp.example.test/feed"}),
        ("url", {"url": "ftp://erp.example.test/feed"}),
        ("url", {"url": "http://erp.example.test/feed"}),
        ("url", {"url": "http://192.168.10.4:8811/feed"}),
        ("url", {"url": "https://ada:hunter2-c9@erp.example.test/feed"}),
        ("url", {"url": "https://ada@erp.example.test/feed"}),
        ("secret_env", {"secret_env": None}),
        ("secret_env", {"secret_env": 7}),
        ("secret_env", {"secret_env": ""}),
        ("projects", {"projects": ["invoices", "ledger"]}),
        ("projects", {"projects": "invoices"}),
        ("limit", {"limit": 0}),
        ("limit", {"limit": 201}),
        ("limit", {"limit": "50"}),
        ("limit", {"limit": True}),
        ("limit", {"limit": 1.5}),
        ("colour", {"colour": "blue"}),
    ],
)
def test_check_refuses_a_bad_setting_by_its_name(kind, key, settings):
    with pytest.raises(ValueError) as refused:
        kind.check(config(**settings))
    assert key in str(refused.value)
    assert "hunter2-c9" not in str(refused.value)


def test_a_credential_in_the_config_is_refused_and_never_repeated(kind, tmp_path):
    for leaked, key in (
        (config(url=f"https://erp.example.test/feed?token={TOKEN}"), "url"),
        (config(url=f"https://erp.example.test/{TOKEN}/feed"), "url"),
        (config(secret_env=TOKEN), "secret_env"),
        (config(**{TOKEN: "x"}), None),
    ):
        with pytest.raises(ValueError) as refused:
            kind.check(leaked)
        said = str(refused.value)
        assert TOKEN not in said and TOKEN[4:] not in said
        if key:
            assert key in said
    for odd in (None, [], "url"):
        with pytest.raises(ValueError):
            kind.check(odd)

    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        store.create_project("invoices")
        with pytest.raises(ValueError):
            store.add_source("erp", "connector", config(url=f"{URL}?token={TOKEN}"))
        assert store.list_sources() == []
    finally:
        store.close()
    assert TOKEN not in dump(db)


def test_the_secret_comes_from_the_environment_and_is_among_the_source_s_secrets(kind):
    checked = kind.check(config())
    http = httpx.AsyncClient(transport=httpx.MockTransport(Router().handle))

    def opened(env):
        return kind.open(
            "erp", checked, http=http, env=env, clock=Clock(SWITCH_ON), sleep=None,
            deadline=None, since=SWITCH_ON,
        )  # fmt: skip

    for env in ({}, {SECRET_ENV: ""}, {SECRET_ENV: "   "}, {"ANOTHER_VARIABLE": SECRET}):
        with pytest.raises(SourceError) as missing:
            opened(env)
        assert SECRET_ENV in str(missing.value) and SECRET not in str(missing.value)
    assert SECRET in opened(ENV).secrets


# Through the runner


@pytest.fixture
def world(tmp_path):
    clock = Clock(SWITCH_ON - DAY)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    for name in ("invoices", "ledger"):
        store.create_project(name)
    connector = Connector()
    router = Router(connector)
    # A client that would follow a redirect: the kind must not, whatever it is given.
    http = httpx.AsyncClient(transport=httpx.MockTransport(router.handle), follow_redirects=True)
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    yield SimpleNamespace(
        store=store, db=db, clock=clock, connector=connector, router=router, http=http,
        slept=slept, sleep=sleep,
    )  # fmt: skip
    store.close()


def add(world, name: str = "erp", **settings):
    return world.store.add_source(name, "connector", config(projects=[], **settings))


def switch_on(world, name: str = "erp", at: datetime = SWITCH_ON, project: str = "invoices"):
    world.clock.now = at
    return world.store.enable_source(name, projects=[project])


def attempt(world, at: datetime, name: str = "erp", **options):
    """One run at `at`: (its report, None) or (None, the error it raised)."""
    world.clock.now = at
    for connector in world.router.connectors.values():
        connector.runs += 1
    running = run_source(
        world.store, name, http=world.http, env=ENV, clock=world.clock, sleep=world.sleep,
        **options,
    )  # fmt: skip
    try:
        report = asyncio.run(running)
    except Exception as error:
        return None, error
    return report, None


def run(world, at: datetime, name: str = "erp", **options):
    report, error = attempt(world, at, name, **options)
    if error is not None:
        raise error
    return report


def notes(world, project: str = "invoices") -> list:
    return world.store.list_notes(world.store.get_project(project).id)


def texts(world, project: str = "invoices") -> list[str]:
    return [note.text for note in notes(world, project)]


def cursor_of(world, name: str = "erp") -> str | None:
    return world.store.get_source(name).cursor


def asked(connector: Connector, run: int | None = None) -> list:
    """The cursors the requests of a run sent."""
    return [r.body["cursor"] for r in connector.of_run(run)]


def media(value: str | None) -> str:
    return (value or "").split(";")[0].strip().lower()


def test_through_the_runner_it_is_off_until_switched_on_then_writes_each_item_once(world):
    connector = world.connector
    connector.page = 2
    connector.feed = [
        item(1, source_ref="https://erp.example.test/invoices/1"),
        item(2),
        item(3, title=None, topic=None, at=None),
        item(4),
    ]
    added = add(world)
    assert added.enabled is False
    report, _ = attempt(world, SWITCH_ON - HOUR)
    assert report.off is True and connector.requests == []

    switch_on(world)
    report = run(world, SWITCH_ON + HOUR)
    first, second = connector.of_run()
    for request in (first, second):
        assert request.method == "POST" and request.url == URL
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        assert media(request.headers.get("content-type")) == "application/json"
        assert media(request.headers.get("accept")) == "application/json"
    since = first.body["since"]
    assert first.body == {"protocol": 1, "since": since, "cursor": None, "limit": 50}
    assert RFC3339_UTC.fullmatch(since) and parsed(since) == SWITCH_ON
    assert second.body == {**first.body, "cursor": connector.answers[0]["cursor"]}

    assert report.saved == 4 and (report.skipped, report.refused, report.rejected) == (0, 0, 0)
    held = notes(world)
    assert [c.text for c in held] == [i["text"] for i in connector.feed]
    assert {c.via for c in held} == {"connector"}
    assert [c.source_ref for c in held] == [
        "https://erp.example.test/invoices/1",
        "erp:INV-2",
        "erp:INV-3",
        "erp:INV-4",
    ]
    for note, given in zip(held, connector.feed, strict=True):
        if "title" in given:
            assert (note.title, note.topic) == (given["title"], given["topic"])
    assert notes(world, "ledger") == []
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.detail["source"], event.detail["saved"]) == (
        "source.synced",
        "erp",
        4,
    )

    again = run(world, SWITCH_ON + 2 * HOUR)
    assert asked(connector)[0] == connector.answers[1]["cursor"]  # the last answer's cursor
    assert again.saved == 0 and len(notes(world)) == 4
    connector.feed.append(item(5))
    later = run(world, SWITCH_ON + 3 * HOUR)
    assert asked(connector)[0] == connector.answers[1]["cursor"]
    assert later.saved == 1 and texts(world)[-1] == item(5)["text"]
    assert not holds_secret(dump(world.db), (SECRET,))
    assert world.router.strays == []


def test_the_limit_of_the_config_is_the_one_asked_for(world):
    world.connector.feed = [item(n) for n in range(1, 8)]
    add(world, limit=3)
    switch_on(world)
    assert run(world, SWITCH_ON + HOUR).saved == 7
    assert [r.body["limit"] for r in world.connector.of_run()] == [3, 3, 3]
    assert [len(a["items"]) for a in world.connector.answers] == [3, 3, 1]


# Once per id


def test_an_id_is_written_once_whatever_the_connector_sends_for_it_later(world):
    connector = world.connector
    connector.feed = [
        item(1),
        item(1, text=f"Invoice 1, sent twice in one answer ({TEXT_MARK})\n"),
        item(2),
    ]
    add(world)
    switch_on(world)
    assert run(world, SWITCH_ON + HOUR).saved == 2
    assert texts(world) == [item(1)["text"], item(2)["text"]]
    before = notes(world)
    pages = world.store.list_pages(world.store.get_project("invoices").id)

    connector.feed.append(
        item(
            1,
            text=f"# Invoice 1, revised\n\nInvoice 1 is now due on day 9 ({TEXT_MARK}).\n",
            title="Invoice 1 (revised)",
            topic="Revised invoices",
            source_ref="https://erp.example.test/invoices/1?v=2",
            at=None,
        )
    )
    report = run(world, SWITCH_ON + 2 * HOUR)
    assert asked(connector) == ["after:3"] and len(connector.answers[-1]["items"]) == 1
    assert (report.saved, report.unchanged) == (0, 1) and notes(world) == before
    assert world.store.list_pages(world.store.get_project("invoices").id) == pages

    world.store.disable_source("erp")
    again = SWITCH_ON + DAY
    switch_on(world, at=again)
    connector.feed += [
        item(2, text=f"Invoice 2 again, after a switch-on ({TEXT_MARK})\n", at=None),
        item(1, text=f"Invoice 1 again, after a switch-on ({TEXT_MARK})\n", at=z(again + HOUR)),
    ]
    report = run(world, again + 2 * HOUR)
    first = connector.of_run()[0]
    assert first.body["cursor"] is None and parsed(first.body["since"]) == again
    assert len(connector.answers[-1]["items"]) == 3  # the three sent since the switch-on
    assert (report.saved, report.unchanged) == (0, 3) and notes(world) == before
    assert world.store.list_pages(world.store.get_project("invoices").id) == pages


def test_two_sources_may_each_write_the_same_id_once(world):
    crm = Connector(url=CRM_URL)
    world.router.connectors[CRM_URL] = crm
    world.connector.feed = [item(1)]
    crm.feed = [item(1, text=f"# Invoice 1 at the CRM\n\nOpened by Brook ({TEXT_MARK}).\n")]
    add(world)
    add(world, "crm", url=CRM_URL)
    switch_on(world)
    switch_on(world, "crm", project="ledger")
    assert run(world, SWITCH_ON + HOUR).saved == 1
    assert run(world, SWITCH_ON + HOUR, "crm").saved == 1
    assert [c.source_ref for c in notes(world)] == ["erp:INV-1"]
    assert [c.source_ref for c in notes(world, "ledger")] == ["crm:INV-1"]
    assert run(world, SWITCH_ON + 2 * HOUR, "crm").saved == 0


def test_an_id_the_guard_refused_is_written_when_it_comes_again_clean(world):
    connector = world.connector
    leaked = f"# Invoice 2\n\nPaid with the key {TOKEN} ({TEXT_MARK}).\n"
    connector.feed = [item(1), item(2, text=leaked), item(3)]
    add(world)
    switch_on(world)
    report = run(world, SWITCH_ON + HOUR)
    assert (report.saved, report.refused) == (2, 1)
    assert texts(world) == [item(1)["text"], item(3)["text"]]

    connector.feed.append(item(2))  # the same id, clean this time: it never was written
    report = run(world, SWITCH_ON + 2 * HOUR)
    assert report.saved == 1 and texts(world)[-1] == item(2)["text"]
    connector.feed.append(item(2, text=f"Invoice 2 once more ({TEXT_MARK})\n"))
    report = run(world, SWITCH_ON + 3 * HOUR)
    assert (report.saved, report.unchanged) == (0, 1) and len(notes(world)) == 3
    assert TOKEN not in dump(world.db)


def stored_state(world, name: str = "erp") -> int:
    """The size of what the source itself keeps: its config and its cursor."""
    source = world.store.get_source(name)
    return len(json.dumps(source.config)) + len(source.cursor or "")


def test_the_stored_state_does_not_grow_with_the_items_written(world):
    connector = world.connector
    connector.feed = [item(n) for n in range(1, 11)]
    add(world, limit=200)
    switch_on(world)
    assert run(world, SWITCH_ON + HOUR).saved == 10
    after_ten = stored_state(world)

    connector.feed += [item(n) for n in range(11, 1_001)]
    at = SWITCH_ON + 2 * HOUR
    for _ in range(20):  # as many runs as it takes, each one bounded
        at += HOUR
        run(world, at)
        if not connector.answers[-1]["more"]:
            break
    assert len(notes(world)) == 1_000
    after_a_thousand = stored_state(world)
    assert after_a_thousand <= after_ten + 256

    resent = [1, 500, 1_000]
    connector.feed += [item(n, text=f"Invoice {n} sent once more ({TEXT_MARK})\n") for n in resent]
    report = run(world, at + HOUR)
    assert (report.saved, report.unchanged) == (0, 3) and len(notes(world)) == 1_000
    held = texts(world)
    assert all(item(n)["text"] in held for n in resent)


# A run cut off


class FailsOn:
    """A note guard that fails, as a store that cannot write would, on the note holding
    `words`; it lets everything else through."""

    def __init__(self, words: str) -> None:
        self.words = words

    def check(self, text: str) -> list:
        if self.words in text:
            raise OSError("disk I/O error")
        return []


def test_a_run_the_store_stops_moves_no_cursor_and_the_next_writes_each_item_once(world):
    connector = world.connector
    connector.feed = [item(1), item(2)]
    add(world)
    switch_on(world)
    assert run(world, SWITCH_ON + HOUR).saved == 2
    kept = cursor_of(world)
    old = connector.answers[-1]["cursor"]

    connector.feed += [item(n) for n in range(3, 8)]  # five more, in one answer
    report, error = attempt(world, SWITCH_ON + 2 * HOUR, guard=FailsOn("Invoice 5 is due"))
    assert report is None and error is not None
    assert cursor_of(world) == kept
    assert world.store.get_source("erp").last_error
    assert item(5)["text"] not in texts(world)

    run(world, SWITCH_ON + 3 * HOUR)
    assert asked(connector) == [old]
    assert texts(world) == [item(n)["text"] for n in range(1, 8)]
    run(world, SWITCH_ON + 4 * HOUR)
    assert asked(connector) == ["after:7"]
    assert len(notes(world)) == 7


def test_a_run_out_of_time_stores_the_cursor_from_before_the_answer_it_did_not_finish(world):
    from cairn.gate import lint_gate

    connector = world.connector
    connector.feed = [item(1), item(2)]
    add(world)
    switch_on(world)
    assert run(world, SWITCH_ON + HOUR).saved == 2
    old = connector.answers[-1]["cursor"]
    connector.feed += [item(n) for n in range(3, 8)]
    ticked = []

    def slow_gate(candidate, spans):  # the note of item 4 takes the rest of the run's time
        if not ticked and any("Invoice 4 is due" in span.text for span in spans.values()):
            ticked.append(True)
            world.clock.now += 11 * MINUTE
        return lint_gate(candidate, spans)

    report = run(world, SWITCH_ON + 2 * HOUR, gate=slow_gate)
    assert report.saved == 2 and report.left >= 1
    assert texts(world) == [item(n)["text"] for n in range(1, 5)]

    run(world, SWITCH_ON + 3 * HOUR)
    assert asked(connector) == [old]
    assert texts(world) == [item(n)["text"] for n in range(1, 8)]
    run(world, SWITCH_ON + 4 * HOUR)
    assert asked(connector) == ["after:7"] and len(notes(world)) == 7


def test_the_transport_dying_before_a_later_answer_moves_no_cursor(world):
    connector = world.connector
    connector.feed = [item(1), item(2)]
    add(world)
    switch_on(world)
    run(world, SWITCH_ON + HOUR)
    kept, old = cursor_of(world), connector.answers[-1]["cursor"]

    connector.feed += [item(n) for n in range(3, 7)]
    connector.page = 2
    first_answer = Connector.give  # the first request is answered as usual, the second dies

    def dies_second(self, method, url, headers, raw):
        if len(self.of_run()) == 1:
            self.forced = ["disconnect"]
        return first_answer(self, method, url, headers, raw)

    connector.give = dies_second.__get__(connector)
    report, error = attempt(world, SWITCH_ON + 2 * HOUR)
    assert isinstance(error, SourceError) and report is None
    assert len(connector.of_run()) == 2
    assert cursor_of(world) == kept and world.store.get_source("erp").last_error

    del connector.give
    run(world, SWITCH_ON + 3 * HOUR)
    assert asked(connector)[0] == old
    assert texts(world) == [item(n)["text"] for n in range(1, 7)]


# since and at


def test_items_from_before_the_switch_on_are_not_written_and_one_without_at_is(world):
    connector = world.connector
    connector.ignore_since = True  # it sends everything: cairn holds the line itself
    connector.feed = [
        item(1, at=z(SWITCH_ON - MINUTE)),
        item(2, at="2099-11-14T10:30:00+02:00"),  # 08:30 UTC: before, though it sorts after
        item(3, at=z(SWITCH_ON)),
        item(4, at=None),
        item(5, at="2099-11-14T11:00:00+02:00"),  # 09:00 UTC: the switch-on itself
        item(6, at="2099-11-14T10:00:00.250Z"),
        item(7, at="2099-11-14T03:00:00-07:00"),  # 10:00 UTC
    ]
    add(world)
    switch_on(world)
    run(world, SWITCH_ON + 2 * HOUR)
    assert texts(world) == [item(n)["text"] for n in (3, 4, 5, 6, 7)]


# When the connector says no, or breaks the protocol


def big_answer(size: int) -> bytes:
    head = json.dumps({"protocol": 1, "items": [item(3), item(4)], "cursor": "after:4"})
    body = head[:-1] + ', "more": false, "padding": "'
    return (body + "x" * (size - len(body) - 2) + '"}').encode()


REFUSALS = {
    "401": (401, {"error": "the secret was refused", "detail": CANARY}, {}),
    "403": (403, {"detail": CANARY}, {}),
    "429": (429, {"detail": CANARY}, {"Retry-After": "30"}),
    "500": (500, {"detail": CANARY}, {}),
    "503 html": (503, f"<html>{CANARY}</html>", {}),
    "timeout": "timeout",
    "not json": (200, f"<html>{CANARY}</html>", {}),
    "cut short": (200, b'{"protocol": 1, "items": [{"id": "' + CANARY.encode(), {}),
    "an array": (200, [CANARY], {}),
    "protocol 2": (200, {"protocol": 2, "items": [], "cursor": "after:2", "more": False}, {}),
    "no protocol": (200, {"items": [item(3)], "cursor": "after:3", "more": False}, {}),
    "protocol as text": (200, {"protocol": "1", "items": [], "cursor": None, "more": False}, {}),
    "items not a list": (200, {"protocol": 1, "items": item(3), "cursor": None, "more": False}, {}),
    "items null": (200, {"protocol": 1, "items": None, "cursor": None, "more": False}, {}),
    "201 items": (
        200,
        {"protocol": 1, "items": [item(n) for n in range(3, 204)], "cursor": "x", "more": False},
        {},
    ),
    "a cursor of 2,001": (
        200,
        {"protocol": 1, "items": [item(3)], "cursor": "after:3;" + "p" * 1_993, "more": False},
        {},
    ),
    "a cursor that is a number": (
        200,
        {"protocol": 1, "items": [item(3)], "cursor": 3, "more": False},
        {},
    ),
    "more as text": (
        200,
        {"protocol": 1, "items": [item(3)], "cursor": "after:3", "more": "false"},
        {},
    ),
    "more that does not move on": (  # no item, and the cursor it was sent ("after:2")
        200,
        {"protocol": 1, "items": [], "cursor": "after:2", "more": True},
        {},
    ),
    "over 4 MiB": (200, big_answer(4 * 1024 * 1024 + 1024), {"content-type": "application/json"}),
    "a redirect elsewhere": (302, b"", {"Location": "https://elsewhere.example.test/feed"}),
    "a redirect on the same host": (307, b"", {"Location": "https://erp.example.test/cairn/o"}),
}


@pytest.mark.parametrize("case", list(REFUSALS))
def test_each_refusal_ends_the_run_moves_no_cursor_and_shows_no_secret(world, caplog, case):
    caplog.set_level(logging.DEBUG)
    connector = world.connector
    connector.feed = [item(1), item(2)]
    add(world, limit=200)
    switch_on(world)
    run(world, SWITCH_ON + HOUR)
    kept, old = cursor_of(world), connector.answers[-1]["cursor"]
    connector.feed += [item(3), item(4)]

    connector.forced = [REFUSALS[case]]
    report, error = attempt(world, SWITCH_ON + 2 * HOUR)
    assert len(connector.of_run()) == 1  # nothing asked again in the run, nothing followed
    assert world.router.strays == [] and world.slept == []
    assert cursor_of(world) == kept and len(notes(world)) == 2
    if case == "429":
        assert error is None or isinstance(error, SourceError)
        assert error is not None or report.saved == 0
    else:
        assert isinstance(error, SourceError), error
        reason = world.store.get_source("erp").last_error
        assert reason and str(error)
        for said in (reason, str(error)):
            assert CANARY not in said
            assert not holds_secret(said, (SECRET,))
        if case.startswith("protocol") or case == "no protocol":
            assert "version" in reason.lower()
    assert not holds_secret(caplog.text, (SECRET,)) and CANARY not in caplog.text
    assert not holds_secret(dump(world.db), (SECRET,))

    run(world, SWITCH_ON + 3 * HOUR)  # the next run asks again from where it was
    assert asked(connector)[0] == old
    assert texts(world) == [item(n)["text"] for n in (1, 2, 3, 4)]


def test_an_answer_that_never_comes_ends_at_the_per_request_deadline(world, monkeypatch):
    monkeypatch.setattr(timing, "FLOOR", 0.05)
    connector = world.connector
    connector.feed = [item(1)]
    add(world)
    switch_on(world)
    connector.forced = ["hang"]
    world.clock.now = SWITCH_ON + HOUR
    connector.runs += 1
    running = run_source(
        world.store, "erp", http=world.http, env=ENV, clock=world.clock, sleep=world.sleep,
        limit=timedelta(seconds=1),
    )  # fmt: skip
    with pytest.raises(SourceError):
        asyncio.run(asyncio.wait_for(running, 60))  # well past the deadline: a hang fails here
    assert cursor_of(world) is None and notes(world) == []
    assert run(world, SWITCH_ON + 2 * HOUR).saved == 1


def test_an_error_s_short_sentence_is_the_run_s_reason_once_it_holds_no_secret(world):
    connector = world.connector
    connector.feed = [item(1)]
    add(world)
    switch_on(world)
    long_sentence = "The ledger is closed while the auditors read it, " + "o" * 152
    assert len(long_sentence) == 201

    def reason_for(status: int, body) -> str:
        """The run's reason as kept (last_error), after the error's own text, which the
        command line prints: everything after the check must hold for both."""
        connector.forced = [(status, body, {})]
        _, error = attempt(world, SWITCH_ON + HOUR)
        assert isinstance(error, SourceError)
        kept = world.store.get_source("erp").last_error
        assert kept and str(error)
        return f"{error}\n{kept}"

    said = reason_for(500, {"error": "the ledger is closed for the night", "detail": CANARY})
    assert "the ledger is closed for the night" in said and CANARY not in said
    said = reason_for(403, {"error": "this secret may read invoices only", "trace": CANARY})
    assert "this secret may read invoices only" in said and CANARY not in said
    for echoed in (SECRET, b64(SECRET), quote(quote(SECRET))):
        said = reason_for(500, {"error": f"zebra-crossing {echoed}"})
        assert said and not holds_secret(said, (SECRET,)) and "zebra-crossing" not in said
    said = reason_for(500, {"error": long_sentence})
    assert said and long_sentence not in said
    said = reason_for(500, {"error": 42, "message": CANARY})
    assert said and CANARY not in said
    assert run(world, SWITCH_ON + HOUR).saved == 1


def test_a_cursor_of_2000_characters_and_keys_cairn_does_not_know(world):
    connector = world.connector
    add(world)
    switch_on(world)
    longest = "k" * 2_000
    answer = {
        "protocol": 1,
        "items": [{**item(1), "colour": "blue", "links": [{"rel": "self"}]}],
        "cursor": longest,
        "more": False,
        "next_version_key": {"anything": [1, 2, 3]},
    }
    padded = json.dumps(answer)[:-1] + ', "padding": "' + "x" * (4 * 1024 * 1024 - 4096) + '"}'
    connector.forced = [(200, padded.encode(), {"content-type": "application/json"})]
    assert run(world, SWITCH_ON + HOUR).saved == 1
    connector.forced = [(200, {"protocol": 1, "items": [], "cursor": longest, "more": False}, {})]
    run(world, SWITCH_ON + 2 * HOUR)
    assert asked(connector) == [longest]


BAD_ITEMS = [
    {"text": f"BAD no id ({TEXT_MARK})"},
    {"id": "", "text": "BAD empty id"},
    {"id": 2041, "text": "BAD a number for an id"},
    {"id": "X" * 201, "text": "BAD an id of 201"},
    {"id": "BAD-5"},
    {"id": "BAD-6", "text": 7},
    {"id": "BAD-7", "text": "BAD a title of 201", "title": "T" * 201},
    {"id": "BAD-8", "text": "BAD a topic that is a list", "topic": ["Invoices"]},
    {
        "id": "BAD-9",
        "text": "BAD a source_ref of 501",
        "source_ref": "https://e.test/" + "r" * 486,
    },
    {"id": "BAD-13", "text": "BAD a topic of 201", "topic": "P" * 201},
    {"id": "BAD-10", "text": "BAD an at that is not a time", "at": "yesterday"},
    {"id": "BAD-11", "text": "BAD an at that is a number", "at": 4102444800},
    {"id": "BAD-12", "text": "BAD an at without a zone", "at": "2099-11-14T10:00:00"},
    "BAD an item that is a string",
    ["BAD", "a list"],
]


def test_an_item_that_breaks_the_protocol_is_skipped_and_counted_and_the_others_written(world):
    connector = world.connector
    connector.ignore_since = True
    good = [item(n) for n in range(1, 5)]
    good[2] = item(3, title="T" * 200, topic="P" * 200, source_ref="https://e.test/" + "r" * 485)
    assert len(BAD_ITEMS[8]["source_ref"]) == 501 and len(good[2]["source_ref"]) == 500
    connector.feed = [good[0], *BAD_ITEMS[:7], good[1], good[2], *BAD_ITEMS[7:], good[3]]
    add(world)
    switch_on(world)
    report = run(world, SWITCH_ON + HOUR)
    assert report.saved == 4 and texts(world) == [i["text"] for i in good]
    at_bounds = notes(world)[2]
    assert at_bounds.title and ("T" * 200).startswith(at_bounds.title)  # a note may cut it
    assert at_bounds.topic and ("P" * 200).startswith(at_bounds.topic)
    assert at_bounds.source_ref == good[2]["source_ref"]
    assert report.skipped == len(BAD_ITEMS)
    assert sum(report.reasons.values()) == report.skipped
    assert not any("BAD" in text for text in texts(world))
    assert not holds_secret(json.dumps(report.reasons), (SECRET,))
    assert run(world, SWITCH_ON + 2 * HOUR).saved == 0


# The secret echoed back


def test_the_secret_echoed_in_an_item_is_withheld_and_in_a_cursor_fails_the_run(world):
    connector = world.connector
    connector.feed = [
        item(1, text=f"Invoice 1 for the key {SECRET} ({TEXT_MARK})\n"),
        item(2, title=f"Invoice 2 {b64(SECRET)}"),
        item(3, topic=f"Invoices of {quote(quote(SECRET))}"),
        item(4, source_ref=f"https://erp.example.test/i/4?k={quote(SECRET)}"),
        item(5, text=f"Invoice 5, the key spaced out: {' '.join(SECRET)} ({TEXT_MARK})\n"),
        item(6),
    ]
    add(world)
    switch_on(world)
    report = run(world, SWITCH_ON + HOUR)
    assert report.saved == 1 and texts(world) == [item(6)["text"]]
    assert report.reasons.get(HELD_CREDENTIALS) == 5
    kept = cursor_of(world)
    connector.feed.append(item(7))

    for cursor in (f"after:7;{SECRET}", b64(f"after:7;{SECRET}"), f"after:7;Bearer {SECRET}"):
        connector.forced = [
            (200, {"protocol": 1, "items": [item(7)], "cursor": cursor, "more": False}, {})
        ]
        report, error = attempt(world, SWITCH_ON + 2 * HOUR)
        assert isinstance(error, SourceError)
        assert cursor_of(world) == kept
        assert not holds_secret(world.store.get_source("erp").last_error, (SECRET,))
    assert not holds_secret(dump(world.db), (SECRET,))
    assert run(world, SWITCH_ON + 3 * HOUR).saved == 1


# A connector that never stops


def test_a_connector_that_always_says_more_cannot_keep_a_run_going(world):
    connector = world.connector
    connector.fault = "endless"
    connector.page = 2
    add(world)
    switch_on(world)
    report = run(world, SWITCH_ON + HOUR)  # it ends, and it worked
    answered = connector.of_run()
    assert 1 <= len(answered) < 1_000
    assert report.saved == 2 * len(answered) == len(notes(world))
    last = connector.answers[-1]["cursor"]
    run(world, SWITCH_ON + 2 * HOUR)
    assert asked(connector)[0] == last  # the next run goes on from there


def test_a_more_answer_that_does_not_move_on_fails_the_run(world):
    connector = world.connector
    connector.feed = [item(1), item(2)]
    add(world)
    switch_on(world)
    stuck = {"protocol": 1, "items": [], "cursor": None, "more": True}
    connector.forced = [(200, stuck, {})]  # sent null, gives null and nothing
    _, error = attempt(world, SWITCH_ON + HOUR)
    assert isinstance(error, SourceError) and len(connector.of_run()) == 1
    assert cursor_of(world) is None and notes(world) == []

    first = {"protocol": 1, "items": [item(1)], "cursor": "after:1", "more": True}
    still = {"protocol": 1, "items": [], "cursor": "after:1", "more": True}
    connector.forced = [(200, first, {}), (200, still, {})]
    _, error = attempt(world, SWITCH_ON + 2 * HOUR)
    assert isinstance(error, SourceError) and len(connector.of_run()) == 2
    assert cursor_of(world) is None and world.store.get_source("erp").last_error
    assert texts(world) in ([], [item(1)["text"]])

    run(world, SWITCH_ON + 3 * HOUR)  # from where it was, and nothing twice
    assert asked(connector)[0] is None
    assert texts(world) == [item(1)["text"], item(2)["text"]]


def test_more_with_only_a_new_cursor_or_only_items_moves_on(world):
    connector = world.connector
    add(world)
    switch_on(world)
    empty = {"protocol": 1, "items": [], "cursor": "skip:1", "more": True}  # a new cursor
    same = {"protocol": 1, "items": [item(1)], "cursor": "skip:1", "more": True}  # an item
    last = {"protocol": 1, "items": [item(2)], "cursor": "skip:2", "more": False}
    connector.forced = [(200, empty, {}), (200, same, {}), (200, last, {})]
    report = run(world, SWITCH_ON + HOUR)
    assert asked(connector) == [None, "skip:1", "skip:1"]
    assert report.saved == 2 and texts(world) == [item(1)["text"], item(2)["text"]]

    repeating = {"protocol": 1, "items": [item(1)], "cursor": "skip:2", "more": True}
    connector.forced = [(200, repeating, {})] * 2_000  # an item each time, the same one
    report = run(world, SWITCH_ON + 2 * HOUR)  # it ends at the bound, and it worked
    assert 1 <= len(connector.of_run()) < 1_000
    assert report.saved == 0 and len(notes(world)) == 2
    connector.forced = [(200, {**last, "items": []}, {})]
    run(world, SWITCH_ON + 3 * HOUR)
    assert asked(connector) == ["skip:2"]  # the next run goes on from the last cursor


# Its notes and the model


class NoModel:
    """A model that must never be asked."""

    name = "c9-no-model"

    def __init__(self) -> None:
        self.requests: list = []

    def complete(self, messages) -> Reply:
        self.requests.append(list(messages))
        return Reply(text="{}", prompt_tokens=1, completion_tokens=1)


def test_its_notes_go_to_no_model_unless_the_operator_allows_it(world):
    world.connector.feed = [
        item(1, text="# Invoice 1\n\n- Ada approved invoice 1.\n- Brook paid invoice 1.\n"),
    ]
    add(world)
    switch_on(world)
    assert run(world, SWITCH_ON + HOUR).saved == 1
    project = world.store.get_project("invoices")
    (page,) = world.store.list_pages(project.id)
    model = NoModel()
    result = arrange_page(world.store, project.id, page.slug, model, withhold=withheld_via())
    assert result.status == "withheld" and model.requests == []


# What the Sources page and source-add propose


def test_the_project_proposed_for_a_new_source_is_its_own_name(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    Store(db).close()
    settings = json.dumps({"url": URL, "secret_env": SECRET_ENV, "projects": []})
    argv = ["source-add", "--db", str(db), "--name", "erp-feed", "--kind", "connector"]
    assert main([*argv, "--config", settings]) == 0
    said = capsys.readouterr().out
    assert "--project erp-feed" in said and SECRET not in said

    store = Store(db)
    try:
        owner = store.sign_in(
            "sso", "o", "o@example.test", "Olive", issuer="https://id.example.test",
            join="default",
        )  # fmt: skip
        base = "http://127.0.0.1:8765"
        with TestClient(create_app(store, public_url=base), base_url=base) as client:
            client.cookies.set("cairn_session", owner.token)
            page = client.get("/sources")
        assert page.status_code == 200
        assert re.search(r"""value=["']erp-feed["']""", page.text)
    finally:
        store.close()


# connector-check


def checker(url: str, *options: str, cwd: Path, secret: str | None = SECRET):
    environ = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    environ["PYTHONPATH"] = os.pathsep.join([str(SRC), environ.get("PYTHONPATH", "")])
    environ.pop(SECRET_ENV, None)
    if secret is not None:
        environ[SECRET_ENV] = secret
    argv = ["connector-check", "--url", url, "--secret-env", SECRET_ENV, *options]
    return subprocess.run(
        [sys.executable, "-m", "cairn.server", *argv],
        cwd=cwd, env=environ, capture_output=True, text=True, timeout=300,
    )  # fmt: skip


def verdicts(stdout: str) -> list[str]:
    """Each line's verdict: "FAILED" when it says so, else "ok", which it must then say."""
    said = []
    for line in [line for line in stdout.splitlines() if line.strip()]:
        if re.search(r"\bFAILED\b", line):
            said.append("FAILED")
        else:
            assert re.search(r"\bok\b", line), line
            said.append("ok")
    return said


def stocked(connector: Connector) -> Connector:
    connector.feed = [item(n) for n in range(1, 6)]
    connector.page = 2
    return connector


def bearers(connector: Connector) -> set[str]:
    return {r.headers.get("authorization", "") for r in connector.requests}


def test_connector_check_passes_a_faithful_connector(tmp_path):
    with Loopback(stocked(Connector())) as served:
        done = checker(served.connector.url, "--since", CHECK_SINCE, "--limit", "3", cwd=tmp_path)
    connector = served.connector
    assert done.returncode == 0, done.stdout + done.stderr
    said = verdicts(done.stdout)
    assert len(said) >= 9 and set(said) == {"ok"}
    shown = done.stdout + done.stderr
    assert SECRET not in shown and TEXT_MARK not in shown and CANARY not in shown
    assert list(tmp_path.iterdir()) == []  # no database, no file
    assert all(r.method == "POST" and r.url == connector.url for r in connector.requests)
    for request in connector.requests:
        assert request.body["protocol"] == 1 and request.body["limit"] == 3
        assert parsed(request.body["since"]) == parsed(CHECK_SINCE)
    right = [r for r in connector.requests if r.headers["authorization"] == f"Bearer {SECRET}"]
    assert len([r for r in right if r.body["cursor"] is None]) >= 2  # the same request twice
    assert {r.body["cursor"] for r in right} >= {None, "after:2", "after:4"}  # it paged
    wrong = [r for r in connector.requests if r not in right]
    assert wrong and all(SECRET not in r.headers.get("authorization", "") for r in wrong)


FAULTS = {  # each breaks one rule; the faults of one group break the same one
    "reordered": "the same request twice",  # an earlier item reordered on the second asking
    "an earlier one gone": "the same request twice",
    "not idempotent": "the same request twice",  # a more answer's cursor differs
    "stuck": "moves on",  # a more answer with no item and the cursor it was sent
    "over bound": "bounds",
    "wrong secret answered": "wrong secret",
    "duplicate id": "unique ids",
    "before since": "since",
    "long cursor": "cursor size",
}


def test_connector_check_fails_exactly_the_rule_a_connector_breaks(tmp_path):
    with Loopback(stocked(Connector())) as served:
        done = checker(served.connector.url, "--since", CHECK_SINCE, "--limit", "3", cwd=tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    rules = len(verdicts(done.stdout))
    failed_at = {}
    for fault in FAULTS:
        connector = stocked(Connector())
        connector.fault = fault
        if fault in ("duplicate id", "before since"):
            connector.page = 2  # the extra item keeps the answer within the limit of 3
        with Loopback(connector) as served:
            done = checker(connector.url, "--since", CHECK_SINCE, "--limit", "3", cwd=tmp_path)
        said = verdicts(done.stdout)
        assert done.returncode == 1, (fault, done.stdout, done.stderr)
        assert len(said) == rules and said.count("FAILED") == 1, (fault, done.stdout)
        failed_at[fault] = said.index("FAILED")
        line = done.stdout.splitlines()[failed_at[fault]]
        if fault in ("over bound", "duplicate id"):
            assert "INV-1" in line, (fault, line)
        shown = done.stdout + done.stderr
        assert TEXT_MARK not in shown and CANARY not in shown, fault
        assert not any(b and b.split()[-1] in shown for b in bearers(served.connector)), fault
        assert list(tmp_path.iterdir()) == []
    for one, other in [(a, b) for a in FAULTS for b in FAULTS if a < b]:
        same = FAULTS[one] == FAULTS[other]
        assert (failed_at[one] == failed_at[other]) == same, (one, other, failed_at)

    # Not faults: a last page that gained an item between the two askings, and a backlog
    # longer than the checker follows (20 answers); the line for moving on says how far.
    filling = Connector()
    filling.feed, filling.fault = [item(1), item(2)], "filling"
    backlog = Connector()
    backlog.feed, backlog.page = [item(n) for n in range(1, 101)], 2
    for connector in (filling, backlog):
        with Loopback(connector):
            done = checker(connector.url, "--since", CHECK_SINCE, "--limit", "3", cwd=tmp_path)
        assert done.returncode == 0, done.stdout + done.stderr
        assert verdicts(done.stdout) == ["ok"] * rules, done.stdout
    assert [len(a["items"]) for a in filling.answers if a is not None][:2] == [2, 3]
    followed = len([r for r in backlog.requests if r.body and r.body["cursor"]])
    assert followed in (19, 20)  # 20 answers, or the first and 20 more: not the 50 there are
    line = done.stdout.splitlines()[failed_at["stuck"]]
    assert re.search(r"\b20\b", line), line


def call(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as stopped:
        return stopped.code if isinstance(stopped.code, int) else 1


def test_connector_check_refuses_bad_options_and_sends_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(SECRET_ENV, SECRET)
    with Loopback(stocked(Connector())) as served:
        url = served.connector.url
        base = ["connector-check", "--url", url, "--secret-env", SECRET_ENV]
        for argv in (
            ["connector-check", "--secret-env", SECRET_ENV],
            ["connector-check", "--url", url],
            [*base, "--limit", "0"],
            [*base, "--limit", "201"],
            [*base, "--limit", "many"],
            [*base, "--since", "yesterday"],
            [*base, "--db", str(tmp_path / "cairn.db")],
            [base[0], "--url", "ftp://erp.example.test/feed", *base[3:]],
            [base[0], "--url", "http://erp.example.test/feed", *base[3:]],
            [base[0], "--url", "https://ada:hunter2-c9@erp.example.test/feed", *base[3:]],
        ):
            assert call(argv) == 2, argv
            printed = capsys.readouterr()
            assert "hunter2-c9" not in printed.out + printed.err and SECRET not in printed.err
        monkeypatch.delenv(SECRET_ENV)
        assert call(base) != 0
        printed = capsys.readouterr()
        assert SECRET_ENV in printed.out + printed.err
        assert served.connector.requests == []
    assert list(tmp_path.iterdir()) == []

    assert call(["connector-check", "--help"]) == 0
    shown = " ".join(capsys.readouterr().out.split()).lower()
    assert "connector" in shown and "secret" in shown and "only" in shown and "--url" in shown


# What ships beside the code


def fenced_json(text: str) -> list:
    found = []
    for block in re.findall(r"```json[^\n]*\n(.*?)```", text, re.S):
        try:
            found.append(json.loads(block))
        except ValueError:
            continue
    return found


def resolve(schema, document):
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        target = document
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        schema = target
    return schema


def test_the_specification_states_the_protocol_with_json_schemas():
    spec = ROOT / "docs" / "connector-protocol.md"
    text = spec.read_text()
    documents = fenced_json(text)
    for beside in sorted((ROOT / "docs").rglob("*.json")):
        if "connector" in str(beside.relative_to(ROOT / "docs")):
            documents.append(json.loads(beside.read_text()))
    schemas = [(d, d) for d in documents if isinstance(d, dict)]
    for document in documents:  # a file may hold both, under $defs or definitions
        for key in ("$defs", "definitions"):
            if isinstance(document, dict) and isinstance(document.get(key), dict):
                schemas += [(s, document) for s in document[key].values() if isinstance(s, dict)]

    def having(keys: set[str]):
        return [
            (s, d)
            for s, d in schemas
            if isinstance(s.get("properties"), dict) and keys <= set(s["properties"])
        ]

    ((request, _),) = having({"protocol", "since", "cursor", "limit"})[:1]
    assert {"protocol", "since", "cursor", "limit"} <= set(request.get("required", []))
    limit = resolve(request["properties"]["limit"], request)
    assert (limit.get("minimum"), limit.get("maximum")) == (1, 200)
    ((answer, document),) = having({"protocol", "items", "cursor", "more"})[:1]
    assert {"protocol", "items"} <= set(answer.get("required", []))
    assert re.search(r'"maxLength": 2000\b', json.dumps(answer["properties"]["cursor"]))
    one = resolve(resolve(answer["properties"]["items"], document).get("items"), document)
    assert {"id", "text", "title", "topic", "source_ref", "at"} <= set(one["properties"])
    assert {"id", "text"} <= set(one.get("required", []))
    assert not {"title", "topic", "source_ref", "at"} & set(one.get("required", []))
    assert resolve(one["properties"]["id"], document).get("maxLength") == 200
    for key, bound in (("title", 200), ("topic", 200), ("source_ref", 500)):
        assert resolve(one["properties"][key], document).get("maxLength") == bound, key

    lowered = text.lower()
    for fact in ("4 mib", "retry-after", "401", "403", "429", "bearer", "changelog", "version 2"):
        assert fact in lowered, fact
    assert "2,000" in text or "2000" in text


def test_the_reference_connector_is_short_standard_library_only_and_documented():
    folder = ROOT / "examples" / "connector"
    code = sorted(folder.glob("*.py"))
    assert folder / "connector.py" in code
    lines = 0
    for path in code:
        source = path.read_text()
        lines += len(source.splitlines())
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] in sys.stdlib_module_names, (path.name, name)
    assert lines <= 400
    readme = (folder / "README.md").read_text()
    for said in ("connector.py", "connector-check", "source-add", "connector"):
        assert said in readme, said


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_reference_connector_passes_the_checker_and_a_source_writes_its_folder(tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    given = {}
    for n in range(1, 6):
        path = folder / f"memo-{n}.md"
        given[path.name] = f"# Memo {n}\n\nCy filed memo {n} for the north warehouse.\n"
        path.write_text(given[path.name])
        stamp = (SWITCH_ON + (6 - n) * 10 * MINUTE).timestamp()  # memo-5 is the oldest
        os.utime(path, (stamp, stamp))
    (folder / "draft.txt").write_text("Not Markdown: not an item.\n")
    port = free_port()
    environ = {**os.environ, SECRET_ENV: SECRET, "PYTHONDONTWRITEBYTECODE": "1"}
    script = ROOT / "examples" / "connector" / "connector.py"
    argv = [sys.executable, str(script), "--folder", str(folder), "--port", str(port)]
    served = subprocess.Popen(
        [*argv, "--secret-env", SECRET_ENV], cwd=tmp_path, env=environ,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )  # fmt: skip
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(600):  # up to a minute for it to listen
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                with pytest.raises(subprocess.TimeoutExpired):
                    served.wait(timeout=0.1)  # still starting, not exited
        done = checker(url, "--since", CHECK_SINCE, "--limit", "2", cwd=tmp_path / "notes")
        assert done.returncode == 0, done.stdout + done.stderr
        assert set(verdicts(done.stdout)) == {"ok"}

        clock = Clock(SWITCH_ON - DAY)
        store = Store(tmp_path / "cairn.db", clock=clock)
        try:
            store.create_project("notes")
            settings = {"url": url, "secret_env": SECRET_ENV, "limit": 2}
            store.add_source("notes", "connector", settings)
            clock.now = SWITCH_ON
            store.enable_source("notes", projects=["notes"])

            async def sync() -> int:
                async with source_client() as http:
                    report = await run_source(store, "notes", http=http, env=ENV, clock=clock)
                return report.saved

            clock.now = SWITCH_ON + 2 * HOUR
            assert asyncio.run(sync()) == 5
            held = store.list_notes(store.get_project("notes").id)
            assert [c.text for c in held] == [given[f"memo-{n}.md"] for n in (5, 4, 3, 2, 1)]
            assert {c.via for c in held} == {"connector"}
            assert asyncio.run(sync()) == 0
            later = folder / "memo-6.md"
            later.write_text("# Memo 6\n\nDee closed the north warehouse file.\n")
            stamp = (SWITCH_ON + HOUR).timestamp()
            os.utime(later, (stamp, stamp))
            assert asyncio.run(sync()) == 1
            assert store.list_notes(store.get_project("notes").id)[-1].text == later.read_text()
        finally:
            store.close()
    finally:
        served.terminate()
        _, errors = served.communicate(timeout=30)
    assert SECRET not in errors.decode()


def test_the_sources_guide_has_a_section_for_a_connector_of_your_own():
    guide = files("cairn.guides").joinpath("sources.md").read_text()
    heading = re.search(r"^(#+) A connector of your own\s*$", guide, re.M | re.I)
    assert heading
    level = len(heading.group(1))
    rest = guide[heading.end() :]
    following = re.search(rf"^#{{1,{level}}} ", rest, re.M)
    section = rest[: following.start()] if following else rest
    assert "connector-check" in section and "connector-protocol" in section
