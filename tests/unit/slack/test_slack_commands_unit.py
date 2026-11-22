"""The slash command's words (cairn.slack.commands), each handler called as cairn.slack.dispatch
calls it, on a real store, with every call to Slack sent to an httpx.MockTransport. The
helpers here (Bench) are shared with the unit tests of direct messages and the shortcut.
Every name is made up."""

import asyncio
import dataclasses

from cairn.memory import Memory
from cairn.slack import blocks, commands, dispatch, people
from cairn.store import Claim, StalePage, Store
from support.slack.slack_commands import ADA_SLACK, BASE, BROOK_SLACK, ask, said
from support.slack.slack_commands import bench as bench


def test_every_word_has_its_handler_and_none_is_left_unregistered():
    assert {"", "find", "note", "project", "link", "unlink", "help"} <= set(dispatch.COMMANDS)
    for word in ("", "find", "note", "project", "link", "unlink", "help"):
        assert dispatch.COMMANDS[word].__module__ == commands.__name__


def test_help_none_and_blank_are_the_help_of_the_installs_command(bench):
    help_ = blocks.help_text("/cairn")
    for text in ("help", "", "  ", "HELP me"):
        answer = ask(bench, text)
        assert said(answer) == said(help_)
    assert bench.calls == []


def test_an_unlinked_user_gets_the_one_time_address_and_nothing_else(bench):
    for text in ("find jars", "note Row 4.", "project", "project library", "link"):
        answer = said(ask(bench, text))
        assert f"{BASE}/integrations/slack/link?t=" in answer
    offered = bench.store._con.execute("SELECT COUNT(*) FROM slack_link_offers").fetchone()
    assert offered == (1,) and bench.notes() == []  # one offer for a Slack user at a time


