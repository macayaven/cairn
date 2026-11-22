"""Unit tests for the Gmail source: what tests/acceptance/test_c6_gmail.py does not pin. HTTP
goes through httpx.MockTransport; time, sleep and the backoff's jitter are injected. The
credential is a made-up refresh token, so no key is generated here; every name, address and
id is made up."""

import asyncio
import json
import logging
from datetime import UTC, datetime
from urllib.parse import unquote

import httpx
import pytest

from cairn.compiler import slugify
from cairn.sources import SourceError, holds_secret
from cairn.sources import gmail as g
from cairn.sources.google_api import Reply
from cairn.store.memory import FIELD_MAX
from cairn.store.usage import passages_of
from support.sources.gmail import (
    BASE,
    CANARY,
    CREDENTIAL,
    DAY,
    HOUR,
    LABEL_ID,
    MINUTE,
    NOW,
    REFRESH,
    SECRET,
    SINCE,
    TOKEN,
    VAR,
    Stand,
    b64,
    error,
    mail,
    part,
    read,
    refs,
    settings,
    tree,
)


def refusal(config) -> str:
    with pytest.raises(ValueError) as refused:
        g.check_config(config)
    return str(refused.value)


# The kind and its config


def test_the_kind_starts_off_proposes_mail_and_says_what_switching_on_reads():
    kind = g.KIND
    assert kind.check is g.check_config and kind.open is g.open_source
    assert kind.describe is g.describe
    assert kind.starts_off and kind.via == "gmail" == g.LABEL
    assert kind.default_project == "mail"
    assert (
        kind.switching_on()
        == g.SWITCH_ON
        == (
            "Switching it on reads the messages that arrive from then on and carry the label, each"
            " once: nothing that arrived before, even when it is labelled later."
        )
    )
    assert g.SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_the_defaults_are_filled_in_and_what_is_stored_checks_the_same():
    checked = g.check_config({"credentials_env": VAR, "label": "cairn"})
    assert checked == {
        "credentials_env": VAR,
        "label": "cairn",
        "lookback_days": 30,
        "projects": [],
        "base_url": "https://gmail.googleapis.com/gmail/v1",
    }
    assert g.check_config(checked) == checked
    kept = g.check_config(settings(subject="ada@example.test", base_url=BASE + "/"))
    assert (kept["subject"], kept["base_url"]) == ("ada@example.test", BASE)


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({"credentials_env": None}, "a gmail source needs credentials_env"),
        ({"credentials_env": "lower"}, "credentials_env names an environment variable"),
        ({"subject": "ada"}, "subject is one email address"),
        ({"subject": "a" * 250 + "@example.test"}, "subject is one email address"),
        ({"label": None}, "a gmail source needs label: the name of the Gmail label"),
        ({"label": ""}, "label is the name of one Gmail label"),
        ({"label": "L" * 201}, "label is the name of one Gmail label"),
        ({"label": "cairn\tnotes"}, "label is the name of one Gmail label"),
        ({"label": "cairn\x7f"}, "label is the name of one Gmail label"),
        ({"label": "INBOX"}, "label names one of Gmail's own labels"),
        ({"label": "category_updates"}, "label names one of Gmail's own labels"),
        ({"lookback_days": 0}, "lookback_days is a whole number from 1 to 365"),
        ({"lookback_days": 30.0}, "lookback_days is a whole number from 1 to 365"),
        ({"projects": ["a", "b"]}, "projects names at most one project"),
        ({"projects": ["Not a name!"]}, "projects holds a project's name"),
        ({"projects": "mail"}, "projects is a list of at most one project's name"),
        ({"base_url": "http://gmail.example.test/v1"}, "base_url is https"),
        ({"base_url": 7}, "base_url is the address of the Gmail API (v1)"),
    ],
)
def test_each_refusal_names_its_setting_and_says_what_it_takes(given, said):
    assert refusal(settings(**given)).startswith(said)


@pytest.mark.parametrize(
    "label", ["Clients/Acme", "Inbox zero", "Starred later", "CATEGORY", "To read 👩‍💻", "Ünïcode"]
)
def test_a_label_of_the_persons_own_is_kept_as_written(label):
    """Only a control character is refused: an emoji's zero-width joiner is not one."""
    assert g.check_config(settings(label=label))["label"] == label


def test_an_unknown_setting_is_named_unless_its_name_could_be_a_credential():
    assert refusal(settings(query="in:anywhere")) == (
        "a gmail source has no setting 'query': it takes credentials_env, subject, label,"
        " lookback_days, projects, base_url"
    )
    pasted = "ya29." + "unitMailPasted" + "Token"
    said = refusal({**settings(), pasted: 1})
    assert pasted not in said and said.startswith("a gmail source does not take")


