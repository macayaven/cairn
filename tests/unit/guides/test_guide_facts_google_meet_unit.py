"""Sentences of src/cairn/guides/sources.md (and deploy/README.md) about the Google Meet
source, each pinned here so the guide cannot go false unnoticed: the sentence is in the
test's docstring and in the guide, and the test asks the running code for it, or, for what
Google says, the notes the guide was written from (docs/google-meet-api.md and
docs/google-auth.md). Google is a stand-in; nothing leaves the process. Every name, address
and key is made up, and the key pair is made at test time."""

import json
import shlex

import httpx
import pytest

from cairn.sources import FROM_THEN_ON, KINDS, SourceError
from cairn.sources import google_auth as auth
from cairn.sources import google_meet as m
from support.google_authorize_guide import own_authorisation
from support.guide_source import guide_source
from support.paths import REPO_ROOT
from support.sources.google_auth import key_file, keys, user  # noqa: F401 (keys is a fixture)
from support.sources.google_meet import (
    DAY,
    HOUR,
    MINUTE,
    NOW,
    PEOPLE,
    PEOPLE_NAME,
    SINCE,
    Stand,
    meeting,
    refs,
    settings,
    transcript,
)

ROOT = REPO_ROOT
RAW = guide_source("sources")
GUIDE = " ".join(RAW.split())
NOTES = " ".join((ROOT / "docs" / "google-meet-api.md").read_text().split())
AUTH_NOTES = " ".join((ROOT / "docs" / "google-auth.md").read_text().split())
HEADING = "### Meeting transcripts from Google Meet, read by the server"


def said(sentence: str) -> None:
    """The guide says `sentence`, word for word (whitespace aside)."""
    assert " ".join(sentence.split()) in GUIDE, sentence


def section() -> str:
    """The Meet section of the guide, to its last line."""
    start = RAW.index(HEADING)
    return RAW[start : RAW.index("\n## ", start)]


def noted(*quotes: str, notes: str = NOTES) -> None:
    for quote in quotes:
        assert " ".join(quote.split()) in notes, quote


def test_the_guide_says_when_to_use_meet_granola_or_an_agent():
    """sources.md: "When a team meets on Google Meet, the operator can add a Google Meet
    source, and each meeting's transcript arrives as a note without anyone asking an agent.
    Use it for what was said, a line a turn. Use the Granola source when the team keeps its
    notes in Granola and wants Granola's summaries, and the recipe above when you want an
    agent to choose what matters and write it in its own words.\""""
    assert HEADING in RAW
    said(
        "When a team meets on Google Meet, the operator can add a Google Meet source, and each"
        " meeting's transcript arrives as a note without anyone asking an agent. Use it for"
        " what was said, a line a turn. Use the Granola source when the team keeps its notes in"
        " Granola and wants Granola's summaries, and the recipe above when you want an agent to"
        " choose what matters and write it in its own words."
    )
    assert {"google_meet", "granola"} <= set(KINDS)
    assert RAW.index("### Meetings from Granola") < RAW.index(HEADING) < RAW.index("## Mail")


def test_it_reads_the_meetings_google_lists_for_one_person_into_one_project():
    """sources.md: "A Google Meet source reads the meetings Google Meet lists for one person,
    or the one meeting whose code it names, and writes each transcript as a note into one
    project.\""""
    said(
        "A Google Meet source reads the meetings Google Meet lists for one person, or the one"
        " meeting whose code it names, and writes each transcript as a note into one project."
    )
    with pytest.raises(ValueError, match="projects names at most one project"):
        m.check_config(settings(projects=["a", "b"]))
    stand = Stand()
    stand.add(1, SINCE + HOUR, SINCE + 2 * HOUR)
    got = stand.pull()
    assert refs(got) == ["meet:call001/t1"] and {d.project for d in got.drafts} == {"calls"}
    (listed,) = stand.of("records")  # one listing: the person's, nobody else's
    assert listed.url.path == "/v2/conferenceRecords"
    stand.answers["space"] = lambda request: httpx.Response(  # spaces/{meetingCode} (SP-GET)
        200, json={"name": "spaces/space-call001", "meetingCode": "abc-defg-hij"}
    )
    got = stand.pull(meeting_code="abc-defg-hij")  # one meeting: its code in the listing
    assert refs(got) == ["meet:call001/t1"]
    (listed,) = stand.of("records")
    assert listed.url.params["filter"].endswith(' AND space.meeting_code = "abc-defg-hij"')


