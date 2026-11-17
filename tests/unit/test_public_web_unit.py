"""Unit tests for the pages anyone can read (web/public.py), beyond the frozen P1 contract:
the guides' names are a fixed list, a signed-in reader keeps the app's navigation, and the
landing page's commands and links."""

import hashlib
import re
from importlib.resources import files
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub, Google
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web.public import GUIDES

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        yield client, key
    store.close()


def test_a_guide_is_one_of_the_six_and_nothing_else(env):
    client, _ = env
    assert GUIDES == ("connect", "patterns", "sources", "security", "privacy", "run")
    listed = client.get("/docs").text
    assert listed.index('href="/docs/patterns"') < listed.index('href="/docs/sources"')
    assert "documents and to-dos" in listed
    shown = client.get("/docs/sources").text
    assert "<h1>Sources: meetings, mail, calendar, documents and to-dos</h1>" in shown
    assert "cannot delete one note" not in shown and "forgotten" in shown
    for path in ("/docs/%2e%2e", "/docs/..%2fserver", "/docs/CONNECT", "/docs/connect.md"):
        assert client.get(path, follow_redirects=False).status_code == 404, path
    for path in ("/docs/connect/", "/docs/connect/x"):
        assert client.get(path, follow_redirects=False).status_code == 404, path


def test_a_guide_shows_its_sections_first_and_links_between_guides_stay_on_the_server(env):
    client, _ = env
    page = client.get("/docs/patterns").text
    assert '<nav class="toc" aria-label="On this page">' in page
    assert 'href="#many-agents-one-project"' in page and 'id="many-agents-one-project"' in page
    assert 'href="/docs/connect"' in client.get("/docs").text
    assert "<script" not in page and " style=" not in page


def test_a_signed_in_reader_keeps_the_apps_navigation(env):
    client, key = env
    visitor = client.get("/docs/security").text
    assert 'href="/login">Sign in<' in visitor and 'action="/logout"' not in visitor
    client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
    reader = client.get("/docs/security").text
    assert 'href="/workspace">Workspace<' in reader and 'action="/logout"' in reader


def test_the_landing_page_shows_this_servers_address_and_its_own_providers(tmp_path):
    store = Store(tmp_path / "cairn.db")
    offline = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    providers = (GitHub("id", "secret"), Google("id", "secret"))
    app = create_app(store, public_url=BASE, sign_in=SignIn(providers=providers, http=offline))
    with TestClient(app, base_url=BASE) as client:
        home = client.get("/").text
    store.close()
    assert f"claude mcp add --transport http cairn {BASE}/mcp" in home
    assert f"codex mcp add cairn --url {BASE}/mcp" in home
    assert "GitHub or Google" in home
    assert "/pricing" not in home and "$" not in home  # nothing is on sale here
    assert 'href="/docs/connect"' in home
    own = home.split("<h3>On your own machine</h3>", 1)[1].split("</div>", 1)[0]
    assert 'href="/docs/run"' in own and "docker run" in own


def test_the_run_guide_takes_a_self_hoster_from_docker_run_to_an_upgrade(env):
    client, _ = env
    page = client.get("/docs/run")
    assert page.status_code == 200 and "<h1>Run it yourself</h1>" in page.text
    text = re.sub(r"<[^>]+>", " ", page.text)
    for step in (
        "docker run -d --name cairn",
        "key-create --db /data/cairn.db",
        "Sign in",
        "/data/cairn.db",
        "export --db /data/cairn.db --account acct_default",
        "docker pull",
    ):
        assert step in text, step
    start = page.text.split('id="start-it"', 1)[1].split("<pre", 1)[0]  # before the commands
    assert "main branch" in start and "public" in start and "not public yet" in start
    assert 'href="/docs/run"' in client.get("/docs").text


# The guides' key examples: a key on a command line is readable by other processes while
# the command runs. A client's own `--header` command comes after the configuration that
# reads the key from the environment (or, for a client without one, after its `headers`
# entry), with why; curl reads its header from a file or stdin.
ARGV_KEY = re.compile(r"""(?:-H|--header) +["']?[^\n"']*\$\{?CAIRN_API_KEY""")
KEY_GUIDES = {
    "connect": files("cairn.guides").joinpath("connect.md"),
    "run": files("cairn.guides").joinpath("run.md"),
    "README": Path(__file__).parents[2] / "README.md",
}


