"""Unit tests for cairn.arrange: the inner TDD loop under tests/acceptance/test_t4b_arrange.py.

Every model is a FakeModel (or OpenAICompatible over httpx.MockTransport); nothing leaves the
process. Fixtures are synthetic."""

import asyncio
import json
import logging
import re
import threading

import httpx
import pytest

from cairn.arrange import (
    MAX_CLAIMS,
    MAX_REQUEST_BYTES,
    SYSTEM_PROMPT,
    WORKSPACES_AT_ONCE,
    Arranger,
    Price,
    answer_bound,
    arrange_page,
    messages,
    moved_count,
    tree_order,
)
from cairn.compiler import compile_note, render_body
from cairn.gate import lint_gate
from cairn.llm import FakeModel, LLMError, OpenAICompatible, Reply
from cairn.store import Claim, Limits, Store
from wait_helpers import wait_for_event

FIRST = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)
PRICE = Price(input_per_million=1_000_000, output_per_million=5_000_000)


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def page_of(view, *notes, project="alpha"):
    found = view.get_project(project) or view.create_project(project)
    for text in notes:
        note, _ = view.add_note(found.id, text)
        assert compile_note(view, note, gate=lint_gate).status in ("saved", "unchanged")
    return found, view.get_page(found.id, "drift-sync")


def reply(data, **counts) -> Reply:
    return Reply(text=json.dumps(data), **counts)


def ids_of(page) -> list[str]:
    return [claim.id for claim in page.claims]


# The answer: everything the frozen file does not name


def bad_answers(ids):
    first, again = ids[0], ids[3]  # the two claims that say "Drift mirrors two buckets."
    return {
        "same is missing": {"order": ids},
        "same is not a list": {"order": ids, "same": {"a": 1}},
        "a group of one": {"order": ids, "same": [[first]]},
        "a group that is not a list": {"order": ids, "same": [first]},
        "an id in two groups": {
            "order": [i for i in ids if i != again],
            "same": [[first, again], [first, again]],
        },
        "a kept id folded elsewhere": {
            "order": [i for i in ids if i not in (again, ids[1])],
            "same": [[first, again], [again, ids[1]]],
        },
        "a number for an id": {"order": [*ids[:-1], 7], "same": []},
        "order is a string": {"order": " ".join(ids), "same": []},
        "an unknown id in a group": {"order": ids, "same": [[first, "cl_0000000000000000"]]},
    }


@pytest.mark.parametrize("name", list(bad_answers(["a"] * 6)))
def test_every_other_shape_is_refused_and_the_retry_names_it_in_cairns_words(store, name):
    project, before = page_of(store, FIRST, SECOND)
    bad = reply(bad_answers(ids_of(before))[name])
    model = FakeModel([bad, bad])
    result = arrange_page(store, project.id, "drift-sync", model)
    assert (result.status, result.draft.attempts) == ("invalid", 2), name
    assert result.problem and store.get_page(project.id, "drift-sync") == before
    first_user, second_user = model.requests[0][-1]["content"], model.requests[1][-1]["content"]
    assert second_user.startswith(first_user) and "not an arrangement" in second_user
    assert "cl_0000000000000000" not in second_user  # never the model's own words back


def test_an_answer_cut_off_at_the_token_limit_is_refused(store):
    project, before = page_of(store, FIRST, SECOND)
    cut = Reply(text=json.dumps({"order": ids_of(before), "same": []}), finish_reason="length")
    model = FakeModel([cut, cut])
    result = arrange_page(store, project.id, "drift-sync", model)
    assert result.status == "invalid" and "cut off" in result.problem


def test_a_think_block_and_a_fence_around_the_answer_are_dropped(store):
    project, before = page_of(store, FIRST, SECOND)
    ids = ids_of(before)
    text = "<think>reorder</think>\n```json\n" + json.dumps({"order": ids[::-1], "same": []})
    model = FakeModel([text + "\n```"])
    assert arrange_page(store, project.id, "drift-sync", model).status == "arranged"


def test_a_same_group_across_sections_is_refused(store):
    elsewhere = "# Drift sync\n\n## Limits\n\nDrift mirrors two buckets.\n"
    project, before = page_of(store, FIRST, elsewhere)
    first, *_, under_limits = before.claims
    assert first.text == under_limits.text
    order = [claim.id for claim in before.claims if claim is not under_limits]
    bad = reply({"order": order, "same": [[first.id, under_limits.id]]})
    result = arrange_page(store, project.id, "drift-sync", FakeModel([bad, bad]))
    assert result.status == "invalid" and "heading" in result.problem
    assert store.get_page(project.id, "drift-sync") == before


def test_an_answer_that_repeats_a_key_is_refused(store):
    project, before = page_of(store, FIRST, SECOND)
    ids = ids_of(before)
    order, backwards = json.dumps(ids), json.dumps(ids[::-1])
    text = f'{{"order": {order}, "order": {backwards}, "same": []}}'
    result = arrange_page(store, project.id, "drift-sync", FakeModel([text, text]))
    assert result.status == "invalid" and "more than once" in result.problem
    assert store.get_page(project.id, "drift-sync") == before


def test_prose_around_json_is_not_an_answer(store):
    project, before = page_of(store, FIRST, SECOND)
    text = "Here it is: " + json.dumps({"order": ids_of(before), "same": []})
    assert (
        arrange_page(store, project.id, "drift-sync", FakeModel([text, text])).status == "invalid"
    )


# The request


