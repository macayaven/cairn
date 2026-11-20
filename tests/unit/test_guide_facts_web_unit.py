"""Sentences of the user guides (src/cairn/guides/*.md) that are asked of the running service,
over HTTP, the REST API and MCP, each pinned here so the guide cannot go false unnoticed. The
guide's sentence is in each test's docstring. The app runs in process over a real store in a
temporary file; sign-in providers are stand-ins; nothing leaves the process."""

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from cairn import server
from cairn.identity import GitHub
from cairn.plans import Plan, Plans
from cairn.server import SESSION_COOKIE, SignIn, create_app
from cairn.store import Store
from cairn.web.signin import SIGN_INS

BASE = "http://127.0.0.1:8765"
ISSUER = "https://id.example.test/pool-1"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
METADATA = "/.well-known/oauth-authorization-server"
REDIRECT = "http://127.0.0.1:33418/callback"
NOVEMBER = datetime(2026, 11, 10, 12, tzinfo=UTC)
INITIALIZE = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "guide-facts", "version": "0"},
}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def rpc(client, key: str, method: str, params=None, path: str = "/mcp", **headers):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    sent = {**MCP_HEADERS, "x-api-key": key, **headers}
    return client.post(path, headers=sent, json=body, follow_redirects=False)


def tool(client, key: str, name: str, /, **arguments) -> dict:
    r = rpc(client, key, "tools/call", {"name": name, "arguments": arguments})
    assert r.status_code == 200
    return r.json()["result"]


def person(store: Store, name: str, *, join: str = "default") -> Store:
    signed = store.sign_in(
        "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER, join=join
    )
    return store.for_account(signed.account_id, signed.user.id)


def cookie(token: str) -> dict[str, str]:
    return {"cookie": f"{SESSION_COOKIE}={token}"}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


@pytest.fixture
def team(store):
    """Ada owns the default workspace, Brook administers it, Cy and Dee are members. Each
    has a session; Brook has a key."""
    people = {name.lower(): person(store, name) for name in ("Ada", "Brook", "Cy", "Dee")}
    people["ada"].set_role(people["brook"].user_id, "admin")
    sessions = {name: view.create_session() for name, view in people.items()}
    return SimpleNamespace(store=store, sessions=sessions, **people)


# connect.md: the address and the transport


def test_mcp_answers_only_at_slash_mcp_and_not_at_the_bare_host_or_api(store):
    """connect.md, "If it does not connect": "The URL must end in `/mcp`. The bare host or
    `/api` will not work.\""""
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url="https://cairn.example.test"),
                    base_url="https://cairn.example.test") as client:  # fmt: skip
        answered = rpc(client, key, "initialize", INITIALIZE)
        assert answered.status_code == 200 and "result" in answered.json()
        for path in ("/", "/api", "/api/"):
            r = rpc(client, key, "initialize", INITIALIZE, path=path)
            assert r.status_code in (303, 404, 405), (path, r.status_code)
            assert '"jsonrpc"' not in r.text and "protocolVersion" not in r.text, path
            if r.status_code == 303:
                assert r.headers["location"].startswith("/login"), path


def test_a_private_network_host_works_as_the_public_url_or_an_allowed_host(store):
    """connect.md, "If it does not connect": "A private-network URL (localhost, VPN, Tailnet)
    works for clients running on a machine on that network." It does when that host is the
    server's public URL (then sign-in through the browser is on, over https) or one of its
    allowed hosts (keys only, without a public https URL)."""
    key = store.create_key("laptop")
    tailnet = "https://cairn.tailnet.example"
    with TestClient(create_app(store, public_url=tailnet), base_url=tailnet) as client:
        assert rpc(client, key, "tools/list").status_code == 200
        metadata = client.get(METADATA)
        assert metadata.status_code == 200 and metadata.json()["issuer"] == tailnet
    lan = "http://nas.lan:8765"
    with TestClient(create_app(store, allowed_hosts=["nas.lan:8765"]), base_url=lan) as client:
        assert rpc(client, key, "tools/list").status_code == 200
        assert client.get(METADATA).status_code == 404
        assert rpc(client, key, "tools/list", host="other.lan:8765").status_code == 421


