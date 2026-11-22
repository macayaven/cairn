"""The sentences the run guide says about several browsers and several sign-ins under way
(B2), in "Under your own name, behind the sign-in", each pinned here so the guide cannot go
false unnoticed. Each test finds its sentence in the guide, then asks the app. The app runs in
process over a real store in a temporary file; the identity provider is a stand-in; nothing
leaves the process."""

import re
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web.signin import STATE_TTL, UNDER_WAY
from support.cookie_names import session_cookie

RUN = files("cairn.guides").joinpath("run.md").read_text(encoding="utf-8")
BASE = "https://kb.example.test"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


def flat(text: str) -> str:
    return " ".join(text.split())


def paragraph(text: str, lead: str) -> str:
    """The paragraph that starts with the bold `lead`."""
    found = [p for p in text.split("\n\n") if p.startswith(f"**{lead}**")]
    assert len(found) == 1, lead
    return flat(found[0])


SEVERAL = paragraph(RUN, "Several browsers.")


def says(sentence: str) -> None:
    assert flat(sentence) in SEVERAL, sentence


def github(request: httpx.Request) -> httpx.Response:
    url = str(request.url).split("?")[0]
    if url == "https://github.com/login/oauth/access_token":
        return httpx.Response(200, json={"access_token": "gho_made_up"})
    if url == "https://api.github.com/user":
        return httpx.Response(200, json={"id": 9201, "login": "ada", "name": "Ada Quill"})
    if url == "https://api.github.com/user/emails":
        emails = [{"email": "ada@example.test", "primary": True, "verified": True}]
        return httpx.Response(200, json=emails)
    return httpx.Response(404)


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "kb.db")
    http = httpx.AsyncClient(transport=httpx.MockTransport(github))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    app = create_app(store, public_url=BASE, sign_in=sign_in, wall=True, brand_name="Juniper")
    with TestClient(app, base_url=BASE, follow_redirects=False) as first:
        yield store, lambda: TestClient(app, base_url=BASE, follow_redirects=False), first


def start(browser: TestClient) -> str:
    r = browser.get("/auth/github")
    assert r.status_code == 302
    return parse_qs(urlsplit(r.headers["location"]).query)["state"][0]


def back(browser: TestClient, state: str) -> httpx.Response:
    return browser.get(f"/auth/github/callback?code=c&state={state}")


def signed_in(browser: TestClient) -> bool:
    return browser.get("/keys").status_code == 200


def test_a_person_is_signed_in_on_several_browsers_at_once(world):
    """The run guide says: "A person can be signed in on several browsers and machines at
    once, each with a sign-in of its own"."""
    says("A person can be signed in on several browsers and machines at once, each with a")
    says("sign-in of its own")
    store, browser, laptop = world
    phone = browser()
    tokens = []
    for each in (laptop, phone):
        r = back(each, start(each))
        assert r.status_code == 200
        tokens.append(each.cookies.get(session_cookie("https", branded=True)))
    assert tokens[0] != tokens[1] and all(store.resolve_session(t) for t in tokens)
    assert signed_in(laptop) and signed_in(phone)
    assert laptop.post("/logout", headers=SAME_ORIGIN).status_code == 303
    assert not signed_in(laptop) and signed_in(phone)


def test_several_sign_ins_can_be_under_way_in_one_browser(world):
    """The run guide says: "several sign-ins can be under way in one browser, in two tabs
    say: up to five at a time, each for ten minutes"."""
    says("several sign-ins can be under way in one browser, in two tabs say: up to five at a")
    says("time, each for ten minutes.")
    assert UNDER_WAY == 5 and STATE_TTL == 600
    _, _, browser = world
    first, second = start(browser), start(browser)  # two tabs
    assert back(browser, second).status_code == 200
    assert back(browser, first).status_code == 200


def test_the_refusal_signs_nobody_in_or_out_and_starting_again_signs_in(world):
    """The run guide says: "When the sign-in page says "This sign-in is not valid any more",
    nobody was signed in or out by it: starting again signs the person in, and every sign-in
    they already have, in that browser or any other, stays.\""""
    says('When the sign-in page says "This sign-in is not valid any more", nobody was signed')
    says("in or out by it: starting again signs the person in, and every sign-in they already")
    says("have, in that browser or any other, stays.")
    _, browser, laptop = world
    phone = browser()
    assert back(laptop, start(laptop)).status_code == 200  # signed in on the laptop
    assert back(phone, start(phone)).status_code == 200  # and on the phone
    used = start(phone)
    assert back(phone, used).status_code == 200

    for each, state in ((phone, used), (laptop, "a-state-never-issued"), (browser(), used)):
        r = back(each, state)
        assert r.status_code == 400 and "This sign-in is not valid any more" in r.text
        assert not r.headers.get_list("set-cookie")  # no cookie set or cleared
    assert signed_in(laptop) and signed_in(phone)

    fresh = browser()  # the one refused with no sign-in at all: starting again signs in
    assert back(fresh, "a-state-never-issued").status_code == 400
    r = back(fresh, start(fresh))
    assert r.status_code == 200 and signed_in(fresh)
    assert re.search(r"http-equiv=[\"']refresh", r.text)
