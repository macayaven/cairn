"""Sentences of src/cairn/guides/sources.md about the Granola source and the project a source
proposes, each pinned here so the guide cannot go false unnoticed: the sentence is in the
test's docstring and in the guide, and the test asks the running code for it. Granola is a
stand-in; nothing leaves the process."""

import re
import shlex
from datetime import timedelta
from importlib.resources import files
from pathlib import Path

import pytest
from guide_source import guide_source
from starlette.testclient import TestClient
from test_granola_unit import KEY, NOW, SINCE, Stand, item, meeting, origins

from cairn.server import create_app
from cairn.sources import SourceError
from cairn.sources import granola as g
from cairn.store import Store
from cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
ISSUER = "https://id.example.test/pool-1"
RAW = guide_source("sources")
GUIDE = " ".join(RAW.split())
GRANOLA = {"key_env": "GRANOLA_KEY", "projects": []}


def said(sentence: str) -> None:
    """The guide says `sentence`, word for word (whitespace aside)."""
    assert " ".join(sentence.split()) in GUIDE, sentence


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def new_project_field(html: str, source: str) -> str:
    (found,) = re.findall(rf'<input type="text" id="source-{source}-new-project"[^>]*>', html)
    return found


def test_a_granola_source_proposes_the_project_meetings_and_the_choice_stays_yours(store):
    """sources.md: "For a source that names no project yet, the page proposes its kind's
    project, `meetings` for Granola: it ticks a project of that name when you reach one, and
    otherwise, for an owner, fills in the name as a new project. Tick another or type another
    name to write elsewhere.\""""
    said(
        "For a source that names no project yet, the page proposes its kind's project, `meetings`"
        " for Granola: it ticks a project of that name when you reach one, and otherwise, for"
        " an owner, fills in the name as a new project. Tick another or type another name to"
        " write elsewhere."
    )
    owner = store.sign_in("sso", "o", "o@example.test", "Olive", issuer=ISSUER, join="default")
    store.add_source("meet", "granola", GRANOLA)
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        client.cookies.set(session_cookie(), owner.token)
        html = client.get("/sources").text
        assert 'value="meetings"' in new_project_field(html, "meet")
        store.create_project("meetings")
        html = client.get("/sources").text
        assert 'name="project" value="meetings" checked' in html
        assert "value=" not in new_project_field(html, "meet")
        posted = client.post(
            "/integrations/meet/on",
            data={"project": "", "new_project": "board"},  # another name typed instead
            headers={"sec-fetch-site": "same-origin"},
            follow_redirects=False,
        )
        assert posted.status_code == 303
        assert store.get_source("meet").config["projects"] == ["board"]


# sources.md, "Meetings from Granola, read by the server"


def test_it_writes_each_meeting_into_one_project_on_a_page_of_its_own(store):
    """sources.md: "A Granola source reads the notes one Granola API key can read, or the
    notes of one folder or space, and writes each meeting as a note into one project." ...
    "Each meeting's page is named by its title and its date, so a weekly meeting gets a page a
    week; two meetings with the same title on the same day share one.\""""
    said(
        "A Granola source reads the notes one Granola API key can read, or the notes of one"
        " folder or space, and writes each meeting as a note into one project."
    )
    said(
        "Each meeting's page is named by its title and its date, so a weekly meeting gets a page"
        " a week; two meetings with the same title on the same day share one."
    )
    with pytest.raises(ValueError, match="at most one project"):
        g.check_config({**GRANOLA, "projects": ["meetings", "board"]})
    stand = Stand()
    days = (timedelta(days=1), timedelta(days=8), timedelta(days=8, hours=3))
    for n, at in enumerate(days, 1):  # a weekly meeting, two weeks; the second met twice
        note_id = stand.add(n, SINCE + at)
        stand.notes[note_id]["title"] = "Payments weekly"
    got = stand.pull(folder_id="fol_" + "Abcdefghij0123")
    topics = [d.topic for d in got.drafts]
    assert len(topics) == 3 and all(t.startswith("Payments weekly, ") for t in topics)
    assert topics[0] != topics[1] == topics[2]
    assert {d.project for d in got.drafts} == {"meetings"}
    lists = [r for r in stand.requests if r.url.path.endswith("/notes")]
    assert lists and all(r.url.params["folder_id"] == "fol_Abcdefghij0123" for r in lists)