def test_over_plain_http_on_another_host_sign_in_is_off_and_a_key_still_works(store):
    """connect.md, "If it does not connect": "Over plain http on any other host, sign-in is
    off and a key would cross the network unprotected.\""""
    key = store.create_key("laptop")
    plain = "http://cairn.example.net"
    with TestClient(create_app(store, public_url=plain), base_url=plain) as client:
        assert client.get(METADATA).status_code == 404
        assert client.post("/register", json={}).status_code == 404
        listed = rpc(client, key, "tools/list")
        assert listed.status_code == 200 and listed.json()["result"]["tools"]


# patterns.md: the project named after the repository


def test_the_instructions_an_agent_gets_tell_it_to_write_in_the_project_named_after_the_repo(
    store,
):
    """patterns.md, "Many agents, many projects": "They pick the project named after the
    repository they are working in; cairn's instructions tell them to.\""""
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        r = rpc(client, key, "initialize", INITIALIZE)
    assert (
        "Write about a repository in the project named after it."
        in r.json()["result"]["instructions"]
    )


# patterns.md: a credential that acts for no person reaches a private project


def test_a_key_that_acts_for_no_person_reaches_a_private_project_an_admin_does_not(team):
    """patterns.md, "Private projects": "Admins don't reach it unless the rule names them. A
    credential that acts for no person reaches it too: a key made on the server's command line
    with `key-create` ...\""""
    store = team.store
    journal = team.cy.create_project("journal", everyone="none")
    nightly = store.create_key("nightly")  # what key-create makes: a key of no person
    admin_key = team.brook.create_key("brook-ci")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        listed = client.get("/api/projects", headers={"x-api-key": nightly}).json()["projects"]
        assert "journal" in [b["name"] for b in listed]
        written = client.post(
            f"/api/projects/{journal.id}/notes",
            headers={"x-api-key": nightly},
            json={"text": "# Night run\n\nThe nightly job finished at 3 am.\n"},
        )
        assert written.status_code == 201
        projects = json.loads(tool(client, nightly, "projects")["content"][0]["text"])["projects"]
        assert "journal" in [b["name"] for b in projects]
        theirs = client.get("/api/projects", headers={"x-api-key": admin_key}).json()["projects"]
        assert "journal" not in [b["name"] for b in theirs]
        hidden = client.get(f"/api/projects/{journal.id}/pages", headers={"x-api-key": admin_key})
        assert hidden.status_code == 404
    assert len(store.list_notes(journal.id)) == 1


# patterns.md: who names the people of a private project, and how


def test_the_maker_an_owner_or_a_manager_names_people_one_or_several_at_a_time(team):
    """patterns.md, "Private projects": "Its maker, an owner, or anyone its rule gives Read and
    write, and decide who reaches it, names people on the project's Who reaches it page, one at
    a time or several at once by email address.\""""
    store, sessions = team.store, team.sessions
    journal = team.cy.create_project("journal", everyone="none")
    team.cy.set_project_access(journal.id, people={team.dee.user_id: "manage"})
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        for who in ("cy", "ada", "dee"):
            project = client.get("/b/journal", headers=cookie(sessions[who])).text
            assert '<a href="/b/journal/access">Who reaches it</a>' in project, who
            page = client.get("/b/journal/access", headers=cookie(sessions[who])).text
            assert 'name="people"' in page and 'action="/b/journal/share"' in page, who
        assert client.get("/b/journal/access", headers=cookie(sessions["brook"])).status_code == 404

        def post(who: str, path: str, data: dict):
            headers = {**cookie(sessions[who]), **SAME_ORIGIN}
            return client.post(path, data=data, headers=headers, follow_redirects=False)

        one = [f"{team.dee.user_id}:manage", f"{team.brook.user_id}:read"]
        assert post("dee", "/b/journal/access", {"people": one}).status_code == 303
        several = {"emails": "brook@example.test, ada@example.test", "level": "write"}
        assert post("dee", "/b/journal/share", several).status_code == 303
    assert team.cy.project_access(journal.id).people == {
        team.dee.user_id: "manage",
        team.brook.user_id: "write",
    }


# security.md: signing in


