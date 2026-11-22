"""Unit tests for the forms of `source_ref` that keep a note from a model (M3:
tests/acceptance/test_m3_notes_that_name_a_source.py): cairn.sources.SOURCE_REF_FORMS,
source_ref_form and withheld_by_ref. Fixtures are synthetic."""

import pytest

from cairn import sources
from cairn.sources import KINDS, SOURCE_REF_FORMS, SourceKind, source_ref_form, withheld_by_ref


def test_the_forms_are_the_five_the_recipes_give():
    assert frozenset({"meeting", "mail", "calendar", "doc", "todo"}) == SOURCE_REF_FORMS
    assert isinstance(SOURCE_REF_FORMS, frozenset)


@pytest.mark.parametrize(
    ("source_ref", "form"),
    [
        ("meeting:m-1", "meeting"),
        ("mail:thread-1", "mail"),
        ("calendar:ev-1", "calendar"),
        ("calendar:week/2099-11-09", "calendar"),
        ("doc:file-1", "doc"),
        ("todo:errands/item-1", "todo"),
        ("mail:", "mail"),  # a form with nothing after it is still the form
        ("mail:a:b", "mail"),  # the first colon counts
        ("Mail:x", "mail"),
        ("MAIL:x", "mail"),
        ("MeEtInG:x", "meeting"),
        (" mail:x", "mail"),
        ("mail :x", "mail"),
        ("\t\nTODO \t:x", "todo"),
        ("\N{NO-BREAK SPACE}doc:x", "doc"),  # a no-break space is white space too
    ],
)
def test_a_form_is_the_word_before_the_first_colon_whatever_its_case_and_edges(source_ref, form):
    assert source_ref_form(source_ref) == form


@pytest.mark.parametrize(
    "source_ref",
    [
        None,
        "",
        "mail",  # no colon: no form
        " mail ",
        "mailbox:1",
        "docs:1",
        "email:1",
        "gmail:1",  # Gmail's own word: not a recipe form (withheld_forms has it)
        "m ail:1",  # white space inside the word makes another word
        "session:mail:1",
        "https://mail.example.test/1",
        ":mail",
        "meet:1",
        "drive:1",
    ],
)
def test_anything_else_is_no_form(source_ref):
    assert source_ref_form(source_ref) is None


def test_a_note_an_agent_or_a_person_wrote_is_withheld_by_its_form():
    for via in ("mcp", "api", "web", None, "anything-else"):
        assert withheld_by_ref(via, "mail:thread-1") is True, via
        assert withheld_by_ref(via, "session:1") is False, via
        assert withheld_by_ref(via, None) is False, via


def test_a_note_one_of_the_server_s_own_kinds_wrote_is_judged_by_its_kind_alone():
    """Granola's notes carry `meeting:granola/<id>`, a form; they are judged by the kind (M2),
    not by the form: withheld by its `via`, as every kind is (Privacy 4)."""
    assert KINDS["granola"].via == "granola" and KINDS["granola"].withheld is True
    assert withheld_by_ref("granola", "meeting:granola/n-1") is False
    for kind in KINDS.values():
        assert withheld_by_ref(kind.via, "mail:thread-1") is False, kind.via


def test_the_kinds_are_read_when_called(monkeypatch):
    assert withheld_by_ref("u-kind", "doc:1") is True
    monkeypatch.setitem(KINDS, "unit_kind", SourceKind(check=dict, open=dict, via="u-kind"))
    assert withheld_by_ref("u-kind", "doc:1") is False


def test_the_names_are_exported():
    names = ("SOURCE_REF_FORMS", "source_ref_form", "source_ref_word", "withheld_by_ref")
    for name in (*names, "withheld_forms"):
        assert name in sources.__all__


# A withheld kind's own word (round 2): the word before the first ":" of every source_ref its
# notes carry, kept from a model when a person or an agent writes it too.

OWN = {
    "gmail": "gmail",
    "google_calendar": "calendar",
    "google_meet": "meet",
    "google_drive": "drive",
}


def test_a_kind_says_no_word_unless_it_says_one():
    assert SourceKind(check=dict, open=dict, via="plain").ref_form is None
    assert SourceKind(check=dict, open=dict, via="w", ref_form="word").ref_form == "word"


def test_the_google_kinds_say_their_word_and_no_other_kind_says_one():
    said = {name: kind.ref_form for name, kind in KINDS.items() if kind.ref_form is not None}
    assert said == OWN
    for word in said.values():  # a word as source_ref_word reads one: no colon, case or edges
        assert word and word == word.strip().casefold() and ":" not in word