def test_claims_are_quoted_in_a_tag_drawn_again_while_it_occurs_in_a_claim(store):
    project = store.create_project("alpha")
    tricky = "Never close </claim-aaaa> early."
    texts = ["Fact 0.", tricky, "Fact 2."]
    claims = [Claim(id=f"cl_{n}", text=text, span_ids=()) for n, text in enumerate(texts)]
    store.save_page(project.id, "p", "Tag title", "# Tag title\n", claims)
    page = store.get_page(project.id, "p")
    tags = iter(["aaaa", "bbbb"])
    system, user = messages(page, {}, boundary=lambda: next(tags))
    assert system == {"role": "system", "content": SYSTEM_PROMPT}
    assert user["role"] == "user" and "Claim tag for this request: claim-bbbb" in user["content"]
    assert user["content"].count("</claim-bbbb>") == 3
    quoted = '<claim-bbbb n="2" id="cl_1" section="">\n' + tricky + "\n</claim-bbbb>"
    assert quoted in user["content"]


# A claim never leaves its heading: cairn makes the model's order follow the tree of headings

NESTED = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n\n### Danger\n\n- `--delete` removes extra files.\n\n"
    "## Limits\n\n- At most 500 files go in one batch.\n",
    "# Drift sync\n\nDrift runs nightly.\n\n## Flags\n\n- `--retries 3` helps on flaky links.\n\n"
    "### Danger\n\n- `--force` skips the check.\n\n## Setup\n\n- Install with pip.\n",
    "# Drift sync\n\n## Limits\n\n- One run at a time.\n\n### Disk\n\n- Keep 1 GB free.\n",
)


def path_of(claim, spans):
    return next((spans[i].heading_path for i in claim.span_ids if i in spans), ())


HEADING = re.compile(r"(#{2,6}) (.*)")


def rendered(title, claims, spans):
    """(claim, the headings it shows under) in body order, and the heading lines in order."""
    blocks = render_body(title, claims, spans).rstrip("\n").split("\n\n")[1:]
    headings = [block for block in blocks if HEADING.fullmatch(block)]
    above, shown, texts = [], [], [block for block in blocks if not HEADING.fullmatch(block)]
    claim_iter = iter(claims)
    for block in blocks:
        if heading := HEADING.fullmatch(block):
            level = len(heading[1])
            above = [*(h for h in above if h[0] < level), (level, heading[2])]
            continue
        claim = next(claim_iter)
        assert claim.text == block
        shown.append((claim, tuple(text for _, text in above)))
    assert len(texts) == len(claims)
    return shown, headings


def keeps_the_tree(title, claims, spans):
    shown, headings = rendered(title, claims, spans)
    owns = []
    for claim, under in shown:
        path = path_of(claim, spans)
        own = path[1:] if path[:1] == (title,) else path
        assert under == own, (claim.text, under, path)
        owns.append(own)
    runs = [path_of(c, spans) for c in claims]
    starts = [path for n, path in enumerate(runs) if n == 0 or runs[n - 1] != path]
    assert len(starts) == len(set(starts)), "a heading's claims are split"
    nodes = {own[:depth] for own in owns for depth in range(1, len(own) + 1)}
    assert len(headings) == len(nodes)  # each heading of the tree written once


def sections_of(view, page):
    spans = view.get_spans([i for claim in page.claims for i in claim.span_ids])
    return {claim.id: path_of(claim, spans) for claim in page.claims}, spans


def test_a_page_that_grew_note_by_note_is_grouped_when_the_model_groups_it(store):
    project, before = page_of(store, FIRST, SECOND)  # two notes that share "## Flags"
    sections, spans = sections_of(store, before)
    grouped = tree_order(ids_of(before), sections)
    assert grouped != ids_of(before)  # the page as compiled does not follow the tree
    result = arrange_page(
        store, project.id, "drift-sync", FakeModel([reply({"order": grouped, "same": []})])
    )
    assert (result.status, result.version) == ("arranged", before.version + 1)
    after = store.get_page(project.id, "drift-sync")
    assert ids_of(after) == grouped and after.body.count("## Flags") == 1
    keeps_the_tree(after.title, after.claims, spans)


def test_the_pages_own_order_given_back_is_unchanged(store):
    project, before = page_of(store, FIRST, SECOND)
    model = FakeModel([reply({"order": ids_of(before), "same": []})])
    result = arrange_page(store, project.id, "drift-sync", model)
    assert (result.status, result.version) == ("unchanged", before.version)
    assert store.get_page(project.id, "drift-sync") == before


def test_a_page_in_tree_order_answered_with_an_order_that_sorts_back_to_it_is_unchanged(store):
    project, compiled = page_of(store, FIRST, SECOND)
    sections, _ = sections_of(store, compiled)
    grouped = tree_order(ids_of(compiled), sections)
    first = arrange_page(
        store, project.id, "drift-sync", FakeModel([reply({"order": grouped, "same": []})])
    )
    before = store.get_page(project.id, "drift-sync")
    assert first.status == "arranged" and ids_of(before) == grouped  # now in tree order
    scattered = [*grouped[2:], *grouped[:2]]  # heading-less claims last: sorts back to it
    assert scattered != grouped and tree_order(scattered, sections) == grouped
    model = FakeModel([reply({"order": scattered, "same": []})])
    result = arrange_page(store, project.id, "drift-sync", model)
    assert (result.status, result.version) == ("unchanged", before.version)
    assert store.get_page(project.id, "drift-sync") == before


def test_the_reversed_frozen_page_keeps_every_claim_under_its_own_heading(store):
    project, before = page_of(store, FIRST, SECOND)
    model = FakeModel([reply({"order": ids_of(before)[::-1], "same": []})])
    assert arrange_page(store, project.id, "drift-sync", model).status == "arranged"
    after = store.get_page(project.id, "drift-sync")
    spans = store.get_spans([i for claim in after.claims for i in claim.span_ids])
    body = after.body
    assert body.index("Drift mirrors two buckets.") < body.index("## Flags")
    keeps_the_tree(after.title, after.claims, spans)