def test_its_note_is_a_heading_the_facts_and_a_line_a_turn():
    """sources.md: "Its note has a heading with the meeting code and when the meeting started,
    then when it started and ended, when it was transcribed and who took part, then a line a
    turn: the time since the meeting started, who spoke and what they said.\""""
    said(
        "Its note has a heading with the meeting code and when the meeting started, then when it"
        " started and ended, when it was transcribed and who took part, then a line a turn: the"
        " time since the meeting started, who spoke and what they said."
    )
    start = meeting().start
    entries = [m.Entry(PEOPLE_NAME("p1"), "Morning.", start + MINUTE)]
    (draft,) = m.drafts(
        meeting(), transcript(), entries, list(PEOPLE.values()), "abc-mnop-xyz", "b"
    )
    lines = draft.text.splitlines()
    assert lines[0] == f"# Meet abc-mnop-xyz, {start:%Y-%m-%d %H:%M} UTC"
    assert [line.split(":")[0] for line in lines[2:6]] == [
        "- Started",
        "- Ended",
        "- Transcribed",
        "- Participants",
    ]
    assert "Ada Park" in lines[5]
    assert lines[-1] == "- 00:01:00 Ada Park: Morning."


def test_a_meeting_gets_a_page_named_by_its_code_and_its_start():
    """sources.md: "The Meet API gives a meeting no title, so each meeting's page is named by
    its meeting code and its start in UTC, and a meeting gets a page of its own.\""""
    said(
        "The Meet API gives a meeting no title, so each meeting's page is named by its meeting"
        " code and its start in UTC, and a meeting gets a page of its own."
    )
    noted("No meeting title exists anywhere in the Meet API")
    stand = Stand()
    said_once = [("p1", "Hello.", SINCE + HOUR + MINUTE)]
    stand.add(
        1,
        SINCE + HOUR,
        SINCE + 2 * HOUR,
        transcripts=[("t1", "FILE_GENERATED", said_once), ("t2", "FILE_GENERATED", said_once)],
    )
    stand.add(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR, code="abc-defg-hij")  # the code reused
    got = stand.pull()
    topics = [d.topic for d in got.drafts]
    assert topics[0] == topics[1] == f"Meet abc-defg-hij, {SINCE + HOUR:%Y-%m-%d %H:%M} UTC"
    assert topics[2] == f"Meet abc-defg-hij, {SINCE + 3 * HOUR:%Y-%m-%d %H:%M} UTC"


def test_names_are_written_as_meet_shows_them_and_never_an_address():
    """sources.md: "Names are written as Meet shows them, never an email address: someone
    Meet gives no name, or a name that holds an address, is "Unknown speaker".\""""
    said(
        "Names are written as Meet shows them, never an email address: someone Meet gives no"
        ' name, or a name that holds an address, is "Unknown speaker".'
    )
    stand = Stand()
    start = SINCE + HOUR
    turns = [
        ("p1", "Hi.", start + MINUTE),
        ("p2", "Yo.", start + 2 * MINUTE),
        ("p3", "Hey.", start + 3 * MINUTE),
    ]
    people = [("p1", "Ada Park"), ("p2", "dee@example.test"), ("p3", None)]
    stand.add(1, start, start + HOUR, people=people, transcripts=[("t1", "FILE_GENERATED", turns)])
    (draft,) = stand.pull().drafts
    assert "@" not in draft.text
    assert "- 00:01:00 Ada Park: Hi." in draft.text
    assert "- 00:02:00 Unknown speaker: Yo." in draft.text
    assert "- 00:03:00 Unknown speaker: Hey." in draft.text


def test_someone_who_did_not_sign_in_is_marked_on_every_line():
    """sources.md: "Someone who did not sign in has "(anonymous)" or "(by phone)" after their
    name on every line, so a guest who types the name of someone who signed in is not written
    as that person.\""""
    said(
        'Someone who did not sign in has "(anonymous)" or "(by phone)" after their name on every'
        " line, so a guest who types the name of someone who signed in is not written as that"
        " person."
    )
    start = meeting().start
    people = [
        m.Person(PEOPLE_NAME("p1"), "Ada Park", "signedinUser"),
        m.Person(PEOPLE_NAME("p2"), "Ada Park", "anonymousUser"),
        m.Person(PEOPLE_NAME("p3"), "Ada Park", "phoneUser"),
    ]
    entries = [
        m.Entry(PEOPLE_NAME(p), f"Turn {i}.", start + (i + 1) * MINUTE)
        for i, p in enumerate(("p1", "p2", "p3", "p2"))
    ]
    (draft,) = m.drafts(meeting(), transcript(), entries, people, None, "b")
    assert draft.text.splitlines()[-4:] == [
        "- 00:01:00 Ada Park: Turn 0.",
        "- 00:02:00 Ada Park (anonymous): Turn 1.",
        "- 00:03:00 Ada Park (by phone): Turn 2.",
        "- 00:04:00 Ada Park (anonymous): Turn 3.",
    ]


