"""A read never answers a body in which a code fence runs from one claim into the next
(fourth report, must-fix 4).

A version an earlier build stored can hold more such fences than a body closes
(cairn.spans.CLOSERS_MAX), and a stored version is not an import, so nothing refused it: its
read rendered sixteen closed and sent the rest as the body. Now the read answers the claims
as always and, in the body's place, one fixed sentence, with `body_not_shown` true; every
other page is answered as before, with no such field. Each body here is judged by the
Markdown parser on what the reader was given. Made-up notes."""

import html
import io
import zipfile

import pytest
from starlette.testclient import TestClient

import cairn.server
from cairn.compiler import render_body, render_body_before_review_four
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.server import create_app
from cairn.spans import CLOSERS_MAX, claim_id
from cairn.store import Claim, Store
from cairn.web.ui.memory_pages import page_view
from support.compiler.assembled_fence import READERS, no_closer, parses, saved_by_23c24229, triples
from support.compiler.core_promise_title import BASE, PRE, SAME_ORIGIN, mcp_page

NOT_SHOWN = (
    "The body of this version is not shown. Its notes' Markdown cannot be put together"
    " safely: a code fence would run from one claim into the next, and a body closes at most"
    " 16 such fences. Read the claims, where each claim's text and citations are exact. The"
    " body is shown again once a note the page cites is forgotten, which writes the page again."
)
PAGE_FIELDS = {"project", "slug", "title", "version", "versions", "body", "claims", "metrics"}
SIXTEEN = (
    "# Ops\n\n"
    + "\n\n".join(
        f"- item {n}\n\n    ~~~\n    sample {n}\n    ~~~\n\n  After {n}." for n in range(16)
    )
    + "\n"
)
ENDPOINTS = ["memory", "rest", "mcp"]


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "cairn.db")
    store.create_project("alpha")
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        assert client.post("/login", data={"key": key}, headers=SAME_ORIGIN).status_code < 400
        yield store, client, key
    store.close()


def read(env, endpoint: str, version: int | None = None) -> dict:
    """The page `ops` as Memory.page, REST or the MCP tool answers it."""
    store, client, key = env
    if endpoint == "memory":
        return Memory(store, lint_gate).page("alpha", "ops", version)
    if endpoint == "mcp":
        return mcp_page(client, key, "ops", version)
    params = {} if version is None else {"version": version}
    response = client.get(
        "/api/projects/alpha/pages/ops", params=params, headers={"x-api-key": key}
    )
    assert response.is_success
    return response.json()


def web(env, version: int | None = None) -> tuple[str, str]:
    """The web's page view of `ops`: all of it, and what it shows as the Markdown body."""
    _, client, _ = env
    response = client.get("/b/alpha/p/ops", params={} if version is None else {"version": version})
    assert response.is_success
    text = html.unescape(response.text)
    return text, html.unescape(PRE.search(response.text).group(1))


def runs_on(body: str, claims) -> bool:
    """Whether a reader of `body` reads a code fence that starts in one claim's lines, or in
    no claim's, and covers a line of another claim: the lines of each claim are found in the
    body as its text, and the fences are the Markdown parser's, with raw HTML off and on."""
    lines = body.split("\n")
    owner: dict[int, str] = {}
    for claim in claims:
        text = claim.text.split("\n")
        for at in range(len(lines) - len(text) + 1):
            if lines[at : at + len(text)] == text:
                owner.update(dict.fromkeys(range(at, at + len(text)), claim.id))
    for reader in READERS.values():
        for token in reader.parse(body):
            if token.type != "fence" or not token.map:
                continue
            first, end = token.map
            if any(owner.get(n, owner.get(first)) != owner.get(first) for n in range(first, end)):
                return True
    return False


def exact(answer: dict, page) -> bool:
    """Whether the answer holds the version's claims, each with its text and citations."""
    return answer["version"] == page.version and [
        (claim["id"], claim["text"], tuple(claim["passage_ids"])) for claim in answer["claims"]
    ] == [(claim.id, claim.text, claim.span_ids) for claim in page.claims]


def test_the_sentence_is_one_paragraph_that_opens_no_fence():
    assert "`" not in NOT_SHOWN and "~" not in NOT_SHOWN and "\n" not in NOT_SHOWN
    assert str(CLOSERS_MAX) in NOT_SHOWN
    for reader in READERS.values():
        assert [token.type for token in reader.parse(NOT_SHOWN)] == [
            "paragraph_open",
            "inline",
            "paragraph_close",
        ]


# Seventeen: the claims, the sentence in the body's place, and the field


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_a_version_stored_with_seventeen_is_answered_with_its_claims_and_no_body(env, endpoint):
    page, _ = saved_by_23c24229(env[0], triples(CLOSERS_MAX + 1))
    assert runs_on(page.body, page.claims)  # as stored: the last note reads as code
    spans = env[0].get_spans([i for claim in page.claims for i in claim.span_ids])
    assert runs_on(render_body("Ops", page.claims, spans), page.claims)  # and as rendered
    answer = read(env, endpoint)
    assert exact(answer, page) and len(answer["claims"]) == 3 * (CLOSERS_MAX + 1)
    assert not runs_on(answer["body"], page.claims)
    assert answer["body"] == NOT_SHOWN
    assert answer["body_not_shown"] is True
    assert set(answer) == PAGE_FIELDS | {"body_not_shown"}


