"""A long list of projects can be narrowed without a script (review: UI and UX, suspicion): on
the page that approves an agent, the one that makes a key and the one that makes a set, more
than 20 projects to choose from bring a "Filter projects" field whose button sends the same
form with GET to the same page. Ticked boxes stay ticked and a ticked project is always shown;
the page says how many are shown of how many and links back to all. The filter is applied to
the projects already read, it grants nothing, and its text is neither stored nor logged.
Every name is made up."""

import logging
import re
import sqlite3
from html import unescape
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from starlette.testclient import TestClient

from cairn.narrowing import FILTER_FROM
from cairn.server import create_app
from cairn.store import Store
from support.cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
REDIRECT = "http://127.0.0.1:33418/callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"  # RFC 7636's example, for CHALLENGE
GRANTS = ["authorization_code", "refresh_token"]
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
FIELD = '<label for="filter-projects">Filter projects</label>'
WORDS = ("harbor", "ember", "quill")


def names(count: int) -> list[str]:
    return [f"{WORDS[n % 3]}-{n:02d}" for n in range(count)]


def make_env(tmp_path, count: int):
    db = tmp_path / "cairn.db"
    store = Store(db)
    ids = {name: store.create_project(name).id for name in names(count)}
    client = TestClient(create_app(store, public_url=BASE), base_url=BASE)
    client.__enter__()
    key = store.create_key("owner")
    r = client.post("/login", data={"key": key}, headers=SAME_ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    return SimpleNamespace(client=client, store=store, db=db, ids=ids)


@pytest.fixture
def many(tmp_path):
    env = make_env(tmp_path, FILTER_FROM + 1)
    yield env
    env.client.__exit__(None, None, None)
    env.store.close()


@pytest.fixture
def few(tmp_path):
    env = make_env(tmp_path, FILTER_FROM)
    yield env
    env.client.__exit__(None, None, None)
    env.store.close()


def main_of(html: str) -> str:
    return html.split('<main id="main" tabindex="-1">', 1)[1].split("</main>", 1)[0]


def ticked(html: str) -> set[str]:
    return set(re.findall(r'name="projects" value="([^"]+)" checked', html))


def shown(html: str, env) -> set[str]:
    by_id = {v: k for k, v in env.ids.items()}
    return {by_id[i] for i in re.findall(r'name="projects" value="([^"]+)"', html) if i in by_id}


def consent(env, **query) -> SimpleNamespace:
    """An agent registered and sent to the consent page; with `query`, the page again as the
    filter button sends it (the form's own fields, by GET)."""
    c = env.client
    client_id = c.post(
        "/register",
        json={"client_name": "bot", "redirect_uris": [REDIRECT], "grant_types": GRANTS},
    ).json()["client_id"]
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": "s",
    }
    r = c.get("/authorize?" + urlencode(params), follow_redirects=False)
    page = c.get(r.headers["location"])
    assert page.status_code == 200
    request = re.search(r'name="request" value="([^"]+)"', page.text).group(1)
    return SimpleNamespace(page=page, request=request, client_id=client_id)


# 10. More than 20: the field, the count and the link


def test_the_key_page_offers_a_filter_that_sends_the_same_form_by_get(many):
    main = main_of(many.client.get("/keys").text)
    assert FIELD in main
    assert '<input id="filter-projects" type="search" name="filter" value=""' in main
    button = re.search(r"<button[^>]*>Filter</button>", main).group(0)
    assert 'formmethod="get"' in button and 'formaction="/keys"' in button
    assert "formnovalidate" in button  # the label may still be empty
    assert f"{FILTER_FROM + 1} shown of {FILTER_FROM + 1}" in unescape(main)
    assert "Show all projects" not in main
    assert "<script" not in main and "style=" not in main


