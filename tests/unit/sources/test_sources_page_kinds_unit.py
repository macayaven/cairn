"""GET /sources for each kind of source: a kind describes what a source of it reads, so the
page shows a Granola source as sensibly as a Langfuse one without naming either."""

import json
import re
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.sources import KINDS, SourceKind
from cairn.store import Store
from support.cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
LANGFUSE = {
    "base_url": "https://langfuse.example.test",
    "projects": ["ops"],
    "public_key_env": "LF_PUBLIC",
    "secret_key_env": "LF_SECRET",
}
GRANOLA = {"key_env": "GRANOLA_KEY", "projects": [], "base_url": "https://granola.example.test/v1"}
MEET = {
    "credentials_env": "MEET_CREDENTIAL",
    "subject": "ada@example.test",
    "projects": [],
    "base_url": "https://meet.example.test/v2",
}
DRIVE = {
    "credentials_env": "DRIVE_CREDENTIAL",
    "folder_id": "1PageFolder" + "AbCdEfGh",
    "projects": [],
    "base_url": "https://drive.example.test/drive/v3",
}
CALENDAR = {
    "credentials_env": "CALENDAR_CREDENTIAL",
    "subject": "ada@example.test",
    "calendar_id": "team@group.example.test",
    "projects": [],
    "base_url": "https://calendar.example.test/v3",
}
GMAIL = {
    "credentials_env": "GMAIL_CREDENTIAL",
    "subject": "ada@example.test",
    "label": "For cairn",
    "projects": [],
    "base_url": "https://gmail.example.test/gmail/v1",
}


def plain(config: object) -> dict:
    """A kind that keeps whatever it is given: the page must not guess from a setting's name."""
    return {**config, "projects": list(config.get("projects", []))}  # type: ignore[dict-item]


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setitem(
        KINDS, "minutes", SourceKind(check=plain, open=None, via="m", starts_off=True)
    )
    store = Store(tmp_path / "cairn.db")
    store.create_project("ops")
    owner = store.sign_in(
        "sso", "o", "o@example.test", "Olive", issuer="https://id.example.test", join="default"
    )
    client = TestClient(create_app(store, public_url=BASE), base_url=BASE)
    client.cookies.set(session_cookie(), owner.token)

    def section(name: str, token: str | None = None) -> str:
        headers = {"cookie": f"{session_cookie()}={token}"} if token else None
        html = client.get("/sources", headers=headers).text
        found = re.search(
            rf'<section class="integration" id="source-{name}".*?</section>', html, re.S
        )
        assert found, name
        return " ".join(found.group(0).split())

    with client:
        yield SimpleNamespace(store=store, client=client, section=section, owner=owner)
    store.close()


def reads(section: str) -> str | None:
    found = re.search(r"<dt>Reads</dt>\s*<dd[^>]*>(.*?)</dd>", section)
    return found.group(1) if found else None


def test_each_built_in_kind_describes_what_a_source_of_it_reads():
    assert KINDS["langfuse"].describe(LANGFUSE) == (
        "Traces marked with the score “cairn” at https://langfuse.example.test"
    )
    granola = KINDS["granola"]
    assert granola.describe(granola.check(GRANOLA)) == (
        "Meeting notes the key can read, summaries only, at https://granola.example.test/v1;"
        " each is read once it has not changed for 30 minutes"
    )
    narrowed = granola.check(
        {**GRANOLA, "folder_id": "fol_" + "Abcdefghij0123", "transcripts": True,
         "settle_minutes": 0}
    )  # fmt: skip
    assert granola.describe(narrowed) == (
        "Meeting notes in folder fol_Abcdefghij0123, with their transcripts, at"
        " https://granola.example.test/v1; each is read as soon as it is listed"
    )
    meet = KINDS["google_meet"]
    assert meet.describe(meet.check(MEET)) == (
        "Google Meet transcripts of the meetings Google lists for ada@example.test, at"
        " https://meet.example.test/v2; each is read once its file is generated, looked for"
        " until 48 hours after the meeting ends"
    )
    own = meet.check({**MEET, "subject": None, "wait_hours": 1})
    assert meet.describe(own) == (
        "Google Meet transcripts of the meetings Google lists for the person who authorised"
        " its credential, at https://meet.example.test/v2; each is read once its file is"
        " generated, looked for until 1 hour after the meeting ends"
    )
    calendar = KINDS["google_calendar"]
    assert calendar.describe(calendar.check(CALENDAR)) == (
        "Events of calendar team@group.example.test, read as ada@example.test, at"
        " https://calendar.example.test/v3; each is read once, after it has ended"
    )
    own = {k: v for k, v in CALENDAR.items() if k not in ("subject", "calendar_id")}
    primary = calendar.check(own)
    assert calendar.describe(primary) == (
        "Events of the primary calendar of the account its credential signs in as, at"
        " https://calendar.example.test/v3; each is read once, after it has ended"
    )
    gmail = KINDS["gmail"]
    assert gmail.describe(gmail.check(GMAIL)) == (
        "Messages in the mailbox of ada@example.test that carry the Gmail label “For cairn”, at"
        " https://gmail.example.test/gmail/v1; each is read once, when it is labelled within"
        " 30 days of arriving"
    )
    own = gmail.check({**GMAIL, "subject": None, "lookback_days": 1})
    assert gmail.describe(own) == (
        "Messages in the mailbox of the person who authorised its refresh token that carry the"
        " Gmail label “For cairn”, at https://gmail.example.test/gmail/v1; each is read once, when"
        " it is labelled within 1 day of arriving"
    )


