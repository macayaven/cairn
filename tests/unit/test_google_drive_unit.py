"""Unit tests for the Google Drive source: what tests/acceptance/test_c7_google_drive.py does
not pin. HTTP goes through httpx.MockTransport; time, sleep and the backoff's jitter are
injected. The credential is a made-up refresh token, so no key is generated here; every
name, address and id is made up."""

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from functools import partial
from urllib.parse import parse_qsl

import httpx
import pytest
from linear_cost import assert_near_linear

from cairn.sources import SourceError, holds_secret
from cairn.sources import google_drive as d

VAR = "GOOGLE_UNIT_DRIVE"
CLIENT_ID = "unit-" + "drive-client" + ".apps.example.test"
SECRET = "GOCSPX-" + "unitDrive" + "MadeUpSecret"
REFRESH = "1//" + "0unitDrive" + "MadeUpRefresh"
TOKEN = "ya29." + "unitDrive" + "MadeUpAccessToken"
CREDENTIAL = json.dumps(
    {
        "type": "authorized_user",
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "refresh_token": REFRESH,
    }
)
BASE = "https://drive.example.test/drive/v3"
FOLDER = "1UnitFolder" + "AbCdEfGh"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=3)
MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)
CANARY = "unit-" + "drive-body-" + "canary"


def settings(**given) -> dict:
    return {
        key: value
        for key, value in {
            "credentials_env": VAR,
            "folder_id": FOLDER,
            "projects": ["specs"],
            **given,
        }.items()
        if value is not None
    }


def refusal(config) -> str:
    with pytest.raises(ValueError) as refused:
        d.check_config(config)
    return str(refused.value)


# The kind and its config


def test_the_kind_starts_off_proposes_its_project_and_says_what_switching_on_reads():
    kind = d.KIND
    assert kind.check is d.check_config and kind.open is d.open_source
    assert kind.starts_off and kind.via == "google-drive"
    assert kind.default_project == "documents"
    assert (
        kind.switching_on()
        == kind.switch_on
        == (
            "Switching it on reads the documents put into the folder or changed there from then"
            " on, each once it has rested: nothing from before, and nothing from while it was off."
            " With existing set, it reads the documents already there too."
        )
    )
    assert d.SCOPES == ["https://www.googleapis.com/auth/drive.readonly"]


def test_the_defaults_are_filled_in_and_what_is_stored_checks_the_same():
    checked = d.check_config({"credentials_env": VAR, "folder_id": FOLDER})
    assert checked == {
        "credentials_env": VAR,
        "folder_id": FOLDER,
        "settle_hours": 24,
        "existing": False,
        "projects": [],
        "base_url": "https://www.googleapis.com/drive/v3",
    }
    assert d.check_config(checked) == checked
    assert "subject" not in d.check_config(settings(subject=None))
    kept = d.check_config(settings(subject="ada@example.test", base_url=BASE + "/"))
    assert (kept["subject"], kept["base_url"]) == ("ada@example.test", BASE)
    assert d.check_config(settings(base_url="http://[::1]:8781/drive/v3"))["base_url"] == (
        "http://[::1]:8781/drive/v3"
    )


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"credentials_env": None}, "a google_drive source needs credentials_env"),
        ({"credentials_env": "google_creds"}, "credentials_env names an environment variable"),
        ({"credentials_env": "A" * 129}, "credentials_env names an environment variable"),
        ({"subject": "ada"}, "subject is one email address"),
        ({"subject": "ada@example.test brook@example.test"}, "subject is one email address"),
        ({"subject": "a" * 250 + "@example.test"}, "subject is one email address"),
        ({"folder_id": None}, "a google_drive source needs folder_id"),
        ({"folder_id": "short"}, "folder_id is the folder's id: 10 to 100 letters"),
        ({"folder_id": "a" * 101}, "folder_id is the folder's id: 10 to 100 letters"),
        ({"folder_id": 1234567890123}, "folder_id is the folder's id: 10 to 100 letters"),
        (
            {"folder_id": "https://drive.google.com/drive/folders/" + FOLDER},
            "folder_id is the folder's id, not its address: the last part of the address,"
            " after /folders/",
        ),
        ({"settle_hours": 0}, "settle_hours is a whole number from 1 to 720"),
        ({"settle_hours": "24"}, "settle_hours is a whole number from 1 to 720"),
        ({"settle_hours": True}, "settle_hours is a whole number from 1 to 720"),
        ({"existing": 1}, "existing is true or false"),
        ({"existing": None}, "existing is true or false"),
        ({"projects": ["specs", "board"]}, "projects names at most one project"),
        ({"projects": [""]}, "projects holds a project's name"),
        ({"projects": "specs"}, "projects is a list of at most one project's name"),
        ({"base_url": "https://drive.example.test/v3?x=1"}, "base_url has no query"),
        ({"base_url": "http://drive.example.test/v3"}, "base_url is https"),
        ({"base_url": "https://user@drive.example.test/v3"}, "base_url must not hold a user"),
        ({"base_url": 7}, "base_url is the address"),
    ],
)
def test_each_refusal_names_its_setting_and_says_what_it_takes(given, said):
    config = {"credentials_env": VAR, "folder_id": FOLDER, "projects": ["specs"], **given}
    assert said in refusal(config)


def test_an_unknown_setting_is_named_unless_its_name_could_be_a_credential():
    said = refusal(settings(colour="blue"))
    assert "'colour'" in said and "folder_id" in said
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
        "ya29." + "unitPastedToken",
    ],
)
def test_a_credential_in_any_setting_is_refused_without_repeating_it(value):
    for setting in ("credentials_env", "subject", "folder_id"):
        said = refusal(settings(**{setting: value}))
        assert setting in said and value not in said
    said = refusal(settings(projects=[value]))
    assert "projects" in said and value not in said


def test_the_page_says_which_folder_a_source_reads_as_whom_and_never_the_variable():
    said = d.describe(d.check_config(settings(subject="ada@example.test")))
    assert said == (
        f"Google Docs, Markdown and text files directly in the Google Drive folder {FOLDER},"
        " read as ada@example.test, at https://www.googleapis.com/drive/v3; each is read once"
        " it has not changed for 24 hours"
    )
    said = d.describe(d.check_config(settings(base_url=BASE, settle_hours=1, existing=True)))
    assert said == (
        f"Google Docs, Markdown and text files directly in the Google Drive folder {FOLDER},"
        f" read as the account its credential belongs to, at {BASE}; each is read once it has"
        " not changed for 1 hour, those already there when it was switched on included"
    )
    assert VAR not in said


