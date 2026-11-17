"""A Google Meet source that reads one meeting, by its code (`meeting_code`): the setting,
the listing's filter, the account path, two sources of one meeting, a preview and the page's
sentence. Google is an httpx.MockTransport written from docs/google-meet-api.md (the stand-in
of tests/unit/test_google_meet_unit.py, whose listing here also honours the documented
`space.meeting_code` condition); every name, address, id and code is made up."""

import asyncio
import re
from types import SimpleNamespace

import httpx
import pytest
import test_google_meet_unit as u

from cairn.sources import CHOOSING, SourceError, overlaps
from cairn.sources import google_meet as m
from cairn.sources.runner import preview_new_source, preview_source
from cairn.store import Store

CODE = "abc-mnop-xyz"


def refusal(config) -> str:
    with pytest.raises(ValueError) as refused:
        m.check_config(config)
    return str(refused.value)


# The setting


@pytest.mark.parametrize("typed", ["abc-mnop-xyz", "ABC-MNOP-XYZ", "abcmnopxyz", "AbcMnopXyz"])
def test_a_meeting_code_is_taken_with_or_without_dashes_in_either_case(typed):
    checked = m.check_config(u.settings(meeting_code=typed))
    assert checked["meeting_code"] == CODE
    assert m.check_config(checked) == checked


def test_a_code_of_other_group_lengths_keeps_its_dashes():
    assert m.check_config(u.settings(meeting_code="Aa-bbbbb-c"))["meeting_code"] == "aa-bbbbb-c"


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "abc-mnop",
        "abcmnop",  # without dashes, only the ten letters of abc-mnop-xyz can be grouped
        "abc-mnop-xyz-q",
        "abc--mnop-xyz",
        "abc-mn0p-xyz",
        "abc mnop xyz",
        "abc mnop xyz",  # spaces inside are not a code; around it they are taken off
        "https://meet.google.com/abc-mnop-xyz",
        "spaces/abc-mnop-xyz",
        "abc-" + "m" * 40 + "-xyz",
        7,
        ["abc-mnop-xyz"],
        None,
    ],
)
def test_anything_else_is_refused_in_a_sentence_that_shows_the_form(typed):
    config = {**u.settings(), "meeting_code": typed}
    said = refusal(config)
    assert said == m.CODE_REFUSED and "meeting_code" in said and "abc-mnop-xyz" in said


def test_a_credential_given_as_a_code_is_refused_without_repeating_it():
    leaked = "ya29." + "PastedAsACode99"
    said = refusal(u.settings(meeting_code=leaked))
    assert "meeting_code" in said and leaked not in said


def test_without_a_code_the_config_is_as_before():
    assert "meeting_code" not in m.check_config(u.settings())
    assert "meeting_code" in m.SETTINGS


# The filter


def test_the_filter_with_a_code_is_the_time_bound_and_the_documented_code_condition():
    moment = u.NOW - u.DAY
    assert m.record_filter(moment) == f'start_time>="{u.z(moment)}"'
    assert m.record_filter(moment, CODE) == (
        f'start_time>="{u.z(moment)}" AND space.meeting_code = "abc-mnop-xyz"'
    )
    assert m.CODE_CONDITION == 'space.meeting_code = "{}"'


class OneMeeting(u.Stand):
    """The stand-in, whose listing takes the documented filter: the time bound, then, when
    given, ` AND space.meeting_code = "<code>"`; anything else is answered 400. With
    `ignores_code`, it lists by the time bound alone, as a service that ignored the code's
    condition would. `spaces.get` takes `spaces/{meetingCode}` as well as `spaces/{space}`
    (SP-GET) and answers the space's `name` and `meetingCode`; `unreachable` is the status
    a code answers with instead (404 or 403)."""

    FORM = re.compile(r'start_time>="([^"]+)"(?: AND space\.meeting_code = "([a-z-]+)")?')

    def __init__(self) -> None:
        super().__init__()
        self.ignores_code = False
        self.unreachable: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/conferenceRecords") and "records" not in self.answers:
            self.requests.append(request)
            found = self.FORM.fullmatch(request.url.params["filter"])
            if found is None:
                return httpx.Response(400, json={"error": {"code": 400, "message": u.CANARY}})
            bound, code = m.moment(found.group(1)), found.group(2)
            code = None if self.ignores_code else code
            listed = [
                self.record(c)
                for c in self.calls.values()
                if c.start >= bound and (code is None or c.code == code)
            ]
            return httpx.Response(200, json={"conferenceRecords": listed})
        asked = path.removeprefix("/v2/spaces/")
        if path.startswith("/v2/spaces/") and m.CODE.fullmatch(asked):
            self.requests.append(request)
            held = [c for c in self.calls.values() if c.code == asked]
            if self.unreachable or not held:
                status = self.unreachable or 404
                return httpx.Response(status, json={"error": {"code": status, "message": u.CANARY}})
            return httpx.Response(
                200, json={"name": f"spaces/space-{held[0].cid}", "meetingCode": asked}
            )
        return super().handler(request)