def test_the_note_holds_names_and_the_summary_unchanged_and_no_address_or_private_note():
    """sources.md: "Its note has a heading with the title, when the meeting took place, who
    attended and who owns the note, by name, then the summary as Granola wrote it." ...
    "cairn writes people's names, never the email addresses Granola sends beside them, and
    never the owner's private notes. The summary itself is written unchanged, so an
    address in it meets the note guard as in any note.\""""
    said(
        "Its note has a heading with the title, when the meeting took place, who attended and"
        " who owns the note, by name, then the summary as Granola wrote it."
    )
    said(
        "cairn writes people's names, never the email addresses Granola sends beside them,"
        " and never the owner's private notes. The summary itself is written unchanged, so an"
        " address in it meets the note guard as in any note."
    )
    summary = "### Next steps\n\n- Write to ada@example.test about the refund window.\n"
    text = g.summary_text(meeting(summary_markdown=summary))
    assert text.startswith("# Payments weekly\n") and "Ada Park" in text and "Dee Quinn" in text
    assert text.endswith(summary)  # unchanged, address and all: the guard decides
    rest = text.removesuffix(summary)
    assert "@" not in rest and "aside" not in text


def test_a_name_that_holds_an_address_counts_as_no_name():
    """sources.md: "A name that holds an address counts as no name: the attendee is counted,
    the owner left out.\""""
    said(
        "A name that holds an address counts as no name: the attendee is counted, the owner left"
        " out."
    )
    text = g.summary_text(
        meeting(
            owner={"name": "dee@example.test", "email": "dee@example.test"},
            attendees=[
                {"name": "Ada Park", "email": "ada@example.test"},
                {"name": "brook\uff20example.test", "email": "brook@example.test"},
            ],
        )
    )
    assert "- Attendees: Ada Park, and 1 more without a name\n" in text
    assert "Owner" not in text and "example.test" not in text


def test_transcripts_are_off_unless_asked_for_and_go_on_pages_of_their_own():
    """sources.md: "With `transcripts` on, the meeting's transcript is written too, a line a
    turn, on pages of its own; transcripts are off unless you ask for them.\""""
    said(
        "With `transcripts` on, the meeting's transcript is written too, a line a turn, on"
        " pages of its own; transcripts are off unless you ask for them."
    )
    assert g.check_config(GRANOLA)["transcripts"] is False
    stand = Stand()
    note_id = stand.add(1, SINCE + timedelta(days=1))
    stand.segments[note_id] = [item("Ada", "Morning.", 0), item("Bo", "Hello.", 5)]
    assert len(stand.pull().drafts) == 1
    summary, said_draft = stand.pull(transcripts=True).drafts
    assert said_draft.topic != summary.topic and said_draft.text.count("\n- ") == 2


def test_a_note_is_read_once_after_it_settles_and_only_if_made_after_the_switch_on():
    """sources.md: "A note is read once its summary has not changed for `settle_minutes` (30
    minutes unless set), and only once: an edit at Granola afterwards, a regenerated summary
    or a deletion changes nothing in cairn. Only notes created after the source was switched
    on are read, nothing from before.\""""
    said(
        "A note is read once its summary has not changed for `settle_minutes` (30 minutes"
        " unless set), and only once: an edit at Granola afterwards, a regenerated summary or a"
        " deletion changes nothing in cairn. Only notes created after the source was switched"
        " on are read, nothing from before."
    )
    assert g.check_config(GRANOLA)["settle_minutes"] == 30
    stand = Stand()
    stand.add(1, SINCE - timedelta(hours=1), updated=NOW - timedelta(hours=2))  # before
    fresh = stand.add(2, NOW - timedelta(hours=1), updated=NOW - timedelta(minutes=10))
    first = stand.pull()
    assert first.drafts == []  # one from before, one still changing
    stand.now = NOW + timedelta(minutes=30)
    second = stand.pull(first.cursor)
    assert origins(second) == [g.ORIGIN + fresh]
    stand.notes[fresh]["updated_at"] = "2026-11-13T12:40:00Z"
    stand.notes[fresh]["summary_markdown"] = "Rewritten."
    stand.now = NOW + timedelta(hours=2)
    assert stand.pull(second.cursor).drafts == []


