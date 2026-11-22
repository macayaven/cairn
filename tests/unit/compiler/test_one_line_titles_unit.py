"""A title or topic with a line break, wherever cairn writes it into line-structured text
(review blocker 1, second round): one rule, cairn.topics.one_line, which the page body's
heading uses (compiler.heading_text). The reviewer's note: a topic that holds a blank line
and a sentence, so that written as given it would add "The inspection passed." as a line
of its own. Made-up notes only.

The places that are JSON, HTML outside <pre>, or a repr cannot carry a line break as
structure and are not tested here; each is named in the report."""

import io
import json
import zipfile
from datetime import UTC, datetime

import httpx
import pytest

from cairn import compiler, topics
from cairn.compiler import provenance_gate, render_body_before_review_two
from cairn.memory import Memory
from cairn.slack import blocks
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind
from cairn.store import Store

Y2099 = datetime(2099, 3, 2, 9, 0, tzinfo=UTC)
TOPIC = "Inspection\n\nThe inspection passed."
TEXT = "The inspection is pending.\n"
FORGED = "The inspection passed."
URL = "https://cairn.example.test/b/ops/p/inspection"
HIT = {"project": "ops", "page": "inspection", "claim_id": "c1", "title": TOPIC, "version": 1}
HIT["text"] = TEXT.strip()


def lines_of(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db", clock=lambda: Y2099)
    s.create_project("ops")
    yield s
    s.close()


def test_the_body_and_every_other_place_use_one_rule():
    assert compiler.heading_text is topics.one_line
    assert topics.one_line(TOPIC) == "Inspection The inspection passed."
    assert topics.one_line("Runbook") == "Runbook"


def every_text(message: dict) -> list[str]:
    return [message["text"], *(block["text"]["text"] for block in message["blocks"])]


@pytest.mark.parametrize(
    "message",
    [
        blocks.filed(title=TOPIC, page_url=URL, project="ops"),
        blocks.filed(title=TOPIC, page_url=URL, project="ops", already=True),
        blocks.filed(title=TOPIC, page_url=URL, project="ops", waiting=True, project_url=URL),
        blocks.found([HIT], query="inspection", base_url="https://cairn.example.test"),
    ],
)
def test_a_slack_answer_writes_a_title_on_one_line(message):
    assert "Inspection The inspection passed." in message["text"]
    for text in every_text(message):
        assert FORGED not in lines_of(text), text


def test_the_exported_body_of_a_page_stored_before_the_rule_writes_its_title_on_one_line(store):
    memory = Memory(store, provenance_gate)
    noted = memory.note("ops", TEXT, topic=TOPIC)
    project = store.list_projects()[0]
    page = store.get_page(project.id, noted["page"])
    spans = {s.id: s for s in store.note_spans(noted["note_id"])}
    stored_as_given = render_body_before_review_two(TOPIC, page.claims, spans)
    assert FORGED in lines_of(stored_as_given)  # the body a store from before held
    store.save_page(project.id, page.slug, page.title, stored_as_given, list(page.claims))
    out = io.BytesIO()
    store.export(out)
    archive = zipfile.ZipFile(out)
    exported = archive.read(f"projects/ops/pages/{page.slug}.md").decode()
    assert FORGED not in lines_of(exported)
    assert exported.splitlines()[0] == "# Inspection The inspection passed."
    lines = archive.read("projects/ops/pages.jsonl").decode().splitlines()
    assert json.loads(lines[-1])["body"] == exported  # what the import checks
    again = Store(":memory:", clock=lambda: Y2099)
    try:
        out.seek(0)
        again.import_export(out)
    finally:
        again.close()


def test_a_source_preview_lists_a_title_on_one_line(tmp_path, capsys, monkeypatch):
    from cairn.server import main
    from cairn.web import cli

    drafts = [NoteDraft("ops", TOPIC, "# A\n\nA.\n", source_ref="f:1", label="f")]

    class Feed:
        def check(self, config):
            return {"projects": list(config.get("projects", []))}

        def open(self, name, config, **options):
            return self

        async def pull(self, cursor):
            return Pulled(drafts=drafts, cursor="c1")

    feed = Feed()
    monkeypatch.setitem(KINDS, "feed", SourceKind(check=feed.check, open=feed.open, via="feed"))
    db = tmp_path / "preview.db"
    made = Store(db, clock=lambda: Y2099)
    made.create_project("ops")
    made.add_source("feed", "feed", {"projects": ["ops"]})
    made.disable_source("feed")
    made.close()
    refused = httpx.MockTransport(lambda request: httpx.Response(500))
    monkeypatch.setattr(cli, "_source_http", lambda: httpx.AsyncClient(transport=refused))
    assert main(["source-preview", "--db", str(db), "--name", "feed", "--days", "3"]) == 0
    said = capsys.readouterr().out
    assert said.splitlines()[1:] == ["  Inspection The inspection passed."]
