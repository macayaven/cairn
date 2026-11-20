"""Nobody is offered a note form where they cannot write (fifth report, "Read and write
access"). The home page offers Write a note only to a person who can write to a project; the
quick form at /new, the share target too, lists only the projects they can write to, and
answers a person who reads projects and writes to none as a project's own form answers a
reader: 403, in the same words. A note sent to a project the person only reads is 403
whatever else is wrong with it, and nothing of it is stored. Every name here is made up."""

import html
import re

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cookie_names import session_cookie

BASE = "https://cairn.example.test"
ISSUER = "https://id.example.test"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
READ_ONLY = "This project is read-only for you: you can read it but not write to it."
OFFER = '<a class="button" href="/new">Write a note</a>'
NOTE = "# Quill\n\nQuill rests on the seventh day.\n"
SHARED = {"title": "Quill & co", "text": "Quill caches results.", "url": "https://example.test/q"}
# What each person can write to, of `handbook` (every member reads it) and `team-ops`.
WRITES = {
    "Ada": ["handbook", "team-ops"],  # the workspace's owner
    "Dee": ["handbook", "team-ops"],  # a member given both
    "Brook": ["team-ops"],  # reads the handbook
    "Quill": [],  # reads both
}


class World:
    def __init__(self, store: Store, client: TestClient) -> None:
        self.store, self.client = store, client
        self.people = {
            name: store.sign_in(
                "sso", name.lower(), f"{name.lower()}@example.test", name,
                issuer=ISSUER, join="default",
            )
            for name in (*WRITES, "Nova")
        }  # fmt: skip
        self.ada = store.for_account(self.people["Ada"].account_id, self.people["Ada"].user.id)
        self.handbook = self.ada.create_project("handbook", everyone="read")
        self.ops = self.ada.create_project("team-ops", everyone="none")
        ids = {name: signed.user.id for name, signed in self.people.items()}
        self.ada.set_project_access(self.handbook.id, people={ids["Dee"]: "write"})
        levels = {ids["Dee"]: "write", ids["Brook"]: "write", ids["Quill"]: "read"}
        self.ada.set_project_access(self.ops.id, people=levels)

    def as_(self, name: str) -> TestClient:
        self.client.cookies.set(session_cookie("https"), self.people[name].token)
        return self.client

    def post(self, who: str, path: str, /, **fields: str):
        return self.as_(who).post(path, data=fields, headers=SAME_ORIGIN, follow_redirects=False)

    def notes(self) -> int:
        return sum(self.ada.count_notes(p.id) for p in self.ada.list_projects())


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "web.db")
    try:
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            yield World(store, client)
    finally:
        store.close()


def listed(page: str) -> list[str]:
    """The projects the quick form lets the person choose from."""
    select = re.search(r'<select\b[^>]*name="project".*?</select>', page, re.S)
    assert select, "no project picker"
    return re.findall(r'<option\b[^>]*value="([^"]*)"', select.group(0))


def refused_as_a_reader(response) -> bool:
    """The project form's answer to a reader: 403, its words, and no form to write in."""
    assert "<textarea" not in response.text and "<select" not in response.text
    return response.status_code == 403 and READ_ONLY in response.text


# The home page


@pytest.mark.parametrize("name", list(WRITES))
def test_home_offers_write_a_note_only_to_who_can_write_somewhere(world, name):
    home = world.as_(name).get("/")
    assert home.status_code == 200
    assert "handbook" in home.text  # every one of them sees a project there
    assert (OFFER in home.text) is bool(WRITES[name])
    assert ("Write a note" in home.text) is bool(WRITES[name])


def test_home_stops_and_starts_offering_as_the_rule_changes(world):
    quill = world.people["Quill"].user.id
    world.ada.set_project_access(world.handbook.id, people={quill: "write"})
    assert OFFER in world.as_("Quill").get("/").text
    world.ada.set_project_access(world.handbook.id, people={})
    assert OFFER not in world.as_("Quill").get("/").text
    world.ada.set_project_access(world.handbook.id, everyone="write")
    assert OFFER in world.as_("Quill").get("/").text


@pytest.mark.parametrize("name", list(WRITES))
def test_home_shown_again_after_a_refused_new_project_offers_the_same(world, name):
    again = world.post(name, "/projects", name="not a name")
    assert again.status_code == 400 and 'role="alert"' in again.text
    assert (OFFER in again.text) is bool(WRITES[name])


def test_home_with_no_project_in_reach_offers_no_note(world):
    home = world.as_("Nova").get("/")  # a member of the workspace: reads the handbook
    assert OFFER not in home.text
    world.ada.set_project_access(world.handbook.id, everyone="none")
    home = world.as_("Nova").get("/")
    assert home.status_code == 200 and "No projects yet" in home.text
    assert "Write a note" not in home.text


# The quick form and the share target


@pytest.mark.parametrize("name", [name for name in WRITES if WRITES[name]])
@pytest.mark.parametrize("shared", [{}, SHARED], ids=["new", "share-target"])
def test_the_quick_form_lists_only_the_projects_the_person_can_write_to(world, name, shared):
    form = world.as_(name).get("/new", params=shared)
    assert form.status_code == 200
    assert listed(form.text) == WRITES[name]
    if shared:
        assert 'value="Quill &amp; co"' in form.text
        assert "Quill caches results.\n\nhttps://example.test/q" in html.unescape(form.text)


