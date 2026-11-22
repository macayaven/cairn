"""Unit tests for the Granola source: what tests/acceptance/test_c3_granola.py does not pin.
HTTP goes through httpx.MockTransport; time and sleep are injected. Every key, name and
address here is made up."""

import json
import logging
from datetime import UTC, datetime, timedelta
from functools import partial

import httpx
import pytest

from cairn.sources import KINDS, SourceError
from cairn.sources import granola as g
from support.linear_cost import assert_near_linear
from support.sources.granola import (
    BASE,
    KEY,
    NOW,
    SINCE,
    VAR,
    Stand,
    item,
    meeting,
    nid,
    origins,
    settings,
    z,
)


def refusal(config) -> str:
    with pytest.raises(ValueError) as refused:
        g.check_config(config)
    return str(refused.value)


# The config, setting by setting


def test_the_kind_is_registered_starts_off_and_writes_with_its_own_via():
    kind = KINDS["granola"]
    assert kind is g.KIND and kind.starts_off and kind.via == "granola"
    assert kind.check is g.check_config and kind.open is g.open_source


def test_the_defaults_are_filled_in_and_what_is_stored_checks_the_same():
    checked = g.check_config({"key_env": VAR})
    assert checked == {
        "key_env": VAR,
        "projects": [],
        "base_url": "https://public-api.granola.ai/v1",
        "transcripts": False,
        "settle_minutes": 30,
        "lookback_days": 30,
    }
    assert g.check_config(checked) == checked


def test_a_folder_or_space_id_is_kept_as_given_and_a_null_one_is_left_out():
    assert g.check_config(settings(folder_id="FOL_" + "Abcdefghij0123"))["folder_id"] == (
        "FOL_Abcdefghij0123"
    )
    assert "space_id" not in g.check_config(settings(space_id=None))


def test_base_url_loses_a_trailing_slash_and_keeps_its_path():
    assert g.check_config(settings(base_url=BASE + "/"))["base_url"] == BASE
    assert g.check_config(settings(base_url="http://[::1]:8123/v1"))["base_url"] == (
        "http://[::1]:8123/v1"
    )


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"key_env": "granola_key"}, "key_env names an environment variable"),
        ({"key_env": "A" * 129}, "key_env names an environment variable"),
        ({"projects": ["meetings", "planning"]}, "projects names at most one project"),
        ({"projects": [""]}, "projects holds a project's name"),
        ({"projects": [7]}, "projects holds a project's name"),
        ({"base_url": "https://granola.example.test/v1?x=1"}, "base_url has no query"),
        ({"base_url": "https://granola.example.test/v1#top"}, "base_url has no query"),
        ({"base_url": "//granola.example.test/v1"}, "base_url is https"),
        ({"base_url": 7}, "base_url is the address"),
        ({"folder_id": "fol_" + "abc"}, "folder_id is a folder's id"),
        ({"space_id": "fol_" + "Abcdefghij0123"}, "space_id is a space's id"),
        ({"transcripts": 1}, "transcripts is true or false"),
        ({"settle_minutes": True}, "settle_minutes is a whole number from 0 to 1440"),
        ({"settle_minutes": 2.5}, "settle_minutes is a whole number from 0 to 1440"),
        ({"lookback_days": None}, "lookback_days is a whole number from 1 to 365"),
    ],
)
def test_each_refusal_names_its_setting_and_says_what_it_takes(given, said):
    assert said in refusal(settings(**given))


def test_a_missing_key_env_and_an_unknown_setting_are_named():
    assert "needs key_env" in refusal({"projects": []})
    said = refusal(settings(colour="blue"))
    assert "'colour'" in said and "key_env" in said  # and it lists the settings it takes


def test_a_setting_whose_name_could_be_a_key_is_refused_without_repeating_it():
    leaked = "grn_" + "PastedAsAName99"
    said = refusal(settings(**{leaked: True}))
    assert leaked not in said and "does not take" in said
    odd = "a setting with\nlines"
    assert odd not in refusal(settings(**{odd: True}))


@pytest.mark.parametrize(
    "value",
    [
        "https://grn_" + "AKeyInTheHost0.example.test/v1",
        "https://granola.example.test/grn_" + "AKeyInThePath1",
        "https://granola.example.test/ghp_" + "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zAaB3d",
    ],
)
def test_a_base_url_carrying_a_key_is_refused_by_name_without_the_key(value):
    said = refusal(settings(base_url=value))
    assert "base_url" in said and "AKeyIn" not in said and "ghp_" not in said