def test_a_filter_narrows_by_name_keeps_the_choices_and_shows_the_ticked(many):
    ember = next(n for n in many.ids if n.startswith("ember"))
    one_ticked = next(n for n in many.ids if n.startswith("quill"))
    query = [
        ("label", "night shift"), ("scope", "selected"), ("projects", many.ids[one_ticked]),
        ("access", "read"), ("filter", "HARBOR"),
    ]  # fmt: skip
    main = main_of(many.client.get("/keys?" + urlencode(query)).text)
    harbors = {n for n in many.ids if n.startswith("harbor")}
    assert shown(main, many) == harbors | {one_ticked}  # case-insensitive, and the ticked one
    assert ember not in shown(main, many)
    assert ticked(main) == {many.ids[one_ticked]}
    assert re.search(r'name="scope" value="selected" checked', main)
    assert not re.search(r'name="scope" value="all" checked', main)
    assert re.search(r'name="access" value="read" checked', main)
    label = 'aria-describedby="key-label-hint" value="night shift">'
    assert label in main
    assert 'name="filter" value="HARBOR"' in main
    assert f"{len(harbors) + 1} shown of {FILTER_FROM + 1}" in unescape(main)
    rest = urlencode([pair for pair in query if pair[0] != "filter"])
    assert f'<a href="/keys?{rest.replace("&", "&amp;")}">Show all projects</a>' in main


def test_the_set_page_narrows_the_form_whose_button_was_pressed(many):
    group = many.store.create_set("work", [many.ids["ember-01"]])
    main = main_of(many.client.get("/sets").text)
    assert main.count("Filter projects</label>") == 2  # the new set's form and the set's own
    assert 'id="filter-new"' in main and f'id="filter-{group.id}"' in main
    buttons = re.findall(r"<button[^>]*>Filter</button>", main)
    assert all('formmethod="get"' in b and 'formaction="/sets"' in b for b in buttons)
    assert any(f'name="filtering" value="{group.id}"' in b for b in buttons)
    query = [("projects", many.ids["quill-02"]), ("filter", "harb"), ("filtering", group.id)]
    main = main_of(many.client.get("/sets?" + urlencode(query)).text)
    mine = main.split(f'<section class="set" aria-labelledby="{group.id}">', 1)[1]
    harbors = {n for n in many.ids if n.startswith("harbor")}
    assert shown(mine, many) == harbors | {"quill-02"}
    assert ticked(mine) == {many.ids["quill-02"]}  # what was ticked on that form, not stored
    new = main.split('<section class="new-set"', 1)[1].split("</section>", 1)[0]
    assert shown(new, many) == set(many.ids) and ticked(new) == set()  # the other form: all
    query = [("name", "late"), ("projects", many.ids["ember-01"]), ("filter", "quill"),
             ("filtering", "new")]  # fmt: skip
    main = main_of(many.client.get("/sets?" + urlencode(query)).text)
    new = main.split('<section class="new-set"', 1)[1].split("</section>", 1)[0]
    assert 'value="late"' in new and ticked(new) == {many.ids["ember-01"]}
    quills = {n for n in many.ids if n.startswith("quill")}
    assert shown(new, many) == quills | {"ember-01"}
    mine = main.split(f'<section class="set" aria-labelledby="{group.id}">', 1)[1]
    assert ticked(mine) == {many.ids["ember-01"]} and shown(mine, many) == set(many.ids)


def test_the_consent_page_narrows_and_the_request_still_settles(many):
    pending = consent(many)
    assert FIELD in pending.page.text
    query = [
        ("request", pending.request), ("scope", "selected"),
        ("projects", many.ids["ember-04"]), ("access", "write"), ("filter", "quill"),
    ]  # fmt: skip
    again = many.client.get("/oauth/consent?" + urlencode(query))
    assert again.status_code == 200
    quills = {n for n in many.ids if n.startswith("quill")}
    assert shown(again.text, many) == quills | {"ember-04"}
    assert ticked(again.text) == {many.ids["ember-04"]}
    button = re.search(r"<button[^>]*>Filter</button>", again.text).group(0)
    assert 'formaction="/oauth/consent"' in button and 'formmethod="get"' in button
    request = re.search(r'name="request" value="([^"]+)"', again.text).group(1)
    chosen = {"scope": "selected", "projects": many.ids["ember-04"], "access": "write"}
    r = many.client.post(
        "/oauth/consent",
        data={"request": request, "decision": "approve", "filter": "quill", **chosen},
        headers=SAME_ORIGIN,
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].startswith(REDIRECT)
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    token = many.client.post(
        "/token",
        data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
            "client_id": pending.client_id, "code_verifier": VERIFIER,
        },
    )  # fmt: skip
    assert token.status_code == 200, token.text
    (grant,) = many.store.list_grants()
    assert list(grant.projects) == [many.ids["ember-04"]]


