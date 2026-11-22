"""Unit tests for the Google Calendar source: what tests/acceptance/test_c5_google_calendar.py
does not pin. HTTP goes through httpx.MockTransport; time, sleep and the backoff's jitter are
injected. The credential is a made-up refresh token, so no key is generated here; every name,
address and id is made up."""

import asyncio
import json
import logging
import tomllib
from datetime import UTC, date, datetime, time, timedelta, timezone
from functools import partial
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx
import pytest
from linear_cost import assert_near_linear

from cairn.compiler import slugify
from cairn.sources import FROM_THEN_ON, SourceError, holds_secret
from cairn.sources import google_calendar as c

VAR = "GOOGLE_UNIT_CALENDAR"
CLIENT_ID = "unit-" + "calendar-client" + ".apps.example.test"
SECRET = "GOCSPX-" + "unitCal" + "MadeUpSecret"
REFRESH = "1//" + "0unitCal" + "MadeUpRefresh"
TOKEN = "ya29." + "unitCal" + "MadeUpAccessToken"
CREDENTIAL = json.dumps(
    {
        "type": "authorized_user",
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "refresh_token": REFRESH,
    }
)
BASE = "https://calendar.example.test/calendar/v3"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=1)
MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)
CANARY = "unit-" + "calendar-body-" + "canary"
ROOT = Path(__file__).parents[2]


def settings(**given) -> dict:
    return {
        key: value
        for key, value in {"credentials_env": VAR, "projects": ["calendar"], **given}.items()
        if value is not None
    }


def refusal(config) -> str:
    with pytest.raises(ValueError) as refused:
        c.check_config(config)
    return str(refused.value)


def z(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def timed(n: int, start: datetime, end: datetime, **fields) -> dict:
    """An event as Google lists it, with the fields given."""
    return {
        "kind": "calendar#event",
        "id": f"unitev{n:04d}",
        "status": "confirmed",
        "summary": f"Event {n}",
        "start": {"dateTime": z(start)},
        "end": {"dateTime": z(end)},
        **fields,
    }


def all_day(n: int, first: date, after: date, **fields) -> dict:
    return {
        "kind": "calendar#event",
        "id": f"unitev{n:04d}",
        "status": "confirmed",
        "summary": f"Day {n}",
        "start": {"date": first.isoformat()},
        "end": {"date": after.isoformat()},
        **fields,
    }


def error(status: int, reason: str = "backendError") -> httpx.Response:
    item = {"domain": "global", "reason": reason, "message": CANARY}
    return httpx.Response(
        status, json={"error": {"errors": [item], "code": status, "message": CANARY}}
    )


# The kind and its config


def test_the_kind_starts_off_proposes_its_project_and_says_what_switching_on_reads():
    kind = c.KIND
    assert kind.check is c.check_config and kind.open is c.open_source
    assert kind.describe is c.describe
    assert kind.starts_off and kind.via == "google-calendar" == c.LABEL
    assert kind.default_project == "calendar" and kind.switch_on is None
    assert kind.switching_on() == FROM_THEN_ON
    assert c.SCOPES == ["https://www.googleapis.com/auth/calendar.events.readonly"]


def test_tzdata_is_declared_so_every_zone_is_found_on_every_machine():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert any(d.startswith("tzdata>=") for d in project["dependencies"])
    assert c.zone_of("Pacific/Pago_Pago") == ZoneInfo("Pacific/Pago_Pago")


def test_the_defaults_are_filled_in_and_what_is_stored_checks_the_same():
    checked = c.check_config({"credentials_env": VAR})
    assert checked == {
        "credentials_env": VAR,
        "calendar_id": "primary",
        "projects": [],
        "base_url": "https://www.googleapis.com/calendar/v3",
    }
    assert c.check_config(checked) == checked
    kept = c.check_config(settings(subject="ada@example.test", base_url=BASE + "/"))
    assert (kept["subject"], kept["base_url"]) == ("ada@example.test", BASE)


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"credentials_env": None}, "a google_calendar source needs credentials_env"),
        ({"credentials_env": "lower"}, "credentials_env names an environment variable"),
        ({"subject": "ada"}, "subject is one email address"),
        ({"subject": "a" * 250 + "@example.test"}, "subject is one email address"),
        ({"calendar_id": ""}, "calendar_id is the id of one calendar"),
        ({"calendar_id": "c" * 255}, "calendar_id is the id of one calendar"),
        ({"calendar_id": "a b"}, "calendar_id is the id of one calendar"),
        ({"calendar_id": "a\u00a0b"}, "calendar_id is the id of one calendar"),
        ({"calendar_id": "a\x7fb"}, "calendar_id is the id of one calendar"),
        ({"calendar_id": None}, ""),  # left out: "primary"
        ({"projects": ["a", "b"]}, "projects names at most one project"),
        ({"projects": ["Not a name!"]}, "projects holds a project's name"),
        ({"projects": "calendar"}, "projects is a list of at most one project's name"),
        ({"base_url": "http://calendar.example.test/v3"}, "base_url is https"),
        ({"base_url": 7}, "base_url is the address of the Google Calendar API"),
    ],
)
def test_each_refusal_names_its_setting_and_says_what_it_takes(given, said):
    if not said:
        assert c.check_config(settings(**given))["calendar_id"] == "primary"
        return
    assert refusal(settings(**given)).startswith(said)


@pytest.mark.parametrize("odd", [".", ".."])
def test_a_calendar_id_a_url_would_resolve_away_is_refused(odd):
    """quote(safe="") leaves "." and ".." as they are, and the URL then resolves them:
    calendar_id ".." made the listing ask for /calendar/v3/events, not a calendar's events."""
    assert refusal(settings(calendar_id=odd)).startswith("calendar_id is the id of one calendar")


