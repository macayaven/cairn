"""A claim is never shown under a heading that is not its own (fix H), in the Markdown body
(`render_body`, stored as `page.body`). Store-level tests compile made-up notes; the rest
call render_body on made-up spans. The web page's half is test_page_headings_web_unit."""

import pytest

from cairn.compiler import BREADCRUMB, claim_id, compile_note, render_body
from cairn.spans import Span
from cairn.store import Claim, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


@pytest.fixture
def project(store):
    return store.create_project("alpha-ops")


def compiled(store, project, text, **fields):
    note, _ = store.add_note(project.id, text, **fields)
    result = compile_note(store, note)
    assert result.status == "saved", result
    return store.get_page(project.id, result.slug)


def span(span_id: str, path: tuple[str, ...], text: str) -> Span:
    return Span(
        id=span_id,
        source_id="cap_000000000000",
        kind="paragraph",
        heading_path=path,
        start_line=1,
        end_line=1,
        text=text,
    )


def body_of(title: str, paths: list[tuple[str, ...]]) -> str:
    """render_body of one claim per path, the claim texts "c1", "c2", ... in that order."""
    spans = [span(f"sp_{n:016x}", path, f"c{n}") for n, path in enumerate(paths, 1)]
    claims = [Claim(id=claim_id(s.id), text=s.text, span_ids=(s.id,)) for s in spans]
    return render_body(title, claims, {s.id: s for s in spans})


def headings_over(body: str) -> dict[str, tuple[str, ...]]:
    """Each claim line of `body` ("c1", ...): the headings it sits under, as a Markdown reader
    has them (a heading at level n closes every heading at level n and below)."""
    open_: list[tuple[int, str]] = []
    over = {}
    for block in body.split("\n\n"):
        block = block.strip()
        marks = len(block) - len(block.lstrip("#"))
        if marks == 1:
            continue  # the page's title
        if marks:
            while open_ and open_[-1][0] >= marks:
                open_.pop()
            # the deepest level holds the rest of the path as a breadcrumb (should-fix 9)
            open_.extend((marks, text) for text in block[marks:].strip().split(BREADCRUMB))
        else:
            over[block] = tuple(text for _, text in open_)
    return over


# 1. The reviewer's two examples, on made-up notes


def test_a_claim_with_no_heading_is_not_shown_under_an_earlier_notes_section(store, project):
    compiled(
        store,
        project,
        "## Deprecated — DO NOT USE\n\nNever run legacy-reset.sh on production hosts.\n",
        title="Deploy",
    )
    page = compiled(
        store,
        project,
        "Always run verify-backup.sh before release.\n",
        title="Release verify",
        topic="Deploy",
    )
    assert page.version == 2
    over = headings_over(page.body)
    assert over["Always run verify-backup.sh before release."] == ()
    assert over["Never run legacy-reset.sh on production hosts."] == ("Deprecated — DO NOT USE",)


def test_nested_sections_keep_their_parents(store, project):
    page = compiled(
        store,
        project,
        "## Production\n\n### Ports\n\nPort 8080 serves TLS.\n\n"
        "## Staging\n\n### Ports\n\nPort 9090 serves staging.\n",
        title="Ports guide",
    )
    assert page.body == (
        "# Ports guide\n\n## Production\n\n### Ports\n\nPort 8080 serves TLS.\n\n"
        "## Staging\n\n### Ports\n\nPort 9090 serves staging.\n"
    )


# 1. Whatever the order of the claims


@pytest.mark.parametrize(
    "paths",
    [
        [("A",), ()],
        [("A", "B"), ("A",)],
        [("A", "B"), ("A",), ("A", "B")],
        [("A",), ("B",), ("A",)],
        [("A", "B", "C"), ("A",), ("A", "D"), ()],
        [("T", "A"), ("T",), ("A",)],
        [("A",), ("T",)],
        [tuple("abcdefgh"), tuple("abcdefg"), tuple("abcdefgi")],
        [tuple("abcdefg"), tuple("abcdef"), tuple("abcdefh"), tuple("abcde"), tuple("abcdx")],
        [("T", *"abcdef"), tuple("abcdef")],
        [("A", "B"), ("C", "B"), ("A", "B")],
    ],
)
def test_no_claim_is_shown_under_a_heading_off_its_path(paths):
    over = headings_over(body_of("T", paths))
    for n, path in enumerate(paths, 1):
        own = path[1:] if path[:1] == ("T",) else path
        shown = over[f"c{n}"]
        assert shown == own, (n, path, shown)  # every heading, past ###### too (should-fix 9)


# 2. A claim with no section comes before every section


