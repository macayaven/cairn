"""Related pages as people and agents ask for them: Memory.related, GET
/api/projects/<project>/pages/<slug>/related and the MCP tool `related`. The shape of the
answer, what each claim in it says of who wrote it, the refusals, the read event, what a
forget does to it, and what the tool and the instructions say. The app runs in process over
a real store in a temporary file; made-up names."""

import json
import logging
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn import relations
from cairn.gate import lint_gate
from cairn.memory import RELATED_RANGE, BadRequest, Memory, NotFound
from cairn.server import create_app
from cairn.store import Claim, Store, relating
from cairn.web.mcp import INSTRUCTIONS, TREAT_AS_INFORMATION, build_mcp, instructions

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
WRITTEN = datetime(2099, 2, 3, 4, 5, tzinfo=UTC)
WHEN = "2099-02-03T04:05:00.000000+00:00"


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
    ops = store.create_project("ops")
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        yield SimpleNamespace(client=client, store=store, key=key, h={"x-api-key": key}, ops=ops)
    store.close()


def note(env, title: str, text: str, project: str = "ops") -> dict:
    return answer(env.client, env.key, "note", project=project, text=text, title=title, topic=title)


def rest(env, slug: str, query: str = "", project: str = "ops"):
    return env.client.get(f"/api/projects/{project}/pages/{slug}/related{query}", headers=env.h)


@pytest.fixture
def world(env):
    """Deploy states a flag that Retention states too, and Retention's claim names Deploy."""
    deploy = note(env, "Deploy", "Run the job with `--audit-keep` set to 7.\n")
    retention = note(env, "Retention", "`--audit-keep` says how many; see Deploy.\n")
    note(env, "Queue", "Brook drains the queue at noon.\n")
    return SimpleNamespace(deploy=deploy, retention=retention)


# The shape, over MCP and REST alike


def test_the_answer_names_each_related_page_and_why_with_the_claims(env, world):
    found = answer(env.client, env.key, "related", project="ops", slug="deploy")
    assert rest(env, "deploy").json() == found
    assert Memory(env.store, lint_gate).related("ops", "deploy") == found

    def claim(text: str, noted: dict) -> dict:
        page = env.store.get_page(env.ops.id, noted["page"])
        (held,) = [c for c in page.claims if c.text == text]
        return {
            "claim_id": held.id,
            "text": text,
            "passage_ids": list(held.span_ids),
            "outside": False,
            "written_by": [
                {
                    "note": noted["note_id"],
                    "kind": "key",
                    "name": "laptop",
                    "via": "mcp",
                    "outside": False,
                }
            ],
            "written_at": WHEN,
        }

    theirs = claim("`--audit-keep` says how many; see Deploy.", world.retention)
    assert found == {
        "project": "ops",
        "page": "deploy",
        "title": "Deploy",
        "version": 1,
        "related": [
            {
                "page": "retention",
                "title": "Retention",
                "version": 1,
                "why": [
                    {
                        "kind": "mentioned_by",
                        "term": "Deploy",
                        "term_kind": "title",
                        "there": theirs,
                    },
                    {
                        "kind": "shares",
                        "term": "--audit-keep",
                        "term_kind": "inline_code",
                        "pages": 2,
                        "here": claim("Run the job with `--audit-keep` set to 7.", world.deploy),
                        "there": theirs,
                    },
                ],
                "more": 0,
            }
        ],
        "searched": {
            "complete": True,
            "terms_looked_up": 1,
            "terms_not_looked_up": 0,
            "claims_read": 1,
            "claims_not_read": 0,
            "pages_left_out": 0,
        },
    }


def test_a_page_with_no_specific_term_and_no_mention_and_a_project_of_one_page(env):
    note(env, "Queue", "Brook drains the queue at noon with `true` set.\n")
    nothing = {
        "complete": True,
        "terms_looked_up": 0,
        "terms_not_looked_up": 0,
        "claims_read": 0,
        "claims_not_read": 0,
        "pages_left_out": 0,
    }
    alone = answer(env.client, env.key, "related", project="ops", slug="queue")
    assert (alone["related"], alone["searched"]) == ([], nothing)
    env.store.create_project("solo")
    note(env, "Only", "Use `only.cfg` with --dry-run.\n", project="solo")
    only = answer(env.client, env.key, "related", project="solo", slug="only")
    assert only["related"] == [] and only["searched"] == {**nothing, "terms_looked_up": 2}