def ignored_filter() -> OneMeeting:
    """Three meetings, the second of them the chosen one, listed whatever the filter says."""
    stand = OneMeeting()
    stand.ignores_code = True
    for n, code in ((1, "qrs-tuvw-xyz"), (2, CODE), (3, "def-ghij-klm")):
        stand.add(n, u.SINCE + n * u.HOUR, u.SINCE + (n + 1) * u.HOUR, code=code)
    return stand


def test_a_service_that_ignores_the_filter_gets_only_the_chosen_meeting_read():
    stand = ignored_filter()
    got = stand.pull(meeting_code=CODE)
    assert u.refs(got) == ["meet:call002/t1"]
    assert got.drafts[0].title.startswith("Meet abc-mnop-xyz, ")
    for kind in ("transcripts", "entries", "participants"):
        asked = {r.url.path.split("/")[3] for r in stand.of(kind)}
        assert asked == {"call002"}, kind  # nothing asked about the other two
    assert (
        got.stopped
        == m.unasked(2)
        == (
            "Google Meet returned 2 meetings this source did not ask for, of other meetings than"
            " its meeting_code: they were left unread, so Google Meet ignored the filter"
        )
    )
    assert m.unasked(1).startswith("Google Meet returned 1 meeting this source did not ask for")
    # The cursor holds none of them: a later pull leaves them unread again, and says so.
    again = stand.pull(got.cursor, meeting_code=CODE)
    assert again.drafts == [] and again.stopped == m.unasked(2)
    assert {r.url.path.split("/")[3] for r in stand.of("transcripts")} <= {"call002"}


def test_with_a_code_a_pull_makes_one_spaces_get_and_no_other_for_the_title():
    stand = ignored_filter()
    stand.ignores_code = False  # honoured: only the chosen meeting is listed
    got = stand.pull(meeting_code=CODE)
    assert u.refs(got) == ["meet:call002/t1"] and got.stopped is None
    paths = [r.url.path for r in stand.requests]
    assert paths == [
        "/token",
        "/v2/spaces/abc-mnop-xyz",
        "/v2/conferenceRecords",
        "/v2/conferenceRecords/call002/transcripts",
        "/v2/conferenceRecords/call002/transcripts/t1/entries",
        "/v2/conferenceRecords/call002/participants",
    ]  # 6 requests: the code's space once, and never the record's space for its title
    # Without a code: the requests of before, and no spaces.get by a code.
    stand.ignores_code = True
    got = stand.pull()
    assert u.refs(got) == ["meet:call001/t1", "meet:call002/t1", "meet:call003/t1"]
    assert len(stand.requests) == 14 and len(stand.of("space")) == 3  # 1 + 1 + 4 a meeting
    assert all("space-" in r.url.path for r in stand.of("space"))


@pytest.mark.parametrize("status", [404, 403])
def test_a_code_that_resolves_to_no_space_reads_nothing_and_names_the_code(status):
    stand = ignored_filter()
    stand.unreachable = status
    with pytest.raises(SourceError) as failed:
        stand.pull(meeting_code=CODE)
    said = str(failed.value)
    assert said.startswith(
        f"Google Meet has no meeting abc-mnop-xyz that this account can reach (HTTP {status}"
        " to spaces.get), so nothing was read"
    ), said
    assert u.CANARY not in said and u.TOKEN not in said and u.VAR not in said
    assert [r.url.path for r in stand.requests] == ["/token", "/v2/spaces/abc-mnop-xyz"]
    stand.unreachable = None
    with pytest.raises(SourceError, match="has no meeting zzz-zzzz-zzz"):
        stand.pull(meeting_code="zzz-zzzz-zzz")  # no space holds it


def two_meetings() -> OneMeeting:
    stand = OneMeeting()
    stand.add(1, u.SINCE + u.HOUR, u.SINCE + 2 * u.HOUR, code=CODE)
    stand.add(2, u.SINCE + u.HOUR, u.SINCE + 2 * u.HOUR, code="qrs-tuvw-xyz")
    return stand


def test_with_a_code_only_that_meetings_records_are_listed_and_read():
    stand = two_meetings()
    got = stand.pull(meeting_code="ABCMNOPXYZ")
    assert u.refs(got) == ["meet:call001/t1"]
    assert got.drafts[0].title.startswith("Meet abc-mnop-xyz, ")
    (listed,) = stand.of("records")
    assert dict(listed.url.params) == {
        "filter": f'start_time>="{u.z(u.SINCE - u.DAY)}" AND space.meeting_code = "{CODE}"',
        "pageSize": "100",
    }


