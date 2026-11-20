"""Synthetic message-rendering contract; no Slack connection or credentials."""

import copy
import json
import re

import pytest

from cairn.slack import blocks


def hit(**changes):
    return dict(
        project="lab",
        page="overview",
        title="Overview",
        claim_id="c-1",
        text="A claim",
        version=7,
        **changes,
    )


def section_text(payload):
    return "\n".join(block["text"]["text"] for block in payload["blocks"])


def assert_valid(payload):
    assert set(payload) == {"text", "blocks"}
    assert payload["text"].strip()
    assert 1 <= len(payload["blocks"]) <= 50
    for block in payload["blocks"]:
        assert block["type"] == "section"
        obj = block["text"]
        assert 1 <= len(obj["text"]) <= 3000
        if obj["type"] == "mrkdwn":
            assert obj["verbatim"] is True


@pytest.mark.parametrize(
    "raw, expected",
    [("", ""), ("<&>", "&lt;&amp;&gt;"), ("😀 café e\u0301 | * `", "😀 café e\u0301 | * `")],
)
def test_escape_encodes_slack_control_characters_only(raw, expected):
    assert blocks.escape(raw) == expected


def test_found_links_titles_to_encoded_claim_and_shows_project_version_and_claim():
    hits = [
        dict(
            project="a/b +é",
            page="p?# 雪",
            title="Title & <tag>",
            claim_id="id/# ?é",
            text="Evidence & <!here>",
            version=7,
        )
    ]
    before = copy.deepcopy(hits)
    payload = blocks.found(hits, query="query", base_url="https://example.test/root/")
    rendered = section_text(payload)
    assert (
        "<https://example.test/root/b/a%2Fb%20%2B%C3%A9/p/p%3F%23%20%E9%9B%AA"
        "#id%2F%23%20%3F%C3%A9|Title &amp; &lt;tag&gt;>" in rendered
    )
    assert "a/b +é" in rendered and "7" in rendered
    assert "Evidence &amp; &lt;!here&gt;" in rendered
    assert "Evidence" in payload["text"] and "Title" in payload["text"]
    assert hits == before
    assert_valid(payload)


def test_found_shows_only_first_five_without_leaking_later_hits():
    hits = [hit() for _ in range(5)] + [
        dict(
            project="secret-sixth",
            page="six",
            title="Hidden",
            claim_id="6",
            text="Hidden claim",
            version=6,
        )
    ]
    payload = blocks.found(hits, query="q", base_url="https://example.test")
    assert section_text(payload).count("#c-1|Overview>") == 5
    assert "Hidden" not in json.dumps(payload)
    assert "secret-sixth" not in json.dumps(payload)
    assert_valid(payload)


def test_empty_found_returns_meaningful_no_results_message():
    assert blocks.found(
        [], query="missing", base_url="https://example.test"
    ) == blocks.nothing_found("missing")
    payload = blocks.nothing_found("<!here>")
    assert "found" in payload["text"].lower()
    assert "&lt;!here&gt;" in payload["text"]
    assert "<!here>" not in json.dumps(payload)
    assert_valid(payload)


def test_found_cannot_inject_another_link_through_title_or_url_delimiters():
    payload = blocks.found(
        [
            dict(
                project="p|<@U1>",
                page="p",
                title="title|> <!here> <https://evil.test|x>",
                claim_id="c|>",
                text="@here https://evil.test <@U1>",
                version="<&>",
            )
        ],
        query="<!here>",
        base_url="https://example.test",
    )
    rendered = section_text(payload)
    assert len(re.findall(r"<https://", rendered)) == 1
    assert "<@U1>" not in rendered and "<!here>" not in rendered
    assert "%7C%3E" in rendered and "&lt;&amp;&gt;" in rendered
    assert_valid(payload)


@pytest.mark.parametrize("already, word", [(False, "Filed"), (True, "Already")])
def test_filed_links_page_and_distinguishes_duplicate(already, word):
    payload = blocks.filed(
        title="Note & <x>",
        page_url="https://example.test/p?a=1&b=2",
        project="lab",
        already=already,
    )
    rendered = section_text(payload)
    assert word.lower() in payload["text"].lower()
    # The link's label is the reply's own words, and the title plain text after it (the
    # tester's second attack, finding 3: a label is mrkdwn, and styled the title).
    assert "<https://example.test/p?a=1&amp;b=2|Open its page>" in rendered
    assert rendered.endswith("\nNote &amp; &lt;x&gt;")
    assert "lab" in rendered
    assert_valid(payload)


@pytest.mark.parametrize(
    "where",
    [
        {"page_url": "https://example.test/p"},
        {"page_url": "https://example.test/p", "already": True},
        {"page_url": None, "project_url": "https://example.test/b/lab"},
        {"page_url": None, "waiting": True, "already": True},
        {"page_url": "https://example.test/p", "waiting": True},
    ],
    ids=["filed", "already", "no page", "already, waiting", "waiting"],
)
def test_a_filed_reply_names_the_title_in_plain_text(where):
    """The tester's second attack, finding 3: the title is the message's words, or a name's,
    and a mrkdwn section styles `*draft*` bold where Slack displayed it as characters. It
    is a plain_text text object of its own (Slack's means for text it does not format,
    "Disabling formatting" on its page on message text), escaped as every text object is;
    the reply's own words stay mrkdwn, and none of them is the title."""
    title = "*draft* _v2_ ~old~ <!here> & @*Cedar*"
    payload = blocks.filed(title=title, project="lab", **where)
    *words, named = payload["blocks"]
    assert named["text"] == {"type": "plain_text", "text": blocks.escape(title)}
    for block in words:
        assert block["text"]["type"] == "mrkdwn" and "draft" not in block["text"]["text"]
    assert_valid(payload)