def test_a_browser_session_lasts_twelve_hours(tmp_path):
    """security.md, "Signing in": "A browser session lasts 12 hours, unless the operator keeps
    sign-ins longer with `serve --session-days`" (the longer case:
    test_guide_facts_b1_unit.py)."""
    clock = Clock(NOVEMBER)
    store = Store(tmp_path / "cairn.db", clock=clock)
    try:
        key = store.create_key("laptop")
        app = create_app(store, public_url=BASE, clock=clock)
        with TestClient(app, base_url=BASE) as client:
            login = client.post(
                "/login", data={"key": key}, headers=SAME_ORIGIN, follow_redirects=False
            )
            assert login.status_code == 303
            assert "max-age=43200" in login.headers["set-cookie"].lower()
            clock.now = NOVEMBER + timedelta(hours=12) - timedelta(seconds=1)
            assert client.get("/", follow_redirects=False).status_code == 200
            clock.now = NOVEMBER + timedelta(hours=12)
            ended = client.get("/", follow_redirects=False)
            assert ended.status_code == 303 and ended.headers["location"].startswith("/login")
    finally:
        store.close()


def test_one_flood_of_sign_ins_leaves_the_people_already_signed_in_working(store):
    """security.md, "What cairn leaves to the operator": "while it lasts, new sign-ins can be
    refused. People already signed in are not affected.\""""
    ada = person(store, "Ada")
    session = ada.create_session()
    key = store.create_key("laptop")
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    app = create_app(
        store,
        public_url="https://cairn.example.test",
        sign_in=SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http),
    )

    async def flood() -> None:
        transport = httpx.ASGITransport(app=app, client=("203.0.113.5", 40000))
        async with httpx.AsyncClient(
            transport=transport, base_url="https://cairn.example.test"
        ) as browser:
            answers = [(await browser.get("/auth/github")).status_code for _ in range(SIGN_INS)]
            assert set(answers) == {302}
            refused = await browser.get("/auth/github")
            assert refused.status_code == 429  # the address's allowance is spent
            assert (await browser.get("/", headers=cookie(session))).status_code == 200
            api = await browser.get("/api/projects", headers={"x-api-key": key})
            assert api.status_code == 200
            assert (await browser.get("/auth/github")).status_code == 429  # still flooding

    asyncio.run(flood())


# security.md: agents and keys


def approve(team, view: Store, name: str) -> str:
    """`view`'s person approves an agent called `name`; the id of the grant it gets."""
    agent = team.store.register_client(name, [REDIRECT])
    assert team.store.redeem_code(view.create_code(agent.id, REDIRECT, "c" * 43), agent.id)
    (grant,) = [g for g in team.store.list_grants() if g.client_name == name]
    return grant.id


def test_a_member_sees_and_ends_only_their_own_agents_and_an_admin_or_owner_all(team):
    """security.md, "Agents and keys": "Under Workspace, the Agents page lists the agents a
    person approved, with what each reaches, and disconnects any of them. An owner or an
    admin sees every agent in the workspace there." patterns.md says the same."""
    grants = {
        name: approve(team, getattr(team, name), f"{name}-agent")
        for name in ("ada", "brook", "cy", "dee")
    }
    with TestClient(create_app(team.store, public_url=BASE), base_url=BASE) as client:

        def page(who: str) -> tuple[set[str], set[str]]:
            text = client.get("/agents", headers=cookie(team.sessions[who])).text
            shown = set(re.findall(r"\b(?:ada|brook|cy|dee)-agent\b", text))
            ends = set(re.findall(r'href="/agents/([^"/]+)/revoke"', text))
            return shown, ends

        def end(who: str, grant: str) -> int:
            return client.post(
                f"/agents/{grant}/revoke",
                headers={**SAME_ORIGIN, **cookie(team.sessions[who])},
                follow_redirects=False,
            ).status_code

        assert page("cy") == ({"cy-agent"}, {grants["cy"]})
        assert end("cy", grants["dee"]) == 404  # another member's agent is as unknown
        assert grants["dee"] in {g.id for g in team.store.list_grants()}
        everyone = {"ada-agent", "brook-agent", "cy-agent", "dee-agent"}
        for manager in ("brook", "ada"):
            assert page(manager) == (everyone, set(grants.values())), manager
        assert end("brook", grants["dee"]) == 303  # an admin ends another person's agent
        assert end("ada", grants["brook"]) == 303  # and so does an owner
        assert end("cy", grants["cy"]) == 303  # a member ends their own
        assert {g.id for g in team.store.list_grants()} == {grants["ada"]}