def test_an_order_that_interleaves_two_headings_shows_each_heading_once(store):
    project, before = page_of(store, FIRST, SECOND)
    intro, dry, delete, again, retries, limit = ids_of(before)
    order = [limit, dry, intro, delete, again, retries]  # Limits, Flags, none, Flags, none
    model = FakeModel([reply({"order": order, "same": []})])
    result = arrange_page(store, project.id, "drift-sync", model)
    after = store.get_page(project.id, "drift-sync")
    assert after.body.count("## Flags") == 1 and after.body.count("## Limits") == 1
    assert ids_of(after) == [intro, again, limit, dry, delete, retries]
    assert result.moved == moved_count([ids_of(before).index(i) for i in ids_of(after)])


def test_a_sub_headings_claim_never_sits_under_a_sibling_or_the_parents_next_heading(store):
    project, before = page_of(store, *NESTED)
    spans = store.get_spans([i for claim in before.claims for i in claim.span_ids])
    by_path = {}
    for claim in before.claims:
        by_path.setdefault(path_of(claim, spans), []).append(claim.id)
    danger = by_path[("Drift sync", "Flags", "Danger")]
    disk = by_path[("Drift sync", "Limits", "Disk")]
    setup = by_path[("Drift sync", "Setup")]
    rest = [c.id for c in before.claims if c.id not in danger + disk + setup]
    order = [*setup, danger[0], *rest, disk[0], danger[1]]  # Danger split by everything
    model = FakeModel([reply({"order": order, "same": []})])
    assert arrange_page(store, project.id, "drift-sync", model).status == "arranged"
    after = store.get_page(project.id, "drift-sync")
    keeps_the_tree(after.title, after.claims, spans)
    shown = [path_of(c, spans) for c in after.claims]
    assert shown.index(("Drift sync", "Flags", "Danger")) > shown.index(("Drift sync", "Flags"))


def test_any_order_the_model_gives_keeps_the_tree_and_shows_each_heading_once(store):
    import random

    _project, before = page_of(store, *NESTED)
    spans = store.get_spans([i for claim in before.claims for i in claim.span_ids])
    sections = {claim.id: path_of(claim, spans) for claim in before.claims}
    by_id = {claim.id: claim for claim in before.claims}
    draw = random.Random(20261111)
    for _ in range(300):
        order = ids_of(before)
        draw.shuffle(order)
        kept = tree_order(order, sections)
        assert sorted(kept) == sorted(order)
        keeps_the_tree(before.title, [by_id[i] for i in kept], spans)


def test_the_section_of_a_claim_is_the_heading_path_of_its_first_span(store):
    _, page = page_of(store, FIRST, SECOND)
    spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
    user = messages(page, spans)[1]["content"]
    assert 'section="Drift sync > Flags">\n- `--dry-run`' in user
    assert 'section="Drift sync > Limits">' in user


def test_the_answer_asked_for_is_bounded_by_the_page_size(store):
    project, before = page_of(store, FIRST, SECOND)
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        body = {"order": ids_of(before), "same": []}
        message = {"role": "assistant", "content": json.dumps(body)}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    model = OpenAICompatible("http://127.0.0.1:9/v1", "m", transport=httpx.MockTransport(handler))
    assert arrange_page(store, project.id, "drift-sync", model).status == "unchanged"
    assert sent[0]["max_tokens"] == answer_bound(6) == 32 + 6 * 12


def test_a_page_past_the_size_bound_is_never_sent(store):
    project, before = page_of(store, FIRST, SECOND)
    model = FakeModel([])
    result = arrange_page(store, project.id, "drift-sync", model, max_claims=5)
    assert (result.status, result.version, model.requests) == ("large", before.version, [])
    assert MAX_CLAIMS == 300


def test_a_request_past_the_byte_bound_is_never_sent_however_few_its_claims(store):
    accents = "# Drift sync\n\n" + "\n\n".join(f"Été numéro {n}, déjà passé." for n in range(6))
    project, before = page_of(store, accents + "\n")
    spans = store.get_spans([i for claim in before.claims for i in claim.span_ids])
    ask = messages(before, spans, lambda: "t0")
    size = sum(len(message["content"].encode()) for message in ask)
    assert sum(len(message["content"]) for message in ask) < size  # bytes, not characters

    model = FakeModel([])
    result = arrange_page(
        store, project.id, "drift-sync", model, boundary=lambda: "t0", max_bytes=size - 1
    )
    assert (result.status, result.version, model.requests) == ("large", before.version, [])
    assert result.problem and "bytes" in result.problem

    retried = FakeModel([reply({"order": [], "same": []})])  # its retry is longer still
    result = arrange_page(
        store, project.id, "drift-sync", retried, boundary=lambda: "t0", max_bytes=size
    )
    assert (result.status, result.draft.attempts, len(retried.requests)) == ("large", 1, 1)
    assert MAX_REQUEST_BYTES == 64_000


# The guard, the budget and a provider that fails


def test_a_page_the_policy_now_refuses_is_not_sent(store):
    store.set_setting("guard.policy", "off")
    note = SECOND.replace("flaky links.", "flaky links. Ask ada@example.org.")
    project, before = page_of(store, FIRST, note)
    store.set_setting("guard.policy", "secrets+personal")
    model = FakeModel([])
    result = arrange_page(store, project.id, "drift-sync", model)
    assert (result.status, result.version, model.requests) == ("guarded", before.version, [])
    assert store.usage().model_tokens_in == 0