# Opening


def test_opening_reads_the_credential_and_its_secrets_and_shows_none():
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    source = d.open_source("drive", d.check_config(settings()), http=http, env={VAR: CREDENTIAL})
    assert set(source.secrets) == {SECRET, REFRESH}
    assert SECRET not in repr(source) and REFRESH not in repr(source)
    with pytest.raises(SourceError) as missing:
        d.open_source("drive", d.check_config(settings()), http=http, env={})
    assert VAR in str(missing.value)


def test_a_refresh_token_with_a_subject_is_refused_naming_subject_before_any_request():
    asked = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: asked.append(r)))
    with pytest.raises(SourceError) as refused:
        d.open_source(
            "drive",
            d.check_config(settings(subject="ada@example.test")),
            http=http,
            env={VAR: CREDENTIAL},
        )
    assert str(refused.value) == (
        "source 'drive' reads with a refresh token, which reads as the person who authorised"
        " it: remove subject from its config"
    )
    assert asked == []


# What a draft says

LABEL = "google-drive"


def document(**given) -> "d.Document":
    fields = {
        "id": "1UnitDoc" + "XyZwVuTsRq",
        "name": "Ledger design",
        "mime": d.DOC,
        "modified": datetime(2026, 11, 12, 9, 30, 41, tzinfo=UTC),
        "size": None,
        "link": "https://docs.google.com/document/d/1UnitDoc/edit?usp=drivesdk#h",
        "editor": "Ada Park",
        "can_download": True,
    }
    return d.Document(**{**fields, **given})


@pytest.mark.parametrize(
    ("name", "mime", "title"),
    [
        ("Runbook.md", "text/markdown", "Runbook"),
        ("Runbook.MD", "text/markdown", "Runbook"),
        ("Ideas.txt", "text/plain", "Ideas"),
        ("notes.md", "text/plain", "notes"),  # the extension goes whatever the type says
        ("Plan.md", "application/vnd.google-apps.document", "Plan.md"),  # a Doc's name as it is
        (".md", "text/markdown", ".md"),
        ("  Two\n lines\t here ", "application/vnd.google-apps.document", "Two lines here"),
        ("", "application/vnd.google-apps.document", "Untitled document"),
        (" \n", "text/markdown", "Untitled document"),
    ],
)
def test_the_title_is_the_name_on_one_line_without_a_text_files_extension(name, mime, title):
    assert d.title_of(name, mime) == title


def test_a_title_is_cut_at_a_word_to_what_its_heading_may_take_of_a_passages_headings():
    from cairn.store import FIELD_MAX, HEADINGS_MAX
    from cairn.store.usage import path_bytes

    assert FIELD_MAX["title"] == d.TITLE_MAX and d.TITLE_BYTES < HEADINGS_MAX
    name = "word " * 60  # 300 characters
    title = d.title_of(name, d.DOC)
    assert len(title) <= 160 and title.endswith("word") and name.startswith(title)
    assert len(d.title_of("x" * 300, d.DOC)) == 160
    wide = d.title_of("会" * 100, d.DOC)  # three bytes a character
    assert wide == "会" * 53 and path_bytes([wide]) == 159
    assert path_bytes([d.title_of('"\\' * 100, d.DOC)]) <= 160  # JSON doubles both


def test_the_full_slug_is_the_compilers_before_its_cut():
    from cairn.compiler import SLUG_MAX, slugify

    assert d.SLUG_MAX == SLUG_MAX  # mirrored: the compiler cannot be imported from here

    name = "Quarterly planning notes for the payments platform and the ledger review"
    assert len(d.full_slug(name)) > 64 and d.full_slug(name).startswith(slugify(name))
    assert d.full_slug("Café, déjà vu!") == "cafe-deja-vu"
    assert d.full_slug("会议记录") == ""


def test_a_title_that_fits_is_the_topic_and_later_parts_add_their_number():
    title = "Settlement design notes for the ledger migration and v22"  # a slug of 56
    assert d.topic(title, "1UnitDoc", 1) == title
    assert d.topic(title, "1UnitDoc", 20) == f"{title}, part 20"


@pytest.mark.parametrize(
    "title",
    [
        "Settlement design notes for the ledger migration and the v2 rollout plan",
        "Quarterly planning notes for the payments platform and the ledger, review " + "z" * 9,
        "Averyveryveryveryveryveryveryveryveryveryveryveryveryverylongsinglewordtitle",
        "会议记录",  # nothing of it reaches a slug: alone it would be "untitled"
        "!" * 195,  # a slug of nothing, and no room for ", part 20" in 200 characters
    ],
)
def test_a_title_that_does_not_fit_is_cut_and_tagged_with_a_digest_of_the_id(title):
    from cairn.compiler import slugify

    tag = d.digest("1UnitDoc")[:6]
    for number in (1, 2, 20):
        made = d.topic(title, "1UnitDoc", number)
        suffix = f", {tag}" + (f", part {number}" if number > 1 else "")
        assert made.endswith(suffix), made
        cut = made.removesuffix(suffix)
        assert cut and title.startswith(cut), made
        assert len(d.full_slug(made)) <= 64 and slugify(made) == d.full_slug(made)
        assert len(made) <= 200
    assert d.topic(title, "1UnitDoc", 1) != d.topic(title, "1UnitDoc" + "x", 1)


def test_a_cut_title_ends_at_a_word_and_never_on_a_comma():
    title = "Settlement design notes, for the ledger migration, and the v2 rollout plan, again"
    cut = d.topic(title, "1UnitDoc", 1).rsplit(", ", 1)[0]
    assert title.startswith(cut + " ") or title.startswith(cut + ", ")
    assert not cut.endswith(",")


def test_the_text_is_decoded_as_utf8_with_replacement_and_a_bom_dropped():
    assert d.clean(b"\xef\xbb\xbfCaf\xc3\xa9\r\n") == "Café\r\n"
    assert d.clean(b"caf\xe9 \xff") == "caf� �"
    assert d.clean("﻿﻿Two".encode()) == "﻿Two"  # one mark: the leading one