@pytest.mark.parametrize("setting", ["credentials_env", "subject", "label", "projects", "base_url"])
@pytest.mark.parametrize("value", [CREDENTIAL, "-----BEGIN PRIVATE KEY-----\nMII", TOKEN, REFRESH])
def test_a_credential_in_any_setting_is_refused_without_repeating_it(setting, value):
    given = settings(**{setting: [value] if setting == "projects" else value})
    said = refusal(given)
    assert setting in said
    for secret in (TOKEN, SECRET, REFRESH, "MII"):
        assert secret not in said


def test_the_page_says_whose_mail_under_which_label_and_never_the_variable():
    theirs = g.describe(g.check_config(settings(base_url=BASE)))
    assert theirs == (
        "Messages in the mailbox of the person who authorised its refresh token that carry the"
        f" Gmail label “cairn”, at {BASE}; each is read once, when it is labelled within 30 days"
        " of arriving"
    )
    named = g.describe(
        g.check_config(settings(subject="ada@example.test", label="Clients/Acme", lookback_days=1))
    )
    assert named == (
        "Messages in the mailbox of ada@example.test that carry the Gmail label “Clients/Acme”, at"
        " https://gmail.googleapis.com/gmail/v1; each is read once, when it is labelled within 1"
        " day of arriving"
    )
    for said in (theirs, named):
        assert VAR not in said and "credentials_env" not in said


# Opening


def test_opening_reads_the_credential_and_its_secrets_and_sends_nothing():
    sent = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: sent.append(r)))
    source = g.open_source("mail", g.check_config(settings()), http=http, env={VAR: CREDENTIAL})
    assert holds_secret(f"said {REFRESH} once", tuple(source.secrets))
    assert holds_secret(SECRET, tuple(source.secrets))
    assert REFRESH not in repr(source) and sent == []
    with pytest.raises(SourceError, match=VAR):
        g.open_source("mail", g.check_config(settings()), http=http, env={})


def test_a_refresh_token_with_a_subject_is_refused_by_its_setting():
    config = g.check_config(settings(subject="ada@example.test"))
    with pytest.raises(SourceError) as refused:
        g.open_source("mail", config, http=None, env={VAR: CREDENTIAL})
    assert str(refused.value) == (
        "source 'mail' reads with a refresh token, which reads the mailbox of the person who"
        " authorised it: remove subject from its config"
    )


def test_the_mailbox_is_named_by_the_subjects_address_or_me():
    """**[cairn]** The notes do not state that "me" names the impersonated person under
    domain-wide delegation; the address is the documented form."""
    assert g.mailbox(None) == "me"
    assert g.mailbox("ada+ops@example.test") == "ada%2Bops%40example.test"


# The title, the topic and the facts


@pytest.mark.parametrize(
    ("header", "title"),
    [
        ("Budget review", "Budget review"),
        ("  Budget\r\n review  ", "Budget review"),
        ("=?UTF-8?B?" + b64("Revisión") + "?=", "Revisión"),
        ("=?x-unknown?Q?Caf=E9?=", "=?x-unknown?Q?Caf=E9?="),  # left as it came
        ("=?UTF-8?Q?Caf=C3=A9", "=?UTF-8?Q?Caf=C3=A9"),  # not closed: not an encoded word
        (None, "(no subject)"),
        ("   ", "(no subject)"),
    ],
)
def test_a_title_is_the_subject_decoded_on_one_line(header, title):
    assert g.title_of(header) == title


def test_a_long_subject_is_cut_at_a_word_to_what_a_title_and_its_heading_hold():
    long = g.title_of(" ".join(["Quarterly"] * 40))
    assert len(long) <= g.TITLE_MAX and long.endswith("Quarterly")
    wide = g.title_of("Ünïcödé " * 60)
    assert len(json.dumps(wide, ensure_ascii=False).encode()) - 2 <= g.TITLE_BYTES
    assert wide.endswith("Ünïcödé")
    one = g.title_of("x" * 400)
    assert len(one) == g.TITLE_MAX


@pytest.mark.parametrize(
    ("subject", "topic"),
    [
        ("Budget review", "Budget review, 2026-11"),
        ("Re: Fwd: AW: WG: FW: Fw: re: Budget review", "Budget review, 2026-11"),
        ("RE :Budget review", "Budget review, 2026-11"),
        ("Re: Re:", "(no subject), 2026-11"),
        ("Reply needed", "Reply needed, 2026-11"),  # "Re" without a colon is a word
        ("Budget: Re: review", "Budget: Re: review, 2026-11"),  # only in front
    ],
)
def test_a_topic_is_the_subject_without_its_prefixes_and_the_month(subject, topic):
    assert g.topic(subject, datetime(2026, 11, 30, 23, 59, tzinfo=UTC)) == topic