def test_an_unknown_setting_is_named_unless_its_name_could_be_a_credential():
    assert refusal(settings(color="blue")) == (
        "a google_calendar source has no setting 'color': it takes credentials_env, subject,"
        " calendar_id, projects, base_url"
    )
    pasted = "ya29." + "unitCalPasted" + "Token"
    said = refusal({**settings(), pasted: 1})
    assert pasted not in said and said.startswith("a google_calendar source does not take")


@pytest.mark.parametrize(
    "setting", ["credentials_env", "subject", "calendar_id", "projects", "base_url"]
)
@pytest.mark.parametrize("value", [CREDENTIAL, "-----BEGIN PRIVATE KEY-----\nMII", TOKEN, REFRESH])
def test_a_credential_in_any_setting_is_refused_without_repeating_it(setting, value):
    given = settings(**{setting: [value] if setting == "projects" else value})
    said = refusal(given)
    assert setting in said
    for secret in (TOKEN, SECRET, REFRESH, "MII"):
        assert secret not in said


def test_the_page_says_which_calendar_a_source_reads_and_never_the_variable():
    own = c.describe(c.check_config(settings(base_url=BASE)))
    assert own == (
        "Events of the primary calendar of the account its credential signs in as, at"
        f" {BASE}; each is read once, after it has ended"
    )
    named = c.describe(
        c.check_config(settings(subject="ada@example.test", calendar_id="team@group.example.test"))
    )
    assert named == (
        "Events of calendar team@group.example.test, read as ada@example.test, at"
        " https://www.googleapis.com/calendar/v3; each is read once, after it has ended"
    )
    primary = c.describe(c.check_config(settings(subject="ada@example.test")))
    assert primary.startswith("Events of the primary calendar of ada@example.test, at")
    for said in (own, named, primary):
        assert VAR not in said and "credentials_env" not in said


# Opening


def test_opening_reads_the_credential_and_its_secrets_and_sends_nothing():
    sent = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: sent.append(r)))
    source = c.open_source("cal", c.check_config(settings()), http=http, env={VAR: CREDENTIAL})
    assert holds_secret(f"said {REFRESH} once", tuple(source.secrets))
    assert holds_secret(SECRET, tuple(source.secrets))
    assert REFRESH not in repr(source) and sent == []
    with pytest.raises(SourceError, match=VAR):
        c.open_source("cal", c.check_config(settings()), http=http, env={})


def test_a_refresh_token_with_a_subject_is_refused_by_its_setting():
    config = c.check_config(settings(subject="ada@example.test"))
    with pytest.raises(SourceError) as refused:
        c.open_source("cal", config, http=None, env={VAR: CREDENTIAL})
    assert str(refused.value) == (
        "source 'cal' reads with a refresh token, which reads as the person who authorised"
        " it: remove subject from its config"
    )


# Times and zones


@pytest.mark.parametrize(
    "value",
    ["", "../x", "/etc/localtime", "America", "Mars/Olympus_Mons", "x" * 300, 7, None, ["UTC"]],
)
def test_a_zone_no_database_knows_is_none_and_never_an_error(value):
    assert c.zone_of(value) is None


@pytest.mark.parametrize(
    "name", ["UTC", "Europe/Madrid", "Pacific/Auckland", "America/Argentina/Salta"]
)
def test_a_zone_the_database_knows_is_read(name):
    assert c.zone_of(name) == ZoneInfo(name)


def test_an_all_day_event_ends_at_midnight_in_the_zone_else_at_noon_utc():
    day = date(2026, 11, 13)
    assert c.ended_at(day, ZoneInfo("Pacific/Auckland")) == datetime(2026, 11, 12, 11, tzinfo=UTC)
    assert c.ended_at(day, ZoneInfo("Pacific/Pago_Pago")) == datetime(2026, 11, 13, 11, tzinfo=UTC)
    assert c.ended_at(day, None) == datetime(2026, 11, 13, 12, tzinfo=UTC)
    moment = datetime(2026, 11, 13, 9, 0, 0, 1, tzinfo=UTC)
    assert c.ended_at(moment, None) == moment  # a timed event: its end, to the microsecond


# Reading an event


def test_an_event_is_read_with_its_times_in_utc_and_its_end():
    item = timed(1, SINCE, SINCE + HOUR)
    item["start"] = {"dateTime": "2026-11-12T13:00:00.250+01:00", "timeZone": "Europe/Madrid"}
    event = c.read_event(item, None)
    assert event is not None and event.eid == "unitev0001"
    assert event.start == datetime(2026, 11, 12, 12, 0, 0, 250000, tzinfo=UTC)
    assert event.ended == SINCE + HOUR
    day = c.read_event(all_day(2, date(2026, 11, 12), date(2026, 11, 14)), ZoneInfo("UTC"))
    assert day is not None and (day.start, day.end) == (date(2026, 11, 12), date(2026, 11, 14))
    assert day.ended == datetime(2026, 11, 14, tzinfo=UTC)


@pytest.mark.parametrize(
    "item",
    [
        {"id": "unitev0001", "status": "cancelled"},
        {"id": "unitev0001", "status": "cancelled", "end": {"dateTime": "nonsense"}},
    ],
)
def test_a_cancelled_event_needs_only_its_id_and_is_none(item):
    assert c.read_event(item, None) is None


