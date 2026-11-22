"""Sentences of the guides and the README about pages that hold notes read from Google sources
(M2: tests/acceptance/test_m2_withheld_notes.py), each checked against cairn itself: the
sources they name are exactly the withheld kinds, the setting they name is the one `serve` and
`arrange` read, and what they say happens to such a page is what the worker does. Each check
is a function of the text, so test_the_checks_see_a_doctored_guide can run it on a doctored
copy and see it fail. Every model is a FakeModel; fixtures are synthetic."""

import asyncio
import dataclasses
import re

import pytest

from cairn import sources
from cairn.arrange import TIDY, Arranger, messages, tidy_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import FakeModel
from cairn.sources import KINDS
from cairn.store import Limits, Store
from cairn.web import cli, cli_arrange
from support.guide_source import guide_source
from support.paths import REPO_ROOT

ROOT = REPO_ROOT
# The names a reader knows a Google kind by, long and short.
SHOWN = {
    "Google Meet": "google_meet",
    "Meet": "google_meet",
    "Google Calendar": "google_calendar",
    "Calendar": "google_calendar",
    "Gmail": "gmail",
    "Google Drive": "google_drive",
    "Drive": "google_drive",
    "a connector of your own": "connector",  # C9: withheld, not Google's
    # Privacy 4: every kind the server reads is withheld, so the sentences name these too.
    "Langfuse": "langfuse",
    "Granola": "granola",
    "Linear": "linear",
    "GitHub": "github",
}
NOTES = ("google-meet-api.md", "google-calendar-api.md", "google-drive-api.md", "gmail-api.md")
FIRST = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)


def guide(name: str) -> str:
    return guide_source(name.removesuffix(".md"))


def flat(text: str) -> str:
    return " ".join(text.split())


def run_section() -> str:
    return guide("run.md").split("## Connect a model", 1)[1].split("\n## ", 1)[0]


def sources_section() -> str:
    return guide("sources.md").split("## Sources the server reads", 1)[1]


def models_line() -> str:
    (line,) = [line for line in guide("privacy.md").splitlines() if "**Models.**" in line]
    return line


def readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def sentence_with(text: str, words: str) -> str:
    """The one sentence of `text` that holds `words`."""
    found = [s for s in re.split(r"(?<=[.;])\s+", flat(text)) if words in s]
    assert len(found) == 1, (words, found)
    return found[0]


def named_kinds(sentence: str) -> set[str]:
    """The Google kinds a sentence names, by the names a reader knows them by."""
    names = sorted(SHOWN, key=len, reverse=True)
    found, rest = set(), sentence
    for name in names:
        if name in rest:
            found.add(SHOWN[name])
            rest = rest.replace(name, "")
    return found


def withheld_kinds() -> set[str]:
    return {name for name, kind in KINDS.items() if kind.withheld}


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def mail_page(view, project="alpha"):
    """A page of two notes read from Gmail, six claims, one line twice."""
    found = view.get_project(project) or view.create_project(project)
    for text in (FIRST, SECOND):
        note, _ = view.add_note(found.id, text, via=KINDS["gmail"].via)
        compile_note(view, note, gate=lint_gate)
    return found, view.get_page(found.id, "drift-sync")


# The checks, each a function of the text


def check_names_every_withheld_kind(sentence: str) -> None:
    """A sentence that lists the sources whose pages are not sent names exactly the kinds
    that are withheld."""
    assert named_kinds(sentence) == withheld_kinds(), sentence


def check_send_is_the_setting(sentence: str, prefix: str) -> None:
    """`<prefix>_SOURCE_NOTES=send`, as the sentence writes it, is what sends such a page."""
    (shown,) = re.findall(r"`([A-Z_<>]+)_SOURCE_NOTES=(\w+)`", sentence)
    assert shown[0] == prefix and shown[1] == "send", sentence
    name = f"{prefix}_SOURCE_NOTES"
    assert cli_arrange.sends_source_notes(prefix, {name: shown[1]}) is True
    assert cli_arrange.sends_source_notes(prefix, {}) is False


def check_the_rule_s_words(text: str) -> None:
    """'`withhold`, the default (also when it is unset or blank), or `send`, written in lower
    case; any other value stops cairn at start.'"""
    said = flat(text)
    assert (
        "is `withhold`, the default (also when it is unset or blank), or `send`, written in"
        " lower case; any other value stops cairn at start" in said
    )


# run.md, "Connect a model"