def test_a_long_subjects_topic_leaves_room_in_the_slug_for_the_month_and_a_part():
    subject = "Quarterly planning for the platform team: budget, hiring, roadmap and the offsite"
    made = g.topic(subject, datetime(2026, 11, 2, tzinfo=UTC))
    assert made == "Quarterly planning for the platform team: budget, 2026-11"
    assert len(slugify(made + ", part 20")) <= 64
    assert slugify(made + ", part 20").endswith("2026-11-part-20")
    wide = g.topic("Ü" * 300, datetime(2026, 11, 2, tzinfo=UTC))
    assert len(wide + ", part 20") <= FIELD_MAX["topic"]


@pytest.mark.parametrize("subject", ["word " * 50_000, "x" * 250_000, "Ü " * 125_000])
def test_a_long_subject_is_cut_to_its_bound_before_any_word_is_taken_off(monkeypatch, subject):
    """The work of cutting a subject is bounded by its title's and topic's bounds, not by its
    length: the whole subject is slugged once (for a subject whose slug keeps nothing, its
    tag), and every other slug or heading measured is of at most a bound's characters. The
    old cut slugged the whole subject again after taking off each word (a 250 KB subject
    took 88 s)."""
    measured: list[int] = []
    full_slug, path_bytes = g._full_slug, g.path_bytes

    def counted_slug(text):
        measured.append(len(text))
        return full_slug(text)

    def counted_bytes(path):
        measured.append(sum(len(p) for p in path))
        return path_bytes(path)

    monkeypatch.setattr(g, "_full_slug", counted_slug)
    monkeypatch.setattr(g, "path_bytes", counted_bytes)
    at = datetime(2026, 11, 2, tzinfo=UTC)
    made, title = g.topic(subject, at), g.title_of(subject)
    bound = max(g.TITLE_MAX, g.TOPIC_TEXT_MAX) + 1
    assert sorted(measured)[-2] <= bound  # at most one measure of the whole subject
    assert sum(measured) <= len(subject) + 400 * bound
    assert made.endswith(", 2026-11") and len(title) <= g.TITLE_MAX