@pytest.mark.parametrize(
    "item",
    [
        [CANARY],
        {"status": "cancelled"},
        {**timed(1, SINCE, NOW), "id": 7},
        {**timed(1, SINCE, NOW), "id": ""},
        {**timed(1, SINCE, NOW), "id": "has space"},
        {**timed(1, SINCE, NOW), "id": "x" * 1025},
        {k: v for k, v in timed(1, SINCE, NOW).items() if k != "end"},
        {**timed(1, SINCE, NOW), "end": {}},
        {**timed(1, SINCE, NOW), "end": CANARY},
        {**timed(1, SINCE, NOW), "end": {"dateTime": CANARY}},
        {**timed(1, SINCE, NOW), "end": {"dateTime": "2026-11-13T09:00:00"}},
        {**timed(1, SINCE, NOW), "start": {"dateTime": CANARY}},
        {k: v for k, v in timed(1, SINCE, NOW).items() if k != "start"},
        {**all_day(1, date(2026, 11, 12), date(2026, 11, 13)), "end": {"date": "2026-13-45"}},
        {**all_day(1, date(2026, 11, 12), date(2026, 11, 13)), "end": {"date": 20261113}},
        {**timed(1, SINCE, NOW), "start": {"date": "2026-11-12"}},  # one timed, one all-day
        {**timed(1, SINCE, NOW), "status": 7},
    ],
)
def test_an_event_not_of_the_documented_shape_fails_without_its_values(item):
    with pytest.raises(SourceError) as failed:
        c.read_event(item, None)
    said = str(failed.value)
    assert said.startswith("Google Calendar's answer to events.list is not the documented shape")
    assert CANARY not in said and "unitev" not in said


def me(answer: str, **more) -> dict:
    return {"email": "ada@example.test", "self": True, "responseStatus": answer, **more}


@pytest.mark.parametrize(
    ("fields", "passed"),
    [
        ({}, False),
        ({"eventType": "default"}, False),
        ({"eventType": "focusTime"}, True),
        ({"eventType": "outOfOffice"}, True),
        ({"eventType": "workingLocation"}, True),
        ({"eventType": "birthday"}, True),
        ({"eventType": "fromGmail"}, True),
        ({"eventType": "aTypeGoogleAddsLater"}, True),
        ({"visibility": "private"}, True),
        ({"visibility": "confidential"}, True),
        ({"visibility": "public"}, False),
        ({"visibility": "default"}, False),
        ({"attendees": [me("declined")]}, True),
        ({"attendees": [me("accepted")]}, False),
        ({"attendees": [me("tentative")]}, False),
        ({"attendees": [me("needsAction")]}, False),
        ({"attendees": [{"email": "brook@example.test", "responseStatus": "declined"}]}, False),
        ({"attendees": [me("declined", self=False)]}, False),
        ({"attendees": []}, False),
        ({"attendees": "odd"}, False),
    ],
)
def test_what_is_not_a_meeting_of_the_person_is_passed_over(fields, passed):
    event = c.read_event(timed(1, SINCE, SINCE + HOUR, **fields), None)
    assert event is not None and c.passed_over(event) is passed


# The cursor


def test_a_cursor_is_compact_json_and_reads_back_as_written():
    mark = datetime(2026, 11, 13, 9, 0, 0, 123456, tzinfo=UTC)
    cursor = c.Cursor(mark=mark, seen={"0123456789abcdef": 1_763_000_000})
    text = cursor.encode()
    assert " " not in text and json.loads(text) == {
        "v": 1,
        "mark": "2026-11-13T09:00:00.123456Z",
        "seen": {"0123456789abcdef": 1_763_000_000},
    }
    assert c.Cursor.decode(text) == cursor


@pytest.mark.parametrize(
    "stored",
    [
        "not json",
        "[]",
        json.dumps({"v": 2, "mark": "2026-11-13T09:00:00Z", "seen": {}}),
        json.dumps({"v": 1, "mark": "yesterday", "seen": {}}),
        json.dumps({"v": 1, "mark": "2026-11-13T09:00:00Z", "seen": []}),
        json.dumps({"v": 1, "mark": "2026-11-13T09:00:00Z", "seen": {"unitev0001": 1}}),
        json.dumps({"v": 1, "mark": "2026-11-13T09:00:00Z", "seen": {"0123456789abcdef": -1}}),
        json.dumps({"v": 1, "mark": "2026-11-13T09:00:00Z", "seen": {"0123456789abcdef": True}}),
    ],
)
def test_a_cursor_this_source_did_not_write_fails_loudly(stored):
    with pytest.raises(SourceError, match="not one this Google Calendar source wrote"):
        c.Cursor.decode(stored)


def test_a_digest_is_let_go_once_the_next_listing_cannot_return_its_event():
    lower = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    edge = int(lower.timestamp())
    cursor = c.Cursor(
        mark=NOW,
        seen={"a" * 16: edge - 3_600, "b" * 16: edge - 30, "c" * 16: edge, "d" * 16: edge + 9},
    )
    kept = cursor.pruned(lower)
    assert set(kept.seen) == {"b" * 16, "c" * 16, "d" * 16}  # a minute's margin


def test_switched_on_again_the_mark_rises_to_the_switch_on():
    cursor = c.Cursor(mark=SINCE, seen={"a" * 16: 1})
    assert cursor.switched_on(NOW).mark == NOW
    assert cursor.switched_on(SINCE - DAY).mark == SINCE


# What a draft says


def test_a_title_is_the_summary_on_one_line_without_an_address():
    assert c.title_of(None) == c.title_of("") == c.title_of("  \n ") == "Untitled event"
    assert c.title_of(7) == "Untitled event"
    assert c.title_of("Plan\n  the   week") == "Plan the week"
    assert c.title_of("1:1 with ada@example.test today") == "1:1 with today"
    assert c.title_of("ada@example.test") == "Untitled event"


def test_a_title_is_cut_at_a_word_to_its_bounds():
    long = " ".join(["word"] * 60)  # 299 characters
    cut = c.title_of(long)
    assert len(cut) <= c.TITLE_MAX and long.startswith(cut) and long[len(cut)] == " "
    wide = " ".join(["漢字漢字"] * 40)  # three bytes a character
    cut = c.title_of(wide)
    assert len(json.dumps(cut, ensure_ascii=False).encode()) <= c.TITLE_BYTES
    assert wide.startswith(cut) and cut


