"""The guides say what cairn does today where an outside review found them wrong or silent:
the evidence of a version and the version a hit names (decisions 154 and 156), what is kept
as digests and what sealed (decision 148), how many versions `serve` keeps (Q1), what an
export leaves out and what an import does not bring back, what a copy of the database holds,
and what a source reads and how far the check of two sources goes (decisions 139 and 155).

The privacy, connect and sources guides are read as a signed-in person is served them, with
no brand and under a made-up one; the README and the Granola notes as files."""

import asyncio
import html
import re
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SESSION_COOKIE, SignIn, create_app
from cairn.store import Store
from cairn.web.auth import BRANDED_SESSION_COOKIE, branded_cookie

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://memory.example.test"
ANCHOR = "what-a-source-reads"


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def flat(page: str) -> str:
    """A served page's words: tags dropped, entities read, whitespace as single spaces."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", page)).split())


@pytest.fixture
def sign_in():
    offline = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    yield SignIn(providers=(GitHub("client-id", "made-up"),), http=offline)
    asyncio.run(offline.aclose())


@pytest.fixture(params=[None, "Juniper"], ids=["no brand", "a brand"])
def served(request, tmp_path, sign_in):
    """Each guide as a signed-in person is served it: {name: (raw html, words)}."""
    brand = request.param
    with closing(Store(tmp_path / "cairn.db")) as store:
        app = create_app(store, brand_name=brand, public_url=BASE, sign_in=sign_in)
        with TestClient(app, base_url=BASE) as client:
            signed = store.sign_in(
                "github",
                "made-up-subject",
                "reader@example.test",
                "Reader",
                issuer="https://id.example.test",
                join="default",
            )
            view = store.for_account(signed.account_id, signed.user.id)
            cookie = branded_cookie(BRANDED_SESSION_COOKIE, True) if brand else SESSION_COOKIE
            client.cookies.set(cookie, view.create_session())
            pages = {}
            for name in ("privacy", "connect", "sources"):
                response = client.get(f"/docs/{name}")
                assert response.status_code == 200, name
                pages[name] = (response.text, flat(response.text))
    return brand or "cairn", pages


def holds(words: str, *phrases: str) -> None:
    missing = [phrase for phrase in phrases if phrase not in words]
    assert missing == []


# Item 1: the evidence of a version, and the version a hit names (decisions 154, 156)


def test_the_connect_guide_says_evidence_takes_a_version_and_a_hit_names_one(served):
    _, pages = served
    raw, words = pages["connect"]
    assert "{{name}}" not in raw
    holds(
        words,
        "Evidence names its version.",
        "?version=N over REST, version over MCP",
        "names the version it answered for",
        "without one, it answers for the latest",
        "A version the page no longer keeps is refused",
        "a claim that version does not hold is not found",
        "Each hit of recall and GET /api/search has version",
        "project, page, claim id and version",
        "for as long as the page keeps that version",
        "A citation shows what a note said, not which of two notes that disagree is right.",
    )


# Items 3 to 7: the privacy guide


def test_the_privacy_guide_says_what_is_kept_as_digests_and_what_sealed(served):
    product, pages = served
    raw, words = pages["privacy"]
    assert "{{name}}" not in raw
    holds(
        words,
        f"Credentials {product} issues, as SHA-256 digests only:",
        f"A usable key, session or token that {product} issued.",
        f"the database cannot be used to sign in to {product}",
        "the only credentials of other services the database can hold",
    )
    # the promise that was too wide: no usable token at all
    assert "A usable key, session or token. Only digests are kept" not in words


def test_the_privacy_guide_says_how_many_versions_serve_keeps(served):
    _, pages = served
    _, words = pages["privacy"]
    holds(
        words,
        "serve keeps the newest 50 versions of a page unless --keep-versions N",
        "or --keep-versions all (every one) says otherwise",
        "A program that uses the library without serve keeps every version",
    )


def test_the_privacy_guide_says_what_an_export_leaves_out_and_an_import_does_not_bring(
    served,
):
    _, pages = served
    _, words = pages["privacy"]
    holds(
        words,
        "the Google accounts connected in the browser",
        "the revocations waiting to be sent",
        "who each project is shared with",
        "the items each source wrote",
        "the digests of forgotten notes",
        "A forgotten note is not in it, nor the page versions erased with it.",
        "Every project an import brings is private.",
        "does not refuse the text of a note forgotten before the export",
        "so it can bring a forgotten item back",
    )


def test_the_privacy_guide_says_what_a_copy_of_the_database_holds(served):
    _, pages = served
    _, words = pages["privacy"]
    holds(
        words,
        "A copy of the database file, and the replica in the operator's bucket, hold all of it",
        "the sealed Google tokens",
        "the key the Google tokens are sealed with",
        "holds it until that copy is deleted",
    )


def test_the_privacy_guide_says_what_a_source_reads_and_where_the_check_stops(served):
    _, pages = served
    raw, words = pages["privacy"]
    assert f'id="{ANCHOR}"' in raw
    holds(
        words,
        "that service's one read-only permission",
        "Google still granted the whole of it",
        "Connecting an account reads nothing and makes no source.",
        "A source made from an account starts off",
        "Preview stores nothing",
        "A Langfuse source added on the command line is on as soon as it is added.",
        "Everyone who reads the project a source writes into reads what it brings.",
        "by no other member until its rule names them",
        "compares the sources made from one registered account",
        "the labels of two of them that are on is held, per message",
        "one Google or Granola account registered twice is two accounts to it",
        "a source added on the command line is compared with nothing",
        "a person's other address naming their primary calendar is not ruled out",
        "two ids that differ beyond letter case, neither of them primary, are accepted",
        "a run of either reads and writes nothing",
    )


# Item 8: the sources guide's "Whose account." paragraph


def test_the_sources_guide_says_what_is_compared_and_points_to_the_bounds(served):
    _, pages = served
    raw, words = pages["sources"]
    assert "{{name}}" not in raw
    assert f'href="/docs/privacy#{ANCHOR}"' in raw
    holds(words, "Sources made from one registered account are compared")
    assert "Two sources of one account never write one item" not in words


def test_the_sources_guide_stays_under_its_size():
    # The bound that is real is docs_publisher.MAX_FILE_BYTES (128 KiB): test_docs_helper shows a
    # sources guide past 70,000 bytes published and searched. This lower one only makes growth
    # a choice someone states; it was raised from 70,000 for the sentence on changing the key.
    assert len(text("src/cairn/guides/sources.md").encode()) < 72_000


# Item 2: the README


def test_the_readme_says_what_is_digested_and_what_sealed():
    readme = text("README.md")
    (row,) = [line for line in readme.splitlines() if line.startswith("| Store |")]
    assert "keys, sessions and tokens stored only as digests" not in row
    holds(
        row,
        "keys, browser sessions, agents' tokens and invitation links cairn issues, stored only",
        "with `--google-connect`, a Google refresh token stored sealed",
    )


def test_the_readme_says_what_cairn_does_not_promise_before_the_licence():
    readme = text("README.md")
    start = readme.index("\n## What it does not promise\n")
    assert start < readme.index("\n## Licence\n")
    section = " ".join(readme[start : readme.index("\n## Licence\n")].split())
    holds(
        section,
        "A page keeps its newest 50 versions under `serve`",
        "`--keep-versions N` keeps N",
        "for as long as the page keeps that version",
        "A citation shows what a note said, not which of two notes that disagree is right.",
        f"docs/guides/privacy.md#{ANCHOR}",
    )


# Item 9: the Granola notes' error body


def test_the_granola_notes_error_body_is_ours():
    notes = text("docs/granola-api.md")
    start = notes.index("### E.1 Error body")
    section = notes[start : notes.index("### E.2", start)]
    holds(section, "the values are ours, made up", '"requestId": "example-request-0001"')
    for theirs in ("Invalid query parameters", "0c6f3f0e-7f0f-4c59-9d0b-2f1f6f1a3b7e"):
        assert theirs not in section
    for field in ('"code"', '"message"', '"details"', '"field"', '"issue"', '"timestamp"'):
        assert field in section
