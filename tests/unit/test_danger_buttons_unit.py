"""A button that removes something for good does not look like the button that saves.

Deleting a workspace, deleting a project and forgetting a note cannot be undone. Their submit
buttons carry the class "danger", and the stylesheet gives that class the design system's
`stop` colour in both colour schemes instead of the ink every other main button uses.
"""

import re
from pathlib import Path

from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.server import create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
PACKAGE = Path(__file__).resolve().parents[2] / "src" / "cairn"


def submit_buttons(html: str) -> list[str]:
    main = html.split("<main", 1)[1]
    return re.findall(r"<button\b[^>]*>", main)


def test_deleting_a_project_and_forgetting_a_note_use_the_danger_button(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        key = store.create_key("laptop")
        project = store.create_project("ops")
        note, _ = store.add_note(project.id, "# Deploys\n\nDeploys go out on Tuesdays.\n")
        assert compile_note(store, note).status == "saved"
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            for path in ("/b/ops/delete", f"/b/ops/notes/{note.id}/forget"):
                page = client.get(path)
                assert page.status_code == 200, path
                (button,) = submit_buttons(page.text)
                assert 'class="danger"' in button, (path, button)
            composer = client.get("/b/ops/new")
            assert all("danger" not in button for button in submit_buttons(composer.text))
    finally:
        store.close()


def test_deleting_the_workspace_uses_it_too_and_the_stylesheet_defines_it():
    account = (PACKAGE / "templates" / "account.html").read_text(encoding="utf-8")
    delete_form = account.split('action="/account/delete"', 1)[1].split("</form>", 1)[0]
    assert '<button type="submit" class="danger">' in delete_form

    css = (PACKAGE / "static" / "cairn.css").read_text(encoding="utf-8")
    rule = re.search(r"button\.danger[^{]*\{([^}]*)\}", css)
    assert rule is not None and "background: var(--stop)" in rule.group(1)
    assert "color: var(--on-stop)" in rule.group(1)
    assert "button.danger:hover" in css
    assert css.count("--stop:") == 2  # one value for each colour scheme