def test_run_md_says_what_the_model_is_sent(store):
    """'The model is sent a page's claims: the page's title, each claim's words and the
    heading it sits under.' The request holds exactly those of the page."""
    assert (
        "The model is sent a page's claims: the page's title, each claim's words and the"
        " heading it sits under." in flat(run_section())
    )
    found = store.create_project("alpha")
    for text in (FIRST, SECOND):
        note, _ = store.add_note(found.id, text)
        compile_note(store, note, gate=lint_gate)
    page = store.get_page(found.id, "drift-sync")
    spans = store.get_spans(i for claim in page.claims for i in claim.span_ids)
    user = messages(page, spans)[1]["content"]
    assert page.title in user and all(claim.text in user for claim in page.claims)
    assert 'section="Drift sync > Flags"' in user and 'section="Drift sync > Limits"' in user


def test_run_md_names_the_withheld_sources_and_the_setting_that_sends_them():
    text = run_section()
    sentence = sentence_with(text, "is not sent unless the file also says")
    check_names_every_withheld_kind(sentence)
    words = cli._parser().parse_args(["serve", "--db", "x.db", "--model-env", "ARRANGER"])
    assert words.model_env == "ARRANGER" and "--model-env ARRANGER" in text
    check_send_is_the_setting(sentence, "ARRANGER")


def test_run_md_s_reason_is_what_google_s_notes_quote():
    """'Google's rules for apps that read its users' data limit passing that data on to
    others': each Google source's notes quote the Limited Use rule on transfers."""
    assert (
        "Google's rules for apps that read its users' data limit passing that data on to"
        " others, and whether your model may read it is yours to decide" in flat(run_section())
    )
    for name in NOTES:
        notes = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert '"Transfers of data are not allowed, except:"' in notes, name


def test_run_md_a_page_not_sent_is_tidied_as_without_a_model(store):
    """'A page that is not sent is still tidied, without the model, as when no model is
    connected': the worker with a model leaves it as tidy_page alone would."""
    assert (
        "A page that is not sent is still tidied, without the model, as when no model is"
        " connected, except" in flat(run_section())
    )
    project, before = mail_page(store)
    twin, _ = mail_page(store, project="twin")
    assert tidy_page(store, twin.id, "drift-sync").status == "arranged"
    model = FakeModel([])
    arranger = Arranger(model, price=None, view_for=store.for_account)
    arranger.queue(store.account_id, project.id, "drift-sync")
    (done,) = asyncio.run(arranger.run_once())
    after = store.get_page(project.id, "drift-sync")
    assert done.status == "withheld" and model.requests == []
    assert after.body == store.get_page(twin.id, "drift-sync").body
    assert after.metrics["arranged_by"] == TIDY and len(after.claims) < len(before.claims)


def test_run_md_a_workspace_whose_model_budget_is_spent_waits(store):
    """'... except in a workspace whose plan's model budget is spent: cairn then reads none of
    its pages until the budget is renewed.' The worker reads the budget before the page: a
    page that is not sent waits there, queued and untidied, read by nobody."""
    assert (
        "except in a workspace whose plan's model budget is spent: cairn then reads none of"
        " its pages until the budget is renewed." in flat(run_section())
    )
    project, before = mail_page(store)
    spent = store.limited(Limits(model_budget_micros=0))
    read = []

    class Watched:
        def __getattr__(self, name):
            read.append(name)
            return getattr(spent, name)

    arranger = Arranger(FakeModel([]), price=None, view_for=lambda account: Watched())
    arranger.queue(store.account_id, project.id, "drift-sync")
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["no budget"]
    assert read == ["model_budget_left"] and store.get_page(project.id, "drift-sync") == before
    assert [p.slug for p in arranger.pending()] == ["drift-sync"]


@pytest.mark.parametrize(
    ("value", "sends"),
    [(None, False), ("", False), ("withhold", False), ("send", True), ("SEND", None)],
)
def test_run_md_the_two_words_and_any_other(value, sends):
    check_the_rule_s_words(run_section())
    environ = {} if value is None else {"ARRANGER_SOURCE_NOTES": value}
    if sends is None:
        with pytest.raises(ValueError, match="ARRANGER_SOURCE_NOTES"):
            cli_arrange.sends_source_notes("ARRANGER", environ)
    else:
        assert cli_arrange.sends_source_notes("ARRANGER", environ) is sends