def test_without_a_code_the_requests_are_those_of_before():
    stand = two_meetings()
    got = stand.pull()
    assert u.refs(got) == ["meet:call001/t1", "meet:call002/t1"]
    (listed,) = stand.of("records")
    assert dict(listed.url.params) == {
        "filter": f'start_time>="{u.z(u.SINCE - u.DAY)}"',
        "pageSize": "100",
    }


def test_a_refused_filter_reads_nothing_and_does_not_fall_back_to_every_meeting():
    stand = two_meetings()
    stand.answers["records"] = lambda request: httpx.Response(
        400, json={"error": {"code": 400, "message": u.CANARY, "status": "INVALID_ARGUMENT"}}
    )
    with pytest.raises(SourceError) as failed:
        stand.pull(meeting_code=CODE)
    said = str(failed.value)
    assert said == m.FILTER_REFUSED
    assert u.CANARY not in said and u.TOKEN not in said and u.REFRESH not in said
    assert len(stand.of("records")) == 1  # asked once, with the code: no listing without it
    paths = [r.url.path for r in stand.requests]
    assert paths == ["/token", "/v2/spaces/abc-mnop-xyz", "/v2/conferenceRecords"]
    # Without a code, a 400 to the listing is said as before.
    stand.requests.clear()
    with pytest.raises(SourceError) as failed:
        stand.pull()
    assert str(failed.value) == (
        "Google Meet refused the request to conferenceRecords.list (HTTP 400)"
    )


def test_the_page_says_which_meeting_a_source_reads():
    said = m.describe(m.check_config(u.settings(meeting_code=CODE, subject="ada@example.test")))
    assert "the meeting abc-mnop-xyz" in said
    before = m.describe(m.check_config(u.settings(subject="ada@example.test")))
    assert before.startswith("Google Meet transcripts of the meetings Google lists for ")


# Made from an account: one meeting must be named


@pytest.fixture
def world(tmp_path):
    """A workspace whose owner Ada has a Google Meet account (a refresh token's variable,
    read at the stand-in's address) and a private project "calls"."""
    store = Store(tmp_path / "cairn.db", clock=lambda: u.NOW)
    signed = store.sign_in(
        "sso", "sub-ada", "ada@example.test", "Ada", issuer="https://id.example.test/meet",
        join="default",
    )  # fmt: skip
    ada = store.for_account(signed.account_id, signed.user.id)
    ada.create_project("calls", everyone="none")
    account = {"credentials_env": u.VAR, "base_url": u.BASE}
    store.add_credential("ada-meet", "google_meet", account, owner="ada@example.test")
    yield SimpleNamespace(store=store, ada=ada)
    store.close()


def test_a_meet_source_from_an_account_names_one_meeting(world):
    choosing = CHOOSING["google_meet"]
    assert choosing.refused is None and choosing.selector == ("meeting_code",)
    assert choosing.needs == ("meeting_code",)
    for select in ({}, {"meeting_code": None}):
        with pytest.raises(ValueError) as refused:
            world.ada.add_source_from("standup", "ada-meet", select, {}, "calls")
        assert str(refused.value) == (
            "name one meeting: the code from its address, such as abc-mnop-xyz"
        )
    with pytest.raises(ValueError) as refused:
        world.ada.add_source_from("standup", "ada-meet", {"meeting_code": "abc"}, {}, "calls")
    assert str(refused.value) == m.CODE_REFUSED
    with pytest.raises(ValueError, match="wait_hours is not a selector"):
        world.ada.add_source_from("standup", "ada-meet", {"wait_hours": 6}, {}, "calls")
    assert world.store.list_sources() == []
    info = world.ada.add_source_from(
        "standup", "ada-meet", {"meeting_code": "ABCMNOPXYZ"}, {"wait_hours": 6}, "calls"
    )
    assert (info.config["meeting_code"], info.config["wait_hours"]) == (CODE, 6)
    assert info.enabled is False


def test_the_same_meeting_twice_from_one_account_is_refused(world):
    world.ada.add_source_from("standup", "ada-meet", {"meeting_code": CODE}, {}, "calls")
    with pytest.raises(ValueError, match="already reads"):
        world.ada.add_source_from(
            "again", "ada-meet", {"meeting_code": "ABC-MNOP-XYZ"}, {}, "calls"
        )
    world.ada.add_source_from("other", "ada-meet", {"meeting_code": "qrs-tuvw-xyz"}, {}, "calls")
    one = m.check_config(u.settings(meeting_code=CODE))
    same = m.check_config(u.settings(meeting_code="abcmnopxyz"))
    other = m.check_config(u.settings(meeting_code="qrs-tuvw-xyz"))
    assert overlaps("google_meet", one, same) and not overlaps("google_meet", one, other)
    # Re-routed (switched on into projects), each is checked against the other again.
    world.ada.enable_source("other", ["calls"])
    assert world.store.get_source("other").enabled is True