def test_a_page_gives_three_reasons_at_most_and_counts_the_rest(env):
    terms = " ".join(f"`t{n}.cfg`" for n in range(5))
    note(env, "Asked", f"Use {terms}.\n")
    note(env, "Twin", f"Brook uses {terms} too.\n")
    (page,) = answer(env.client, env.key, "related", project="ops", slug="asked")["related"]
    assert [reason["term"] for reason in page["why"]] == ["t0.cfg", "t1.cfg", "t2.cfg"]
    assert page["more"] == 2 and len(page["why"]) == relations.WHY_MAX


def test_limit_takes_the_best_and_the_answer_counts_the_pages_left_out(env):
    note(env, "Asked", "Use `a.cfg`, `b.cfg` and `c.cfg`.\n")
    note(env, "Two", "Use `a.cfg` and `b.cfg`.\n")
    note(env, "One", "Use `c.cfg`.\n")
    found = answer(env.client, env.key, "related", project="ops", slug="asked", limit=1)
    assert [page["page"] for page in found["related"]] == ["two"]
    assert found["searched"]["pages_left_out"] == 1 and found["searched"]["complete"] is False
    assert rest(env, "asked", "?limit=1").json() == found
    both = answer(env.client, env.key, "related", project="ops", slug="asked")
    assert [page["page"] for page in both["related"]] == ["two", "one"]
    assert both["searched"]["complete"] is True and rest(env, "asked").json() == both


def test_five_pages_are_given_when_no_limit_is_asked_for(env):
    """The default, relations.RELATED_DEFAULT, over MCP, over REST and of Memory.related:
    of seven related pages the best five, and the two left out counted."""
    terms = [f"`t{n}.cfg`" for n in range(7)]
    note(env, "Asked", "Use " + ", ".join(terms) + ".\n")
    for n, term in enumerate(terms):
        note(env, f"Page {n}", f"Brook sets {term}.\n")
    found = answer(env.client, env.key, "related", project="ops", slug="asked")
    assert [page["page"] for page in found["related"]] == [f"page-{n}" for n in range(5)]
    assert found["searched"]["pages_left_out"] == 2 and found["searched"]["complete"] is False
    assert rest(env, "asked").json() == found == rest(env, "asked", "?limit=5").json()
    assert Memory(env.store, lint_gate).related("ops", "asked") == found
    assert len(env.store.related_pages(env.ops.id, "asked").pages) == 5  # and the store's own
    six = answer(env.client, env.key, "related", project="ops", slug="asked", limit=6)
    assert len(six["related"]) == 6 and six["searched"]["pages_left_out"] == 1
    import cairn.server

    said = " ".join(cairn.server.__doc__.split())  # the contract says the same number twice
    assert "related(project, slug, limit=5) -> " in said
    assert (
        "GET /api/projects/{project}/pages/{slug}/related[?limit=N] (KG1): what `related`"
        " answers; N is 1 to 20 (5 when left out)"
    ) in said


def test_a_term_whose_claims_were_not_all_read_gives_no_reason_and_the_answer_counts_them(
    env, monkeypatch
):
    """Every `pages` an answer gives is exact. A term with a claim the read did not reach
    relates nothing in that answer; `claims_not_read` counts those claims, and the answer is
    not complete. No reason carries a mark of its own."""
    monkeypatch.setattr(relating, "CLAIMS_MAX", 2)
    note(env, "Asked", "Use `a.cfg` and `b.cfg`.\n")
    note(env, "Few", "Set `a.cfg` once.\n")
    note(env, "Many", "\n".join(f"- Line {n} sets `b.cfg`." for n in range(3)) + "\n")
    found = answer(env.client, env.key, "related", project="ops", slug="asked")
    (few,) = found["related"]
    (reason,) = few["why"]
    assert (few["page"], reason["term"], reason["pages"]) == ("few", "a.cfg", 2)
    assert list(reason) == ["kind", "term", "term_kind", "pages", "here", "there"]
    assert found["searched"] == {
        "complete": False,
        "terms_looked_up": 2,
        "terms_not_looked_up": 0,
        "claims_read": 1,
        "claims_not_read": 3,
        "pages_left_out": 0,
    }
    assert rest(env, "asked").json() == found