def test_the_operator_adds_it_off_with_the_key_named_in_the_servers_environment(
    store, capsys, tmp_path
):
    """sources.md: "The operator puts the key in the server's environment, never in cairn
    (with Docker, a line in `deploy/cairn.env`), and adds the source, which starts off:" ...
    "Everything but `key_env` is optional: `folder_id` or `space_id` (one folder or one
    space), `transcripts` (false), `settle_minutes` (30), `lookback_days` (30: how far back
    the daily look for notes that turned up late goes) and `base_url`.\""""
    said(
        "The operator puts the key in the server's environment, never in cairn (with Docker,"
        " a line in `deploy/cairn.env`), and adds the source, which starts off:"
    )
    said(
        "Everything but `key_env` is optional: `folder_id` or `space_id` (one folder or one"
        " space), `transcripts` (false), `settle_minutes` (30), `lookback_days` (30: how far"
        " back the daily look for notes that turned up late goes) and `base_url`."
    )
    assert set(g.SETTINGS) == {
        "key_env", "folder_id", "space_id", "transcripts", "settle_minutes", "lookback_days",
        "base_url", "projects",
    }  # fmt: skip
    assert g.check_config({"key_env": "GRANOLA_API_KEY"})["lookback_days"] == 30
    with pytest.raises(ValueError, match="looks like a key"):
        g.check_config({"key_env": KEY})
    command = re.search(r"python -m cairn\.server source-add [^\n]*--kind granola[^\n]*", RAW)
    assert command, "the guide shows the command"
    argv = shlex.split(command.group(0))[3:]
    db = tmp_path / "guide.db"
    Store(db).close()
    argv[argv.index("--db") + 1] = str(db)
    from cairn import server

    assert server.main(argv) == 0
    added = Store(db)
    try:
        assert added.list_sources()[0].enabled is False
    finally:
        added.close()
    assert "--project meetings" in capsys.readouterr().out
    with pytest.raises(SourceError, match="GRANOLA_API_KEY"):
        g.open_source("granola", g.check_config({"key_env": "GRANOLA_API_KEY"}), http=None, env={})


def test_the_guide_lists_every_kind_of_source_the_server_reads():
    """sources.md: "It reads these: Langfuse, where the traces someone marked with a score
    become notes; [Granola](#meetings-from-granola-read-by-the-server), where meeting notes
    do; [Google Meet](#meeting-transcripts-from-google-meet-read-by-the-server), where meeting
    transcripts do; [Google Drive](#documents-from-google-drive-read-by-the-server), where the
    documents put into one folder do;
    [Google Calendar](#events-from-google-calendar-read-by-the-server), where events do; and
    [Gmail](#mail-from-gmail-read-by-the-server), where the messages a person labels do.\""""
    said(
        "It reads these: Langfuse, where the traces someone marked with a score become notes;"
        " [Granola](#meetings-from-granola-read-by-the-server), where meeting notes do;"
        " [Google Meet](#meeting-transcripts-from-google-meet-read-by-the-server), where"
        " meeting transcripts do;"
        " [Google Drive](#documents-from-google-drive-read-by-the-server), where the documents"
        " put into one folder do;"
        " [Google Calendar](#events-from-google-calendar-read-by-the-server), where events do;"
        " and [Gmail](#mail-from-gmail-read-by-the-server), where the messages a person labels"
        " do."
    )
    from cairn.sources import KINDS

    # a new kind joins the list
    assert set(KINDS) == {
        "langfuse",
        "granola",
        "google_meet",
        "google_drive",
        "google_calendar",
        "gmail",
        "linear",
        "github",
        "connector",
    }
    assert "### Meetings from Granola, read by the server" in RAW
    assert "### Meeting transcripts from Google Meet, read by the server" in RAW
    assert "### Documents from Google Drive, read by the server" in RAW
    assert "### Events from Google Calendar, read by the server" in RAW
    assert "### Mail from Gmail, read by the server" in RAW
    assert "### Issues from Linear, read by the server" in RAW


def test_what_the_guide_says_of_granola_itself_is_what_its_documentation_says():
    """sources.md: "Granola's API returns only notes that have a generated summary, so a
    meeting without one is not read." ... "Granola makes API keys on its Business and
    Enterprise plans." Both are Granola's statements, recorded in our words in
    docs/granola-api.md."""
    said(
        "Granola's API returns only notes that have a generated summary, so a meeting without"
        " one is not read."
    )
    said("Granola makes API keys on its Business and Enterprise plans.")
    notes = (Path(__file__).parents[2] / "docs" / "granola-api.md").read_text()
    notes = " ".join(notes.split())
    assert (
        "The API returns a note only once its AI summary has been generated; a note still"
        " being processed, or never summarised, is left out of List notes and answers 404 on"
        " Get note (paraphrased). [doc: INTRO]" in notes
    )
    plans = (
        "KEYS-HELP (paraphrased): every workspace member on a **Business** or **Enterprise**"
        " plan can make API keys [doc: KEYS-HELP]"
    )
    assert plans in notes