@pytest.mark.perf
@pytest.mark.parametrize(
    "summary",
    [lambda n: "x" * n, lambda n: "ada@example.test " * (n // 5) + "x" * n],
    ids=["summary0", "summary1"],  # the ids the strings these replaced were given
)
def test_a_long_summary_is_made_a_title_in_linear_time(summary):
    """Only time shows this (the cut takes off a word or a character at a time). The old cut
    worked on the whole summary each time: 40,000 characters took 4.4 s and the time grew
    with the square of the length, so 400,000 took minutes. Cut to the title's bound first,
    eight times the summary takes about eight times as long (linear_cost)."""
    assert_near_linear(lambda n: partial(c.title_of, summary(n)), 25_000)
    made = c.title_of(summary(25_000))
    assert 0 < len(made) <= c.TITLE_MAX and "@" not in made


def test_a_draft_makes_its_title_once(monkeypatch):
    event = c.read_event(full_event(), None)
    assert event is not None
    made = []
    title_of = c.title_of
    monkeypatch.setattr(c, "title_of", lambda summary: made.append(summary) or title_of(summary))
    draft = c.draft(event, "calendar")
    assert len(made) == 1 and draft.text.startswith("# Quarterly planning\n")


@pytest.mark.parametrize(
    ("title", "topic"),
    [
        ("Quarterly planning", "Quarterly planning, 2026-11-12"),
        ("Untitled event", "Untitled event, 2026-11-12"),
        ("合意", "合意, 2026-11-12"),  # no ASCII: the slug is the date
    ],
)
def test_a_topic_is_the_title_and_the_start_date(title, topic):
    assert c.topic(title, date(2026, 11, 12)) == topic


def test_a_long_title_leaves_room_in_the_slug_for_the_date():
    title = "Northern region quarterly operations planning and budget alignment review with vendors"
    made = c.topic(title, date(2026, 11, 12))
    cut = made.removesuffix(", 2026-11-12")
    assert title.startswith(cut) and title[len(cut)] == " "
    assert slugify(made).endswith("-2026-11-12") and len(slugify(made)) <= 64
    assert len(slugify(cut)) <= c.TOPIC_SLUG_ROOM
    one_word = "x" * 90
    assert slugify(c.topic(one_word, date(2026, 11, 12))).endswith("2026-11-12")


def full_event(**changes) -> dict:
    start = datetime(2026, 11, 12, 9, 0, tzinfo=timezone(timedelta(hours=2)))
    item = timed(
        1,
        start,
        start + 90 * MINUTE,
        summary="Quarterly planning",
        location="Harbour Street,\nroom 4",
        organizer={"email": "cy@example.test", "displayName": "Cy Moss"},
        attendees=[
            me("accepted", displayName="Ada Park"),
            {"email": "brook@example.test", "displayName": "Brook Lane"},
            {"email": "board@resource.example.test", "displayName": "Board", "resource": True},
            {"email": "guest@example.test"},
            {"email": "odd@example.test", "displayName": "odd@example.test"},
        ],
        conferenceData={
            "conferenceId": "abc-defg-hij",
            "conferenceSolution": {"key": {"type": "hangoutsMeet"}},
        },
        description="<p>Bring the <b>figures</b>.</p><ul><li>Budget</li></ul>",
    )
    item["start"] = {"dateTime": "2026-11-12T09:00:00+02:00"}
    item["end"] = {"dateTime": "2026-11-12T10:30:00+02:00"}
    item.update(changes)
    return item


def test_the_text_is_a_heading_the_facts_and_the_description():
    event = c.read_event(full_event(), None)
    assert event is not None
    assert c.text(event) == (
        "# Quarterly planning\n"
        "\n"
        "- When: 2026-11-12 07:00 to 2026-11-12 08:30 UTC\n"
        "- Where: Harbour Street, room 4\n"
        "- Organiser: Cy Moss\n"
        "- Attendees: Ada Park, Brook Lane and 2 more\n"
        "- Meet: abc-defg-hij\n"
        "\n"
        "Bring the figures.\n"
        "\n"
        "- Budget\n"
    )


def test_a_draft_s_source_ref_begins_with_the_kind_s_word():
    """M3: `KIND.ref_form` is the word before the first ":" of the source_ref this kind
    writes, so a note a person or an agent writes the same way is withheld as these are."""
    from cairn.sources import source_ref_word

    event = c.read_event(full_event(), None)
    assert event is not None
    draft = c.draft(event, "calendar")
    assert source_ref_word(draft.source_ref) == c.KIND.ref_form == "calendar"


def test_a_draft_is_one_event_on_its_own_page():
    event = c.read_event(full_event(), None)
    assert event is not None
    draft = c.draft(event, "calendar")
    assert (draft.project, draft.title, draft.topic) == (
        "calendar",
        "Quarterly planning",
        "Quarterly planning, 2026-11-12",
    )
    assert (draft.source_ref, draft.label) == ("calendar:unitev0001", "google-calendar")
    assert draft.text == c.text(event) and draft.resume is None


@pytest.mark.parametrize(
    ("first", "after", "when"),
    [
        (date(2026, 11, 12), date(2026, 11, 13), "- When: 2026-11-12, all day"),
        (date(2026, 11, 12), date(2026, 11, 15), "- When: 2026-11-12 to 2026-11-14, all day"),
        (date(2026, 11, 12), date(2026, 11, 12), "- When: 2026-11-12, all day"),  # odd: no length
    ],
)
def test_an_all_day_events_days_end_the_day_before_its_end_date(first, after, when):
    event = c.read_event(all_day(1, first, after), None)
    assert event is not None
    assert c.text(event).splitlines()[2] == when


def test_a_timed_event_across_midnight_says_both_dates():
    late = timed(
        1, datetime(2026, 11, 12, 23, 30, tzinfo=UTC), datetime(2026, 11, 13, 0, 30, tzinfo=UTC)
    )
    event = c.read_event(late, None)
    assert event is not None
    assert "- When: 2026-11-12 23:30 to 2026-11-13 00:30 UTC" in c.text(event)


@pytest.mark.parametrize(
    ("attendees", "line"),
    [
        (None, None),
        ([], None),
        ([{"email": "room@resource.example.test", "resource": True}], None),
        (
            [{"email": "a@example.test"}, {"email": "b@example.test"}],
            "- Attendees: 2 without a name",
        ),
        ([{"email": "a@example.test", "displayName": "Ada  Park\n"}], "- Attendees: Ada Park"),
        ([{"displayName": "Ada Park"}, "odd", 7], "- Attendees: Ada Park"),
        (
            [{"displayName": "Ada Park"}, {"displayName": "brook\uff20example.test"}],
            "- Attendees: Ada Park and 1 more",
        ),
        ([{"displayName": "cy\ufe6bexample.test"}], "- Attendees: 1 without a name"),
    ],
)
def test_attendees_are_people_by_name_and_the_rest_counted(attendees, line):
    fields = {} if attendees is None else {"attendees": attendees}
    event = c.read_event(timed(1, SINCE, SINCE + HOUR, **fields), None)
    assert event is not None
    found = [x for x in c.text(event).splitlines() if x.startswith("- Attendees")]
    assert found == ([] if line is None else [line])
    assert "@" not in c.text(event)


@pytest.mark.parametrize(
    ("organizer", "line"),
    [
        ({"email": "cy@example.test"}, None),
        ({"email": "cy@example.test", "displayName": "cy@example.test"}, None),
        ({"displayName": "cy\uff20example.test"}, None),
        ({"displayName": "Cy Moss", "self": True}, "- Organiser: Cy Moss"),
        ("odd", None),
    ],
)
def test_the_organiser_is_named_when_google_gives_a_name(organizer, line):
    event = c.read_event(timed(1, SINCE, SINCE + HOUR, organizer=organizer), None)
    assert event is not None
    found = [x for x in c.text(event).splitlines() if x.startswith("- Organiser")]
    assert found == ([] if line is None else [line])


@pytest.mark.parametrize(
    ("conference", "line"),
    [
        (
            {
                "conferenceId": "abc-defg-hij",
                "conferenceSolution": {"key": {"type": "hangoutsMeet"}},
            },
            "- Meet: abc-defg-hij",
        ),
        ({"conferenceId": "abc-defg-hij", "conferenceSolution": {"key": {"type": "addOn"}}}, None),
        ({"conferenceSolution": {"key": {"type": "hangoutsMeet"}}}, None),
        (
            {"conferenceId": "has space", "conferenceSolution": {"key": {"type": "hangoutsMeet"}}},
            None,
        ),
        (
            {"conferenceId": "a@b.test", "conferenceSolution": {"key": {"type": "hangoutsMeet"}}},
            None,
        ),
        (
            {
                "conferenceId": "a\uff20b.test",
                "conferenceSolution": {"key": {"type": "hangoutsMeet"}},
            },
            None,
        ),
        (
            {"conferenceId": "x" * 129, "conferenceSolution": {"key": {"type": "hangoutsMeet"}}},
            None,
        ),
        ({"conferenceId": 7, "conferenceSolution": {"key": {"type": "hangoutsMeet"}}}, None),
        ({"conferenceId": "abc", "conferenceSolution": "odd"}, None),
        ("odd", None),
    ],
)
def test_the_meet_line_is_the_code_of_a_google_meet_conference(conference, line):
    event = c.read_event(timed(1, SINCE, SINCE + HOUR, conferenceData=conference), None)
    assert event is not None
    found = [x for x in c.text(event).splitlines() if x.startswith("- Meet")]
    assert found == ([] if line is None else [line])


def test_a_location_on_one_line_without_an_address_and_none_when_empty():
    for location, line in (
        ("Room 4", "- Where: Room 4"),
        ("board@resource.example.test", None),
        ("Room 4, board@resource.example.test", "- Where: Room 4,"),
        ("   ", None),
        (7, None),
    ):
        event = c.read_event(timed(1, SINCE, SINCE + HOUR, location=location), None)
        assert event is not None
        found = [x for x in c.text(event).splitlines() if x.startswith("- Where")]
        assert found == ([] if line is None else [line]), location


def test_a_description_that_is_only_markup_writes_no_paragraph():
    event = c.read_event(timed(1, SINCE, SINCE + HOUR, description="<p> </p>"), None)
    assert event is not None
    assert c.text(event).endswith("UTC\n")


# Pulls


class Stand:
    """The token endpoint and events.list, enough to test what the acceptance file does not;
    `answer` overrides events.list with a function of the request."""

    def __init__(self) -> None:
        self.now = NOW
        self.items: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.slept: list[float] = []
        self.answer = None
        self.page = 2500
        self.top: dict = {"timeZone": "UTC", "accessRole": "owner"}
        self.later_top: dict | None = None  # what pages after the first say instead
        self.tick = timedelta(0)

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def lists(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/events")]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.now += self.tick
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3599})
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if self.answer is not None:
            return self.answer(request)
        params = request.url.params
        offset = int(params.get("pageToken", "0"))
        low, high = (datetime.fromisoformat(params[k]) for k in ("timeMin", "timeMax"))
        listed = [i for i in self.items if self.bounds(i)[1] > low and self.bounds(i)[0] < high]
        top = self.top if offset == 0 or self.later_top is None else self.later_top
        body = {"kind": "calendar#events", **top}
        body["items"] = listed[offset : offset + self.page]
        if offset + self.page < len(listed):
            body["nextPageToken"] = str(offset + self.page)
        return httpx.Response(200, json=body)

    def bounds(self, item: dict) -> tuple[datetime, datetime]:
        """Where events.list bounds an event: its times, or an all-day event's days at
        midnight in the calendar's zone (UTC when the stand names none)."""
        zone = c.zone_of(self.top.get("timeZone")) or UTC
        found = []
        for key in ("start", "end"):
            when = item[key]
            if "dateTime" in when:
                found.append(datetime.fromisoformat(when["dateTime"]))
            else:
                found.append(datetime.combine(date.fromisoformat(when["date"]), time(0), zone))
        return found[0], found[1]

    def source(self, *, since=SINCE, deadline=None, **given):
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return c.open_source(
            "cal", c.check_config(settings(base_url=BASE, **given)), http=http,
            env={VAR: CREDENTIAL}, clock=self.clock, sleep=self.sleep, deadline=deadline,
            since=since, jitter=lambda: 0.5,
        )  # fmt: skip

    def pull(self, cursor=None, **given):
        return asyncio.run(self.source(**given).pull(cursor))


def refs(pulled) -> list[str]:
    return [d.source_ref for d in pulled.drafts]


def test_the_listing_asks_for_ended_events_by_their_documented_parameters():
    stand = Stand()
    stand.items = [timed(1, SINCE + HOUR, SINCE + 2 * HOUR)]
    stand.pull(calendar_id="ops#team@group.example.test")
    (listed,) = stand.lists()
    assert listed.url.raw_path.decode().startswith(
        "/calendar/v3/calendars/ops%23team%40group.example.test/events?"
    )
    assert dict(listed.url.params) == {
        "singleEvents": "true",
        "orderBy": "startTime",
        "timeMin": "2026-11-10T12:00:00.000Z",
        "timeMax": "2026-11-13T12:00:00.000Z",
        "maxResults": "2500",
    }
    assert c.listing(SINCE - 2 * DAY, NOW) == dict(listed.url.params)


def test_each_page_repeats_the_parameters_beside_its_token_and_the_bound_stays_put():
    stand = Stand()
    stand.page = 2
    stand.tick = MINUTE  # the clock moves while it pages
    stand.items = [timed(n, SINCE + n * HOUR, SINCE + n * HOUR + MINUTE) for n in range(1, 6)]
    got = stand.pull()
    assert refs(got) == [f"calendar:unitev{n:04d}" for n in range(1, 6)]
    first, *later = stand.lists()
    assert len(later) == 2
    for request in later:
        params = dict(request.url.params)
        assert params.pop("pageToken") and params == dict(first.url.params)


def test_drafts_are_written_by_end_then_id_and_an_event_listed_twice_once():
    stand = Stand()
    stand.items = [
        timed(3, SINCE + HOUR, SINCE + 3 * HOUR),
        timed(2, SINCE + 2 * HOUR, SINCE + 3 * HOUR),
        timed(1, SINCE + 2 * HOUR, SINCE + 4 * HOUR),
        timed(2, SINCE + 2 * HOUR, SINCE + 3 * HOUR),
    ]
    got = stand.pull()
    assert refs(got) == ["calendar:unitev0002", "calendar:unitev0003", "calendar:unitev0001"]


def test_an_event_moved_a_little_later_after_it_was_written_is_not_written_again():
    stand = Stand()
    item = timed(1, SINCE + HOUR, SINCE + 2 * HOUR)
    stand.items = [item]
    first = stand.pull()
    assert refs(first) == ["calendar:unitev0001"]
    cursor = first.cursor
    item["end"] = {"dateTime": z(SINCE + 3 * HOUR)}  # moved an hour later
    for hours in range(6, 24 * 6, 6):  # a pull every six hours for six days
        stand.now = NOW + hours * HOUR
        got = stand.pull(cursor)
        assert got.drafts == [], hours
        cursor = got.cursor


def test_a_seen_events_digest_is_kept_while_the_listing_returns_it():
    stand = Stand()
    stand.items = [timed(1, SINCE + HOUR, SINCE + 2 * HOUR)]
    first = stand.pull()
    seen = json.loads(first.cursor)["seen"]
    assert list(seen) == [c.digest("unitev0001")]
    stand.now = NOW + 3 * DAY
    stand.items = []
    later = stand.pull(first.cursor)
    assert json.loads(later.cursor)["seen"] == {}
    assert json.loads(later.cursor)["mark"] == "2026-11-16T12:00:00.000000Z"


def test_an_event_that_ends_after_now_waits_and_one_ended_before_since_is_never_written():
    stand = Stand()
    stand.items = [
        timed(1, SINCE - 2 * HOUR, SINCE - MINUTE),
        timed(2, NOW - HOUR, NOW),  # ended right at the pull: written
        timed(3, NOW - HOUR, NOW + MINUTE),
    ]
    got = stand.pull()
    assert refs(got) == ["calendar:unitev0002"]
    stand.now = NOW + HOUR
    assert refs(stand.pull(got.cursor)) == ["calendar:unitev0003"]


def test_switched_off_and_on_it_reads_nothing_that_ended_while_it_was_off():
    stand = Stand()
    first = stand.pull()
    stand.now = NOW + 2 * DAY
    stand.items = [
        timed(1, NOW + DAY, NOW + DAY + HOUR),  # while it was off
        timed(2, NOW + 2 * DAY - HOUR, NOW + 2 * DAY),
    ]
    got = stand.pull(first.cursor, since=NOW + 2 * DAY - 30 * MINUTE)
    assert refs(got) == ["calendar:unitev0002"]


def test_a_calendar_shared_as_free_busy_only_fails_and_says_to_share_its_details():
    stand = Stand()
    stand.top = {"timeZone": "UTC", "accessRole": "freeBusyReader"}
    busy = timed(1, SINCE + HOUR, SINCE + 2 * HOUR)
    stand.items = [{k: busy[k] for k in ("id", "status", "start", "end")}]
    with pytest.raises(SourceError) as failed:
        stand.pull()
    assert str(failed.value) == (
        "calendar_id names a calendar the account that reads it sees as free/busy only, which"
        " hides each event's title and details: share the calendar with that account so that"
        " its event details are visible, and the next run reads it"
    )


@pytest.mark.parametrize("told", [None, 7, "", "Mars/Olympus_Mons"])
def test_a_listing_without_a_known_zone_ends_an_all_day_event_at_noon_utc(told):
    stand = Stand()
    stand.top = {"accessRole": "owner"} | ({} if told is None else {"timeZone": told})
    stand.items = [all_day(1, date(2026, 11, 12), date(2026, 11, 13))]
    stand.now = datetime(2026, 11, 13, 11, 59, tzinfo=UTC)
    first = stand.pull()
    assert first.drafts == []
    stand.now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    assert refs(stand.pull(first.cursor)) == ["calendar:unitev0001"]


def test_the_zone_comes_from_the_first_page_that_gives_one():
    stand = Stand()
    stand.page = 1
    stand.items = [
        timed(1, SINCE + HOUR, SINCE + 2 * HOUR),
        all_day(2, date(2026, 11, 12), date(2026, 11, 13)),
    ]
    stand.top = {"timeZone": "Pacific/Auckland", "accessRole": "owner"}
    stand.later_top = {"accessRole": "owner"}  # the second page names no zone
    got = stand.pull(since=datetime(2026, 11, 12, 10, tzinfo=UTC))
    # Auckland's midnight starting 2026-11-13 is 11:00 UTC on the 12th
    assert refs(got) == ["calendar:unitev0002", "calendar:unitev0001"]


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (
            error(401, "authError"),
            "Google Calendar refused access (HTTP 401) to events.list: check that the credential"
            f" in {VAR} carries the scope https://www.googleapis.com/auth/calendar.events.readonly"
            " (for a service account, in its domain-wide delegation), that the Google Calendar"
            " API is enabled in its Cloud project, and that the account it reads as may read"
            " calendar_id",
        ),
        (error(403, "forbidden"), "Google Calendar refused access (HTTP 403) to events.list"),
        (error(403, "accessNotConfigured"), "Google Calendar refused access (HTTP 403)"),
        (
            error(403, "quotaExceeded"),
            "Google Calendar answered that a calendar usage limit was reached (quotaExceeded):"
            " waiting a few seconds does not lift it, so it is not retried; the next run tries"
            " again",
        ),
        (
            error(404, "notFound"),
            "Google Calendar found no calendar for calendar_id (HTTP 404): check that it names"
            " a calendar, and that the calendar is shared with the account that reads it",
        ),
        (
            error(410, "fullSyncRequired"),
            "Google Calendar answered 410 (gone) to events.list, which this server does not expect"
            " without a sync token: the next run tries again",
        ),
        (
            error(429, "rateLimitExceeded"),
            "Google Calendar still answered with a rate limit to events.list after 4 retries:"
            " the next run goes on",
        ),
        (
            error(403, "userRateLimitExceeded"),
            "Google Calendar still answered with a rate limit to events.list after 4 retries",
        ),
        (
            error(503),
            "Google Calendar answered HTTP 503 to events.list, a fault on its side: the next run"
            " tries again",
        ),
        (error(400, "invalid"), "Google Calendar refused the request to events.list (HTTP 400)"),
        (
            httpx.Response(302, headers={"location": "https://elsewhere.example.test/"}),
            "Google Calendar answered events.list with a redirect (HTTP 302), which this server"
            " does not follow: check base_url",
        ),
        (httpx.Response(200, text=CANARY), "Google Calendar's answer to events.list is not JSON"),
        (
            httpx.Response(200, json=[CANARY]),
            "Google Calendar's answer to events.list is not the documented shape",
        ),
        (
            httpx.Response(200, json={"items": CANARY}),
            "Google Calendar's answer to events.list is not the documented shape",
        ),
        (
            httpx.Response(200, json={"items": [], "nextPageToken": 7}),
            "Google Calendar's answer to events.list is not the documented shape",
        ),
    ],
)
def test_each_refusal_says_what_happened_and_what_to_check(answer, said):
    stand = Stand()
    stand.answer = lambda request: answer
    with pytest.raises(SourceError) as failed:
        stand.pull()
    message = str(failed.value)
    assert message.startswith(said), message
    assert CANARY not in message and TOKEN not in message


