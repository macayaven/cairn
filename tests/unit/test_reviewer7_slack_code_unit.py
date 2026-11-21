"""File Slack text, then read the note, its stored claims and the served claim HTML."""

import re

import pytest
import test_slack_shortcuts_unit as shortcuts
from starlette.testclient import TestClient
from test_design_system_unit import tree
from test_reviewer6_slack_code_unit import AUTHOR, READER
from test_slack_commands_unit import ADA_SLACK, BASE

from cairn.server import create_app
from cairn.web.markdown import render
from cookie_names import session_cookie

slack_bench = shortcuts.bench
URL = "https://docs.example.test/manual"
MANUAL = f"[manual]({URL})"
FENCE = "```"


def file_message(bench, text, *, rich=None):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    message = {"type": "message", "user": shortcuts.CEDAR, "ts": "1900000000.000100", "text": text}
    if rich is not None:
        message["blocks"] = [{"type": "rich_text", "elements": rich}]
    assert shortcuts.handle(bench, message).status_code == 200
    ((note_id, project, note, via),) = bench.notes()
    assert (project, via) == (bench.library.id, "slack")
    assert {s.heading_path for s in bench.owner.note_spans(note_id)} == {(AUTHOR,)}
    (listed,) = bench.owner.list_pages(project)
    page = bench.owner.get_page(project, listed.slug)

    session = bench.store.sign_in(
        "sso",
        "sub-ada",
        "ada@example.test",
        "Ada",
        issuer="https://sso.example.test",
        join="personal",
    )
    assert session.account_id == bench.account
    with TestClient(
        create_app(bench.store, public_url=BASE, clock=bench.clock), base_url=BASE
    ) as client:
        client.cookies.set(session_cookie("https"), session.token)
        response = client.get(f"/b/library/p/{page.slug}")
    assert response.status_code == 200
    articles = tree(response.text).all("article", "claim")
    assert [a.attrs["id"] for a in articles] == [c.id for c in page.claims]
    web = []
    for article in articles:
        (contents,) = article.all(cls="claim-text")
        web.append(contents)
    return note, page, web


def assert_literal(nodes, text):
    for node in nodes:
        assert not [link for link in node.all("a") if link.attrs.get("href") == URL]
        assert text in node.text()


def test_literal_documentation_link_in_slack_code_stays_literal_in_each_claim(slack_bench):
    message = f"1.\n\t{FENCE}\n\n   {MANUAL}\n   {FENCE}"
    note, page, web = file_message(slack_bench, message)
    (claim,) = [claim for claim in page.claims if URL in claim.text]
    (shown,) = [node for node in web if "manual" in node.text()]
    readers = {
        "note": tree(str(render(note))),
        "stored body": tree(str(render(page.body))),
        "claim alone": tree(str(render(claim.text))),
        "served claim": shown,
    }
    linked = [
        name
        for name, node in readers.items()
        if any(a.attrs.get("href") == URL for a in node.all("a"))
    ]
    assert linked == []
    assert_literal(readers.values(), MANUAL)


def test_three_field_row_keeps_its_final_tab_in_note_claim_and_web(slack_bench):
    row = "    north\tblue\t"
    note, page, web = file_message(slack_bench, "Readings.\n\n    unit\tcolour\tcomment\n" + row)
    (claim,) = [c for c in page.claims if "north" in c.text]
    (code,) = [c.text() for node in web for c in node.all("code") if "north" in c.text()]
    rows = [
        next(line for line in text.splitlines() if "north" in line)
        for text in (note, claim.text, code)
    ]
    assert rows == [row, row, row.removeprefix("    ")]
    assert all(line.split("\t")[-1] == "" and len(line.split("\t")) == 3 for line in rows)


def section(text):
    return {"type": "rich_text_section", "elements": [{"type": "text", "text": text}]}


def assert_fence(markdown, token, expected):
    assert token.content == expected
    lines = markdown.split("\n")
    start, end = token.map
    # A quote marker belongs to the container. Everything else on each delimiter line is a fence.
    delimiter = r"(?:[ \t]*>[ \t]?)*[ \t]*(`{3,}|~{3,})[ \t]*"
    first = re.fullmatch(delimiter, lines[start])
    last = re.fullmatch(delimiter, lines[end - 1])
    assert first and last
    mark = first[1][0]
    assert first[1] == token.markup and last[1] == mark * len(last[1])
    runs = re.findall(re.escape(mark) + "+", expected)
    assert len(last[1]) >= len(first[1]) > max(map(len, runs), default=0)


@pytest.mark.parametrize(
    "shape",
    [
        "three",
        "four",
        "five",
        "empty",
        "one-line-with-prose",
        "quoted",
        "after-list",
        "end-of-message",
    ],
)
def test_both_readers_keep_a_complete_slack_block_as_one_fence(slack_bench, shape):
    literal = MANUAL + "  \t"
    intro = "Field log.\n\n"
    rich = None
    if shape in ("three", "four", "five"):
        size = {"three": 3, "four": 4, "five": 5}[shape]
        literal = "literal " + "`" * size + " and " + "~" * size + "\n" + literal
        # Slack's preformatted element identifies exactly which backticks were displayed.
        rich = [
            section(intro),
            {"type": "rich_text_preformatted", "elements": [{"type": "text", "text": literal}]},
        ]
    if shape == "empty":
        literal = ""
    block = FENCE + "\n" + (literal + "\n" if literal else "") + FENCE
    if shape == "one-line-with-prose":
        message = intro + f"Before {FENCE}{literal}{FENCE} after."
    elif shape == "quoted":
        message = intro + "\n".join("&gt; " + line for line in block.splitlines())
    elif shape == "after-list":
        message = intro + "- Review next.\n\n" + block
    else:
        message = intro + block
    if shape == "empty":
        message += "\nDone."

    note, page, web = file_message(slack_bench, message, rich=rich)
    expected = literal + "\n" if literal else ""
    for markdown in (note, page.body):
        fences = [t for t in READER.parse(markdown) if t.type == "fence"]
        assert len(fences) == 1, f"Expected one fenced code block, found {len(fences)}"
        assert_fence(markdown, fences[0], expected)
    (standalone,) = [
        (c.text, t) for c in page.claims for t in READER.parse(c.text) if t.type == "fence"
    ]
    assert_fence(*standalone, expected)
    (pre,) = [pre for node in web for pre in node.all("pre")]
    (code,) = pre.all("code")
    assert code.text() == expected
    if shape == "one-line-with-prose":
        visible = "\n".join(node.text() for node in web)
        assert visible.index("Before") < visible.index(MANUAL) < visible.index("after.")
        assert "Before" not in code.text() and "after." not in code.text()


def test_inline_code_with_a_literal_backtick_never_turns_its_text_into_markup(slack_bench):
    literal = "row`id " + MANUAL + " *raw*"
    rich = [
        section("Field log.\n\n"),
        {
            "type": "rich_text_section",
            "elements": [
                {"type": "text", "text": "Use "},
                {"type": "text", "text": literal, "style": {"code": True}},
                {"type": "text", "text": " today."},
            ],
        },
    ]
    note, page, web = file_message(slack_bench, f"Field log.\n\nUse `{literal}` today.", rich=rich)
    (claim,) = [c for c in page.claims if URL in c.text]
    (served,) = [node for node in web if "manual" in node.text()]
    nodes = [tree(str(render(m))) for m in (note, page.body, claim.text)] + [served]
    assert_literal(nodes, literal)
    assert not any(node.all(tag) for node in nodes for tag in ("strong", "em", "img"))