def test_a_long_transcript_is_written_in_parts():
    """sources.md: "A transcript of more than 1,500 turns is written in parts.\""""
    said("A transcript of more than 1,500 turns is written in parts.")
    assert m.TURNS_PER_PART == 1_500
    start = meeting().start
    people = [m.Person(PEOPLE_NAME(p), p.upper(), "signedinUser") for p in ("p1", "p2")]
    entries = [m.Entry(people[i % 2].name, f"Turn {i}.", start + i * MINUTE) for i in range(1_501)]
    made = m.drafts(meeting(), transcript(), entries, people, None, "b")
    assert [d.source_ref.count("/") for d in made] == [1, 2]


def test_switched_on_the_page_proposes_the_project_meet_transcripts():
    """sources.md: "When it is switched on, the page proposes the project
    `meet-transcripts`.\""""
    said("When it is switched on, the page proposes the project `meet-transcripts`.")
    assert KINDS["google_meet"].default_project == "meet-transcripts"


def test_a_meeting_has_a_transcript_only_if_google_made_one():
    """sources.md: "A meeting has a transcript only if someone in it started transcription, or
    an admin made transcription automatic, and only on a Google Workspace edition that has
    transcripts." Google's statements, recorded in docs/google-meet-api.md (I)."""
    said(
        "A meeting has a transcript only if someone in it started transcription, or an admin"
        " made transcription automatic, and only on a Google Workspace edition that has"
        " transcripts."
    )
    noted(
        "a person in the meeting has to start transcription (paraphrased).",
        '"Meetings are transcribed by default"',
        "Learn which Google Workspace editions have Transcripts",
    )


def test_it_looks_until_wait_hours_after_the_end_and_reads_each_transcript_once():
    """sources.md: "Google can take up to 24 hours to generate the file, so the source looks
    at each meeting at every run until `wait_hours` after it ended (48 unless set, from 1 to
    168), and reads each transcript once, as soon as its file is generated: a change at
    Google afterwards changes nothing in cairn. A transcript still not generated by then is
    reported once and not read.\""""
    said(
        "Google can take up to 24 hours to generate the file, so the source looks at each"
        " meeting at every run until `wait_hours` after it ended (48 unless set, from 1 to"
        " 168), and reads each transcript once, as soon as its file is generated: a change at"
        " Google afterwards changes nothing in cairn. A transcript still not generated by then"
        " is reported once and not read."
    )
    noted("it can take up to 24 hours")
    assert m.check_config(settings())["wait_hours"] == 48
    for hours in (0, 169):
        with pytest.raises(ValueError, match="from 1 to 168"):
            m.check_config(settings(wait_hours=hours))
    stand = Stand()
    end = NOW - 2 * HOUR
    call = stand.add(1, end - HOUR, end, state="ENDED")
    stand.add(2, end - HOUR, end + MINUTE, state="ENDED")
    first = stand.pull()
    assert first.drafts == [] and set(json.loads(first.cursor)["waiting"]) == {
        "call001",
        "call002",
    }
    stand.now = end + 40 * HOUR  # generated at last, within the 48 hours
    call.transcripts = [("t1", "FILE_GENERATED", call.transcripts[0][2])]
    second = stand.pull(first.cursor)
    assert refs(second) == ["meet:call001/t1"]
    call.transcripts = [("t1", "FILE_GENERATED", [("p1", "Edited.", end - MINUTE)])]
    stand.now = end + 49 * HOUR
    third = stand.pull(second.cursor)
    assert third.drafts == []  # read once: the edit changes nothing
    assert [(s.ref, s.reason) for s in third.skipped] == [("meet:call002", m.NOT_GENERATED)]
    stand.now += HOUR
    assert stand.pull(third.cursor).skipped == []  # reported once