def test_data_images_are_dropped_and_their_alt_text_kept_and_other_images_stay():
    blob = "iVBORw0KGgo" + "A" * 5000
    content = "\n".join(
        [
            "Intro ![Chart one](data:image/png;base64,BLOB) then"
            " ![](<data:image/png;base64,BLOB>).",
            "A ref image: ![Logo][image1] and ![][IMAGE2], a link [image1] stays text.",
            "A web image: ![Map](https://example.test/map.png) stays.",
            "",
            "[image1]: <data:image/png;base64,BLOB>",
            '   [image2]: data:image/jpeg;base64,BLOB "A title"',
            "[site]: https://example.test/",
            "    [indented]: <data:image/png;base64,BLOB>",
            "",
        ]
    ).replace("BLOB", blob)
    cleaned = d.clean(content.encode())
    lines = cleaned.splitlines()
    assert lines[0] == "Intro Chart one then ."
    assert lines[1] == "A ref image: Logo and , a link [image1] stays text."
    assert lines[2] == "A web image: ![Map](https://example.test/map.png) stays."
    assert "[site]: https://example.test/" in lines
    assert not any(line.lstrip().startswith(("[image1]", "[image2]")) for line in lines)
    assert [line for line in lines if blob in line] == [
        f"    [indented]: <data:image/png;base64,{blob}>"  # four spaces: not a definition
    ]


