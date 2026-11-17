"""M1 acceptance tests (frozen): write and search from the web view, on a phone too.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstring of src/cairn/server.py; this is what it must add.

Writing from the web view (a web session; forms are plain HTML, no script)
- GET /b/<project> links to GET /b/<project>/new, a composer: one form posting to
  /b/<project>/notes with a `text` textarea and optional `title`, `topic` and `source_ref`.
- POST /b/<project>/notes stores and compiles the note exactly as the `note` tool does, with
  via "web", and redirects (303) to the page it landed on, /b/<project>/p/<slug>.
  Blank text: 400 and the composer again with a message (role="alert"). A note the gate
  rejects: 422, the gate's violations listed, and what was typed still in the form.
  An unknown project, or another workspace's: 404.
- GET /new is the same composer with a `project` select listing the caller's projects, posting
  to /notes. It pre-fills `title` and `text` from the query string, and appends a `url`
  parameter to the text on a line of its own: this is where a phone's share sheet lands.
  With no project yet it says so and offers to create one.
- GET / has a form posting to /projects (`name`, `description`): 303 to /b/<name> when created,
  409 with a message when the name is taken, 400 with a message when it is not a valid name.
- Like every form in the web view, these refuse cross-site posts (403) and write nothing.

Installable
- Every page links a web app manifest at /static/manifest.webmanifest: `name`, `short_name`,
  `start_url` "/", `display` "standalone", at least one icon served from /static/, and a
  `share_target` with action "/new", method "GET" and params title, text and url. The web
  view's Content-Security-Policy allows it (`manifest-src 'self'`); without that a browser
  refuses to load the manifest.
- The search box is on the home page and on every project page.

Reads never write: opening a composer writes nothing.
"""

import html
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.compiler import GateResult
from cairn.server import create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
CROSS_SITE = {"sec-fetch-site": "cross-site"}
NOTE = "# Retry policy\n\nQuill retries 3 times before failing.\n\n- Lumen never retries.\n"
SLUG = "retry-policy"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def world(tmp_path, **options) -> SimpleNamespace:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("phone")
    app = create_app(store, public_url=BASE, **options)
    return SimpleNamespace(store=store, db=db, key=key, app=app)


