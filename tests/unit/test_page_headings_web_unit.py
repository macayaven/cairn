"""A claim is never shown under a heading that is not its own (fix H), on the web page
(`page_view`, page.html), and the web page agrees with the Markdown body. Made-up notes."""

import html
import re

import pytest
from starlette.testclient import TestClient

from cairn.compiler import claim_id, compile_note
from cairn.server import create_app
from cairn.store import Claim, Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
CLAIMS = re.compile(r'<section aria-label="Claims">(.*?)</section>', re.S)
PARTS = re.compile(
    r'<h([1-6]) class="section">(.*?)</h\1>|<article class="claim [^"]*" id="([^"]+)">', re.S
)
MD_HEADING = re.compile(r"(#{2,6}) (.*)")

DEPRECATED = "## Deprecated — DO NOT USE\n\nNever run legacy-reset.sh on production hosts.\n"
VERIFY = "Always run verify-backup.sh before release.\n"
PORTS = (
    "## Production\n\n### Ports\n\nPort 8080 serves TLS.\n\n"
    "## Staging\n\n### Ports\n\nPort 9090 serves staging.\n"
)


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    project = store.create_project("alpha-ops")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        assert client.post("/login", data={"key": key}, headers=SAME_ORIGIN).status_code < 400
        yield store, project, client
    store.close()


def compiled(store, project, text, **fields):
    note, _ = store.add_note(project.id, text, **fields)
    result = compile_note(store, note)
    assert result.status == "saved", result
    return store.get_page(project.id, result.slug)


def web_outline(client, page) -> list[tuple]:
    """The claims section of the page's web view: ("h", level, text) per heading and
    ("c", claim text) per claim, in order."""
    text = client.get(f"/b/alpha-ops/p/{page.slug}").text
    claims = CLAIMS.search(text)
    assert claims, "no claims section"
    by_id = {claim.id: claim.text for claim in page.claims}
    out = []
    for level, heading, cited in PARTS.findall(claims.group(1)):
        out.append(("c", by_id[cited]) if cited else ("h", int(level), html.unescape(heading)))
    return out


def body_outline(page) -> list[tuple]:
    """The same, read from the page's Markdown body."""
    out = []
    for block in page.body.rstrip("\n").split("\n\n")[1:]:
        heading = MD_HEADING.fullmatch(block)
        out.append(("h", len(heading[1]), heading[2]) if heading else ("c", block))
    return out


def under(outline) -> dict[str, tuple[str, ...]]:
    """Each claim's text: the headings it shows under, as a reader has them."""
    open_: list[tuple[int, str]] = []
    over = {}
    for part in outline:
        if part[0] == "h":
            open_ = [*(h for h in open_ if h[0] < part[1]), (part[1], part[2])]
        else:
            over[part[1]] = tuple(text for _, text in open_)
    return over


# 1, on the web: the reviewer's two examples


def test_the_web_page_shows_no_claim_under_an_earlier_notes_section(env):
    store, project, client = env
    compiled(store, project, DEPRECATED, title="Deploy")
    page = compiled(store, project, VERIFY, title="Release verify", topic="Deploy")
    over = under(web_outline(client, page))
    assert over["Always run verify-backup.sh before release."] == ()
    assert over["Never run legacy-reset.sh on production hosts."] == ("Deprecated — DO NOT USE",)


def test_the_web_page_keeps_nested_sections_parents(env):
    store, project, client = env
    page = compiled(store, project, PORTS, title="Ports guide")
    assert web_outline(client, page) == [
        ("h", 2, "Production"),
        ("h", 3, "Ports"),
        ("c", "Port 8080 serves TLS."),
        ("h", 2, "Staging"),
        ("h", 3, "Ports"),
        ("c", "Port 9090 serves staging."),
    ]


# 4. Real headings under the one h1, no level skipped going down


