"""Sentences of the guides about a project kept from every model (M4:
tests/acceptance/test_m4_a_project_no_model_reads.py), each checked against cairn itself:
who privacy.md says may change it is who the store lets, what it says a switched project's
pages get is what the arranger does, the kinds it says start at never are the private kinds,
what privacy.md and run.md say an import brings is what an import does, and what privacy.md
says of a call already under way is what the arranger does. The sources
guide's clause points to a heading that is there. Each check is a function of the text, so
test_the_checks_see_a_doctored_guide can run it on a doctored copy and see it fail. Every
model is a FakeModel; fixtures are synthetic."""

import asyncio
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path

import pytest
from guide_source import guide_source

from cairn.arrange import Arranger, arrange_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.kinds import KINDS
from cairn.llm import FakeModel, Reply
from cairn.memory import Memory
from cairn.store import MODELS, Store
from cairn.web.mcp import build_mcp

ISSUER = "https://id.example.test/pool-m4-guide"
SIX = "".join(f"- Crate {n} sits on shelf {n}.\n" for n in range(6))
# The words for the switch, as the project's page writes it.
SWITCH = "**Models**"

WHO = (
    "The person who made the project, an owner, or anyone the project lets decide who reaches"
    " it sets **Models** to never on the project's page, or over the API with a key of their"
    " own that is not limited to some projects or to reading; a key made on the server's"
    " command line can too, and an agent cannot."
)
HOLDS = (
    "Then no page of that project is sent, whatever its notes say and whatever the operator"
    " set: cairn tidies its pages itself, and a page a model put in order before stays as it"
    " is."
)
STARTS = "A project of Personal notes starts at never when it is made."
UNDER_WAY = (
    "A call to a model already under way when **Models** is set to never can still finish at"
    " the model's provider, but what it answers is not saved."
)
EXPORT_SAYS = (
    "An import brings each project's **Models** choice: a project the file says is at never"
    " arrives at never, and a project of Personal notes arrives at never whatever the file says."
)
EXPORT_OLD = (
    "A file exported before cairn carried that choice does not say it, and the import says so:"
    " each of its projects starts at **Models** as a new one of its kind does, so a project of"
    " Personal notes is at never and the others at allowed."
)
RUN_SAYS = (
    "Each arrives at **Models** where the file says, and a project of Personal notes at never"
    " whatever it says."
)
RUN_OLD = (
    "A file exported before cairn carried that choice does not say it, and the import says so:"
    " each then starts at **Models** as a new project of its kind does, a project of Personal"
    " notes at never, the others at allowed."
)


def flat(text: str) -> str:
    return " ".join(text.split())


def sentence_with(text: str, words: str) -> str:
    """The one sentence of `text` that holds `words`, read paragraph by paragraph."""
    found = [
        s
        for paragraph in text.split("\n\n")
        for s in re.split(r"(?<=[.;])\s+(?=[A-Z`])", flat(paragraph))
        if words in s
    ]
    assert len(found) == 1, (words, found)
    return found[0]


def models_line() -> str:
    (line,) = [line for line in guide_source("privacy").splitlines() if "**Models.**" in line]
    return line


def export_part() -> str:
    return guide_source("privacy").split("## Export", 1)[1].split("\n## ", 1)[0]


def run_import_part() -> str:
    raw = guide_source("run")
    return raw[raw.index("To bring an export back") :].split("\n\n**", 1)[0]


def recipes_part() -> str:
    raw = guide_source("sources")
    return raw.split("## What every recipe shares", 1)[1].split("\n## ", 1)[0]


# The checks, each a function of the text


def check_who_may(sentence: str) -> None:
    """The switch by its page's name and its one other value; the three kinds of person the
    store lets change who reaches a project; a key no wider than the store lets change it;
    the command line's key; and no agent."""
    assert SWITCH in sentence and f"to {MODELS[1]}" in sentence, sentence
    for words in (
        "The person who made the project",
        "an owner",
        "anyone the project lets decide who reaches it",
        "not limited to some projects or to reading",
        "a key made on the server's command line can too",
        "an agent cannot",
    ):
        assert words in sentence, words


