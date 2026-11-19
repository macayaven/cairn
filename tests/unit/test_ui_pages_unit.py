"""Unit tests for the pages' wave of fixes (review: UI and UX 2, 3, 5, 6, 7, 8, 9): one click
ends nothing, the first-run pages, getting around (skip link, breadcrumbs, citations), the
set-up page's address and narrow screens."""

import re

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cairn.web.brand import Brand
from cairn.web.templating import _page

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
SKIP = '<a class="skip-link" href="#main">Skip to the main content</a>'
NOTE = "# Deploy\n\nRun verify-backup.sh before tagging a release.\n"


@pytest.fixture
def env(tmp_path):
    """A key session in the default workspace, with the project "ops" holding one note."""
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        client.post("/projects", data={"name": "ops"}, headers=SAME_ORIGIN)
        saved = client.post("/b/ops/notes", data={"text": NOTE}, headers=SAME_ORIGIN)
        assert saved.status_code in (200, 303)
        yield client, store
    store.close()


def main_of(html: str) -> str:
    return html.split('<main id="main">', 1)[1].split("</main>", 1)[0]


# 4. Every page starts with a link to its main content


def test_every_page_starts_with_a_skip_link_to_its_main_landmark(env):
    client, _ = env
    for path in ("/", "/b/ops", "/search", "/new", "/workspace", "/keys", "/setup", "/start"):
        html = client.get(path).text
        body = html.split("<body", 1)[1].split(">", 1)[1].lstrip()
        assert body.startswith(SKIP), path
        assert html.count('<main id="main">') == 1, path


def test_a_signed_out_page_has_the_skip_link_too(env):
    client, _ = env
    client.post("/logout", headers=SAME_ORIGIN)
    html = client.get("/login").text
    assert SKIP in html and '<main id="main">' in html


def test_a_branded_page_has_the_skip_link_too():
    html = _page("brand_retry.html", brand=Brand("Fable Works"), url="/").body.decode()
    body = html.split("<body", 1)[1].split(">", 1)[1].lstrip()
    assert body.startswith(SKIP) and '<main id="main">' in html


def test_the_skip_link_shows_only_when_it_has_the_keyboards_focus():
    from cairn.web.templating import HERE

    css = (HERE / "static" / "cairn.css").read_text("utf-8")
    hidden = re.search(r"\.skip-link \{([^}]*)\}", css.replace(".visually-hidden, ", ""))
    shown = re.search(r"\.skip-link:focus \{([^}]*)\}", css)
    assert hidden and "clip-path: inset(50%)" in hidden.group(1)
    assert shown and "clip-path: none" in shown.group(1)