def test_the_web_shows_each_claim_of_a_version_stored_with_seventeen_and_no_body(env):
    page, _ = saved_by_23c24229(env[0], triples(CLOSERS_MAX + 1))
    spans = env[0].get_spans([i for claim in page.claims for i in claim.span_ids])
    text, shown = web(env)
    assert shown == NOT_SHOWN and text.count(NOT_SHOWN) == 1
    assert text.count('<article class="claim ') == len(page.claims)
    assert all(f'id="{claim.id}"' in text for claim in page.claims)
    # neither the stored body nor its rendering, whole or from the claim left open, is there
    assert page.body not in text and render_body("Ops", page.claims, spans) not in text
    assert f"sample {CLOSERS_MAX}\n\n  After {CLOSERS_MAX}." not in text
    memory = Memory(env[0], lint_gate)
    view = page_view(memory, memory.project("alpha"), page)
    assert view["body"] == NOT_SHOWN and len(view["items"]) == len(page.claims)


# Sixteen: as before, byte for byte, and no field


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_a_version_stored_with_sixteen_is_answered_as_before(env, endpoint):
    page, _ = saved_by_23c24229(env[0], triples(CLOSERS_MAX))
    assert runs_on(page.body, page.claims)  # as stored
    answer = read(env, endpoint)
    assert exact(answer, page)
    assert answer["body"].encode() == SIXTEEN.encode()
    assert not runs_on(answer["body"], page.claims)
    assert set(answer) == PAGE_FIELDS  # no field a healthy page did not have
    assert web(env)[1] == SIXTEEN


def test_a_page_this_version_saved_has_no_such_field(env):
    store = env[0]
    memory = Memory(store, lint_gate)
    assert memory.note("alpha", "Ada keeps `port 80` open.", title="Ops")["status"] == "saved"
    for endpoint in ENDPOINTS:
        answer = read(env, endpoint)
        assert set(answer) == PAGE_FIELDS
        assert answer["body"] == "# Ops\n\nAda keeps `port 80` open.\n"


# One step past: an earlier version, a fence no closer closes, a refused note, a forget


def test_an_earlier_version_with_seventeen_is_not_shown_while_the_latest_is(env):
    store = env[0]
    old, _ = saved_by_23c24229(store, triples(CLOSERS_MAX + 1))
    latest = store.save_page(
        old.project_id, "ops", "Ops", "# Ops\n\n- item 0\n", old.claims[:1], expected_version=1
    )
    assert (old.version, latest.version) == (1, 2)
    for endpoint in ENDPOINTS:
        earlier, now = read(env, endpoint, 1), read(env, endpoint)
        assert exact(earlier, old) and earlier["body"] == NOT_SHOWN
        assert earlier["body_not_shown"] is True
        assert exact(now, latest) and now["body"] == "# Ops\n\n- item 0\n"
        assert set(now) == PAGE_FIELDS
    assert web(env, 1)[1] == NOT_SHOWN
    assert web(env)[1] == "# Ops\n\n- item 0\n"


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_a_version_whose_fence_no_closer_closes_is_not_shown_either(env, endpoint, monkeypatch):
    """The other way the renderer leaves a fence to run on (no such page is known; the
    parser's answer is stood in for): one fence, far under the cap."""
    page, _ = saved_by_23c24229(env[0], triples(1))
    no_closer(monkeypatch)
    answer = read(env, endpoint)
    assert exact(answer, page)
    assert (answer["body"], answer["body_not_shown"]) == (NOT_SHOWN, True)
    assert web(env)[1] == NOT_SHOWN


def test_a_note_the_page_refuses_leaves_the_version_and_its_read_as_they_were(env):
    store = env[0]
    page, _ = saved_by_23c24229(store, triples(CLOSERS_MAX + 1))
    answer = Memory(store, lint_gate).note("alpha", "More.", title="Ops", topic="Ops")
    assert (answer["status"], answer["page"]) == ("rejected", "ops")
    assert store.get_page(page.project_id, "ops") == page
    for endpoint in ENDPOINTS:
        again = read(env, endpoint)
        assert exact(again, page) and again["body"] == NOT_SHOWN and again["body_not_shown"]


def test_the_read_is_safe_before_and_after_a_forget_and_the_body_is_shown_again(env):
    """The sentence's last clause. Which claims a forget keeps is the forget's own rule and
    is not asked here (a claim whose fence the body would not close leaves with it): only
    that what a reader is given holds no fence across claims, and that the body of the
    version the forget wrote is shown."""
    store = env[0]
    page, notes = saved_by_23c24229(store, [*triples(CLOSERS_MAX + 1), "Quill keeps the log."])
    for endpoint in ENDPOINTS:
        before = read(env, endpoint)
        assert exact(before, page) and before["body"] == NOT_SHOWN and before["body_not_shown"]
    store.forget_note(notes[-1].id)
    left = store.get_page(page.project_id, "ops")
    assert left.version == page.version + 1 and left.claims
    for endpoint in ENDPOINTS:
        after = read(env, endpoint)
        assert exact(after, left)
        assert set(after) == PAGE_FIELDS and after["body"] == left.body
        assert not runs_on(after["body"], left.claims)
    text, shown = web(env)
    assert shown == left.body and NOT_SHOWN not in text


