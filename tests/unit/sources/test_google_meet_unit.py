"""Unit tests for the Google Meet source: what tests/acceptance/test_c4_google_meet.py does
not pin. HTTP goes through httpx.MockTransport; time, sleep and the backoff's jitter are
injected. The credential is a made-up refresh token, so no key is generated here; every
name, address and id is made up."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta, timezone
from functools import partial

import httpx
import pytest

from cairn.sources import FROM_THEN_ON, SourceError, holds_secret
from cairn.sources import google_meet as m
from support.linear_cost import assert_near_linear
from support.sources.google_meet import (
    BASE,
    CANARY,
    CREDENTIAL,
    DAY,
    HOUR,
    MINUTE,
    NOW,
    PEOPLE,
    PEOPLE_NAME,
    REFRESH,
    SECRET,
    SINCE,
    TOKEN,
    VAR,
    Stand,
    cursor,
    error,
    meeting,
    person,
    refs,
    refusal,
    said,
    settings,
    transcript,
    z,
)

# The kind and its config


def test_the_kind_starts_off_proposes_its_project_and_says_what_switching_on_reads():
    kind = m.KIND
    assert kind.check is m.check_config and kind.open is m.open_source
    assert kind.starts_off and kind.via == "google-meet"
    assert kind.default_project == "meet-transcripts" and kind.switch_on is None
    assert kind.switching_on() == FROM_THEN_ON
    assert m.SCOPES == ["https://www.googleapis.com/auth/meetings.space.readonly"]


def test_the_defaults_are_filled_in_and_what_is_stored_checks_the_same():
    checked = m.check_config({"credentials_env": VAR})
    assert checked == {
        "credentials_env": VAR,
        "projects": [],
        "base_url": "https://meet.googleapis.com/v2",
        "wait_hours": 48,
    }
    assert m.check_config(checked) == checked
    assert "subject" not in m.check_config(settings(subject=None))
    kept = m.check_config(settings(subject="ada@example.test", base_url=BASE + "/"))
    assert (kept["subject"], kept["base_url"]) == ("ada@example.test", BASE)
    assert m.check_config(settings(base_url="http://[::1]:8781/v2"))["base_url"] == (
        "http://[::1]:8781/v2"
    )


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"credentials_env": None}, "a google_meet source needs credentials_env"),
        ({"credentials_env": "google_creds"}, "credentials_env names an environment variable"),
        ({"credentials_env": "A" * 129}, "credentials_env names an environment variable"),
        ({"subject": "ada"}, "subject is one email address"),
        ({"subject": "ada@example.test brook@example.test"}, "subject is one email address"),
        ({"subject": "a" * 250 + "@example.test"}, "subject is one email address"),
        ({"projects": ["calls", "board"]}, "projects names at most one project"),
        ({"projects": [""]}, "projects holds a project's name"),
        ({"projects": "calls"}, "projects is a list of at most one project's name"),
        ({"base_url": "https://meet.example.test/v2?x=1"}, "base_url has no query"),
        ({"base_url": "http://meet.example.test/v2"}, "base_url is https"),
        ({"base_url": "https://user@meet.example.test/v2"}, "base_url must not hold a user"),
        ({"base_url": 7}, "base_url is the address"),
        ({"wait_hours": True}, "wait_hours is a whole number from 1 to 168"),
        ({"wait_hours": 1.5}, "wait_hours is a whole number from 1 to 168"),
        ({"wait_hours": None}, "wait_hours is a whole number from 1 to 168"),
    ],
)
def test_each_refusal_names_its_setting_and_says_what_it_takes(given, said):
    config = {"credentials_env": VAR, "projects": ["calls"], **given}
    assert said in refusal(config)


def test_an_unknown_setting_is_named_unless_its_name_could_be_a_credential():
    said = refusal(settings(colour="blue"))
    assert "'colour'" in said and "credentials_env" in said
    leaked = "ya29." + "PastedAsAName99"
    said = refusal(settings(**{leaked: True}))
    assert leaked not in said and "does not take" in said


@pytest.mark.parametrize(
    "value",
    [
        "-----BEGIN " + "PRIVATE KEY-----",
        "AIza" + "SyUnitMadeUpGoogleKey0123456789abcd",
        "{",
        "GOCSPX-" + "Pasted",
    ],
)
def test_a_credential_in_any_setting_is_refused_without_repeating_it(value):
    for setting in ("credentials_env", "subject"):
        said = refusal(settings(**{setting: value}))
        assert setting in said and value not in said
    said = refusal(settings(projects=[value]))
    assert "projects" in said and value not in said


def test_the_page_says_whose_meetings_a_source_reads_and_never_the_variable():
    said = m.describe(m.check_config(settings(subject="ada@example.test")))
    assert said == (
        "Google Meet transcripts of the meetings Google lists for ada@example.test, at"
        " https://meet.googleapis.com/v2; each is read once its file is generated, looked for"
        " until 48 hours after the meeting ends"
    )
    said = m.describe(m.check_config(settings(base_url=BASE, wait_hours=1)))
    assert said == (
        "Google Meet transcripts of the meetings Google lists for the person who authorised"
        f" its credential, at {BASE}; each is read once its file is generated, looked for"
        " until 1 hour after the meeting ends"
    )
    assert VAR not in said


# Opening


def test_opening_reads_the_credential_and_its_secrets_and_shows_none():
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    source = m.open_source("meet", m.check_config(settings()), http=http, env={VAR: CREDENTIAL})
    assert set(source.secrets) == {SECRET, REFRESH}
    assert SECRET not in repr(source) and REFRESH not in repr(source)
    with pytest.raises(SourceError) as missing:
        m.open_source("meet", m.check_config(settings()), http=http, env={})
    assert VAR in str(missing.value)


# The filter, timestamps, digests


def test_the_filter_is_the_documented_form_with_milliseconds_and_z():
    moment = datetime(2026, 11, 12, 13, 4, 5, 678901, tzinfo=UTC)
    assert m.record_filter(moment) == 'start_time>="2026-11-12T13:04:05.678Z"'
    other = moment.astimezone(timezone(timedelta(hours=2)))
    assert m.record_filter(other) == 'start_time>="2026-11-12T13:04:05.678Z"'


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-12T09:00:00Z", datetime(2026, 11, 12, 9, tzinfo=UTC)),
        ("2026-11-12T09:00:00.5Z", datetime(2026, 11, 12, 9, 0, 0, 500000, tzinfo=UTC)),
        ("2026-11-12T09:00:00.123Z", datetime(2026, 11, 12, 9, 0, 0, 123000, tzinfo=UTC)),
        ("2026-11-12T09:00:00.123456789Z", datetime(2026, 11, 12, 9, 0, 0, 123456, tzinfo=UTC)),
        ("2026-11-12T10:00:00.000+01:00", datetime(2026, 11, 12, 9, tzinfo=UTC)),
        ("2026-11-12T09:00:00", None),
        ("2026-11-12", None),
        ("2026-11-12T09:00:00.1234567890Z", None),
        ("yesterday", None),
        (7, None),
    ],
)
def test_a_timestamp_has_0_to_9_fractional_digits_and_a_zone(value, expected):
    assert m.moment(value) == expected


def test_a_digest_is_sixteen_hex_digits_of_the_resource_name():
    one = m.digest("conferenceRecords/abc/transcripts/def")
    assert len(one) == 16 and int(one, 16) >= 0
    assert one != m.digest("conferenceRecords/abc")


# The cursor


def test_a_cursor_is_compact_json_and_reads_back_as_written():
    written = cursor()
    text = written.encode()
    assert " " not in text and json.loads(text)["v"] == 1
    assert set(json.loads(text)) == {"v", "mark", "waiting", "settled", "parts"}
    assert m.Cursor.decode(text) == written


def test_a_waiting_meetings_end_is_kept_to_the_microsecond():
    end = datetime(2026, 11, 13, 11, 0, 0, 2345, tzinfo=UTC)
    text = cursor(waiting={"conf1": m.Waiting(end, False)}).encode()
    assert m.Cursor.decode(text).waiting["conf1"].end == end


@pytest.mark.parametrize(
    "stored",
    [
        "not json",
        "[]",
        json.dumps({"v": 2}),
        json.dumps({"v": 1, "mark": "x", "waiting": {}, "settled": {}, "parts": {}}),
        json.dumps(
            {"v": 1, "mark": z(NOW), "waiting": {"a/b": [1, 0]}, "settled": {}, "parts": {}}
        ),
        json.dumps({"v": 1, "mark": z(NOW), "waiting": {}, "settled": {"XYZ": 1}, "parts": {}}),
        json.dumps({"v": 1, "mark": z(NOW), "waiting": {}, "settled": {}, "parts": {"0" * 16: 1}}),
        json.dumps({"v": 1, "mark": z(NOW), "waiting": {"c": [1, 2]}, "settled": {}, "parts": {}}),
    ],
)
def test_a_cursor_this_source_did_not_write_fails_loudly(stored):
    with pytest.raises(SourceError, match="remove the source and add it again"):
        m.Cursor.decode(stored)


def test_pruning_lets_go_of_what_google_no_longer_keeps():
    old, kept = NOW - 31 * DAY, NOW - 29 * DAY
    pruned = cursor(
        settled={"a" * 16: int(old.timestamp()), "b" * 16: int(kept.timestamp())},
        parts={"c" * 16: (2, int(old.timestamp())), "d" * 16: (2, int(kept.timestamp()))},
    ).pruned(NOW)
    assert set(pruned.settled) == {"b" * 16} and set(pruned.parts) == {"d" * 16}


def test_switched_on_again_the_mark_rises_and_meetings_ended_before_are_let_go():
    later = NOW + HOUR
    moved = cursor(
        waiting={"conf1": m.Waiting(NOW, True), "conf2": m.Waiting(later + MINUTE, False)}
    ).switched_on(later)
    assert moved.mark == later and set(moved.waiting) == {"conf2"}
    assert cursor().switched_on(NOW - DAY).mark == NOW  # the mark never goes back


# What a draft says


def test_the_title_names_the_meeting_code_and_the_start_in_utc():
    start = datetime(2026, 11, 12, 9, 0, 59, tzinfo=UTC)
    assert m.title("abc-mnop-xyz", start) == "Meet abc-mnop-xyz, 2026-11-12 09:00 UTC"
    assert m.title(None, start) == "Meet, 2026-11-12 09:00 UTC"
    for odd in ("ABC-mnop-xyz", "abc", "abc-mnop-xyz\n# x", "a-b-" + "c" * 40):
        assert m.title(odd, start) == "Meet, 2026-11-12 09:00 UTC", odd


def test_consecutive_entries_of_one_participant_are_one_turn():
    turns = m.turns(
        [
            said("p1", "Morning.", 60),
            said("p1", "Refunds\nfirst.", 63),
            said("p2", "Green.", 70),
            said("p3", "Hear me?", 80),
            said("p9", "Wrong room.", 90),  # not among the participants
            said("p5", "Hi.", 95),  # a name that is an address
        ],
        PEOPLE,
    )
    assert [(t.speaker, t.start, t.text) for t in turns] == [
        ("Ada Park", NOW + MINUTE, "Morning. Refunds first."),
        ("Brook Lane", NOW + timedelta(seconds=70), "Green."),
        ("Unknown speaker", NOW + timedelta(seconds=80), "Hear me?"),
        ("Unknown speaker", NOW + timedelta(seconds=90), "Wrong room."),
        ("Unknown speaker", NOW + timedelta(seconds=95), "Hi."),
    ]


def test_a_guest_who_types_a_signed_in_name_is_marked_as_a_guest_on_every_turn():
    """Anyone let into a meeting without signing in types the name they are shown by. A guest
    who typed "Ada Park" was written as "Ada Park" on every turn, so what the guest said read
    as said by the signed-in Ada Park."""
    people = {
        p.name: p
        for p in (
            person("p1", "Ada Park"),
            person("p7", "Ada Park", "anonymousUser"),
            person("p8", "Ada Park", "phoneUser"),
        )
    }
    turns = m.turns(
        [said("p1", "Ship Friday.", 60), said("p7", "Wire it.", 70), said("p8", "Yes.", 80)],
        people,
    )
    assert [t.speaker for t in turns] == [
        "Ada Park",
        "Ada Park (anonymous)",
        "Ada Park (by phone)",
    ]


def test_a_name_holding_an_at_sign_of_any_width_is_no_name():
    people = {
        p.name: p
        for p in (
            person("p1", "dee\uff20example.test", "anonymousUser"),
            person("p2", "dee\ufe6bexample.test"),
        )
    }
    turns = m.turns([said("p1", "Hi.", 60), said("p2", "Yo.", 70)], people)
    assert [t.speaker for t in turns] == ["Unknown speaker", "Unknown speaker"]
    assert m._participants(list(people.values())) == "2 without a name"


@pytest.mark.perf
def test_one_participants_long_run_of_entries_is_joined_in_linear_time():
    """Only time shows this. The old join copied the turn's text so far for every entry, so
    its time grew with the square of the text: 80,000 entries of 300 characters (24 MB) took
    over 15 s. Joined once, eight times the entries take about eight times as long
    (linear_cost)."""

    def entries(n: int) -> list[m.Entry]:
        return [said("p1", f"{k:06d}" + "x" * 294, 60 + k) for k in range(n)]

    assert_near_linear(lambda n: partial(m.turns, entries(n), PEOPLE), 5_000)
    (turn,) = m.turns(entries(40_000), PEOPLE)
    assert turn.text.startswith("000000x") and turn.text.endswith(" 039999" + "x" * 294)
    assert len(turn.text) == 40_000 * 301 - 1


def test_a_blank_entry_in_a_run_adds_no_space():
    turns = m.turns([said("p1", "One.", 60), said("p1", " ", 61), said("p1", "Two.", 62)], PEOPLE)
    assert [t.text for t in turns] == ["One. Two."]
    assert [t.text for t in m.turns([said("p1", "", 60), said("p1", "x", 61)], PEOPLE)] == ["x"]


def test_entries_are_taken_in_order_of_their_start_whatever_order_they_came_in():
    turns = m.turns([said("p2", "Second.", 70), said("p1", "First.", 60)], PEOPLE)
    assert [t.text for t in turns] == ["First.", "Second."]


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [(0, "00:00:00"), (3723, "01:02:03"), (-5, "-00:00:05"), (100 * 3600, "100:00:00")],
)
def test_a_turns_time_is_hours_minutes_and_seconds_since_the_meeting_began(seconds, shown):
    assert m.offset(NOW + timedelta(seconds=seconds), NOW) == shown


def test_the_text_is_a_heading_the_facts_and_a_line_a_turn():
    start = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    entries = [
        m.Entry(PEOPLE_NAME("p1"), "Morning.", start + MINUTE),
        m.Entry(PEOPLE_NAME("p6"), "Dialled in.", start + timedelta(minutes=2, seconds=5)),
    ]
    (draft,) = m.drafts(
        meeting(), transcript(), entries, list(PEOPLE.values()), "abc-mnop-xyz", "calls"
    )
    assert draft.text == (
        "# Meet abc-mnop-xyz, 2026-11-12 09:00 UTC\n"
        "\n"
        "- Started: 2026-11-12 09:00 UTC\n"
        "- Ended: 2026-11-12 10:05 UTC\n"
        "- Transcribed: 2026-11-12 09:01 to 10:06 UTC\n"
        "- Participants: Ada Park, Brook Lane, Cy Moss (anonymous), +1 555-xxx-xx42 (by"
        " phone), and 2 without a name\n"
        "\n"
        "- 00:01:00 Ada Park: Morning.\n"
        "- 00:02:05 +1 555-xxx-xx42 (by phone): Dialled in.\n"
    )
    assert (draft.title, draft.topic, draft.project, draft.label) == (
        "Meet abc-mnop-xyz, 2026-11-12 09:00 UTC",
        "Meet abc-mnop-xyz, 2026-11-12 09:00 UTC",
        "calls",
        "google-meet",
    )
    assert draft.source_ref == "meet:c1/t1" and "@" not in draft.text


def test_every_draft_s_source_ref_begins_with_the_kind_s_word():
    """M3: `KIND.ref_form` is the word before the first ":" of every source_ref this kind
    writes, so a note a person or an agent writes the same way is withheld as these are."""
    from cairn.sources import source_ref_word

    start = meeting().start
    entries = [
        m.Entry(PEOPLE_NAME(("p1", "p2")[k % 2]), f"Point {k}.", start + timedelta(seconds=k))
        for k in range(2 * m.TURNS_PER_PART + 1)
    ]
    parts = m.drafts(meeting(), transcript(), entries, list(PEOPLE.values()), None, "calls")
    assert len(parts) == 3
    assert {source_ref_word(d.source_ref) for d in parts} == {m.KIND.ref_form} == {"meet"}


def test_a_transcript_over_the_turns_a_part_holds_is_split_and_later_parts_hold_only_turns():
    start = meeting().start
    entries = [
        m.Entry(PEOPLE_NAME(("p1", "p2")[k % 2]), f"Point {k}.", start + timedelta(seconds=k))
        for k in range(2 * m.TURNS_PER_PART + 1)
    ]
    parts = m.drafts(meeting(), transcript(), entries, list(PEOPLE.values()), None, "calls")
    assert [d.source_ref for d in parts] == ["meet:c1/t1", "meet:c1/t1/2", "meet:c1/t1/3"]
    title = "Meet, 2026-11-12 09:00 UTC"
    assert [d.topic for d in parts] == [title, f"{title}, part 2", f"{title}, part 3"]
    assert parts[1].text.startswith(f"# {title}, part 2\n\n- 00:25:00 Ada Park: Point 1500.\n")
    assert "Participants" not in parts[1].text
    assert parts[2].text.count("\n- ") == 1


def test_each_skip_reason_is_a_fixed_sentence_that_says_what_follows():
    for reason in (m.GONE_MEETING, m.GONE_TRANSCRIPT, m.NO_ENTRIES, m.NOT_GENERATED, m.TOO_LONG):
        assert reason == " ".join(reason.split()) and ":" in reason
    assert m.GONE_MEETING.startswith("gone:") and m.GONE_TRANSCRIPT.startswith("gone:")
    assert m.NOT_GENERATED.startswith("no transcript was generated:")
    assert "wait_hours" in m.NOT_GENERATED


# The pull, against a stand-in of the Meet API


def test_every_list_asks_for_googles_largest_page_and_repeats_the_filter_beside_the_token():
    stand = Stand()
    stand.add(1, SINCE + HOUR, SINCE + 2 * HOUR)
    pages = iter(
        [
            {"conferenceRecords": [], "nextPageToken": "page-2"},
            {"conferenceRecords": [stand.record(stand.calls["call001"])]},
        ]
    )
    stand.answers["records"] = lambda request: httpx.Response(200, json=next(pages))
    got = stand.pull()
    assert refs(got) == ["meet:call001/t1"]
    first, second = stand.of("records")
    bound = 'start_time>="' + z(SINCE - DAY) + '"'
    assert dict(first.url.params) == {"filter": bound, "pageSize": "100"}
    assert dict(second.url.params) == {"filter": bound, "pageSize": "100", "pageToken": "page-2"}
    assert stand.of("transcripts")[0].url.params["pageSize"] == "100"
    assert stand.of("entries")[0].url.params["pageSize"] == "100"
    assert stand.of("participants")[0].url.params["pageSize"] == "250"
    assert "pageSize" not in stand.of("space")[0].url.params


def test_participants_and_the_space_are_asked_once_a_meeting_and_a_404_leaves_no_code():
    stand = Stand()
    start = SINCE + HOUR
    said = [("p1", "Hello.", start + MINUTE)]
    stand.add(
        1,
        start,
        start + HOUR,
        gone={"space"},
        transcripts=[("t1", "FILE_GENERATED", said), ("t2", "FILE_GENERATED", said)],
    )
    got = stand.pull()
    assert refs(got) == ["meet:call001/t1", "meet:call001/t2"]
    assert {d.title for d in got.drafts} == {f"Meet, {start:%Y-%m-%d %H:%M} UTC"}
    assert len(stand.of("participants")) == 1 and len(stand.of("space")) == 1


def test_a_meeting_no_longer_listed_is_read_by_name_while_it_waits():
    stand = Stand()
    start, end = NOW - 30 * HOUR, NOW - 28 * HOUR
    call = stand.add(1, start, end, state="ENDED")
    first = stand.pull(since=start - HOUR)
    assert first.drafts == [] and "call001" in json.loads(first.cursor)["waiting"]
    stand.now = NOW + 6 * HOUR  # its start is now more than a day before the mark
    call.transcripts = [("t1", "FILE_GENERATED", call.transcripts[0][2])]
    got = stand.pull(first.cursor, since=start - HOUR)
    assert refs(got) == ["meet:call001/t1"]
    assert stand.of("records")[0].url.params["filter"] == f'start_time>="{z(NOW - DAY)}"'
    assert len(stand.of("record")) == 1  # its start and space, asked by name
    assert got.drafts[0].title == f"Meet abc-defg-hij, {start:%Y-%m-%d %H:%M} UTC"


def test_a_meeting_first_seen_after_its_wait_is_looked_at_once_then_dropped():
    """Sync was down longer than wait_hours: what Google generated meanwhile is still
    written, and what it did not is reported, once."""
    stand = Stand()
    stand.add(1, SINCE + HOUR, SINCE + 2 * HOUR)
    stand.add(2, SINCE + 3 * HOUR, SINCE + 4 * HOUR, state="ENDED")
    got = stand.pull(wait_hours=6)
    assert refs(got) == ["meet:call001/t1"]
    ((skip),) = got.skipped
    assert skip.ref == "meet:call002" and skip.reason == m.NOT_GENERATED
    assert json.loads(got.cursor)["waiting"] == {}
    stand.now += HOUR
    again = stand.pull(got.cursor, wait_hours=6)
    assert again.drafts == [] and again.skipped == []
    assert stand.of("transcripts") == []  # both still listed, neither asked about


def test_a_transcript_of_another_state_is_waited_for_like_an_unfinished_one():
    stand = Stand()
    stand.add(1, SINCE + HOUR, SINCE + 2 * HOUR, state="STATE_UNSPECIFIED")
    got = stand.pull()
    assert got.drafts == [] and stand.of("entries") == []
    assert json.loads(got.cursor)["waiting"]["call001"][1] == 1


def test_a_meeting_gone_when_its_participants_are_asked_is_skipped_once():
    stand = Stand()
    stand.add(1, SINCE + HOUR, SINCE + 2 * HOUR, gone={"participants"})
    got = stand.pull()
    assert got.drafts == [] and [(s.ref, s.reason) for s in got.skipped] == [
        ("meet:call001", m.GONE_MEETING)
    ]
    stand.now += HOUR
    assert stand.pull(got.cursor).skipped == [] and stand.of("transcripts") == []


def test_a_meeting_that_ended_before_since_is_never_asked_about():
    stand = Stand()
    stand.add(1, SINCE - 3 * HOUR, SINCE - timedelta(microseconds=1))
    stand.add(2, SINCE - 3 * HOUR, SINCE)
    assert refs(stand.pull()) == ["meet:call002/t1"]
    assert {r.url.path.split("/")[3] for r in stand.of("transcripts")} == {"call002"}


def test_switched_off_and_on_it_reads_nothing_that_ended_while_it_was_off():
    stand = Stand()
    stand.add(1, NOW - 3 * HOUR, NOW - 2 * HOUR, state="ENDED")
    first = stand.pull()
    assert "call001" in json.loads(first.cursor)["waiting"]
    stand.calls["call001"].transcripts[0] = ("t1", "FILE_GENERATED", [])
    stand.now += 2 * HOUR
    again = stand.pull(first.cursor, since=NOW + HOUR)  # off, then on an hour ago
    assert again.drafts == [] and again.skipped == [] and stand.of("transcripts") == []
    assert json.loads(again.cursor)["waiting"] == {}


def test_a_pull_stops_at_its_budget_of_requests_and_the_next_goes_on(monkeypatch):
    monkeypatch.setattr(m, "BUDGET", 8)  # the listing, then four requests a meeting
    stand = Stand()
    for n in (1, 2, 3):
        stand.add(n, SINCE + n * HOUR, SINCE + n * HOUR + 30 * MINUTE)
    cut = stand.pull()
    assert refs(cut) == ["meet:call001/t1"] and len(stand.requests) == 9  # with the token's
    rest = stand.pull(cut.cursor)
    assert refs(rest) == ["meet:call002/t1"]


def test_a_deadline_before_it_signs_in_or_lists_the_meetings_fails_the_pull():
    stand = Stand()
    stand.add(1, SINCE + HOUR, SINCE + 2 * HOUR)
    with pytest.raises(SourceError, match="no time was left to sign in"):
        asyncio.run(stand.source(deadline=NOW).pull(None))
    assert stand.requests == []
    original = stand.handler

    def slow_token(request):
        stand.now += MINUTE if request.url.path == "/token" else timedelta(0)
        return original(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(slow_token))
    with pytest.raises(SourceError, match="could not be listed"):
        asyncio.run(stand.source(deadline=NOW + MINUTE, http=http).pull(None))
    assert stand.of("records") == []


def test_a_deadline_before_one_meeting_is_finished_fails_the_pull():
    stand = Stand()
    stand.add(1, SINCE + HOUR, SINCE + 2 * HOUR)
    original = stand.handler

    def slow(request):
        stand.now += MINUTE if "transcripts" in request.url.path else timedelta(0)
        return original(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    with pytest.raises(SourceError, match="did not finish one meeting"):
        asyncio.run(stand.source(deadline=NOW + MINUTE, http=http).pull(None))


def test_a_transcript_longer_than_cairn_reads_is_skipped(monkeypatch):
    monkeypatch.setattr(m, "MAX_PAGES", 2)
    stand = Stand()
    stand.add(1, SINCE + HOUR, SINCE + 2 * HOUR)
    page = {"transcriptEntries": [], "nextPageToken": "more"}
    stand.answers["entries"] = lambda request: httpx.Response(200, json=page)
    got = stand.pull()
    assert got.drafts == [] and [(s.ref, s.reason) for s in got.skipped] == [
        ("meet:call001/t1", m.TOO_LONG)
    ]


def test_a_list_of_more_items_than_its_pages_hold_ends_as_a_list_of_too_many_pages(monkeypatch):
    """Google chooses how many items a page holds, so a list bounded by its pages alone kept
    whatever one page brought. A list now ends at MAX_PAGES full pages of items too, with the
    outcome too many pages have: a transcript TOO_LONG, any other list a SourceError."""
    monkeypatch.setattr(m, "MAX_PAGES", 1)
    monkeypatch.setattr(m, "ENTRIES_PAGE", 2)
    stand = Stand()
    start = SINCE + HOUR
    said = [("p1", f"Line {k}.", start + k * MINUTE) for k in range(3)]  # one page of three
    stand.add(1, start, start + HOUR, transcripts=[("t1", "FILE_GENERATED", said)])
    got = stand.pull()
    assert got.drafts == [] and [(s.ref, s.reason) for s in got.skipped] == [
        ("meet:call001/t1", m.TOO_LONG)
    ]
    monkeypatch.setattr(m, "RECORDS_PAGE", 2)
    for n in (2, 3):
        stand.add(n, start + n * MINUTE, start + HOUR)
    with pytest.raises(SourceError, match="listed more than this server reads"):
        stand.pull()


@pytest.mark.parametrize(
    ("endpoint", "answer", "said"),
    [
        (
            "records",
            error(403, "ACCESS_TOKEN_SCOPE_INSUFFICIENT"),
            "Google Meet refused access (HTTP 403) to conferenceRecords.list: check that the"
            f" credential in {VAR} carries the scope"
            " https://www.googleapis.com/auth/meetings.space.readonly (for a service account,"
            " in its domain-wide delegation), that the Google Meet REST API is enabled in its"
            " Cloud project, and that the person it reads for may use Meet",
        ),
        (
            "entries",
            error(401),
            "Google Meet refused access (HTTP 401) to transcripts.entries.list: check that",
        ),
        (
            "records",
            error(429, "RATE_LIMIT_EXCEEDED"),
            "Google Meet still answered with a rate limit to conferenceRecords.list after 4"
            " retries: the next run goes on",
        ),
        (
            "participants",
            error(503),
            "Google Meet answered HTTP 503 to participants.list, a fault on its side: the next"
            " run tries again",
        ),
        (
            "records",
            httpx.Response(302, headers={"location": "https://elsewhere.example.test/"}),
            "Google Meet answered conferenceRecords.list with a redirect (HTTP 302), which"
            " this server does not follow: check base_url",
        ),
        (
            "records",
            error(404),
            "Google Meet answered 404 to conferenceRecords.list: check base_url (it ends in /v2)",
        ),
        (
            "transcripts",
            error(400),
            "Google Meet refused the request to transcripts.list (HTTP 400)",
        ),
        ("space", error(403), "Google Meet refused access (HTTP 403) to spaces.get: check that"),
    ],
)
def test_each_refusal_says_what_happened_and_what_to_check(endpoint, answer, said):
    stand = Stand()
    stand.add(1, SINCE + HOUR, SINCE + 2 * HOUR)
    stand.answers[endpoint] = lambda request: answer
    with pytest.raises(SourceError) as failed:
        stand.pull()
    message = str(failed.value)
    assert message.startswith(said), message
    assert CANARY not in message and TOKEN not in message


@pytest.mark.parametrize(
    ("endpoint", "body", "what"),
    [
        (
            "records",
            {"conferenceRecords": [{"name": "conferenceRecords/a/b", "startTime": z(NOW)}]},
            "conferenceRecords.list",
        ),
        (
            "records",
            {"conferenceRecords": [{"name": "conferenceRecords/a", "startTime": "today"}]},
            "conferenceRecords.list",
        ),
        ("records", {"conferenceRecords": [], "nextPageToken": 7}, "conferenceRecords.list"),
        (
            "transcripts",
            {
                "transcripts": [
                    {"name": "conferenceRecords/other/transcripts/t1", "state": "FILE_GENERATED"}
                ]
            },
            "transcripts.list",
        ),
        (
            "transcripts",
            {"transcripts": [{"name": "conferenceRecords/call001/transcripts/t1"}]},
            "transcripts.list",
        ),
        (
            "entries",
            {"transcriptEntries": [{"participant": 7, "text": "x", "startTime": z(NOW)}]},
            "transcripts.entries.list",
        ),
        (
            "entries",
            {"transcriptEntries": [{"participant": "p", "text": "x"}]},
            "transcripts.entries.list",
        ),
        ("participants", {"participants": [{"signedinUser": {}}]}, "participants.list"),
        ("space", [CANARY], "spaces.get"),
    ],
)
def test_an_answer_not_of_the_documented_shape_fails_the_pull(endpoint, body, what):
    stand = Stand()
    stand.add(1, SINCE + HOUR, SINCE + 2 * HOUR)
    stand.answers[endpoint] = lambda request: httpx.Response(200, json=body)
    with pytest.raises(SourceError) as failed:
        stand.pull()
    assert str(failed.value) == f"Google Meet's answer to {what} is not the documented shape"


def test_the_token_and_the_credential_reach_no_log_line(caplog):
    from cairn.sources import source_client

    stand = Stand()
    stand.add(1, SINCE + HOUR, SINCE + 2 * HOUR)
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.DEBUG)
    try:
        with caplog.at_level(logging.DEBUG):
            source = stand.source(http=source_client(transport=httpx.MockTransport(stand.handler)))
            got = asyncio.run(source.pull(None))
    finally:
        root.setLevel(level)
    assert refs(got) == ["meet:call001/t1"]
    assert holds_secret(f"x {TOKEN} y", tuple(source.secrets))
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (TOKEN, SECRET, REFRESH):
        assert secret not in logged
    assert "HTTP Request" not in logged


def test_a_transcript_cairn_fails_on_is_skipped_once_and_the_rest_written(monkeypatch):
    """Anything but a SourceError while one transcript is made into drafts used to fail the
    pull, so the cursor never moved past that meeting."""
    stand = Stand()
    for n in (1, 2, 3):
        stand.add(n, SINCE + n * HOUR, SINCE + n * HOUR + 30 * MINUTE)
    made = m.drafts

    def failing(meeting, transcript, *rest):
        if meeting.cid == "call002":
            raise KeyError("startTime")
        return made(meeting, transcript, *rest)

    monkeypatch.setattr(m, "drafts", failing)
    first = stand.pull()
    assert refs(first) == ["meet:call001/t1", "meet:call003/t1"]
    assert [(s.ref, s.reason) for s in first.skipped] == [("meet:call002/t1", m.UNREADABLE)]
    stand.now += 3 * DAY  # every wait passed: each meeting is dropped without a request
    again = stand.pull(first.cursor)
    assert again.drafts == again.skipped == []