def test_a_project_name_that_looks_like_a_key_is_refused_without_repeating_it():
    said = refusal(settings(projects=["grn_" + "ProjectNamedKey1"]))
    assert "projects" in said and "ProjectNamedKey1" not in said


def test_opening_reads_the_key_from_the_environment_and_names_only_the_variable():
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    checked = g.check_config(settings())
    for env in ({}, {VAR: "  "}):
        with pytest.raises(SourceError) as missing:
            g.open_source("meet", checked, http=http, env=env, since=SINCE)
        said = str(missing.value)
        assert VAR in said and "'meet'" in said and "Granola API key" in said
    source = g.open_source("meet", checked, http=http, env={VAR: f" {KEY}\n"}, since=SINCE)
    assert source.secrets == (KEY, f"Bearer {KEY}")
    assert KEY not in repr(source)


# The cursor


def test_a_cursor_is_compact_json_of_digests_and_reads_back_as_written():
    cursor = g.Cursor(
        mark=NOW - timedelta(minutes=30),
        rescan=NOW,
        floor=SINCE,
        seen={g.digest("not_" + "Abcdefghij0123"): int(SINCE.timestamp()) + 60},
        parts={g.digest("not_" + "Zyxwvutsrq9876"): (1, int(SINCE.timestamp()) + 120)},
    )
    text = cursor.encode()
    assert " " not in text and json.loads(text)["v"] == 1
    assert "not_" not in text.lower() and "Abcdefghij0123" not in text
    assert g.Cursor.decode(text) == cursor
    assert g.Cursor.decode(g.Cursor.first(SINCE).encode()) == g.Cursor.first(SINCE)


def test_a_digest_is_sixteen_hex_digits_and_ignores_the_ids_case():
    one = g.digest("not_" + "Abcdefghij0123")
    assert len(one) == 16 and int(one, 16) >= 0
    assert one == g.digest("NOT_" + "ABCDEFGHIJ0123")


@pytest.mark.parametrize(
    "stored",
    [
        "not json",
        "[]",
        '{"v": 2}',
        '{"v": 1, "mark": "yesterday", "rescan": null, "floor": "2026-11-01T00:00:00+00:00",'
        ' "seen": {}, "parts": {}}',
        '{"v": 1, "mark": "2026-11-01T00:00:00+00:00", "rescan": null,'
        ' "floor": "2026-11-01T00:00:00+00:00", "seen": {"not_x": 1}, "parts": {}}',
        '{"v": 1, "mark": "2026-11-01T00:00:00+00:00", "rescan": null,'
        ' "floor": "2026-11-01T00:00:00+00:00", "seen": {}, "parts": {"0123456789abcdef": 1}}',
    ],
)
def test_a_cursor_that_is_not_this_sources_fails_loudly(stored):
    with pytest.raises(SourceError) as broken:
        g.Cursor.decode(stored)
    assert "cursor" in str(broken.value)


def test_pruning_keeps_only_what_a_rescan_could_still_list():
    floor = int(SINCE.timestamp())
    cursor = g.Cursor(
        mark=NOW,
        rescan=NOW,
        floor=SINCE - timedelta(days=1),
        seen={"0" * 16: floor - 1, "1" * 16: floor, "2" * 16: floor + 1},
        parts={"3" * 16: (1, floor - 1), "4" * 16: (2, floor)},
    )
    kept = cursor.pruned(SINCE)
    assert kept.floor == SINCE
    assert set(kept.seen) == {"1" * 16, "2" * 16} and set(kept.parts) == {"4" * 16}
    assert cursor.pruned(SINCE - timedelta(days=2)).floor == cursor.floor  # never back


# What a draft says


def test_the_topic_is_the_title_a_comma_and_the_date():
    assert g.topic("Payments weekly", datetime(2026, 11, 11, tzinfo=UTC)) == (
        "Payments weekly, 2026-11-11"
    )