def test_a_heading_the_policy_now_refuses_keeps_the_page_from_the_model(store):
    store.set_setting("guard.policy", "off")
    note = SECOND.replace("## Limits", "## Limits (ask ada@example.org)")
    project, _ = page_of(store, FIRST, note)
    store.set_setting("guard.policy", "secrets+personal")
    model = FakeModel([])
    assert arrange_page(store, project.id, "drift-sync", model).status == "guarded"
    assert model.requests == []


def test_a_workspace_out_of_budget_costs_one_look_and_one_line_until_it_is_let_go(store, caplog):
    project, _ = page_of(store, FIRST, SECOND)
    now = [100.0]
    empty = store.limited(Limits(model_budget_micros=0))
    reads = []

    class Counting:
        def __init__(self, view):
            self._view = view

        def __getattr__(self, name):
            reads.append(name)
            return getattr(self._view, name)

    model = FakeModel([])
    arranger = Arranger(
        model, price=PRICE, view_for=lambda a: Counting(empty), min_claims=1, clock=lambda: now[0]
    )
    arranger.queue(store.account_id, project.id, "drift-sync")
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        first = asyncio.run(arranger.run_once())
        for _ in range(5):
            assert asyncio.run(arranger.run_once()) == []  # held: not even looked at
    assert [d.status for d in first] == ["no budget"]
    assert reads == ["model_budget_left"] and model.requests == []
    assert caplog.text.count("no budget") == 1
    assert [p.slug for p in arranger.pending()] == ["drift-sync"]  # it waits, queued
    now[0] += 61
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["no budget"]


def test_a_call_that_could_cost_more_than_is_left_is_not_made(store):
    project, before = page_of(store, FIRST, SECOND)
    ids = ids_of(before)
    model = FakeModel(
        [reply({"order": ids[::-1], "same": []}, prompt_tokens=1, completion_tokens=1)]
    )
    tight = store.limited(Limits(model_budget_micros=1_100))  # under the most the call may cost
    assert arrange_page(tight, project.id, "drift-sync", model, price=PRICE).status == "no budget"
    assert model.requests == []
    roomy = store.limited(Limits(model_budget_micros=10_000))
    assert arrange_page(roomy, project.id, "drift-sync", model, price=PRICE).status == "arranged"
    unpriced = FakeModel([])
    empty = store.limited(Limits(model_budget_micros=0))
    assert arrange_page(empty, project.id, "drift-sync", unpriced).status == "no budget"
    assert unpriced.requests == []


def test_a_call_reserves_its_upper_bound_before_it_is_made_and_settles_after(store):
    project, before = page_of(store, FIRST, SECOND)
    held = []

    class Looking(FakeModel):
        def complete(self, messages):
            held.extend(store.model_reservations())
            return super().complete(messages)

    answer = reply(
        {"order": ids_of(before)[::-1], "same": []}, prompt_tokens=7, completion_tokens=3
    )
    model = Looking([answer])
    limited = store.limited(Limits(model_budget_micros=1_000_000))
    assert arrange_page(limited, project.id, "drift-sync", model, price=PRICE).status == "arranged"
    size = sum(len(message["content"].encode()) for message in model.requests[0])
    assert [r.micros for r in held] == [PRICE.micros(size, answer_bound(6))]
    assert store.model_reservations() == []
    assert store.usage().model_micros == PRICE.micros(7, 3)


def test_two_calls_at_once_cannot_spend_the_same_budget(store):
    project, before = page_of(store, FIRST, SECOND)
    other, _ = page_of(store, FIRST, SECOND, project="beta")
    limited = store.limited(Limits(model_budget_micros=4_000))  # room for one call's bound
    meanwhile = []

    class Overlapping(FakeModel):
        def complete(self, messages):
            beside = FakeModel([])
            meanwhile.append(arrange_page(limited, other.id, "drift-sync", beside, price=PRICE))
            assert beside.requests == []
            return super().complete(messages)

    model = Overlapping([reply({"order": ids_of(before)[::-1], "same": []})])
    assert arrange_page(limited, project.id, "drift-sync", model, price=PRICE).status == "arranged"
    assert [result.status for result in meanwhile] == ["no budget"]


def test_a_call_that_raises_anything_else_leaves_its_reservation_spent_in_full(store):
    project, _ = page_of(store, FIRST, SECOND)

    class Broken(FakeModel):
        def complete(self, messages):
            super().complete(messages)
            raise RuntimeError("the client broke")

    model = Broken([reply({"order": [], "same": []})])
    with pytest.raises(RuntimeError):
        arrange_page(store, project.id, "drift-sync", model, price=PRICE)
    (left,) = store.model_reservations()
    size = sum(len(message["content"].encode()) for message in model.requests[0])
    assert left.micros == PRICE.micros(size, answer_bound(6)) == store.usage().model_micros


def test_every_request_the_client_sent_is_charged_and_reserved_for(store):
    project, before = page_of(store, FIRST, SECOND)
    answer = json.dumps({"order": ids_of(before)[::-1], "same": []})
    bodies, held = [], []

    def handler(request):
        bodies.append(json.loads(request.content))
        held.extend(store.model_reservations())
        if len(bodies) == 1:
            return httpx.Response(503)
        usage = {"prompt_tokens": 7, "completion_tokens": 3}
        body = {"choices": [{"message": {"content": answer}}], "usage": usage}
        return httpx.Response(200, json=body)

    model = OpenAICompatible(
        "http://127.0.0.1:9/v1", "m", retries=2, sleep=lambda s: None,
        transport=httpx.MockTransport(handler),
    )  # fmt: skip
    result = arrange_page(store, project.id, "drift-sync", model, price=PRICE)
    assert (result.status, len(bodies)) == ("arranged", 2)
    size = sum(len(m["content"].encode()) for m in bodies[0]["messages"])
    each = PRICE.micros(size, answer_bound(6))
    assert held[0].micros == 3 * each  # three requests at most
    used = store.usage()  # the 503 reported nothing: it is paid at what it was reserved at
    assert (used.model_tokens_in, used.model_tokens_out) == (7 + size, 3 + answer_bound(6))
    assert used.model_micros == PRICE.micros(7, 3) + each