def test_only_meetings_that_ended_after_it_was_switched_on_are_read():
    """sources.md: "Only meetings that ended after the source was switched on are read,
    nothing from before.\""""
    said("Only meetings that ended after the source was switched on are read, nothing from before.")
    kind = KINDS["google_meet"]
    assert kind.starts_off and kind.switching_on() == FROM_THEN_ON
    stand = Stand()
    stand.add(1, SINCE - 2 * HOUR, SINCE - MINUTE)
    stand.add(2, SINCE - HOUR, SINCE + MINUTE)
    assert refs(stand.pull()) == ["meet:call002/t1"]


def test_google_deletes_after_30_days_and_a_run_picks_up_what_ended_since_the_last():
    """sources.md: "Google deletes a meeting's record and its transcript entries 30 days after
    the meeting ends, so the server must sync within that time: a run picks up the meetings
    since the last run that finished, as long as Google still has them.\""""
    said(
        "Google deletes a meeting's record and its transcript entries 30 days after the meeting"
        " ends, so the server must sync within that time: a run picks up the meetings since the"
        " last run that finished, as long as Google still has them."
    )
    noted(
        "The resource is deleted 30 days after the conference ends.",
        "Transcript entries provided by the Meet REST API are deleted 30 days after the"
        " conference ends.",
    )
    stand = Stand()
    first = stand.pull()
    stand.now = NOW + 20 * DAY  # no run for twenty days
    stand.add(1, NOW + 5 * DAY, NOW + 5 * DAY + HOUR)
    got = stand.pull(first.cursor, since=SINCE)
    assert refs(got) == ["meet:call001/t1"]
    (listed,) = stand.of("records")
    assert listed.url.params["filter"] == f'start_time>="{m._stamp(NOW - DAY)}"'


def test_the_credential_is_json_in_a_variable_of_the_servers_environment(keys):  # noqa: F811
    """sources.md: "The source signs in to Google in one of two ways, and needs the scope
    `https://www.googleapis.com/auth/meetings.space.readonly` either way. Its credential is the
    text of a JSON document in a variable of the server's environment, never in cairn and never
    a path to a file: with Docker, a line in `deploy/cairn.env` with the JSON on one line
    between single quotes." (That Compose reads such a line, quotes removed and the key's
    `\\n` kept, was checked by hand with `docker compose config`, Compose v5.1.2.)"""
    said(
        "The source signs in to Google in one of two ways, and needs the scope"
        " `https://www.googleapis.com/auth/meetings.space.readonly` either way. Its credential"
        " is the text of a JSON document in a variable of the server's environment, never in"
        " cairn and never a path to a file: with Docker, a line in `deploy/cairn.env` with the"
        " JSON on one line between single quotes."
    )
    assert m.SCOPES == ["https://www.googleapis.com/auth/meetings.space.readonly"]
    _, pem = keys
    line = json.dumps(key_file(pem))
    assert "\n" not in line  # json.dumps writes the key's line breaks as \n
    assert isinstance(auth.load_credential("V", {"V": line}), auth.ServiceAccount)
    with pytest.raises(SourceError, match="not a path"):
        auth.load_credential("V", {"V": "/run/secrets/google.json"})
    with pytest.raises(ValueError, match="credentials_env holds what looks like a credential"):
        m.check_config(settings(credentials_env=line))