def test_a_key_made_on_the_command_line_makes_and_revokes_keys_in_a_shared_workspace(
    tmp_path, capsys
):
    """security.md, "Agents and keys": "A key made on the server's command line acts for no
    person: it reaches every project and manages keys.\""""
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        ada = person(store, "Ada")
        person(store, "Cy")  # a member: the workspace is shared
        ada.create_key("ada-laptop")
        out = tmp_path / "nightly.env"
        assert server.main(["key-create", "--db", str(db), "--label", "nightly",
                            "--out", str(out)]) == 0  # fmt: skip
        capsys.readouterr()
        nightly = out.read_text().strip().partition("=")[2]
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            login = client.post(
                "/login", data={"key": nightly}, headers=SAME_ORIGIN, follow_redirects=False
            )
            assert login.status_code == 303
            made = client.post("/keys", data={"label": "ci-runner"}, headers=SAME_ORIGIN)
            assert made.status_code == 200
            assert "ci-runner" in [k.label for k in store.list_keys()]
            for label in ("ci-runner", "ada-laptop"):  # its own, and a person's
                revoked = client.post(
                    f"/keys/{label}/revoke", headers=SAME_ORIGIN, follow_redirects=False
                )
                assert revoked.status_code == 303, label
        assert sorted(k.label for k in store.list_keys()) == ["nightly"]
    finally:
        store.close()


# security.md: the web pages


def directives(policy: str) -> dict[str, list[str]]:
    parsed = {}
    for part in policy.split(";"):
        words = part.split()
        if words:
            parsed[words[0].lower()] = words[1:]
    return parsed


def test_no_page_runs_a_script_and_styles_images_and_fonts_come_from_cairn_only(store):
    """security.md, "The web pages": "No page runs a script. The pages allow styles, images
    and fonts from cairn itself and nothing else, and cannot be shown inside another site's
    frame.\""""
    key = store.create_key("laptop")
    store.create_project("ops")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        public = client.get("/docs/security")
        client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        for r in (public, client.get("/"), client.get("/b/ops")):
            assert r.status_code == 200, r.url
            policy = directives(r.headers["content-security-policy"])
            assert policy["default-src"] == ["'none'"], r.url
            assert not {"script-src", "script-src-elem", "script-src-attr"} & set(policy), r.url
            assert policy["style-src"] == ["'self'"] and policy["img-src"] == ["'self'"], r.url
            assert policy["font-src"] == ["'self'"], r.url
            assert policy["frame-ancestors"] == ["'none'"], r.url
            assert "<script" not in r.text.lower(), r.url


def test_the_typefaces_licences_are_where_the_guide_says(store):
    """security.md, "The web pages": "The two typefaces, Atkinson Hyperlegible Next and
    Atkinson Hyperlegible Mono, ship with cairn under the SIL Open Font License 1.1; their
    licences are served at `/static/fonts/atkinson-hyperlegible-next.OFL.txt` and
    `/static/fonts/atkinson-hyperlegible-mono.OFL.txt`.\""""
    guide = (files("cairn") / "guides" / "security.md").read_text(encoding="utf-8")
    (line,) = [line for line in guide.splitlines() if "SIL Open Font License 1.1" in line]
    addresses = re.findall(r"`(/static/fonts/[^`]+)`", line)
    assert sorted(addresses) == [
        "/static/fonts/atkinson-hyperlegible-mono.OFL.txt",
        "/static/fonts/atkinson-hyperlegible-next.OFL.txt",
    ]
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        for address in addresses:  # no session, like the stylesheet and the fonts
            r = client.get(address)
            assert r.status_code == 200, address
            assert r.headers["content-type"].startswith("text/plain"), address
            assert "SIL OPEN FONT LICENSE Version 1.1" in r.text, address
            family = "Next" if "next" in address else "Mono"
            assert f"Atkinson Hyperlegible {family}" in line, address
            assert f"Atkinson Hyperlegible {family}" in r.text.splitlines()[0], address


