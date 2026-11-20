"""What a project's page says about Models is said once, to everyone who sees the project
(fifth report, must-fix 4): the sentence of the state and what Models governs, the server's
own model and not the assistants people connect. Only the form that changes it depends on
who may change it. Every name here is made up."""

import re
from html import unescape

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cookie_names import session_cookie

BASE = "https://cairn.example.test"
ISSUER = "https://id.example.test"
GOVERNS = (
    "Models decides only what this server's own model, the one that arranges pages, is sent;"
    " the assistants people connect read what they reach and send it to their own provider."
)
SAID = {
    "never": (
        "Models: never. No model is sent this project's pages, whatever their notes say or the"
        " operator set. Pages already arranged stay as they are."
    ),
    "allowed": (
        "Models: allowed. A model the operator connects can be sent this project's pages, to"
        " put their claims in order."
    ),
    None: (
        "Models: not decided. Nobody has chosen for this project: a model the operator"
        " connects can be sent its pages, unless the operator sends only the pages of projects"
        " whose people chose allowed, and then it is sent none."
    ),
}
FORM = 'action="/b/ops/models"'
MAY_CHANGE = {"Ada": True, "Dee": True, "Brook": False, "Quill": False}


def visible(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split()).replace(" .", ".")


def models_part(html: str) -> str:
    return html.split('class="row models"', 1)[1].split("</div>", 1)[0]


def said(html: str) -> str:
    """The words of the Models paragraph, without the buttons under it."""
    (paragraph,) = re.findall(r"<p\b[^>]*>.*?</p>", models_part(html), re.S)
    return visible(paragraph)


@pytest.fixture
def world(tmp_path):
    """Ada owns the workspace and made `ops`; Dee decides who reaches it; Brook writes to it;
    Quill reads it, as every other member does."""
    store = Store(tmp_path / "web.db")
    try:
        people = {
            name: store.sign_in(
                "sso", name.lower(), f"{name.lower()}@example.test", name,
                issuer=ISSUER, join="default",
            )
            for name in MAY_CHANGE
        }  # fmt: skip
        ada = store.for_account(people["Ada"].account_id, people["Ada"].user.id)
        ops = ada.create_project("ops", everyone="read")
        levels = {people["Dee"].user.id: "manage", people["Brook"].user.id: "write"}
        ada.set_project_access(ops.id, people=levels)
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:

            def page(name: str, project: str = "ops") -> str:
                client.cookies.set(session_cookie("https"), people[name].token)
                shown = client.get(f"/b/{project}")
                assert shown.status_code == 200, (name, shown.status_code)
                return shown.text

            yield ada, ops, page
    finally:
        store.close()


@pytest.mark.parametrize("state", ["never", "allowed", None], ids=["never", "allowed", "undecided"])
@pytest.mark.parametrize("name", list(MAY_CHANGE))
def test_everyone_who_sees_the_project_reads_the_state_and_what_models_governs(world, state, name):
    ada, ops, page = world
    if state:
        ada.set_project_models(ops.id, state)
    html = page(name)
    assert said(html) == f"{SAID[state]} {GOVERNS}"
    text = visible(html)
    assert text.count(SAID[state]) == 1 and text.count(GOVERNS) == 1
    assert sum(other in text for other in SAID.values()) == 1  # no other state's sentence


@pytest.mark.parametrize("state", ["never", "allowed", None], ids=["never", "allowed", "undecided"])
def test_only_who_may_change_models_is_given_the_form(world, state):
    ada, ops, page = world
    if state:
        ada.set_project_models(ops.id, state)
    offered = {choice for choice in ("allowed", "never") if choice != state}
    for name, may in MAY_CHANGE.items():
        html = page(name)
        found = set(re.findall(r'<button\b[^>]*name="models" value="([^"]+)"', html))
        assert (FORM in html) is may, name
        assert found == (offered if may else set()), name


def test_a_reader_of_personal_notes_reads_what_models_governs_after_how_they_start(world):
    ada, _, page = world
    diary = ada.create_project("diary", kind="personal")
    ada.set_project_access(diary.id, everyone="read")
    whole = f"{SAID['never']} Personal notes start this way. {GOVERNS}"
    for name in ("Ada", "Quill"):
        assert said(page(name, "diary")) == whole