def test_the_page_says_the_meeting_and_its_field_in_plain_words(world):
    from cairn.web import ui

    world.ada.add_source_from("standup", "ada-meet", {"meeting_code": CODE}, {}, "calls")
    world.store.add_source("op-meet", "google_meet", {"credentials_env": u.VAR, "projects": []})
    rows = ui._source_rows(world.ada, world.ada.list_sources(), "cairn")
    assert rows["standup"]["first"] == (
        "From your Google Meet: transcripts of the meeting abc-mnop-xyz → into calls"
    )
    assert rows["op-meet"]["first"] == (
        "From the workspace's Google Meet: meeting transcripts → into no project yet"
    )
    ((setting, label, _),) = ui.CHOOSE_FIELDS["google_meet"]
    assert (setting, label) == (
        "meeting_code",
        "The meeting's code, from its address: abc-mnop-xyz",
    )
    # Its one reading option, under "More choices", in words and with the kind's range.
    assert ui.OPTION_FIELDS["wait_hours"] == (
        "number",
        "Hours after a meeting ends that its transcript is looked for",
    )
    assert ui.RANGES[("google_meet", "wait_hours")] == m.WAIT_HOURS


def test_the_guide_says_a_meet_source_from_an_account_names_one_meeting():
    from pathlib import Path

    guide = (Path(__file__).resolve().parents[2] / "src/cairn/guides/sources.md").read_text()
    flat = " ".join(guide.split())
    assert "no Google Meet source can be made" not in flat
    assert (
        "One that would read everything is refused in its kind's words (a calendar must be"
        " named: `primary` is your main one; a Google Meet source names one meeting by the"
        " code in its address, `abc-mnop-xyz`)."
    ) in flat
    assert CHOOSING["google_calendar"].needs and CHOOSING["google_meet"].needs


def test_a_preview_of_one_meeting_before_and_after_saving_shows_its_code_and_start(world):
    stand = two_meetings()
    env = {u.VAR: u.CREDENTIAL}
    select = {"meeting_code": CODE}

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(stand.handler))

    before = asyncio.run(
        preview_new_source(
            world.ada, "standup", "ada-meet", select, {}, "calls", http=client(), env=env,
            days=2, clock=stand.clock,
        )
    )  # fmt: skip
    start = f"{u.SINCE + u.HOUR:%Y-%m-%d %H:%M}"
    assert (before.read, before.count) == (True, 1), before
    assert before.titles == [f"Meet abc-mnop-xyz, {start} UTC"]
    assert world.store.list_sources() == []
    (listed,) = stand.of("records")
    assert listed.url.params["filter"].endswith(' AND space.meeting_code = "abc-mnop-xyz"')
    world.ada.add_source_from("standup", "ada-meet", select, {}, "calls")
    stand.requests.clear()
    stored = asyncio.run(
        preview_source(world.ada, "standup", http=client(), env=env, days=2, clock=stand.clock)
    )
    assert (stored.read, stored.count, stored.titles) == (True, 1, before.titles)


def test_a_meeting_left_waiting_before_the_code_was_chosen_is_not_written_under_it():
    """A cursor can hold a meeting that was waiting for its transcript when the source had
    no code, or another one. Under a code it belongs to another meeting: it is dropped from
    the cursor, never written."""
    stand = ignored_filter()
    stand.ignores_code = False
    first = stand.pull(meeting_code=CODE)
    held = m.Cursor.decode(first.cursor)
    other = stand.calls["call001"]  # of the meeting qrs-tuvw-xyz
    carried = m.Cursor(
        mark=held.mark,
        waiting={**held.waiting, other.cid: m.Waiting(other.end, False)},
        settled=dict(held.settled),
        parts=dict(held.parts),
    ).encode()
    got = stand.pull(carried, meeting_code=CODE)
    assert all(not ref.startswith("meet:call001") for ref in u.refs(got))
    assert other.cid not in m.Cursor.decode(got.cursor).waiting
    # One question about it (whose meeting it is), and nothing of it read.
    for kind in ("transcripts", "entries", "participants"):
        assert not [r for r in stand.of(kind) if "/call001/" in r.url.path], kind


def test_a_code_typed_with_spaces_around_it_is_the_same_code():
    assert m._check_code("  ABC-mnop-xyz \n") == CODE
    assert m._check_code(" abcmnopxyz ") == CODE
