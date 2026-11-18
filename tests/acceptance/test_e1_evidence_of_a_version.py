"""E1: the evidence of a claim is the evidence of the version the claim was read on.

A page keeps its versions, and `page` shows any kept one with its claims. Tidying a page
folds claims that say the same thing: the folded claim cites every passage, and the claims
it replaced are gone from the latest version. Before this contract `evidence` looked a claim
up on the latest version only, so a claim read on an older version was answered with what
the latest version cites, or with "no claim" although the version shown holds it.

Contract
- `Memory.evidence(project, slug, claim_id, version=None)`; MCP tool `evidence` takes an
  optional `version`; REST `GET .../pages/<slug>/claims/<id>/evidence?version=N`.
- Without `version`: the latest version, as before.
- With `version`: the claim as that version holds it, with the passages that version cites
  and no other. A claim that version does not hold: not found. A version the page does not
  keep (or never had): the same refusal `page` gives for it.
- The answer says which version it is of: `"version": N`.
- `version` is read as `page` reads it: a whole number of 1 or more, and the same refusal
  for anything else.

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


@pytest.fixture
def desk(tmp_path):
    """A project whose page has three versions: one note; a second note that repeats the
    first one's sentence; the page tidied, which folds the two repeats into one claim."""
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("desk")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        desk = SimpleNamespace(client=client, key=key, store=store)
        call(desk, "new_project", name="alpha", description="first")
        call(desk, "note", project="alpha", text=ONE, source_ref="made-up:one")
        call(desk, "note", project="alpha", text=TWO, source_ref="made-up:two")
        tidy_page(store, store.get_project("alpha").id, SLUG)
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


def get(desk, path: str, **params):
    return desk.client.get(f"/api/projects/alpha/pages/{SLUG}{path}", params=params,
                           headers={"x-api-key": desk.key})  # fmt: skip


def refs(answer: dict) -> list[str]:
    return sorted(passage["source_ref"] for passage in answer["passages"])


def test_the_page_has_the_three_versions_this_contract_is_about(desk):
    second, third = (call(desk, "page", project="alpha", slug=SLUG, version=v) for v in (2, 3))
    assert third["versions"] == [1, 2, 3]
    assert [len(claim["passage_ids"]) for claim in second["claims"]] == [1, 1, 1]
    assert sorted(len(claim["passage_ids"]) for claim in third["claims"]) == [1, 2]
    gone = {c["id"] for c in second["claims"]} - {c["id"] for c in third["claims"]}
    assert len(gone) == 1  # the repeat the tidy folded away


def test_a_claim_read_on_an_older_version_gets_what_that_version_cites(desk):
    second = call(desk, "page", project="alpha", slug=SLUG, version=2)
    latest = call(desk, "page", project="alpha", slug=SLUG)
    folded = next(c for c in latest["claims"] if len(c["passage_ids"]) == 2)
    then = next(c for c in second["claims"] if c["id"] == folded["id"])
    assert len(then["passage_ids"]) == 1  # the same claim, before it was folded

    over_mcp = call(desk, "evidence", project="alpha", slug=SLUG, claim_id=folded["id"], version=2)
    over_rest = get(desk, f"/claims/{folded['id']}/evidence", version=2)
    assert over_rest.status_code == 200, over_rest.text
    for answer in (over_mcp, over_rest.json()):
        assert answer["version"] == 2
        assert refs(answer) == ["made-up:one"]
        assert [p["id"] for p in answer["passages"]] == then["passage_ids"]

    # without a version: the latest, as before, and it says so
    now = call(desk, "evidence", project="alpha", slug=SLUG, claim_id=folded["id"])
    assert now["version"] == 3 and refs(now) == ["made-up:one", "made-up:two"]
    assert get(desk, f"/claims/{folded['id']}/evidence").json() == now


def test_a_claim_the_latest_version_no_longer_holds_is_found_on_the_version_that_does(desk):
    second = call(desk, "page", project="alpha", slug=SLUG, version=2)
    latest = call(desk, "page", project="alpha", slug=SLUG)
    (gone,) = [c for c in second["claims"] if c["id"] not in {x["id"] for x in latest["claims"]}]

    there = call(desk, "evidence", project="alpha", slug=SLUG, claim_id=gone["id"], version=2)
    assert there["version"] == 2 and there["claim"]["id"] == gone["id"]
    assert refs(there) == ["made-up:two"]
    assert get(desk, f"/claims/{gone['id']}/evidence", version=2).json() == there

    # on a version that does not hold it, it is not found: the latest, and the first
    for version in ({}, {"version": 3}, {"version": 1}):
        missing = rpc(desk, "evidence", {"project": "alpha", "slug": SLUG, "claim_id": gone["id"],
                                         **version})  # fmt: skip
        assert missing["isError"] is True and "no claim" in missing["content"][0]["text"]
        assert get(desk, f"/claims/{gone['id']}/evidence", **version).status_code == 404


def test_a_version_the_page_does_not_have_is_refused_as_page_refuses_it(desk):
    latest = call(desk, "page", project="alpha", slug=SLUG)
    claim = latest["claims"][0]["id"]
    as_page = rpc(desk, "page", {"project": "alpha", "slug": SLUG, "version": 9})
    as_evidence = rpc(desk, "evidence", {"project": "alpha", "slug": SLUG, "claim_id": claim,
                                         "version": 9})  # fmt: skip
    assert as_page["isError"] is True and as_evidence["isError"] is True
    said = [answer["content"][0]["text"].split(": ", 1)[1] for answer in (as_page, as_evidence)]
    assert said[0] == said[1] and "no version 9 of page" in said[0]  # after the tool's name
    assert (
        get(desk, f"/claims/{claim}/evidence", version=9).status_code
        == get(desk, "", version=9).status_code
    )


@pytest.mark.parametrize("version", ["0", "-1", "two", "1.5", ""])
def test_a_version_is_read_from_the_query_exactly_as_page_reads_it(desk, version):
    """Whatever `page` answers a `version` it cannot use, `evidence` answers the same: the
    two read the query with one function."""
    claim = call(desk, "page", project="alpha", slug=SLUG)["claims"][0]["id"]
    answer = get(desk, f"/claims/{claim}/evidence", version=version)
    as_page = get(desk, "", version=version)
    assert answer.status_code == as_page.status_code
    if version:  # an empty value is no version at all, for both
        assert answer.status_code in (400, 404), answer.text
        assert answer.json() == as_page.json()


def test_an_argument_evidence_does_not_take_is_still_refused(desk):
    claim = call(desk, "page", project="alpha", slug=SLUG)["claims"][0]["id"]
    assert get(desk, f"/claims/{claim}/evidence", versions=2).status_code == 400
    wrong = rpc(desk, "evidence", {"project": "alpha", "slug": SLUG, "claim_id": claim, "v": 2})
    assert wrong["isError"] is True and "unknown argument 'v'" in wrong["content"][0]["text"]