def test_a_long_title_is_cut_so_the_page_slug_keeps_the_date_and_part():
    from cairn.compiler import slugify

    long = "Quarterly planning review with the payments and ledger teams " * 4
    day = datetime(2026, 11, 11, tzinfo=UTC)
    named = g.topic(long, day)
    assert named.endswith(", 2026-11-11") and len(named) <= 200
    for suffix in (" transcript", " transcript, part 99"):
        assert slugify(named + suffix).endswith("2026-11-11-" + slugify(suffix.strip()))
    wide = g.topic("会議" * 300, day)  # no ASCII to slug: still bounded
    assert wide.endswith(", 2026-11-11") and len(wide) <= 200


def test_the_text_is_a_heading_the_facts_and_the_summary_unchanged():
    event = {
        "event_title": "Payments weekly",
        "invitees": [],
        "organiser": None,
        "calendar_event_id": None,
        "scheduled_start_time": "2026-11-12T00:30:00+02:00",
        "scheduled_end_time": "2026-11-12T01:45:00+02:00",
    }
    text = g.summary_text(meeting(calendar_event=event))
    assert text == (
        "# Payments weekly\n\n"
        "- When: 2026-11-11 22:30 UTC, for 1 hour 15 minutes\n"
        "- Attendees: Ada Park, and 1 more without a name\n"
        "- Owner: Dee Quinn\n\n"
        "### Decisions\n\n- Ship it.\n"
    )


def test_without_a_calendar_event_the_time_is_when_the_note_was_created():
    text = g.summary_text(meeting(attendees=[], owner={"name": None, "email": "x@example.test"}))
    assert "- When: 2026-11-10 21:00 UTC, when the note was created\n" in text
    assert "Attendees" not in text and "Owner" not in text and "@" not in text


def test_a_name_that_holds_an_address_is_no_name():
    """Granola's name fields are typed by people (a calendar guest's name is often their
    address). cairn wrote any name as it came, so "ada@example.test" in a name field reached
    the facts, though the email fields beside it were never written."""
    text = g.summary_text(
        meeting(
            owner={"name": "dee@example.test", "email": "dee@example.test"},
            attendees=[
                {"name": "Ada Park", "email": "a@example.test"},
                {"name": "brook@example.test", "email": "brook@example.test"},
                {"name": "cy\uff20example.test", "email": "cy@example.test"},
                {"name": "eve\ufe6bexample.test", "email": "eve@example.test"},
            ],
        )
    )
    assert "- Attendees: Ada Park, and 3 more without a name\n" in text
    assert "Owner" not in text and "example.test" not in text


def test_a_speaker_name_that_holds_an_address_is_no_name():
    said = g.turns(
        [
            item("ada@example.test", "Morning.", 0, diarization_label="Speaker A"),
            item("brook\uff20example.test", "Hello.", 2),
            item(None, "Mine.", 4, attribution="me"),
            item(None, "Hm.", 6, diarization_label="cy@example.test"),
        ],
        owner=None,
    )
    assert [t.speaker for t in said] == [
        "Speaker A",
        "an unknown speaker",
        "the note's owner",
        "an unknown speaker",
    ]
    note = meeting(owner={"name": "dee@example.test", "email": "dee@example.test"})
    assert g.turns([item(None, "Mine.", 0, attribution="me")], note.owner)[0].speaker == (
        "the note's owner"
    )


def test_a_title_or_name_from_granola_stays_on_one_line():
    text = g.summary_text(
        meeting(title="Retro\n# Not a heading", attendees=[{"name": "Ada\nPark", "email": "a@x"}])
    )
    assert text.startswith("# Retro # Not a heading\n") and "- Attendees: Ada Park\n" in text


def test_a_blank_markdown_summary_falls_back_to_the_text_one():
    assert g.summary_text(meeting(summary_markdown="  ")).endswith("\n\nPlain.\n")
    assert g.summary_text(meeting(summary_markdown=None, summary_text="")).endswith(
        "\n\nGranola gave no summary.\n"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"created_at": "yesterday"},
        {"owner": None},
        {"attendees": None},
        {"attendees": ["Ada"]},
        {"summary_text": None},
        {"calendar_event": []},
        {"transcript": "said"},
        {"id": "not_" + "Somethingelse0"},
    ],
)
def test_a_note_not_of_the_documented_shape_fails_loudly(change):
    with pytest.raises(SourceError) as wrong:
        meeting(**change)
    assert "Get note" in str(wrong.value)


