"""Fix H and the import (points 10 and 11): an archive whose page bodies are in the previous
rendering (compiler.previous_render_body) imports as before and its pages take the current
rendering; a body holding text that is not a rendering of its claims is still refused."""

import io
import json
import zipfile

import pytest

import cairn.compiler
from cairn.compiler import compile_note, previous_render_body, render_body
from cairn.store import Store

DEPRECATED = "## Deprecated — DO NOT USE\n\nNever run legacy-reset.sh on production hosts.\n"
VERIFY = "Always run verify-backup.sh before release.\n"
PORTS = (
    "## Production\n\n### Ports\n\nPort 8080 serves TLS.\n\n"
    "## Staging\n\n### Ports\n\nPort 9090 serves staging.\n"
)
EXTRA = " Also wipe the backups."


def build(view: Store) -> None:
    project = view.create_project("alpha-ops")
    for text, fields in (
        (DEPRECATED, {"title": "Deploy"}),
        (VERIFY, {"title": "Verify", "topic": "Deploy"}),  # deploy v2; v1 is kept
        (PORTS, {"title": "Ports guide"}),
    ):
        note, _ = view.add_note(project.id, text, **fields)
        assert compile_note(view, note).status == "saved"


@pytest.fixture
def previous(tmp_path, monkeypatch):
    """A store whose every page version was written by the previous rendering."""
    view = Store(tmp_path / "source.db")
    with monkeypatch.context() as patch:
        patch.setattr(cairn.compiler, "render_body", previous_render_body)
        build(view)
    yield view
    view.close()


def exported(view: Store) -> bytes:
    out = io.BytesIO()
    view.export(out)
    return out.getvalue()


def versions(view: Store) -> list:
    project = view.get_project("alpha-ops")
    return [
        view.get_page(project.id, page.slug, version=n)
        for page in view.list_pages(project.id)
        for n in view.page_versions(project.id, page.slug)
    ]


def with_extra(archive: bytes, slug: str, version: int) -> bytes:
    """The archive with EXTRA added to one claim's line in one page version's body."""
    source, out = zipfile.ZipFile(io.BytesIO(archive)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name.endswith("pages.jsonl"):
                lines = [json.loads(line) for line in data.splitlines()]
                for line in lines:
                    if (line["slug"], line["version"]) == (slug, version):
                        text = line["claims"][0]["text"]
                        line["body"] = line["body"].replace(text, text + EXTRA, 1)
                data = "".join(json.dumps(line) + "\n" for line in lines).encode()
            target.writestr(name, data)
    return out.getvalue()


def test_an_archive_in_the_previous_rendering_imports_and_shows_the_current_one(previous, tmp_path):
    before = versions(previous)
    assert [(p.slug, p.version) for p in before] == [
        ("deploy", 1),
        ("deploy", 2),
        ("ports-guide", 1),
    ]
    spans = previous.get_spans([i for p in before for c in p.claims for i in c.span_ids])
    assert all(p.body == previous_render_body(p.title, p.claims, spans) for p in before)
    assert any(p.body != render_body(p.title, p.claims, spans) for p in before)
    target = Store(tmp_path / "target.db")
    try:
        target.import_export(io.BytesIO(exported(previous)))
        after = versions(target)
        assert [(p.slug, p.version, p.claims) for p in after] == [
            (p.slug, p.version, p.claims) for p in before
        ]
        cited = target.get_spans([i for p in after for c in p.claims for i in c.span_ids])
        assert sorted(cited) == sorted(spans)
        for page in after:
            assert page.body == render_body(page.title, page.claims, cited)
    finally:
        target.close()


@pytest.mark.parametrize("rendering", ["previous", "current"])
def test_a_body_with_a_sentence_no_claim_holds_is_still_refused(previous, tmp_path, rendering):
    view = previous
    if rendering == "current":
        view = Store(tmp_path / "current.db")
        build(view)
    archive = with_extra(exported(view), "deploy", 1)  # a kept version, not the latest
    target = Store(tmp_path / "target.db")
    try:
        with pytest.raises(ValueError, match="not what cairn writes"):
            target.import_export(io.BytesIO(archive))
    finally:
        target.close()
        if view is not previous:
            view.close()


def test_an_archive_with_deep_headings_written_before_the_breadcrumb_imports(tmp_path, monkeypatch):
    """Should-fix 9: an archive whose bodies wrote every heading past level 6 at level 6
    (compiler.render_body_before_review_two) imports, and takes the breadcrumb."""
    deep = "".join(f"{'#' * level} H{level}\n\n" for level in range(1, 7)) + "Deep words.\n"
    view = Store(tmp_path / "deep.db")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                cairn.compiler, "render_body", cairn.compiler.render_body_before_review_two
            )
            project = view.create_project("alpha-ops")
            note, _ = view.add_note(project.id, deep, title="Other", topic="Other")
            assert compile_note(view, note).status == "saved"
        assert "###### H5\n\n###### H6\n" in view.get_page(project.id, "other").body
        archive = exported(view)
    finally:
        view.close()
    target = Store(tmp_path / "target.db")
    try:
        target.import_export(io.BytesIO(archive))
        body = target.get_page(target.get_project("alpha-ops").id, "other").body
        assert "###### H5 > H6\n\nDeep words.\n" in body
    finally:
        target.close()