def test_a_failure_that_does_not_say_how_many_requests_is_charged_for_each_it_could_be(store):
    project, _ = page_of(store, FIRST, SECOND)

    class Retrying(FakeModel):
        max_requests = 3

    model = Retrying([LLMError("the endpoint answered HTTP 503")])
    assert arrange_page(store, project.id, "drift-sync", model, price=PRICE).status == "failed"
    ask = model.requests[0]
    size = sum(len(m["content"].encode()) for m in ask)
    each = PRICE.micros(size, answer_bound(6))
    used = store.usage()
    assert used.model_tokens_in == 3 * size and store.model_reservations() == []
    assert used.model_micros == 3 * each  # the bound, for each request it could have sent

    answered = Retrying([reply({"order": ids_of(store.get_page(project.id, "drift-sync"))[::-1],
                                "same": []}, prompt_tokens=7, completion_tokens=3)])  # fmt: skip
    assert arrange_page(store, project.id, "drift-sync", answered, price=PRICE).status == "arranged"
    assert store.usage().model_micros - used.model_micros == PRICE.micros(7, 3) + 2 * each


class Client(FakeModel):
    """A client that retries, scripted: each item is (the requests it sent, then a Reply or
    the LLMError it raised), as OpenAICompatible reports them."""

    max_requests = 3

    def __init__(self, script):
        super().__init__([outcome for _, outcome in script])
        self._sent = [sent for sent, _ in script]
        self._last = None

    def complete(self, messages):
        self._last = self._sent.pop(0)
        try:
            return super().complete(messages)
        except LLMError as error:
            error.requests = self._last
            raise

    def last_requests(self):
        return self._last


# The month's model_micros after one call, as (requests paid at their bound, reported
# (tokens in, out) or None). `each` is one request's bound: its UTF-8 bytes as sent, read as
# tokens, and answer_bound(6) tokens out, at PRICE.
FAILED_CALLS = {
    "a timeout after sending": (1, "the endpoint did not answer within 60 s (ReadTimeout)"),
    "a truncated answer, every try": (3, "could not reach the endpoint (RemoteProtocolError)"),
    "an invalid completion": (1, "the endpoint's reply is not a chat completion"),
    "a connection refused before sending": (0, "could not reach the endpoint (ConnectError)"),
}


@pytest.mark.parametrize("case", list(FAILED_CALLS))
def test_a_request_sent_whose_usage_was_never_reported_is_paid_at_its_bound(store, case):
    project, _ = page_of(store, FIRST, SECOND)
    sent, message = FAILED_CALLS[case]
    model = Client([(sent, LLMError(message))])
    result = arrange_page(store, project.id, "drift-sync", model, price=PRICE)
    assert result.status == "failed" and store.model_reservations() == []
    size = sum(len(m["content"].encode()) for m in model.requests[0])
    each = PRICE.micros(size, answer_bound(6))
    used = store.usage()
    assert used.model_micros == sent * each, case
    assert (used.model_tokens_in, used.model_tokens_out) == (sent * size, sent * answer_bound(6))


def test_two_failed_tries_then_an_answer_pay_both_tries_at_their_bound(store):
    project, before = page_of(store, FIRST, SECOND)
    answer = reply(
        {"order": ids_of(before)[::-1], "same": []}, prompt_tokens=7, completion_tokens=3
    )
    model = Client([(3, answer)])
    assert arrange_page(store, project.id, "drift-sync", model, price=PRICE).status == "arranged"
    size = sum(len(m["content"].encode()) for m in model.requests[0])
    each = PRICE.micros(size, answer_bound(6))
    used = store.usage()
    assert used.model_micros == PRICE.micros(7, 3) + 2 * each  # only the answer said its usage
    assert (used.model_tokens_in, used.model_tokens_out) == (7 + 2 * size, 3 + 2 * answer_bound(6))


def test_a_stop_between_reserving_and_settling_leaves_every_request_spent(store):
    project, _ = page_of(store, FIRST, SECOND)

    class Stopped(Client):
        def complete(self, messages):
            super().complete(messages)
            raise SystemExit  # the process stops before the call is settled

    model = Stopped([(1, reply({"order": [], "same": []}))])
    with pytest.raises(SystemExit):
        arrange_page(store, project.id, "drift-sync", model, price=PRICE)
    size = sum(len(m["content"].encode()) for m in model.requests[0])
    (left,) = store.model_reservations()
    assert left.micros == 3 * PRICE.micros(size, answer_bound(6)) == store.usage().model_micros


def test_a_page_abandoned_at_shutdown_leaves_its_reservation_spent(store):
    project, _ = page_of(store, FIRST, SECOND)
    release, entered = threading.Event(), threading.Event()

    class Slow(FakeModel):
        def complete(self, messages):
            entered.set()
            release.wait(5)
            raise RuntimeError("abandoned")

    arranger = Arranger(Slow([]), price=PRICE, view_for=store.for_account, min_claims=1)
    arranger.queue(store.account_id, project.id, "drift-sync")

    async def run():
        task = asyncio.create_task(arranger.run_once())
        await asyncio.to_thread(entered.wait, 5)
        stopped = await asyncio.to_thread(arranger.stop, 0.05)
        task.cancel()
        return stopped

    assert asyncio.run(run()) is False
    (left,) = store.model_reservations()
    assert left.micros > 0 and store.usage().model_micros == left.micros
    release.set()