# Transcripts


def test_consecutive_items_of_one_speaker_make_one_turn_and_unnamed_ones_are_labelled():
    said = g.turns(
        [
            item("Ada Park", "Morning.", 0),
            item("Ada Park", " Shall   we start?", 2),
            item(None, "Yes.", 5, diarization_label="Speaker B"),
            item(None, "I own it.", 9, attribution="me"),
            item(None, "Agreed.", 12, attribution="them"),
            item(None, "Hm.", 14),
        ],
        owner="Dee Quinn",
    )
    assert [(t.speaker, t.text) for t in said] == [
        ("Ada Park", "Morning. Shall we start?"),
        ("Speaker B", "Yes."),
        ("Dee Quinn", "I own it."),
        ("another participant", "Agreed."),
        ("an unknown speaker", "Hm."),
    ]
    assert said[0].start == NOW and said[1].start == NOW + timedelta(seconds=5)
    assert g.turns([item(None, "Mine.", 0, attribution="me")], owner=None)[0].speaker == (
        "the note's owner"
    )


@pytest.mark.parametrize(
    "bad", [["text"], [{"speaker": None, "text": "x", "start_time": "2026-11-13T12:00:00Z"}]]
)
def test_a_transcript_item_not_of_the_documented_shape_fails_loudly(bad):
    with pytest.raises(SourceError):
        g.turns(bad, owner=None)


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [(0, "00:00:00"), (90, "00:01:30"), (3723, "01:02:03"), (100 * 3600, "100:00:00"),
     (-130, "-00:02:10")],
)  # fmt: skip
def test_a_turns_time_is_hours_minutes_and_seconds_from_the_start(seconds, shown):
    assert g.offset(NOW + timedelta(seconds=seconds), NOW) == shown


def summary_of(found: "g.Meeting") -> "g.NoteDraft":
    return g.summary_draft(found, "meetings")


def test_times_count_from_the_calendar_start_else_from_the_first_item():
    event = {
        "event_title": None, "invitees": [], "organiser": None, "calendar_event_id": None,
        "scheduled_start_time": "2026-11-13T12:00:10Z", "scheduled_end_time": None,
    }  # fmt: skip
    said = g.turns([item("Ada", "Early.", 0), item("Bo", "On time.", 70)], owner=None)
    scheduled = meeting(calendar_event=event)
    (part,) = g.transcript_drafts(summary_of(scheduled), scheduled, said)
    assert "- -00:00:10 Ada: Early.\n" in part.text and "- 00:01:00 Bo: On time.\n" in part.text
    unscheduled = meeting()  # created the evening before: times count from the first item
    (part,) = g.transcript_drafts(summary_of(unscheduled), unscheduled, said)
    assert "- 00:00:00 Ada: Early.\n" in part.text and "- 00:01:10 Bo: On time.\n" in part.text


def test_a_transcript_part_is_headed_by_its_topic_and_parts_hold_1500_turns():
    found = meeting()
    summary = summary_of(found)
    said = [g.Turn(f"P{k % 2}", NOW + timedelta(seconds=k), f"Point {k}.") for k in range(3001)]
    parts = g.transcript_drafts(summary, found, said)
    assert [p.source_ref for p in parts] == [
        summary.source_ref + "/transcript",
        summary.source_ref + "/transcript/2",
        summary.source_ref + "/transcript/3",
    ]
    assert parts[2].topic == summary.topic + " transcript, part 3"
    assert parts[0].text.startswith(f"# {summary.topic} transcript\n\n- 00:00:00 P0: Point 0.\n")
    assert [p.text.count("\n- ") for p in parts] == [1500, 1500, 1]
    assert {(p.project, p.label) for p in parts} == {("meetings", "granola")}
    assert parts[1].title == "Payments weekly transcript, part 2"


# Requests: waits and wording


@pytest.mark.parametrize(
    ("header", "wait"),
    [("7", 7.0), ("0", 0.0), ("600", 60.0), (None, 5.0), ("soon", 5.0), ("-3", 5.0),
     ("nan", 5.0)],
)  # fmt: skip
def test_a_rate_limit_waits_its_retry_after_capped_else_five_seconds(header, wait):
    assert g.retry_wait(header) == wait