def test_a_pages_address_is_not_sent_to_other_sites(team):
    """security.md, "The web pages": "a page's address is not sent to other sites.\""""
    store = team.store
    store.create_project("ops")
    invitation = team.ada.invite("eve@example.test")
    allowed = {"same-origin", "no-referrer"}  # neither sends any of the address elsewhere
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        for path in ("/", "/search?q=retries", "/b/ops", "/docs/privacy"):
            r = client.get(path, headers=cookie(team.sessions["ada"]))
            assert r.status_code == 200, path
            assert r.headers["referrer-policy"] in allowed, path
        joining = client.get(f"/join/{invitation.token}", headers=cookie(team.sessions["cy"]))
        assert joining.status_code == 200 and "eve@example.test" in joining.text
        assert joining.headers["referrer-policy"] == "no-referrer"  # the token is its address
        # Signed out, the invitee is sent to sign in first, at an address that still holds
        # the token (in `next`): that page too keeps its address from other sites.
        away = client.get(f"/join/{invitation.token}", follow_redirects=False)
        assert away.status_code == 303 and away.headers["location"].startswith("/login?next=")
        login = client.get(away.headers["location"])
        assert login.status_code == 200 and login.headers["referrer-policy"] in allowed


# security.md: only the operator adds a source


def test_no_web_rest_or_mcp_route_adds_a_source(store):
    """security.md, "What cairn leaves to the operator": "A source's credentials, such as a
    Langfuse key, are environment variables of the server, so only the command line
    registers an account, or adds or removes a source from a config; owners and admins make
    sources from a registered account." No route under a
    path that names sources takes anything but GET; a source made from an account (R2) is
    added under /api/integrations or from the Sources page (/integrations/new), from an
    account only the command line registers."""
    owner = person(store, "Ada")
    session = owner.create_session()
    key = owner.create_key("ada-laptop")
    store.create_project("ops")
    config = {"name": "traces", "kind": "langfuse", "config": {"projects": ["ops"]}}
    app = create_app(store, public_url=BASE)

    def walk(routes, prefix=""):
        for route in routes:
            yield prefix + getattr(route, "path", ""), getattr(route, "methods", None)
            yield from walk(getattr(route, "routes", []) or [], prefix + getattr(route, "path", ""))

    for path, methods in walk(app.routes):
        if re.search(r"\bsources?\b", path):
            assert methods is not None and methods <= {"GET", "HEAD"}, (path, methods)
    with TestClient(app, base_url=BASE) as client:
        form = {**config, "config": json.dumps(config["config"])}
        for path in ("/sources", "/sources/new", "/sources/traces"):  # as the owner, signed in
            r = client.post(
                path, data=form, headers={**SAME_ORIGIN, **cookie(session)}, follow_redirects=False
            )
            assert r.status_code in (404, 405), (path, r.status_code)
        for path in ("/api/sources", "/api/sources/traces"):  # with the owner's key
            r = client.post(path, json=config, headers={"x-api-key": key})
            assert r.status_code in (404, 405), (path, r.status_code)
        tools = rpc(client, key, "tools/list").json()["result"]["tools"]
        assert [t["name"] for t in tools if "source" in t["name"]] == []
    assert store.list_sources() == []


# security.md: what a member learns at the cap on projects


def test_a_member_refused_a_project_at_the_cap_learns_which_limit_and_no_number_or_plan(team):
    """security.md, "Who reaches what": "when a cap refuses their note they learn which limit,
    without a number or the plan." and "a member who is refused a new project at that cap ...
    can tell that others exist." The refusal names the limit, nothing more."""
    store = team.store
    team.ada.create_project("handbook")
    team.ada.set_role(team.cy.user_id, "admin")
    cy_key = team.cy.create_key("cy-laptop")
    team.ada.set_role(team.cy.user_id, "member")
    plans = Plans({"pebble": Plan("Starter", projects=1)}, default="pebble")  # no shared part
    with TestClient(create_app(store, public_url=BASE, plans=plans), base_url=BASE) as client:
        r = client.post("/api/projects", headers={"x-api-key": cy_key}, json={"name": "diary"})
        assert r.status_code == 402
        body = r.json()
        assert set(body) == {"error", "limit"} and body["limit"] == "projects"
        refused = tool(client, cy_key, "new_project", name="diary")
        said = refused["content"][0]["text"]
        assert refused["isError"] is True and "project" in said
        form = client.post(
            "/projects",
            data={"name": "diary"},
            headers={**SAME_ORIGIN, **cookie(team.sessions["cy"])},
        )
        assert form.status_code == 402
        (alert,) = re.findall(r'role="alert"[^>]*>(.*?)</', form.text, re.S)
        for told in (body["error"], said, alert):
            assert "project" in told.lower(), told
            assert not re.search(r"\d", told), told
            assert "pebble" not in told.lower() and "starter" not in told.lower(), told
    assert [b.name for b in store.list_projects()] == ["handbook"]


