"""Sentences of the guides about notes whose `source_ref` names a mail thread, a meeting, a
calendar, a document or a to-do list (M3: tests/acceptance/test_m3_notes_that_name_a_source.py),
each checked against cairn itself: the forms sources.md's table gives are exactly
cairn.sources.SOURCE_REF_FORMS, the forms privacy.md names are those, the setting they name is
the one `serve` and `arrange` read, and what they say happens to such a page is what the
arranger does. Each check is a function of the text, so test_the_checks_see_a_doctored_guide
can run it on a doctored copy and see it fail. Every model is a FakeModel; fixtures are
synthetic."""

import json
import re
from pathlib import Path

import pytest
from guide_source import guide_source

from cairn import sources
from cairn.arrange import arrange_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import FakeModel, Reply
from cairn.sources import KINDS, SOURCE_REF_FORMS, source_ref_form, withheld_by_ref
from cairn.store import Store
from cairn.web import cli_arrange

TABLE_LEAD = "The recipes below use these forms:"
# How the recipes name what a note came from: the words each place that says which notes are
# kept from a model uses for the recipe forms.
RECIPE_WORDS = "a meeting, a mail thread, a calendar, a document or a to-do list"
# The name a reader knows each withheld kind with a word of its own by.
KIND_SHOWN = {"gmail": "Gmail", "google_meet": "Google Meet", "google_drive": "Google Drive"}
SIX = "".join(f"- Crate {n} sits on shelf {n}.\n" for n in range(6))


def flat(text: str) -> str:
    return " ".join(text.split())


def sentence_with(text: str, words: str) -> str:
    """The one sentence of `text` that holds `words`, read paragraph by paragraph (a table
    is a paragraph of its own)."""
    found = [
        s
        for paragraph in text.split("\n\n")
        for s in re.split(r"(?<=[.;])\s+(?=[A-Z`])", flat(paragraph))
        if words in s
    ]
    assert len(found) == 1, (words, found)
    return found[0]


def recipes_part() -> str:
    """sources.md's "What every recipe shares", where the table of forms is."""
    raw = guide_source("sources")
    return raw.split("## What every recipe shares", 1)[1].split("\n## ", 1)[0]


def models_line() -> str:
    (line,) = [line for line in guide_source("privacy").splitlines() if "**Models.**" in line]
    return line


def table_rows(part: str) -> list[str]:
    """The `source_ref` cell of each row of the table after TABLE_LEAD."""
    after = part.split(TABLE_LEAD, 1)[1].lstrip("\n")
    rows = []
    for line in after.splitlines():
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells[0] in ("Source", "---") or set(cells[0]) <= {"-"}:
            continue
        rows.append(cells[1])
    return rows


# The checks, each a function of the text


def check_the_table_is_the_forms(part: str) -> None:
    """Every row's `source_ref` has a form, and the forms of the rows are SOURCE_REF_FORMS."""
    found = set()
    rows = table_rows(part)
    assert rows, "no table"
    for cell in rows:
        shown = re.findall(r"`([^`]+)`", cell)
        assert shown, cell
        for ref in shown:
            form = source_ref_form(ref)
            assert form is not None, ref
            found.add(form)
    assert found == SOURCE_REF_FORMS, found


def check_names_the_forms(sentence: str) -> None:
    """A sentence that lists the words names each as `word:`, and exactly withheld_forms():
    the recipe forms and the withheld kinds' own words, each of those beside its kind."""
    shown = set(re.findall(r"`([a-z]+):`", sentence))
    assert shown == sources.withheld_forms(), sentence
    check_names_the_kinds_words(sentence)


def check_names_the_kinds_words(sentence: str) -> None:
    """The words a sentence gives beyond the recipe forms are exactly the withheld kinds' own
    (SourceKind.ref_form), and it names the kind each is the word of."""
    shown = set(re.findall(r"`([a-z]+):`", sentence)) - SOURCE_REF_FORMS
    assert shown == sources.withheld_forms() - SOURCE_REF_FORMS, sentence
    for word in shown:
        (kind,) = [k for k, v in KINDS.items() if v.withheld and v.ref_form == word]
        assert KIND_SHOWN[kind] in sentence, (kind, sentence)