def test_the_store_gives_no_more_pages_than_an_answer_may_hold(env):
    """Whoever calls: a limit past the most an answer gives is that most, and the rest counted."""
    terms = [f"`t{n}.cfg`" for n in range(relations.RELATED_MAX + 2)]
    note(env, "Asked", "Use " + " ".join(terms) + ".\n")
    for n, term in enumerate(terms):
        note(env, f"Page {n}", f"Brook sets {term}.\n")
    found = env.store.related_pages(env.ops.id, "asked", 1_000)
    assert len(found.pages) == relations.RELATED_MAX and found.left.pages_left_out == 2


# Everything an agent reads says who wrote it


def test_a_claim_of_outside_text_says_so_with_each_note_it_cites(env):
    mail = env.store.writing_as("source", "team-mail")
    brought, _ = mail.add_note(env.ops.id, "# Mail\n\nSet `x.cfg` first.\n", via="mail")
    ours = [env.store.add_note(env.ops.id, f"# N{n}\n\nSet `x.cfg` first.\n")[0] for n in range(3)]
    spans = [
        next(s.id for s in env.store.note_spans(held.id) if s.kind == "paragraph")
        for held in (brought, *ours)
    ]
    folded = Claim(id="cl_folded", text="Set `x.cfg` first.", span_ids=tuple(spans))
    env.store.save_page(env.ops.id, "setup", "Setup", "", [folded])
    note(env, "Asked", "Then read `x.cfg` again.\n")
    (page,) = answer(env.client, env.key, "related", project="ops", slug="asked")["related"]
    (reason,) = page["why"]
    there = reason["there"]
    assert there["outside"] is True and reason["here"]["outside"] is False
    assert there["written_by"][0] == {
        "note": brought.id,
        "kind": "source",
        "name": "team-mail",
        "via": "mail",
        "outside": True,
    }
    assert [entry["note"] for entry in there["written_by"]] == [brought.id, ours[0].id, ours[1].id]
    assert there["written_by_more"] == 1 and "written_by_more" not in reason["here"]
    assert there["written_at"] == WHEN


def test_a_long_claim_is_cut_at_the_bound_and_says_so_with_the_line_that_holds_the_term(env):
    """Past relations.TEXT_MAX characters a claim gives `text_cut` and, in place of its text,
    the line of its passage that holds the term (a window of a line longer than LINE_MAX, and
    `line_cut`): no head of the claim beside it. `page` gives the claim whole. A claim within
    the bound gives its text and none of the three fields, and everything else a claim says
    is what a hit of `recall` says."""
    long = "Brook pins `quill.lock` and then " + "waits for the nightly log, " * 300 + "stops."
    note(env, "Asked", "Ada reads `quill.lock` daily.\n")
    note(env, "Long", long + "\n")
    found = answer(env.client, env.key, "related", project="ops", slug="asked")
    assert rest(env, "asked").json() == found
    (page,) = found["related"]
    (reason,) = page["why"]
    there = reason["there"]
    (hit,) = answer(env.client, env.key, "recall", query="pins", project="ops")["hits"]
    assert hit["text"] == long and len(long) > 8_000
    assert "text" not in there and there["text_cut"] is True
    assert there["line"] == long[: relations.LINE_MAX] and there["line_cut"] is True
    for field in ("claim_id", "passage_ids", "outside", "written_by", "written_at"):
        assert there[field] == hit[field], field
    assert list(there) == [
        "claim_id",
        "text_cut",
        "line",
        "line_cut",
        "passage_ids",
        "outside",
        "written_by",
        "written_at",
    ]
    assert list(reason["here"]) == [
        "claim_id",
        "text",
        "passage_ids",
        "outside",
        "written_by",
        "written_at",
    ]
    (whole,) = answer(env.client, env.key, "page", project="ops", slug="long")["claims"]
    assert whole["text"] == long and whole["id"] == there["claim_id"]


