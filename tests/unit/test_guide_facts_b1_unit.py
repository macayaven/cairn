"""The sentences the docs say about the wall, the brand and a remembered sign-in (B1), each
pinned here so a doc cannot go false unnoticed: the run guide's "Under your own name, behind
the sign-in", deploy/README.md's "Only the people you name", docs/pre-release.md's "Who can get
in" and the README's sign-in section. Each test finds its sentence in the document, then asks
the app, the command line or the parser. The app runs in process over a real store in a
temporary file; the identity provider is a stand-in; nothing leaves the process."""

import json
import re
import shlex
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from cairn import server
from cairn.arrange import tidy_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web import cli

ROOT = Path(__file__).resolve().parents[2]
RUN = files("cairn.guides").joinpath("run.md").read_text(encoding="utf-8")
DEPLOY = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
PRE_RELEASE = (ROOT / "docs" / "pre-release.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
BASE = "https://kb.example.test"
BRAND = "Knowledge Base"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 32">'
    b'<text x="4" y="22" fill="#1b2a24">Juniper Field</text></svg>\n'
)
START = datetime(2099, 11, 14, 9, 0, tzinfo=UTC)  # ahead of the real clock, as the frozen file's
FIVE = ("--wall", "--brand-name", "--brand-logo", "--brand-logo-alt", "--session-days")


def flat(text: str) -> str:
    return " ".join(text.split())


def section(text: str, heading: str) -> str:
    """The text under `heading` up to the next heading of the same or a higher level (a line
    in a fenced block is never a heading: `# A hosted service` in a bash block is a comment)."""
    level = len(heading.split(" ", 1)[0])
    lines = text[text.index(f"\n{heading}\n") + len(heading) + 2 :].splitlines()
    fenced, kept = False, []
    for line in lines:
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(rf"#{{1,{level}}} ", line):
            break
        kept.append(line)
    return "\n".join(kept)


THE_SECTION = section(RUN, "## Under your own name, behind the sign-in")


def says(text: str, sentence: str) -> None:
    assert flat(sentence) in flat(text), sentence


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


class GitHubFake:
    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_made_up"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={"id": 7, "login": "ada", "name": "Ada Quill"})
        if url == "https://api.github.com/user/emails":
            emails = [{"email": "ada@example.test", "primary": True, "verified": True}]
            return httpx.Response(200, json=emails)
        return httpx.Response(404)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    made = Store(tmp_path / "kb.db", clock=clock)
    yield made
    made.close()


def app(store, **settings):
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake()))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    return create_app(store, public_url=BASE, sign_in=sign_in, **settings)


def browser(made) -> TestClient:
    return TestClient(made, base_url=BASE, follow_redirects=False)


def sign_in(client: TestClient) -> httpx.Response:
    started = client.get("/auth/github")
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    back = client.get(f"/auth/github/callback?code=c&state={state}")
    assert back.status_code == 200, back.text
    return back


def cookie_names(response: httpx.Response) -> set[str]:
    return {line.split("=", 1)[0] for line in response.headers.get_list("set-cookie")}


def max_age(response: httpx.Response, name: str) -> int:
    for line in response.headers.get_list("set-cookie"):
        if line.startswith(name + "="):
            return int(re.search(r"max-age=(\d+)", line, re.I).group(1))
    raise AssertionError(f"no {name} cookie")


def call_tool(client: TestClient, key: str, name: str, **arguments: object) -> dict:
    """The result of one MCP tools/call, made with the API key `key`."""
    body = {"jsonrpc": "2.0", "id": 9, "method": "tools/call"}
    body["params"] = {"name": name, "arguments": arguments}
    answer = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
    assert answer.status_code == 200, answer.text
    return answer.json()["result"]


def initialize(client: TestClient, **credential: str) -> httpx.Response:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "guide-facts", "version": "0"},
        },
    }
    return client.post("/mcp", headers={**MCP_HEADERS, **credential}, json=body)


# The run guide: the five options