def check_says_the_recipe_kinds(sentence: str) -> None:
    """A sentence that says which notes are kept from a model names the notes whose
    `source_ref` says where they came from, in the recipes' words."""
    assert "source_ref" in sentence and RECIPE_WORDS in sentence, sentence


def check_send_is_the_setting(sentence: str) -> None:
    """`<PREFIX>_SOURCE_NOTES=send`, as the sentence writes it, is what sends such a page."""
    (shown,) = re.findall(r"`<PREFIX>_SOURCE_NOTES=(\w+)`", sentence)
    assert shown == "send", sentence
    assert cli_arrange.sends_source_notes("ARRANGER", {"ARRANGER_SOURCE_NOTES": shown}) is True
    assert cli_arrange.sends_source_notes("ARRANGER", {}) is False


def check_the_examples_of_letter_case(sentence: str) -> None:
    """'... `Mail:` counts, `mailbox:` and `email:` do not.' Each example is as said."""
    counts, rest = sentence.split(" counts, ", 1)
    counted = re.findall(r"`([^`]+)`", counts)[-1]
    assert source_ref_form(counted + "x") is not None, counted
    for ref in re.findall(r"`([^`]+)`", rest):
        assert source_ref_form(ref + "x") is None, ref


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def page_of(view, source_ref, *, via="mcp", title="Shelf plan"):
    project = view.get_project("alpha") or view.create_project("alpha")
    note, _ = view.add_note(project.id, f"# {title}\n\n{SIX}", via=via, source_ref=source_ref)
    compile_note(view, note, gate=lint_gate)
    return project, view.get_page(project.id, title.lower().replace(" ", "-"))


def answer(page) -> Reply:
    return Reply(text=json.dumps({"order": list(range(len(page.claims), 0, -1)), "same": []}))


# sources.md: the table of forms, and what a note that carries one is kept from


def test_sources_md_s_table_is_the_forms_the_arranger_reads():
    check_the_table_is_the_forms(recipes_part())


def test_sources_md_says_where_the_table_is_that_such_a_note_is_kept_from_a_model(store):
    """'A page that holds a note whose `source_ref` has one of these forms, or starts the way
    the server's own Gmail, Google Meet and Google Drive sources start theirs (`gmail:`,
    `meet:` and `drive:`), is not sent to a model the operator connects to arrange pages,
    unless the operator sets `<PREFIX>_SOURCE_NOTES=send` and the page's project is not kept
    from every model (the privacy guide says by whom); cairn tidies that page itself instead.'
    The clause about a project kept from every model is checked in
    test_guide_facts_m4_unit."""
    part = recipes_part()
    sentence = sentence_with(part, "is not sent to a model the operator connects")
    assert sentence == (
        "A page that holds a note whose `source_ref` has one of these forms, or starts the way"
        " the server's own Gmail, Google Meet and Google Drive sources start theirs (`gmail:`,"
        " `meet:` and `drive:`), is not sent to a model the operator connects to arrange pages,"
        " unless the operator sets `<PREFIX>_SOURCE_NOTES=send` and the page's project is not"
        " kept from every model ([the privacy guide](privacy.md#where-it-is) says by whom);"
        " cairn tidies that page itself instead."
    )
    check_names_the_kinds_words(sentence.replace("`<PREFIX>_SOURCE_NOTES=send`", ""))
    check_send_is_the_setting(sentence)
    assert flat(part).index(TABLE_LEAD) < flat(part).index(sentence)  # after the table
    for ref in ("mail:thread-1", "gmail:m-1", "Meet:c/t", "drive:f-1"):
        project, page = page_of(store, ref, title=f"Shelf {ref[:4]}")
        model = FakeModel([])
        said = arrange_page(store, project.id, page.slug, model, withhold=sources.withheld_via())
        assert said.status == "withheld" and model.requests == [], ref


def test_sources_md_says_whoever_wrote_it_and_how_the_form_is_read(store):
    """'That holds whoever wrote the note: cairn goes by the form, so write it as the recipe
    gives it. The form is the word before the first `:`, in any letter case and with any white
    space around it: `Mail:` counts, `mailbox:` and `email:` do not.'"""
    text = flat(recipes_part())
    assert (
        "That holds whoever wrote the note: cairn goes by the form, so write it as the recipe"
        " gives it." in text
    )
    words = (
        "The form is the word before the first `:`, in any letter case and with any white"
        " space around it: `Mail:` counts, `mailbox:` and `email:` do not."
    )
    assert words in text
    check_the_examples_of_letter_case(words)
    for via in ("mcp", "api", "web"):
        assert withheld_by_ref(via, "doc:file-1") is True
    assert source_ref_form("mail:a:b") == "mail" and source_ref_form("session:mail:1") is None
    assert source_ref_form(" MAIL :x") == "mail"


