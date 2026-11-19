"""The hold on Slack's notes goes by `via`, so it covers each way a note comes through Slack
(a message filed with the shortcut, a note typed with the command, a direct message), costs
what the hold on a withheld kind costs, and never turns on the hold by `source_ref` (M3) that
<PREFIX>_SOURCE_NOTES=send lifts. Every call to Slack goes to an httpx.MockTransport (Bench);
the model is a FakeModel. Every name is made up."""

import asyncio
import json

import pytest
from test_slack_commands_unit import ADA_SLACK, Bench, ask
from test_slack_messages_unit import handle as direct_message
from test_slack_shortcuts_unit import cedar
from test_slack_shortcuts_unit import handle as file_message

from cairn.arrange import Arranger, holds_withheld
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import FakeModel, Reply
from cairn.sources import HELD_VIA, withheld_via

FIRST = (
    "# Quince rows\n\nRow 4 holds the quince jars.\n\n## Shelves\n\n"
    "- The top shelf is for labels.\n- The low shelf is for lids.\n"
)
SECOND = (
    "# Quince rows\n\nRow 4 holds the quince jars.\n\n## Shelves\n\n"
    "- Brook moved the twine.\n\n## Counts\n\n- Ada counts the jars on Fridays.\n"
)


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    made.users["U0CEDAR01"] = cedar()
    yield made
    made.store.close()


def answer(count: int) -> Reply:
    return Reply(text=json.dumps({"order": list(range(count, 0, -1)), "same": []}))


def page_of(view, project, *, via=None, source_ref=None):
    for text in (FIRST, SECOND):
        note, _ = view.add_note(project.id, text, via=via, source_ref=source_ref)
        assert compile_note(view, note, gate=lint_gate).status in ("saved", "unchanged")
    return view.get_page(project.id, "quince-rows")


def judge(arranger, view, project, slugs):
    for slug in slugs:
        arranger.queue(view.account_id, project.id, slug)
    return {d.slug: d.status for d in asyncio.run(arranger.run_once(limit=len(slugs)))}


def test_a_filed_message_a_typed_note_and_a_direct_message_are_all_held(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    assert file_message(bench).status_code == 200
    ask(bench, "note Kiln log\n\nThe kiln is fired on Tuesdays.")
    assert direct_message(bench, "note: Twine shelf\n\nThe twine is on shelf 2.").status_code == 200
    vias = [via for *_, via in bench.notes()]
    assert vias == ["slack", "slack", "slack"]  # the three ways in, one `via`
    filed, typed, direct = (
        bench.view.get_note(note_id) for note_id, *_ in bench.notes()
    )  # what each carries as source_ref: a link for the filed one, nothing for the others
    assert filed.source_ref.startswith("https://") and typed.source_ref is direct.source_ref is None
    slack_pages = [page.slug for page in bench.view.list_pages(bench.library.id)]
    assert len(slack_pages) == 3
    control = page_of(bench.view, bench.library)
    model = FakeModel([answer(len(control.claims))] * 4)
    arranger = Arranger(model, price=None, view_for=bench.store.for_account, min_claims=1)
    done = judge(arranger, bench.view, bench.library, [*slack_pages, control.slug])
    assert done == {**dict.fromkeys(slack_pages, "withheld"), control.slug: "arranged"}
    assert len(model.requests) == 1


# Cost, in statements: deciding the hold for a page


def statements(store, decide) -> tuple[bool, int]:
    seen: list[str] = []
    store._con.set_trace_callback(seen.append)
    try:
        said = decide()
    finally:
        store._con.set_trace_callback(None)
    return said, len(seen)


def test_deciding_the_hold_costs_the_statements_it_did(bench):
    view = bench.view
    slack = page_of(view, view.create_project("filed"), via="slack")
    mail = page_of(view, view.create_project("mailed"), via="gmail")
    plain = page_of(view, view.create_project("plain"))
    kinds = withheld_via()

    def cost(page, withhold, held_via=HELD_VIA):
        return statements(
            bench.store, lambda: holds_withheld(view, page, withhold, held_via=held_via)
        )

    today_mail = statements(bench.store, lambda: holds_withheld(view, mail, kinds))
    today_plain = statements(bench.store, lambda: holds_withheld(view, plain, kinds))
    assert today_mail[0] is True and today_plain[0] is False
    assert cost(mail, kinds) == today_mail  # what was held costs what it did
    assert cost(plain, kinds) == today_plain  # and what was sent too
    assert cost(slack, kinds) == today_mail  # Slack's page, held as a kind's is
    assert cost(slack, ()) == today_mail  # under <PREFIX>_SOURCE_NOTES=send, the same
    assert cost(plain, (), held_via=()) == (False, 0)  # nothing held reads nothing


# What <PREFIX>_SOURCE_NOTES=send lifts stays lifted


def test_holding_slack_does_not_hold_what_source_notes_send_sends(bench):
    pages = {
        name: page_of(bench.view, bench.view.create_project(name), via=via, source_ref=ref)
        for name, via, ref in (
            ("mailed", "gmail", None),
            ("named", None, "mail:thread-4"),
            ("filed", "slack", None),
        )
    }
    model = FakeModel([answer(len(p.claims)) for p in pages.values()])
    arranger = Arranger(model, price=None, view_for=bench.store.for_account, withhold=())
    done = {}
    for name, page in pages.items():
        done |= {name: judge(arranger, bench.view, bench.view.get_project(name), [page.slug])}
    assert done == {
        "mailed": {"quince-rows": "arranged"},
        "named": {"quince-rows": "arranged"},
        "filed": {"quince-rows": "withheld"},
    }
    assert len(model.requests) == 2