def test_the_run_guide_lists_the_five_options_and_each_needs_sign_in(tmp_path, monkeypatch, capsys):
    """The run guide says:

    "Each of them needs `--sign-in`. A value cairn cannot use, or an option that would do
    nothing, stops `serve` at start, and the last line it prints names the option."
    """
    says(THE_SECTION, "Each of them needs `--sign-in`.")
    says(THE_SECTION, "the last line it prints names the option.")
    for option in FIVE:
        assert f"| `{option}" in THE_SECTION, option
    logo = tmp_path / "logo.svg"
    logo.write_bytes(SVG)
    monkeypatch.setattr(cli, "create_app", lambda *a, **k: pytest.fail("the app was made"))
    values = {
        "--brand-name": [BRAND],
        "--brand-logo": [str(logo)],
        "--brand-logo-alt": ["Juniper Field"],
        "--session-days": ["30"],
    }
    for option in FIVE:
        argv = ["serve", "--db", str(tmp_path / "kb.db"), "--public-url", BASE, option]
        try:
            code = server.main([*argv, *values.get(option, [])])
        except SystemExit as stopped:
            code = stopped.code
        assert code == 2, option
        assert option in capsys.readouterr().err.strip().splitlines()[-1], option


@pytest.mark.parametrize(
    ("argv", "option"),
    [
        (["--brand-name", "K" * 61], "--brand-name"),
        (["--brand-name", "Knowledge <Base>"], "--brand-name"),
        (["--brand-name", BRAND, "--brand-logo", "BIG"], "--brand-logo"),
        (["--brand-logo", "LOGO"], "--brand-logo"),
        (["--brand-name", BRAND, "--brand-logo-alt", "Juniper Field"], "--brand-logo-alt"),
        (["--session-days", "0"], "--session-days"),
        (["--session-days", "366"], "--session-days"),
    ],
)
def test_the_run_guides_limits_are_the_ones_serve_holds(
    tmp_path, monkeypatch, capsys, argv, option
):
    """The run guide's table: a name of "1 to 60 characters with no `<` or `>`", a logo "of
    at most 256 KB" that "needs `--brand-name`", an alternative that "needs `--brand-logo`",
    and "from 1 to 365" days. Past each, `serve` stops (exit 2) and its last line names the
    option."""
    for said in (
        "1 to 60 characters with no `<` or `>`",
        "of at most 256 KB, on the sign-in page and in each page's frame. It needs `--brand-name`.",
        "It needs `--brand-logo`.",
        "A sign-in lasts N days, from 1 to 365",
    ):
        says(THE_SECTION, said)
    logo, big = tmp_path / "logo.svg", tmp_path / "big.svg"
    logo.write_bytes(SVG)
    big.write_bytes(SVG.replace(b"</svg>", b"<!--" + b"x" * 256 * 1024 + b"--></svg>"))
    argv = [{"LOGO": str(logo), "BIG": str(big)}.get(word, word) for word in argv]
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_ID", "gh-id")
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_SECRET", "made-up")
    monkeypatch.setattr(cli, "create_app", lambda *a, **k: pytest.fail("the app was made"))
    start = ["serve", "--db", str(tmp_path / "kb.db"), "--public-url", BASE, "--sign-in", "github"]
    try:
        code = server.main([*start, *argv])
    except SystemExit as stopped:
        code = stopped.code
    assert code == 2
    assert option in capsys.readouterr().err.strip().splitlines()[-1]


def test_the_run_guides_command_is_one_serve_takes():
    """The run guide's `docker run` example: the words after the image are a command the real
    parser takes, with the five options' values as the guide gives them."""
    block = THE_SECTION.split("```bash\n", 1)[1].split("```", 1)[0]
    words = shlex.split(block.replace("\\\n", " "))
    command = words[words.index("cairn:local") + 1 :]
    parsed = cli._parser().parse_args(command)
    assert command[0] == "serve" and parsed.wall is True
    assert (parsed.brand_name, parsed.brand_logo, parsed.session_days) == (
        BRAND,
        Path("/brand/logo.svg"),
        30,
    )
    assert '-v "$PWD/logo.svg:/brand/logo.svg:ro"' in block


# The run guide: what the wall shows


