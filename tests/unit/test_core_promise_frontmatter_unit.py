"""A rule line is not front matter (review 7.4).

Text between a `---` on a note's first line and the next `---` line is front matter only when
it reads as metadata: every line a key and its value (a key is one word, or quoted), a line
that continues the value above it (indented, or a list item under a key), a comment, or blank.
Otherwise the first `---` is a thematic break and every line is content. Made-up notes."""

import pytest

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import chunk
from cairn.store import Store

RULES = "---\nProduction rule: never drop users table.\n---\nFooter note."


def kinds(text: str) -> list[tuple[str, int, int]]:
    return [(s.kind, s.start_line, s.end_line) for s in chunk("doc", text)]


@pytest.fixture
def memory(tmp_path):
    store = Store(tmp_path / "cairn.db")
    store.create_project("alpha")
    yield Memory(store, lint_gate)
    store.close()


# The reviewer's probes, on the repository's fixtures


def test_the_reviewers_note_keeps_its_rule_line_and_search_finds_it(memory):
    answer = memory.note("alpha", RULES, title="Rules")
    assert answer["status"] == "saved"
    body = memory.page("alpha", answer["page"])["body"]
    assert "Production rule: never drop users table." in body and "Footer note." in body
    hits = memory.recall("drop", "alpha")["hits"]
    assert len(hits) == 1 and hits[0]["page"] == answer["page"]


def test_a_note_of_dashes_around_prose_makes_a_page(memory):
    answer = memory.note("alpha", "---\nOnly content inside dashes.\n---", title="OnlyDashes")
    assert answer["status"] == "saved"
    assert "Only content inside dashes." in memory.page("alpha", answer["page"])["body"]


# What reads as metadata


def test_prose_between_two_rules_is_content_every_line():
    assert "frontmatter" not in [kind for kind, _, _ in kinds(RULES)]
    lines = {n for _, start, end in kinds(RULES) for n in range(start, end + 1)}
    assert lines == {1, 2, 3, 4}


@pytest.mark.parametrize(
    "inside",
    [
        "title: x",
        "title: x\ndate: 2026-03-14\ntags: [runbook, cli]",
        "title: x\n\n# a yaml comment",
        "title: x\ntags:\n  - one\n  - two",
        "tags:\n- one\n- two",
        "summary: >\n  folded text\n  over lines",
        "\"quoted key\": 1\n'other': 2",
        "title: [unclosed",  # metadata that is not YAML: lint still says so
        "key_with-dots.and_more: v",
        "empty:",
        "",  # nothing between the two lines: no content either way
        "\n",  # blank lines only: no content either
    ],
)
def test_metadata_is_front_matter(inside):
    text = f"---\n{inside}\n---\nbody\n" if inside else "---\n---\nbody\n"
    assert kinds(text)[0][0] == "frontmatter"
    assert kinds(text)[-1][0] == "paragraph"


@pytest.mark.parametrize(
    "inside",
    [
        "Production rule: never drop users table.",
        "Only content inside dashes.",
        "- a list",  # a list with no key is not a mapping
        "title: x\nThen a sentence of prose.",
        "  indented: before any key",
        "A sentence: with a colon, and words after it.",
        "url:https://example.test",  # no space after the colon: not a key
        "# Deploy",  # a heading, with no key: not a mapping
        "# a comment\n# and another",  # comments alone, with no key
    ],
)
def test_anything_else_is_content(inside):
    text = f"---\n{inside}\n---\nbody\n"
    assert "frontmatter" not in [kind for kind, _, _ in kinds(text)]
    lines = {n for _, start, end in kinds(text) for n in range(start, end + 1)}
    assert lines == set(range(1, len(text.splitlines()) + 1))


def test_the_rules_limit_a_one_word_label_reads_as_a_key():
    """The shape cannot tell "Note: ..." from "title: ...": a one-word label and its sentence
    between two rule lines read as metadata, as they did before (the rule's stated limit)."""
    text = "---\nNote: never drop the users table.\n---\nFooter.\n"
    assert kinds(text)[0][0] == "frontmatter"


# What was stored before keeps its citations


def test_the_lines_after_the_block_keep_the_spans_they_had():
    """The block between the two lines is one passage of its own, so every span after it is
    the span it was when the block was front matter: same lines, same id."""
    before = chunk("doc", "---\ntitle: x\n---\nFooter note.\n\nMore.")
    after = chunk(
        "doc", "---\nProduction rule: never drop users table.\n---\nFooter note.\n\nMore."
    )
    assert [(s.kind, s.start_line, s.end_line) for s in after] == [
        ("paragraph", 1, 3),
        ("paragraph", 4, 4),
        ("paragraph", 6, 6),
    ]
    assert [s.id for s in after[1:]] == [s.id for s in before[1:]]


def test_an_archive_written_when_the_block_was_front_matter_imports(tmp_path, monkeypatch):
    import io

    from cairn import spans

    with monkeypatch.context() as before:  # the rule as it was: any block is front matter
        before.setattr(spans, "_metadata", lambda lines: True)
        source = Store(tmp_path / "before.db")
        source.create_project("alpha")
        answer = Memory(source, lint_gate).note("alpha", RULES, title="Rules")
        assert answer["status"] == "saved"
        archive = io.BytesIO()
        source.export(archive)
        source.close()
    archive.seek(0)
    target = Store(tmp_path / "after.db")
    try:
        target.import_export(archive)
        page = target.get_page(target.get_project("alpha").id, answer["page"])
        assert [claim.text for claim in page.claims] == ["Footer note."]
    finally:
        target.close()
