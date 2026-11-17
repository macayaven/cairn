"""Unit tests for `serve --operator-notes FILE`: the operator's own words, made safe by the
claims' Markdown renderer, as the last section of /docs/privacy in place of the list the
operator would otherwise have to fill in."""

import pytest
import uvicorn
from starlette.testclient import TestClient

from cairn import server
from cairn.server import create_app
from cairn.store import Store
from cairn.web.public import OPERATOR_NOTES_MAX, read_operator_notes

BASE = "http://127.0.0.1:8765"
NOTES = (
    "**Who runs this install:** Ada Lovelace, ada@example.test\n\n"
    "- **Where it runs:** one machine in Frankfurt\n\n"
    "# Not a heading\n\n<script>alert(1)</script>\n"
)


def privacy(tmp_path, notes: str | None) -> str:
    store = Store(tmp_path / "cairn.db")
    with TestClient(create_app(store, public_url=BASE, operator_notes=notes), base_url=BASE) as c:
        page = c.get("/docs/privacy").text
    store.close()
    return page


def test_the_operators_notes_are_the_last_section_of_the_privacy_guide(tmp_path):
    page = privacy(tmp_path, NOTES)
    last = page.split(
        "<h2",
    )[-1]
    assert last.startswith(' id="from-the-operator">From the operator</h2>')
    assert "<strong>Who runs this install:</strong> Ada Lovelace" in last
    assert "<h1>Not a heading" not in page and "Not a heading" in last  # its headings are cairn's
    assert "<script" not in page and "&lt;script&gt;" in last  # raw HTML is shown as text
    assert "For the operator to fill in" not in page and "_the operator" not in page
    assert 'href="#from-the-operator"' in page  # listed on the page like any section


def test_without_notes_the_privacy_guide_keeps_the_list_to_fill_in(tmp_path):
    page = privacy(tmp_path, None)
    assert "For the operator to fill in" in page and "From the operator" not in page


def test_a_notes_file_is_read_whole_up_to_64_kib(tmp_path):
    file = tmp_path / "notes.md"
    file.write_text(NOTES, encoding="utf-8")
    assert read_operator_notes(file) == NOTES
    assert OPERATOR_NOTES_MAX == 64 * 1024
    file.write_bytes(b"x" * OPERATOR_NOTES_MAX)
    assert len(read_operator_notes(file)) == OPERATOR_NOTES_MAX
    file.write_bytes(b"x" * (OPERATOR_NOTES_MAX + 1))
    with pytest.raises(ValueError, match="64 KiB"):
        read_operator_notes(file)
    with pytest.raises(ValueError, match="cannot read"):
        read_operator_notes(tmp_path / "missing.md")
    file.write_bytes(b"\xff\xfe not text")
    with pytest.raises(ValueError, match="UTF-8"):
        read_operator_notes(file)


def test_serve_refuses_to_start_with_notes_it_cannot_use(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    argv = ["serve", "--db", str(tmp_path / "cairn.db"), "--operator-notes"]
    assert server.main([*argv, str(tmp_path / "missing.md")]) == 2
    assert "operator notes" in capsys.readouterr().err


def test_serve_shows_the_notes_it_was_given(tmp_path, monkeypatch):
    file = tmp_path / "notes.md"
    file.write_text(NOTES, encoding="utf-8")
    seen = {}

    def run(app, **kwargs):
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            seen["page"] = client.get("/docs/privacy").text

    monkeypatch.setattr(uvicorn, "run", run)
    argv = ["serve", "--db", str(tmp_path / "cairn.db"), "--operator-notes", str(file)]
    assert server.main(argv) == 0
    assert "Ada Lovelace" in seen["page"]