def test_a_claim_cut_whose_line_is_short_gives_the_line_whole_and_no_mark_on_it(env):
    lines = [f"Brook checks ledger {n} at dawn." for n in range(90)] + ["Ada pins `quill.lock`."]
    note(env, "Asked", "Ada reads `quill.lock` daily.\n")
    note(env, "Long", "\n".join(lines) + "\n")
    there = answer(env.client, env.key, "related", project="ops", slug="asked")["related"][0][
        "why"
    ][0]["there"]
    assert (there["text_cut"], there["line"]) == (True, "Ada pins `quill.lock`.")
    assert "line_cut" not in there and "text" not in there


def test_a_long_claim_that_mentions_the_page_says_it_is_cut_and_gives_the_line_that_names_it(env):
    note(env, "Quill ledger", "Brook balances it at dusk.\n")
    lines = [f"Ada waits for nightly log {n}." for n in range(60)]
    note(env, "Long", "\n".join([*lines, "Then she opens the Quill ledger."]) + "\n")
    found = answer(env.client, env.key, "related", project="ops", slug="quill-ledger")
    assert rest(env, "quill-ledger").json() == found
    (reason,) = found["related"][0]["why"]
    there = reason["there"]
    assert reason["kind"] == "mentioned_by" and "here" not in reason
    assert "text" not in there and there["text_cut"] is True
    assert there["line"] == "Then she opens the Quill ledger." and "line_cut" not in there
    assert list(there)[:3] == ["claim_id", "text_cut", "line"]
    # a line longer than LINE_MAX: a window of it around the title, marked
    note(env, "Wide", "Ada waits for the nightly log, " * 40 + "then opens the Quill ledger.\n")
    wide = [
        page["why"][0]["there"]
        for page in answer(env.client, env.key, "related", project="ops", slug="quill-ledger")[
            "related"
        ]
        if page["page"] == "wide"
    ]
    assert wide[0]["line_cut"] is True and len(wide[0]["line"]) == relations.LINE_MAX
    assert wide[0]["line"].endswith("then opens the Quill ledger.") and "text" not in wide[0]


@pytest.mark.parametrize("held", [relating.MARKS[0], relating.MARKS[1], "\x00"])
def test_a_claim_cut_that_no_line_can_be_given_of_gives_its_head_as_text(env, held):
    """The one case: a claim that mentions the page and holds a mark of the index or a null
    character, where the index cannot say which line names the title. It gives its first
    TEXT_MAX characters as `text`, with `text_cut`, and no line; over MCP and REST alike."""
    whole = "Ada waits for the nightly log. " * 30 + f"She opens {held} the Quill ledger."
    Memory(env.store, lint_gate).note("ops", "Brook balances it.\n", title="Quill ledger")
    Memory(env.store, lint_gate).note("ops", whole + "\n", title="Long")
    found = answer(env.client, env.key, "related", project="ops", slug="quill-ledger")
    assert rest(env, "quill-ledger").json() == found
    (reason,) = found["related"][0]["why"]
    there = reason["there"]
    assert reason["kind"] == "mentioned_by"
    assert (there["text"], there["text_cut"]) == (whole[: relations.TEXT_MAX], True)
    assert "line" not in there and "line_cut" not in there
    assert list(there)[:4] == ["claim_id", "text", "text_cut", "passage_ids"]