def test_sources_md_says_a_source_the_server_reads_is_judged_by_its_source(store):
    """'A note that one of the sources the server reads writes is judged by its source
    instead.' Granola writes `meeting:granola/<id>`: judged by its source, withheld as every
    source the server reads is (Privacy 4), not by the form."""
    text = flat(recipes_part())
    assert (
        "A note that one of the [sources the server reads](#sources-the-server-reads) writes is"
        " judged by its source instead." in text
    )
    assert "## Sources the server reads" in guide_source("sources")
    for kind in KINDS.values():
        assert withheld_by_ref(kind.via, "meeting:granola/n-1") is False
    project, page = page_of(store, "meeting:granola/n-1", via=KINDS["granola"].via)
    model = FakeModel([answer(page)])
    said = arrange_page(store, project.id, page.slug, model, withhold=sources.withheld_via())
    assert said.status == "withheld" and model.requests == []


# privacy.md, the Models line


def test_privacy_md_names_the_forms_whose_notes_are_not_sent(store):
    """'Neither is a page that holds a note a person or an agent wrote whose `source_ref` says
    it came from a meeting, a mail thread, a calendar, a document or a to-do list: `meeting:`,
    `mail:`, `calendar:`, `doc:` or `todo:`, the forms the sources guide gives, or `gmail:`,
    `meet:` or `drive:`, the way the server's own Gmail, Google Meet and Google Drive sources
    start theirs.'"""
    line = models_line()
    sentence = sentence_with(line, "Neither is a page that holds a note a person or an agent")
    check_names_the_forms(sentence)
    check_says_the_recipe_kinds(sentence)
    for via, ref in (
        ("mcp", "todo:list/1"),
        ("api", "gmail:t-1"),
        ("web", "drive:f"),
        (None, "meet:c"),
    ):
        project, page = page_of(store, ref, via=via, title=f"Shelf {via}")
        said = arrange_page(
            store, project.id, page.slug, FakeModel([]), withhold=sources.withheld_via()
        )
        assert said.status == "withheld", via


def test_privacy_md_says_the_one_setting_sends_both():
    """'The operator's one setting, `<PREFIX>_SOURCE_NOTES=send`, sends both, except in a
    project its people keep from every model.' The exception is checked in
    test_guide_facts_m4_unit."""
    sentence = sentence_with(models_line(), "The operator's one setting")
    assert sentence == (
        "The operator's one setting, `<PREFIX>_SOURCE_NOTES=send`, sends both, except in a"
        " project its people keep from every model."
    )
    check_send_is_the_setting(sentence)


def test_privacy_md_says_what_is_still_sent_and_the_limit(store):
    """'Every other page can be sent when a model is connected, the claims of all its notes with
    it, and that includes anything an agent copied from somewhere into a note without such a
    `source_ref`: cairn goes by what the note says, and cannot know where an agent's text came
    from.'"""
    assert (
        "Every other page can be sent when a model is connected, the claims of all its notes with"
        " it, and that includes anything an agent copied from somewhere into a note without"
        " such a `source_ref`: cairn goes by what the note says, and cannot know where an"
        " agent's text came from." in flat(models_line())
    )
    project, page = page_of(store, "session:s-1", title="Copied mail")  # mail, copied
    model = FakeModel([answer(page)])
    said = arrange_page(store, project.id, page.slug, model, withhold=sources.withheld_via())
    assert said.status == "arranged" and page.claims[0].text in model.requests[0][1]["content"]


# Every other place that says which notes a connected model is not sent