@pytest.mark.parametrize(
    ("status", "code", "endpoint", "words"),
    [
        (401, "INVALID_API_KEY", "List notes", "unknown, revoked or expired"),
        (401, None, "List notes", "refused the API key"),
        (403, "IP_ALLOWLIST_DENIED", "Get note", "IP allowlist"),
        (403, "SUBSCRIPTION_INACTIVE", "List notes", "subscription is not active"),
        (403, "ACCOUNT_SUSPENDED", "List notes", "suspended"),
        (403, "FORBIDDEN", "List notes", "may not do this"),
        (429, "RATE_LIMITED", "List notes", "after 3 retries"),
        (500, "INTERNAL_ERROR", "Get note", "HTTP 500 to Get note"),
        (503, None, "List notes", "the next run tries again"),
        (404, "NOT_FOUND", "List notes", "check base_url"),
        (400, "VALIDATION_ERROR", "List notes", "folder_id or space_id"),
        (302, None, "List notes", "redirect"),
        (418, None, "Get transcript", "HTTP 418 to Get transcript"),
    ],
)
def test_each_refusal_says_what_happened_and_what_to_do(status, code, endpoint, words):
    said = g.refusal(status, code, endpoint, VAR)
    assert words in said
    if status in (401, 403):
        assert VAR in said


# The pull, against a small stand-in


def test_drafts_come_oldest_first_then_by_id_whatever_its_case_and_once_from_both_scans():
    stand = Stand()
    tie = SINCE + timedelta(days=1)
    upper = stand.add(1, tie, id="NOT_" + "UNIT0000000002")
    lower = stand.add(2, tie, id="not_" + "unit0000000001")
    early = stand.add(3, SINCE + timedelta(hours=1))
    got = stand.pull()  # the first pull lists by update and rescans by creation
    lists = [r for r in stand.requests if r.url.path.endswith("/notes")]
    assert {p for r in lists for p in r.url.params if p.endswith("_after")} == {
        "updated_after",
        "created_after",
    }
    assert origins(got) == [g.ORIGIN + early, g.ORIGIN + lower, g.ORIGIN + upper]


def test_the_bounds_sent_are_whole_seconds_an_hour_before_the_moment_they_stand_for():
    stand = Stand()
    since = SINCE + timedelta(microseconds=123456)
    stand.pull(since=since)
    asked = {k: v for r in stand.requests for k, v in r.url.params.items() if k.endswith("after")}
    expected = z((since - g.OVERLAP).replace(microsecond=0))
    assert asked == {"updated_after": expected, "created_after": expected}


def test_switched_on_again_it_looks_for_changes_from_the_new_switch_on_not_the_old_mark():
    stand = Stand()
    cursor = stand.pull().cursor
    stand.requests.clear()
    stand.now = NOW + timedelta(days=40)
    later = NOW + timedelta(days=39)
    stand.pull(cursor, since=later)
    (changed,) = [
        r.url.params["updated_after"] for r in stand.requests if "updated_after" in r.url.params
    ]
    assert changed == z(later - g.OVERLAP)


def test_a_note_listed_with_an_id_of_another_form_fails_the_pull_before_any_get():
    stand = Stand()
    stand.add(1, SINCE + timedelta(hours=1), id="not_../../folders")
    with pytest.raises(SourceError) as wrong:
        stand.pull()
    assert "List notes" in str(wrong.value) and "../" not in str(wrong.value)
    assert all(r.url.path.endswith("/notes") for r in stand.requests)


def test_a_page_that_has_more_without_a_cursor_fails_rather_than_looping():
    stand = Stand()
    stand.answers["list"] = lambda r: httpx.Response(
        200, json={"notes": [], "hasMore": True, "cursor": None}
    )
    with pytest.raises(SourceError) as wrong:
        stand.pull()
    assert "List notes" in str(wrong.value)


def test_a_listing_longer_than_a_pull_reads_fails_and_says_how_to_narrow_it(monkeypatch):
    monkeypatch.setattr(g, "MAX_LIST_PAGES", 2)
    stand = Stand()
    for n in range(1, 62):
        stand.add(n, SINCE + timedelta(minutes=n))
    with pytest.raises(SourceError) as wrong:
        stand.pull()
    said = str(wrong.value)
    assert "lookback_days" in said and "folder_id" in said
    assert len(stand.requests) == 2