def test_a_provider_failure_ends_at_once_and_is_charged_at_its_bound(store):
    project, _ = page_of(store, FIRST, SECOND)
    model = FakeModel([LLMError("the endpoint answered HTTP 503")])
    result = arrange_page(store, project.id, "drift-sync", model, price=PRICE)
    assert (result.status, result.draft.attempts, result.draft.usage_complete) == (
        "failed",
        1,
        False,
    )
    assert result.problem == "the endpoint answered HTTP 503"
    size = sum(len(m["content"].encode()) for m in model.requests[0])
    used = store.usage()  # a model that does not count: the one request it can send
    assert (used.model_tokens_in, used.model_tokens_out) == (size, answer_bound(6))
    assert used.model_micros == PRICE.micros(size, answer_bound(6))


def test_price_rounds_up_to_a_whole_millionth():
    assert Price(input_per_million=1, output_per_million=0).micros(1, 0) == 1
    assert Price(input_per_million=0, output_per_million=0).micros(10**6, 10**6) == 0
    assert PRICE.micros(300, 40) == 500
    with pytest.raises(ValueError):
        Price(input_per_million=-1, output_per_million=0)


def test_moved_counts_the_fewest_claims_that_change_place():
    assert moved_count([0, 1, 2, 3]) == 0
    assert moved_count([0, 1, 4, 2, 5]) == 1
    assert moved_count([3, 2, 1, 0]) == 3
    assert moved_count([]) == 0


def test_a_page_the_verbatim_gate_refuses_is_left_as_it_is(store):
    project = store.create_project("alpha")
    claims = [Claim(id=f"cl_{n}", text=f"Made up {n}.", span_ids=("sp_none",)) for n in range(3)]
    store.save_page(project.id, "p", "P", "# P\n", claims)
    model = FakeModel([reply({"order": ["cl_2", "cl_1", "cl_0"], "same": []})])
    result = arrange_page(store, project.id, "p", model)
    assert result.status == "rejected" and result.problem
    assert store.page_versions(project.id, "p") == [1]


def test_a_page_whose_claims_repeat_an_id_is_refused_before_any_call(store):
    project, before = page_of(store, FIRST, SECOND)
    twice = (*before.claims, before.claims[0])
    store.save_page(project.id, "drift-sync", before.title, before.body, twice)
    model = FakeModel([])
    result = arrange_page(store, project.id, "drift-sync", model, price=PRICE)
    assert (result.status, result.version, model.requests) == ("invalid page", 3, [])
    assert result.problem and before.claims[0].text not in result.problem
    assert store.usage().model_micros == 0


@pytest.mark.parametrize("name", ["m" * 81, "fake\narranger", ""])
def test_a_model_whose_name_the_audit_log_cannot_hold_is_never_called(store, name):
    project, _ = page_of(store, FIRST, SECOND)
    model = FakeModel([], name=name)
    result = arrange_page(store, project.id, "drift-sync", model, price=PRICE)
    assert (result.status, model.requests) == ("failed", [])
    assert "name" in result.problem and store.usage().model_micros == 0


def test_an_unknown_page_is_gone(store):
    project = store.create_project("alpha")
    model = FakeModel([])
    result = arrange_page(store, project.id, "nothing", model)
    assert (result.status, result.version, model.requests) == ("gone", None, [])


# The worker


def test_no_model_call_runs_on_the_event_loop(store):
    project, before = page_of(store, FIRST, SECOND)
    threads = []

    class Watching(FakeModel):
        def complete(self, messages):
            threads.append(threading.current_thread())
            return super().complete(messages)

    async def run(arranger):
        loop_thread = threading.current_thread()
        done = await arranger.run_once()
        return loop_thread, done

    arranger = Arranger(
        Watching([reply({"order": ids_of(before)[::-1], "same": []})]),
        price=None, view_for=store.for_account, min_claims=1,
    )  # fmt: skip
    arranger.queue(store.account_id, project.id, "drift-sync")
    loop_thread, done = asyncio.run(run(arranger))
    assert [d.status for d in done] == ["arranged"]
    assert threads and threads[0] is not loop_thread


def test_a_version_is_put_to_the_model_once(store):
    project, _ = page_of(store, FIRST, SECOND)
    bad = reply({"order": [], "same": []})
    model = FakeModel([bad, bad])
    arranger = Arranger(model, price=None, view_for=store.for_account, min_claims=1)
    arranger.queue(store.account_id, project.id, "drift-sync")
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["invalid"]
    arranger.queue(store.account_id, project.id, "drift-sync")
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["seen"]
    assert len(model.requests) == 2  # the two tries of the first pass, and nothing since


def test_a_guarded_page_is_not_remembered_so_a_looser_policy_lets_it_through(store):
    store.set_setting("guard.policy", "off")
    note = SECOND.replace("flaky links.", "flaky links. Ask ada@example.org.")
    project, before = page_of(store, FIRST, note)
    store.set_setting("guard.policy", "secrets+personal")
    model = FakeModel([reply({"order": ids_of(before)[::-1], "same": []})])
    arranger = Arranger(model, price=None, view_for=store.for_account, min_claims=1)
    arranger.queue(store.account_id, project.id, "drift-sync")
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["guarded"]
    store.set_setting("guard.policy", "off")
    arranger.queue(store.account_id, project.id, "drift-sync")
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["arranged"]