def test_a_header_of_many_encoded_words_is_decoded_only_up_to_its_bound(monkeypatch):
    """The standard library's decoding of RFC 2047 words takes time growing with the square of
    a header's length (50,000 words took 2.7 s, so these 200,000 took about 40 s), so only a
    header's first HEADER_MAX characters are ever handed to it. What is pinned is that work,
    not the clock: a bound of one second failed at 1.02 s on a slow machine."""
    handed: list[int] = []
    decode = g.decode_header

    def counted(value):
        handed.append(len(value))
        return decode(value)

    monkeypatch.setattr(g, "decode_header", counted)
    words = "=?utf-8?q?a?= " * 200_000
    assert g.title_of(words) == "a" * g.TITLE_MAX
    assert g.people(f'"{words}" <ada@example.test>') == "a" * (g.HEADER_MAX // 14)
    assert g.HEADER_MAX == 16_384
    assert 1 <= len(handed) <= 4 and max(handed) <= g.HEADER_MAX, handed


def test_a_subject_with_no_letter_a_slug_keeps_gets_a_tag_of_its_own():
    """The compiler's slug keeps ASCII letters and digits only: without a tag, every such
    subject of a month would share one page. The tag comes from the subject, so a
    conversation's messages stay together."""
    at = datetime(2026, 11, 2, tzinfo=UTC)
    one, again, other = (
        g.topic("予算の確認", at),
        g.topic("Re: 予算の確認", at),
        g.topic("採用", at),
    )
    assert one == again and one != other
    assert one.startswith("予算の確認, ") and one.endswith(", 2026-11")
    assert slugify(one) != slugify(other) and slugify(one) != "2026-11"


@pytest.mark.parametrize(
    ("header", "line"),
    [
        ("Ada Lovelace <ada@example.test>", "Ada Lovelace"),
        (
            '"Lovelace, Ada" <ada@example.test>, brook@partner.example.test',
            "Lovelace, Ada, partner.example.test",
        ),
        ('"ada@example.test" <ada@example.test>', "example.test"),  # a name that is an address
        ("Ada (ada@example.test) <ada@example.test>", "example.test"),
        ('"ada\uff20example.test" <ada@example.test>', "example.test"),  # a fullwidth at sign
        ("=?UTF-8?Q?ada=EF=B9=ABexample.test?= <ada@example.test>", "example.test"),  # a small one
        ("=?UTF-8?Q?Lovelace=2C_Ad=C3=A1?= <ada@example.test>", "Lovelace, Adá"),
        ("undisclosed-recipients:;", None),
        ("", None),
    ],
)
def test_people_are_named_and_one_without_a_name_is_their_domain(header, line):
    assert g.people(header) == line


def test_more_than_ten_people_are_counted():
    header = ", ".join(f"Person {k} <p{k}@example.test>" for k in range(13))
    assert g.people(header) == ", ".join(f"Person {k}" for k in range(10)) + " and 3 more"


def test_an_attachment_named_with_an_at_sign_of_any_width_is_counted_not_shown():
    payload = tree(
        "multipart/mixed",
        part("text/plain", "Body."),
        part("application/pdf", filename="Budget.pdf", attachmentId="unitatt1"),
        part("application/pdf", filename="ada\uff20example.test.pdf", attachmentId="unitatt2"),
        part("application/pdf", filename="ada\ufe6bexample.test.pdf", attachmentId="unitatt3"),
    )
    assert g.attachments(payload) == "Budget.pdf and 2 more"


def test_the_facts_say_when_who_and_which_files_without_an_address():
    payload = tree(
        "multipart/mixed",
        part("text/plain", "Body."),
        part("application/pdf", filename="Budget.pdf", attachmentId="unitatt1"),
        part("text/plain", "x", filename="from-ada@example.test.txt"),
        *(part("image/png", filename=f"chart{k}.png", attachmentId="a") for k in range(10)),
    )
    item = mail(
        at=datetime(2026, 11, 12, 14, 7, 41, tzinfo=UTC),
        headers={
            "To": "Brook Lane <brook@example.test>",
            "cc": "cy@example.test",
            "From": '"Lovelace, Ada" <ada@example.test>',
        },
        payload=payload,
    )
    facts = g.facts(read(item))
    assert facts == [
        "- Arrived: 2026-11-12 14:07 UTC",
        "- From: Lovelace, Ada",
        "- To: Brook Lane",
        "- Cc: example.test",
        "- Attachments: Budget.pdf, chart0.png, chart1.png, chart2.png, chart3.png,"
        " chart4.png, chart5.png, chart6.png, chart7.png, chart8.png and 2 more",
    ]


# The body


def test_the_body_is_the_first_plain_text_with_words_else_the_first_html():
    blank_plain = tree(
        "multipart/alternative", part("text/plain", " \r\n "), part("text/html", "<p>Kept</p>")
    )
    assert g.body_of(blank_plain) == "Kept"
    moved_out = tree(
        "multipart/alternative",
        part("text/plain", attachmentId="unitatt2"),  # its data held apart: not read
        part("text/plain", "Second plain"),
    )
    assert g.body_of(moved_out) == "Second plain"
    assert g.body_of(part("TEXT/PLAIN", "Upper case type")) == "Upper case type"
    assert g.body_of(tree("multipart/mixed", part("image/png", filename="a.png"))) is None


def test_line_ends_become_newlines_and_blank_lines_at_either_end_go():
    payload = part("text/plain", "\r\n\r\nFirst line\r\nsecond\rthird\r\n\r\n")
    assert g.body_of(payload) == "First line\nsecond\nthird"


@pytest.mark.parametrize("data", ["not base64!", "abc$", 7, "a"])
def test_body_data_that_is_not_base64url_fails_without_quoting_it(data):
    payload = {"mimeType": "text/plain", "filename": "", "body": {"data": data}}
    with pytest.raises(SourceError) as failed:
        g.body_of(payload)
    assert str(failed.value) == (
        "Gmail's answer to messages.get is not the documented shape (a part's data is not"
        " base64url)"
    )


@pytest.mark.parametrize(
    "item",
    [
        [CANARY],
        {"payload": {}},
        {**mail(), "internalDate": CANARY},
        {**mail(), "internalDate": "-5"},
        {**mail(), "internalDate": "9" * 17},
        {**mail(), "payload": CANARY},
        {**mail(), "labelIds": CANARY},
        {**mail(), "labelIds": [7]},
    ],
)
def test_a_message_not_of_the_documented_shape_fails_without_its_values(item):
    with pytest.raises(SourceError) as failed:
        g.read_message("unitm0001", json.dumps(item).encode())
    said = str(failed.value)
    assert said.startswith("Gmail's answer to messages.get is not the documented shape")
    assert CANARY not in said and "unitm" not in said


def test_a_message_that_is_not_json_fails_without_quoting_it():
    with pytest.raises(SourceError) as failed:
        g.read_message("unitm0001", f"<html>{CANARY}</html>".encode())
    assert str(failed.value) == "Gmail's answer to messages.get is not JSON"


# The draft and its parts


def test_the_text_is_a_heading_the_facts_and_the_body_with_nothing_after():
    item = mail(headers={"Subject": "Re: Budget review", "To": "Brook <brook@example.test>"})
    (draft,) = g.drafts(read(item), "mail")
    assert draft.text == (
        "# Re: Budget review\n\n- Arrived: 2026-11-12 13:00 UTC\n- From: Ada Lovelace\n"
        "- To: Brook\n\nItem 1 is agreed.\n"
    )
    assert (draft.title, draft.topic) == ("Re: Budget review", "Budget review, 2026-11")
    assert (draft.source_ref, draft.label, draft.project) == ("gmail:unitm0001", "gmail", "mail")


def test_a_message_without_a_text_part_has_no_drafts_but_a_reason():
    item = mail(payload=tree("multipart/mixed", part("image/png", filename="scan.png")))
    assert g.drafts(read(item), "mail") == g.NO_TEXT


def test_every_draft_s_source_ref_begins_with_the_kind_s_word():
    """M3: `KIND.ref_form` is the word before the first ":" of every source_ref this kind
    writes, so a note a person or an agent writes the same way is withheld as these are."""
    from cairn.sources import source_ref_word

    body = "\n\n".join(f"Paragraph {k}." for k in range(3_100))
    made = [
        *g.drafts(read(mail(headers={"Subject": "Re: Budget review"})), "mail"),
        *g.drafts(read(mail(payload=part("text/plain", body))), "mail"),
    ]
    assert len(made) == 4
    assert {source_ref_word(d.source_ref) for d in made} == {g.KIND.ref_form} == {"gmail"}


def test_each_part_holds_at_most_1500_passages_as_the_store_counts_them():
    body = "\n\n".join(f"Paragraph {k}." for k in range(3_100))
    made = g.drafts(read(mail(payload=part("text/plain", body))), "mail")
    counts = [passages_of(d.text) for d in made]
    assert counts == [1_500, 1_500, 102]  # the two facts (when, from) are passages too
    assert [d.source_ref for d in made] == [
        "gmail:unitm0001",
        "gmail:unitm0001/2",
        "gmail:unitm0001/3",
    ]
    assert made[1].text.startswith("# Item 1, part 2\n\nParagraph 1498.\n")
    assert made[2].text.endswith("Paragraph 3099.\n")
    assert [d.topic for d in made] == [
        "Item 1, 2026-11",
        "Item 1, 2026-11, part 2",
        "Item 1, 2026-11, part 3",
    ]
    assert all(d.title == "Item 1" for d in made)


def test_a_body_that_starts_like_front_matter_is_counted_as_it_is_written():
    body = "---\ntitle: not front matter here\n---\n\n" + "\n\n".join(
        f"P {k}." for k in range(1_500)
    )
    made = g.drafts(read(mail(payload=part("text/plain", body))), "mail")
    assert all(passages_of(d.text) <= 1_500 for d in made) and len(made) == 2


def test_a_body_past_twenty_parts_is_too_long():
    body = "\n\n".join(f"P {k}." for k in range(20 * 1_500 + 1))
    assert g.drafts(read(mail(payload=part("text/plain", body))), "mail") == g.TOO_LONG


# The cursor


def test_a_cursor_is_compact_json_and_reads_back_as_written():
    cursor = g.Cursor(
        done={g.digest("a"): 1_763_000_000}, parts={g.digest("b"): [1_763_000_001, 2]}
    )
    text = cursor.encode()
    assert text == (
        '{"v":1,"done":{"'
        + g.digest("a")
        + '":1763000000},"parts":{"'
        + g.digest("b")
        + '":[1763000001,2]}}'
    )
    assert g.Cursor.decode(text) == cursor


@pytest.mark.parametrize(
    "stored",
    [
        "not json",
        "[]",
        '{"v":2,"done":{},"parts":{}}',
        '{"v":1,"done":[],"parts":{}}',
        '{"v":1,"done":{"unitm0001":1},"parts":{}}',
        '{"v":1,"done":{"0123456789abcdef":-1},"parts":{}}',
        '{"v":1,"done":{},"parts":{"0123456789abcdef":[1,0]}}',
        '{"v":1,"done":{},"parts":{"0123456789abcdef":[1]}}',
    ],
)
def test_a_cursor_this_source_did_not_write_fails_loudly(stored):
    with pytest.raises(SourceError, match="the stored cursor is not one this Gmail source wrote"):
        g.Cursor.decode(stored)


def test_a_digest_is_let_go_once_older_than_the_listing_and_no_longer_listed():
    after = 1_763_000_000
    old, listed, recent = g.digest("old"), g.digest("listed"), g.digest("recent")
    cursor = g.Cursor(
        done={old: after - 3_600, listed: after - 86_400 * 400, recent: after - 30},
        parts={g.digest("half"): [after - 3_600, 1], g.digest("kept"): [after, 1]},
    )
    kept = cursor.pruned(after, {listed})
    assert kept.done == {listed: after - 86_400 * 400, recent: after - 30}
    assert kept.parts == {g.digest("kept"): [after, 1]}


def test_the_listing_goes_a_day_before_the_later_of_since_and_the_look_back():
    assert g.after(SINCE, NOW, 30) == int((SINCE - DAY).timestamp())
    late = NOW + 40 * DAY
    assert g.after(SINCE, late, 30) == int((late - 31 * DAY).timestamp())
    assert g.listing(LABEL_ID, 1_763_000_000) == {
        "labelIds": LABEL_ID,
        "q": "after:1763000000",
        "maxResults": "500",
    }


# Pulls


@pytest.mark.parametrize("odd", [".", "..", "a/b", "a?b", "%2e%2e"])
def test_a_listed_id_of_another_form_fails_the_pull_before_it_goes_into_a_path(odd):
    """quote(safe="") leaves "." and ".." as they are, and the URL then resolves them: a
    listed ".." made the pull ask for users/me instead of a message."""
    stand = Stand()
    stand.messages = [mail(1), {**mail(2), "id": odd}]
    assert "a message has no usable id" in stand.failed()
    assert stand.calls("get") == []


def test_the_requests_go_to_the_mailbox_with_the_documented_parameters():
    stand = Stand()
    stand.messages = [mail(1)]
    stand.pull()
    paths = [r.url.path for r in stand.requests[1:]]
    assert paths == [
        "/gmail/v1/users/me/labels",
        "/gmail/v1/users/me/messages",
        "/gmail/v1/users/me/messages/unitm0001",
    ]
    (listed,) = stand.calls("list")
    assert dict(listed.url.params) == g.listing(LABEL_ID, int((SINCE - DAY).timestamp()))
    (got,) = stand.calls("get")
    assert dict(got.url.params) == {"format": "full"}
    assert all(r.method == "GET" and r.content == b"" for r in stand.requests[1:])


def test_messages_are_read_in_the_reverse_of_the_listings_order_at_most_50_a_pull():
    """The guide says newest first, so the oldest are read first when that holds; nothing
    depends on it."""
    stand = Stand()
    stand.messages = [mail(n, SINCE + (200 - n) * MINUTE) for n in range(1, 121)]
    first = stand.pull()
    read_ids = [r.url.path.rsplit("/", 1)[1] for r in stand.calls("get")]
    assert read_ids == [f"unitm{n:04d}" for n in range(120, 70, -1)]
    assert refs(first) == [f"gmail:unitm{n:04d}" for n in range(120, 70, -1)]
    assert g.READS == 50 and len(read_ids) == 50


def test_what_is_passed_over_is_read_once_and_kept_in_the_cursor_not_read_again():
    stand = Stand()
    stand.messages = [
        mail(1, SINCE - HOUR),  # before the switch-on
        mail(2, SINCE + HOUR, labels=["DRAFT", LABEL_ID]),
        mail(3, SINCE + 2 * HOUR),
    ]
    first = stand.pull()
    assert refs(first) == ["gmail:unitm0003"] and first.skipped == []
    assert len(stand.calls("get")) == 3
    assert set(g.Cursor.decode(first.cursor).done) == {g.digest(f"unitm{n:04d}") for n in (1, 2, 3)}
    stand.requests.clear()
    second = stand.pull(first.cursor)
    assert second.drafts == [] and stand.calls("get") == []


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (lambda r: error(404, "notFound"), g.GONE),
        (lambda r: httpx.Response(200, content=b'{"x": "' + b"y" * (9 << 20) + b'"}'), g.TOO_LARGE),
    ],
)
def test_a_message_gone_or_too_large_is_skipped_once_with_its_reason(answer, reason):
    stand = Stand()
    stand.messages = [mail(1)]
    stand.answers["get"] = answer
    first = stand.pull()
    assert first.drafts == [] and [(s.ref, s.reason) for s in first.skipped] == [
        ("unitm0001", reason)
    ]
    del stand.answers["get"]
    assert stand.pull(first.cursor).skipped == []


