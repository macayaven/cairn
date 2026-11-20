"""How large an answer of `related` can be (KG1), and that it is a bound.

The bound is the size of `largest(limit, character)`: the answer the constants allow at most,
built here field by field. `limit` pages (relations.RELATED_MAX at most), each of
relations.WHY_MAX reasons, each a term of relations.TERM_MAX characters and a claim on each
side: relations.TEXT_MAX characters of text, relations.LINE_MAX of the line that holds the
term, relations.PASSAGES_MAX passages named, memory.WRITTEN_BY_MAX writers of
audit.ACTOR_NAME_MAX characters each, every field that is there only sometimes, and every
text made of the character that costs most to send. That is a control character: \\u0001 in
JSON, 6 bytes over REST, and 7 over MCP, which sends the answer as JSON inside a JSON string.
Who wrote a claim weighs as much in the largest answer as the claim's text and line do: three
writers' names of 200 characters, where a name is mostly a few letters.
STATED holds the numbers, and one test says they are what the constants give.

What makes it a bound and not a hope: `within` checks an answer field by field against the
largest (no text longer, no list longer, no field the largest has not), and it is asked of
answers made through the product from claims far larger than any bound: a table of 322,320
characters as one claim on both sides of every reason at limit 20, and pages of control
characters. The sizes compared are the bytes each surface sends, pinned to the responses of
the server itself.

The bound covers every page the product writes or imports: a title and a topic are 200
characters at most (store.FIELD_MAX), a slug 64 (compiler.SLUG_MAX), an id is as the product
makes it. A page saved by hand through Store.save_page, which no surface offers, can hold a
title or an id of any length.

Made-up names; no line of a note is shown by a failure."""

import json
from contextlib import closing
from types import SimpleNamespace

import pydantic_core
import pytest
from starlette.testclient import TestClient

import cairn.memory
from cairn import relations
from cairn.compiler import SLUG_MAX
from cairn.memory import SLACK_FILED, WRITTEN_BY_MAX
from cairn.server import create_app
from cairn.store import FIELD_MAX, Store, relating
from cairn.store.audit import ACTOR_NAME_MAX

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
COSTLIEST = "\x01"  # a control character: the most bytes a character of text takes, sent
TABLE = 322_320  # characters of the largest claim measured: this repository's table of decisions

# The numbers stated: the most bytes an answer takes, by surface and limit, for any text and
# for text of one byte a character (plain English). Each is a size of `largest`.
STATED = {
    ("rest", 8, "any text"): 587_744,
    ("rest", 20, "any text"): 1_466_924,
    ("mcp", 8, "any text"): 722_905,
    ("mcp", 20, "any text"): 1_804_225,
    ("rest", 8, "plain text"): 141_944,
    ("rest", 20, "plain text"): 353_924,
    ("mcp", 8, "plain text"): 187_945,
    ("mcp", 20, "plain text"): 468_625,
}


def as_rest(answer: dict) -> int:
    """The bytes GET /api/projects/{project}/pages/{slug}/related sends for this answer."""
    return len(json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode())


def as_mcp(answer: dict) -> int:
    """The bytes the `related` tool's response takes: the answer as indented JSON, in a text
    content of a JSON-RPC result."""
    text = pydantic_core.to_json(answer, indent=2).decode()
    result = {"content": [{"type": "text", "text": text}], "isError": False}
    sent = {"jsonrpc": "2.0", "id": 1, "result": result}
    return len(json.dumps(sent, ensure_ascii=False, separators=(",", ":")).encode())


SENT = {"rest": as_rest, "mcp": as_mcp}


def largest(limit: int, character: str) -> dict:
    """The answer the constants allow at most, every text someone wrote made of `character`
    (a title, a term, a claim's text and its line, a writer's name and a note's `via`). What
    the product makes is of plain letters and as long as it makes it: an id, a slug, a
    project's name, a kind, a time."""

    def text(length: int) -> str:
        return character * length

    def made(length: int) -> str:
        return "a" * length

    most = 2**63 - 1  # a count or a version: no whole number the store holds is longer
    passage_id = claim_id = len("cl_") + 16
    note_id = len("cap_") + 12
    title = max(FIELD_MAX["title"], FIELD_MAX["topic"])
    writer = {
        "note": made(note_id),
        "kind": made(len("operator")),
        "name": text(ACTOR_NAME_MAX),
        "via": text(FIELD_MAX["via"]),
        "outside": False,
        "filed_message": SLACK_FILED,
    }
    claim = {
        "claim_id": made(claim_id),
        "text": text(relations.TEXT_MAX),
        "text_cut": True,
        "line": text(relations.LINE_MAX),
        "line_cut": True,
        "passage_ids": [made(passage_id)] * relations.PASSAGES_MAX,
        "passage_ids_more": most,
        "outside": False,
        "written_by": [writer] * WRITTEN_BY_MAX,
        "written_by_more": most,
        "written_at": made(len("2099-02-03T04:05:00.000000+00:00")),
    }
    reason = {
        "kind": made(len("mentioned_by")),
        "term": text(max(relations.TERM_MAX, title)),  # a term, or the page's title
        "term_kind": made(len("inline_code")),
        "pages": most,
        "here": claim,
        "there": claim,
    }
    page = {
        "page": made(SLUG_MAX),
        "title": text(title),
        "version": most,
        "why": [reason] * relations.WHY_MAX,
        "more": most,
    }
    return {
        "project": made(64),
        "page": made(SLUG_MAX),
        "title": text(title),
        "version": most,
        "related": [page] * limit,
        "searched": {
            "complete": False,
            "terms_looked_up": most,
            "terms_not_looked_up": most,
            "claims_read": most,
            "claims_not_read": most,
            "pages_left_out": most,
        },
    }