def test_headings_are_real_elements_under_one_h1_and_skip_no_level(env):
    store, project, client = env
    text = "## A\n\n### B\n\n#### C\n\nDeep words.\n\n## D\n\nFlat words.\n"
    page = compiled(store, project, text, title="Levels")
    full = client.get(f"/b/alpha-ops/p/{page.slug}").text
    assert len(re.findall(r"<h1[ >]", full)) == 1
    levels = [part[1] for part in web_outline(client, page) if part[0] == "h"]
    assert levels == [2, 3, 4, 2]
    assert all(b <= a + 1 for a, b in zip([1, *levels], levels, strict=False))


# 5. The Markdown body and the web page agree


@pytest.mark.parametrize(
    "notes",
    [
        [(DEPRECATED, {"title": "Deploy"}), (VERIFY, {"title": "Verify", "topic": "Deploy"})],
        [(PORTS, {"title": "Ports guide"})],
        [
            ("# Mixed\n\nIntro.\n\n## A\n\n### B\n\nUnder B.\n\n## C\n\nUnder C.\n", {}),
            ("# Mixed\n\n## A\n\nUnder A.\n\nLoose? No, under A too.\n", {}),
            ("Loose words.\n", {"title": "Loose", "topic": "Mixed"}),
        ],
    ],
)
def test_the_markdown_body_and_the_web_page_agree(env, notes):
    store, project, client = env
    for text, fields in notes:
        page = compiled(store, project, text, **fields)
    assert web_outline(client, page) == body_outline(page)


# 2 and 7. Claims keep their stored numbers; a body stored before shows right on the web


def test_a_page_stored_with_an_old_body_shows_right_on_the_web_and_keeps_its_numbers(env):
    store, project, client = env
    first = compiled(store, project, DEPRECATED, title="Deploy")
    note, _ = store.add_note(project.id, VERIFY, title="Verify", topic="Deploy")
    span = next(s for s in store.note_spans(note.id) if s.kind == "paragraph")
    claims = (*first.claims, Claim(id=claim_id(span.id), text=span.text, span_ids=(span.id,)))
    old = "# Deploy\n\n## Deprecated — DO NOT USE\n\n" + "\n\n".join(c.text for c in claims) + "\n"
    store.save_page(project.id, "deploy", "Deploy", old, claims)
    page = store.get_page(project.id, "deploy")
    assert page.body == old  # what is stored stays as it was saved
    over = under(web_outline(client, page))
    assert over["Always run verify-backup.sh before release."] == ()
    full = client.get("/b/alpha-ops/p/deploy").text
    for n, claim in enumerate(page.claims, 1):  # "Cited by claim n": n is the stored place
        assert f'<a href="#{claim.id}">claim {n}</a>' in full


# 9. No new statement to the database


def statements_to_show(store, client, slug) -> int:
    seen: list[str] = []
    store._con.set_trace_callback(seen.append)
    try:
        assert client.get(f"/b/alpha-ops/p/{slug}").status_code == 200
    finally:
        store._con.set_trace_callback(None)
    return len(seen)


def test_showing_a_nested_page_costs_what_a_flat_one_does(env):
    store, project, client = env
    compiled(store, project, "## A\n\nOne.\n\n## B\n\nTwo.\n", title="Flat")
    compiled(store, project, "## A\n\n### B\n\nOne.\n\nTwo.\n", title="Deep")
    compiled(store, project, "One more.\n", title="Loose", topic="Deep")
    compiled(store, project, "One more too.\n", title="Loose too", topic="Flat")
    assert statements_to_show(store, client, "deep") == statements_to_show(store, client, "flat")


def test_six_nested_headings_show_their_whole_ancestry_as_the_body_does(env):
    """Should-fix 9 on the web page: past level 6 the rest of the path is one breadcrumb
    heading, through section_headings, which the body uses too."""
    store, project, client = env
    text = "".join(f"{'#' * level} H{level}\n\n" for level in range(1, 7)) + "Deep words.\n"
    page = compiled(store, project, text, title="Other", topic="Other")
    web = web_outline(client, page)
    assert web == body_outline(page)
    assert web[-2:] == [("h", 6, "H5 > H6"), ("c", "Deep words.")]