@pytest.mark.perf
@pytest.mark.parametrize(
    "line",
    [
        lambda n: "![" * n,  # each "![" used to scan the rest of the line for its "]"
        lambda n: "![a" * n,
        lambda n: "![a](data:" * (n // 2),  # each "(data:" used to scan the rest for ")"
        lambda n: "[x]: data:a\n" + "![x" * n,  # images by a dropped reference are looked for
        lambda n: "[x]: data:a\n" + "![x][" * (n // 2),
    ],
    ids=[f"line{i}" for i in range(5)],  # the ids the strings these replaced were given
)
def test_cleaning_a_line_built_to_make_the_image_patterns_backtrack_takes_linear_time(line):
    """Only time shows this. The old patterns took time that grew with the square of a line's
    length: 9 s for 40,000 "![" and 30 s for 40,000 images by reference, so lines five times
    longer took minutes. Linear, eight times the line takes about eight times as long
    (linear_cost)."""
    assert_near_linear(lambda n: partial(d.clean, line(n).encode()), 2_500)


def test_data_images_next_to_brackets_and_parentheses_are_still_dropped():
    blob = "data:image/png;base64,AAAA"
    content = (
        f"See ![(x)]({blob}) and ![ok]({blob}) (in brackets) [too], ![one]({blob})![two]({blob})."
    )
    assert d.clean(content.encode()) == "See (x) and ok (in brackets) [too], onetwo."


def test_an_alt_text_holding_a_bracket_is_not_read_as_an_image():
    """The price of a linear pattern: no "[" inside the alt text (Google's export writes
    none), so such an image is left as it came."""
    content = "![a [b] c](data:image/png;base64,AAAA)"
    assert d.clean(content.encode()) == content


def test_the_facts_say_when_who_and_where_and_never_an_address():
    assert d.facts(document()) == [
        "- Last modified: 2026-11-12 09:30 UTC",
        "- Last modified by: Ada Park",
        "- Open it in Google Drive: https://docs.google.com/document/d/1UnitDoc/edit",
    ]
    bare = document(editor=None, link=None, modified=datetime(2026, 1, 2, 3, 4, tzinfo=UTC))
    assert d.facts(bare) == ["- Last modified: 2026-01-02 03:04 UTC"]
    odd = document(editor="dee@example.test", link="javascript:alert(1)")
    assert d.facts(odd) == ["- Last modified: 2026-11-12 09:30 UTC"]
    for wide in ("dee\uff20example.test", "dee\ufe6bexample.test"):  # at signs of other widths
        assert d.facts(document(editor=wide, link=None)) == [
            "- Last modified: 2026-11-12 09:30 UTC"
        ]
    spaced = document(editor=" Brook\n  Lane ", link="https://u:p@docs.example.test/x?k=1")
    assert d.facts(spaced) == [
        "- Last modified: 2026-11-12 09:30 UTC",
        "- Last modified by: Brook Lane",
    ]


def paragraphs(count: int, start: int = 0) -> list[str]:
    return [f"Paragraph {k:05d} of the ledger." for k in range(start, start + count)]


def held(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"^Paragraph (\d{5}) of the ledger\.$", text, re.M)]


def test_a_short_document_is_one_draft_with_the_heading_the_facts_and_the_text():
    content = "\n# Ledger v2\n\nThe ledger moves on Friday.\n\n- Owner: payments\n\n\n"
    (draft,) = d.drafts(document(), content, "specs")
    assert draft.text == (
        "# Ledger design\n\n"
        "- Last modified: 2026-11-12 09:30 UTC\n"
        "- Last modified by: Ada Park\n"
        "- Open it in Google Drive: https://docs.google.com/document/d/1UnitDoc/edit\n\n"
        "# Ledger v2\n\nThe ledger moves on Friday.\n\n- Owner: payments\n"
    )
    assert (draft.project, draft.title, draft.topic) == ("specs", "Ledger design", "Ledger design")
    assert (draft.source_ref, draft.label) == ("drive:1UnitDocXyZwVuTsRq", LABEL)
    assert draft.resume is None


def test_every_draft_s_source_ref_begins_with_the_kind_s_word():
    """M3: `KIND.ref_form` is the word before the first ":" of every source_ref this kind
    writes, so a note a person or an agent writes the same way is withheld as these are."""
    from cairn.sources import source_ref_word

    made = d.drafts(document(), "\n\n".join(paragraphs(3_100)) + "\n", "specs")
    assert made is not None and len(made) == 3
    assert {source_ref_word(m.source_ref) for m in made} == {d.KIND.ref_form} == {"drive"}


def test_a_long_document_is_cut_between_passages_and_later_parts_hold_only_the_text():
    from cairn.store.usage import passages_of

    content = "\n\n".join(paragraphs(3_100)) + "\n"
    made = d.drafts(document(), content, "specs")
    assert [m.source_ref for m in made] == [
        "drive:1UnitDocXyZwVuTsRq",
        "drive:1UnitDocXyZwVuTsRq/2",
        "drive:1UnitDocXyZwVuTsRq/3",
    ]
    assert [m.topic for m in made] == [
        "Ledger design",
        "Ledger design, part 2",
        "Ledger design, part 3",
    ]
    assert [m.title for m in made] == ["Ledger design"] * 3
    numbers = [held(m.text) for m in made]
    assert [k for part in numbers for k in part] == list(range(3_100))
    assert [len(n) for n in numbers] == [1_497, 1_500, 103]  # the first holds three facts too
    assert [passages_of(m.text) for m in made] == [1_500, 1_500, 103]
    assert made[1].text.startswith("# Ledger design, part 2\n\nParagraph 01497 ")
    assert "Last modified" not in made[1].text


def test_a_part_ends_before_the_first_of_a_run_of_headings_in_its_last_third():
    lines = []
    for k, said in enumerate(paragraphs(1_600)):
        if k == 900:
            lines += ["## Too early", ""]  # before the last third: no cut here
        if k == 1_200:
            lines += ["## Settlement", "", "### Windows", ""]  # a run of two headings
        if k == 1_400:
            lines += ["## Later", ""]
        lines += [said, ""]
    made = d.drafts(document(), "\n".join(lines), "specs")
    assert len(made) == 2
    assert held(made[0].text) == list(range(1_400))
    assert made[1].text.startswith("# Ledger design, part 2\n\n## Later\n\nParagraph 01400 ")
    run = paragraphs(1_600)
    run[1_300:1_300] = ["## Settlement", "### Windows"]
    made = d.drafts(document(), "\n\n".join(run), "specs")
    assert made[1].text.startswith("# Ledger design, part 2\n\n## Settlement\n\n### Windows\n\n")


def test_with_no_heading_in_its_last_third_a_part_is_cut_where_it_is_full():
    lines = ["## Overview", ""] + [p + "\n" for p in paragraphs(1_700)]
    made = d.drafts(document(), "\n".join(lines), "specs")
    assert [len(held(m.text)) for m in made] == [1_497, 203]


def test_every_part_holds_at_most_1500_passages_as_cairn_counts_them_whatever_the_text():
    from cairn.store.usage import passages_of

    odd = "\n".join(["---", "title: front matter", "---", ""])  # front matter only at the top
    odd += "  indented under the facts\n\n" + "\r\n\r\n".join(paragraphs(1_498))
    odd += "\u2028" + "\x0c".join(paragraphs(800, start=5_000)) + "\n```\ncode\n\nmore\n```\n"
    made = d.drafts(document(), odd, "specs")
    assert len(made) >= 2
    assert all(passages_of(m.text) <= 1_500 for m in made), [passages_of(m.text) for m in made]
    assert "\r\n\r\n" in made[0].text and "```\ncode\n\nmore\n```" in made[-1].text


def test_a_document_that_would_take_more_than_20_parts_has_no_drafts():
    assert d.drafts(document(), "\n\n".join(paragraphs(20 * 1_500)), "specs") is None
    assert len(d.drafts(document(), "\n\n".join(paragraphs(20 * 1_500 - 3)), "specs")) == 20


# The cursor, and what counts as new


def doc(n: int, modified: datetime, **given) -> "d.Document":
    return document(id=f"1UnitDoc{n:05d}" + "XyZw", modified=modified, **given)


def key(n: int) -> str:
    return d.digest(doc(n, NOW).id)


def test_a_cursor_is_compact_json_and_reads_back_as_written():
    on = datetime(2026, 11, 10, 8, 0, 0, 123456, tzinfo=UTC)
    cursor = d.Cursor(on=on, there={key(2), key(1)}, done={key(3)}, parts={key(4): 2})
    text = cursor.encode()
    assert text == json.dumps(
        {
            "v": 1,
            "on": 1_794_297_600_123_456,  # Unix microseconds
            "there": sorted([key(1), key(2)]),
            "done": [key(3)],
            "parts": {key(4): 2},
        },
        separators=(",", ":"),
    )
    assert d.Cursor.decode(text) == cursor
    assert d.Cursor.decode(text).on == on


@pytest.mark.parametrize(
    "stored",
    [
        "not json",
        "[]",
        json.dumps({"v": 2, "on": 0, "there": [], "done": [], "parts": {}}),
        json.dumps({"v": 1, "on": -1, "there": [], "done": [], "parts": {}}),
        json.dumps({"v": 1, "on": True, "there": [], "done": [], "parts": {}}),
        json.dumps({"v": 1, "on": 0, "there": ["1UnitDoc00001XyZw"], "done": [], "parts": {}}),
        json.dumps({"v": 1, "on": 0, "there": [], "done": {}, "parts": {}}),
        json.dumps({"v": 1, "on": 0, "there": [], "done": [], "parts": {"0" * 16: 0}}),
        json.dumps({"v": 1, "on": 0, "there": [], "done": [], "parts": {"0" * 16: "2"}}),
        json.dumps({"v": 1, "on": 0, "there": [], "done": []}),
    ],
)
def test_a_cursor_this_source_did_not_write_fails_loudly(stored):
    with pytest.raises(SourceError) as failed:
        d.Cursor.decode(stored)
    assert str(failed.value) == (
        "the stored cursor is not one this Google Drive source wrote: remove the source and"
        " add it again to start afresh"
    )


def test_the_first_pull_takes_stock_of_what_was_there_before_the_switch_on():
    listed = [doc(1, SINCE - DAY), doc(2, SINCE), doc(3, SINCE + HOUR), doc(4, SINCE - MINUTE)]
    taken = d.Cursor.taken(None, SINCE, listed, existing=False)
    assert taken == d.Cursor(on=SINCE, there={key(1), key(4)}, done=set(), parts={})
    assert d.Cursor.taken(None, SINCE, listed, existing=True).there == set()


def test_the_stock_is_taken_again_after_a_switch_on_and_kept_until_then():
    before = d.Cursor(on=SINCE, there={key(1)}, done={key(2)}, parts={key(3): 1, key(6): 2})
    listed = [doc(n, NOW - DAY) for n in (1, 2, 3, 4)] + [doc(n, NOW + HOUR) for n in (5, 6)]
    assert d.Cursor.taken(before, SINCE, listed, existing=False) == before
    again = d.Cursor.taken(before, NOW, listed, existing=False)
    # 3, half written and last modified before the switch-on, gives up its parts and is there
    assert again == d.Cursor(
        on=NOW, there={key(1), key(3), key(4)}, done={key(2)}, parts={key(6): 2}
    )
    with_existing = d.Cursor.taken(before, NOW, listed, existing=True)
    assert with_existing.there == set() and with_existing.parts == {key(6): 2}


def test_what_the_cursor_knows_is_kept_only_for_documents_the_folder_still_lists():
    known = d.Cursor(on=SINCE, there={key(1), key(9)}, done={key(2), key(8)}, parts={key(7): 1})
    assert known.kept({key(1), key(2), key(3)}) == d.Cursor(
        on=SINCE, there={key(1)}, done={key(2)}, parts={}
    )


def test_a_document_is_due_once_rested_when_new_to_the_stock_or_changed_since():
    known = d.Cursor(on=SINCE, there={key(1), key(2)}, done={key(3)}, parts={key(4): 1})
    listed = [
        doc(1, SINCE - DAY),  # there, unchanged: never
        doc(2, NOW - 30 * HOUR),  # there, changed since and rested
        doc(3, NOW - 30 * HOUR),  # done
        doc(4, NOW - 50 * HOUR),  # half written
        doc(5, SINCE - 300 * DAY),  # put in after the stock-taking, however old
        doc(6, NOW - 2 * HOUR),  # new, still settling
        doc(7, NOW - 24 * HOUR),  # new, rested just now
        doc(8, NOW - 30 * HOUR),  # the same time as 2: by id
    ]
    due = d.due(known, listed, since=SINCE, now=NOW, settle=timedelta(hours=24))
    assert [document.id for document in due] == [doc(n, NOW).id for n in (5, 4, 2, 8, 7)]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-12T09:30:41Z", datetime(2026, 11, 12, 9, 30, 41, tzinfo=UTC)),
        ("2026-11-12T09:30:41.5Z", datetime(2026, 11, 12, 9, 30, 41, 500000, tzinfo=UTC)),
        ("2026-11-12T09:30:41.123456789Z", datetime(2026, 11, 12, 9, 30, 41, 123456, tzinfo=UTC)),
        ("2026-11-12T10:30:41.000+01:00", datetime(2026, 11, 12, 9, 30, 41, tzinfo=UTC)),
        ("2026-11-12T09:30:41", None),
        ("today", None),
        (20261112, None),
    ],
)
def test_a_time_has_0_to_9_fractional_digits_and_a_zone(value, expected):
    assert d.moment(value) == expected