def nested(depth: int) -> str:
    """A part tree `depth` parts deep, as compact JSON text."""
    inner = json.dumps(part("text/plain", "Deep down."))
    return '{"mimeType":"multipart/mixed","parts":[' * (depth - 1) + inner + "]}" * (depth - 1)


def poisoned(n: int, payload: str) -> httpx.Response:
    at = str(round((SINCE + HOUR).timestamp() * 1000))
    body = f'{{"id":"unitm{n:04d}","internalDate":"{at}","labelIds":["{LABEL_ID}"],"payload":'
    return httpx.Response(200, content=(body + payload + "}").encode())


@pytest.mark.parametrize(
    "payload",
    [
        nested(2_000),  # the reviewer's message: the recursive walk raised RecursionError
        '{"parts":[' * 200_000 + "{}" + "]}" * 200_000,  # deeper than JSON is parsed
        nested(g.MAX_DEPTH + 1),
        json.dumps(tree("multipart/mixed", *[part("text/plain", "x")] * g.MAX_MIME_PARTS)),
    ],
)
def test_a_message_too_deep_or_of_too_many_parts_is_skipped_once_and_the_rest_written(payload):
    stand = Stand()
    stand.messages = [mail(1), mail(2), mail(3)]
    stand.answers["get"] = lambda r: (
        poisoned(2, payload)
        if r.url.path.endswith("unitm0002")
        else httpx.Response(
            200, json=next(m for m in stand.messages if r.url.path.endswith(m["id"]))
        )
    )
    first = stand.pull()
    assert refs(first) == ["gmail:unitm0001", "gmail:unitm0003"]
    assert [(s.ref, s.reason) for s in first.skipped] == [("unitm0002", g.TANGLED)]
    stand.requests.clear()
    again = stand.pull(first.cursor)
    assert again.drafts == again.skipped == [] and stand.calls("get") == []