def test_claims_with_no_section_come_first_in_their_order():
    body = body_of("T", [("A",), (), ("T",), ("B",), ()])
    assert body == "# T\n\nc2\n\nc3\n\nc5\n\n## A\n\nc1\n\n## B\n\nc4\n"


def test_a_page_whose_claims_have_no_section_reads_in_order():
    assert body_of("T", [(), ("T",), ()]) == "# T\n\nc1\n\nc2\n\nc3\n"


def test_moving_them_first_changes_no_stored_claim(store, project):
    compiled(store, project, "## Old\n\nOld words here.\n", title="Deploy")
    page = compiled(store, project, "Fresh words here.\n", title="Fresh", topic="Deploy")
    assert [c.text for c in page.claims] == ["Old words here.", "Fresh words here."]
    assert page.body.index("Fresh words here.") < page.body.index("## Old")


# 3. The whole path, each heading at its depth, written again only when it changes


def test_each_heading_is_written_at_its_depth_and_deeper_ones_in_the_sixth():
    """Should-fix 9: past ###### the rest of the path is one heading, a breadcrumb, so no
    heading of it reads as a sibling of its parent (it read "###### e", "###### f", ...)."""
    body = body_of("T", [tuple("abcdefg")])
    assert body == "# T\n\n## a\n\n### b\n\n#### c\n\n##### d\n\n###### e > f > g\n\nc1\n"


def test_six_nested_headings_under_another_title_keep_their_whole_ancestry(store, project):
    """The reviewer's note: six nested headings, and a page title that is none of them."""
    text = "".join(f"{'#' * level} H{level}\n\n" for level in range(1, 7)) + "Deep words.\n"
    page = compiled(store, project, text, title="Other", topic="Other")
    assert headings_over(page.body)["Deep words."] == tuple(f"H{n}" for n in range(1, 7))
    assert "###### H5 > H6\n" in page.body


def test_a_heading_that_holds_the_breadcrumb_reads_as_two():
    """The limit of a breadcrumb: a heading whose own text holds " > " past the sixth level
    reads as two headings there; every heading is still shown, in order."""
    body = body_of("T", [("a", "b", "c", "d", "e", "f > g")])
    assert "###### e > f > g\n" in body


def test_a_heading_is_written_again_only_when_it_changes_at_its_depth_or_above():
    body = body_of(
        "T",
        [
            ("Production", "Ports"),
            ("Production", "Ports"),
            ("Production", "Hosts"),
            ("Staging", "Ports"),
        ],
    )
    assert body == (
        "# T\n\n## Production\n\n### Ports\n\nc1\n\nc2\n\n### Hosts\n\nc3\n\n"
        "## Staging\n\n### Ports\n\nc4\n"
    )


def test_returning_to_a_parent_writes_the_parent_again():
    body = body_of("T", [("A", "B"), ("A",)])
    assert body == "# T\n\n## A\n\n### B\n\nc1\n\n## A\n\nc2\n"


def test_a_leading_heading_that_is_the_pages_title_is_not_written():
    assert body_of("T", [("T", "A", "B")]) == "# T\n\n## A\n\n### B\n\nc1\n"


# 6. A one-note page of flat sections, or of none, renders as before (green before and after)


FLAT = "# Guide\n\nIntro line.\n\n## Install\n\n- step one\n- step two\n\n## Run\n\nRun it.\n"


@pytest.mark.parametrize(
    ("text", "body"),
    [
        (FLAT, FLAT.replace("one\n-", "one\n\n-")),  # each list item is a claim
        ("## Install\n\nGet it.\n\n## Run\n\nRun it.\n", None),
        ("Just words.\n\nMore words.\n", None),
        ("# Guide\n\nJust words.\n\nMore words.\n", "# Guide\n\nJust words.\n\nMore words.\n"),
    ],
)
def test_a_one_note_page_of_flat_sections_or_none_is_unchanged(store, project, text, body):
    page = compiled(store, project, text, title="Guide")
    expected = body if body is not None else "# Guide\n\n" + text
    assert page.body == expected


# 9. One pass over the claims


def test_render_body_reads_the_claims_once():
    spans = [span("sp_1", ("A", "B"), "x"), span("sp_2", (), "y"), span("sp_3", ("A",), "z")]
    claims = [Claim(id=claim_id(s.id), text=s.text, span_ids=(s.id,)) for s in spans]
    once = iter(claims)
    body = render_body("T", once, {s.id: s for s in spans})  # type: ignore[arg-type]
    assert body == "# T\n\ny\n\n## A\n\n### B\n\nx\n\n## A\n\nz\n"