def test_the_wall_sends_pages_to_the_sign_in_and_leaves_machines_alone(store):
    """The run guide says:

    "A browser that is not signed in and asks for any page, including one a later version
    adds, is sent to the sign-in page; a form it posts gets a one-line refusal. What agents and
    scripts use answers as it does without the wall: `/mcp`, `/api`, `/healthz`, the OAuth
    endpoints and their `/.well-known/` addresses. [...] Every answer to a browser that is not
    signed in, except what agents and scripts use, carries `X-Robots-Tag: noindex`."
    """
    says(THE_SECTION, "is sent to the sign-in page; a form it posts gets a one-line refusal.")
    says(THE_SECTION, "`/mcp`, `/api`, `/healthz`, the OAuth endpoints and their `/.well-known/`")
    says(THE_SECTION, "except what agents and scripts use, carries `X-Robots-Tag: noindex`")
    walled, open_ = browser(app(store, wall=True)), browser(app(store))
    for address in ("/", "/docs/run", "/no-page-yet"):
        r = walled.get(address)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), address
        assert r.headers["x-robots-tag"] == "noindex", address
    posted = walled.post("/b/core/compose", data={"text": "x"}, headers=SAME_ORIGIN)
    assert posted.status_code == 401 and len(posted.text.splitlines()) == 1
    for method, address in (
        ("POST", "/mcp"),
        ("GET", "/api/projects"),
        ("GET", "/healthz"),
        ("GET", "/.well-known/oauth-authorization-server"),
        ("GET", "/.well-known/oauth-protected-resource/mcp"),
        ("POST", "/token"),
    ):
        a, b = walled.request(method, address), open_.request(method, address)
        assert (a.status_code, a.content) == (b.status_code, b.content), address
        assert "x-robots-tag" not in a.headers, address
    page = walled.get("/login")
    assert page.status_code == 200 and page.headers["x-robots-tag"] == "noindex"
    # "The sign-in page, the sign-in addresses under `/auth/` and the files under `/static/`
    # stay open too"
    says(THE_SECTION, "the sign-in addresses under `/auth/` and the files under `/static/`")
    to_provider = walled.get("/auth/github")
    assert to_provider.status_code == 302
    assert to_provider.headers["location"].startswith("https://github.com/login/oauth/authorize")
    sheet = re.search(r'<link rel="stylesheet" href="(/static/[^"]+)"', page.text).group(1)
    assert walled.get(sheet).status_code == 200


# The run guide: what the brand covers and what it does not


def test_the_brand_covers_what_a_signed_out_browser_gets_and_the_frame(store, tmp_path):
    """The run guide says:

    "With `--brand-name`, nothing a browser that is not signed in can get names cairn. [...]
    There is no landing page, and the guides are for people signed in.
    Once signed in, a person sees your name in each page's title, in the bar at the top and in
    the footer, and your logo, if you gave one, in the bar and as the browser tab's icon."
    """
    says(THE_SECTION, "With `--brand-name`, nothing a browser that is not signed in can get")
    says(THE_SECTION, "There is no landing page, and the guides are for people signed in.")
    logo = tmp_path / "logo.svg"
    logo.write_bytes(SVG)
    client = browser(app(store, brand_name=BRAND, brand_logo=logo))
    for address in ("/", "/docs", "/docs/run"):
        r = client.get(address)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), address
    signin = client.get("/login")
    assert "cairn" not in signin.text.lower()
    assert f'alt="{BRAND}"' in signin.text  # "--brand-logo-alt": "Without it, the brand name."
    says(THE_SECTION, "What the logo says, for people who cannot see it. Without it, the brand")
    sign_in(client)
    home = client.get("/workspace")
    assert home.status_code == 200
    head, body = home.text.split("</head>", 1)
    assert re.search(rf"<title>[^<]* · {BRAND}</title>", head)
    assert '<link rel="icon" href="/static/logo-' in head
    masthead = body.split("</header>", 1)[0]
    assert BRAND in masthead and 'src="/static/logo-' in masthead
    assert BRAND in body.split('<footer class="colophon">', 1)[1]