def test_a_tree_at_the_bounds_is_read():
    deepest = g.read_message("unitm0001", poisoned(1, nested(g.MAX_DEPTH)).content)
    assert g.drafts(deepest, "mail")[0].text.endswith("Deep down.\n")
    widest = tree("multipart/mixed", *[part("text/plain", "Wide.")] * (g.MAX_MIME_PARTS - 1))
    assert g.drafts(read(mail(1, payload=widest)), "mail")[0].text.endswith("Wide.\n")


def test_a_message_cairn_fails_on_is_skipped_once_and_the_rest_written():
    """A From header the standard library's address parser recurses on, one level a comment:
    it raised RecursionError, which failed the run and kept the cursor where it was."""
    stand = Stand()
    stand.messages = [mail(1), mail(2, headers={"From": "(a" * 50_000 + ")" * 50_000}), mail(3)]
    first = stand.pull()
    assert refs(first) == ["gmail:unitm0001", "gmail:unitm0003"]
    assert [(s.ref, s.reason) for s in first.skipped] == [("unitm0002", g.UNREADABLE)]
    assert stand.pull(first.cursor).skipped == []


def test_each_skip_reason_says_what_happened_and_that_it_is_not_read_again():
    assert g.GONE == (
        "gone: Gmail no longer had it when this server came to read it (deleted since it was"
        " listed); it is not read again"
    )
    assert g.NO_TEXT == (
        "no text to read: it has no text part of its own, only attachments, which this server does"
        " not read; it is not read again"
    )
    assert g.TOO_LARGE == (
        "too large: Gmail's answer for it is over 8 MB, more than this server reads; it is not"
        " read again"
    )
    assert g.TOO_LONG == (
        "too long: its text would take more than 20 notes of 1,500 passages each, each within"
        " what a page holds; it is not read again"
    )


