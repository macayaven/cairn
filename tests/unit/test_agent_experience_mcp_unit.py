"""8.1 to 8.4 and 8.6, as an agent meets them over MCP (and REST, where the same answer goes):
when each note was written, what the instructions say of notes that disagree, error
sentences that name only what an agent can do, the graph as a tool, and a search with no
words. The app runs in process over a real store in a temporary file; nothing leaves it."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.memory import NOTHING_LEFT_OUT, FiledOnce, Memory, searched
from cairn.server import create_app
from cairn.store import Store
from cairn.store.models import Searched
from cairn.web.mcp import INSTRUCTIONS, TREAT_AS_INFORMATION, instructions

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
WRITTEN = datetime(2099, 2, 3, 4, 5, tzinfo=UTC)


def rpc(client, key: str, method: str, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    r = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def tool(client, key: str, name: str, /, **arguments) -> dict:
    return rpc(client, key, "tools/call", {"name": name, "arguments": arguments})["result"]


def answer(client, key: str, name: str, /, **arguments) -> dict:
    result = tool(client, key, name, **arguments)
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def refusal(client, key: str, name: str, /, **arguments) -> str:
    result = tool(client, key, name, **arguments)
    assert result["isError"] is True, result
    return result["content"][0]["text"]


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "cairn.db", clock=lambda: WRITTEN)
    store.create_project("ops")
    key = store.create_key("owner")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        yield SimpleNamespace(client=client, store=store, key=key, h={"x-api-key": key})
    store.close()


def call(env, name: str, /, **arguments) -> dict:
    return answer(env.client, env.key, name, **arguments)


# 8.1: when was this written, over MCP and REST


def test_recall_page_and_evidence_say_when_over_mcp_and_rest(env):
    noted = call(env, "note", project="ops", text="# Deploy\n\nThe deploy runs at noon.\n")
    when = env.store.get_note(noted["note_id"]).created_at
    assert when == "2099-02-03T04:05:00.000000+00:00"
    slug = noted["page"]

    (hit,) = call(env, "recall", query="deploy noon")["hits"]
    (rest_hit,) = env.client.get("/api/search?q=deploy+noon", headers=env.h).json()["hits"]
    assert hit["written_at"] == rest_hit["written_at"] == when

    (claim,) = call(env, "page", project="ops", slug=slug)["claims"]
    rest_page = env.client.get(f"/api/projects/ops/pages/{slug}", headers=env.h).json()
    assert claim["written_at"] == rest_page["claims"][0]["written_at"] == when

    (passage,) = call(env, "evidence", project="ops", slug=slug, claim_id=claim["id"])["passages"]
    path = f"/api/projects/ops/pages/{slug}/claims/{claim['id']}/evidence"
    (rest_passage,) = env.client.get(path, headers=env.h).json()["passages"]
    assert passage["written_at"] == rest_passage["written_at"] == when


# 8.1: what to do when notes disagree

DISAGREE = (
    "A page is built from every note on its topic, and it does not drop an older note's"
    " claims when a newer note disagrees with them: each claim of `page`, each hit of"
    " `recall` and each passage of `evidence` says when its note was written (`written_at`)."
    " To retire a statement, say so in a new note, or ask a person to forget the old note on"
    " the web: an agent cannot forget a note."
)


def test_the_instructions_say_what_to_do_when_notes_disagree():
    for said in (INSTRUCTIONS, instructions("Fable Memory")):
        assert DISAGREE in said
        assert said.endswith("\n\n" + TREAT_AS_INFORMATION)  # still the last paragraph
    assert "cairn" not in DISAGREE.casefold()  # B3: names no product


def test_what_the_instructions_say_of_disagreeing_notes_is_true(env):
    """Both notes stay on the page, each claim with its own time; no MCP tool forgets."""
    first = call(env, "note", project="ops", text="Use the blue queue.", topic="Queue")
    second = call(
        env, "note", project="ops", text="Switch from the blue queue to the red.", topic="Queue"
    )
    assert first["page"] == second["page"]
    claims = call(env, "page", project="ops", slug=first["page"])["claims"]
    assert [c["text"] for c in claims] == [
        "Use the blue queue.",
        "Switch from the blue queue to the red.",
    ]
    assert all(c["written_at"] for c in claims)
    tools = {t["name"] for t in rpc(env.client, env.key, "tools/list")["result"]["tools"]}
    assert not {name for name in tools if "forget" in name or "delete" in name}


# 8.2, 8.6: errors name only what an agent can do


def test_a_note_filed_under_another_topic_says_what_an_agent_can_do(env):
    text = "Quill retries 3 times before failing."
    first = call(env, "note", project="ops", text=text, topic="Retry policy", source_ref="s-1")
    said = refusal(env.client, env.key, "note", project="ops", text=text, topic="Backoff",
                   source_ref="s-1")  # fmt: skip
    assert f"'{first['page']}'" in said and "filed once" in said
    assert "forget it there" not in said
    assert "write a new note with that page's title as its topic" in said
    assert "a person who may forget notes in this project can forget it on the web" in said
    assert text not in said


def test_the_page_title_as_topic_reaches_the_page_it_is_filed_on(env):
    """What the refusal says to do works: a new note with the page's title as its topic is
    filed on that page."""
    first = call(env, "note", project="ops", text="Quill retries 3 times.", topic="Retry policy")
    title = call(env, "page", project="ops", slug=first["page"])["title"]
    more = call(env, "note", project="ops", text="Quill waits 2 seconds.", topic=title)
    assert more["page"] == first["page"] and more["status"] == "saved"


def test_filed_once_names_its_page_and_never_a_tool_an_agent_lacks():
    said = str(FiledOnce("ops", "retry-policy"))
    assert said.startswith("Nothing was stored: this note is already in project 'ops'")
    assert "forget it there" not in said


def test_a_page_asked_for_by_its_title_names_the_nearest_pages(env):
    call(env, "note", project="ops", text="# Deploy\n\nThe deploy runs at noon.\n")
    call(env, "note", project="ops", text="# Rollback\n\nRoll back with the red lever.\n")
    said = refusal(env.client, env.key, "page", project="ops", slug="Deploy")
    assert said.endswith(
        "no page 'Deploy' in project 'ops'. A page is named by its slug, as note, recall and"
        " overview give it; the nearest: 'deploy' (Deploy), 'rollback' (Rollback)."
    )
    rest = env.client.get("/api/projects/ops/pages/Deploy", headers=env.h)
    assert rest.status_code == 404 and "the nearest: 'deploy' (Deploy)" in rest.text


def test_an_unknown_name_names_the_nearest_slugs_in_their_order(env):
    for name in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon"):
        call(env, "note", project="ops", text=f"# {name}\n\n{name} holds a quill.\n")
    said = refusal(env.client, env.key, "page", project="ops", slug="dalta")
    assert said.endswith("the nearest: 'delta' (Delta), 'epsilon' (Epsilon), 'gamma' (Gamma).")
    said = refusal(env.client, env.key, "evidence", project="ops", slug="zz", claim_id="cl_x")
    assert said.endswith("the nearest: 'alpha' (Alpha), 'beta' (Beta), 'delta' (Delta).")


def test_a_project_with_no_pages_says_there_are_none(env):
    said = refusal(env.client, env.key, "page", project="ops", slug="Deploy")
    assert said.endswith("no page 'Deploy' in project 'ops'. Project 'ops' has no pages yet.")


def test_the_nearest_pages_cost_a_bounded_read(env, monkeypatch):
    for i in range(30):
        call(env, "note", project="ops", text=f"# Leaf {i:02d}\n\nLeaf {i} faces north.\n")
    memory = Memory(env.store, None)
    seen: list[str] = []
    env.store._con.set_trace_callback(seen.append)
    try:
        with pytest.raises(Exception, match="the nearest: 'leaf-05'"):
            memory.page("ops", "Leaf 05")
    finally:
        env.store._con.set_trace_callback(None)
    assert len([s for s in seen if s.lstrip().upper().startswith("SELECT")]) <= 6


def test_a_note_that_made_no_page_says_so_and_names_no_page(env):
    """A note whose gate refused its first page: no page is made, and the answer names none."""
    noted = call(env, "note", project="ops", text="---\ntitle: x\n---\n# Only a heading\n")
    assert noted["version"] is None and noted["page"] is None
    assert noted["message"] == (
        "No page was made from this note: it is stored, but no page holds it, so there is no"
        " page to read."
    )
    rest = env.client.post(
        "/api/projects/ops/notes", headers=env.h, json={"text": "# Another heading only\n"}
    )
    assert rest.status_code == 201
    assert rest.json()["page"] is None and rest.json()["message"].startswith("No page was made")


def test_a_note_that_made_a_page_answers_as_before(env):
    noted = call(env, "note", project="ops", text="# Deploy\n\nThe deploy runs at noon.\n")
    assert noted["page"] == "deploy" and "message" not in noted


# 8.3: the graph, for clients that only call tools


def test_the_graph_tool_gives_what_the_resource_gives(env):
    call(env, "note", project="ops", text="# Deploy\n\nThe deploy runs at noon.\n")
    read = rpc(env.client, env.key, "resources/read", {"uri": "graph://project/ops/20"})
    from_resource = json.loads(read["result"]["contents"][0]["text"])
    assert call(env, "graph", project="ops", limit=20) == from_resource
    assert call(env, "graph", project="ops") == from_resource  # limit 20 by default


def test_the_graph_tool_has_the_resources_reach_and_its_refusals(env):
    other = env.store.create_project("secret-plans", everyone="none")
    narrowed = env.store.create_key("narrow", projects=[env.store.get_project("ops").id])
    assert other is not None
    unknown = refusal(env.client, env.key, "graph", project="no-such")
    out_of_reach = refusal(env.client, narrowed, "graph", project="secret-plans")
    assert unknown.endswith("Project not found.") and out_of_reach.endswith("Project not found.")
    for limit in (0, 21):
        assert refusal(env.client, env.key, "graph", project="ops", limit=limit).endswith(
            "Limit must be a whole number from 1 to 20."
        )


def test_the_graph_tool_reads_and_writes_nothing_else(env):
    tools = {t["name"]: t for t in rpc(env.client, env.key, "tools/list")["result"]["tools"]}
    graph = tools["graph"]
    assert graph["annotations"]["readOnlyHint"] is True
    assert set(graph["inputSchema"]["properties"]) == {"project", "limit"}
    assert len(graph["description"]) <= 300


@pytest.mark.parametrize(
    "uri", ["graph://project/ops", "graph://project/ops/0", "graph://project/ops/25"]
)
def test_a_graph_address_not_well_formed_is_answered_with_its_form(env, uri):
    error = rpc(env.client, env.key, "resources/read", {"uri": uri})["error"]
    assert error["message"] == (
        "A graph's address is graph://project/<project>/<limit>: a project's name or id, then"
        " a limit of 1 to 20 pages, as in graph://project/ops/20. The graph tool gives the same."
    )


def test_the_instructions_name_the_graph_tool():
    assert "call `graph`" in INSTRUCTIONS


# 8.4: a search with no words says so

NO_WORDS = (
    "Nothing was searched: the query holds no words, only spaces or punctuation. Search with"
    " words of letters or digits."
)


@pytest.mark.parametrize("query", ["", "   ", "--- ??? !!!"])
def test_a_query_of_no_words_says_nothing_was_searched(env, query):
    found = call(env, "recall", query=query)
    assert found["hits"] == [] and found["searched"]["said"] == NO_WORDS
    rest = env.client.get("/api/search", params={"q": query}, headers=env.h).json()
    assert rest["searched"]["said"] == NO_WORDS


def test_no_words_is_never_every_word():
    facts = Searched(
        words_searched=0, words_left_out=(), words_left_out_count=0,
        claims_matched=0, claims_ranked=0, ranked_by="all", projects_searched=1,
    )  # fmt: skip
    assert searched(facts)["said"] == NO_WORDS != NOTHING_LEFT_OUT
    one = facts.model_copy(update={"words_searched": 1})
    assert searched(one)["said"] == NOTHING_LEFT_OUT