def check_what_it_holds(sentence: str) -> None:
    """Nothing a note says and nothing the operator set sends a page; the page is tidied; an
    order a model gave before stays."""
    for words in (
        "no page of that project is sent",
        "whatever its notes say",
        "whatever the operator set",
        "tidies its pages itself",
        "a page a model put in order before stays as it is",
    ):
        assert words in sentence, words


def check_names_the_private_kinds(sentence: str) -> None:
    """Every kind that is private by its kind is named by its title, and no other kind is."""
    for kind in KINDS.values():
        assert (kind.title in sentence) is kind.private, kind.id


def check_an_import_starts_as_a_new_project(sentence: str) -> None:
    """After an import, each project is at what a new one of its kind starts at: the private
    kinds, named, at never; the others at allowed."""
    assert SWITCH in sentence and "as a new" in sentence, sentence
    check_names_the_private_kinds(sentence)
    for kind in KINDS.values():
        if kind.private:
            assert re.search(rf"{kind.title} (is )?at {MODELS[1]}", sentence), kind.id
    assert re.search(rf"others? at {MODELS[0]}", sentence), sentence


def check_an_import_brings_the_choice(sentence: str) -> None:
    """An import brings the choice the file says, the private kinds, named, at never whatever
    it says."""
    assert SWITCH in sentence and "the file says" in sentence, sentence
    check_names_the_private_kinds(sentence)
    for kind in KINDS.values():
        if kind.private:
            assert re.search(rf"{kind.title} (arrives )?at {MODELS[1]} whatever", sentence), kind.id


def check_the_link_is_to_the_models_line(sentence: str) -> None:
    """The clause links to the privacy guide's heading under which the Models line is."""
    (anchor,) = re.findall(r"\(privacy\.md#([a-z-]+)\)", sentence)
    raw = guide_source("privacy")
    heading = next(
        line[3:] for line in raw.splitlines() if line.startswith("## ") and _anchor(line) == anchor
    )
    section = raw.split(f"## {heading}", 1)[1].split("\n## ", 1)[0]
    assert "**Models.**" in section, heading
    assert "kept from every model" in sentence, sentence


