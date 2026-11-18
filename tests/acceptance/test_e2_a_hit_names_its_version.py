"""E2: a hit of a search names the version of the page its claim was read on.

`evidence` takes the version of the page a claim was read on (E1). A hit of `recall` (MCP)
and of `GET /api/search` (REST) gives the page and the claim, read on the page's latest
version at that moment, but not which version that was. An agent that asks for a hit's
evidence after the page was saved again was answered for the newer version, or told there
is no such claim.

Contract
- Every hit of `recall` and of `GET /api/search` has `"version"`: the number of the version
  of its page the hit's claim was read on, the page's latest version in the same read that
  produced the hit.
- A hit's `project`, `page`, `claim_id` and `version`, passed to `evidence`, answer with
  exactly the hit's `passage_ids`, also after the page was saved again or tidied since.
- That version, shown by `page`, holds the hit's claim with the hit's `passage_ids`.

Everything here is made up; nothing leaves the process.
"""

import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.arrange import tidy_page
from cairn.server import create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
ONE = "# Retry policy\n\nQuill retries a failed charge 3 times, 30 s apart.\n"
TWO = (
    "# Retry policy\n\nQuill retries a failed charge 3 times, 30 s apart.\n\n"
    "Lumen never retries: it reports the failure and stops.\n"
)
SLUG = "retry-policy"
QUERY = "quill charge"


@pytest.fixture
def desk(tmp_path):
    """A project whose page has two versions: one note, then a second note that repeats the
    first one's sentence. Not tidied yet: the tests tidy it after taking their hits."""
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("desk")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        desk = SimpleNamespace(client=client, key=key, store=store)
        call(desk, "new_project", name="alpha", description="first")
        call(desk, "note", project="alpha", text=ONE, source_ref="made-up:one")
        call(desk, "note", project="alpha", text=TWO, source_ref="made-up:two")
        yield desk
    store.close()


def rpc(desk, name: str, arguments: dict) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    body["params"] = {"name": name, "arguments": arguments}
    answer = desk.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": desk.key}, json=body)
    assert answer.status_code == 200, answer.text
    return answer.json()["result"]


def call(desk, name: str, /, **arguments) -> dict:
    result = rpc(desk, name, arguments)
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def hits(desk) -> list[tuple[str, list[dict]]]:
    """The hits for QUERY over both doors, each door's hits with its name."""
    over_rest = desk.client.get("/api/search", params={"q": QUERY},
                                headers={"x-api-key": desk.key})  # fmt: skip
    assert over_rest.status_code == 200, over_rest.text
    over_mcp = call(desk, "recall", query=QUERY)
    return [("recall", over_mcp["hits"]), ("search", over_rest.json()["hits"])]


def tidy(desk) -> None:
    tidy_page(desk.store, desk.store.get_project("alpha").id, SLUG)


def evidence(desk, hit: dict, **version) -> dict:
    return rpc(desk, "evidence", {"project": hit["project"], "slug": hit["page"],
                                  "claim_id": hit["claim_id"], **version})  # fmt: skip


def test_every_hit_names_the_latest_version_its_claim_was_read_on(desk):
    for door, found in hits(desk):
        assert len(found) == 2, door  # the sentence each note gave, before the tidy
        for hit in found:
            assert hit["version"] == 2, (door, hit)
            shown = call(desk, "page", project=hit["project"], slug=hit["page"],
                         version=hit["version"])  # fmt: skip
            (claim,) = [c for c in shown["claims"] if c["id"] == hit["claim_id"]]
            assert claim["passage_ids"] == hit["passage_ids"], door

    tidy(desk)  # a new version: a hit taken now names it
    for door, found in hits(desk):
        assert found and {hit["version"] for hit in found} == {3}, door


def test_a_hit_taken_before_a_tidy_gets_its_own_passages_at_its_version(desk):
    taken = hits(desk)
    tidy(desk)  # one of the two claims is folded into the other, which then cites both
    latest = call(desk, "page", project="alpha", slug=SLUG)
    held = {claim["id"] for claim in latest["claims"]}
    for door, found in taken:
        kinds = set()
        for hit in found:
            then = evidence(desk, hit, version=hit["version"])
            assert then["isError"] is False, (door, then)
            answer = json.loads(then["content"][0]["text"])
            assert answer["version"] == hit["version"] == 2, door
            assert [p["id"] for p in answer["passages"]] == hit["passage_ids"], door

            now = evidence(desk, hit)  # without the version: the latest, which differs
            if hit["claim_id"] in held:
                kinds.add("folded")
                cited = [p["id"] for p in json.loads(now["content"][0]["text"])["passages"]]
                assert cited != hit["passage_ids"], door
            else:
                kinds.add("gone")
                assert now["isError"] is True and "no claim" in now["content"][0]["text"]
        assert kinds == {"folded", "gone"}, door  # both cases are covered, over each door


def test_the_recall_tools_description_says_a_hit_names_its_version(desk):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    answer = desk.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": desk.key}, json=body)
    tools = {tool["name"]: tool["description"] for tool in answer.json()["result"]["tools"]}
    said = " ".join(tools["recall"].split())
    assert "the version of its page" in said and "`evidence`" in said