# Cost: a read renders once, as before, whatever the page holds


def stored(store: Store, texts: list[str]):
    """The page `ops` of these notes as this version writes it, saved at the store."""
    project = store.get_project("alpha")
    notes = [store.add_note(project.id, text, title="Ops", topic="Ops")[0] for text in texts]
    spans = {s.id: s for note in notes for s in store.note_spans(note.id)}
    claims = [Claim(id=claim_id(i), text=s.text, span_ids=(i,)) for i, s in spans.items()]
    return store.save_page(project.id, "ops", "Ops", render_body("Ops", claims, spans), claims)


READS = {
    # the notes: the parses of one read (each claim that holds a run alone, then the body)
    "no backtick and no tilde": ([f"Ada keeps log {n}." for n in range(200)], 0),
    "a closed fence in every claim": ([f"```\nlog {n}\n```" for n in range(200)], 200 + 1),
    "sixteen fences to close": (triples(CLOSERS_MAX), CLOSERS_MAX + 1 + CLOSERS_MAX),
}


@pytest.mark.parametrize("name", list(READS))
def test_a_read_of_a_page_costs_one_render(env, monkeypatch, name):
    store = env[0]
    texts, count = READS[name]
    page = stored(store, texts)
    spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
    assert parses(monkeypatch, lambda: render_body("Ops", page.claims, spans)) == count
    memory = Memory(store, lint_gate)
    assert parses(monkeypatch, lambda: memory.page("alpha", "ops")) == count
    project = memory.project("alpha")
    assert parses(monkeypatch, lambda: page_view(memory, project, page)) == count


def test_a_read_of_a_version_that_is_not_shown_costs_one_render_too(env, monkeypatch):
    store = env[0]
    page, _ = saved_by_23c24229(store, triples(CLOSERS_MAX + 1))
    memory = Memory(store, lint_gate)
    # each of the 17 passages that hold a run alone; the body once and once for each closer
    count = CLOSERS_MAX + 1 + 1 + CLOSERS_MAX
    assert parses(monkeypatch, lambda: memory.page("alpha", "ops")) == count
    project = memory.project("alpha")
    assert parses(monkeypatch, lambda: page_view(memory, project, page)) == count


# What an agent is told of the field


def test_the_tool_and_the_contract_name_the_field(env):
    _, client, key = env
    listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "x-api-key": key,
    }
    tools = client.post("/mcp", headers=headers, json=listing).json()["result"]["tools"]
    (page,) = [tool for tool in tools if tool["name"] == "page"]
    assert page["description"].count("body_not_shown") == 1
    assert cairn.server.__doc__.count('"body_not_shown"') == 1


# A limit that stays: the export copies a version's Markdown as it is stored


def exported_md(store: Store) -> str:
    archive = io.BytesIO()
    store.export(archive)
    with zipfile.ZipFile(archive) as held:
        (name,) = [n for n in held.namelist() if n.endswith("pages/ops.md")]
        return held.read(name).decode()


def test_the_export_holds_a_version_stored_with_seventeen_as_stored_until_its_next_save(env):
    """The exported `.md` is the stored body, the fences across claims with it, until the
    page is saved again: here by a forget, the one save such a page takes."""
    store = env[0]
    page, notes = saved_by_23c24229(store, [*triples(CLOSERS_MAX + 1), "Quill keeps the log."])
    assert exported_md(store) == page.body and runs_on(page.body, page.claims)
    store.forget_note(notes[-1].id)
    left = store.get_page(page.project_id, "ops")
    assert exported_md(store) == left.body and not runs_on(left.body, left.claims)


def test_the_export_of_such_a_version_whose_title_holds_a_line_break_is_its_rendering(env):
    """The export renders a version whose title holds a line break (cairn.topics.one_line),
    with render_body: sixteen fences closed and the rest left to run, so the limit is the
    same, a fence across claims in the `.md` until the page is saved again."""
    store = env[0]
    page, _ = saved_by_23c24229(store, triples(CLOSERS_MAX + 1))
    spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
    body = render_body_before_review_four("Ops\nmore", page.claims, spans)
    store.save_page(page.project_id, "ops", "Ops\nmore", body, page.claims, expected_version=1)
    written = exported_md(store)
    assert written == render_body("Ops\nmore", page.claims, spans)
    assert written.count("\n    ~~~\n\n  After") == CLOSERS_MAX and runs_on(written, page.claims)
    for endpoint in ENDPOINTS:  # the read of that version is the sentence all the same
        assert read(env, endpoint)["body"] == NOT_SHOWN