def test_a_granola_source_shows_its_kind_where_it_writes_its_state_and_what_it_reads(page):
    page.store.add_source("meet", "granola", GRANOLA)
    section = page.section("meet")
    assert '<span class="tag">granola</span>' in section
    assert "Off." in section and "Writes into" in section and "Never" in section
    assert reads(section) == (
        "Meeting notes the key can read, summaries only, at https://granola.example.test/v1;"
        " each is read once it has not changed for 30 minutes"
    )
    assert "score" not in section and "GRANOLA_KEY" not in section


def test_a_google_meet_source_shows_whose_meetings_it_reads_and_never_its_variable(page):
    page.store.add_source("calls", "google_meet", MEET)
    section = page.section("calls")
    assert '<span class="tag">google_meet</span>' in section
    assert "Off." in section and "Writes into" in section and "Never" in section
    assert reads(section) == (
        "Google Meet transcripts of the meetings Google lists for ada@example.test, at"
        " https://meet.example.test/v2; each is read once its file is generated, looked for"
        " until 48 hours after the meeting ends"
    )
    assert "MEET_CREDENTIAL" not in section and "credentials_env" not in section
    assert 'value="meet-transcripts"' in field(section) and ticked(section) == []


def test_a_google_drive_source_shows_which_folder_it_reads_and_never_its_variable(page):
    page.store.add_source("specs", "google_drive", DRIVE)
    section = page.section("specs")
    assert '<span class="tag">google_drive</span>' in section
    assert "Off." in section and "Writes into" in section and "Never" in section
    assert reads(section) == (
        "Google Docs, Markdown and text files directly in the Google Drive folder"
        " 1PageFolderAbCdEfGh, read as the account its credential belongs to, at"
        " https://drive.example.test/drive/v3; each is read once it has not changed for 24"
        " hours"
    )
    assert state(section) == (
        "Off. It reads nothing. Switching it on reads the documents put into the folder or"
        " changed there from then on, each once it has rested: nothing from before, and nothing"
        " from while it was off. With existing set, it reads the documents already there too."
    )
    assert "DRIVE_CREDENTIAL" not in section and "credentials_env" not in section
    assert 'value="documents"' in field(section) and ticked(section) == []


def test_a_google_calendar_source_shows_which_calendar_it_reads_and_never_its_variable(page):
    page.store.add_source("diary", "google_calendar", CALENDAR)
    section = page.section("diary")
    assert '<span class="tag">google_calendar</span>' in section
    assert "Off." in section and "Writes into" in section and "Never" in section
    assert reads(section) == (
        "Events of calendar team@group.example.test, read as ada@example.test, at"
        " https://calendar.example.test/v3; each is read once, after it has ended"
    )
    assert "CALENDAR_CREDENTIAL" not in section and "credentials_env" not in section
    assert 'value="calendar"' in field(section) and ticked(section) == []


