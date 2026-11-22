"""Unit tests for the HTML error page: its heading is a sentence, built in the web layer from
the message REST and MCP give as it is, and its link suits a reader who is signed out."""

import html

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cairn.web.templating import error_sentence

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


@pytest.mark.parametrize(
    ("status", "message", "sentence"),
    [
        (404, "no project 'hiring'", "There is no project named 'hiring' here."),
        (404, "no page 'retry' in project 'ops'", "There is no page 'retry' in the project 'ops'."),
        (
            404,
            "no version 3 of page 'retry' in project 'ops'",
            "There is no version 3 of the page 'retry' in the project 'ops'.",
        ),
        (
            404,
            "no claim 'cl_1' on page 'retry' in project 'ops'",
            "There is no claim 'cl_1' on the page 'retry' in the project 'ops'.",
        ),
        (404, "Not Found", "There is no page at this address."),
        (405, "Method Not Allowed", "Method not allowed."),
        (
            403,
            "this project is read-only for you: you can read it but not write to it",
            "This project is read-only for you: you can read it but not write to it.",
        ),
        (400, "Choose Approve or Deny.", "Choose Approve or Deny."),
        (429, "Try again in 3 s?", "Try again in 3 s?"),
    ],
)
def test_an_error_heading_is_a_sentence(status, message, sentence):
    assert error_sentence(status, message) == sentence


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        yield client, key
    store.close()


def heading(page: str) -> str:
    return html.unescape(page.split("<h1>", 1)[1].split("</h1>", 1)[0])


def test_the_html_page_says_the_sentence_and_rest_keeps_its_message(env):
    client, key = env
    client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
    page = client.get("/b/hiring")
    assert page.status_code == 404
    assert heading(page.text) == "There is no project named 'hiring' here."
    assert 'href="/">Back to the projects</a>' in page.text
    api = client.get("/api/projects/hiring/pages", headers={"authorization": f"Bearer {key}"})
    assert api.status_code == 404 and api.json() == {"error": "no project 'hiring'"}


def test_a_signed_out_reader_is_sent_to_the_start_page(env):
    client, _ = env
    page = client.get("/no-such-page")
    assert page.status_code == 404
    assert heading(page.text) == "There is no page at this address."
    assert 'href="/">Go to the start page</a>' in page.text
    assert "Back to the projects" not in page.text
