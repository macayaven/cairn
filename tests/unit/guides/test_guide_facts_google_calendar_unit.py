"""Sentences of src/cairn/guides/sources.md (and deploy/README.md) about the Google Calendar
source, each pinned here so the guide cannot go false unnoticed: the sentence is in the
test's docstring and in the guide, and the test asks the running code for it, or, for what
Google says, the notes the guide was written from (docs/google-calendar-api.md and
docs/google-auth.md). Google is a stand-in; nothing leaves the process. Every name, address
and key is made up, and the key pair is made at test time."""

import json
import shlex
from importlib.resources import files

import pytest

from cairn.sources import FROM_THEN_ON, KINDS, SourceError
from cairn.sources import google_auth as auth
from cairn.sources import google_calendar as c
from support.google_authorize_guide import own_authorisation
from support.guide_source import guide_source
from support.paths import REPO_ROOT
from support.sources.google_auth import key_file, keys, user  # noqa: F401 (keys is a fixture)
from support.sources.google_calendar import (
    CREDENTIAL,
    DAY,
    HOUR,
    MINUTE,
    NOW,
    SINCE,
    VAR,
    Stand,
    all_day,
    full_event,
    refs,
    settings,
    timed,
)

ROOT = REPO_ROOT
RAW = guide_source("sources")
GUIDE = " ".join(RAW.split())
NOTES = " ".join((ROOT / "docs" / "google-calendar-api.md").read_text().split())
AUTH_NOTES = " ".join((ROOT / "docs" / "google-auth.md").read_text().split())
HEADING = "### Events from Google Calendar, read by the server"
ANCHOR = "#events-from-google-calendar-read-by-the-server"


def said(sentence: str) -> None:
    """The guide says `sentence`, word for word (whitespace aside)."""
    assert " ".join(sentence.split()) in GUIDE, sentence


def section() -> str:
    """The Calendar source's section of the guide, to its last line."""
    start = RAW.index(HEADING)
    return RAW[start : RAW.index("\n## ", start)]


def here(sentence: str) -> None:
    """The Calendar source's section says `sentence` (the Meet section says some alike)."""
    assert " ".join(sentence.split()) in " ".join(section().split()), sentence


def noted(*quotes: str, notes: str = NOTES) -> None:
    for quote in quotes:
        assert " ".join(quote.split()) in notes, quote


def test_the_guide_lists_it_and_says_cairn_signs_in_to_a_calendar_only_through_it():
    """sources.md: "... and to your calendar only through a [Google Calendar
    source](#events-from-google-calendar-read-by-the-server) the operator adds." (The guide's
    first line said cairn signs in to neither; with the Gmail source, it names both.)"""
    said(
        f"and to your calendar only through a [Google Calendar source]({ANCHOR}) the operator adds."
    )
    assert "cairn does not sign in to your mail or your calendar." not in GUIDE
    assert "cairn does not sign in to your mail" not in GUIDE
    assert f"[Google Calendar]({ANCHOR}), where events do;" in GUIDE
    assert "google_calendar" in KINDS


def test_the_guide_says_when_to_use_it_the_recipe_or_meet():
    """sources.md: "When the operator can sign in to Google as a server, they can add a Google
    Calendar source instead, and each event of one calendar arrives as a note of its own
    without anyone asking an agent. Use it for what each meeting was: when and where, who was
    invited and what its description says. Use the recipe above when a week in one note is
    enough, or when you want an agent to choose what matters and write it in its own words,
    and the [Google Meet source](#meeting-transcripts-from-google-meet-read-by-the-server) for
    what was said.\""""
    assert HEADING in RAW
    said(
        "When the operator can sign in to Google as a server, they can add a Google Calendar"
        " source instead, and each event of one calendar arrives as a note of its own without"
        " anyone asking an agent. Use it for what each meeting was: when and where, who was"
        " invited and what its description says. Use the recipe above when a week in one note"
        " is enough, or when you want an agent to choose what matters and write it in its own"
        " words, and the [Google Meet source]"
        "(#meeting-transcripts-from-google-meet-read-by-the-server) for what was said."
    )
    assert RAW.index("## Calendar") < RAW.index(HEADING) < RAW.index("## Documents")