def test_a_claim_folded_from_many_passages_names_the_first_eight_and_counts_the_rest(env):
    """The product's own way to a claim of many passages: the same line filed in eleven notes
    of one page, which a tidy folds into one claim that cites all eleven. The claim is read
    by its first passage, names relations.PASSAGES_MAX of them and counts the others; `page`
    names every one. Who wrote it is still said of all eleven notes, as a hit of `recall`
    says it."""
    from cairn.arrange import tidy_page

    for n in range(11):
        note(env, "Fold", f"Ada pins `quill.lock` daily.\n\nBrook checks ledger {n} at dawn.\n")
    note(env, "Asked", "Quill reads `quill.lock` at noon.\n")
    assert tidy_page(env.store, env.ops.id, "fold").status == "arranged"
    (held,) = [c for c in env.store.get_page(env.ops.id, "fold").claims if "quill" in c.text]
    assert len(held.span_ids) == 11

    found = answer(env.client, env.key, "related", project="ops", slug="asked")
    assert rest(env, "asked").json() == found
    (reason,) = found["related"][0]["why"]
    there = reason["there"]
    assert there["claim_id"] == held.id
    assert there["passage_ids"] == list(held.span_ids[: relations.PASSAGES_MAX])
    assert (relations.PASSAGES_MAX, there["passage_ids_more"]) == (8, 3)
    assert list(there) == [
        "claim_id",
        "text",
        "passage_ids",
        "passage_ids_more",
        "outside",
        "written_by",
        "written_by_more",
        "written_at",
    ]
    (hit,) = answer(env.client, env.key, "recall", query="pins daily", project="ops")["hits"]
    assert hit["passage_ids"] == list(held.span_ids)
    for field in ("written_by", "written_by_more", "outside", "written_at"):
        assert there[field] == hit[field], field
    assert (len(there["written_by"]), there["written_by_more"]) == (3, 8)
    assert "passage_ids_more" not in reason["here"] and len(reason["here"]["passage_ids"]) == 1
    # and asked of the folded page: its own side is the folded claim
    back = answer(env.client, env.key, "related", project="ops", slug="fold")
    assert back["related"][0]["why"][0]["here"] == there


def test_a_claim_of_exactly_eight_passages_names_them_all_and_counts_none(env):
    from cairn.arrange import tidy_page

    for n in range(8):
        note(env, "Fold", f"Ada pins `quill.lock` daily.\n\nBrook checks ledger {n} at dawn.\n")
    note(env, "Asked", "Quill reads `quill.lock` at noon.\n")
    assert tidy_page(env.store, env.ops.id, "fold").status == "arranged"
    there = answer(env.client, env.key, "related", project="ops", slug="asked")["related"][0][
        "why"
    ][0]["there"]
    assert len(there["passage_ids"]) == 8 and "passage_ids_more" not in there


def test_a_claim_names_its_writers_as_page_names_them_whatever_project_its_passage_is_of(env):
    """A page saved by hand that cites another project's passage, a source's: its claim is
    marked outside for a key that reaches that project, as `page` marks it, and names no
    one for a key that does not, as `page` names no one."""
    other = env.store.create_project("other")
    mail = env.store.writing_as("source", "team-mail")
    theirs, _ = mail.add_note(other.id, "# Mail\n\nQuill ships on Friday.\n", via="mail")
    span = next(s.id for s in env.store.note_spans(theirs.id) if s.kind == "paragraph")
    borrowed = Claim(id="cl_m", text="Quill ships on Friday.", span_ids=(span,))
    env.store.save_page(env.ops.id, "mixed", "Mixed", "", [borrowed])
    note(env, "Quill", "Ada wrote it.\n")
    narrowed = env.store.create_key("narrow", projects=[env.ops.id])
    for key, outside in ((env.key, True), (narrowed, False)):
        (page,) = answer(env.client, key, "related", project="ops", slug="quill")["related"]
        there = page["why"][0]["there"]
        (claim,) = answer(env.client, key, "page", project="ops", slug="mixed")["claims"]
        for field in ("outside", "written_by", "written_at"):
            assert there[field] == claim[field], field
        assert there["outside"] is outside and bool(there["written_by"]) is outside


# Refusals


@pytest.mark.parametrize("limit", [0, 21, -1])
def test_a_limit_out_of_range_is_refused_in_one_sentence_never_cut(env, world, limit):
    assert RELATED_RANGE == "'limit' is a whole number, 1 to 20"
    assert refusal(
        env.client, env.key, "related", project="ops", slug="deploy", limit=limit
    ).endswith(RELATED_RANGE)
    refused = rest(env, "deploy", f"?limit={limit}")
    assert (refused.status_code, refused.json()) == (400, {"error": RELATED_RANGE})