def within(answer, most, where: str = "answer") -> list[str]:
    """Where `answer` holds more than `most` allows: a field it has not, a longer text, a
    longer list, a larger number. Nothing of a text is shown, only where it stands."""
    if isinstance(most, dict):
        if not isinstance(answer, dict):
            return [f"{where}: not an object"]
        faults = [f"{where}.{key}: not a field of the largest" for key in answer if key not in most]
        for key in answer.keys() & most.keys():
            faults += within(answer[key], most[key], f"{where}.{key}")
        return faults
    if isinstance(most, list):
        if not isinstance(answer, list) or len(answer) > len(most):
            return [f"{where}: a longer list"]
        return [f for n, item in enumerate(answer) for f in within(item, most[0], f"{where}[{n}]")]
    if isinstance(most, bool) or answer is None:
        return [] if isinstance(answer, bool) or answer is None else [f"{where}: not true or false"]
    if isinstance(most, int):
        return [] if type(answer) is int and 0 <= answer <= most else [f"{where}: a larger number"]
    return (
        [] if isinstance(answer, str) and len(answer) <= len(most) else [f"{where}: a longer text"]
    )


def rpc_related(client, key: str, slug: str, limit: int):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "related",
            "arguments": {"project": "ops", "slug": slug, "limit": limit},
        },
    }
    sent = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
    assert sent.status_code == 200
    return sent


def both(served, slug: str, limit: int) -> dict:
    """The answer for the page, read over REST and over MCP: the same answer, and each
    surface's bytes are what `as_rest` and `as_mcp` say of it."""
    over_rest = served.client.get(
        f"/api/projects/ops/pages/{slug}/related?limit={limit}", headers={"x-api-key": served.key}
    )
    assert over_rest.status_code == 200
    answer = over_rest.json()
    over_mcp = rpc_related(served.client, served.key, slug, limit)
    result = over_mcp.json()["result"]
    assert result["isError"] is False and json.loads(result["content"][0]["text"]) == answer
    assert (len(over_rest.content), len(over_mcp.content)) == (as_rest(answer), as_mcp(answer))
    return answer


def note(served, title: str, text: str) -> str:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "note",
            "arguments": {"project": "ops", "text": text, "title": title, "topic": title},
        },
    }
    sent = served.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": served.key}, json=body)
    result = sent.json()["result"]
    assert result["isError"] is False
    return json.loads(result["content"][0]["text"])["page"]


@pytest.fixture
def served(tmp_path):
    with closing(Store(tmp_path / "cairn.db")) as store:
        store.create_project("ops")
        key = store.create_key("laptop")
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            yield SimpleNamespace(client=client, store=store, key=key)


def sides(answer: dict):
    for page in answer["related"]:
        for reason in page["why"]:
            for side in ("here", "there"):
                if side in reason:
                    yield reason, reason[side]


# The numbers, and that they are what the constants give


def test_the_stated_numbers_are_the_size_of_the_largest_answer_the_constants_allow():
    """Each number of STATED, worked out again: change a constant and this says which
    numbers to state anew."""
    worked = {
        (surface, limit, text): SENT[surface](largest(limit, character))
        for surface in ("rest", "mcp")
        for limit in (8, relations.RELATED_MAX)
        for text, character in (("any text", COSTLIEST), ("plain text", "a"))
    }
    assert worked == STATED
    said = " ".join(cairn.memory.__doc__.split())  # where the contract states them
    assert (
        "The largest answer they allow at `limit` 20 is 353,924 bytes over REST and 468,625"
        " over MCP for text of one byte a character, and 1,466,924 and 1,804,225 whatever the"
        " text holds; at the default `limit` of 8, 141,944 and 187,945, and 587,744 and"
        " 722,905 ("
    ) in said
    for number in STATED.values():
        assert f"{number:,}" in said
    assert (relations.RELATED_MAX, relations.WHY_MAX) == (20, 3)
    assert (relations.TEXT_MAX, relations.LINE_MAX, relations.TERM_MAX) == (600, 400, 200)
    assert (relations.PASSAGES_MAX, WRITTEN_BY_MAX, ACTOR_NAME_MAX) == (8, 3, 200)