def test_a_gmail_source_shows_its_label_and_what_switching_on_reads_and_never_its_variable(page):
    page.store.add_source("inbox", "gmail", GMAIL)
    section = page.section("inbox")
    assert '<span class="tag">gmail</span>' in section
    assert "Off." in section and "Writes into" in section and "Never" in section
    assert reads(section) == (
        "Messages in the mailbox of ada@example.test that carry the Gmail label “For cairn”, at"
        " https://gmail.example.test/gmail/v1; each is read once, when it is labelled within"
        " 30 days of arriving"
    )
    assert state(section) == (
        "Off. It reads nothing. Switching it on reads the messages that arrive from then on and"
        " carry the label, each once: nothing that arrived before, even when it is labelled"
        " later."
    )
    assert "GMAIL_CREDENTIAL" not in section and "credentials_env" not in section
    assert 'value="mail"' in field(section) and ticked(section) == []


def test_a_label_with_markup_in_its_name_is_shown_as_text(page):
    page.store.add_source("inbox", "gmail", {**GMAIL, "label": "<b>Clients</b> & co"})
    section = page.section("inbox")
    assert "<b>Clients</b>" not in section
    assert "label “&lt;b&gt;Clients&lt;/b&gt; &amp; co”, at" in reads(section)


def test_a_langfuse_source_still_shows_its_address_and_score(page):
    page.store.add_source("traces", "langfuse", {**LANGFUSE, "score_name": "keep"})
    assert reads(page.section("traces")) == (
        "Traces marked with the score “keep” at https://langfuse.example.test"
    )


def test_a_kind_that_does_not_describe_itself_shows_no_reads_fact(page):
    page.store.add_source("standup", "minutes", {"projects": [], "base_url": "https://x.test"})
    assert reads(page.section("standup")) is None


# What switching on reads: each kind says it, and the page says no more than that


def state(section: str) -> str:
    found = re.search(r'<p class="state is-off">(.*?)</p>', section)
    assert found, section
    return re.sub(r"<[^>]+>", "", found.group(1))


def test_an_off_langfuse_source_says_it_goes_on_from_where_it_stopped(page):
    page.store.add_source("traces", "langfuse", LANGFUSE)
    page.store.disable_source("traces")
    said = state(page.section("traces"))
    assert "from then on" not in said and "nothing from while it was off" not in said
    assert said == (
        "Off. It reads nothing. Switched on again, it goes on from where it stopped: traces"
        " marked for cairn while it was off are read too."
    )


@pytest.mark.parametrize("kind", ["minutes", "granola", "google_meet", "google_calendar"])
def test_an_off_source_of_a_kind_that_starts_off_reads_only_from_then_on(page, kind):
    config = {"granola": GRANOLA, "google_meet": MEET, "google_calendar": CALENDAR}.get(
        kind, {"projects": []}
    )
    page.store.add_source("standup", kind, config)
    assert state(page.section("standup")) == (
        "Off. It reads nothing. Switching it on reads only what happens from then on: nothing"
        " from before, and nothing from while it was off."
    )


def test_source_enable_says_what_it_does_for_every_kind(capsys):
    from cairn import server

    with pytest.raises(SystemExit):
        server.main(["--help"])
    said = " ".join(capsys.readouterr().out.split())
    assert "source-enable switch a source on and choose the projects it writes into" in said
    assert "from then on" not in said


# A default destination: what the form proposes for a source that names no project yet


def field(section: str) -> str:
    (found,) = re.findall(r'<input type="text"[^>]*name="new_project"[^>]*>', section)
    return found


def ticked(section: str) -> list[str]:
    return re.findall(r'name="project" value="([^"]+)" checked', section)


def test_every_kind_that_names_a_default_project_names_a_valid_one():
    from cairn.store.memory import is_project_name

    named = {name: kind.default_project for name, kind in KINDS.items() if kind.default_project}
    assert named.get("granola") == "meetings"
    assert named.get("google_meet") == "meet-transcripts"
    assert named.get("google_drive") == "documents"
    assert named.get("google_calendar") == "calendar"
    assert named.get("gmail") == "mail"
    assert all(is_project_name(project) for project in named.values()), named


def test_with_no_such_project_the_new_project_field_proposes_the_default(page):
    page.store.add_source("meet", "granola", GRANOLA)
    section = page.section("meet")
    assert 'value="meetings"' in field(section) and ticked(section) == []
    hint = re.search(r'<p class="hint" id="source-meet-new-project-hint">(.*?)</p>', section)
    assert hint and "Made private" in hint.group(1)