def test_an_answer_larger_than_the_bound_is_not_parsed(monkeypatch):
    monkeypatch.setattr(g, "MAX_RESPONSE_BYTES", 1_000)
    stand = Stand()
    stand.add(1, SINCE + timedelta(hours=1), summary_markdown="x" * 2_000)
    with pytest.raises(SourceError) as wrong:
        stand.pull()
    assert "larger than this server reads" in str(wrong.value) and "Get note" in str(wrong.value)


def test_a_network_error_names_its_class_never_the_address():
    stand = Stand()

    def down(request):
        raise httpx.ConnectError("cannot connect to granola.example.test", request=request)

    stand.answers["list"] = down
    with pytest.raises(SourceError) as wrong:
        stand.pull()
    said = str(wrong.value)
    assert said == "cannot reach Granola for List notes: ConnectError"


def transcript_note(stand: Stand, n: int) -> str:
    note_id = stand.add(n, SINCE + timedelta(hours=n))
    stand.segments[note_id] = [item("Ada", f"Line {k}.", k) for k in range(3)]
    return note_id


def test_a_403_on_the_transcript_other_than_switched_off_fails_the_pull_and_loses_nothing():
    stand = Stand()
    transcript_note(stand, 1)
    stand.answers["transcript"] = lambda r: httpx.Response(
        403, json={"code": "SUBSCRIPTION_INACTIVE", "message": "no"}
    )
    with pytest.raises(SourceError) as wrong:
        stand.pull(transcripts=True)
    assert "subscription is not active" in str(wrong.value) and VAR in str(wrong.value)
    stand.answers["transcript"] = lambda r: httpx.Response(403, text="<html>nope</html>")
    with pytest.raises(SourceError):
        stand.pull(transcripts=True)


def test_a_transcript_longer_than_a_pull_reads_is_skipped_and_the_summary_written(monkeypatch):
    monkeypatch.setattr(g, "MAX_TRANSCRIPT_PAGES", 2)
    stand = Stand()
    note_id = transcript_note(stand, 1)
    stand.segments[note_id] = [item("Ada", f"Line {k}.", k) for k in range(250)]
    got = stand.pull(transcripts=True)
    assert origins(got) == [g.ORIGIN + note_id]
    (skip,) = got.skipped
    assert skip.ref == note_id and skip.reason.startswith(
        "transcript longer than this server reads"
    )


def test_a_deadline_reached_while_listing_fails_the_pull_as_it_cannot_move():
    stand = Stand()
    stand.add(1, SINCE + timedelta(hours=1))
    with pytest.raises(SourceError) as late:
        stand.pull(deadline=NOW)
    assert "deadline" in str(late.value)
    assert stand.requests == []


def test_a_rate_limit_whose_wait_would_pass_the_deadline_is_not_waited_for():
    stand = Stand()
    stand.add(1, SINCE + timedelta(hours=1))
    stand.answers["list"] = lambda r: httpx.Response(429, headers={"retry-after": "30"})
    with pytest.raises(SourceError) as late:
        stand.pull(deadline=NOW + timedelta(seconds=10))
    assert "deadline" in str(late.value) and stand.slept == []


def test_a_pull_cut_by_its_deadline_moves_neither_mark_floor_nor_rescan():
    stand = Stand()
    for n in (1, 2, 3):
        stand.add(n, SINCE + timedelta(hours=n))
    first = g.Cursor.decode(stand.pull().cursor)

    ticks: list[int] = []

    def counted(request):
        ticks.append(1)
        stand.now += timedelta(minutes=1)
        return httpx.Response(200, json=stand.notes[request.url.path.split("/")[-1]])

    stand.now = NOW + timedelta(days=2)
    for n in (4, 5, 6):
        stand.add(n, NOW + timedelta(days=1, hours=n))
    stand.answers["get"] = counted
    started = stand.now
    cut = stand.pull(first.encode(), deadline=started + timedelta(minutes=2))
    assert len(cut.drafts) == 2
    after = g.Cursor.decode(cut.cursor)
    assert (after.mark, after.floor, after.rescan) == (first.mark, first.floor, first.rescan)
    assert len(after.seen) == len(first.seen) + 2