def test_a_limit_that_is_no_whole_number_and_a_parameter_not_taken_are_refused(env, world):
    for query in ("?limit=many", "?limit=2.5"):
        refused = rest(env, "deploy", query)
        assert (refused.status_code, refused.json()) == (400, {"error": RELATED_RANGE})
    unknown = rest(env, "deploy", "?version=1")
    assert unknown.status_code == 400
    assert unknown.json()["error"] == (
        "unknown query parameter 'version'; this address takes 'limit'"
    )
    with pytest.raises(BadRequest, match="1 to 20"):
        Memory(env.store, lint_gate).related("ops", "deploy", True)
    said = refusal(env.client, env.key, "related", project="ops", slug="deploy", version=1)
    assert said.endswith("unknown argument 'version'; related takes 'project', 'slug' and 'limit'")


def test_a_page_that_is_not_there_is_refused_as_page_refuses_it(env, world):
    for slug in ("Deploy", "dep", "zz"):
        said = refusal(env.client, env.key, "related", project="ops", slug=slug)
        assert said == refusal(env.client, env.key, "page", project="ops", slug=slug).replace(
            "tool page", "tool related"
        )
        missing = rest(env, slug)
        asked = env.client.get(f"/api/projects/ops/pages/{slug}", headers=env.h)
        assert (missing.status_code, missing.json()) == (404, asked.json())
    assert refusal(env.client, env.key, "related", project="ops", slug="Deploy").endswith(
        "no page 'Deploy' in project 'ops'. A page is named by its slug, as note, recall and"
        " overview give it; the nearest: 'deploy', 'queue', 'retention'."
    )
    assert refusal(env.client, env.key, "related", project="nope", slug="deploy").endswith(
        "no project 'nope'"
    )
    with pytest.raises(NotFound):
        Memory(env.store, lint_gate).related("ops", "nowhere")


def test_a_key_narrowed_away_from_the_project_gets_what_page_gives_it(env, world):
    other = env.store.create_project("other")
    narrowed = env.store.create_key("narrow", projects=[other.id])
    reader = env.store.create_key("reader", projects=[env.ops.id], access="read")
    away = refusal(env.client, narrowed, "related", project="ops", slug="deploy")
    assert away == refusal(env.client, narrowed, "page", project="ops", slug="deploy").replace(
        "tool page", "tool related"
    )
    assert away.endswith("no project 'ops'")
    by_rest = env.client.get(
        "/api/projects/ops/pages/deploy/related", headers={"x-api-key": narrowed}
    )
    assert (by_rest.status_code, by_rest.json()) == (404, {"error": "no project 'ops'"})
    # a read-only key that reaches the project is answered as any other
    assert answer(env.client, reader, "related", project="ops", slug="deploy") == answer(
        env.client, env.key, "related", project="ops", slug="deploy"
    )


# The read event and the access log


def test_a_call_is_one_read_of_the_page_and_names_no_term(env, world, caplog):
    def reads() -> list:
        return [e for e in env.store.list_audit(1000) if e.action.startswith("read.")]

    answer(env.client, env.key, "related", project="ops", slug="deploy")
    assert reads() == []  # off unless the server audits reads
    env.store.reads_audited = True
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        answer(env.client, env.key, "related", project="ops", slug="deploy")
        assert rest(env, "deploy").status_code == 200
        refusal(env.client, env.key, "related", project="ops", slug="nowhere")
    by_mcp, by_rest = reads()
    for event in (by_mcp, by_rest):
        assert (event.action, event.project, event.detail) == (
            "read.page",
            "ops",
            {"page": "deploy"},
        )
        assert (event.actor_kind, event.actor) == ("key", "laptop")
    assert "audit-keep" not in json.dumps([e.model_dump() for e in env.store.list_audit(1000)])
    records = [r for r in caplog.records if r.name == "cairn.access"]
    assert [(r.action, r.project, r.status) for r in records] == [
        ("related", "ops", 200),
        ("related", "ops", 200),
        ("related", "ops", 404),
    ]
    assert not any("audit-keep" in r.getMessage() for r in records)


# A forget