def test_under_a_brand_people_signed_in_and_their_agents_are_told_the_brands_name(store):
    """The run guide says (B3), in the table: `--brand-name NAME` is "Your name [...] in place
    of cairn's wherever a browser that is not signed in can look, and in what people signed
    in and their agents are told." And:

    "The pages and the guides say your name where they would say cairn's. A command or a
    config that adds the server to an agent names the connection after your name in lower
    case, each run of other characters a dash (`knowledge-base` for "Knowledge Base"), and
    an export's file name starts with that same name. An agent is told your name too: the
    MCP server gives it as its name and as the subject of its instructions, and no tool's
    description names cairn. What a person or an agent wrote, such as a note, is shown as
    they wrote it. They still see cairn's name where it must be typed exactly, in commands:
    the module in `python -m cairn.server`, variables such as `CAIRN_API_KEY`, and the files
    `cairn.db` and `cairn.env`. An agent still meets it where machines read it: its tokens
    start with `cairn.at.` and `cairn.rt.`, a refusal over MCP or the REST API names the
    note guard's rule `cairn_credential`, and a page tidied without a model says
    `"arranged_by": "cairn"` in its metrics, as it is recorded. This guide is not shown
    under a brand: `/docs/run` answers as a guide that does not exist, and no page links it
    [...]. The logs, the command line and the files on your machine keep cairn's name."
    """
    for said in (
        "in place of cairn's wherever a browser that is not signed in can look, and in what"
        " people signed in and their agents are told.",
        "The pages and the guides say your name where they would say cairn's.",
        "names the connection after your name in lower case, each run of other characters a"
        ' dash (`knowledge-base` for "Knowledge Base"), and an export\'s file name starts with'
        " that same name.",
        "the MCP server gives it as its name and as the subject of its instructions, and no"
        " tool's description names cairn.",
        "What a person or an agent wrote, such as a note, is shown as they wrote it.",
        "They still see cairn's name where it must be typed exactly, in commands: the module in"
        " `python -m cairn.server`, variables such as `CAIRN_API_KEY`, and the files"
        " `cairn.db` and `cairn.env`.",
        "An agent still meets it where machines read it: its tokens start with `cairn.at.` and"
        " `cairn.rt.`, a refusal over MCP or the REST API names the note guard's rule"
        ' `cairn_credential`, and a page tidied without a model says `"arranged_by": "cairn"`'
        " in its metrics, as it is recorded.",
        "This guide is not shown under a brand: `/docs/run` answers as a guide that does not"
        " exist, and no page links it",
        "The logs, the command line and the files on your machine keep cairn's name.",
    ):
        says(THE_SECTION, said)
    client = browser(app(store, brand_name=BRAND))
    sign_in(client)
    welcome = client.get("/welcome").text
    assert f"Choose what you will use {BRAND} for" in welcome
    assert "claude mcp add --transport http knowledge-base " in welcome
    connect = client.get("/docs/connect").text
    assert f"<h1>Connect an agent to {BRAND}</h1>" in connect
    assert "codex mcp add knowledge-base --url" in connect and "$CAIRN_API_KEY" in connect
    sources = client.get("/docs/sources").text  # typed exactly
    assert "python -m cairn.server source-add --db cairn.db" in sources
    assert "deploy/cairn.env" in sources
    assert client.get("/docs/run").status_code == 404
    assert 'href="/docs/run"' not in client.get("/docs").text
    export = client.get("/account/export")
    assert 'filename="knowledge-base-' in export.headers["content-disposition"]
    account = store.resolve_session(client.cookies["__Host-session"]).account_id
    view = store.for_account(account)
    view.create_project("trail")
    key = view.create_key("an-agent")
    mine = "Ada built a cairn of flat stones at the pass.\n"  # a person's own word for it
    with TestClient(app(store, brand_name=BRAND), base_url=BASE) as agent:
        told = initialize(agent, **{"x-api-key": key}).json()["result"]
        listed = agent.post(
            "/mcp",
            headers={**MCP_HEADERS, "x-api-key": key},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        noted = agent.post("/api/projects/trail/notes", headers={"x-api-key": key},
                           json={"text": mine, "topic": "ridge"})  # fmt: skip
        page = agent.get(f"/api/projects/trail/pages/{noted.json()['page']}",
                         headers={"x-api-key": key})  # fmt: skip
        token = "cairn.at." + "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zAaB3dE5gH7jK"
        refused = agent.post("/api/projects/trail/notes", headers={"x-api-key": key},
                             json={"text": f"use {token}\n"})  # fmt: skip
        mcp_refused = call_tool(agent, key, "note", project="trail", text=f"use {token}\n")
        trail = view.get_project("trail").id
        for text in ("# Nightly\n\nIt runs.\n", "# Nightly\n\nIt runs.\n\n- It logs.\n"):
            compile_note(view, view.add_note(trail, text)[0], gate=lint_gate)
        assert tidy_page(view, trail, "nightly").status == "arranged"
        tidied = call_tool(agent, key, "page", project="trail", slug="nightly")
    assert told["serverInfo"]["name"] == BRAND and told["instructions"].startswith(BRAND + " ")
    assert "cairn" not in told["instructions"].lower()
    assert listed.status_code == 200 and "cairn" not in listed.text.lower()
    assert mine.strip() in page.text  # as written
    assert refused.status_code == 422 and "cairn_credential" in refused.text
    assert "cairn" not in refused.text.replace("cairn_credential", "").lower()
    assert mcp_refused["isError"] and "cairn_credential" in mcp_refused["content"][0]["text"]
    assert "cairn" not in json.dumps(mcp_refused).replace("cairn_credential", "").lower()
    answer = json.loads(tidied["content"][0]["text"])
    assert answer["metrics"].pop("arranged_by") == "cairn"  # as recorded (arrange.TIDY)
    assert "cairn" not in json.dumps(answer).lower()


# The run guide: what changes name


def test_under_a_brand_the_cookies_and_the_scope_have_other_names(store):
    """The run guide says:

    "Under a brand the session cookie is `__Host-session` in place of `cairn_session`, the
    cookie that carries a sign-in to the provider and back is `__Host-signin` in place of
    `cairn_signin`, and the OAuth scope is `memory` in place of `cairn`. The `__Host-` prefix
    keeps other hosts under your domain from setting either cookie; when `--public-url` is
    plain http, as on localhost, browsers refuse the prefix and the cookies are `session` and
    `signin`. So the day you switch a brand on, or off, everyone signed in signs in once
    more."
    """
    says(THE_SECTION, "the session cookie is `__Host-session` in place of `cairn_session`")
    says(THE_SECTION, "is `__Host-signin` in place of `cairn_signin`, and the OAuth scope is")
    says(THE_SECTION, "The `__Host-` prefix keeps other hosts under your domain from setting")
    says(THE_SECTION, "browsers refuse the prefix and the cookies are `session` and `signin`.")
    says(THE_SECTION, "everyone signed in signs in once more.")
    local = "http://127.0.0.1:8765"
    for settings, session, state, scope, url in (
        ({}, "cairn_session", "cairn_signin", "cairn", BASE),
        ({"brand_name": BRAND}, "__Host-session", "__Host-signin", "memory", BASE),
        ({"brand_name": BRAND}, "session", "signin", "memory", local),
    ):
        http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake()))
        sign_in_ = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
        made = create_app(store, public_url=url, sign_in=sign_in_, **settings)
        client = TestClient(made, base_url=url, follow_redirects=False)
        assert state in cookie_names(client.get("/auth/github"))
        assert session in cookie_names(sign_in(client))
        metadata = client.get("/.well-known/oauth-authorization-server").json()
        assert metadata["scopes_supported"] == [scope]
    plain = browser(app(store))
    sign_in(plain)
    branded = browser(app(store, brand_name=BRAND))
    branded.cookies.set("cairn_session", plain.cookies["cairn_session"])
    r = branded.get("/workspace")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_an_agent_connected_without_a_brand_keeps_working_under_one(store):
    """The run guide says:

    "Agents keep working: cairn takes either name of the scope from an agent, whichever it
    connected with." An agent's grant made on the server with no brand: under the brand its
    access token reads over /mcp, and its refresh token is taken asking the old name."""
    says(THE_SECTION, "Agents keep working: cairn takes either name of the scope from an agent")
    plain = browser(app(store))
    sign_in(plain)
    account = store.resolve_session(plain.cookies["cairn_session"]).account_id
    registered = plain.post(
        "/register", json={"client_name": "an-agent", "redirect_uris": ["http://127.0.0.1:1/cb"]}
    ).json()
    assert registered["scope"] == "cairn"
    view = store.for_account(account)
    code = view.create_code(registered["client_id"], "http://127.0.0.1:1/cb", "c" * 43)
    pair = view.redeem_code(code, registered["client_id"])
    # "An agent's tokens still start with `cairn.at.` and `cairn.rt.`"
    assert pair.access_token.startswith("cairn.") and pair.refresh_token.startswith("cairn.")
    with TestClient(app(store, brand_name=BRAND), base_url=BASE) as branded:
        auth = {"authorization": f"Bearer {pair.access_token}"}
        assert initialize(branded, **auth).status_code == 200
        refreshed = branded.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": pair.refresh_token,
                "client_id": registered["client_id"],
                "scope": "cairn",
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["scope"] == "memory"
        assert refreshed.json()["access_token"].startswith("cairn.")  # under the brand too
        auth = {"authorization": f"Bearer {refreshed.json()['access_token']}"}
        assert initialize(branded, **auth).status_code == 200