# sources.md, "Sources the server reads" (S2)


def test_no_agent_adds_a_source_and_a_member_sees_only_their_own(team):
    """sources.md: "... a person makes sources from an account on the Sources page
    ([below](#sources-made-from-an-account)). No agent adds one." ... "An owner or an admin
    opens Workspace, then Sources, and switches a source on or off there. A member sees
    there only the sources of their own account.\""""
    app = create_app(team.store, public_url=BASE)

    def routes(found, prefix=""):
        for route in found:
            path = prefix + getattr(route, "path", "")
            if hasattr(route, "routes"):
                yield from routes(route.routes, path)
            else:
                yield path, set(getattr(route, "methods", None) or ())

    about = re.compile(r"/sources\b|/integrations/")
    writes = [
        path
        for path, methods in routes(app.routes)
        if about.search(path) and methods - {"GET", "HEAD"}
    ]
    # The switches, and a preview (R1) on the page and over REST, which stores nothing; over
    # REST (R2), a key also switches, removes, and adds a source made from an account
    # (POST /api/integrations), and so does the Sources page (/integrations/new, with its
    # own remove and disconnect, each asked first): no agent adds one.
    assert sorted(writes) == [
        "/api/integrations/preview",  # R4: a preview before saving, which stores nothing
        "/api/integrations/{name}",
        "/api/integrations/{name}/off",
        "/api/integrations/{name}/on",
        "/api/integrations/{name}/preview",
        "/integrations/accounts/{name}/disconnect",
        "/integrations/new",
        "/integrations/{name}/off",
        "/integrations/{name}/on",
        "/integrations/{name}/preview",
        "/integrations/{name}/remove",
    ]
    adds = [path for path, methods in routes(app.routes) if "POST" in methods]
    assert [path for path in adds if path.rstrip("/").endswith("integrations")] == [
        "/api/integrations"
    ]  # under /api, which takes a key and never an agent's token
    key = team.store.create_key("guide")
    with TestClient(app, base_url=BASE) as client:
        names = [t["name"] for t in rpc(client, key, "tools/list").json()["result"]["tools"]]
        assert not [name for name in names if "source" in name]
        assert client.get("/sources", headers=cookie(team.sessions["brook"])).status_code == 200
        member = client.get("/sources", headers=cookie(team.sessions["cy"]))
        assert member.status_code == 200  # R4: a member with no account: no source, no form
        assert "<form" not in member.text.partition("<main ")[2].partition("</main>")[0]
        workspace = client.get("/workspace", headers=cookie(team.sessions["brook"])).text
        assert 'href="/sources"' in workspace


# security.md and run.md: the graph, with and without `serve --graph-explorer` (G2)


def guide_says(name: str, sentence: str) -> None:
    whole = " ".join((files("cairn") / "guides" / name).read_text(encoding="utf-8").split())
    assert sentence in whole, sentence


def graph_world(store, pages: int = 3) -> SimpleNamespace:
    """ "ops" with `pages` pages and one note that the first page's claim cites; "vault",
    private to Ada, its owner. Cy is a member. The key reaches every project."""
    from cairn.store import Claim

    people = {name.lower(): person(store, name) for name in ("Ada", "Cy")}
    ops = people["ada"].create_project("ops", everyone="write")
    vault = people["ada"].create_project("vault", everyone="none")
    note, _ = people["ada"].add_note(ops.id, "Quill retries 3 times.\n", source_ref="desk:1")
    span = people["ada"].note_spans(note.id)[0].id
    for i in range(pages):
        cited = (span,) if i == 0 else ()
        claims = [Claim(id="retries", text="Quill retries 3 times.", span_ids=cited)]
        people["ada"].save_page(ops.id, f"p-{i:03}", f"Quill page {i}", "", claims)
    people["ada"].save_page(vault.id, "hidden", "Juniper page", "", [])
    return SimpleNamespace(
        note=note,
        key=store.create_key("graph-reader"),
        sessions={name: view.create_session() for name, view in people.items()},
    )