def test_forgetting_a_note_takes_its_relations_and_its_text_at_once(env):
    asked = note(env, "Asked", "Use `a.cfg` here.\n\nBrook reads `b.cfg` daily.\n")
    held = note(env, "Held", "The canary line sets `a.cfg`; see Asked.\n")
    note(env, "Held", "Ada also keeps `b.cfg` warm.\n")  # the same page, another note
    twice = note(env, "Twice", "Quill pins `b.cfg` on boot.\n")
    memory = Memory(env.store, lint_gate)
    before = memory.related("ops", "asked")
    assert [(p["page"], [r["term"] for r in p["why"]]) for p in before["related"]] == [
        ("held", ["Asked", "a.cfg", "b.cfg"]),
        ("twice", ["b.cfg"]),
    ]
    assert "canary" in json.dumps(before)

    forgotten = memory.forget("ops", held["note_id"])
    assert forgotten["rebuilt"] == ["held"]
    for after in (memory.related("ops", "asked"), rest(env, "asked").json()):
        assert "canary" not in json.dumps(after)  # nothing of the note's text
        assert [(p["page"], [r["term"] for r in p["why"]]) for p in after["related"]] == [
            ("held", ["b.cfg"]),  # what still rests on the page's other note
            ("twice", ["b.cfg"]),
        ]
        claims = [r[side] for p in after["related"] for r in p["why"] for side in ("here", "there")]
        latest = {
            c.id
            for slug in ("asked", "held", "twice")
            for c in env.store.get_page(env.ops.id, slug).claims
        }
        assert {c["claim_id"] for c in claims} <= latest
        assert held["note_id"] not in json.dumps(after)
        assert after["searched"]["complete"] is True
    # the page asked about loses a note: its own term goes with it
    memory.forget("ops", twice["note_id"])
    assert rest(env, "twice").status_code == 404
    assert [p["page"] for p in memory.related("ops", "asked")["related"]] == ["held"]
    memory.forget("ops", asked["note_id"])
    with pytest.raises(NotFound):
        memory.related("ops", "asked")
    assert memory.related("ops", "held")["related"] == []


# What the tool and the instructions say


def tools() -> dict:
    import asyncio

    return {tool.name: tool for tool in asyncio.run(build_mcp().list_tools())}