@pytest.mark.parametrize("shared", [{}, SHARED], ids=["new", "share-target"])
def test_the_quick_form_answers_a_reader_of_everything_as_a_project_s_form_does(world, shared):
    quick = world.as_("Quill").get("/new", params=shared)
    own = world.as_("Quill").get("/b/handbook/new")
    assert refused_as_a_reader(quick) and refused_as_a_reader(own)
    # Nothing shared is said back: not the text, the title or the link.
    assert "Quill caches" not in quick.text and "example.test/q" not in quick.text
    assert "Quill &amp; co" not in quick.text


def test_the_navigation_still_links_new_note_and_a_reader_of_everything_meets_the_refusal(world):
    """A stated limit: every signed-in page links New note in its navigation, whoever reads
    it, since asking who can write would cost a read on every page. The link leads a person
    who can write nowhere to the refusal, never to a form."""
    link = '<a href="/new">New note</a>'
    for name in WRITES:
        assert link in world.as_(name).get("/").text, name
        assert link in world.as_(name).get("/b/handbook").text, name
    assert refused_as_a_reader(world.as_("Quill").get("/new"))


def test_the_quick_form_still_offers_to_make_a_project_when_none_is_in_reach(world):
    world.ada.set_project_access(world.handbook.id, everyone="none")
    page = world.as_("Nova").get("/new")
    assert page.status_code == 200 and 'action="/projects"' in page.text
    assert 'action="/notes"' not in page.text and READ_ONLY not in html.unescape(page.text)


# A note sent where the person only reads


WRONG = {
    "a note": {"text": NOTE},
    "no text": {"text": ""},
    "a source close to a form": {"text": NOTE, "source_ref": "email:quill"},
    "a title too long": {"text": NOTE, "title": "Quill " * 400},
}


@pytest.mark.parametrize("name", ["Brook", "Quill"])
@pytest.mark.parametrize("path", ["/notes", "/b/handbook/notes"])
@pytest.mark.parametrize("sent", list(WRONG))
def test_a_note_sent_to_a_project_the_person_only_reads_is_403_and_not_stored(
    world, name, path, sent
):
    fields = WRONG[sent] | ({"project": "handbook"} if path == "/notes" else {})
    assert refused_as_a_reader(world.post(name, path, **fields))
    assert world.notes() == 0
    assert world.ada.list_pages(world.handbook.id) == []


@pytest.mark.parametrize("sent", [sent for sent in WRONG if sent != "a note"])
def test_a_refused_note_shows_the_form_again_with_only_the_writable_projects(world, sent):
    again = world.post("Brook", "/notes", project="team-ops", **WRONG[sent])
    assert again.status_code in (400, 413) and 'role="alert"' in again.text
    assert listed(again.text) == ["team-ops"]
    assert world.notes() == 0


def test_a_note_sent_with_no_project_chosen(world):
    brook = world.post("Brook", "/notes", text=NOTE)
    assert brook.status_code == 400 and listed(brook.text) == ["team-ops"]
    assert refused_as_a_reader(world.post("Quill", "/notes", text=NOTE))
    assert world.notes() == 0


def test_a_writer_s_note_is_stored_where_they_chose(world):
    sent = world.post("Brook", "/notes", project="team-ops", text=NOTE)
    assert sent.status_code == 303 and sent.headers["location"] == "/b/team-ops/p/quill"
    assert world.ada.count_notes(world.ops.id) == 1
    assert world.ada.count_notes(world.handbook.id) == 0
    assert world.post("Quill", "/notes", project="nowhere", text=NOTE).status_code == 404


@pytest.mark.parametrize("sent", list(WRONG))
def test_a_note_sent_to_a_project_out_of_reach_is_404_as_for_one_that_does_not_exist(world, sent):
    """Nova is not let into `team-ops`: the answer never says it is there to be read."""
    for path, fields in (("/notes", {"project": "team-ops"}), ("/b/team-ops/notes", {})):
        there = world.post("Nova", path, **WRONG[sent], **fields)
        assert there.status_code == 404 and READ_ONLY not in there.text
    assert world.as_("Nova").get("/b/team-ops/new").status_code == 404
    assert world.notes() == 0


def test_a_project_that_became_read_only_since_the_form_opened_is_not_swapped_for_another(world):
    """Brook opens the quick form with the handbook writable, loses the right, and sends a
    note whose source is close to a form: the answer is the refusal, never the form again
    with another project chosen in its place."""
    brook = world.people["Brook"].user.id
    world.ada.set_project_access(world.handbook.id, people={brook: "write"})
    assert listed(world.as_("Brook").get("/new").text) == ["handbook", "team-ops"]
    world.ada.set_project_access(world.handbook.id, people={})
    late = world.post("Brook", "/notes", project="handbook", **WRONG["a source close to a form"])
    assert refused_as_a_reader(late)
    assert world.notes() == 0


# A session is a person's or a whole key's: none reads through a read-only key


def test_a_read_only_key_opens_no_web_session(world):
    key = world.store.create_key("reader", access="read")
    world.client.cookies.clear()
    login = world.client.post(
        "/login", data={"key": key}, headers=SAME_ORIGIN, follow_redirects=False
    )
    signed_in = session_cookie("https") in world.client.cookies  # a failure prints no cookie
    assert login.status_code != 303 and not signed_in
    assert world.client.get("/new", follow_redirects=False).status_code != 200