def graph_markup(html: str) -> SimpleNamespace:
    """A graph page's scripts, its drawing's nodes, its list's nodes, whether its controls
    are hidden, and its "Next batch" links."""
    from html.parser import HTMLParser

    seen = SimpleNamespace(scripts=[], svg=[], listed=[], hidden=[], next=[])

    class Parser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "script":
                seen.scripts.append(a)
            if "data-graph-node" in a:
                where = seen.svg if a.get("data-graph-area") == "svg" else seen.listed
                where.append(a["data-graph-node"])
            if "data-graph-controls" in a:
                seen.hidden.append("hidden" in a)
            if tag == "a" and a.get("rel") == "next":
                seen.next.append(a["href"].replace("&amp;", "&"))

    Parser().feed(html)
    return seen


def test_with_the_graph_explorer_the_graph_page_alone_loads_cairns_own_script(store):
    """security.md, "The web pages": "Every page except a project's **Graph** page runs no
    script. By default `serve` loads one script on that page alone, cairn's own, by a nonce
    made for that page and with its hash, and the page still works without it; with `serve
    --no-graph-explorer` no page runs a script.\""""
    import base64
    import hashlib

    guide_says(
        "security.md",
        "Every page except a project's **Graph** page runs no script. By default `serve` loads"
        " one script on that page alone, {{name}}'s own, by a nonce made for that page and with"
        " its hash, and the page still works without it; with `serve --no-graph-explorer` no"
        " page runs a script.",
    )
    world = graph_world(store)
    own = (files("cairn") / "static" / "graph-view.js").read_bytes()  # shipped in the package
    digest = "sha256-" + base64.b64encode(hashlib.sha256(own).digest()).decode()
    paths = ("/", "/b/ops", "/b/ops/graph", "/b/ops/graph?focus=notes", "/docs/security")
    for explorer in (False, True):
        app = create_app(store, public_url=BASE, graph_explorer=explorer)
        with TestClient(app, base_url=BASE) as client:
            note_page = f"/b/ops/graph/notes/{world.note.id}"
            for path in (*paths, note_page):
                r = client.get(path, headers=cookie(world.sessions["ada"]))
                policy = directives(r.headers["content-security-policy"])
                shown = graph_markup(r.text)
                graph_page = path == "/b/ops/graph" or path.startswith("/b/ops/graph?")
                if not (explorer and graph_page):
                    assert "script-src" not in policy and not shown.scripts, (explorer, path)
                    assert r.status_code == (404 if path == note_page and not explorer else 200)
                    continue
                (tag,) = shown.scripts
                assert tag["src"] == "/static/graph-view.js" and tag["integrity"] == digest
                assert policy["script-src"] == [f"'nonce-{tag['nonce']}'"], path
                assert client.get(tag["src"]).content == own  # this server's own copy
                # It works without the script: everything drawn is listed, and the
                # controls only the script uses are hidden until it shows them.
                assert shown.svg and sorted(shown.svg) == sorted(shown.listed), path
                assert shown.hidden == [True], path