@pytest.mark.parametrize("guide", sorted(KEY_GUIDES))
def test_a_guide_puts_the_environment_variable_configuration_first(guide):
    text = KEY_GUIDES[guide].read_text()
    curls = [line for line in text.splitlines() if line.lstrip().startswith("curl ")]
    for line in curls:
        assert "CAIRN_API_KEY" not in line and "-H @" in line, line
    for found in ARGV_KEY.finditer(text):
        before = text[: found.start()]
        section = before[before.rfind("\n## ") :]  # the section the command is in
        first = ("${CAIRN_API_KEY}", "env-var CAIRN_API_KEY", "`headers` object")
        assert any(config in section for config in first), (guide, found.group(0))
        assert "other processes" in text[found.start() - 600 : found.end() + 600], found.group(0)


def test_the_headless_curl_example_reads_its_header_from_a_private_file():
    text = KEY_GUIDES["connect"].read_text()
    section = text.split("## Headless agents and scripts", 1)[1].split("\n## ", 1)[0]
    assert "umask 077" in section and "printf 'x-api-key: %s\\n' \"$CAIRN_API_KEY\"" in section
    assert "curl -sS -H @" in section and "other processes" in section


def test_the_guides_say_what_a_repeat_and_a_refusal_record_and_no_more():
    """A second identical run stores no note and no page version, but its outcome is in the
    audit log (note.unchanged); a refusal's answer names the rule and the line, its audit
    event only the rule (tests/acceptance/test_g1_guard.py pins both)."""
    guides = files("cairn.guides")
    sources = guides.joinpath("sources.md").read_text()
    twice = sources.split("**Running it twice is safe.**", 1)[1].split("\n", 1)[0]
    assert "writes nothing" not in twice and "audit log" in twice
    security = guides.joinpath("security.md").read_text()
    assert "the answer and the audit log name the rule and the line" not in security
    assert "The answer names the rule and the line; the audit log names the rule" in security


def test_no_page_anyone_can_read_links_to_the_source_repository(tmp_path, env):
    client, _ = env
    for path in ("/docs", *(f"/docs/{name}" for name in GUIDES), "/login"):
        assert "github.com/" not in client.get(path).text, path
    store = Store(tmp_path / "landing.db")
    offline = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    sign_in = SignIn(providers=(GitHub("id", "secret"),), http=offline)
    with TestClient(create_app(store, public_url=BASE, sign_in=sign_in), base_url=BASE) as c:
        assert "github.com/" not in c.get("/").text
    store.close()


def test_on_a_phone_commands_wrap_and_section_headings_take_the_column(env):
    client, _ = env
    css = client.get("/static/cairn.css").text
    heading = re.search(r"\.wide h2 \{([^}]*)\}", css)
    assert heading and "text-wrap: balance" in heading[1] and "max-width" not in heading[1]
    phone = css.split("@media (max-width: 40rem) {", 1)[1].split("\n}\n", 1)[0]
    wrap = re.search(r"\.wide pre, \.setup pre \{([^}]*)\}", phone)
    assert wrap and "white-space: pre-wrap" in wrap[1] and "overflow-wrap: anywhere" in wrap[1]


def test_every_page_links_the_stylesheet_by_a_digest_of_the_file_as_shipped(tmp_path, env):
    """An upgrade changes the stylesheet's address, so no browser keeps the old one."""
    shipped = (files("cairn") / "static" / "cairn.css").read_bytes()
    link = f'href="/static/cairn.css?v={hashlib.sha256(shipped).hexdigest()[:12]}"'
    client, key = env
    pages = [client.get(path).text for path in ("/login", "/docs", "/docs/run", "/no-such-page")]
    client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
    pages += [client.get(path).text for path in ("/", "/workspace", "/b/no-such-project")]
    store = Store(tmp_path / "landing.db")
    offline = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    sign_in = SignIn(providers=(GitHub("id", "secret"),), http=offline, join="personal")
    with TestClient(create_app(store, public_url=BASE, sign_in=sign_in), base_url=BASE) as c:
        pages.append(c.get("/").text)  # the landing page, which Auth renders
    store.close()
    for page in pages:
        assert link in page and 'href="/static/cairn.css"' not in page
    assert client.get(link.split('"')[1]).status_code == 200  # the address serves the file
    for template in (files("cairn") / "templates").iterdir():  # only base.html links it
        assert template.name == "base.html" or "/static/cairn.css" not in template.read_text()