@pytest.mark.parametrize(
    ("labels", "said"),
    [
        ([{"id": "x1", "name": "Receipts", "type": "user"}], g.NOT_FOUND),
        (
            [
                {"id": "x1", "name": "Cairn", "type": "user"},
                {"id": "x2", "name": "CAIRN", "type": "user"},
            ],
            g.AMBIGUOUS,
        ),
        ([{"id": "SCHEDULED", "name": "cairn", "type": "system"}], g.NOT_YOURS),
    ],
)
def test_a_label_that_cannot_be_read_says_what_to_do_in_gmail(labels, said):
    stand = Stand()
    stand.labels = labels
    assert stand.failed() == said
    assert "label" in said and "Gmail" in said
    assert stand.calls("list") == [] and stand.calls("get") == []


def test_each_label_message_says_what_to_check():
    assert g.NOT_FOUND == (
        "Gmail has no label named as the label setting says in the mailbox read: make that"
        " label in Gmail, or set label to its name exactly as Gmail shows it (a nested label"
        " with its parents in front, parent/child)"
    )
    assert g.AMBIGUOUS == (
        "Gmail has more than one label whose name differs from the label setting only in"
        " case: set label to the name of one of them exactly as Gmail shows it"
    )
    assert g.NOT_YOURS == (
        "the label setting names one of Gmail's own labels, which this server never reads: make a"
        " label of your own in Gmail, put it on the messages to read, and name it in label"
    )


@pytest.mark.parametrize(
    ("reply", "what", "said"),
    [
        (
            Reply(401),
            "labels.list",
            "Gmail refused access (HTTP 401) to labels.list: check that"
            f" the credential in {VAR} carries the scope https://www.googleapis.com/auth/gmail"
            ".readonly (for a service account, in its domain-wide delegation), that the Gmail API"
            " is enabled in its Cloud project, and that no administrator's policy keeps apps from"
            " Gmail",
        ),
        (
            Reply(403, reasons=frozenset({"dailyLimitExceeded"})),
            "messages.get",
            "Gmail answered"
            " messages.get that the Cloud project's daily limit is reached (dailyLimitExceeded), a"
            " cap set on its quota: it is not retried; raise or remove the cap in the Cloud"
            " console, and the next run goes on",
        ),
        (
            Reply(429, limited=True),
            "messages.list",
            "Gmail still answered with a rate limit to"
            " messages.list after 4 retries: the next run goes on",
        ),
        (
            Reply(404),
            "messages.list",
            "Gmail found no mailbox to read (HTTP 404 to"
            " messages.list): check subject, the person whose mail is read",
        ),
        (
            Reply(302),
            "labels.list",
            "Gmail answered labels.list with a redirect (HTTP 302),"
            " which this server does not follow: check base_url",
        ),
        (
            Reply(503),
            "messages.get",
            "Gmail answered HTTP 503 to messages.get, a fault on its"
            " side: the next run tries again",
        ),
        (Reply(400), "messages.list", "Gmail refused the request to messages.list (HTTP 400)"),
    ],
)
def test_each_refusal_says_what_happened_and_what_to_check(reply, what, said):
    assert g.refusal(reply, what, VAR) == said