# The run guide: how long a sign-in lasts


def test_a_sign_in_lasts_12_hours_or_n_days_renewed_by_use(store, clock):
    """The run guide says:

    "Without `--session-days`, a sign-in lasts 12 hours and use does not renew it. With
    `--session-days N`, it lasts N days, and a use more than a day after it was made or last
    renewed makes it last N days from then. So with `--session-days 30` a browser used at
    least once every four weeks stays signed in, across restarts of the server; with
    `--session-days 1` that use never comes in time, and a sign-in lasts one day."
    (Four weeks, not 29 days: a use at exactly a day renews nothing, and the next must come
    before the 30 days from the last renewal run out.)
    """
    says(THE_SECTION, "Without `--session-days`, a sign-in lasts 12 hours and use does not renew")
    says(THE_SECTION, "a use more than a day after it was made or last renewed makes it last N")
    says(THE_SECTION, "used at least once every four weeks stays signed in, across restarts")
    says(THE_SECTION, "with `--session-days 1` that use never comes in time, and a sign-in lasts")
    plain = browser(app(store))
    assert max_age(sign_in(plain), "cairn_session") == 12 * 3600
    clock.now += timedelta(hours=11)
    assert "cairn_session" not in cookie_names(plain.get("/workspace"))
    clock.now += timedelta(hours=2)
    assert plain.get("/workspace").status_code == 303  # ran out, never renewed
    clock.now = START
    remembered = browser(app(store, session_days=30))
    assert max_age(sign_in(remembered), "cairn_session") == 30 * 86_400
    clock.now += timedelta(days=1)
    assert "cairn_session" not in cookie_names(remembered.get("/workspace"))  # not more than
    for _ in range(3):  # used every four weeks, well past the first 30; a new app is a restart
        clock.now += timedelta(weeks=4)
        again = browser(app(store, session_days=30))
        again.cookies.set("cairn_session", remembered.cookies["cairn_session"])
        r = again.get("/workspace")
        assert r.status_code == 200 and max_age(r, "cairn_session") == 30 * 86_400
    clock.now = START
    one_day = browser(app(store, session_days=1))
    sign_in(one_day)
    clock.now += timedelta(hours=23)
    assert "cairn_session" not in cookie_names(one_day.get("/workspace"))  # not renewed
    clock.now += timedelta(hours=1)
    assert one_day.get("/workspace").status_code == 303  # a day after the sign-in: over