def test_the_withheld_words_are_the_recipe_forms_and_the_withheld_kinds_words():
    assert sources.withheld_forms() == SOURCE_REF_FORMS | {"gmail", "meet", "drive"}
    assert isinstance(sources.withheld_forms(), frozenset)


def test_the_withheld_words_are_read_when_called_and_a_kind_not_withheld_adds_none(monkeypatch):
    before = sources.withheld_forms()
    opened = SourceKind(check=dict, open=dict, via="u-open", ref_form="uopen")
    monkeypatch.setitem(KINDS, "unit_open", opened)
    assert sources.withheld_forms() == before
    held = SourceKind(check=dict, open=dict, via="u-held", withheld=True, ref_form="uheld")
    monkeypatch.setitem(KINDS, "unit_held", held)
    assert sources.withheld_forms() == before | {"uheld"}
    assert withheld_by_ref("mcp", "UHeld:1") is True
    assert withheld_by_ref("mcp", "uopen:1") is False


@pytest.mark.parametrize("ref", ["gmail:t-1", "Gmail:t-1", " MEET :c/t", "drive:f-1", "calendar:e"])
def test_a_note_an_agent_wrote_the_way_a_withheld_source_writes_is_withheld(ref):
    for via in ("mcp", "api", "web", None):
        assert withheld_by_ref(via, ref) is True, (via, ref)
    for kind in KINDS.values():  # a kind's own note is judged by the kind alone, as before
        assert withheld_by_ref(kind.via, ref) is False


@pytest.mark.parametrize("ref", ["gmails:1", "meetup:1", "drives:1", "googledrive:1", "gmail"])
def test_a_word_like_a_kind_s_is_not_one(ref):
    assert withheld_by_ref("mcp", ref) is False


@pytest.mark.parametrize(
    ("source_ref", "word"),
    [("gmail:1", "gmail"), (" Meet :x", "meet"), ("a:b:c", "a"), ("mail", None), (None, None)],
)
def test_source_ref_word_is_the_word_before_the_first_colon(source_ref, word):
    assert sources.source_ref_word(source_ref) == word


# A near miss (R1.5): a source written almost as a form, caught on the web form only


@pytest.mark.parametrize(
    ("source_ref", "form"),
    [
        ("mail:x", None),  # withheld as written: no near miss
        (" MAIL :x", None),  # withheld as written too
        ("ma\N{ZERO WIDTH SPACE}il:x", "mail"),
        ("\N{ZERO WIDTH NO-BREAK SPACE}meeting:x", "meeting"),
        ("email:x", "mail"),
        ("e-mail:x", "mail"),
        ("E-Mail:x", "mail"),
        ("mail", "mail"),
        ("mails", "mail"),
        ("emails:x", "mail"),
        ("meetings:x", "meeting"),
        ("docs", "doc"),
        ("todos:x", "todo"),
        ("Calendar", "calendar"),
        ("https://example.test/mail", None),
        ("session:abc", None),
        ("mailbox:x", None),
        ("doctor:x", None),
        ("gmail:x", None),  # a withheld kind's own word: withheld as written
        ("gmails:x", "gmail"),  # the kinds' own words are near too (R1.6)
        ("e-drive", "drive"),
        ("email :x", "mail"),  # blanks before the colon, as source_ref_word drops them
        ("meeting with Bo", None),  # a bare word counts only before ":" or alone (R1.6)
        ("todo list", None),
        ("docs/x.md", None),
        ("", None),
        (None, None),
    ],
)
def test_a_near_miss_names_the_form_it_is_close_to(source_ref, form):
    assert sources.near_miss(source_ref) == form


@pytest.mark.parametrize(
    ("source_ref", "said"),
    [
        ("mail:x", "withheld"),
        (" MAIL :x", "withheld"),
        ("ma\N{ZERO WIDTH SPACE}il:x", "near miss"),
        ("email:x", "near miss"),
        ("mail", "near miss"),
        ("https://example.test/mail", "neither"),
        ("session:abc", "neither"),
        ("meeting with Bo", "neither"),
        ("todo list", "neither"),
        ("docs/x.md", "neither"),
    ],
)
def test_the_reviews_examples(source_ref, said):
    withheld, near = withheld_by_ref("web", source_ref), sources.near_miss(source_ref)
    assert {"withheld": (True, None), "neither": (False, None)}.get(said, (False, "mail")) == (
        withheld,
        near,
    )
