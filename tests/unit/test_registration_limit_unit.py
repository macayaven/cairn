"""How many agents one client address may register in an hour is the operator's to set.

The apps of some vendors register from the vendor's own servers, so many people's agents can
arrive from a few addresses: the limit that suits a home install is too low for a hosted one.
"""

import pytest
import uvicorn
from starlette.testclient import TestClient

from cairn import server
from cairn.server import create_app
from cairn.store import Store

BASE = "https://cairn.example.test"
CLIENT = {"client_name": "agent", "redirect_uris": ["http://127.0.0.1:33418/callback"]}


def registrations(app, tries: int) -> list[int]:
    with TestClient(app, base_url=BASE, follow_redirects=False) as raw:
        return [raw.post("/register", json=CLIENT).status_code for _ in range(tries)]


@pytest.fixture
def store(tmp_path):
    made = Store(tmp_path / "cairn.db")
    yield made
    made.close()


def test_one_address_registers_twenty_agents_an_hour_unless_told(store):
    app = create_app(store, public_url=BASE)
    assert app.state.registrations_per_hour == 20
    assert registrations(app, 21) == [201] * 20 + [429]


def test_the_operator_sets_another_number(store):
    app = create_app(store, public_url=BASE, registrations_per_hour=3)
    assert app.state.registrations_per_hour == 3
    with TestClient(app, base_url=BASE, follow_redirects=False) as raw:
        answers = [raw.post("/register", json=CLIENT) for _ in range(4)]
    assert [a.status_code for a in answers] == [201, 201, 201, 429]
    assert answers[-1].headers["retry-after"] == "3600"
    assert answers[-1].json()["error"] == "too_many_requests"


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "20"])
def test_the_number_is_a_whole_number_of_at_least_one(store, bad):
    with pytest.raises(ValueError):
        create_app(store, public_url=BASE, registrations_per_hour=bad)


def test_serve_takes_the_number_on_the_command_line(tmp_path, monkeypatch):
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    serve = ["serve", "--db", str(tmp_path / "serve.db"), "--public-url", BASE]

    assert server.main(serve) == 0
    assert server.main([*serve, "--registrations-per-hour", "500"]) == 0

    assert [app.state.registrations_per_hour for app in apps] == [20, 500]
    for bad in ("0", "-3", "many", "1.5", "nan"):
        with pytest.raises(SystemExit):
            server.main([*serve, "--registrations-per-hour", bad])