def test_lowering_session_days_cuts_the_sign_ins_already_made(tmp_path, monkeypatch, capsys):
    """The run guide says:

    "When you lower `--session-days` or remove it, `serve` cuts every sign-in at start so that
    it ends at most the new length from then; it never lengthens one, and keys and agents are
    not touched." `serve --help` says it where it describes the option."""
    says(THE_SECTION, "When you lower `--session-days` or remove it, `serve` cuts every sign-in")
    says(THE_SECTION, "it ends at most the new length from then; it never lengthens one, and")
    with pytest.raises(SystemExit):
        server.main(["serve", "--help"])
    helped = flat(capsys.readouterr().out)
    assert "At start, a sign-in made under a longer length is cut to end at most" in helped
    db = tmp_path / "served.db"
    made = Store(db)  # the real clock, as serve's
    try:
        year = made.sign_in("github", "7", "ada@example.test", "Ada", issuer="https://github.com",
                            join="personal", ttl_seconds=365 * 86_400)  # fmt: skip
        key = made.for_account(year.account_id, year.user.id).create_key("laptop")
    finally:
        made.close()
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_ID", "gh-id")
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_SECRET", "made-up")
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **options: None)
    argv = ["serve", "--db", str(db), "--public-url", BASE, "--sign-in", "github"]
    assert server.main([*argv, "--session-days", "7"]) == 0
    after = Store(db, clock=lambda: datetime.now(UTC) + timedelta(days=7, minutes=1))
    try:
        assert after.resolve_session(year.token) is None  # cut to 7 days from the start
        assert after.resolve_key(key) is not None
    finally:
        after.close()