def test_it_reads_the_events_of_one_calendar_into_one_project():
    """sources.md: "A Google Calendar source reads the events of one calendar, and writes each
    event that has ended as a note into one project.\""""
    said(
        "A Google Calendar source reads the events of one calendar, and writes each event that"
        " has ended as a note into one project."
    )
    with pytest.raises(ValueError, match="projects names at most one project"):
        c.check_config(settings(projects=["a", "b"]))
    stand = Stand()
    stand.items = [
        timed(1, SINCE + HOUR, SINCE + 2 * HOUR),
        timed(2, NOW - HOUR, NOW + HOUR),  # not ended yet
    ]
    got = stand.pull(calendar_id="team@group.example.test")
    assert refs(got) == ["calendar:unitev0001"] and {d.project for d in got.drafts} == {"calendar"}
    (listed,) = stand.lists()  # one listing, of the one calendar
    assert "/calendars/team%40group.example.test/events" in listed.url.raw_path.decode()


def test_its_note_is_a_heading_the_facts_and_the_description():
    """sources.md: "Its note has a heading with the event's title, then when it took place (in
    UTC, or its days for an all-day event), where, who organised it, who was invited and the
    Google Meet meeting code when it has one, then its description as text.\""""
    said(
        "Its note has a heading with the event's title, then when it took place (in UTC, or its"
        " days for an all-day event), where, who organised it, who was invited and the Google"
        " Meet meeting code when it has one, then its description as text."
    )
    event = c.read_event(full_event(), None)
    assert event is not None
    lines = c.text(event).splitlines()
    assert lines[0] == "# Quarterly planning"
    assert [line.split(":")[0] for line in lines[2:7]] == [
        "- When",
        "- Where",
        "- Organiser",
        "- Attendees",
        "- Meet",
    ]
    assert lines[2].endswith(" UTC") and lines[-3:] == ["Bring the figures.", "", "- Budget"]
    day = c.read_event(all_day(2, NOW.date(), NOW.date() + 2 * DAY), None)
    assert day is not None
    assert "- When: 2026-11-13 to 2026-11-14, all day" in c.text(day)
    noted("ID is the 10-letter meeting code, for example `aaa-bbbb-ccc`.")


def test_each_event_gets_a_page_named_by_its_title_and_the_day_it_started():
    """sources.md: "Each event's page is named by its title and the day it started, so each
    occurrence of a weekly meeting gets a page of its own.\""""
    said(
        "Each event's page is named by its title and the day it started, so each occurrence of"
        " a weekly meeting gets a page of its own."
    )
    stand = Stand()
    stand.items = [
        timed(n, start, start + HOUR, summary="Weekly sync")
        for n, start in enumerate((SINCE - 7 * DAY, SINCE, SINCE + 2 * HOUR), 1)
    ]
    got = stand.pull(since=SINCE - 8 * DAY)
    assert [d.topic for d in got.drafts] == [
        "Weekly sync, 2026-11-05",
        "Weekly sync, 2026-11-12",
        "Weekly sync, 2026-11-12",  # the same title on the same day: one page
    ]


def test_it_passes_over_private_declined_and_what_is_not_a_meeting():
    """sources.md: "It passes over events marked private, events the calendar's own person
    declined, and events that are not meetings: out of office, focus time, working location,
    birthdays and events from Gmail.\""""
    said(
        "It passes over events marked private, events the calendar's own person declined, and"
        " events that are not meetings: out of office, focus time, working location, birthdays"
        " and events from Gmail."
    )
    declined = [{"email": "ada@example.test", "self": True, "responseStatus": "declined"}]
    for fields in (
        {"visibility": "private"},
        {"attendees": declined},
        *({"eventType": kind} for kind in
          ("outOfOffice", "focusTime", "workingLocation", "birthday", "fromGmail")),
    ):  # fmt: skip
        event = c.read_event(timed(1, SINCE, SINCE + HOUR, **fields), None)
        assert event is not None and c.passed_over(event), fields
    noted('`fromGmail` | "An event from Gmail. This type of event cannot be created."')