def test_the_graph_is_drawn_by_the_server_and_listed_again_in_both_states(store):
    """security.md, "The web pages": "A project's page links its **Graph**: its pages, the
    claims on them, the passages those claims cite and the notes the passages come from,
    drawn by the server and listed again as text below the drawing. By default under `serve`
    it shows one view at a time (the pages, the notes, a page's claims or a claim's
    passages), at most 20 to a batch, with **Next batch** for the rest, and each note has a
    page of its own; with `--no-graph-explorer` the page shows at most 20 pages on one static
    sheet. A key reads at most 20 pages of the graph at `GET /api/graph?project=<name>`, an
    agent at the MCP resource `graph://project/<name>/<limit>`, and a project out of reach is
    answered as one that does not exist.\""""
    guide_says(
        "security.md",
        "drawn by the server and listed again as text below the drawing. By default under"
        " `serve` it shows one view at a time (the pages, the notes, a page's claims or a"
        " claim's passages), at most 20 to a batch, with **Next batch** for the rest, and each"
        " note has a page of its own; with `--no-graph-explorer` the page shows at most 20"
        " pages on one static sheet. A key reads at most 20 pages of the graph at"
        " `GET /api/graph?project=<name>`, an agent at the MCP resource"
        " `graph://project/<name>/<limit>`, and a project out of reach is answered as one that"
        " does not exist.",
    )
    world = graph_world(store, pages=23)
    ada, cy = cookie(world.sessions["ada"]), cookie(world.sessions["cy"])

    def pages_in(html: str) -> int:
        return sum(1 for node in graph_markup(html).listed if node.startswith("page:"))

    for explorer in (False, True):
        app = create_app(store, public_url=BASE, graph_explorer=explorer)
        with TestClient(app, base_url=BASE) as client:
            assert 'href="/b/ops/graph"' in client.get("/b/ops", headers=ada).text
            first = client.get("/b/ops/graph", headers=ada)
            shown = graph_markup(first.text)
            assert "<svg" in first.text and sorted(shown.svg) == sorted(shown.listed)
            assert pages_in(first.text) == 20
            if explorer:
                (following,) = shown.next
                assert pages_in(client.get(following, headers=ada).text) == 3
                for view, kind in (("notes", "note:"), ("page%3Ap-000", "claim:")):
                    listed = graph_markup(
                        client.get(f"/b/ops/graph?focus={view}", headers=ada).text
                    ).listed
                    assert any(node.startswith(kind) for node in listed), view
                note = client.get(f"/b/ops/graph/notes/{world.note.id}", headers=ada)
                assert note.status_code == 200 and "Quill retries 3 times." in note.text
            else:
                assert not shown.next
            api = client.get("/api/graph?project=ops", headers={"x-api-key": world.key})
            assert api.json()["counts"]["page"] == 20
            read = rpc(client, world.key, "resources/read", {"uri": "graph://project/ops/20"})
            graph = json.loads(read.json()["result"]["contents"][0]["text"])
            assert graph["counts"]["page"] == 20
            for path in ("/b/vault/graph", "/b/vault/graph?focus=notes"):
                hidden, missing = (
                    client.get(path, headers=cy),
                    client.get(path.replace("vault", "nowhere"), headers=cy),
                )
                assert hidden.status_code == missing.status_code == 404, path
                assert "Juniper" not in hidden.text


def test_run_md_says_what_the_graph_explorer_switches_on(store, tmp_path, monkeypatch, capsys):
    """run.md, "A graph to explore": "By default `serve` makes a project's **Graph** page
    one to explore: its pages or its notes a batch of at most 20 at a time, with **Next
    batch** for the rest, a search within the view, a page's claims, the passages a claim
    cites, and a page for each note. That page is the only one that runs a script, cairn's
    own, and it works without it. To run no script on any page, pass `--no-graph-explorer`:
    the graph page is then drawn by the server alone and runs no script (`--graph-explorer`
    is still accepted).\" The batches, the views and the script are pinned by the two tests
    above; here, the options and the search."""
    import uvicorn

    from cairn.web import cli

    guide_says(
        "run.md",
        "By default `serve` makes a project's **Graph** page one to explore: its pages or its"
        " notes a batch of at most 20 at a time, with **Next batch** for the rest, a search"
        " within the view, a page's claims, the passages a claim cites, and a page for each"
        " note. That page is the only one that runs a script, cairn's own, and it works without"
        " it. To run no script on any page, pass `--no-graph-explorer`: the graph page is then"
        " drawn by the server alone and runs no script (`--graph-explorer` is still accepted).",
    )
    made = []

    def note(store, **settings):
        made.append(settings["graph_explorer"])
        raise SystemExit(0)

    monkeypatch.setattr(cli, "create_app", note)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    for argv in ([], ["--graph-explorer"], ["--no-graph-explorer"]):
        with pytest.raises(SystemExit):
            server.main(["serve", "--db", str(tmp_path / "kb.db"), *argv])
    assert made == [True, True, False]
    with pytest.raises(SystemExit):
        server.main(["serve", "--help"])
    helped = capsys.readouterr().out
    assert "--graph-explorer" in helped and "--no-graph-explorer" in helped
    world = graph_world(store, pages=3)
    with TestClient(create_app(store, public_url=BASE, graph_explorer=True), base_url=BASE) as c:
        found = c.get("/b/ops/graph?q=page%202", headers=cookie(world.sessions["ada"])).text
        assert "Quill page 2" in found and "Quill page 1" not in found