def test_what_the_largest_answer_is_made_of():
    """How the bound follows, as sums. `limit` pages of WHY_MAX reasons of two claims are 120
    claims at limit 20. Each holds at most TEXT_MAX + LINE_MAX characters of its text and
    its line, 1,000; and WRITTEN_BY_MAX writers, each a name of 200 characters and a `via`
    of 40, 720 in all. At 6 bytes a character over REST those are 720,000 and 518,400 bytes
    of the 1,466,924 stated, and the 60 terms and 21 titles of 200 characters are 97,200
    more; what is left is the ids, the fixed words and JSON's own marks."""
    claims = relations.RELATED_MAX * relations.WHY_MAX * 2
    text = claims * (relations.TEXT_MAX + relations.LINE_MAX)
    writers = claims * WRITTEN_BY_MAX * (ACTOR_NAME_MAX + FIELD_MAX["via"])
    named = (relations.RELATED_MAX * relations.WHY_MAX + relations.RELATED_MAX + 1) * 200
    assert (claims, text, writers, named) == (120, 120_000, 86_400, 16_200)
    stated = STATED["rest", 20, "any text"]
    most = 6 * (text + writers + named)
    assert most == 1_335_600 and most < stated < most + most // 10
    assert STATED["rest", 20, "plain text"] < 2 * (text + writers + named)


@pytest.mark.parametrize(
    "character", ["a", '"', "\\", "\n", "\t", "\x00", "\x1f", "\x7f", "é", "€", "\U0001f600"]
)
def test_no_character_costs_more_to_send_than_a_control_character(character):
    for sent in (as_rest, as_mcp):
        assert sent({"text": character * 100}) <= sent({"text": COSTLIEST * 100})
    assert as_rest({"text": COSTLIEST * 100}) - as_rest({"text": ""}) == 600
    assert as_mcp({"text": COSTLIEST * 100}) - as_mcp({"text": ""}) == 700


# Claims far larger than the bound


def table(page: int, terms: dict[int, str]) -> str:
    """A table of exactly TABLE characters, which is filed as one passage and one claim: rows
    of more than relations.LINE_MAX characters, a term in the rows `terms` names."""
    filler = "Brook keeps the ledger and Ada reads it at dawn; "
    lines = ["| # | Call | Why |", "|---|---|---|"]
    row = 0
    while True:
        held = f" with `{terms[row]}`" if row in terms else ""
        line = f"| {row} | page {page}{held} | " + filler * 9 + f"row {row} of page {page} |"
        if sum(len(each) + 1 for each in lines) + len(line) > TABLE:
            break
        lines.append(line)
        row += 1
    assert row > max(terms)
    short = TABLE - sum(len(each) + 1 for each in lines)
    lines.append("| last | " + "x" * (short - len("| last |  |")) + " |")
    whole = "\n".join(lines)
    assert len(whole) == TABLE and min(len(each) for each in lines[2:-1]) > relations.LINE_MAX
    return whole


def term(page: int, n: int) -> str:
    return f"quill-{page}-{n}.lock"


@pytest.fixture(scope="module")
def tables(tmp_path_factory):
    """Twenty-one pages, each one claim of TABLE characters. The first states sixty terms,
    three with each of the others, far down the table."""
    with closing(Store(tmp_path_factory.mktemp("tables") / "cairn.db")) as store:
        store.create_project("ops")
        key = store.create_key("laptop")
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            served = SimpleNamespace(client=client, store=store, key=key)
            asked = {7 * (3 * page + n) + 5: term(page, n) for page in range(20) for n in range(3)}
            note(served, "Asked", table(99, asked) + "\n")
            for page in range(20):
                theirs = {150 * (n + 1) + page: term(page, n) for n in range(3)}
                note(served, f"Page {page:02d}", table(page, theirs) + "\n")
            (claim,) = store.get_page(store.get_project("ops").id, "asked").claims
            assert len(claim.text) == TABLE
            yield served