def test_the_security_and_privacy_guides_and_the_sign_in_page_say_the_same_length(store):
    """security.md: "A browser session lasts 12 hours, unless the operator keeps sign-ins
    longer with `serve --session-days`; then it lasts that many days, and a use more than a
    day after it was made or last renewed starts them again." privacy.md: "12 hours, or as
    many days as the operator set with `serve --session-days`, counted again from a use more
    than a day after it was made or last renewed". The sign-in page says the length of the
    cookie it sets. (The renewal itself: test_a_sign_in_lasts_12_hours_or_n_days_renewed_by_use.)
    """
    guides = files("cairn.guides")
    says(
        guides.joinpath("security.md").read_text(encoding="utf-8"),
        "A browser session lasts 12 hours, unless the operator keeps sign-ins longer with"
        " `serve --session-days`; then it lasts that many days, and a use more than a day"
        " after it was made or last renewed starts them again.",
    )
    says(
        guides.joinpath("privacy.md").read_text(encoding="utf-8"),
        "| A browser session | 12 hours, or as many days as the operator set with"
        " `serve --session-days`, counted again from a use more than a day after it was made"
        " or last renewed |",
    )
    week = "valid for 7 days; a visit more than a day after it was set or last renewed starts"
    for settings, seconds, said in (
        ({}, 12 * 3600, "valid for 12 hours."),
        ({"session_days": 7}, 7 * 86_400, week + " the 7 days again."),
    ):
        client = browser(app(store, **settings))
        assert said in client.get("/login").text
        assert max_age(sign_in(client), "cairn_session") == seconds


# Ending a sign-in: the run guide, deploy/README.md, docs/pre-release.md, README.md


def session_end_words(text: str, prefix: str) -> list[str]:
    found = re.search(re.escape(prefix) + r" python -m cairn\.server (session-end [^`\n]+)", text)
    assert found, prefix
    return shlex.split(found.group(1))


@pytest.mark.parametrize(
    ("document", "prefix", "address"),
    [
        (THE_SECTION, "docker exec cairn", "ada@example.test"),
        (section(DEPLOY, "### Only the people you name"), "docker compose exec cairn", None),
        (section(PRE_RELEASE, "## Who can get in"), "sudo docker exec cairn", None),
    ],
)
def test_each_session_end_command_the_docs_give_is_one_the_parser_takes(document, prefix, address):
    """The run guide, deploy/README.md ("Only the people you name") and docs/pre-release.md
    ("Who can get in") give `session-end` as it is run there: the parser takes it, with the
    database on the volume."""
    words = session_end_words(document, prefix)
    parsed = cli._parser().parse_args(words)
    assert parsed.db == Path("/data/cairn.db")
    if address:
        assert parsed.email == address
    assert "--email person@example.com" in PRE_RELEASE


@pytest.mark.parametrize(
    "document",
    [
        THE_SECTION,
        section(DEPLOY, "### Only the people you name"),
        section(PRE_RELEASE, "## Who can get in"),
    ],
)
def test_session_end_ends_the_sessions_and_leaves_keys_and_agents(
    document, store, capsys, tmp_path
):
    """The deploy guide and the runbook says:

    "[session-end] ends every session of that address in every workspace and prints how
    many; end their keys and agents on the **Keys** and **Agents** pages." (In the run guide:
    "it ends their sign-ins in every workspace and prints how many. Their keys and agents go
    on working until you end them on the **Keys** and **Agents** pages.")"""
    said = flat(document)
    assert "in every workspace and prints how many" in said
    assert "**Keys** and **Agents** pages" in said
    client = browser(app(store, session_days=30))
    sign_in(client)
    token = client.cookies["cairn_session"]
    account = store.resolve_session(token).account_id
    key = store.for_account(account).create_key("laptop")
    db = str(tmp_path / "kb.db")  # the store fixture's file
    code = server.main(["session-end", "--db", db, "--email", "ADA@example.test"])
    printed = capsys.readouterr().out
    assert code in (0, None) and printed == "ended 1 web session of that address\n"
    assert store.resolve_session(token) is None
    assert store.resolve_key(key) is not None


def test_a_session_keeps_the_address_its_sign_in_was_vouched_with(store):
    """privacy.md, "What cairn stores": "A browser session also keeps the email address the
    sign-in provider gave when the person signed in, so that the operator can end it by that
    address." A person whose provider now gives another address than the one they first
    signed in with is ended by the new one; their stored address stays the first."""
    says(
        files("cairn.guides").joinpath("privacy.md").read_text(encoding="utf-8"),
        "A browser session also keeps the email address the sign-in provider gave when the"
        " person signed in, so that the operator can end it by that address.",
    )
    first = store.sign_in("github", "7", "ada.first@example.test", "Ada",
                          issuer="https://github.com", join="personal")  # fmt: skip
    client = browser(app(store))
    sign_in(client)  # GitHubFake gives ada@example.test for the same account, 7
    token = client.cookies["cairn_session"]
    assert store.resolve_session(token).user_id == first.user.id
    assert store.end_person_sessions("ada@example.test") == {first.account_id: 1}
    assert store.resolve_session(token) is None
    assert store.get_user(first.user.id).email == "ada.first@example.test"