def readme() -> str:
    return (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")


def run_section() -> str:
    return guide_source("run").split("## Connect a model", 1)[1].split("\n## ", 1)[0]


def servers_section() -> str:
    return guide_source("sources").split("## Sources the server reads", 1)[1]


def check_says_the_connector_too(sentence: str) -> None:
    assert "connector" in sentence, sentence


def test_the_readme_says_both_kinds_of_note():
    """README: '... or a note whose `source_ref` says it came from a meeting, a mail thread, a
    calendar, a document or a to-do list (the sources guide gives the forms), is not sent to
    the model ...'"""
    sentence = sentence_with(readme(), "_SOURCE_NOTES=send")
    check_says_the_recipe_kinds(sentence)
    check_says_the_connector_too(sentence)
    assert "(the sources guide gives the forms)" in sentence


def test_run_md_says_both_kinds_of_note():
    sentence = sentence_with(run_section(), "is not sent unless the file also says")
    check_says_the_recipe_kinds(sentence)
    check_says_the_connector_too(sentence)
    assert "(the forms the sources guide gives)" in sentence


def test_sources_md_s_own_model_paragraph_says_both_kinds_of_note():
    """sources.md, "Sources the server reads": the recipe's notes, with a link to the forms."""
    sentence = sentence_with(servers_section(), "is not sent to it unless the operator sets")
    check_says_the_recipe_kinds(sentence)
    check_says_the_connector_too(sentence)
    assert "([the forms](#what-every-recipe-shares))" in sentence
    assert "## What every recipe shares" in guide_source("sources")


@pytest.mark.parametrize("command", ["serve", "arrange"])
def test_the_help_says_both_kinds_of_note(capsys, command):
    from cairn import server

    with pytest.raises(SystemExit):
        server.main([command, "--help"])
    said = flat(capsys.readouterr().out)
    sentence = said.split("PREFIX_SOURCE_NOTES is withhold", 1)[1].split(" or send ", 1)[0]
    check_says_the_recipe_kinds(sentence)
    check_says_the_connector_too(sentence)


def test_the_line_serve_prints_says_both_kinds_of_note(store):
    from cairn.arrange import Arranger
    from cairn.web import cli

    args = cli._parser().parse_args(["serve", "--db", "x.db", "--model-env", "ARRANGER"])
    arranger = Arranger(FakeModel([], name="m"), price=None, view_for=store.for_account)
    for send in (False, True):
        line = cli_arrange.serve_says(args, arranger, send)
        check_says_the_recipe_kinds(line)
        check_says_the_connector_too(line)
    check_says_the_recipe_kinds(flat(cli_arrange.serve_says.__doc__ or ""))


def test_the_checks_see_a_doctored_guide():
    """The checks are not vacuous: a row left out, a row of another form, a form left out of
    the list, another setting, or an example that is not as said fail."""
    part = recipes_part()
    with pytest.raises(AssertionError):  # the to-do row left out
        check_the_table_is_the_forms(re.sub(r"\n\| A to-do \|[^\n]*", "", part))
    with pytest.raises(AssertionError):  # a row whose form is not one
        check_the_table_is_the_forms(part.replace("`doc:<file id>`", "`docs:<file id>`"))
    with pytest.raises(AssertionError):  # a row of a form the arranger does not read
        check_the_table_is_the_forms(
            part.replace("| A to-do |", "| A chat | `chat:<id>` |\n| A to-do |")
        )
    sentence = sentence_with(models_line(), "Neither is a page that holds a note")
    with pytest.raises(AssertionError):
        check_names_the_forms(sentence.replace(" or `todo:`", ""))
    with pytest.raises(AssertionError):
        check_names_the_forms(sentence.replace("`doc:`", "`doc:`, `chat:`"))
    with pytest.raises(AssertionError):  # a kind's word left out
        check_names_the_forms(sentence.replace("`gmail:`, ", ""))
    with pytest.raises(AssertionError):  # a kind's word without its kind
        check_names_the_kinds_words(sentence.replace("Google Drive", "Drive"))
    with pytest.raises(AssertionError):
        check_says_the_recipe_kinds(sentence.replace("a document or ", ""))
    setting = "The operator's one setting, `<PREFIX>_SOURCE_NOTES=send`, sends both."
    with pytest.raises(AssertionError):
        check_send_is_the_setting(setting.replace("=send", "=withhold"))
    words = "`Mail:` counts, `mailbox:` and `email:` do not."
    with pytest.raises(AssertionError):
        check_the_examples_of_letter_case(words.replace("`email:`", "`mail:`"))
    with pytest.raises(AssertionError):
        check_the_examples_of_letter_case(words.replace("`Mail:`", "`Email:`"))