def test_a_stale_page_is_queued_again_three_times_then_waits_for_a_note(store, tmp_path):
    project, _ = page_of(store, FIRST, SECOND)
    other = Store(tmp_path / "cairn.db")
    notes = iter(range(100))

    class Moving:
        name = "moving"

        def complete(self, messages):
            note, _ = other.add_note(project.id, f"# Drift sync\n\nFact {next(notes)}.\n")
            compile_note(other, note, gate=lint_gate)
            page = other.get_page(project.id, "drift-sync")
            return reply({"order": ids_of(page)[: len(page.claims) - 1][::-1], "same": []})

    try:
        arranger = Arranger(Moving(), price=None, view_for=store.for_account, min_claims=1)
        arranger.queue(store.account_id, project.id, "drift-sync")
        statuses = []
        for _ in range(4):
            statuses += [d.status for d in asyncio.run(arranger.run_once())]
    finally:
        other.close()
    assert statuses == ["stale", "stale", "stale"]
    assert arranger.pending() == []


def test_a_workspace_that_is_gone_and_a_failing_page_are_dropped_and_logged(store, caplog):
    project, _ = page_of(store, FIRST, SECOND)

    def view_for(account_id):
        if account_id == "acct_gone":
            raise KeyError(account_id)
        return store.for_account(account_id)

    class Broken:
        name = "broken"

        def complete(self, messages):
            raise RuntimeError("secret words from somewhere")

    arranger = Arranger(Broken(), price=None, view_for=view_for, min_claims=1)
    arranger.queue("acct_gone", project.id, "drift-sync")
    arranger.queue(store.account_id, project.id, "drift-sync")
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        done = asyncio.run(arranger.run_once())
    assert [(d.account_id, d.status) for d in done] == [
        ("acct_gone", "gone"),
        (store.account_id, "failed"),
    ]
    assert arranger.pending() == []
    assert "RuntimeError" in caplog.text and "secret words" not in caplog.text


class Reversing:
    """A model that answers each request with its claim ids reversed, and records how many
    calls were in flight at once, in all and per workspace (by the title it was sent). Until
    `together` calls have been in flight at once, a call waits for that (at most `patience`
    seconds, so a worker that calls one at a time fails the test instead of hanging it); each
    call then waits for `release`."""

    name = "reversing"

    def __init__(self, together: int = 2, patience: float = 5.0) -> None:
        self.together, self.patience = together, patience
        self.release = threading.Event()
        self.release.set()
        self.lock = threading.Condition()
        self.now: dict[str, int] = {}
        self.most = 0
        self.most_per_title = 0

    def in_flight(self, calls: int) -> bool:
        """Wait until `calls` calls have been in flight at once (at most `patience` seconds)."""
        with self.lock:
            return self.lock.wait_for(lambda: self.most >= calls, timeout=self.patience)

    def complete(self, messages):
        user = messages[1]["content"]
        title = user.split("Page title: ", 1)[1].split("\n", 1)[0]
        with self.lock:
            self.now[title] = self.now.get(title, 0) + 1
            self.most = max(self.most, sum(self.now.values()))
            self.most_per_title = max(self.most_per_title, self.now[title])
            self.lock.notify_all()
            self.lock.wait_for(lambda: self.most >= self.together, timeout=self.patience)
        self.release.wait(self.patience)
        with self.lock:
            self.now[title] -= 1
        ids = [part.split('"', 1)[0] for part in user.split(' id="')[1:]]
        return reply({"order": ids[::-1], "same": []})


def note_for(n: int, title: str) -> str:
    lines = "".join(f"- Fact {k} of {title}.\n" for k in range(6))
    return f"# {title}\n\n{lines}"


def test_several_workspaces_are_arranged_at_once_each_one_page_at_a_time(store):
    model = Reversing()
    arranger = Arranger(model, price=None, view_for=store.for_account, min_claims=1)
    for n in range(6):  # more workspaces than are arranged at once
        view = store.for_account(store.create_account(f"W{n}").id)
        project = view.create_project("alpha")
        for title in (f"Page {n}a", f"Page {n}b"):
            note, _ = view.add_note(project.id, note_for(n, title))
            compile_note(view, note, gate=lint_gate)
            arranger.queue(view.account_id, project.id, title.lower().replace(" ", "-"))
    queued = arranger.pending()  # each workspace's two pages, one after the other
    done = asyncio.run(arranger.run_once(limit=12))
    in_turn = [*queued[0::2], *queued[1::2]]  # the first page of each, then the second
    assert [(d.account_id, d.slug) for d in done] == [(q.account_id, q.slug) for q in in_turn]
    assert {d.status for d in done} == {"arranged"}
    assert 1 < model.most <= WORKSPACES_AT_ONCE  # at once, and never more than the bound
    assert model.most_per_title == 1


def test_stop_waits_for_every_page_in_hand(store):
    model = Reversing()
    model.release.clear()  # every call is held until the test lets it go
    arranger = Arranger(model, price=None, view_for=store.for_account, min_claims=1)
    for n in range(2):
        view = store.for_account(store.create_account(f"W{n}").id)
        project = view.create_project("alpha")
        note, _ = view.add_note(project.id, note_for(n, f"Page {n}"))
        compile_note(view, note, gate=lint_gate)
        arranger.queue(view.account_id, project.id, f"page-{n}")

    async def run():
        task = asyncio.create_task(arranger.run_once())
        reached = await asyncio.to_thread(model.in_flight, 2)  # both pages in hand at once
        quick = await asyncio.to_thread(arranger.stop, 0.01)  # held: not done yet
        model.release.set()
        waited = await asyncio.to_thread(arranger.stop, 5.0)
        assert reached
        return quick, waited, await task

    quick, waited, done = asyncio.run(run())
    assert (quick, waited) == (False, True)
    assert [d.status for d in done] == ["arranged", "arranged"]