def test_a_rate_limit_is_waited_out_with_the_backoff_then_read():
    stand = Stand()
    stand.items = [timed(1, SINCE + HOUR, SINCE + 2 * HOUR)]
    answers = [error(403, "userRateLimitExceeded"), error(429, "rateLimitExceeded")]

    def answer(request):
        if answers:
            return answers.pop(0)
        stand.answer = None
        return stand.handler(request)

    stand.answer = answer
    got = stand.pull()
    assert refs(got) == ["calendar:unitev0001"] and stand.slept == [1.5, 2.5]


def test_a_service_account_acting_as_itself_on_its_primary_calendar_is_refused():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from test_google_auth_unit import key_file

    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    env = {VAR: json.dumps(key_file(pem))}
    with pytest.raises(SourceError) as refused:
        c.open_source("cal", c.check_config(settings()), http=None, env=env)
    assert str(refused.value) == (
        "source 'cal' reads with a service account acting as itself (no subject), which has no"
        " calendar of its own to read: set calendar_id to a calendar shared with the service"
        " account's address, or set subject to the person it reads for (domain-wide delegation)"
    )
    shared = c.check_config(settings(calendar_id="team@group.example.test"))
    assert c.open_source("cal", shared, http=None, env=env).subject is None
    with_subject = c.check_config(settings(subject="ada@example.test"))
    assert c.open_source("cal", with_subject, http=None, env=env).subject == "ada@example.test"