# The pull, against a stand-in of the Drive API


def error(status: int, reason: str | None = None, shape: str = "errors") -> httpx.Response:
    if shape == "details":
        details = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}]
        body = {"code": status, "message": CANARY, "status": "X", "details": details}
    else:
        item = {"domain": "global", "reason": reason, "message": CANARY}
        body = {"errors": [item] if reason else [], "code": status, "message": CANARY}
    return httpx.Response(status, json={"error": body})


class Stand:
    """The token endpoint and the Drive API, enough to test what the acceptance file does
    not; `answers` overrides an endpoint ("folder", "list", "export", "media") with a function
    of the request, or a list of them taken in turn."""

    def __init__(self) -> None:
        self.now = NOW
        self.files: dict[str, dict] = {}
        self.texts: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.answers: dict[str, object] = {}
        self.slept: list[float] = []
        self.folder = {"id": FOLDER, "mimeType": d.FOLDER_TYPE, "trashed": False}
        self.folder["capabilities"] = {"canListChildren": True}
        self.page = 1000

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def add(self, n: int, modified: datetime, text: str = "Some text.\n", **given) -> str:
        file_id = doc(n, modified).id
        self.files[file_id] = {
            "id": file_id,
            "name": f"Note {n}",
            "mimeType": d.DOC,
            "modifiedTime": modified.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "webViewLink": f"https://docs.example.test/d/{file_id}/edit?resourcekey=k",
            "capabilities": {"canDownload": True},
            **given,
        }
        self.texts[file_id] = text.encode()
        return file_id

    def endpoint(self, request: httpx.Request) -> str:
        path = request.url.path.removeprefix("/drive/v3")
        if path == "/files":
            return "list"
        if path.endswith("/export"):
            return "export"
        if request.url.params.get("alt") == "media":
            return "media"
        return "folder" if path == f"/files/{FOLDER}" else "other"

    def of(self, kind: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/token" and self.endpoint(r) == kind]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/token":
            assert dict(parse_qsl(request.content.decode()))["refresh_token"] == REFRESH
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3599})
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        kind = self.endpoint(request)
        answer = self.answers.get(kind)
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if answer is not None:
            return answer(request)
        if kind == "folder":
            return httpx.Response(200, json=self.folder)
        if kind == "list":
            ordered = sorted(self.files.values(), key=lambda f: f["id"])
            start = int(request.url.params.get("pageToken", "0"))
            body: dict = {"files": ordered[start : start + self.page]}
            if start + self.page < len(ordered):
                body["nextPageToken"] = str(start + self.page)
            return httpx.Response(200, json=body)
        file_id = request.url.path.split("/")[4]
        return httpx.Response(200, content=self.texts[file_id])

    def source(self, *, since=SINCE, deadline=None, http=None, **given):
        http = http or httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return d.open_source(
            "drive",
            d.check_config(settings(base_url=BASE, **given)),
            http=http,
            env={VAR: CREDENTIAL},
            clock=self.clock,
            sleep=self.sleep,
            deadline=deadline,
            since=since,
            jitter=lambda: 0.5,
        )

    def pull(self, cursor=None, **given):
        self.requests.clear()
        return asyncio.run(self.source(**given).pull(cursor))


def refs(pulled) -> list[str]:
    return [draft.source_ref for draft in pulled.drafts]