def test_claims_of_322_320_characters_on_both_sides_at_limit_20_are_under_the_bound(
    tables, monkeypatch
):
    """With the bytes of passages read raised out of the way, so that all twenty pages are
    related: sixty reasons, a claim of 322,320 characters on each side of each, every one
    given as its first 600 characters and 400 of the row that holds the term."""
    monkeypatch.setattr(relating, "BYTES_MAX", 21 * 4 * TABLE)
    answer = both(tables, "asked", 20)
    assert len(answer["related"]) == 20 and answer["searched"]["complete"] is True
    assert all(len(page["why"]) == 3 and page["more"] == 0 for page in answer["related"])
    given = list(sides(answer))
    assert len(given) == 120
    for reason, claim in given:
        assert (len(claim["text"]), claim["text_cut"]) == (relations.TEXT_MAX, True)
        assert (len(claim["line"]), claim["line_cut"]) == (relations.LINE_MAX, True)
        assert f"`{reason['term']}`" in claim["line"] and "\n" not in claim["line"]
    assert within(answer, largest(20, "a")) == []
    for surface, sent in SENT.items():
        assert sent(answer) <= STATED[surface, 20, "plain text"] < STATED[surface, 20, "any text"]
    assert as_rest(answer) < 120 * TABLE // 100  # and no hundredth of the claims it rests on


def test_as_shipped_the_bytes_read_stop_at_three_such_claims_and_the_answer_says_so(tables):
    """A million bytes of passages read hold three claims of 322,320 characters: three pages
    are related, the seventeen claims not read are counted, and their terms give no reason."""
    assert 3 * TABLE <= relating.BYTES_MAX < 4 * TABLE
    answer = both(tables, "asked", 20)
    assert [len(page["why"]) for page in answer["related"]] == [3, 3, 3]
    assert answer["searched"] == {
        "complete": False,
        "terms_looked_up": 60,
        "terms_not_looked_up": 0,
        "claims_read": 3,
        "claims_not_read": 17,
        "pages_left_out": 0,
    }
    assert within(answer, largest(20, "a")) == []
    # asked of one of the others, whose one candidate is the first page's claim: complete
    back = both(tables, "page-00", 8)
    assert back["searched"]["complete"] is True and len(back["related"]) == 1
    assert within(back, largest(8, "a")) == []


# The largest answer the shipped bounds reach


def long_claim(lead: str, filler: str) -> str:
    """One line a little longer than relations.TEXT_MAX, the term past the cut."""
    return filler * (relations.TEXT_MAX // len(filler)) + f" {lead} " + filler * 20


@pytest.mark.parametrize(
    ("kind", "filler"),
    [
        ("plain text", "Brook waits. "),
        ("any text", COSTLIEST),
        ("any text", '"\\'),
        ("any text", "\u20ac"),  # three bytes as stored, and as each surface sends it
        ("any text", "\U0001f600"),  # four
    ],
)
@pytest.mark.parametrize("limit", [8, 20])
def test_the_largest_answer_made_through_the_product_is_within_the_largest_allowed(
    served, kind, filler, limit
):
    """Twenty pages, each related to the page asked about by three terms, every claim on
    either side a line longer than TEXT_MAX, within the bytes read as shipped: `limit` pages
    of three reasons, every side cut, with a line cut. Its every field is within the
    largest answer's, and its bytes are under the number stated; with text of control
    characters, the costliest there are, it comes to more than half of that number. `both`
    pins the sizes compared to the bytes the server sends, for each of these texts."""
    asked = "\n\n".join(
        long_claim(f"Ada pins `{term(page, n)}` at noon.", filler)
        for page in range(20)
        for n in range(3)
    )
    slug = note(served, "Asked " + "a" * 194, asked + "\n")
    for page in range(20):
        theirs = "\n\n".join(
            long_claim(f"Quill reads `{term(page, n)}` at dusk.", filler) for n in range(3)
        )
        note(served, f"Page {page:02d} " + "b" * 192, theirs + "\n")
    answer = both(served, slug, limit)
    assert len(answer["related"]) == limit and len(answer["title"]) == FIELD_MAX["title"]
    assert answer["searched"]["claims_not_read"] == 0
    assert answer["searched"]["pages_left_out"] == 20 - limit
    given = list(sides(answer))
    assert len(given) == limit * relations.WHY_MAX * 2
    assert len({claim["claim_id"] for _, claim in given}) == len(given)  # no claim twice
    for _, claim in given:
        assert (len(claim["text"]), claim["text_cut"]) == (relations.TEXT_MAX, True)
        assert (len(claim["line"]), claim["line_cut"]) == (relations.LINE_MAX, True)
    character = "a" if kind == "plain text" else COSTLIEST
    assert within(answer, largest(limit, character)) == []
    for surface, sent in SENT.items():
        assert sent(answer) <= STATED[surface, limit, kind]
    if filler == COSTLIEST:
        assert as_rest(answer) > STATED["rest", limit, kind] // 2