def test_run_md_any_other_value_stops_cairn_at_start(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ARRANGER_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("ARRANGER_MODEL", "m")
    monkeypatch.setenv("ARRANGER_SOURCE_NOTES", "sometimes")
    path = tmp_path / "cairn.db"
    assert cli.main(["serve", "--db", str(path), "--model-env", "ARRANGER"]) == 2
    assert "ARRANGER_SOURCE_NOTES" in capsys.readouterr().err and not path.exists()


def test_run_md_the_log_says_which_of_the_two(store):
    """'When cairn starts with a model, its log says which of the two it runs with.'"""
    assert "When cairn starts with a model, its log says which of the two it runs with." in flat(
        run_section()
    )
    args = cli._parser().parse_args(["serve", "--db", "x.db", "--model-env", "ARRANGER"])
    arranger = Arranger(FakeModel([], name="m"), price=None, view_for=store.for_account)
    for send, word in ((False, "withhold"), (True, "send")):
        assert f"ARRANGER_SOURCE_NOTES={word}" in cli_arrange.serve_says(args, arranger, send)


# sources.md, privacy.md and the README


def test_sources_md_says_it_once_where_every_google_section_points():
    sentence = sentence_with(sources_section(), "is not sent to it unless the operator sets")
    check_names_every_withheld_kind(sentence)
    check_send_is_the_setting(sentence, "<PREFIX>")
    assert f"{sentence} cairn tidies that page itself instead." in flat(sources_section())
    raw = guide("sources.md")
    for heading in (
        "### Meeting transcripts from Google Meet, read by the server",
        "### Mail from Gmail, read by the server",
        "### Events from Google Calendar, read by the server",
        "### Documents from Google Drive, read by the server",
    ):
        start = raw.index(heading)
        section = raw[start : raw.index("\n## ", start)]
        assert "(#sources-the-server-reads)" in section, heading


def test_privacy_md_says_what_the_model_is_sent_and_what_it_is_not():
    line = models_line()
    sentence = sentence_with(line, "is not sent unless the operator allows it")
    check_names_every_withheld_kind(sentence)
    assert "cairn tidies that page itself instead" in flat(line)
    assert "A connected model receives a page's title, its claims as stored and the heading" in (
        flat(line)
    )


def test_the_readme_names_the_setting_beside_the_model_s_others():
    sentence = sentence_with(readme(), "_SOURCE_NOTES=send")
    check_names_every_withheld_kind(sentence)
    check_send_is_the_setting(sentence, "<PREFIX>")
    assert "is tidied without it" in sentence and "`withhold` is the default" in flat(readme())


def test_the_checks_see_a_doctored_guide(monkeypatch):
    """The checks are not vacuous: a source left out, another setting, or other words fail."""
    sentence = sentence_with(run_section(), "is not sent unless the file also says")
    with pytest.raises(AssertionError):
        check_names_every_withheld_kind(sentence.replace("Gmail, ", ""))
    with pytest.raises(AssertionError):
        check_send_is_the_setting(sentence.replace("=send", "=withhold"), "ARRANGER")
    with pytest.raises(AssertionError):
        check_send_is_the_setting(sentence, "CAIRN")
    with pytest.raises(AssertionError):
        check_the_rule_s_words(run_section().replace("lower case", "any case"))
    added = dataclasses.replace(KINDS["granola"], via="unit-new", withheld=True)
    monkeypatch.setitem(KINDS, "unit_new", added)  # Privacy 4: every kind is withheld already
    assert "unit-new" in sources.withheld_via()
    with pytest.raises(AssertionError):  # a kind withheld that the guide does not name
        check_names_every_withheld_kind(sentence)


def test_sources_md_says_the_rule_is_about_cairn_s_own_model_not_a_connected_agent_s(store):
    """sources.md: "**cairn's own model reads them only if the operator allows it.** ...
    An agent a person connects reads what that person reaches, these notes included, and
    hands what it reads to the model it runs on: that is the agent's doing, and no setting of
    the operator's and no project's **Models** changes it.\" (Privacy 4: connected assistants
    are outside all three settings.)"""
    text = flat(sources_section())
    assert "**cairn's own model reads them only if the operator allows it.**" in text
    assert "A model reads them only if the operator allows it" not in text
    assert (
        "cairn tidies that page itself instead. An agent a person connects reads what that"
        " person reaches, these notes included, and hands what it reads to the model it runs"
        " on: that is the agent's doing, and no setting of the operator's and no project's"
        " **Models** changes it."
    ) in text
    # a note from a withheld source is found by search like any other
    mail_page(store)  # a page of two notes read from Gmail
    assert store.search("flaky links", None, 8)  # what an agent's recall goes through