def test_a_deadline_before_it_signs_in_fails_the_pull_and_sends_nothing():
    stand = Stand()
    with pytest.raises(SourceError, match="no time was left to sign in"):
        stand.pull(deadline=NOW)
    assert stand.requests == []


def test_a_deadline_before_the_first_page_fails_the_pull():
    stand = Stand()
    stand.tick = 2 * MINUTE
    stand.items = [timed(1, SINCE + HOUR, SINCE + 2 * HOUR)]
    with pytest.raises(SourceError, match="could not list the calendar within one pull's bounds"):
        stand.pull(deadline=NOW + MINUTE)
    assert len(stand.lists()) == 0


def test_a_deadline_after_some_pages_writes_what_they_hold_and_the_next_pull_the_rest():
    stand = Stand()
    stand.page = 2
    stand.tick = MINUTE
    stand.items = [timed(n, SINCE + n * HOUR, SINCE + n * HOUR + MINUTE) for n in range(1, 7)]
    cut = stand.pull(deadline=NOW + MINUTE + 30 * timedelta(seconds=1))  # one page, then out
    assert refs(cut) == ["calendar:unitev0001", "calendar:unitev0002"]
    assert json.loads(cut.cursor)["mark"] == json.loads(cut.drafts[-1].resume)["mark"]
    assert json.loads(cut.cursor)["mark"] == "2026-11-12T12:00:00.000000Z"  # it did not move
    stand.tick = timedelta(0)
    rest = stand.pull(cut.cursor)
    assert refs(rest) == [f"calendar:unitev{n:04d}" for n in range(3, 7)]