def test_a_note_half_written_keeps_its_place_while_it_waits_and_goes_on_from_there():
    stand = Stand()
    note_id = transcript_note(stand, 1)
    whole = stand.pull(transcripts=True)
    summary, said = whole.drafts
    assert said.source_ref == g.ORIGIN + note_id + "/transcript"
    # The run stopped after the summary; the note is edited before the next run.
    stand.notes[note_id]["updated_at"] = z(NOW + timedelta(minutes=50))
    stand.now = NOW + timedelta(hours=1)
    waiting = stand.pull(summary.resume, transcripts=True)
    assert waiting.drafts == []
    stand.now = NOW + timedelta(hours=2)
    rest = stand.pull(waiting.cursor, transcripts=True)
    assert origins(rest) == [said.source_ref]  # the summary is not written twice
    stand.now = NOW + timedelta(hours=3)
    assert stand.pull(rest.cursor, transcripts=True).drafts == []


def test_a_waiting_note_older_than_the_lookback_is_still_written_once_it_settles():
    stand = Stand()
    since = NOW - timedelta(days=5)
    note_id = stand.add(1, since + timedelta(hours=1), updated=NOW - timedelta(minutes=5))
    first = stand.pull(since=since, lookback_days=1)
    assert first.drafts == []
    stand.now = NOW + timedelta(hours=1)
    assert origins(stand.pull(first.cursor, since=since, lookback_days=1)) == [g.ORIGIN + note_id]


def test_the_key_reaches_no_log_line_whatever_the_loggers_level(caplog):
    from cairn.sources import source_client

    stand = Stand()
    transcript_note(stand, 1)
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.DEBUG)  # as a host application may leave it
    try:
        with caplog.at_level(logging.DEBUG):
            http = source_client(transport=httpx.MockTransport(stand.handler))
            got = stand.pull(transcripts=True, http=http)
    finally:
        root.setLevel(level)
    assert got.drafts
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert KEY not in logged and "HTTP Request" not in logged


@pytest.mark.parametrize(
    "value", ["0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59", "2026-11-13T10:00:00+24:00"]
)
def test_a_time_that_cannot_be_in_utc_is_not_a_moment(value):
    """A time Granola could not really send must come back as None, never as an exception
    that ends a pull without a reason."""
    assert g._moment(value) is None


def test_a_note_half_written_before_a_switch_off_lets_the_floor_rise_after_the_switch_on():
    """A note created before the switch-on is never read, so a note half written before the
    source was switched off is not continued after it is switched on again. But its entry in
    `parts` stayed and held the floor at its creation for good: no note read since was ever
    let go, and the cursor grew with every note."""
    stand = Stand()
    note_id = transcript_note(stand, 1)
    stopped = stand.pull(transcripts=True).drafts[0].resume  # the summary only; then off
    stand.notes[note_id]["updated_at"] = z(NOW + timedelta(hours=1))  # edited while off
    again = NOW + timedelta(days=2)
    stand.now = again + timedelta(days=1)
    after = stand.pull(stopped, since=again, transcripts=True)
    assert after.drafts == [] and after.skipped == []
    cursor = g.Cursor.decode(after.cursor)
    assert cursor.parts == {} and cursor.floor >= again - timedelta(seconds=1)


@pytest.mark.perf
def test_one_speakers_long_run_of_items_is_joined_in_linear_time():
    """Only time shows this. The old join copied the turn's text so far for every item, so its
    time grew with the square of the text: 20,000 items of 300 characters (6 MB) took 1.0 s,
    and 80,000 (24 MB) about 16 s. Joined once, eight times the items take about eight times
    as long (linear_cost)."""

    def items(n: int) -> list[dict]:
        return [item("Ada", f"{k:06d}" + "x" * 294, 0) for k in range(n)]

    assert_near_linear(lambda n: partial(g.turns, items(n), owner=None), 5_000)
    (turn,) = g.turns(items(40_000), owner=None)
    assert turn.text.startswith("000000x") and turn.text.endswith(" 039999" + "x" * 294)
    assert len(turn.text) == 40_000 * 301 - 1


def test_a_blank_item_in_a_run_adds_no_space():
    said = g.turns(
        [item("Ada", "One.", 0), item("Ada", "  ", 1), item("Ada", "Two.", 2)], owner=None
    )
    assert [t.text for t in said] == ["One. Two."]
    assert [t.text for t in g.turns([item("Ada", " ", 0), item("Ada", "x", 1)], None)] == ["x"]