def test_with_a_service_account_a_super_admin_delegates_and_subject_names_the_person(keys):  # noqa: F811
    """sources.md, the service account's three steps: a key Google keeps no copy of, which a
    newer organization blocks unless its admin allows it; delegation by a super admin with the
    numeric Client ID and the scope, which can take up to 24 hours; the key file in the
    variable and `subject` in the config."""
    said("With a service account, for a Google Workspace domain whose super admin agrees:")
    said(
        "1. In a Google Cloud project, create a service account and a JSON key for it. Google"
        " keeps no copy of the key, and an organization created on or after May 3, 2024 blocks"
        " key creation unless its admin allows it."
    )
    said(
        "2. A super admin of the domain opens the Admin console, then Security, Access and data"
        " control, API Controls, **Manage Domain Wide Delegation**, and adds the service"
        " account's numeric **Client ID** (not its email address) with the scope above. It"
        " usually takes a few minutes and can take up to 24 hours."
    )
    said(
        "3. The variable holds the key file's contents, and `subject` in the source's config is"
        " the address of the person whose meetings it reads."
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
    with pytest.raises(SourceError, match="needs subject"):
        m.open_source("calls", m.check_config(settings(credentials_env="V")), http=None, env=env)
    credential = auth.load_credential("V", env)
    signed = auth.assertion(credential, m.SCOPES, "ada@example.test", 1_700_000_000)
    claims = json.loads(auth_segment(signed.split(".")[1]))
    assert claims["sub"] == "ada@example.test" and claims["scope"] == m.SCOPE


def auth_segment(segment: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def test_with_ones_own_authorisation_the_variable_holds_a_refresh_token_and_no_subject():
    """sources.md, one person's own authorisation: a Desktop app client, `google-authorize`
    with `--kind google_meet` on the person's computer, the file's one line in the variable
    without `subject`, and where the guide says how long the token lasts (pinned with the
    command in test_guide_facts_google_authorize_unit.py)."""
    text = section()
    said("With one person's own authorisation, when there is no super admin to ask:")
    own_authorisation("google_meet", text, m.SCOPES)  # each step in this section
    step3 = (
        '3. The variable holds the file\'s one line, `{"type": "authorized_user", "client_id":'
        ' "...", "client_secret": "...", "refresh_token": "..."}`, and the config has no'
        " `subject`. Then delete the file."
    )
    assert " ".join(step3.split()) in " ".join(text.split())  # here, not another section
    env = {"V": json.dumps(user())}
    with pytest.raises(SourceError, match="remove subject"):
        m.open_source(
            "calls",
            m.check_config(settings(credentials_env="V", subject="ada@example.test")),
            http=None,
            env=env,
        )
    assert "authorise again" in auth._AUTHORIZED_USER_SAID["invalid_grant"]


def test_the_command_adds_it_off_and_the_settings_are_as_the_guide_lists_them():
    """sources.md: the source-add command, then "`credentials_env` is the one setting it needs;
    `subject` (with a service account), `meeting_code` (one meeting, `abc-mnop-xyz`),
    `wait_hours` (48), `base_url` and `projects` are optional.\""""
    said("Then the operator adds the source, which starts off:")
    (block,) = [
        part.split("```", 1)[0] for part in section().split("```text\n")[1:] if "source-add" in part
    ]
    argv = shlex.split(block.strip())
    assert argv[:5] == ["python", "-m", "cairn.server", "source-add", "--db"]
    assert argv[argv.index("--kind") + 1] == "google_meet"
    config = json.loads(argv[argv.index("--config") + 1])
    checked = m.check_config(config)
    assert checked["credentials_env"] == "GOOGLE_MEET_CREDENTIAL" and checked["projects"] == []
    assert config["subject"].endswith("@example.com")
    said(
        "`credentials_env` is the one setting it needs; `subject` (with a service account),"
        " `meeting_code` (one meeting, `abc-mnop-xyz`), `wait_hours` (48), `base_url` and"
        " `projects` are optional."
    )
    assert set(m.SETTINGS) == {
        "credentials_env", "subject", "meeting_code", "wait_hours", "base_url", "projects",
    }  # fmt: skip
    assert m.check_config({**config, "meeting_code": "abc-mnop-xyz"})["meeting_code"]
    with pytest.raises(ValueError, match="needs credentials_env"):
        m.check_config({})
    assert KINDS["google_meet"].starts_off


def test_the_page_shows_whose_meetings_it_reads_and_never_the_credential():
    """sources.md: "The Sources page shows whose meetings it reads, the `subject`, and never
    the credential.\""""
    said("The Sources page shows whose meetings it reads, the `subject`, and never the credential.")
    config = m.check_config(settings(subject="ada@example.test"))
    shown = KINDS["google_meet"].describe(config)
    assert "ada@example.test" in shown and config["credentials_env"] not in shown


def test_the_deploy_guide_says_a_meet_source_starts_off_and_where_its_credential_goes():
    """deploy/README.md: "A Google Meet source starts off too, and then reads only meetings
    that end from then on; its variable holds a Google credential's JSON, on one line between
    single quotes in `deploy/cairn.env`.\""""
    deploy = " ".join((ROOT / "deploy" / "README.md").read_text().split())
    assert (
        "A Google Meet source starts off too, and then reads only meetings that end from then"
        " on; its variable holds a Google credential's JSON, on one line between single quotes"
        " in `deploy/cairn.env`." in deploy
    )
    assert "A source is a system the server reads notes from on its own: Langfuse, Granola" in (
        deploy
    )
    assert KINDS["google_meet"].starts_off
