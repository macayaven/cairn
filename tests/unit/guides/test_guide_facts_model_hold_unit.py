"""Sentences of the guides about what a connected model is sent since review finding Privacy 4,
each checked against cairn itself: every source's notes are held by default (and Slack's), the
two settings that send them, the opt-in setting, and that the assistants people connect are
outside all three. The model is a fake; fixtures are synthetic."""

import asyncio
import json
import re

import pytest

from cairn import sources
from cairn.arrange import Arranger, arrange_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import FakeModel
from cairn.sources import KINDS
from cairn.store import Store
from cairn.web import cli, cli_arrange
from support.guide_source import guide_source

FIRST = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)
ALL = (
    "(Langfuse, Granola, Google Meet, Google Calendar, Gmail, Google Drive, Linear, GitHub or a"
    " connector of your own)"
)


def flat(text: str) -> str:
    return " ".join(text.split())


def models_line() -> str:
    (line,) = [line for line in guide_source("privacy").splitlines() if "**Models.**" in line]
    return flat(line)


def run_section() -> str:
    text = guide_source("run").split("## Connect a model", 1)[1].split("\n## ", 1)[0]
    return flat(text)


def sources_section() -> str:
    return flat(guide_source("sources").split("## Sources the server reads", 1)[1])


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def page(view, project: str, via: str | None = None) -> str:
    made = view.create_project(project)
    for text in (FIRST, SECOND):
        note, _ = view.add_note(made.id, text, via=via)
        compile_note(view, note, gate=lint_gate)
    return made.id


def test_each_guide_names_every_source_and_none_says_one_is_sent():
    """'... a note any source the server reads brought (...)', in all three guides; no
    sentence says Granola's notes, or any source's, are sent."""
    short = ALL.replace("Google ", "")  # the sources guide gives the short names
    assert ALL in models_line() and ALL in run_section() and short in sources_section()
    names = re.findall(r"[A-Z][A-Za-z]+(?: [A-Z][a-z]+)?", ALL)
    assert len(names) == len(KINDS) - 1  # the connector is "a connector of your own"
    assert set(KINDS) == {n for n, k in KINDS.items() if k.withheld}


def test_privacy_says_slack_is_held_too_and_the_two_settings_that_send(store):
    line = models_line()
    assert "or a note that came through Slack, is not sent unless the operator allows it" in line
    assert "`<PREFIX>_SOURCE_NOTES=send`" in line and "`<PREFIX>_SLACK_NOTES=send`" in line
    granola, slack = page(store, "g", KINDS["granola"].via), page(store, "s", "slack")
    for project_id, withhold, held, sent in (
        (granola, sources.withheld_via(), sources.HELD_VIA, False),
        (granola, (), sources.HELD_VIA, True),  # SOURCE_NOTES=send
        (slack, (), sources.HELD_VIA, False),  # SOURCE_NOTES=send leaves Slack held
        (slack, sources.withheld_via(), (), True),  # SLACK_NOTES=send
    ):
        page_now = store.get_page(project_id, "drift-sync")
        order = list(range(len(page_now.claims), 0, -1))
        model = FakeModel([json.dumps({"order": order, "same": []})])
        said = arrange_page(
            store, project_id, "drift-sync", model, withhold=withhold, held_via=held
        )
        assert (said.status != "withheld") is sent, (project_id, withhold, held)


OPT_IN_PRIVACY = (
    "The operator can also make projects opt in: with `<PREFIX>_UNDECIDED_PROJECTS=withhold`, a"
    " page is sent only when its project's people set **Models** to allowed themselves, and a"
    " project nobody decided for is not sent."
)
OPT_IN_RUN = (
    "To make projects opt in, the file can also say `ARRANGER_UNDECIDED_PROJECTS=withhold`: then"
    " a page is sent only when its project's people set **Models** to allowed themselves, and a"
    " page of a project nobody decided for is tidied without the model."
    " `ARRANGER_UNDECIDED_PROJECTS` is `send`, the default (also when it is unset or blank), or"
    " `withhold`, written in lower case; any other value stops cairn at start."
)


def test_the_guides_say_the_opt_in_setting_as_cairn_reads_it(store):
    assert OPT_IN_PRIVACY in models_line() and OPT_IN_RUN in run_section()
    prefix = "ARRANGER"
    name = f"{prefix}_UNDECIDED_PROJECTS"
    for value, opts in ((None, False), ("", False), ("send", False), ("withhold", True)):
        environ = {} if value is None else {name: value}
        assert cli_arrange.opts_in(prefix, environ) is opts
    with pytest.raises(ValueError, match=name):
        cli_arrange.opts_in(prefix, {name: "WITHHOLD"})
    undecided = page(store, "undecided")
    arranger = Arranger(FakeModel([]), price=None, view_for=store.for_account, opt_in=True)
    arranger.queue(store.account_id, undecided, "drift-sync")
    (done,) = asyncio.run(arranger.run_once())
    assert done.status == "undecided"
    assert store.get_page(undecided, "drift-sync").metrics["arranged_by"] == "cairn"
    # The other half: a project whose people chose "allowed" is sent (the store says so in
    # page_for_model's third item).
    chosen = page(store, "chosen")
    store.set_project_models(chosen, "allowed")
    claims = len(store.get_page(chosen, "drift-sync").claims)
    model = FakeModel([json.dumps({"order": list(range(claims, 0, -1)), "same": []})])
    said = arrange_page(store, chosen, "drift-sync", model, opt_in=True)
    assert said.status == "arranged" and len(model.requests) == 1


def test_run_md_any_other_opt_in_value_stops_cairn_at_start(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ARRANGER_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("ARRANGER_MODEL", "m")
    monkeypatch.setenv("ARRANGER_UNDECIDED_PROJECTS", "sometimes")
    path = tmp_path / "cairn.db"
    assert cli.main(["serve", "--db", str(path), "--model-env", "ARRANGER"]) == 2
    assert "ARRANGER_UNDECIDED_PROJECTS" in capsys.readouterr().err and not path.exists()


def test_the_guides_say_connected_assistants_are_outside_all_three():
    assert (
        "These three settings and **Models** govern only cairn's own model that arranges pages:"
        " an assistant a person connects reads what that person reaches and sends it to its own"
        " provider, whatever they are set to."
    ) in models_line()
    assert (
        "These settings and **Models** cover only the model you connect here: an assistant a"
        " person connects reads what that person reaches and sends it to its own provider."
    ) in run_section()
    assert (
        "that is the agent's doing, and no setting of the operator's and no project's **Models**"
        " changes it."
    ) in sources_section()
    # The arranger is the one place a model is called: an agent's reads go through no hold.
    for name in ("SOURCE_NOTES", "SLACK_NOTES", "UNDECIDED_PROJECTS"):
        assert hasattr(cli_arrange, name)