def test_switched_on_the_page_proposes_the_project_calendar():
    """sources.md: "When it is switched on, the page proposes the project `calendar`.\""""
    said("When it is switched on, the page proposes the project `calendar`.")
    assert KINDS["google_calendar"].default_project == "calendar"


def test_no_email_address_is_among_the_title_and_the_facts():
    """sources.md: "Names are written as Google gives them, never an email address: someone
    Google gives no name, or whose name holds an address, is counted ("and 2 more"), and an
    address in the title or the location is left out. The description is written with its
    words unchanged, so an address in it meets the note guard as in any note.\""""
    said(
        "Names are written as Google gives them, never an email address: someone Google gives no"
        ' name, or whose name holds an address, is counted ("and 2 more"), and an address in the'
        " title or the location is left out. The description is written with its words"
        " unchanged, so an address in it meets the note guard as in any note."
    )
    item = full_event(
        summary="Plan with dee@example.test",
        location="Room 4, desk@example.test",
        description="Ask eve@example.test first.",
    )
    event = c.read_event(item, None)
    assert event is not None
    written = c.text(event)
    facts, description = written.split("\n\n")[0:2], written.split("\n\n")[2]
    assert "@" not in "\n".join(facts)
    assert "- Attendees: Ada Park, Brook Lane and 2 more" in written
    assert description == "Ask eve@example.test first.\n"
    assert "@" not in c.draft(event, "calendar").title


def test_it_reads_each_event_once_and_does_not_follow_later_edits():
    """sources.md: "An event is read once, as soon as it has ended, and later edits are not
    followed: a change at Google afterwards, or a deletion, changes nothing in cairn. An event
    put on the calendar up to two days after it ended is still read. An event moved more than
    two days later after it was read is read again once it has ended at its new time, as a new
    note.\""""
    said(
        "An event is read once, as soon as it has ended, and later edits are not followed: a"
        " change at Google afterwards, or a deletion, changes nothing in cairn. An event put on"
        " the calendar up to two days after it ended is still read. An event moved more than two"
        " days later after it was read is read again once it has ended at its new time, as a"
        " new note."
    )
    assert c.OVERLAP == 2 * DAY
    stand = Stand()
    edited = timed(1, SINCE + HOUR, SINCE + 2 * HOUR, summary="Kickoff")
    moved = timed(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR, summary="Review")
    gone = timed(3, SINCE + 5 * HOUR, SINCE + 6 * HOUR, summary="Retro")
    stand.items = [edited, moved, gone]
    first = stand.pull()
    assert refs(first) == ["calendar:unitev0001", "calendar:unitev0002", "calendar:unitev0003"]

    edited["summary"] = "Kickoff (edited)"
    gone["status"] = "cancelled"
    late = timed(4, NOW - 2 * HOUR, NOW - HOUR, summary="Added late")
    stand.items.append(late)
    stand.now = NOW + 47 * HOUR  # put on the calendar almost two days after it ended
    second = stand.pull(first.cursor)
    assert refs(second) == ["calendar:unitev0004"]

    later = NOW + 5 * DAY
    moved["start"] = {"dateTime": later.strftime("%Y-%m-%dT%H:%M:%SZ")}
    moved["end"] = {"dateTime": (later + HOUR).strftime("%Y-%m-%dT%H:%M:%SZ")}
    cursor = second.cursor
    for hours in range(48 + 6, 5 * 24, 6):  # a pull every six hours until it has started
        stand.now = NOW + hours * HOUR
        got = stand.pull(cursor)
        assert got.drafts == [], hours
        cursor = got.cursor
    stand.now = later + 2 * HOUR
    again = stand.pull(cursor)
    assert refs(again) == ["calendar:unitev0002"]


def test_only_events_that_ended_after_it_was_switched_on_are_read():
    """sources.md: "Only events that ended after the source was switched on are read, nothing
    from before.\""""
    said("Only events that ended after the source was switched on are read, nothing from before.")
    kind = KINDS["google_calendar"]
    assert kind.starts_off and kind.switching_on() == FROM_THEN_ON
    stand = Stand()
    stand.items = [
        timed(1, SINCE - 2 * HOUR, SINCE - MINUTE),
        timed(2, SINCE - HOUR, SINCE + MINUTE),
    ]
    assert refs(stand.pull()) == ["calendar:unitev0002"]