def test_a_pull_reads_at_most_per_pull_notes_and_the_next_goes_on(monkeypatch):
    """A pull kept the drafts of every note it read, bounded only by its deadline. It now
    stops after PER_PULL notes as at its deadline: each note is written once, none lost."""
    monkeypatch.setattr(g, "PER_PULL", 2)
    stand = Stand()
    for n in range(1, 6):
        stand.add(n, SINCE + timedelta(hours=n))
    written, cursor = [], None
    for _ in range(3):
        got = stand.pull(cursor)
        assert len(got.drafts) <= 2
        written += origins(got)
        cursor = got.cursor
    assert written == [g.ORIGIN + nid(n) for n in range(1, 6)]
    assert stand.pull(cursor).drafts == []


def test_a_scan_of_more_notes_than_its_pages_hold_fails_as_one_of_too_many_pages(monkeypatch):
    """Granola chooses how many notes a page holds, so a scan bounded by its pages alone kept
    whatever one page brought. It now ends at MAX_LIST_PAGES full pages of notes too."""
    monkeypatch.setattr(g, "MAX_LIST_PAGES", 2)
    stand = Stand()
    for n in range(2 * g.LIST_PAGE + 1):
        stand.add(n, SINCE + timedelta(minutes=n + 1))
    listed = ("id", "object", "title", "owner", "created_at", "updated_at", "deleted_at")
    page = [{k: s[k] for k in listed} for s in stand.notes.values()]  # all on one page
    stand.answers["list"] = lambda r: httpx.Response(
        200, json={"notes": page, "hasMore": False, "cursor": None}
    )
    with pytest.raises(SourceError, match="listed more than 60 notes in one scan"):
        stand.pull()


def test_a_transcript_of_more_items_than_its_pages_hold_is_skipped_as_too_long(monkeypatch):
    """The same for a transcript, paged or inline: its summary is written, its transcript
    skipped as longer than this server reads."""
    monkeypatch.setattr(g, "MAX_TRANSCRIPT_PAGES", 2)
    stand = Stand()
    paged = stand.add(1, SINCE + timedelta(hours=1))
    inline = stand.add(2, SINCE + timedelta(hours=2))
    items = [item("Ada", f"Line {k}.", k) for k in range(2 * g.TRANSCRIPT_PAGE + 1)]
    stand.answers["transcript"] = lambda r: httpx.Response(
        200, json={"transcript": items, "hasMore": False, "cursor": None}
    )
    stand.notes[inline]["transcript"] = items
    got = stand.pull(transcripts=True)
    assert origins(got) == [g.ORIGIN + paged, g.ORIGIN + inline]
    assert [s.ref for s in got.skipped] == [paged, inline]
    assert all("longer than this server reads (200 items)" in s.reason for s in got.skipped)


def test_a_note_cairn_fails_on_is_skipped_once_and_the_rest_written(monkeypatch):
    """Anything but a SourceError while one note was made into drafts failed the pull, every
    run, so no note after it was ever written."""
    stand = Stand()
    for n in (1, 2, 3):
        stand.add(n, SINCE + timedelta(hours=n))
    made = g.summary_draft

    def failing(meeting, project):
        if meeting.id == nid(2):
            raise ValueError("a value cairn did not expect")
        return made(meeting, project)

    monkeypatch.setattr(g, "summary_draft", failing)
    first = stand.pull()
    assert origins(first) == [g.ORIGIN + nid(1), g.ORIGIN + nid(3)]
    assert [(s.ref, s.reason) for s in first.skipped] == [(nid(2), g.UNREADABLE)]
    stand.now = NOW + timedelta(hours=1)
    again = stand.pull(first.cursor)
    assert again.drafts == [] and again.skipped == []


def test_a_transcript_cairn_fails_on_is_skipped_and_its_summary_still_written(monkeypatch):
    stand = Stand()
    note_id = transcript_note(stand, 1)

    def failing(items, owner):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(g, "turns", failing)
    pulled = stand.pull(transcripts=True)
    assert origins(pulled) == [g.ORIGIN + note_id]
    assert [(s.ref, s.reason) for s in pulled.skipped] == [(note_id, g.UNREADABLE)]
