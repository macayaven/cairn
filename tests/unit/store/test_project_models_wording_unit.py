"""The project page says what Models governs (review finding Privacy 4): this server's own
model that arranges pages, and not the assistants people connect, which read what they reach
and send it to their own provider. One plain sentence, in both states. Fixtures are synthetic."""

import re
from html import unescape

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store

BASE = "https://cairn.example.test"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
GOVERNS = (
    "Models decides only what this server's own model, the one that arranges pages, is sent;"
    " the assistants people connect read what they reach and send it to their own provider."
)


def visible(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split()).replace(" .", ".")


@pytest.mark.parametrize("state", ["allowed", "never"])
def test_the_project_page_says_what_models_governs_in_both_states(tmp_path, state):
    store = Store(tmp_path / "web.db")
    try:
        key = store.create_key("laptop")
        project = store.create_project("ops")
        store.set_project_models(project.id, state)
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            shown = client.get("/b/ops")
        assert shown.status_code == 200
        text = visible(shown.text)
        assert text.count(GOVERNS) == 1, text
        assert text.index(f"Models: {state}.") < text.index(GOVERNS)
        assert "style=" not in shown.text.split('class="row models"', 1)[1].split("</div>", 1)[0]
    finally:
        store.close()