def test_each_request_asks_for_exactly_what_the_notes_document():
    stand = Stand()
    one = stand.add(1, SINCE + HOUR)
    two = stand.add(2, SINCE + 2 * HOUR, name="Plan.md", mimeType=d.MARKDOWN)
    assert refs(stand.pull()) == [f"drive:{one}", f"drive:{two}"]
    (folder,) = stand.of("folder")
    assert dict(folder.url.params) == {
        "fields": "id,mimeType,trashed,capabilities/canListChildren",
        "supportsAllDrives": "true",
    }
    (listing,) = stand.of("list")
    assert dict(listing.url.params) == {
        "q": f"'{FOLDER}' in parents and trashed = false",
        "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink,"
        "lastModifyingUser/displayName,capabilities/canDownload)",
        "pageSize": "1000",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    (export,) = stand.of("export")
    assert export.url.path == f"/drive/v3/files/{one}/export"
    assert dict(export.url.params) == {"mimeType": "text/markdown"}
    (media,) = stand.of("media")
    assert media.url.path == f"/drive/v3/files/{two}"
    assert dict(media.url.params) == {"alt": "media", "supportsAllDrives": "true"}
    assert export.headers["accept"] == media.headers["accept"] == "*/*"
    assert listing.headers["accept"] == "application/json"


def test_a_half_written_document_goes_on_from_its_next_part_whatever_sorts_before_it():
    stand = Stand()
    long = stand.add(1, SINCE + HOUR, "\n\n".join(paragraphs(1_600)), name="Long")
    whole = stand.pull()
    assert refs(whole) == [f"drive:{long}", f"drive:{long}/2"]
    stopped = whole.drafts[0].resume  # a run stopped after the first part
    moved_in = stand.add(2, SINCE - 300 * DAY)  # an old document put in meanwhile sorts first
    stand.now += HOUR
    rest = stand.pull(stopped)
    assert refs(rest) == [f"drive:{moved_in}", f"drive:{long}/2"]
    stand.now += HOUR
    assert stand.pull(rest.cursor).drafts == []


def test_a_half_written_document_edited_while_off_is_not_continued_after_the_switch_on():
    """A run stopped between two parts of a document, the source was switched off, and the
    document was edited while it was off. At the next switch-on the pull went on from the
    next part of the edited text: what was written while the source was off was read, and
    the note's parts came from two versions."""
    stand = Stand()
    long = stand.add(1, SINCE + HOUR, "\n\n".join(paragraphs(1_600)), name="Long")
    stopped = stand.pull().drafts[0].resume  # stopped after the first part; then off
    stand.add(1, NOW + HOUR, "\n\n".join(paragraphs(1_600, 5_000)), name="Long")  # while off
    again = NOW + 2 * DAY
    stand.now = again + DAY
    after = stand.pull(stopped, since=again)
    assert after.drafts == [] and after.skipped == []
    # it counts as there: an edit after the switch-on is read whole, from its first part
    stand.add(1, again + HOUR, "\n\n".join(paragraphs(1_600, 9_000)), name="Long")
    stand.now = again + 2 * DAY
    later = stand.pull(after.cursor, since=again)
    assert refs(later) == [f"drive:{long}", f"drive:{long}/2"]
    assert held(later.drafts[0].text)[0] == 9_000 and held(later.drafts[1].text)[-1] == 10_599


def test_with_existing_a_half_written_document_edited_while_off_is_read_whole_again():
    """With `existing` the documents already in the folder at a switch-on are read, so one
    half written before it is read again from its first part, as it stands then: its page
    gets the first part a second time, and never a part of one version after a part of
    another."""
    stand = Stand()
    long = stand.add(1, SINCE + HOUR, "\n\n".join(paragraphs(1_600)), name="Long")
    stopped = stand.pull(existing=True).drafts[0].resume
    stand.add(1, NOW + HOUR, "\n\n".join(paragraphs(1_600, 5_000)), name="Long")  # while off
    again = NOW + 2 * DAY
    stand.now = again + DAY
    after = stand.pull(stopped, since=again, existing=True)
    assert refs(after) == [f"drive:{long}", f"drive:{long}/2"]
    assert held(after.drafts[0].text)[0] == 5_000 and held(after.drafts[1].text)[-1] == 6_599


def test_at_most_25_documents_are_fetched_and_skips_without_a_request_do_not_count():
    stand = Stand()
    for n in range(5):
        stand.add(n, SINCE + n * MINUTE, capabilities={"canDownload": False})
    for n in range(5, 40):
        stand.add(n, SINCE + n * MINUTE)
    got = stand.pull()
    assert len(got.skipped) == 5 and len(got.drafts) == 25 == len(stand.of("export"))
    assert {s.reason for s in got.skipped} == {d.NO_DOWNLOAD}


def test_content_past_10_mb_is_skipped_once_as_too_large():
    stand = Stand()
    big = stand.add(1, SINCE + HOUR)
    stand.texts[big] = b"x" * (d.MAX_BYTES + 1)
    small = stand.add(2, SINCE + 2 * HOUR)
    got = stand.pull()
    assert refs(got) == [f"drive:{small}"]
    assert [(s.ref, s.reason) for s in got.skipped] == [(big, d.TOO_LARGE)]
    stand.add(3, SINCE + 3 * HOUR, mimeType=d.TEXT, size=str(d.MAX_BYTES + 1))
    stand.now += HOUR
    again = stand.pull(got.cursor)
    assert [s.reason for s in again.skipped] == [d.TOO_LARGE] and stand.of("media") == []


def test_rate_limits_wait_the_documented_backoff_and_then_the_pull_keeps_what_it_read():
    stand = Stand()
    first = stand.add(1, SINCE + HOUR)
    stand.add(2, SINCE + 2 * HOUR)
    limited = error(403, "userRateLimitExceeded")
    stand.answers["export"] = [
        lambda r: httpx.Response(200, content=b"One.\n"),
        lambda r: limited,
    ]
    got = stand.pull()
    assert refs(got) == [f"drive:{first}"] and stand.slept == [1.5, 2.5, 4.5, 8.5]
    assert len(stand.of("export")) == 6


@pytest.mark.parametrize(
    ("answers", "said"),
    [
        (
            {"folder": lambda r: error(404, "notFound")},
            "Google Drive found no folder by the id in folder_id: check the id, and that the"
            " folder is shared with the account that reads it",
        ),
        (
            {"folder": lambda r: httpx.Response(200, json={"id": FOLDER, "mimeType": d.DOC})},
            "folder_id names a file that is not a folder: give the id of a folder, the last part"
            " of its address after /folders/",
        ),
        (
            {
                "folder": lambda r: httpx.Response(
                    200, json={"id": FOLDER, "mimeType": d.FOLDER_TYPE, "trashed": True}
                )
            },
            "the folder folder_id names is in the trash: restore it from the trash, or give"
            " another folder's id",
        ),
        (
            {
                "folder": lambda r: httpx.Response(
                    200,
                    json={
                        "id": FOLDER,
                        "mimeType": d.FOLDER_TYPE,
                        "capabilities": {"canListChildren": False},
                    },
                )
            },
            "the account that reads may not list the folder folder_id names (a folder with"
            " limited access, which Google lists as empty): share that folder itself with it",
        ),
        (
            {"list": lambda r: error(403, "insufficientPermissions")},
            "Google Drive refused access (HTTP 403) to files.list: check that the credential in"
            f" {VAR} carries the scope https://www.googleapis.com/auth/drive.readonly (for a"
            " service account acting for a subject, in its domain-wide delegation), that the"
            " Google Drive API is enabled in its Cloud project, that no administrator's policy"
            " blocks it, and that the folder is shared with the account that reads it",
        ),
        (
            {"list": lambda r: error(404, "notFound")},
            "Google Drive found no folder by the id in folder_id: check the id",
        ),
        (
            {"list": lambda r: error(403, "dailyLimitExceeded")},
            "Google Drive answered files.list that the Cloud project's daily limit is reached, a"
            " cap set on its quota: raise or remove it in the Cloud console; the next run tries"
            " again",
        ),
        (
            {"list": lambda r: error(429)},
            "Google Drive still answered with a rate limit to files.list after 4 retries: the"
            " next run goes on",
        ),
        (
            {"list": lambda r: error(503)},
            "Google Drive answered HTTP 503 to files.list, a fault on its side: the next run"
            " tries again",
        ),
        (
            {"folder": lambda r: httpx.Response(302, headers={"location": "https://x.test/"})},
            "Google Drive answered files.get on the folder with a redirect (HTTP 302), which"
            " this server does not follow: check base_url",
        ),
        (
            {"list": lambda r: error(400, "badRequest")},
            "Google Drive refused the request to files.list (HTTP 400)",
        ),
        (
            {"export": lambda r: error(401, "authError", "details")},
            "Google Drive refused access (HTTP 401) to files.export: check that the credential",
        ),
        (
            {"export": lambda r: error(403, "domainPolicy")},
            "Google Drive refused access (HTTP 403) to files.export: check that the credential",
        ),
        (
            {"export": lambda r: error(500)},
            "Google Drive answered HTTP 500 to files.export, a fault on its side",
        ),
        (
            {"export": lambda r: error(410)},
            "Google Drive refused the request to files.export (HTTP 410)",
        ),
        (
            {"export": lambda r: httpx.Response(307, headers={"location": "https://x.test/"})},
            "Google Drive answered files.export with a redirect (HTTP 307), which this server does"
            " not follow: check base_url",
        ),
    ],
)
def test_each_refusal_says_what_happened_and_what_to_check(answers, said):
    stand = Stand()
    stand.add(1, SINCE + HOUR)
    stand.answers.update(answers)
    with pytest.raises(SourceError) as failed:
        stand.pull()
    message = str(failed.value)
    assert message.startswith(said), message
    assert CANARY not in message and TOKEN not in message and BASE not in message


@pytest.mark.parametrize(
    ("endpoint", "body", "what"),
    [
        ("folder", [CANARY], "files.get on the folder"),
        ("folder", {"id": FOLDER}, "files.get on the folder"),
        ("list", {"files": {}}, "files.list"),
        ("list", {"files": [], "nextPageToken": 7}, "files.list"),
        ("list", {"files": [7]}, "files.list"),
        ("list", {"files": [{"id": "x y", "mimeType": "text/plain"}]}, "files.list"),
        ("list", {"files": [{"id": "1UnitDoc00001XyZw"}]}, "files.list"),
        (
            "list",
            {"files": [{"id": "1UnitDoc00001XyZw", "mimeType": d.DOC, "name": "N"}]},
            "files.list",
        ),
        (
            "list",
            {
                "files": [
                    {
                        "id": "1UnitDoc00001XyZw",
                        "mimeType": d.TEXT,
                        "name": "N",
                        "modifiedTime": "2026-11-12T09:30:41Z",
                        "size": "big",
                    }
                ]
            },
            "files.list",
        ),
    ],
)
def test_an_answer_not_of_the_documented_shape_fails_the_pull(endpoint, body, what):
    stand = Stand()
    stand.add(1, SINCE + HOUR)
    stand.answers[endpoint] = lambda request: httpx.Response(200, json=body)
    with pytest.raises(SourceError) as failed:
        stand.pull()
    assert str(failed.value) == f"Google Drive's answer to {what} is not the documented shape"


def test_a_file_of_another_type_needs_only_an_id_and_a_type():
    stand = Stand()
    one = stand.add(1, SINCE + HOUR)
    stand.files["sheet"] = {"id": "1UnitSheet00XyZw", "mimeType": "application/pdf"}
    assert refs(stand.pull()) == [f"drive:{one}"]


def test_a_listing_of_more_pages_than_cairn_reads_fails_the_pull(monkeypatch):
    monkeypatch.setattr(d, "MAX_PAGES", 3)
    stand = Stand()
    stand.page = 1
    for n in range(4):
        stand.add(n, SINCE + HOUR)
    with pytest.raises(SourceError) as failed:
        stand.pull()
    assert str(failed.value) == "Google Drive listed more than 3 pages of the folder in one pull"
    stand.files.popitem()
    assert len(stand.pull().drafts) == 3


def test_a_listing_of_more_files_than_its_pages_hold_fails_the_pull(monkeypatch):
    """Google chooses how many files a page holds, so a listing bounded by its pages alone
    kept whatever one page brought. It now ends at MAX_PAGES full pages of files too, every
    file counted, of a type cairn reads or not."""
    monkeypatch.setattr(d, "MAX_PAGES", 1)
    monkeypatch.setattr(d, "PAGE_SIZE", 2)
    stand = Stand()  # one page of three
    stand.add(1, SINCE + HOUR)
    stand.add(2, SINCE + HOUR)
    stand.files["sheet"] = {"id": "1UnitSheet00XyZw", "mimeType": "application/pdf"}
    with pytest.raises(SourceError) as failed:
        stand.pull()
    assert str(failed.value) == "Google Drive listed more than 2 files of the folder in one pull"
    stand.files.pop("sheet")
    assert len(stand.pull().drafts) == 2


def test_a_pull_stops_at_its_budget_of_requests_and_the_next_goes_on(monkeypatch):
    monkeypatch.setattr(d, "BUDGET", 4)  # the folder, the listing, two contents
    stand = Stand()
    for n in range(3):
        stand.add(n, SINCE + n * HOUR)
    got = stand.pull()
    assert len(got.drafts) == 2
    stand.now += HOUR
    assert len(stand.pull(got.cursor).drafts) == 1


def test_a_deadline_before_it_signs_in_or_reads_one_document_fails_the_pull():
    stand = Stand()
    stand.add(1, SINCE + HOUR)
    with pytest.raises(SourceError) as failed:
        stand.pull(deadline=NOW)
    assert str(failed.value) == (
        "no time was left to sign in to Google within one pull's deadline, so the cursor"
        " cannot move: the next run tries again"
    )
    assert stand.requests == []


def test_a_deadline_that_comes_before_the_first_document_is_read_fails_the_pull():
    stand = Stand()
    stand.add(1, SINCE + HOUR)
    listing = stand.answers["list"] = []

    def listed(request: httpx.Request) -> httpx.Response:
        stand.now = NOW + HOUR  # the listing took the time that was left
        return httpx.Response(200, json={"files": list(stand.files.values())})

    listing.append(listed)
    with pytest.raises(SourceError) as failed:
        stand.pull(deadline=NOW + 30 * MINUTE)
    assert str(failed.value) == (
        "no document could be read within one pull's bounds (its deadline), so the cursor"
        " cannot move: the next run tries again"
    )


def test_a_deadline_that_comes_while_the_folder_is_listed_fails_the_pull():
    stand = Stand()
    stand.add(1, SINCE + HOUR)

    def folder(request: httpx.Request) -> httpx.Response:
        stand.now = NOW + HOUR
        return httpx.Response(200, json=stand.folder)

    stand.answers["folder"] = folder
    with pytest.raises(SourceError) as failed:
        stand.pull(deadline=NOW + 30 * MINUTE)
    assert str(failed.value) == (
        "the folder could not be listed within one pull's bounds (its deadline), so the cursor"
        " cannot move: the next run tries again"
    )


def test_a_document_there_at_the_stock_taking_and_read_later_is_kept_once_in_the_cursor():
    stand = Stand()
    edited = stand.add(1, SINCE - DAY)
    locked = stand.add(2, SINCE - DAY, capabilities={"canDownload": False})
    first = stand.pull()
    assert json.loads(first.cursor)["there"] == sorted([d.digest(edited), d.digest(locked)])
    for file_id in (edited, locked):
        stand.files[file_id]["modifiedTime"] = "2026-11-11T09:00:00.000Z"  # after the switch-on
    stand.now += 2 * DAY
    got = stand.pull(first.cursor)
    assert refs(got) == [f"drive:{edited}"] and [s.ref for s in got.skipped] == [locked]
    state = json.loads(got.cursor)
    assert state["there"] == [] and state["done"] == sorted([d.digest(edited), d.digest(locked)])


def test_a_pull_that_skipped_and_then_ran_out_keeps_its_skips():
    stand = Stand()
    stand.add(1, SINCE + HOUR, capabilities={"canDownload": False})
    stand.add(2, SINCE + 2 * HOUR)
    stand.answers["export"] = lambda request: error(429)
    got = stand.pull()
    assert got.drafts == [] and [s.reason for s in got.skipped] == [d.NO_DOWNLOAD]
    stand.answers.clear()
    stand.now += HOUR
    assert len(stand.pull(got.cursor).drafts) == 1


def test_the_token_and_the_credential_reach_no_log_line(caplog):
    from cairn.sources import source_client

    stand = Stand()
    stand.add(1, SINCE + HOUR)
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.DEBUG)
    try:
        with caplog.at_level(logging.DEBUG):
            source = stand.source(http=source_client(transport=httpx.MockTransport(stand.handler)))
            got = asyncio.run(source.pull(None))
    finally:
        root.setLevel(level)
    assert len(got.drafts) == 1
    assert holds_secret(f"x {TOKEN} y", tuple(source.secrets))
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (TOKEN, SECRET, REFRESH):
        assert secret not in logged
    assert "HTTP Request" not in logged


