"""docs/guides/<name>.md: each guide as a server without a brand shows it, for a reader of the
repository. The guides in src/cairn/guides hold markers ({{name}}, {{slug}}, {{SLUG}}) that
the server fills (cairn.web.markdown.fill); on GitHub the source shows them raw. The rendered
copies are written by scripts/render_guides.py, which calls that same function, and these
checks fail when a copy is not what the script would write now.

Links. A guide's link to another guide is a relative `<name>.md#anchor`, which the server
turns into /docs/<name>#anchor and which, in docs/guides, leads to the rendered copy beside
it on GitHub; an anchor is the id GitHub gives a heading (cairn.web.markdown.anchor), from
the filled text, as the server's. The repository's documents link the rendered copies."""

import importlib.util
import re

import pytest

from cairn.web.markdown import MARKER, anchor
from cairn.web.public import GUIDES
from support.paths import REPO_ROOT

ROOT = REPO_ROOT
RENDERED = ROOT / "docs" / "guides"
SCRIPT = ROOT / "scripts" / "render_guides.py"
COMMAND = "uv run python scripts/render_guides.py"
# The documents a reader of the repository follows to a guide.
DOCUMENTS = [
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "deploy" / "README.md",
    ROOT / "infra" / "gcp" / "README.md",
    *sorted((ROOT / "docs").glob("*.md")),
]
LINK = re.compile(r"\]\(([^)\s]+)\)")
FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)


def render_guides():
    spec = importlib.util.spec_from_file_location("render_guides", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rendered(name: str) -> str:
    return (RENDERED / f"{name}.md").read_text(encoding="utf-8")


def anchors(text: str) -> set[str]:
    """The ids GitHub gives the headings of `text` (fenced code left out), a repeated one
    with -1, -2 after it."""
    seen: set[str] = set()
    for line in FENCE.sub("", text).splitlines():
        if found := re.match(r"#{1,6} +(.+?) *#*$", line):
            base, name, n = anchor(found.group(1)), anchor(found.group(1)), 0
            while name in seen:
                n += 1
                name = f"{base}-{n}"
            seen.add(name)
    return seen


def links(text: str) -> list[str]:
    return LINK.findall(FENCE.sub("", text))


@pytest.mark.parametrize("name", GUIDES)
def test_each_rendered_copy_is_what_the_script_writes_now(name):
    expected = render_guides().rendered(name)
    assert (RENDERED / f"{name}.md").exists(), f"docs/guides/{name}.md is missing: run {COMMAND}"
    assert rendered(name) == expected, f"docs/guides/{name}.md is out of date: run {COMMAND}"


def test_the_rendered_copies_are_the_guides_and_nothing_else():
    assert sorted(p.name for p in RENDERED.glob("*.md")) == sorted(f"{n}.md" for n in GUIDES)


@pytest.mark.parametrize("name", GUIDES)
def test_a_rendered_copy_says_where_it_comes_from_on_its_first_line(name):
    first = rendered(name).splitlines()[0]
    assert first == (
        f"<!-- Generated from src/cairn/guides/{name}.md by scripts/render_guides.py."
        f" Edit that file, not this one, and run {COMMAND}. -->"
    )


@pytest.mark.parametrize("name", GUIDES)
def test_no_rendered_copy_holds_a_marker(name):
    """No `{{` is left but Docker's own formats in the run guide's commands
    (`docker inspect cairn --format '{{.Image}}'` and `'{{json .RepoDigests}}'`), which
    are commands, not markers."""
    text = rendered(name)
    assert MARKER.search(text) is None
    assert re.findall(r"\{\{(?!\.Image\}\}|json \.RepoDigests\}\})", text) == []


def test_the_copies_are_the_servers_fill_of_the_source_and_not_a_second_one(monkeypatch):
    """The script fills through cairn.web.markdown.fill, the function the server's parser
    calls on each token: a change to it changes the copies."""
    module = render_guides()
    monkeypatch.setattr(module, "fill", lambda text, product: "filled by the server's fill")
    assert module.rendered("connect").endswith("filled by the server's fill")


@pytest.mark.parametrize("name", GUIDES)
def test_each_link_between_the_rendered_copies_leads_to_a_heading_there(name):
    for href in links(rendered(name)):
        if re.match(r"[a-z][a-z0-9+.-]*:", href, re.I):
            continue  # a web or mail address
        path, _, fragment = href.partition("#")
        target = RENDERED / path if path else RENDERED / f"{name}.md"
        assert target.exists(), (name, href)
        if fragment:
            assert fragment in anchors(target.read_text(encoding="utf-8")), (name, href)


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_the_repositorys_documents_link_the_rendered_copies(document):
    """A link that leads a reader to a guide leads to its rendered copy, and to a heading
    it has."""
    for href in links(document.read_text(encoding="utf-8")):
        if re.match(r"[a-z][a-z0-9+.-]*:", href, re.I):
            continue
        path, _, fragment = href.partition("#")
        target = (document.parent / path).resolve()
        assert "src/cairn/guides" not in target.as_posix(), (document.name, href)
        if target.parent == RENDERED:
            assert target.exists(), (document.name, href)
            if fragment:
                assert fragment in anchors(target.read_text(encoding="utf-8")), href


def test_the_readme_says_how_the_server_and_the_repository_show_the_guides():
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "the server and this repository show the same text" not in readme
    assert (
        "A running cairn serves these at `/docs`, from the Markdown in `src/cairn/guides`. The"
        " links here lead to `docs/guides`, the same guides as a server shows them without a"
        f" brand, written by `{COMMAND}`; a test fails when one is out of date."
    ) in readme