def test_the_operator_names_one_calendar_by_its_id():
    """sources.md: "The operator names the one calendar it reads by its id, `calendar_id`:
    `primary` unless told otherwise, the calendar of the account it reads as, or another
    calendar's id, which looks like an email address. The account it reads as must see the
    calendar's event details: on a calendar it sees as free/busy only, which hides each
    event's title, the source writes nothing and says to share the details.\""""
    said(
        "The operator names the one calendar it reads by its id, `calendar_id`: `primary`"
        " unless told otherwise, the calendar of the account it reads as, or another calendar's"
        " id, which looks like an email address. The account it reads as must see the"
        " calendar's event details: on a calendar it sees as free/busy only, which hides each"
        " event's title, the source writes nothing and says to share the details."
    )
    noted(
        "If you want to access the primary calendar of the currently logged in user, use the"
        ' "`primary`" keyword.',
        "is identified by an ID, which is an email address",
        "`accessRole` values: `none`, `freeBusyReader`, `reader`",
    )
    assert c.check_config(settings())["calendar_id"] == "primary"
    stand = Stand()
    stand.top = {"timeZone": "UTC", "accessRole": "freeBusyReader"}
    stand.items = [timed(1, SINCE + HOUR, SINCE + 2 * HOUR)]
    with pytest.raises(SourceError) as refused:
        stand.pull(calendar_id="team@group.example.test")
    assert str(refused.value) == c.FREE_BUSY and "share" in c.FREE_BUSY


def test_the_credential_is_json_in_a_variable_with_the_one_scope(keys):  # noqa: F811
    """sources.md: "The source signs in to Google in one of three ways, and needs the scope
    `https://www.googleapis.com/auth/calendar.events.readonly` whichever it is, which Google
    describes as "View events on all your calendars." The Google Calendar API must be enabled
    in the Cloud project of the service account or the OAuth client. Its credential is the text
    of a JSON document in a variable of the server's environment, never in cairn and never a
    path to a file: with Docker, a line in `deploy/cairn.env` with the JSON on one line
    between single quotes.\""""
    said(
        "The source signs in to Google in one of three ways, and needs the scope"
        " `https://www.googleapis.com/auth/calendar.events.readonly` whichever it is, which"
        ' Google describes as "View events on all your calendars." The Google Calendar API must'
        " be enabled in the Cloud project of the service account or the OAuth client. Its"
        " credential is the text of a JSON document in a variable of the server's environment,"
        " never in cairn and never a path to a file: with Docker, a line in `deploy/cairn.env`"
        " with the JSON on one line between single quotes."
    )
    noted(
        '| `calendar.events.readonly` | "View events on all your calendars." |',
        '"In the Google Cloud console, enable the Google Calendar API."',
    )
    assert c.SCOPES == ["https://www.googleapis.com/auth/calendar.events.readonly"]
    _, pem = keys
    line = json.dumps(key_file(pem))
    assert "\n" not in line
    assert isinstance(auth.load_credential("V", {"V": line}), auth.ServiceAccount)
    with pytest.raises(SourceError, match="not a path"):
        auth.load_credential("V", {"V": "/run/secrets/google.json"})
    with pytest.raises(ValueError, match="credentials_env holds what looks like a credential"):
        c.check_config(settings(credentials_env=line))