def test_workspaces_take_turns_within_a_pass(store):
    views = [store.for_account(store.create_account(name).id) for name in ("A", "B", "C")]
    arranger = Arranger(FakeModel([]), price=None, view_for=store.for_account, min_claims=99)
    for view in views:
        project = view.create_project("alpha")
        for n in range(3):
            arranger.queue(view.account_id, project.id, f"page-{n}")
    done = asyncio.run(arranger.run_once(limit=4))
    accounts = [d.account_id for d in done]
    assert accounts == [
        views[0].account_id,
        views[1].account_id,
        views[2].account_id,
        views[0].account_id,
    ]
    assert {d.status for d in done} == {"gone"}
    assert len(arranger.pending()) == 5


def test_a_background_loop_runs_until_cancelled_and_stop_waits_for_a_page(store, monkeypatch):
    project, before = page_of(store, FIRST, SECOND)
    model = FakeModel([reply({"order": ids_of(before)[::-1], "same": []})])
    arranger = Arranger(model, price=None, view_for=store.for_account, min_claims=1, every=0.01)

    async def run():
        arranged = asyncio.Event()
        run_once = arranger.run_once

        async def observed(limit=10):
            result = await run_once(limit)
            if store.get_page(project.id, "drift-sync").version == before.version + 1:
                arranged.set()
            return result

        monkeypatch.setattr(arranger, "run_once", observed)
        task = asyncio.create_task(arranger.loop())
        try:
            arranger.queue(store.account_id, project.id, "drift-sync")
            await wait_for_event(arranged, "the arranger did not write the next page version")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        return await asyncio.to_thread(arranger.stop, 1.0)

    assert asyncio.run(run()) is True
    assert store.get_page(project.id, "drift-sync").metrics["arranged_by"] == "fake"


# Wiring: notes queue their pages


def test_a_hook_that_fails_never_fails_a_note(store):
    from cairn.memory import Memory

    store.create_project("alpha")
    memory = Memory(store, lint_gate)
    seen = []
    memory.saved = lambda project_id, slug: seen.append(slug)
    assert memory.note("alpha", FIRST)["status"] == "saved"
    assert memory.note("alpha", FIRST)["status"] == "unchanged"  # nothing saved: not queued
    assert seen == ["drift-sync"]

    def broken(project_id, slug):
        raise RuntimeError("down")

    memory.saved = broken
    assert memory.note("alpha", SECOND)["status"] == "saved"


def test_an_agent_note_over_mcp_queues_its_page_and_no_loop_runs_without_every(tmp_path):
    from starlette.testclient import TestClient

    from cairn.server import create_app

    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    arranger = Arranger(FakeModel([]), price=None, view_for=store.for_account)
    app = create_app(store, public_url="http://127.0.0.1:8765", arranger=arranger)
    headers = {"x-api-key": key}
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "note", "arguments": {"project": "alpha", "text": FIRST}},
    }
    accept = {"accept": "application/json, text/event-stream", **headers}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.post("/api/projects", headers=headers, json={"name": "alpha"})
        answered = client.post("/mcp", headers=accept, json=call)
        assert answered.status_code == 200, answered.text
        assert [p.slug for p in arranger.pending()] == ["drift-sync"]
    assert arranger.pending()  # no background pass ran without `every`
    store.close()


def test_a_note_a_source_sync_writes_queues_its_page(store, monkeypatch):
    from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, sync_all

    class Feed:
        async def pull(self, cursor):
            draft = NoteDraft(project="alpha", title="Drift sync", text=FIRST,
                              source_ref="https://example.test/1", label="fake")  # fmt: skip
            return Pulled(drafts=[draft], cursor="c1")

    kind = SourceKind(check=dict, open=lambda name, config, **options: Feed(), via="fake")
    monkeypatch.setitem(KINDS, "fake", kind)
    project = store.create_project("alpha")
    store.add_source("feed", "fake", {"projects": ["alpha"]})
    queued = []
    asyncio.run(sync_all(store, http=httpx.AsyncClient(), env={},
                         saved=lambda *page: queued.append(page)))  # fmt: skip
    assert queued == [(store.account_id, project.id, "drift-sync")]


def test_the_apps_sync_and_the_apps_guard_reach_the_worker(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from cairn.guard import PatternGuard
    from cairn.server import create_app
    from cairn.web import auth

    store = Store(tmp_path / "cairn.db")
    store.set_setting("guard.policy", "off")
    note = SECOND.replace("flaky links.", "flaky links. Ask ada@example.org.")
    project, _ = page_of(store, FIRST, note)
    model = FakeModel([])
    arranger = Arranger(model, price=None, view_for=store.for_account, min_claims=1)
    asked = {}

    async def syncing(store, every, **options):
        asked.update(options)

    monkeypatch.setattr(auth, "sync_forever", syncing)
    strict = PatternGuard("secrets+personal")
    app = create_app(store, public_url="http://127.0.0.1:8765", arranger=arranger, guard=strict,
                     sync_every=60)  # fmt: skip
    with TestClient(app, base_url="http://127.0.0.1:8765"):
        pass
    assert asked["saved"] == arranger.queue
    arranger.queue(store.account_id, project.id, "drift-sync")
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["guarded"]
    assert model.requests == []
    store.close()


def test_the_web_view_says_who_arranged_a_version(tmp_path):
    from starlette.testclient import TestClient

    from cairn.server import create_app

    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    project, before = page_of(store, FIRST, SECOND)
    model = FakeModel([reply({"order": ids_of(before)[::-1], "same": []})], name="lane-b")
    assert arrange_page(store, project.id, "drift-sync", model).status == "arranged"
    app = create_app(store, public_url="http://127.0.0.1:8765")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.post("/login", data={"key": key}, headers={"sec-fetch-site": "same-origin"})
        latest = client.get("/b/alpha/p/drift-sync")
        earlier = client.get("/b/alpha/p/drift-sync?version=2")
    assert latest.status_code == 200 and "Arranged by lane-b" in latest.text
    assert earlier.status_code == 200 and "Arranged by" not in earlier.text
    store.close()