def test_a_link_to_someone_no_longer_a_member_is_no_link(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    bench.context = dataclasses.replace(bench.context, memory_for=lambda user_id: None)
    assert "link?t=" in said(ask(bench, "find jars"))


def test_find_runs_the_persons_search_shows_five_and_counts_the_rest(bench, monkeypatch):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    for n in range(7):
        claim = Claim(id=f"cl_jar{n:04d}", text=f"Jar {n} holds quince.", span_ids=())
        bench.owner.save_page(bench.library.id, f"jar-{n}", f"Jar {n}", "", [claim])
    asked = []
    real = Memory.recall

    def spying(self, query, project=None, limit=8):
        asked.append((query, limit))
        return real(self, query, project, limit)

    monkeypatch.setattr(Memory, "recall", spying)
    answer = said(ask(bench, "find   quince jars "))
    assert asked == [("quince jars", people.FOUND_COUNTED)]
    assert answer.count(f"<{BASE}/b/library/p/jar-") == 5
    assert "5 of 7 claims shown." in answer
    assert f"<{BASE}/search?q=quince%20jars|See them all on the search page>" in answer


def test_find_with_no_words_is_help_and_nothing_found_is_said(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    assert said(ask(bench, "find")) == said(blocks.help_text("/cairn"))
    assert said(ask(bench, "find zucchini")) == said(blocks.nothing_found("zucchini"))


def test_note_writes_to_the_slack_project_and_records_it_typed_before_answering(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    answer = said(ask(bench, "note Row 4 holds\n\nthe quince jars."))
    ((note_id, project_id, text, via),) = bench.notes()
    assert (project_id, text, via) == (bench.library.id, "Row 4 holds\n\nthe quince jars.", "slack")
    recorded = bench.store._con.execute(
        "SELECT install_id, how, conversation_id, message_time, brought_by FROM slack_notes"
        " WHERE note_id = ?",
        (note_id,),
    ).fetchall()
    assert recorded == [(bench.install.id, "typed", None, None, bench.ada)]
    assert answer.startswith("Filed: ") and f"{BASE}/b/library/p/row-4-holds" in answer


def test_a_note_without_a_project_is_refused_naming_the_command(bench):
    bench.link(ADA_SLACK, bench.ada)
    answer = said(ask(bench, "note Row 4."))
    assert answer == said(blocks.refused(people.NO_PROJECT.format(command="/cairn")))
    assert bench.notes() == []


def test_project_lists_the_projects_the_person_writes_to(bench):
    bench.owner.create_project("cellar", everyone="none")
    bench.owner.create_project("reading-room", everyone="read")
    bench.link(BROOK_SLACK, bench.brook)
    answer = said(ask(bench, "project", BROOK_SLACK))
    assert answer == said(blocks.project_is(None, writable=["library"]))
    bench.link(ADA_SLACK, bench.ada)
    everything = ["cellar", "library", "reading-room"]
    assert said(ask(bench, "project")) == said(blocks.project_is(None, writable=everything))


def test_the_projects_a_person_writes_to_are_the_stores_own_answer(bench, monkeypatch):
    memory = bench.memory_for(bench.brook)
    asked = []

    def answered(self):
        asked.append(self)
        return [bench.library]

    def private(*args, **kwargs):
        raise AssertionError("people.writable reads none of the store's private members")

    monkeypatch.setattr(Store, "writable_projects", answered)
    monkeypatch.setattr(Store, "_level", private)
    assert people.writable(memory) == [bench.library] and asked == [memory.store]


def test_project_name_chooses_one_by_name_in_any_case_and_refuses_the_rest_alike(bench):
    bench.owner.create_project("cellar", everyone="none")
    bench.owner.create_project("reading-room", everyone="read")
    bench.link(BROOK_SLACK, bench.brook)
    chosen = said(ask(bench, "project LIBRARY", BROOK_SLACK))
    assert chosen == said(blocks.project_is("library", writable=["library"]))
    assert bench.owner.slack_person(bench.install.id, BROOK_SLACK).project_id == bench.library.id
    refusal = said(blocks.refused(people.NOT_WRITABLE.format(command="/cairn")))
    for name in ("cellar", "reading-room", "attic"):
        assert said(ask(bench, f"project {name}", BROOK_SLACK)) == refusal
    assert bench.owner.slack_person(bench.install.id, BROOK_SLACK).project_id == bench.library.id


def test_a_project_no_longer_reached_is_shown_as_none_chosen(bench):
    cellar = bench.owner.create_project("cellar", everyone="none")
    bench.link(BROOK_SLACK, bench.brook, cellar)
    assert said(ask(bench, "project", BROOK_SLACK)) == said(
        blocks.project_is(None, writable=["library"])
    )


def test_link_says_who_and_unlink_confirms_the_link_is_undone_and_how_to_link_again(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    assert said(ask(bench, "link")) == said(blocks.linked("Ada"))
    answer = said(ask(bench, "unlink"))
    assert bench.owner.slack_person(bench.install.id, ADA_SLACK) is None
    assert "You are unlinked. The bot no longer acts as you here." in answer
    assert "`/cairn link`" in answer
    assert "/integrations/slack/link?t=" not in answer
    assert bench.store._con.execute("SELECT * FROM slack_link_offers").fetchall() == []
    assert said(ask(bench, "unlink")) == answer


def test_after_unlink_the_answer_is_the_one_seam_for_it(bench, monkeypatch):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    asked = []

    async def seam(context, slack_user):
        asked.append((context, slack_user))
        return blocks.refused("seam")

    monkeypatch.setattr(people, "unlinked", seam)
    bench.link(ADA_SLACK, bench.ada, bench.library)
    assert said(ask(bench, "unlink")) == said(blocks.refused("seam"))
    assert asked == [(bench.context, ADA_SLACK)]
    assert bench.owner.slack_person(bench.install.id, ADA_SLACK) is None


def test_a_note_waiting_for_its_page_is_answered_through_the_one_seam_for_it(bench, monkeypatch):
    bench.link(ADA_SLACK, bench.ada, bench.library)

    def moving(*args, **kwargs):
        raise StalePage(expected=0, found=1)

    asked = []

    def seam(**fields):
        asked.append(fields)
        return blocks.refused("seam")

    monkeypatch.setattr(Store, "save_page", moving)
    monkeypatch.setattr(people, "filed_message", seam)
    assert said(ask(bench, "note Row 4 holds the quince jars.")) == said(blocks.refused("seam"))
    assert asked == [{"title": "Row 4 holds the quince jars.", "page_url": None,
                      "project": "library", "waiting": True,
                      "project_url": f"{BASE}/b/library"}]  # fmt: skip
    monkeypatch.undo()
    url = f"{BASE}/b/library/p/row-4-holds-the-quince-jars"
    for flag in (True, False):
        fields = {**asked[0], "waiting": flag, "page_url": None if flag else url}
        assert people.filed_message(**fields) == blocks.filed(
            title=fields["title"], page_url=fields["page_url"], project="library", waiting=flag,
            project_url=f"{BASE}/b/library",
        )  # fmt: skip


def test_waiting_command_keeps_the_note_and_links_its_project(bench, monkeypatch):
    bench.link(ADA_SLACK, bench.ada, bench.library)

    def moving(*args, **kwargs):
        raise StalePage(expected=0, found=1)

    monkeypatch.setattr(Store, "save_page", moving)
    payload = ask(bench, "note Row 4 holds the quince jars.")
    assert len(bench.notes()) == 1
    assert [note.note_id for note in bench.store.waiting_notes()] == [bench.notes()[0][0]]
    assert bench.owner.list_pages(bench.library.id) == []
    for text in (payload["text"], payload["blocks"][0]["text"]["text"]):
        assert "stored" in text.lower() and "appear shortly" in text.lower()
        assert "Filed" not in text
    assert f"<{BASE}/b/library|library>" in said(payload)
    assert "/p/row-4-holds-the-quince-jars" not in said(payload)  # no page there yet


def test_the_command_is_the_installs(bench):
    install = dataclasses.replace(bench.install, command="/shelf")
    bench.context = dataclasses.replace(bench.context, install=install)
    assert said(ask(bench, "help")) == said(blocks.help_text("/shelf"))
    answer = said(ask(bench, "unlink"))
    assert "`/shelf link`" in answer and "/cairn" not in answer


def test_who_asks_shows_ids_in_a_repr_and_nothing_of_their_memory(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    asker = asyncio.run(people.asker(bench.context, ADA_SLACK))
    shown = repr(asker)
    assert ADA_SLACK in shown and "Memory" not in shown and "store" not in shown.lower()


def test_a_typed_text_the_project_held_already_is_not_recorded_as_come_through_slack(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    bench.owner.add_note(bench.library.id, "Row 4 holds the quince jars.", via="web")
    assert said(ask(bench, "note Row 4 holds the quince jars.")).startswith("Filed: ")
    assert len(bench.notes()) == 1
    assert bench.store._con.execute("SELECT COUNT(*) FROM slack_notes").fetchone() == (0,)