def signed_in(w: SimpleNamespace, client: TestClient) -> TestClient:
    r = client.post("/login", data={"key": w.key}, headers=SAME_ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    return client


@pytest.fixture
def env(tmp_path):
    w = world(tmp_path)
    w.store.create_project("alpha", "the first project")
    with TestClient(w.app, base_url=BASE) as client:
        w.client = signed_in(w, client)
        yield w
    w.store.close()


def post(env, path: str, **data):
    return env.client.post(path, data=data, headers=SAME_ORIGIN, follow_redirects=False)


def form_action(text: str, action: str) -> str:
    """The <form> element that posts to `action`, as text."""
    found = re.search(rf'<form\b[^>]*action="{re.escape(action)}"[^>]*>.*?</form>', text, re.S)
    assert found, f"no form posting to {action}"
    assert re.search(r'method="post"', found.group(0), re.I)
    return found.group(0)


def test_a_note_can_be_written_from_a_projects_page(env):
    project_page = env.client.get("/b/alpha").text
    assert 'href="/b/alpha/new"' in project_page

    composer = env.client.get("/b/alpha/new")
    assert composer.status_code == 200
    form = form_action(composer.text, "/b/alpha/notes")
    assert re.search(r'<textarea\b[^>]*name="text"', form)
    for field in ("title", "topic", "source_ref"):
        assert f'name="{field}"' in form, field
    assert "<script" not in composer.text.lower()

    r = post(env, "/b/alpha/notes", text=NOTE, source_ref="phone:2026-11-10")
    assert r.status_code == 303 and r.headers["location"] == f"/b/alpha/p/{SLUG}"
    page = env.client.get(r.headers["location"])
    assert page.status_code == 200 and "Quill retries 3 times before failing." in page.text

    project = env.store.get_project("alpha")
    (note,) = env.store.list_notes(project.id)
    assert (note.text, note.via, note.source_ref) == (NOTE, "web", "phone:2026-11-10")
    assert env.store.search("quill")

    # The same note again changes nothing and still lands on the page.
    again = post(env, "/b/alpha/notes", text=NOTE, source_ref="phone:2026-11-10")
    assert again.status_code == 303 and again.headers["location"] == f"/b/alpha/p/{SLUG}"
    assert len(env.store.list_notes(project.id)) == 1

    filed = post(env, "/b/alpha/notes", text="Lumen logs to stderr.", topic="Retry policy")
    assert filed.status_code == 303 and filed.headers["location"] == f"/b/alpha/p/{SLUG}"
    assert env.store.get_page(project.id, SLUG).version == 2


def test_the_composer_says_what_went_wrong(env, tmp_path):
    before = dump(env.db)
    blank = post(env, "/b/alpha/notes", text="   \n")
    assert blank.status_code == 400 and 'role="alert"' in blank.text
    form_action(blank.text, "/b/alpha/notes")
    assert post(env, "/b/nope/notes", text=NOTE).status_code == 404
    assert env.client.get("/b/nope/new").status_code == 404
    assert dump(env.db) == before

    def refuse(page, spans):
        return GateResult(passed=False, violations=("retention.fact_dropped: 3 times",))

    w = world(tmp_path / "strict", gate=refuse)
    w.store.create_project("alpha")
    try:
        with TestClient(w.app, base_url=BASE) as client:
            signed_in(w, client)
            typed = "# Draft <b>\n\nQuill retries 3 times & stops.\n"
            r = client.post(
                "/b/alpha/notes", data={"text": typed}, headers=SAME_ORIGIN, follow_redirects=False
            )
            assert r.status_code == 422 and 'role="alert"' in r.text
            assert "retention.fact_dropped" in r.text
            assert html.escape(typed.strip()) in r.text  # still in the form, escaped
            assert "<b>" not in r.text
            assert w.store.list_pages(w.store.get_project("alpha").id) == []
    finally:
        w.store.close()


def test_quick_note_picks_the_project_and_takes_shared_text(env):
    env.store.create_project("beta")
    composer = env.client.get("/new")
    assert composer.status_code == 200
    form = form_action(composer.text, "/notes")
    select = re.search(r'<select\b[^>]*name="project".*?</select>', form, re.S).group(0)
    assert re.findall(r'<option\b[^>]*value="([^"]*)"', select) == ["alpha", "beta"]

    shared = env.client.get(
        "/new", params={"title": "Kestrel & co", "text": "Kestrel caches results.", "url": "https://example.test/k?a=1&b=2"}
    )  # fmt: skip
    form = form_action(shared.text, "/notes")
    assert 'value="Kestrel &amp; co"' in form
    area = re.search(r"<textarea\b[^>]*>(.*?)</textarea>", form, re.S).group(1)
    assert (
        html.unescape(area).strip() == "Kestrel caches results.\n\nhttps://example.test/k?a=1&b=2"
    )

    r = post(env, "/notes", project="beta", title="Kestrel & co", text=html.unescape(area).strip())
    assert r.status_code == 303 and r.headers["location"] == "/b/beta/p/kestrel-co"
    beta = env.store.get_project("beta")
    assert [c.title for c in env.store.list_notes(beta.id)] == ["Kestrel & co"]
    assert post(env, "/notes", project="nope", text=NOTE).status_code == 404
    assert post(env, "/notes", text=NOTE).status_code == 400  # no project chosen


def test_quick_note_without_a_project_offers_to_create_one(tmp_path):
    w = world(tmp_path)
    try:
        with TestClient(w.app, base_url=BASE) as client:
            signed_in(w, client)
            page = client.get("/new")
            assert page.status_code == 200 and 'action="/projects"' in page.text
            assert 'action="/notes"' not in page.text
    finally:
        w.store.close()


def test_a_project_can_be_created_from_the_home_page(env):
    form = form_action(env.client.get("/").text, "/projects")
    assert 'name="name"' in form and 'name="description"' in form

    r = post(env, "/projects", name="field-notes", description="written on the move")
    assert r.status_code == 303 and r.headers["location"] == "/b/field-notes"
    assert env.store.get_project("field-notes").description == "written on the move"
    assert "written on the move" in env.client.get("/").text

    taken = post(env, "/projects", name="Field-Notes")
    assert taken.status_code == 409 and 'role="alert"' in taken.text
    invalid = post(env, "/projects", name="not a name")
    assert invalid.status_code == 400 and 'role="alert"' in invalid.text
    assert [b.name for b in env.store.list_projects()] == ["alpha", "field-notes"]


def test_web_forms_refuse_cross_site_posts_and_other_workspaces(env):
    before = dump(env.db)
    for path, data in (
        ("/b/alpha/notes", {"text": NOTE}),
        ("/notes", {"project": "alpha", "text": NOTE}),
        ("/projects", {"name": "sneaky"}),
    ):
        r = env.client.post(path, data=data, headers=CROSS_SITE, follow_redirects=False)
        assert r.status_code == 403, path
    assert dump(env.db) == before

    other = env.store.for_account(env.store.create_account("Brook").id)
    theirs = other.create_project("private")
    assert post(env, f"/b/{theirs.id}/notes", text=NOTE).status_code == 404
    assert post(env, "/notes", project=theirs.id, text=NOTE).status_code == 404
    assert env.client.get(f"/b/{theirs.id}/new").status_code == 404
    assert other.list_notes(theirs.id) == []


def test_the_web_view_is_installable_and_search_is_always_at_hand(env):
    for path in ("/", "/b/alpha", "/b/alpha/new", "/new", "/search?q=x", "/setup"):
        page = env.client.get(path)
        assert '<link rel="manifest" href="/static/manifest.webmanifest">' in page.text, path
        assert 'name="viewport"' in page.text, path
        policy = [part.strip() for part in page.headers["content-security-policy"].split(";")]
        assert "manifest-src 'self'" in policy and "default-src 'none'" in policy, path
    for path in ("/", "/b/alpha"):
        text = env.client.get(path).text
        assert re.search(r'<form\b[^>]*action="/search"', text), path
        assert 'type="search"' in text, path

    env.client.cookies.clear()  # the manifest and its icons are public, like the stylesheet
    served = env.client.get("/static/manifest.webmanifest")
    assert served.status_code == 200
    manifest = json.loads(served.text)
    assert manifest["name"] and manifest["short_name"]
    assert (manifest["start_url"], manifest["display"]) == ("/", "standalone")
    assert manifest["icons"]
    for icon in manifest["icons"]:
        assert icon["src"].startswith("/static/") and icon["sizes"] and icon["type"]
        assert env.client.get(icon["src"]).status_code == 200
    assert manifest["share_target"] == {
        "action": "/new",
        "method": "GET",
        "params": {"title": "title", "text": "text", "url": "url"},
    }


def test_opening_a_composer_writes_nothing(env):
    before = dump(env.db)
    env.client.get("/new")
    env.client.get("/new", params={"text": "shared text", "url": "https://example.test/"})
    env.client.get("/b/alpha/new")
    env.client.get("/")
    assert dump(env.db) == before