def test_each_skip_reason_is_a_fixed_sentence_that_says_what_follows():
    reasons = (d.NO_DOWNLOAD, d.TOO_LARGE, d.GONE, d.REFUSED, d.EMPTY, d.TOO_LONG)
    for reason in reasons:
        assert reason == " ".join(reason.split()) and ":" in reason
        assert "taken out of the folder and put back" in reason
    assert d.TOO_LARGE.startswith("too large:") and d.TOO_LONG.startswith("too long:")
    assert d.GONE.startswith("gone:") and d.EMPTY.startswith("empty:")
    assert "10 MB" in d.TOO_LARGE and "20" in d.TOO_LONG


@pytest.mark.parametrize(
    "value", ["0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59", "2026-11-13T10:00:00+24:00"]
)
def test_a_time_that_cannot_be_in_utc_is_not_a_moment(value):
    """A time Drive could not really send must come back as None, never as an exception
    that ends a pull without a reason."""
    assert d.moment(value) is None


def test_a_document_cairn_fails_on_is_skipped_once_and_the_rest_written(monkeypatch):
    """Anything but a SourceError while one document is made into drafts (a ValueError, a
    RecursionError) used to fail the pull, so the cursor never moved past that document."""
    stand = Stand()
    one = stand.add(1, SINCE + HOUR)
    two = stand.add(2, SINCE + 2 * HOUR)
    three = stand.add(3, SINCE + 3 * HOUR)
    made = d.drafts

    def failing(document, text, project):
        if document.id == two:
            raise RecursionError("maximum recursion depth exceeded")
        return made(document, text, project)

    monkeypatch.setattr(d, "drafts", failing)
    first = stand.pull()
    assert refs(first) == [f"drive:{one}", f"drive:{three}"]
    assert [(s.ref, s.reason) for s in first.skipped] == [(two, d.UNREADABLE)]
    again = stand.pull(first.cursor)
    assert again.drafts == again.skipped == []