def test_a_pull_stops_at_its_budget_of_requests(monkeypatch):
    monkeypatch.setattr(c, "BUDGET", 3)  # the token is not one of them
    stand = Stand()
    stand.page = 1
    stand.items = [timed(n, SINCE + n * HOUR, SINCE + n * HOUR + MINUTE) for n in range(1, 6)]
    cut = stand.pull()
    assert refs(cut) == [f"calendar:unitev{n:04d}" for n in range(1, 4)]
    with pytest.raises(SourceError, match="could not list the calendar within one pull's bounds"):
        stand.pull(cut.cursor)  # the same three pages, nothing new in them: it cannot move
    monkeypatch.setattr(c, "BUDGET", 10)
    assert refs(stand.pull(cut.cursor)) == ["calendar:unitev0004", "calendar:unitev0005"]


def test_a_pull_keeps_at_most_max_events_of_the_listing_as_it_stops_at_its_budget(monkeypatch):
    """Google chooses how many events a page holds, so a listing bounded by its requests alone
    kept whatever its pages brought. It now stops at MAX_EVENTS events as at its budget."""
    monkeypatch.setattr(c, "MAX_EVENTS", 3)
    stand = Stand()  # one page of five
    stand.items = [timed(n, SINCE + n * HOUR, SINCE + n * HOUR + MINUTE) for n in range(1, 6)]
    cut = stand.pull()
    assert refs(cut) == [f"calendar:unitev{n:04d}" for n in range(1, 4)]
    assert json.loads(cut.cursor)["mark"] == "2026-11-12T12:00:00.000000Z"  # it did not move
    with pytest.raises(SourceError, match="could not list the calendar within one pull's bounds"):
        stand.pull(cut.cursor)  # the same events, nothing new in them: it cannot move
    monkeypatch.setattr(c, "MAX_EVENTS", 10)
    assert refs(stand.pull(cut.cursor)) == ["calendar:unitev0004", "calendar:unitev0005"]