def test_with_a_service_account_a_super_admin_delegates_and_subject_names_the_person(keys):  # noqa: F811
    """sources.md, the first way: a key Google keeps no copy of, which a newer organization
    blocks unless its admin allows it; delegation by a super admin with the numeric Client ID
    and the scope, which can take up to 24 hours; the key file in the variable and `subject`
    in the config."""
    here(
        "With a service account acting for one person, for a Google Workspace domain whose"
        " super admin agrees:"
    )
    here(
        "1. In a Google Cloud project, create a service account and a JSON key for it. Google"
        " keeps no copy of the key, and an organization created on or after May 3, 2024 blocks"
        " key creation unless its admin allows it."
    )
    here(
        "2. A super admin of the domain opens the Admin console, then Security, Access and data"
        " control, API Controls, **Manage Domain Wide Delegation**, and adds the service"
        " account's numeric **Client ID** (not its email address) with the scope above. It"
        " usually takes a few minutes and can take up to 24 hours."
    )
    here(
        "3. The variable holds the key file's contents, and `subject` in the source's config is"
        " the address of the person whose calendar it reads."
    )
    noted(
        'Google "does not keep a copy of your private keys"',
        "organization was created on or after May 3, 2024, this constraint is enforced by default.",
        "Main menu > Security > Access and data control > API Controls",
        "Using the client email instead of the numeric ID, or a Google Group, gives"
        " `unauthorized_client`",
        '"usually takes a few minutes ... might take up to 24 hours"',
        notes=AUTH_NOTES,
    )
    _, pem = keys
    env = {"V": json.dumps(key_file(pem))}
    config = c.check_config(settings(credentials_env="V", subject="ada@example.test"))
    assert c.open_source("diary", config, http=None, env=env).subject == "ada@example.test"
    credential = auth.load_credential("V", env)
    signed = auth.assertion(credential, c.SCOPES, "ada@example.test", 1_700_000_000)
    claims = json.loads(_segment(signed.split(".")[1]))
    assert claims["sub"] == "ada@example.test" and claims["scope"] == c.SCOPE