def test_with_session_days_an_open_session_outlives_the_list():
    """deploy/README.md and docs/pre-release.md: "With `--session-days`, an open session can
    go on for as long as that browser is used." The list gates sign-in only (pinned by
    tests/acceptance/test_h8_allowed_emails.py); the renewal by use is pinned by
    test_a_sign_in_lasts_12_hours_or_n_days_renewed_by_use."""
    sentence = (
        "With `--session-days`, an open session can go on for as long as that browser is used."
    )
    says(section(DEPLOY, "### Only the people you name"), sentence)
    says(section(PRE_RELEASE, "## Who can get in"), sentence)


# deploy/README.md: the options with Compose


def test_the_compose_line_gives_serve_the_options_as_one_argument_each():
    """The deploy guide says:

    "add the run guide's five options [...] to `CAIRN_EXTRA_ARGS`. Put quotes round a name
    with a space in it; Compose keeps it one argument." Compose splits a string command as a
    shell would (Docker Compose v5.1.2's `docker compose config` was seen to give
    `--brand-name`, `Knowledge Base`); shlex does the same here, and the parser takes it."""
    part = section(DEPLOY, "### Only the people you name")
    says(part, "Put quotes round a name with a space in it; Compose keeps it one argument.")
    line = next(x for x in part.splitlines() if x.startswith("CAIRN_EXTRA_ARGS="))
    extra = shlex.split(line.partition("=")[2])
    parsed = cli._parser().parse_args(["serve", "--db", "/data/cairn.db", *extra])
    assert parsed.brand_name == BRAND and parsed.brand_logo == Path("/data/logo.svg")
    assert parsed.wall is True and parsed.session_days == 30 and parsed.sign_in == ["github"]
    assert "docker compose restart cairn" in part
    # The logo goes where --brand-logo reads it, written as cairn's own user (as the operator
    # notes are: a file `docker compose cp` puts there keeps your own owner and mode).
    written = re.search(r"docker compose exec -T cairn sh -c 'cat > (\S+)' < logo\.svg", part)
    assert written and Path(written.group(1)) == parsed.brand_logo
    says(DEPLOY, "docker compose exec -T cairn sh -c 'cat > /data/operator-notes.md'")
    for found in re.findall(r"docker compose [^`\n]+", part):
        assert "laptop.env" not in found and "/data/plans.yaml" not in found, found


# README.md


def test_the_readme_names_the_five_options_serve_takes(capsys):
    """README.md, "Sign in with GitHub, Google or your own identity provider": the five options,
    each one `serve --help` lists, and `session-end`."""
    part = section(README, "### Sign in with GitHub, Google or your own identity provider")
    with pytest.raises(SystemExit):
        server.main(["serve", "--help"])
    helped = capsys.readouterr().out
    for option in FIVE:
        assert f"`{option}" in part and option in helped, option
    says(part, "`session-end --db PATH --email ADDRESS` ends that address's open sessions.")
    says(part, "`--session-days N` keeps a sign-in for N days in place of 12 hours, and a use")
    assert "| Wall and brand |" in README and "`test_b1_wall.py`" in README


def test_who_can_get_in_says_what_is_public_with_and_without_the_wall(store):
    """docs/pre-release.md, "Who can get in": "The site is on the public internet. Without
    `--wall`, anyone can read the landing page and the guides; with it, a visitor gets the
    sign-in page and nothing else.\""""
    says(
        section(PRE_RELEASE, "## Who can get in"),
        "The site is on the public internet. Without `--wall`, anyone can read the landing page"
        " and the guides; with it, a visitor gets the sign-in page and nothing else.",
    )
    open_, walled = browser(app(store)), browser(app(store, wall=True))
    for address in ("/", "/docs", "/docs/run"):
        assert open_.get(address).status_code == 200, address
        assert walled.get(address).status_code == 303, address
    assert walled.get("/login").status_code == 200