def test_waiting_note_says_stored_and_links_its_project_until_its_page_appears():
    payload = blocks.filed(
        title="Note & <x>", page_url="https://example.test/p", project="lab", waiting=True,
        project_url="https://example.test/b/lab",
    )  # fmt: skip
    for text in (payload["text"], section_text(payload)):
        assert "stored" in text.lower() and "appear shortly" in text.lower()
        assert "Filed" not in text
        assert "—" not in text and ";" not in re.sub(r"&(?:amp|lt|gt);", "", text)
        assert ": " not in text
    assert "<https://example.test/b/lab|lab>" in section_text(payload)
    assert "Note &amp; &lt;x&gt;" in section_text(payload) and "example.test/p" not in str(payload)
    assert "lab" in payload["text"]
    assert_valid(payload)


def test_unlinked_confirms_the_link_is_undone_and_names_the_installs_own_command():
    payload = blocks.unlinked("/memory<&>")
    for text in (payload["text"], section_text(payload)):
        assert "You are unlinked. The bot no longer acts as you here." in text
        assert "`/memory&lt;&amp;&gt; link`" in text
        assert "—" not in text and ";" not in re.sub(r"&(?:amp|lt|gt);", "", text)
        assert ": " not in text
    assert_valid(payload)


def test_link_prompt_explains_expiring_link_and_keeps_url_usable():
    payload = blocks.link_prompt("https://example.test/link?a=1&b=2")
    assert "https://example.test/link?a=1&amp;b=2" in section_text(payload)
    assert "10 minutes" in payload["text"]
    assert "once" in payload["text"].lower() or "one use" in payload["text"].lower()
    assert_valid(payload)


def test_linked_and_refused_explain_identity_and_reason_safely():
    for payload, text in [
        (blocks.linked("Carlos <@U1>"), "Carlos"),
        (blocks.refused("guard: <bad> & full"), "guard:"),
    ]:
        assert text in payload["text"]
        assert "<@U1>" not in json.dumps(payload)
        assert "<bad>" not in json.dumps(payload)
        assert_valid(payload)


@pytest.mark.parametrize("project", [None, "chosen & <p>"])
def test_project_is_shows_selection_and_writable_choices(project):
    payload = blocks.project_is(project, writable=["alpha", "beta <x>"])
    rendered = section_text(payload)
    assert "alpha" in rendered and "beta &lt;x&gt;" in rendered
    assert ("chosen &amp; &lt;p&gt;" in rendered) if project else ("no project" in rendered.lower())
    assert_valid(payload)


def test_project_is_empty_permissions_does_not_offer_an_unavailable_project():
    payload = blocks.project_is(None, writable=[])
    assert "no writable projects" in payload["text"].lower()
    assert_valid(payload)


def test_help_uses_custom_command_and_explains_dm_and_shortcut():
    payload = blocks.help_text("/memory<&>")
    rendered = section_text(payload)
    for action in ["link", "unlink", "find", "note", "project", "help"]:
        assert f"/memory&lt;&amp;&gt; {action}" in rendered
    assert "note:" in rendered and "File in cairn" in rendered
    assert "direct message" in rendered.lower()
    assert_valid(payload)


@pytest.mark.parametrize("value", ["", "😀e\u0301<&>|`*" * 2000])
def test_all_helpers_keep_even_escaped_long_inputs_inside_slack_limits(value):
    payloads = [
        blocks.found(
            [
                dict(
                    project=value,
                    page=value,
                    title=value,
                    claim_id=value,
                    text=value,
                    version=value,
                )
            ],
            query=value,
            base_url="https://example.test",
        ),
        blocks.nothing_found(value),
        blocks.filed(title=value, page_url="https://example.test/" + value, project=value),
        blocks.refused(value),
        blocks.link_prompt("https://example.test/" + value),
        blocks.linked(value),
        blocks.unlinked(value),
        blocks.filed(
            title=value, page_url="https://example.test/" + value, project=value, waiting=True
        ),
        blocks.project_is(value, writable=[value] * 100),
        blocks.help_text(value),
    ]
    for payload in payloads:
        assert_valid(payload)
        # Escaping must never be cut through an entity.
        for block in payload["blocks"]:
            assert not re.search(r"&(a(mp?)?|l(t?)?|g(t?)?)…?$", block["text"]["text"])


@pytest.mark.parametrize(
    "url",
    [
        "mailto:review@example.test",
        "https://example.test/\n<!here>",
        "https://:443/page",
        "https://example.test:wrong/page",
        "https://example.test:65536/page",
        "https://@example.test/page",
    ],
)
def test_unusable_urls_do_not_become_active_slack_links(url):
    for payload in [
        blocks.link_prompt(url),
        blocks.filed(title="Title", page_url=url, project="lab"),
    ]:
        assert "<" not in section_text(payload)
        assert_valid(payload)


def test_help_and_search_fallback_use_clear_lines_without_dashes_as_separators():
    help_reply = blocks.help_text("/cairn")
    result = blocks.found(
        [
            {
                "project": "lab",
                "page": "sample",
                "claim_id": "claim-1",
                "title": "A claim",
                "text": "Sample",
                "version": 1,
            }
        ],
        query="a claim",
        base_url="https://example.test",
    )
    assert "—" not in help_reply["text"]
    assert "—" not in section_text(help_reply)
    assert "—" not in result["text"]
