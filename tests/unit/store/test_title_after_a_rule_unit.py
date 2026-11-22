"""An untitled note's title skips a line that is a rule (a thematic break, such as the `---`
that opens a block read as content, not front matter): a rule is never a title."""

import pytest

from cairn.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


@pytest.mark.parametrize(
    "text",
    [
        "---\nThe relay retries twice.\n",
        "---\nProduction rule: never drop it.\n---\n\nMore text.\n",  # a block that is content
        "***\n\nThe relay retries twice.\n",
        " - - -\n\nThe relay retries twice.\n",
        "___\n",
    ],
    ids=["dashes", "content-block", "stars", "spaced", "only-a-rule"],
)
def test_an_untitled_note_s_title_is_not_a_rule(store, text):
    project = store.create_project("ops")
    note, _ = store.add_note(project.id, text)
    assert note.title not in {"---", "***", "- - -", "___"}
    expected = {
        "---\nThe relay retries twice.\n": "The relay retries twice.",
        "---\nProduction rule: never drop it.\n---\n\nMore text.\n": (
            "Production rule: never drop it."
        ),
        "***\n\nThe relay retries twice.\n": "The relay retries twice.",
        " - - -\n\nThe relay retries twice.\n": "The relay retries twice.",
        "___\n": "",
    }[text]
    assert note.title == expected


def test_a_line_of_dashes_in_a_sentence_is_kept(store):
    project = store.create_project("ops")
    note, _ = store.add_note(project.id, "--- the relay retries twice\n")
    assert note.title == "--- the relay retries twice"
