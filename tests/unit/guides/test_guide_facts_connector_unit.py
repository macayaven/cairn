"""Sentences of src/cairn/guides/sources.md ("A connector of your own") and privacy.md about a
connector source (C9), each pinned here so the guide cannot go false unnoticed: the sentence
is in the test's docstring and in the guide, and the test asks the running code for it. The
connector is a stand-in behind httpx.MockTransport; the clock is injected at 2099; nothing
leaves the process. Fixtures are synthetic."""

import asyncio
import hashlib
import importlib.util
import json
import re
import shlex
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.server import create_app, main
from cairn.sources import KINDS, SourceError, run_source, withheld_via
from cairn.sources import connector as c
from cairn.sources import connector_check as check
from cairn.sources.runner import NOTHING_NEW
from cairn.store import Store
from support.cookie_names import session_cookie
from support.guide_source import guide_source
from support.paths import REPO_ROOT
from support.sources.connector_check import Feed

ROOT = REPO_ROOT
RAW = guide_source("sources")
GUIDE = " ".join(RAW.split())
PRIVACY = " ".join(guide_source("privacy").split())
SECRET = "guide-" + "made-up-" + "connector-secret-5e1d"
ENV = {"FEED_SECRET": SECRET}
URL = "https://feed.example.test/items"
SINCE = datetime(2099, 6, 1, 9, 0, tzinfo=UTC)
BASE = "http://127.0.0.1:8765"
ISSUER = "https://id.example.test/pool-1"


def said(sentence: str, text: str = GUIDE) -> None:
    """The guide says `sentence`, word for word (whitespace aside)."""
    assert " ".join(sentence.split()) in text, sentence


def section() -> str:
    return RAW.split("## A connector of your own", 1)[1].split("\n## ", 1)[0]


def item(n: int, **fields) -> dict:
    return {
        "id": f"P-{n}",
        "text": f"# Pump {n}\n\nPump {n} runs at {n}0 litres a minute.\n",
    } | fields