def test_with_such_a_project_in_reach_its_box_is_ticked_and_the_field_is_empty(page):
    page.store.create_project("Meetings")
    page.store.add_source("meet", "granola", GRANOLA)
    section = page.section("meet")
    assert ticked(section) == ["Meetings"] and "value=" not in field(section)


def test_a_source_that_names_a_project_gets_no_proposal(page):
    page.store.create_project("meetings")
    page.store.add_source("meet", "granola", {**GRANOLA, "projects": ["ops"]})
    section = page.section("meet")
    assert ticked(section) == ["ops"] and "value=" not in field(section)


def test_a_kind_without_a_default_proposes_nothing_and_renders_as_before(page):
    page.store.create_project("meetings")
    page.store.add_source("standup", "minutes", {"projects": []})
    section = page.section("standup")
    assert ticked(section) == []
    assert field(section) == (
        '<input type="text" id="source-standup-new-project" name="new_project"'
        ' aria-describedby="source-standup-new-project-hint" autocomplete="off"'
        ' spellcheck="false">'
    )


def test_switching_on_with_the_proposal_makes_a_private_project_by_the_poster(page):
    page.store.add_source("meet", "granola", GRANOLA)
    posted = page.client.post(
        "/integrations/meet/on",
        data={"new_project": "meetings"},  # the form as proposed: nothing else touched
        headers={"sec-fetch-site": "same-origin"},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    made = page.store.get_project("meetings")
    assert made.maker_id == page.owner.user.id
    assert page.store.project_access(made.id).everyone == "none"
    source = page.store.get_source("meet")
    assert source.enabled and source.config["projects"] == ["meetings"]


def test_a_default_name_taken_out_of_reach_is_not_revealed_and_the_post_says_so(page):
    page.store.create_project("meetings", everyone="none")  # made by no person: owners only
    admin = page.store.sign_in(
        "sso", "a", "a@example.test", "Abe", issuer="https://id.example.test", join="default"
    )
    page.store.set_role(admin.user.id, "admin")
    page.store.add_source("meet", "granola", GRANOLA)
    section = page.section("meet", admin.token)
    # An admin makes no project for a source (findings 2 and 3): no field proposes the name,
    # and a post of it is refused before the name is looked at, so it says nothing either.
    assert 'name="new_project"' not in section and ticked(section) == []
    assert 'name="project" value="meetings"' not in section
    posted = page.client.post(
        "/integrations/meet/on",
        data={"new_project": "meetings"},
        headers={"sec-fetch-site": "same-origin", "cookie": f"{session_cookie()}={admin.token}"},
        follow_redirects=False,
    )
    assert posted.status_code == 400 and "only an owner decides" in posted.text
    assert "already used" not in posted.text
    assert not page.store.get_source("meet").enabled


def test_posting_nothing_for_a_source_with_no_project_is_still_refused(page):
    page.store.add_source("meet", "granola", GRANOLA)
    posted = page.client.post(
        "/integrations/meet/on",
        data={},
        headers={"sec-fetch-site": "same-origin"},
        follow_redirects=False,
    )
    assert posted.status_code == 400 and not page.store.get_source("meet").enabled


@pytest.mark.parametrize(
    ("kind", "project"),
    [
        ("granola", "meetings"),
        ("google_meet", "meet-transcripts"),
        ("google_drive", "documents"),
        ("google_calendar", "calendar"),
        ("gmail", "mail"),
        ("minutes", "PROJECT"),
    ],
)
def test_source_add_names_the_kinds_default_project_in_how_to_switch_it_on(
    tmp_path, monkeypatch, capsys, kind, project
):
    from cairn import server

    monkeypatch.setitem(
        KINDS, "minutes", SourceKind(check=plain, open=None, via="m", starts_off=True)
    )
    db = tmp_path / "cairn.db"
    Store(db).close()
    config = {
        "granola": GRANOLA,
        "google_meet": MEET,
        "google_drive": DRIVE,
        "google_calendar": CALENDAR,
        "gmail": GMAIL,
    }.get(kind, {"projects": []})
    argv = ["source-add", "--db", str(db), "--name", "meet", "--kind", kind]
    assert server.main([*argv, "--config", json.dumps(config)]) == 0
    said = capsys.readouterr().out
    assert said.strip().endswith(f"source-enable --name meet --project {project}")