def test_every_draft_carries_the_cursor_that_goes_on_right_after_it():
    stand = Stand()
    stand.items = [timed(n, SINCE + n * HOUR, SINCE + n * HOUR + MINUTE) for n in range(1, 4)]
    got = stand.pull()
    for index, draft in enumerate(got.drafts):
        rest = stand.pull(draft.resume)
        assert refs(rest) == refs(got)[index + 1 :]


def test_the_token_and_the_credential_reach_no_log_line(caplog):
    from cairn.sources import source_client

    stand = Stand()
    stand.items = [timed(1, SINCE + HOUR, SINCE + 2 * HOUR)]
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.DEBUG)
    try:
        with caplog.at_level(logging.DEBUG):
            source = c.open_source(
                "cal", c.check_config(settings(base_url=BASE)),
                http=source_client(transport=httpx.MockTransport(stand.handler)),
                env={VAR: CREDENTIAL}, clock=stand.clock, sleep=stand.sleep,
                since=SINCE,
            )  # fmt: skip
            got = asyncio.run(source.pull(None))
    finally:
        root.setLevel(level)
    assert refs(got) == ["calendar:unitev0001"]
    assert holds_secret(f"x {TOKEN} y", tuple(source.secrets))
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (TOKEN, SECRET, REFRESH):
        assert secret not in logged
    assert "HTTP Request" not in logged
    for request in stand.requests:
        assert TOKEN not in unquote(str(request.url))


def test_an_all_day_end_before_year_one_in_utc_falls_back_and_never_raises():
    """Midnight on 0001-01-01 in a zone east of UTC is before year 1 once in UTC. Such a day
    counts as ended by the fallback (12:00 UTC on it), not by an exception that would end a
    pull without a reason."""
    end = date(1, 1, 1)
    assert c.ended_at(end, ZoneInfo("Pacific/Auckland")) == c.ended_at(end, None)
    last = date(9999, 12, 31)  # the other edge stays inside the calendar: no fallback
    assert c.ended_at(last, ZoneInfo("Pacific/Pago_Pago")) == datetime(9999, 12, 31, 11, tzinfo=UTC)


def test_an_event_cairn_fails_on_is_skipped_once_and_the_rest_written(monkeypatch):
    """Anything but a SourceError while one event is made into a draft used to fail the
    pull, and the event was listed again by every pull after it: the source was stuck."""
    stand = Stand()
    stand.items = [timed(n, SINCE + n * HOUR, SINCE + n * HOUR + MINUTE) for n in (1, 2, 3)]
    stand.items[1]["visibility"] = ["private"]  # not text: `in` a set of words raised
    made = c.draft

    def failing(event, project):
        if event.eid == "unitev0003":
            raise ValueError("year 0 is out of range")
        return made(event, project)

    monkeypatch.setattr(c, "draft", failing)
    first = stand.pull()
    assert refs(first) == ["calendar:unitev0001", "calendar:unitev0002"]
    assert [(s.ref, s.reason) for s in first.skipped] == [("unitev0003", c.UNREADABLE)]
    again = stand.pull(first.cursor)
    assert again.drafts == again.skipped == []