def answer(items=(), cursor="after:1", more=False, protocol=1) -> dict:
    return {"protocol": protocol, "items": list(items), "cursor": cursor, "more": more}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Connector:
    """Answers `answers` in turn, each (status, body), and keeps each request."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.answers.pop(0)
        return httpx.Response(status, json=body)


@pytest.fixture
def clock():
    return Clock(SINCE - timedelta(days=1))


@pytest.fixture
def store(tmp_path, clock):
    s = Store(tmp_path / "cairn.db", clock=clock)
    s.create_project("pumps")
    s.add_source("feed", "connector", {"url": URL, "secret_env": "FEED_SECRET"})
    clock.now = SINCE
    s.enable_source("feed", projects=["pumps"])
    clock.now = SINCE + timedelta(hours=1)
    yield s
    s.close()


def run(store, connector, clock):
    async def once():
        http = httpx.AsyncClient(transport=httpx.MockTransport(connector.handle))
        async with http:
            return await run_source(store, "feed", http=http, env=ENV, clock=clock)

    return asyncio.run(once())


def texts(store) -> list[str]:
    return [note.text for note in store.list_notes(store.get_project("pumps").id)]


def test_the_guide_points_to_a_connector_of_your_own_and_its_protocol():
    """sources.md: "For any other system, the server can read [a connector of your
    own](#a-connector-of-your-own): a small web service you write that serves that system's
    items." and "What a connector must do is the connector protocol, version 1, written down
    in the repository's `docs/connector-protocol.md`.\""""
    said(
        "For any other system, the server can read [a connector of your own]"
        "(#a-connector-of-your-own): a small web service you write that serves that system's"
        " items."
    )
    said(
        "What a connector must do is the connector protocol, version 1, written down in the"
        " repository's `docs/connector-protocol.md`."
    )
    assert "connector" in KINDS and re.search(r"^## A connector of your own$", RAW, re.M)
    spec = (ROOT / "docs" / "connector-protocol.md").read_text()
    assert spec.startswith("# The connector protocol, version 1")


def test_a_connector_is_a_web_service_of_your_own_reached_by_its_url():
    """sources.md: "For a system cairn does not read itself, you can write a connector: a
    small web service of your own, in any language, that serves that system's items.\""""
    said(
        "For a system cairn does not read itself, you can write a connector: a small web"
        " service of your own, in any language, that serves that system's items."
    )
    unfilled = (ROOT / "src" / "cairn" / "guides" / "sources.md").read_text()
    assert "For a system {{name}} does not read itself" in unfilled  # a brand's name there
    assert c.SETTINGS == ("url", "secret_env", "projects", "limit")  # an address, never code


def test_its_notes_go_to_no_model_unless_the_operator_allows_it():
    """sources.md: "cairn cannot know whose words a connector brings, so a page that holds its
    notes is not sent to the model the operator connects to arrange pages unless the operator
    allows it, as for the Google sources ([Sources the server
    reads](#sources-the-server-reads)).\""""
    said(
        "cairn cannot know whose words a connector brings, so a page that holds its notes is"
        " not sent to the model the operator connects to arrange pages unless the operator"
        " allows it, as for the Google sources ([Sources the server reads]"
        "(#sources-the-server-reads))."
    )
    assert KINDS["connector"].withheld and c.VIA in withheld_via()
    google = {name for name in KINDS if name.startswith("google_")} | {"gmail"}
    assert all(KINDS[name].withheld for name in google)


def test_each_run_asks_for_what_is_new_and_the_server_guards_against_secrets(store, clock):
    """sources.md: "Each run, the server asks it for what is new, and it answers with a list
    of items, each with an id and a text. The server does the rest: the schedule, the switch,
    the bounds, the guard against secrets, and writing each item once.\""""
    said(
        "Each run, the server asks it for what is new, and it answers with a list of items,"
        " each with an id and a text. The server does the rest: the schedule, the switch, the"
        " bounds, the guard against secrets, and writing each item once."
    )
    leaked = item(2, text=f"# Pump 2\n\nThe pump's key is {SECRET}.\n")
    connector = Connector((200, answer([item(1), leaked])))
    report = run(store, connector, clock)
    assert (report.saved, report.skipped) == (1, 1)
    assert not any(SECRET in text for text in texts(store))
    assert connector.requests[0].method == "POST"


def test_each_item_is_written_once_by_its_id_and_an_edit_is_not(store, clock):
    """sources.md: "Each item is written once, by its id: what the connector later sends
    under the same id is not written, so an edited item needs a new id.\""""
    said(
        "Each item is written once, by its id: what the connector later sends under the same"
        " id is not written, so an edited item needs a new id."
    )
    first = item(1)
    edited = item(1, text="# Pump 1\n\nPump 1 was replaced.\n")
    assert run(store, Connector((200, answer([first]))), clock).saved == 1
    report = run(store, Connector((200, answer([edited], "after:2"))), clock)
    assert (report.saved, report.unchanged) == (0, 1)
    renamed = edited | {"id": "P-1-v2"}
    assert run(store, Connector((200, answer([renamed], "after:3"))), clock).saved == 1
    assert texts(store) == [first["text"], edited["text"]]


def test_an_item_that_was_refused_is_judged_again(store, clock):
    """sources.md: "An item that was refused is judged again when it comes again.\""""
    said("An item that was refused is judged again when it comes again.")
    too_long = item(1, title="t" * (c.TITLE_MAX + 1))
    report = run(store, Connector((200, answer([too_long]))), clock)
    assert (report.saved, report.skipped) == (0, 1)
    assert run(store, Connector((200, answer([item(1)], "after:2"))), clock).saved == 1


def test_the_secret_is_named_never_given_and_goes_only_as_the_bearer_to_url(store, clock):
    """sources.md: "The config names the environment variable that holds the secret
    (`secret_env`), never the secret itself. The server sends the secret only as the
    request's Bearer, and only to the connector's `url`, which is `https`, or `http` only to
    `127.0.0.1` or `localhost`.\""""
    said(
        "The config names the environment variable that holds the secret (`secret_env`),"
        " never the secret itself. The server sends the secret only as the request's Bearer,"
        " and only to the connector's `url`, which is `https`, or `http` only to `127.0.0.1`"
        " or `localhost`."
    )
    token = "ghp_" + "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zAaB3d"  # shaped like a credential
    with pytest.raises(ValueError, match="secret_env") as refused:
        c.check_config({"url": URL, "secret_env": token})
    assert token not in str(refused.value)
    connector = Connector((200, answer([item(1)])))
    run(store, connector, clock)
    (request,) = connector.requests
    assert str(request.url) == URL and request.headers["authorization"] == f"Bearer {SECRET}"
    assert SECRET not in request.content.decode()
    assert c.check_url("http://127.0.0.1:8811/") and c.check_url("http://localhost:8811/")
    with pytest.raises(ValueError, match="url"):
        c.check_url("http://feed.example.test/items")


def test_a_connector_source_starts_off_and_reads_from_its_switch_on(store, clock):
    """sources.md: "A connector source is added off, and reads only what happens from its
    switch-on on: an item dated before then is not written.\""""
    said(
        "A connector source is added off, and reads only what happens from its switch-on on:"
        " an item dated before then is not written."
    )
    assert store.add_source("other", "connector", {"url": URL, "secret_env": "X"}).enabled is False
    before = item(1, at=c.stamp(SINCE - timedelta(minutes=1)))
    after = item(2, at=c.stamp(SINCE + timedelta(minutes=1)))
    connector = Connector((200, answer([before, after])))
    assert run(store, connector, clock).saved == 1
    assert texts(store) == [after["text"]]
    assert c.moment(json.loads(connector.requests[0].content)["since"]) == SINCE


def test_the_sources_page_proposes_a_project_named_like_the_source(tmp_path):
    """sources.md: "For a connector source that names no project yet, the Sources page proposes
    a project named like the source itself.\""""
    said(
        "For a connector source that names no project yet, the Sources page proposes a project"
        " named like the source itself."
    )
    store = Store(tmp_path / "page.db")
    try:
        owner = store.sign_in("sso", "o", "o@example.test", "Olive", issuer=ISSUER, join="default")
        store.add_source("north-pumps", "connector", {"url": URL, "secret_env": "FEED_SECRET"})
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            client.cookies.set(session_cookie(), owner.token)
            page = client.get("/sources").text
        (field,) = re.findall(r'<input type="text" id="source-north-pumps-new-project"[^>]*>', page)
        assert 'value="north-pumps"' in field
    finally:
        store.close()


def test_a_failing_connector_says_why_and_moves_nothing_on(store, clock):
    """sources.md: "The Sources page shows why in words: for instance that the connector
    refused the secret in the variable named (HTTP 401 or 403), that it answered HTTP 500, or
    that its answer breaks protocol 1, and how. A failed run moves nothing on, so the next run
    asks the same request again.\""""
    said(
        "The Sources page shows why in words: for instance that the connector refused the"
        " secret in the variable named (HTTP 401 or 403), that it answered HTTP 500, or that"
        " its answer breaks protocol 1, and how. A failed run moves nothing on, so the next"
        " run asks the same request again."
    )
    run(store, Connector((200, answer([item(1)], "after:1"))), clock)
    kept = store.get_source("feed").cursor
    asked = []
    for status, body, words in (
        (401, {}, "refused the secret in FEED_SECRET (HTTP 401)"),
        (403, {}, "refused the secret in FEED_SECRET (HTTP 403)"),
        (500, {}, "HTTP 500"),
        (200, answer(protocol=2), "breaks protocol 1"),
    ):
        connector = Connector((status, body))
        with pytest.raises(SourceError):
            run(store, connector, clock)
        assert words in store.get_source("feed").last_error, words
        assert store.get_source("feed").cursor == kept
        asked.append(connector.requests[0].content)
    assert len(set(asked)) == 1 and b'"after:1"' in asked[0]


def test_a_run_that_stopped_early_says_why_under_the_counts(store, clock, tmp_path):
    """sources.md: "A run that worked but stopped early says why under the source's counts:
    the connector asked to slow down (HTTP 429), or it still had more after 20 answers, the
    most one run asks for; when nothing it gave was new, it says that too.\""""
    said(
        "A run that worked but stopped early says why under the source's counts: the"
        " connector asked to slow down (HTTP 429), or it still had more after 20 answers, the"
        " most one run asks for; when nothing it gave was new, it says that too."
    )
    slowed = Connector((200, answer([item(1)], "after:1", more=True)), (429, {}))
    report = run(store, slowed, clock)
    assert report.saved == 1 and report.said == c.SLOWED
    assert "asked to slow down (HTTP 429)" in report.said
    repeating = Connector(
        *[(200, answer([item(1)], f"after:{n}", more=True)) for n in range(c.ANSWERS_PER_RUN)]
    )
    report = run(store, repeating, clock)
    assert report.saved == 0 and len(repeating.requests) == c.ANSWERS_PER_RUN == 20
    assert report.said == f"{c.BOUND_REACHED} {NOTHING_NEW}"
    assert "still had more after 20 answers, the most one run asks for" in report.said
    assert "nothing it gave was new" in report.said
    owner = store.sign_in("sso", "o", "o@example.test", "Olive", issuer=ISSUER, join="default")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        client.cookies.set(session_cookie(), owner.token)
        page = " ".join(client.get("/sources").text.split())
    assert "still had more after 20 answers" in page and "nothing it gave was new" in page


def guide_command(name: str) -> list[str]:
    (line,) = [
        line.strip()
        for line in section().splitlines()
        if line.strip().startswith(f"python -m cairn.server {name} ")
    ]
    return shlex.split(line)[3:]


def test_connector_check_prints_a_line_per_rule_and_writes_nothing(tmp_path, monkeypatch, capsys):
    """sources.md: "`connector-check` asks the connector as a source would and prints one
    line per rule of the protocol, `ok` or `FAILED`; it exits 0 when every rule holds and 1
    when one fails. It writes nothing, and never prints the secret or an item's text:\""""
    said(
        "`connector-check` asks the connector as a source would and prints one line per rule"
        " of the protocol, `ok` or `FAILED`; it exits 0 when every rule holds and 1 when one"
        " fails. It writes nothing, and never prints the secret or an item's text:"
    )
    from support.sources.connector_check import SECRET as FEED_SECRET

    argv = guide_command("connector-check")
    assert argv[argv.index("--secret-env") + 1] == "FEED_SECRET"
    monkeypatch.setenv("FEED_SECRET", FEED_SECRET)
    monkeypatch.chdir(tmp_path)
    for broken, code in ((False, 0), (True, 1)):
        feed = Feed()
        if broken:
            feed.items[0]["title"] = "t" * (c.TITLE_MAX + 1)
        transport = httpx.MockTransport(feed.handle)
        monkeypatch.setattr(
            check, "source_client", lambda t=transport: httpx.AsyncClient(transport=t)
        )
        assert main(argv) == code
        printed = capsys.readouterr()
        lines = printed.out.splitlines()
        assert len(lines) == len(check.RULES)
        assert all(line.startswith(("ok ", "FAILED ")) for line in lines)
        assert FEED_SECRET not in printed.out + printed.err
        assert "Valve" not in printed.out + printed.err  # the items' text
    assert list(tmp_path.iterdir()) == []


def test_the_guide_s_source_add_line_adds_a_connector_source_that_starts_off(tmp_path, capsys):
    """sources.md: "Add it as a source, which starts off, then switch it on:\""""
    said("Add it as a source, which starts off, then switch it on:")
    argv = guide_command("source-add")
    db = tmp_path / "guide.db"
    Store(db).close()
    argv[argv.index("--db") + 1] = str(db)
    assert main(argv) == 0
    added = Store(db)
    try:
        (source,) = added.list_sources()
        assert (source.kind, source.enabled) == ("connector", False)
    finally:
        added.close()
    assert f"--project {source.name}" in capsys.readouterr().out


def load_reference():
    path = ROOT / "examples" / "connector" / "connector.py"
    spec = importlib.util.spec_from_file_location("reference_connector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_reference_connector_is_one_file_serving_a_folder_s_markdown(tmp_path):
    """sources.md: "Write the connector, or copy the reference one in `examples/connector/`:
    one file of Python, standard library only, that serves the Markdown files of a folder.\""""
    said(
        "Write the connector, or copy the reference one in `examples/connector/`: one file of"
        " Python, standard library only, that serves the Markdown files of a folder."
    )
    assert [p.name for p in (ROOT / "examples" / "connector").glob("*.py")] == ["connector.py"]
    reference = load_reference()
    (tmp_path / "pump-1.md").write_text(item(1)["text"])
    (tmp_path / "pump-2.txt").write_text("Not Markdown.\n")
    (tmp_path / f"{'p' * 198}.md").write_text(item(3)["text"])  # a name past an id's 200
    request = {"protocol": 1, "since": "2000-01-01T00:00:00Z", "cursor": None, "limit": 5}
    status, body = reference.answer(tmp_path, request)
    assert status == 200 and [i["id"] for i in body["items"]] == ["pump-1.md"]
    read = c.examine(body, sent=None, limit=5, since=datetime(2000, 1, 1, tzinfo=UTC), secrets=())
    assert read.findings == [] and body["more"] is False


def test_privacy_says_a_source_keeps_a_digest_of_each_item_s_id(store, clock, tmp_path):
    """privacy.md: "For each item a source wrote, a digest of the item's id and when it was
    written, never the id itself, until the source is removed: it is how a source writes an
    item once.\""""
    said(
        "For each item a source wrote, a digest of the item's id and when it was written,"
        " never the id itself, until the source is removed: it is how a source writes an item"
        " once.",
        PRIVACY,
    )
    run(store, Connector((200, answer([item(1)]))), clock)
    with sqlite3.connect(tmp_path / "cairn.db") as db:
        rows = db.execute("SELECT item, written_at FROM source_items").fetchall()
    ((kept, written),) = rows
    assert kept == hashlib.sha256(b"P-1").hexdigest()[: len(kept)] and "P-1" not in kept
    assert written
    assert store.remove_source("feed")
    with sqlite3.connect(tmp_path / "cairn.db") as db:
        assert db.execute("SELECT count(*) FROM source_items").fetchone() == (0,)