def _segment(segment: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def test_with_a_service_account_a_calendar_shared_with_its_address_needs_no_delegation(keys):  # noqa: F811
    """sources.md, the second way: the calendar shared with the service account's address,
    no delegation, no `subject`, and a `calendar_id` other than `primary`."""
    here("With a service account and a calendar shared with it, when no super admin delegates:")
    here("1. Create a service account and a JSON key for it, as in the first way.")
    here(
        "2. The calendar's owner shares the calendar with the service account's address,"
        " `client_email` in the key file, so that it sees event details and not only free/busy."
        " No domain-wide delegation is needed. Google's pages do not say what a service account"
        " sees of a calendar shared with it."
    )
    here(
        "3. The variable holds the key file's contents, the config has no `subject`, and"
        " `calendar_id` is the shared calendar's id. A service account has no calendar of its"
        " own to read, so `primary` is refused."
    )
    noted(
        "a calendar shared directly with the service account's address is the only way it could"
        " see anything, and what it then sees is **[not stated]**.",
    )
    noted('"client_email": "SERVICE_ACCOUNT_EMAIL",', notes=AUTH_NOTES)
    _, pem = keys
    env = {"V": json.dumps(key_file(pem))}
    shared = c.check_config(settings(credentials_env="V", calendar_id="team@group.example.test"))
    assert c.open_source("diary", shared, http=None, env=env).subject is None
    with pytest.raises(SourceError, match="no calendar of its own"):
        c.open_source("diary", c.check_config(settings(credentials_env="V")), http=None, env=env)
    credential = auth.load_credential("V", env)
    signed = auth.assertion(credential, c.SCOPES, None, 1_700_000_000)
    assert "sub" not in json.loads(_segment(signed.split(".")[1]))


def test_with_ones_own_authorisation_the_variable_holds_a_refresh_token_and_no_subject():
    """sources.md, the third way: a Desktop app client, `google-authorize` with `--kind
    google_calendar` on the person's computer, the file's one line in the variable without
    `subject`, and where the guide says how long the token lasts (pinned with the command in
    test_guide_facts_google_authorize_unit.py)."""
    here("With one person's own authorisation, when there is no super admin to ask:")
    for sentence in own_authorisation("google_calendar", section(), c.SCOPES):
        here(sentence)
    here(
        '3. The variable holds the file\'s one line, `{"type": "authorized_user", "client_id":'
        ' "...", "client_secret": "...", "refresh_token": "..."}`, and the config has no'
        " `subject`. Then delete the file."
    )
    env = {VAR: CREDENTIAL}
    with pytest.raises(SourceError, match="remove subject"):
        c.open_source(
            "diary", c.check_config(settings(subject="ada@example.test")), http=None, env=env
        )
    assert c.open_source("diary", c.check_config(settings()), http=None, env=env).subject is None
    assert "authorise again" in auth._AUTHORIZED_USER_SAID["invalid_grant"]


def test_the_command_adds_it_off_and_the_settings_are_as_the_guide_lists_them():
    """sources.md: the source-add command, then "`credentials_env` is the one setting it needs;
    `subject` (with a service account acting for a person), `calendar_id` (`primary`),
    `base_url` and `projects` are optional." ... "The Sources page shows which calendar it reads
    and as whom, the `calendar_id` and the `subject`, and never the credential or its
    variable.\""""
    said("Then the operator adds the source, which starts off:")
    (block,) = [
        part.split("```", 1)[0] for part in section().split("```text\n")[1:] if "source-add" in part
    ]
    argv = shlex.split(block.strip())
    assert argv[:5] == ["python", "-m", "cairn.server", "source-add", "--db"]
    assert argv[argv.index("--kind") + 1] == "google_calendar"
    config = json.loads(argv[argv.index("--config") + 1])
    checked = c.check_config(config)
    assert checked["credentials_env"] == "GOOGLE_CALENDAR_CREDENTIAL"
    assert checked["projects"] == [] and checked["calendar_id"] == "primary"
    assert config["subject"].endswith("@example.com")
    said(
        "`credentials_env` is the one setting it needs; `subject` (with a service account acting"
        " for a person), `calendar_id` (`primary`), `base_url` and `projects` are optional."
    )
    assert set(c.SETTINGS) == {"credentials_env", "subject", "calendar_id", "base_url", "projects"}
    said(
        "Then an owner or an admin switches it on under **Workspace**, then **Sources**, and"
        " chooses its project ([Sources the server reads](#sources-the-server-reads))."
    )
    said(
        "The Sources page shows which calendar it reads and as whom, the `calendar_id` and the"
        " `subject`, and never the credential."
    )
    shown = c.describe(checked)
    assert "primary" in shown and config["subject"] in shown
    assert "GOOGLE_CALENDAR_CREDENTIAL" not in shown and "credentials_env" not in shown


def test_the_other_guides_list_it_among_the_sources_the_server_reads():
    """run.md: "A source (Langfuse, Granola, Google Meet, Google Drive, Google Calendar, Gmail,
    Linear, GitHub or a connector of your own) is read by the server itself" ... README.md:
    "(Langfuse traces, Granola meeting notes, Google Meet transcripts, the documents of a
    Google Drive folder, Google Calendar events, the Gmail messages a person labels, Linear
    issues once they are finished, the finished work of one GitHub repository, and any other
    system through a connector of your own)". deploy/README.md: "A source is a system the
    server reads notes from on its own: Langfuse, Granola, Google Meet, Google Drive, Google
    Calendar, Gmail, Linear, GitHub or a connector of your own." ... "A Google Calendar source
    starts off too, and then reads only events that end from then on; its variable holds a
    Google credential's JSON in the same way." tests/unit/test_kind_lists_unit.py holds every
    such list to KINDS."""
    run = " ".join(files("cairn.guides").joinpath("run.md").read_text().split())
    readme = " ".join((ROOT / "README.md").read_text().split())
    deploy = " ".join((ROOT / "deploy" / "README.md").read_text().split())
    assert (
        "A source (Langfuse, Granola, Google Meet, Google Drive, Google Calendar, Gmail, Linear,"
        " GitHub or a connector of your own) is read by the server itself" in run
    )
    assert (
        "(Langfuse traces, Granola meeting notes, Google Meet transcripts, the documents of a"
        " Google Drive folder, Google Calendar events, the Gmail messages a person labels,"
        " Linear issues once they are finished, the finished work of one GitHub repository, and"
        " any other system through a connector of your own)" in readme
    )
    assert (
        "A source is a system the server reads notes from on its own: Langfuse, Granola, Google"
        " Meet, Google Drive, Google Calendar, Gmail, Linear, GitHub or a connector of your own."
        in deploy
    )
    assert (
        "A Google Calendar source starts off too, and then reads only events that end from then"
        " on; its variable holds a Google credential's JSON in the same way." in deploy
    )
    assert KINDS["google_calendar"].starts_off