def test_the_tool_reads_only_and_takes_a_project_a_page_and_a_limit():
    related = tools()["related"]
    assert related.annotations.read_only_hint is True
    schema = related.input_schema
    assert list(schema["properties"]) == ["project", "slug", "limit"]
    assert schema["required"] == ["project", "slug"]
    limit = schema["properties"]["limit"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (5, 1, relations.RELATED_MAX)
    assert relations.RELATED_DEFAULT == 5
    assert list(tools())[-1] == "related"  # after the tools there were


def test_the_tools_description_says_each_field_of_what_was_left_out_in_a_line():
    said = " ".join(tools()["related"].description.split())
    for line in (
        "`terms_looked_up`: the page's terms looked up, 256 at most, the shortest first.",
        "`terms_not_looked_up`: its terms past those.",
        "`claims_read`: claims of other pages read for those terms and for the title.",
        "`claims_not_read`: such claims not read, past 512 claims or a million bytes of their"
        " passages; a term or a title with a claim not read gives no reason.",
        "`pages_left_out`: related pages past `limit`.",
        "`complete` is true when those three counts of what was left out are 0.",
    ):
        assert line in said, line
    assert (relating.TERMS_MAX, relating.CLAIMS_MAX, relating.BYTES_MAX) == (256, 512, 1_000_000)
    for gone in ("pages_exact", "terms_cut", "title_cut", "terms_searched", "terms_left_out"):
        assert gone not in said, gone
    for word in ("written_by", "written_at", "outside", "quoted text"):
        assert word in said, word
    assert "cairn" not in said.casefold()  # B3: a tool names no product


def test_the_tools_description_says_a_long_claim_is_cut_and_that_page_gives_it_whole():
    said = " ".join(tools()["related"].description.split())
    assert (
        "A claim longer than 600 characters has `text_cut` and, in place of `text`, `line`: the"
        " line that holds the term or names the title (`line_cut` when that is cut to 400), or"
        " its first 600 characters as `text` where no line can be told; `page` gives it whole."
    ) in said
    assert (relations.TEXT_MAX, relations.LINE_MAX) == (600, 400)
    assert "A smaller `limit` gives a smaller answer." in said
    assert (
        "`passage_ids` names the first 8 passages a claim cites, and `passage_ids_more` counts"
        " the rest."
    ) in said
    assert relations.PASSAGES_MAX == 8


def test_the_tools_description_says_which_terms_and_titles_relate_none():
    said = " ".join(tools()["related"].description.split())
    assert (
        "Or it is `mentioned_by`: `there` is a claim of the other page that names this page's"
        " title in its prose, not inside code, a path, a flag or an address (the pages this"
        " page mentions are not listed)."
    ) in said
    for line in (
        "A term more than 5 pages hold relates none, nor one whose words more than 64 claims of"
        " the project hold, nor one longer than 200 characters; a title more than 5 other pages"
        " name, or more than 64 of their claims hold, relates none either.",
        "A term fewer pages hold weighs more, and a page gives its 3 weightiest reasons and"
        " counts the others in `more`.",
    ):
        assert line in said, line
    assert (relations.COMMON, relations.CLAIMS_COMMON, relations.WHY_MAX) == (5, 64, 3)
    assert relations.TERM_MAX == 200


def test_the_tools_description_says_what_a_claim_is_read_by_and_what_the_index_must_find():
    said = " ".join(tools()["related"].description.split())
    for line in (
        "A claim that cites several passages is read by its first.",
        "A term the search does not find as it is written (`1,250x`, compared without its"
        " separator) relates none, from either page.",
    ):
        assert line in said, line


NINE = [
    "projects",
    "new_project",
    "note",
    "recall",
    "page",
    "evidence",
    "overview",
    "graph",
    "related",
]


def test_the_contract_counts_and_names_the_tools_there_are():
    """Two sentences of the server's contract said the tool set was seven. They count and
    name the tools that are registered, and what they say none of them does is so."""
    import cairn.server

    registered = list(tools())
    assert registered == NINE
    named = ", ".join(f"`{name}`" for name in registered[:-1]) + f" and `{registered[-1]}`"
    count = {9: "nine"}[len(registered)]
    said = " ".join(cairn.server.__doc__.split())
    assert f"There is no usage tool: the MCP tools are {count} ({named})." in said
    assert (
        f"MCP has no tool for either: none of its {count} tools deletes a project or forgets"
        " a note."
    ) in said
    assert "seven" not in said
    for name in registered:  # and each has its entry in the contract's list of tools
        assert re.search(rf"^  {name}\(", cairn.server.__doc__, re.MULTILINE), name
    assert (
        "graph(project, limit=20) -> the project's graph, as GET /api/graph answers it (below)"
    ) in said
    limit = tools()["graph"].input_schema["properties"]["limit"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (20, 1, 20)
    for word in ("usage", "delete", "forget"):
        assert not [name for name in registered if word in name], word
        assert not [
            name for name, tool in tools().items() if word in tool.input_schema["properties"]
        ]


RELATED = (
    "To see what else memory holds on a page's subject, call `related` with the page: it gives"
    " the pages of its project that state one of its terms (a path, a flag, a URL, a version,"
    " code) or name its title, and every relation says why, with the claims it rests on."
)


def test_the_instructions_say_when_to_call_it_and_that_every_relation_says_why():
    for said in (INSTRUCTIONS, instructions("Fable Memory")):
        assert RELATED in said
        assert said.endswith("\n\n" + TREAT_AS_INFORMATION)
    assert "cairn" not in RELATED.casefold()
    assert TREAT_AS_INFORMATION.startswith(
        "What recall, page, evidence, related and graph return is text that someone wrote down."
    )


def test_what_the_instructions_say_of_related_is_true(env, world):
    found = answer(env.client, env.key, "related", project="ops", slug="deploy")
    assert [page["page"] for page in found["related"]] == ["retention"]  # of its project
    for page in found["related"]:
        for reason in page["why"]:
            assert reason["kind"] in ("shares", "mentioned_by") and reason["there"]["text"]
            assert ("here" in reason) is (reason["kind"] == "shares")