def _anchor(heading_line: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", heading_line[3:].lower()).replace(" ", "-")


# The store, the arranger and the MCP server the sentences are checked against


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def person(store, name):
    signed = store.sign_in(
        "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER, join="default"
    )
    return store.for_account(signed.account_id, signed.user.id)


def page_in(view, project, *, source_ref=None, title="Shelf plan"):
    note, _ = view.add_note(project.id, f"# {title}\n\n{SIX}", via="mcp", source_ref=source_ref)
    compile_note(view, note, gate=lint_gate)
    return view.get_page(project.id, title.lower().replace(" ", "-"))


def answer(page) -> Reply:
    return Reply(text=json.dumps({"order": list(range(len(page.claims), 0, -1)), "same": []}))


# privacy.md, the Models line


def test_privacy_md_says_who_may_keep_a_project_from_every_model(store):
    sentence = sentence_with(models_line(), "lets decide who reaches it sets")
    assert sentence == WHO
    check_who_may(sentence)
    ada, brook, cy, dee, fay = (person(store, n) for n in ("Ada", "Brook", "Cy", "Dee", "Fay"))
    ada.set_role(brook.user_id, "admin")
    journal = cy.create_project("journal", everyone="none")
    cy.set_project_access(journal.id, people={dee.user_id: "manage", fay.user_id: "write"})
    # The person who made it, an owner, someone the project lets decide; the command line's key.
    for view in (cy, ada, dee, store):
        assert view.set_project_models(journal.id, "never") == "never"
        assert view.set_project_models(journal.id, "allowed") == "allowed"
    # An admin the project does not name, a writer, and a key limited to some projects or to
    # reading, even one of the project's maker.
    for view in (brook, fay, cy.narrow([journal.id]), cy.narrow(None, "read")):
        with pytest.raises((KeyError, PermissionError)):
            view.set_project_models(journal.id, "never")
    assert store.kept_from_models([journal.id]) == frozenset()
    # No tool of the MCP server, the only door an agent has, takes it.
    tools = asyncio.run(build_mcp().list_tools())
    assert tools and all("models" not in t.input_schema.get("properties", {}) for t in tools)
    assert not [t.name for t in tools if "model" in t.name]


def test_privacy_md_says_it_holds_whatever_a_note_says_and_the_operator_set(store):
    assert sentence_with(models_line(), "whatever the operator set") == HOLDS
    check_what_it_holds(HOLDS)
    project = store.create_project("alpha")
    page = page_in(store, project, source_ref="gmail:t-1", title="Mail plan")
    arranged = page_in(store, project, title="Shelf plan")
    model = FakeModel([answer(arranged)])
    assert arrange_page(store, project.id, arranged.slug, model).status == "arranged"
    before = store.get_page(project.id, arranged.slug)
    store.set_project_models(project.id, "never")
    for slug in (page.slug, arranged.slug):  # withhold nothing: `send`
        said = arrange_page(store, project.id, slug, FakeModel([]), withhold=())
        assert said.status == "kept from models", slug
    worker = Arranger(FakeModel([]), price=None, view_for=store.for_account, withhold=())
    for slug in (page.slug, arranged.slug):  # the worker tidies, a model's order aside
        worker.queue(store.account_id, project.id, slug)
    assert [d.status for d in asyncio.run(worker.run_once())] == ["kept from models"] * 2
    assert len(model.requests) == 1
    after = store.get_page(project.id, arranged.slug)
    assert (after.version, after.claims) == (before.version, before.claims)


def test_privacy_md_says_a_call_under_way_may_finish_but_is_not_saved(store):
    assert sentence_with(models_line(), "already under way") == UNDER_WAY
    project = store.create_project("alpha")
    page = page_in(store, project)

    class Switching(FakeModel):
        def complete(self, messages):
            store.set_project_models(project.id, "never")  # while the call is under way
            return super().complete(messages)

    model = Switching([answer(page)])
    said = arrange_page(store, project.id, page.slug, model)
    assert len(model.requests) == 1  # it finished
    assert said.status == "kept from models"  # and what it answered is not saved
    assert store.get_page(project.id, page.slug).version == page.version


def test_privacy_md_says_which_projects_start_at_never(store):
    sentence = sentence_with(models_line(), "starts at never")
    assert sentence == STARTS
    check_names_the_private_kinds(sentence)
    memory = Memory(store, lint_gate)
    for kind in KINDS.values():
        made = memory.new_project(f"p-{kind.id}", kind=kind.id)
        assert made["models"] == (MODELS[1] if kind.private else MODELS[0]), kind.id


def test_privacy_md_says_it_before_every_other_page_and_keeps_that_sentence(store):
    """'Every other page can be sent ...' keeps its truth: it comes after the switch, so it
    is about projects where the switch is off."""
    line = flat(models_line())
    assert line.index(WHO) < line.index(HOLDS) < line.index(UNDER_WAY) < line.index(STARTS)
    assert line.index(STARTS) < line.index("Every other page can be sent when a model")


# What an import brings: privacy.md's Export and run.md


def imported_kept(store, tmp_path, *, said: bool) -> set[str]:
    """Every kind twice, one at each state; exported and imported, with the choice in the file
    or (`said` False) taken out of it as a file exported before carried none. The projects
    that arrive at never, as "kind-state before"."""
    ada = person(store, "Ada")
    for kind in KINDS.values():
        for state in MODELS:
            made = ada.create_project(f"p-{kind.id}-{state}", kind=kind.id)
            ada.set_project_models(made.id, state)
    out = io.BytesIO()
    ada.export(out)
    archive = out.getvalue()
    if not said:
        source, rewritten = zipfile.ZipFile(io.BytesIO(archive)), io.BytesIO()
        with zipfile.ZipFile(rewritten, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "export.json":
                    manifest = json.loads(data)
                    for entry in manifest["projects"]:
                        del entry["models"]
                    data = json.dumps(manifest).encode()
                target.writestr(name, data)
        archive = rewritten.getvalue()
    elsewhere = Store(tmp_path / f"elsewhere-{said}.db")
    try:
        summary = elsewhere.import_export(io.BytesIO(archive))
        assert ("does not say" in summary.notice) is not said
        projects = elsewhere.list_projects()
        assert len(projects) == 2 * len(KINDS)
        kept = elsewhere.kept_from_models([p.id for p in projects])
        return {p.name.removeprefix("p-") for p in projects if p.id in kept}
    finally:
        elsewhere.close()


def check_import_brings_the_choice(tmp_path):
    """With the choice in the file, each project arrives where it was, a private kind at never
    whatever it was; without it, each where a new one of its kind starts."""
    private = [kind.id for kind in KINDS.values() if kind.private]
    for said, expected in (
        (
            True,
            {f"{k.id}-{MODELS[1]}" for k in KINDS.values()} | {f"{k}-{MODELS[0]}" for k in private},
        ),
        (False, {f"{k}-{state}" for k in private for state in MODELS}),
    ):
        store = Store(tmp_path / f"source-{said}.db")
        try:
            assert imported_kept(store, tmp_path, said=said) == expected, said
        finally:
            store.close()


def test_privacy_md_says_an_import_brings_the_models_choice(tmp_path):
    sentence = sentence_with(export_part(), "An import brings each project's")
    assert sentence == EXPORT_SAYS
    check_an_import_brings_the_choice(sentence)
    old = sentence_with(export_part(), "does not say it")
    assert old == EXPORT_OLD
    check_an_import_starts_as_a_new_project(old)
    check_import_brings_the_choice(tmp_path)


def test_run_md_says_an_imported_project_arrives_where_the_file_says(tmp_path):
    sentence = sentence_with(run_import_part(), "where the file says")
    assert sentence == RUN_SAYS
    check_an_import_brings_the_choice(sentence)
    old = sentence_with(run_import_part(), "as a new project of its kind")
    assert old == RUN_OLD
    check_an_import_starts_as_a_new_project(old)
    check_import_brings_the_choice(tmp_path)


# sources.md: the clause below the table of forms


def test_sources_md_points_from_the_table_to_the_switch():
    sentence = sentence_with(recipes_part(), "is not sent to a model the operator connects")
    check_the_link_is_to_the_models_line(sentence)


# Every place that gives the operator's setting: `send` sends those notes, except in a
# project its people keep from every model


EXCEPT = "except in a project its people keep from every model"
SENDS_BOTH = (
    "The operator's one setting, `<PREFIX>_SOURCE_NOTES=send`, sends both, except in a project"
    " its people keep from every model."
)
STORES = (
    "- **Project sets and workspace settings**: each set's name and projects, the note guard's"
    " policy, and which projects are at **Models** never."
)


def check_says_the_exception(text: str) -> None:
    assert EXCEPT in flat(text), text


def check_send_keeps_a_kept_project(store) -> None:
    """With `send` (withhold nothing), a page holding a Gmail note or a `mail:` note is sent
    from a project at allowed and not from one at never."""
    for name, never in (("open", False), ("shut", True)):
        project = store.create_project(name)
        if never:
            store.set_project_models(project.id, "never")
        for title, via, ref in (("Mail plan", "gmail", None), ("Thread plan", "mcp", "mail:t-1")):
            note, _ = store.add_note(project.id, f"# {title}\n\n{SIX}", via=via, source_ref=ref)
            compile_note(store, note, gate=lint_gate)
            page = store.get_page(project.id, title.lower().replace(" ", "-"))
            model = FakeModel([] if never else [answer(page)])
            said = arrange_page(store, project.id, page.slug, model, withhold=())
            assert said.status == ("kept from models" if never else "arranged"), (name, title)


def readme() -> str:
    return (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")


def run_model_section() -> str:
    return guide_source("run").split("## Connect a model", 1)[1].split("\n## ", 1)[0]


def servers_section() -> str:
    return guide_source("sources").split("## Sources the server reads", 1)[1]


def help_of(capsys, command: str) -> str:
    from cairn import server

    with pytest.raises(SystemExit):
        server.main([command, "--help"])
    said = flat(capsys.readouterr().out)
    return said.split("PREFIX_SOURCE_NOTES is withhold", 1)[1].split(";", 1)[0]


def test_the_readme_says_send_keeps_a_kept_project_out(store):
    check_says_the_exception(sentence_with(readme(), "_SOURCE_NOTES=send"))
    check_send_keeps_a_kept_project(store)


def test_run_md_says_send_keeps_a_kept_project_out(store):
    check_says_the_exception(sentence_with(run_model_section(), "is not sent unless the file"))
    check_send_keeps_a_kept_project(store)


def test_sources_md_s_own_model_paragraph_says_send_keeps_a_kept_project_out(store):
    sentence = sentence_with(servers_section(), "is not sent to it unless the operator sets")
    check_says_the_exception(sentence)
    check_send_keeps_a_kept_project(store)


@pytest.mark.parametrize("command", ["serve", "arrange"])
def test_the_help_says_send_keeps_a_kept_project_out(capsys, command, store):
    said = help_of(capsys, command)
    assert said.index(" or send ") < said.index(EXCEPT), said  # it is what `send` does
    check_send_keeps_a_kept_project(store)


def test_the_line_serve_prints_says_send_keeps_a_kept_project_out(store):
    from cairn.web import cli, cli_arrange

    args = cli._parser().parse_args(["serve", "--db", "x.db", "--model-env", "ARRANGER"])
    arranger = Arranger(FakeModel([], name="m"), price=None, view_for=store.for_account)
    check_says_the_exception(cli_arrange.serve_says(args, arranger, True) or "")
    assert EXCEPT not in (cli_arrange.serve_says(args, arranger, False) or "")  # sends nothing
    check_says_the_exception(cli_arrange.serve_says.__doc__ or "")
    check_send_keeps_a_kept_project(store)


def test_privacy_md_says_the_one_setting_sends_both_but_not_from_a_kept_project(store):
    assert sentence_with(models_line(), "The operator's one setting") == SENDS_BOTH
    check_says_the_exception(SENDS_BOTH)
    check_send_keeps_a_kept_project(store)


def test_privacy_md_says_the_switch_is_stored_beside_the_guard_s_policy(store, tmp_path):
    (line,) = [
        line
        for line in guide_source("privacy").splitlines()
        if line.startswith("- **Project sets and workspace settings**")
    ]
    assert line == STORES
    assert "the note guard's policy" in line and f"{SWITCH} {MODELS[1]}" in line
    project = store.create_project("plans")
    store.set_project_models(project.id, "never")
    con = sqlite3.connect(tmp_path / "cairn.db")
    try:
        names = [name for (name,) in con.execute("SELECT name FROM settings")]
    finally:
        con.close()
    assert [n for n in names if project.id in n] == [f"project.models:{project.id}"]


def test_the_checks_see_a_doctored_guide():
    """The checks are not vacuous: a person left out, the agent let in, a holding left out,
    another kind, another state or another heading fail."""
    with pytest.raises(AssertionError):
        check_says_the_exception(SENDS_BOTH.replace(", except in a project", ", in a project"))
    with pytest.raises(AssertionError):
        check_who_may(WHO.replace(", an owner", ""))
    with pytest.raises(AssertionError):
        check_who_may(WHO.replace("an agent cannot", "an agent can too"))
    with pytest.raises(AssertionError):
        check_who_may(WHO.replace("to never", "to off"))
    with pytest.raises(AssertionError):
        check_what_it_holds(HOLDS.replace(" and whatever the operator set", ""))
    with pytest.raises(AssertionError):
        check_names_the_private_kinds(STARTS.replace("Personal notes", "Meetings"))
    with pytest.raises(AssertionError):
        check_names_the_private_kinds(STARTS.replace("Personal notes", "Personal notes or General"))
    with pytest.raises(AssertionError):
        check_an_import_starts_as_a_new_project(EXPORT_OLD.replace("is at never", "is at allowed"))
    with pytest.raises(AssertionError):
        check_an_import_starts_as_a_new_project(RUN_OLD.replace("others at allowed", "others"))
    with pytest.raises(AssertionError):
        check_an_import_brings_the_choice(EXPORT_SAYS.replace(" whatever the file says", ""))
    with pytest.raises(AssertionError):
        check_an_import_brings_the_choice(RUN_SAYS.replace(" whatever it says", ""))
    sentence = sentence_with(recipes_part(), "is not sent to a model the operator connects")
    with pytest.raises(StopIteration):  # a heading that is not there
        check_the_link_is_to_the_models_line(sentence.replace("#where-it-is", "#nowhere"))
    with pytest.raises(AssertionError):
        check_the_link_is_to_the_models_line(sentence.replace("#where-it-is", "#export"))