def test_a_consent_request_sealed_for_another_workspace_is_not_shown(many):
    pending = consent(many)
    signed = many.store.sign_in(
        "sso", "sub-cy", "cy@example.test", "Cy", issuer="https://id.example.test/pool-n",
        join="personal",
    )  # fmt: skip
    assert signed.account_id != many.store.account_id
    many.client.cookies.clear()
    many.client.cookies.set(session_cookie(), signed.token)
    query = urlencode([("request", pending.request), ("filter", "quill")])
    assert many.client.get(f"/oauth/consent?{query}").status_code == 400


# 10. Twenty or fewer: nothing changes


@pytest.mark.parametrize("path", ["/keys", "/sets", "consent"])
def test_with_twenty_projects_or_fewer_the_page_is_as_it_was(few, path):
    if path == "consent":
        pending = consent(few)
        plain = pending.page.text
        query = [("request", re.search(r'name="request" value="([^"]+)"', plain).group(1))]
        query = [*query, ("filter", "quill"), ("projects", few.ids["ember-01"])]
        # the page as reached from /authorize, against the same page with a filter's fields
        narrowed = few.client.get("/oauth/consent?" + urlencode(query)).text
        plain = re.sub(r'name="request" value="[^"]+"', "", plain)
        narrowed = re.sub(r'name="request" value="[^"]+"', "", narrowed)
    else:
        few.store.create_set("work", [few.ids["ember-01"]])
        plain = few.client.get(path).text
        query = [("filter", "quill"), ("projects", few.ids["harbor-00"]), ("filtering", "new")]
        narrowed = few.client.get(f"{path}?{urlencode(query)}").text
    assert "Filter projects" not in plain
    assert main_of(narrowed) == main_of(plain)
    assert len(shown(plain, few)) == FILTER_FROM


# 11. What is granted is unchanged; the filter's text is never stored or logged


def dump(db) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def test_the_filter_text_is_neither_stored_nor_logged(many, caplog):
    secret = "zebrafinch"
    with caplog.at_level(logging.DEBUG):
        many.client.get("/keys?" + urlencode({"filter": secret}))
        many.client.get("/sets?" + urlencode({"filter": secret, "filtering": "new"}))
        made = many.client.post(
            "/keys",
            data={"label": "nightly", "filter": secret, "scope": "selected",
                  "projects": many.ids["quill-02"], "access": "read"},
            headers=SAME_ORIGIN,
        )  # fmt: skip
        assert made.status_code == 200
        r = many.client.post(
            "/sets",
            data={"name": "late", "filter": secret, "projects": many.ids["quill-02"]},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert r.status_code == 303
    assert secret not in dump(many.db)
    served = [r for r in caplog.records if not r.name.startswith("httpx")]  # not the client's
    assert served and all(secret not in str(vars(record)) for record in served)
    assert all(secret not in record.getMessage() for record in served)
    (made_key,) = [k for k in many.store.list_keys() if k.label == "nightly"]
    assert list(made_key.projects) == [many.ids["quill-02"]] and made_key.access == "read"


# 12. No new statement


def selects(env, path: str) -> int:
    seen: list[str] = []
    env.store._observe(trace=seen.append)
    try:
        assert env.client.get(path).status_code == 200
    finally:
        env.store._observe(trace=None)
    return len([s for s in seen if s.lstrip().upper().startswith("SELECT")])


def test_a_filter_reads_nothing_more_than_the_page(many):
    group = many.store.create_set("work", [many.ids["ember-01"]])
    pending = consent(many)
    narrowed = {
        "/keys": [("filter", "quill"), ("projects", many.ids["ember-01"])],
        "/sets": [("filter", "quill"), ("filtering", group.id)],
        "/oauth/consent": [("request", pending.request), ("filter", "quill")],
    }
    for path, query in narrowed.items():
        plain = f"{path}?{urlencode(query[:1])}" if path == "/oauth/consent" else path
        selects(many, plain)  # the session and its readers settled first
        assert selects(many, f"{path}?{urlencode(query)}") == selects(many, plain), path