def test_the_deploy_guide_puts_the_key_in_cairn_env_and_says_which_kind_starts_off():
    """deploy/README.md: "put a line such as `GRANOLA_API_KEY=...` in `deploy/cairn.env`, run
    `docker compose up -d`, and give the source `{"key_env": "GRANOLA_API_KEY"}`." ... "A
    Langfuse source is on as soon as it is added; a Granola source starts off and reads
    nothing until someone switches it on, and then only notes made from then on. Langfuse
    reads a trace marked while it was off once it is on again.\""""
    deploy = (Path(__file__).parents[2] / "deploy" / "README.md").read_text()
    deploy = " ".join(deploy.split())
    for sentence in (
        "put a line such as `GRANOLA_API_KEY=...` in `deploy/cairn.env`, run `docker compose"
        ' up -d`, and give the source `{"key_env": "GRANOLA_API_KEY"}`.',
        "A Langfuse source is on as soon as it is added; a Granola source starts off and reads"
        " nothing until someone switches it on, and then only notes made from then on. Langfuse"
        " reads a trace marked while it was off once it is on again.",
    ):
        assert sentence in deploy, sentence
    assert "Switching a source on reads only what happens from then on." not in deploy
    from cairn.sources import KINDS

    assert KINDS["granola"].starts_off and not KINDS["langfuse"].starts_off
    assert g.check_config({"key_env": "GRANOLA_API_KEY"})["key_env"] == "GRANOLA_API_KEY"
    assert "while it was off are read too" in KINDS["langfuse"].switching_on()


def test_the_operator_adds_it_and_the_workspace_switches_it_on_as_for_any_source():
    """sources.md: "`python -m cairn.server source-add --help` lists each kind's settings." ...
    "Then an owner or an admin switches it on under **Workspace**, then **Sources**, and chooses
    its project ([Sources the server reads](#sources-the-server-reads))." run.md: "A source
    (Langfuse, Granola, Google Meet, Google Drive, Google Calendar, Gmail, Linear, GitHub or a
    connector of your own) is read by the server itself with credentials from its environment,
    so you add or remove it on the command line\""""
    said("`python -m cairn.server source-add --help` lists each kind's settings.")
    said(
        "Then an owner or an admin switches it on under **Workspace**, then **Sources**, and"
        " chooses its project ([Sources the server reads](#sources-the-server-reads))."
    )
    run = " ".join(files("cairn.guides").joinpath("run.md").read_text().split())
    assert (
        "A source (Langfuse, Granola, Google Meet, Google Drive, Google Calendar, Gmail, Linear,"
        " GitHub or a connector of your own) is read by the server itself with credentials from"
        " its environment, so you add or remove it on the command line" in run
    )
    from cairn.sources import KINDS

    assert set(KINDS) == {
        "langfuse",
        "granola",
        "google_meet",
        "google_drive",
        "google_calendar",
        "gmail",
        "linear",
        "github",
        "connector",
    }
    assert KINDS["granola"].starts_off


def test_a_failed_runs_reason_is_on_the_page_and_names_the_variable_never_the_key(store):
    """sources.md: "The Sources page shows the reason as the run gave it, in words. That
    reason can name the environment variable a credential is read from, so the operator
    knows which one to check, except for a source made from an account. It never holds the
    credential.\" (A source made from an account: tests/unit/test_sources_page_unit.py.)"""
    import asyncio

    import httpx

    from cairn.sources import run_source

    said(
        "The Sources page shows the reason as the run gave it, in words. That reason can name"
        " the environment variable a credential is read from, so the operator knows which one"
        " to check, except for a source made from an account. It never holds the credential."
    )
    owner = store.sign_in("sso", "o", "o@example.test", "Olive", issuer=ISSUER, join="default")
    store.create_project("meetings")
    store.add_source("meet", "granola", {**GRANOLA, "projects": ["meetings"]})
    store.enable_source("meet")

    def refuses(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED", "message": KEY}})

    async def run(env: dict[str, str]) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(refuses)) as http:
            await run_source(store, "meet", http=http, env=env)

    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        client.cookies.set(session_cookie(), owner.token)
        for env in ({}, {"GRANOLA_KEY": KEY}):  # the variable missing; then the key refused
            with pytest.raises(SourceError):
                asyncio.run(run(env))
            html = client.get("/sources").text
            assert "Failed:" in html and "GRANOLA_KEY" in html, env
            assert KEY not in html


def test_a_run_under_way_when_a_source_is_switched_off_writes_nothing_more(store):
    """sources.md: "A run already under way at that moment may finish the request it is
    making, and writes nothing more.\""""
    said(
        "A run already under way at that moment may finish the request it is making, and"
        " writes nothing more."
    )
    store.create_project("meetings")
    store.add_source("meet", "granola", {**GRANOLA, "projects": ["meetings"]})
    store.enable_source("meet")
    claim = store.claim_source("meet")  # a run takes the source
    assert claim is not None and store.holds_source_claim(claim)
    store.disable_source("meet")  # switched off while that run is under way
    assert not store.holds_source_claim(claim)  # the run finds out before its next note
    assert store.finish_source_run(claim, "cursor", {}) is False  # and stores nothing
    assert store.get_source("meet").cursor is None