def test_a_rate_limit_on_a_read_after_some_were_read_returns_them():
    stand = Stand()
    stand.messages = [mail(n, SINCE + n * MINUTE) for n in range(1, 4)]
    stand.messages.reverse()  # newest first, as the guide says
    calls = []

    def limited_after_one(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json=mail(1, SINCE + MINUTE))
        return error(429, "rateLimitExceeded")

    stand.answers["get"] = limited_after_one
    first = stand.pull()
    assert refs(first) == ["gmail:unitm0001"] and len(stand.slept) == 4
    assert set(g.Cursor.decode(first.cursor).done) == {g.digest("unitm0001")}


def test_a_deadline_before_it_signs_in_fails_the_pull_and_sends_nothing():
    stand = Stand()
    said = stand.failed(deadline=NOW)
    assert said.startswith("no time was left to sign in to Google") and stand.requests == []


def test_a_deadline_before_the_first_read_fails_the_pull():
    stand = Stand()
    stand.messages = [mail(1)]
    stand.tick = MINUTE
    said = stand.failed(deadline=NOW + 3 * MINUTE)
    assert said == (
        "no message could be read within one pull's bounds (its deadline), so the cursor"
        " cannot move: the next run tries again"
    )


def test_a_listing_past_its_pages_fails_the_pull():
    stand = Stand()
    stand.answers["list"] = lambda r: httpx.Response(
        200, json={"messages": [], "nextPageToken": "more"}
    )
    assert stand.failed() == f"Gmail listed more than {g.MAX_PAGES} pages of messages in one pull"
    assert len(stand.calls("list")) == g.MAX_PAGES


def test_a_listing_of_more_messages_than_its_pages_hold_fails_the_pull(monkeypatch):
    """Google chooses how many ids a page holds, so a listing bounded by its pages alone kept
    whatever one page brought. It now ends at MAX_PAGES full pages of ids too."""
    monkeypatch.setattr(g, "MAX_PAGES", 1)
    monkeypatch.setattr(g, "PAGE", 2)
    stand = Stand()  # one page of three
    stand.messages = [mail(n) for n in (1, 2, 3)]
    assert stand.failed() == "Gmail listed more than 2 messages in one pull"
    assert stand.calls("get") == []
    stand.messages.pop()
    assert len(stand.pull().drafts) == 2


def test_a_pull_has_room_for_its_pages_and_reads_and_their_retries():
    assert g.BUDGET == (1 + g.MAX_PAGES + g.READS + g.LOOKS + g.RECHECKS) * 5


def test_every_draft_carries_the_cursor_that_goes_on_right_after_it():
    stand = Stand()
    long = "\n\n".join(f"Paragraph {k}." for k in range(1_600))
    stand.messages = [
        mail(1, SINCE + HOUR),
        mail(2, SINCE + 2 * HOUR, payload=part("text/plain", long)),
        mail(3, SINCE + 3 * HOUR),
    ]
    whole = stand.pull()
    assert refs(whole) == [
        "gmail:unitm0001",
        "gmail:unitm0002",
        "gmail:unitm0002/2",
        "gmail:unitm0003",
    ]
    for index, draft in enumerate(whole.drafts):
        assert refs(stand.pull(draft.resume)) == refs(whole)[index + 1 :]


def test_the_token_and_the_credential_reach_no_log_line(caplog):
    from cairn.sources import source_client

    stand = Stand()
    stand.messages = [mail(1)]
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.DEBUG)
    try:
        with caplog.at_level(logging.DEBUG):
            source = g.open_source(
                "mail", g.check_config(settings(base_url=BASE)),
                http=source_client(transport=httpx.MockTransport(stand.handler)),
                env={VAR: CREDENTIAL}, clock=stand.clock, sleep=stand.sleep, since=SINCE,
            )  # fmt: skip
            got = asyncio.run(source.pull(None))
    finally:
        root.setLevel(level)
    assert refs(got) == ["gmail:unitm0001"]
    assert holds_secret(f"x {TOKEN} y", tuple(source.secrets))
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (TOKEN, SECRET, REFRESH):
        assert secret not in logged
    assert "HTTP Request" not in logged
    for request in stand.requests:
        assert TOKEN not in unquote(str(request.url))
