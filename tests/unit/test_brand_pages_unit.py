"""Unit tests for what a brand changes on the pages (B1): the frame of every signed-in page,
the brand's stylesheet (its plate in both themes, its width rule, every token of the
product's), the sign-in page's own template, the logo's address and the names that change.
They pin what tests/acceptance/test_b1_wall.py leaves open."""

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web import templating
from cairn.web.brand import NEUTRAL, SOFTWARE, Logo, server_name
from cookie_names import session_cookie, signin_cookie

BASE = "https://kb.example.test"
BRAND = "Knowledge Base"
ALT = "Juniper Field"
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 32">'
    b'<text x="4" y="22" fill="#1b2a24">Juniper Field</text></svg>\n'
)
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(17)
TOKEN = re.compile(r"(--[\w-]+)\s*:\s*([^;}]+)")  # the frozen test's reading of a token


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
def store(tmp_path):
    made = Store(tmp_path / "kb.db")
    yield made
    made.close()


def app(store, **settings):
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake()))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    return create_app(store, public_url=BASE, sign_in=sign_in, **settings)


def branded(store, logo: bytes | None = SVG, **settings):
    logo_setting = {}
    if logo:
        logo_setting = {"brand_logo": Logo(logo, "image/svg+xml"), "brand_logo_alt": ALT}
    return app(store, brand_name=BRAND, **logo_setting, **settings)


def signed_in(browser: TestClient) -> str:
    started = browser.get("/auth/github", follow_redirects=False)
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    back = browser.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert back.status_code == 200, back.text
    return back.text


class Frame(HTMLParser):
    """A page's title, its head's links, and its wordmark (the masthead's first link)."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.title, self.links, self.wordmark = "", [], None
        self._in_title = False
        self._mark: dict | None = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "link":
            self.links.append(attributes)
        elif tag == "a" and "wordmark" in (attributes.get("class") or ""):
            self._mark = {"href": attributes.get("href"), "text": "", "imgs": []}
            self.wordmark = self._mark
        elif tag == "img" and self._mark is not None:
            self._mark["imgs"].append(attributes.get("src"))

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "a":
            self._mark = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._mark is not None:
            self._mark["text"] += data


def page_addresses(made) -> list[str]:
    """Every GET address of the app's route table that has no parameter, or one filled in."""
    filled = {"project": "core", "name": "connect", "slug": "no-such-page"}
    found = []
    for route in made.routes:
        path = getattr(route, "path_format", "")
        methods = getattr(route, "methods", None) or set()
        names = re.findall(r"\{(\w+)(?::\w+)?\}", path)
        if "GET" not in methods or any(name not in filled for name in names):
            continue
        if path == "/login" or path.startswith(
            ("/api", "/mcp", "/static", "/auth", "/.well-known", "/authorize")
        ):  # the sign-in page has a template of its own; the rest are not pages
            continue
        for name in names:
            path = re.sub(r"\{" + name + r"(?::\w+)?\}", filled[name], path)
        found.append(path)
    return found


# The frame of every signed-in page


def test_under_a_brand_every_signed_in_page_frames_itself_with_the_brand(store):
    made = branded(store)
    browser = TestClient(made, base_url=BASE)
    signed_in(browser)
    browser.post("/projects", data={"name": "core"}, headers={"sec-fetch-site": "same-origin"})
    logo = Logo(SVG, "image/svg+xml").path
    checked = 0
    for address in [*page_addresses(made), "/no-such-page"]:
        r = browser.get(address, follow_redirects=False)
        if "html" not in r.headers.get("content-type", ""):
            continue
        frame = Frame(r.text)
        # The suffix is the brand's; the page's own part is its prose (a guide's title).
        assert frame.title.rpartition(" · ")[2] == BRAND, (address, frame.title)
        assert frame.wordmark == {"href": "/", "text": BRAND, "imgs": [logo]}, address
        hrefs = [link.get("href", "") for link in frame.links]
        assert not [href for href in hrefs if SOFTWARE in href.lower()], (address, hrefs)
        assert templating.BRANDED_STYLESHEET in hrefs and logo in hrefs, address
        footer = r.text.rpartition("<footer")[2]
        assert SOFTWARE not in footer.split("</footer>")[0].replace("/docs", ""), address
        checked += 1
    assert checked > 20


def test_without_a_logo_the_frame_has_the_name_and_no_image_and_no_icon(store):
    browser = TestClient(branded(store, logo=None), base_url=BASE)
    signed_in(browser)
    frame = Frame(browser.get("/").text)
    assert frame.wordmark == {"href": "/", "text": BRAND, "imgs": []}
    assert not [link for link in frame.links if "icon" in link.get("rel", "")]


def test_without_a_brand_the_frame_is_the_softwares(store):
    browser = TestClient(app(store), base_url=BASE)
    signed_in(browser)
    frame = Frame(browser.get("/").text)
    assert frame.title == "Projects · cairn"
    assert frame.wordmark == {"href": "/", "text": "cairn", "imgs": ["/static/cairn.svg"]}
    hrefs = [link["href"] for link in frame.links]
    assert hrefs == ["/static/cairn.svg", templating.STYLESHEET, "/static/manifest.webmanifest"]


def test_a_visitor_under_a_brand_gets_no_guide_link(store):
    r = TestClient(branded(store), base_url=BASE).get("/no-such-page")
    assert r.status_code == 404
    assert "/docs" not in r.text
    assert [a for a in re.findall(r'href="([^"]+)"', r.text) if a == "/login"]


# The brand's stylesheet


def test_the_brands_stylesheet_keeps_every_token_and_drops_only_comments_that_name_it():
    plain = (templating.HERE / "static" / "cairn.css").read_text("utf-8")
    assert SOFTWARE in plain.lower()  # the shipped one names it, in comments
    sheet = templating.BRANDED_CSS
    assert SOFTWARE not in sheet.lower()
    assert set(TOKEN.findall(plain)) <= set(TOKEN.findall(sheet))
    assert re.sub(r"/\*.*?\*/\n?", "", plain, flags=re.DOTALL) in re.sub(
        r"/\*.*?\*/\n?", "", sheet, flags=re.DOTALL
    )  # every rule as it is
    assert "prefers-color-scheme: dark" in sheet


def rules(css: str, selector: str) -> list[str]:
    """The declarations of each rule whose selector list is exactly `selector`."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return [body for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
            if sel.strip() == selector]  # fmt: skip


def dark_blocks(css: str) -> list[str]:
    """The text inside each @media block that applies the dark theme."""
    found = []
    for match in re.finditer(r"@media[^{]*prefers-color-scheme:\s*dark[^{]*\{", css):
        depth, at = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[at], 0)
            at += 1
        found.append(css[match.end() : at - 1])
    return found


def test_the_logos_plate_is_one_fixed_light_colour_in_both_themes():
    sheet = templating.BRANDED_CSS
    (plate,) = rules(sheet, ".plate")
    assert re.search(r"background:\s*#ffffff\s*;", plate)
    assert "var(" not in re.search(r"background:[^;]*", plate).group()  # not a token
    assert dark_blocks(sheet)  # the dark theme is there ...
    for block in dark_blocks(sheet):  # ... and changes nothing of the plate
        assert ".plate" not in block
    assert not re.search(r"\[data-theme[^\]]*\][^{]*\.plate", sheet)


def test_the_logo_fits_a_320px_screen_with_no_sideways_scroll():
    sheet = templating.BRANDED_CSS
    (plate,) = rules(sheet, ".plate")
    (image,) = rules(sheet, ".plate img")
    assert re.search(r"max-width:\s*100%", plate)
    # The plate takes its width from the image. A logo with a viewBox and no width or height
    # of its own (most exported SVGs) has no size to give it, and a browser then draws the
    # image at zero by zero: seen with a real logo, which no test without a browser shows.
    # So the image's height is a fixed length and its width follows from its proportions;
    # its cap is the screen's width and a fixed length, never a percentage of the plate
    # (which would be a percentage of the image itself); and an image wider than the cap
    # is fitted inside its box, not stretched.
    assert re.search(r"[^-]height:\s*96px", image) and re.search(r"[^-]width:\s*auto", image)
    cap = re.search(r"max-width:\s*([^;]+);", image).group(1)
    assert "%" not in cap and "100vw" in cap and "rem" in cap, cap
    assert re.search(r"object-fit:\s*contain", image)
    (section,) = rules(sheet, ".branded")
    assert re.search(r"max-width:\s*100%", section) and "overflow-wrap: anywhere" in section
    (button,) = rules(sheet, ".branded .button")
    assert re.search(r"min-height:\s*44px", button)
    (focus,) = rules(sheet, ".branded .button:focus-visible")
    assert "outline:" in focus


# The sign-in page under a brand


def test_the_brands_sign_in_page_is_a_page_of_its_own(store):
    r = TestClient(branded(store, wall=True), base_url=BASE).get("/login")
    assert r.status_code == 200
    head = r.text.split("</head>")[0]
    assert r.text.startswith('<!doctype html>\n<html lang="en">')
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in head
    assert "<script" not in r.text.lower() and "<form" not in r.text.lower()
    assert '<p class="plate"><img src="' in r.text and f'alt="{ALT}"' in r.text
    assert 'class="button"' in r.text and 'class="signin branded"' in r.text
    assert r.headers["x-robots-tag"] == "noindex"


def test_the_brands_stylesheet_and_logo_are_served_once_from_addresses_that_change(store):
    with_png = branded(store, logo=None, brand_logo=Logo(PNG, "image/png"))
    browser = TestClient(with_png, base_url=BASE)
    sheet = browser.get(templating.BRANDED_STYLESHEET)
    assert sheet.status_code == 200 and sheet.text == templating.BRANDED_CSS
    assert sheet.headers["content-type"].startswith("text/css")
    logo = Logo(PNG, "image/png")
    served = browser.get(logo.path)
    assert served.content == PNG and served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert "immutable" in served.headers["cache-control"]
    for named in ("cairn.css", "cairn.svg", "manifest.webmanifest", "CAIRN.CSS"):
        assert browser.get(f"/static/{named}").status_code == 404, named
    assert browser.get("/static/fonts/atkinson-hyperlegible-next.ttf").status_code == 200


@pytest.mark.parametrize(
    ("content", "media_type"), [(SVG, "image/svg+xml"), (PNG, "image/png")], ids=["svg", "png"]
)
def test_the_logo_may_be_framed_by_no_page_and_a_png_carries_the_same_policy(
    store, content, media_type
):
    """Opened by itself, the logo runs nothing, fetches nothing and no page may frame it:
    one policy, `frame-ancestors 'none'` included, for an SVG and a PNG alike (a PNG's
    bytes are the file's, never checked to be only an image). Its bytes are not changed."""
    logo = Logo(content, media_type)
    browser = TestClient(branded(store, logo=None, brand_logo=logo), base_url=BASE)
    served = browser.get(logo.path)
    assert served.status_code == 200 and served.content == content
    policy = [d.strip() for d in served.headers["content-security-policy"].split(";")]
    assert "default-src 'none'" in policy and "frame-ancestors 'none'" in policy
    assert "sandbox" in policy


@pytest.mark.parametrize(
    "spelling",
    [
        "cairn.c%C5%BFs",  # U+017F, which a file system may fold to "s" and str.lower() does not
        "cairn.%C5%BFvg",
        "manifest.webmanife%C5%BFt",
        "CAIRN.CSS",
        "fonts/ATKINSON-HYPERLEGIBLE-NEXT.TTF",
        "fonts/atkinson-hyperlegible-next.tt%C5%BF",
        "fonts",
        "fonts/",
        "",
    ],
)
def test_under_a_brand_no_other_spelling_of_a_static_file_is_served(store, spelling):
    """Under a brand, /static serves only what a branded page needs and names nothing: the
    fonts and their licence texts, by their exact names. Nothing is guessed about how the
    file system folds a name, so no other spelling of any file reaches it."""
    browser = TestClient(branded(store, wall=True), base_url=BASE)
    r = browser.get(f"/static/{spelling}")
    assert r.status_code == 404, spelling
    assert "cairn" not in r.text.lower()


def test_under_a_brand_static_serves_exactly_the_fonts_and_their_licences(store, tmp_path):
    """Measured against a folder of its own, so the rule does not depend on this machine's
    file system: a file named with a character that folds to another's is not served."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from cairn.web.app import _Static, branded_static

    (tmp_path / "fonts").mkdir()
    for name in ("fonts/a.ttf", "fonts/a.OFL.txt", "site.css", "cairn.c\u017fs"):
        (tmp_path / name).write_text("named: cairn", encoding="utf-8")
    served = Starlette(routes=[Mount("/static", branded_static(tmp_path))])
    browser = TestClient(served)
    for name, status in (
        ("fonts/a.ttf", 200),
        ("fonts/a.OFL.txt", 200),
        ("site.css", 404),
        ("cairn.c%C5%BFs", 404),
        ("fonts/A.TTF", 404),
        ("fonts/../site.css", 404),
    ):
        assert browser.get(f"/static/{name}").status_code == status, name
    assert isinstance(branded_static(tmp_path), _Static)


def test_under_a_brand_everything_a_branded_page_refers_to_is_served(store):
    """The sign-in page's and a signed-in page's own files, and every font the brand's
    stylesheet names, still answer 200."""
    app_ = branded(store, wall=True)
    browser = TestClient(app_, base_url=BASE)
    pages = [browser.get("/login").text]
    signed_in(browser)
    pages.append(browser.get("/workspace").text)
    refs = {ref for page in pages for ref in re.findall(r'(?:href|src)="(/static/[^"]+)"', page)}
    fonts = re.findall(r"url\(['\"]?([^'\")]+)['\"]?\)", templating.BRANDED_CSS)
    assert fonts
    for font in fonts:
        refs.add(font if font.startswith("/") else f"/static/{font.removeprefix('./')}")
    for ref in refs:
        assert browser.get(ref).status_code == 200, ref


def test_without_a_brand_no_brand_address_exists(store):
    browser = TestClient(app(store), base_url=BASE)
    assert browser.get(urlsplit(templating.BRANDED_STYLESHEET).path).status_code == 404
    assert browser.get(Logo(SVG, "image/svg+xml").path).status_code == 404
    assert browser.get("/static/cairn.css").status_code == 200


# The names that change


def set_cookies(response: httpx.Response) -> dict[str, str]:
    """Each cookie the response sets, by name: the whole Set-Cookie line, in lowercase."""
    return {line.split("=", 1)[0]: line.lower() for line in response.headers.get_list("set-cookie")}


def test_under_a_brand_on_https_the_cookies_are_host_only_by_their_names(store):
    """Under a brand on https the cookies are `__Host-session` and `__Host-signin`: a browser
    takes a cookie of such a name only with Secure, Path=/ and no Domain, so no other host
    can set one. The state cookie's path is / for that reason."""
    browser = TestClient(branded(store), base_url=BASE)
    started = browser.get("/auth/github", follow_redirects=False)
    state = set_cookies(started)["__Host-signin"]
    assert "path=/;" in state and "secure" in state and "domain" not in state
    # Finish the sign-in started above (B2: a second start would leave this one under way).
    issued = dict(httpx.URL(started.headers["location"]).params)["state"]
    back = browser.get(f"/auth/github/callback?code=c&state={issued}", follow_redirects=False)
    assert back.status_code == 200, back.text
    assert set(browser.cookies.keys()) == {"__Host-session"}
    out = browser.post("/logout", headers={"sec-fetch-site": "same-origin"}, follow_redirects=False)
    cleared = set_cookies(out)["__Host-session"]
    assert "max-age=0" in cleared and "path=/;" in cleared and "secure" in cleared


def test_under_a_brand_over_plain_http_the_cookies_keep_their_plain_names(store):
    """A browser refuses the __Host- prefix without Secure, so over http (a loopback
    address) the names stay `session` and `signin`, and the state cookie keeps Path=/auth."""
    local = "http://127.0.0.1:8765"
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake()))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    made = create_app(store, public_url=local, sign_in=sign_in, brand_name=BRAND)
    browser = TestClient(made, base_url=local)
    started = browser.get("/auth/github", follow_redirects=False)
    assert set(set_cookies(started)) == {"signin"}
    assert "path=/auth" in set_cookies(started)["signin"]
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    back = browser.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert set(set_cookies(back)) == {"session", "signin"}


def test_a_session_or_state_cookie_planted_by_a_sibling_host_is_not_read(store):
    """Another host under the same parent domain can set `session` and `signin` for the
    parent domain, and the browser sends them along with this server's own. The server reads
    only the names it sets, which no other host can set: a listed person never acts in the
    planter's workspace, and a planted state cookie signs nobody in."""
    app_ = branded(store, wall=True)
    browser = TestClient(app_, base_url=BASE)
    signed_in(browser)
    (name,) = browser.cookies.keys()
    ada = browser.cookies[name]
    mal = store.sign_in(
        "github", "7666", "mal@example.test", "Mal Lory", issuer="https://github.com",
        join="personal",
    ).token  # fmt: skip
    fresh = TestClient(app_, base_url=BASE)
    for cookie in (
        f"{name}={ada}; {session_cookie(branded=True)}={mal}",
        f"{session_cookie(branded=True)}={mal}; {name}={ada}",
    ):
        r = fresh.get("/workspaces", headers={"cookie": cookie}, follow_redirects=False)
        assert r.status_code == 200 and "Mal Lory" not in r.text, cookie
    alone = fresh.get(
        "/workspaces",
        headers={"cookie": f"{session_cookie(branded=True)}={mal}"},
        follow_redirects=False,
    )
    assert alone.status_code == 303

    planter = TestClient(app_, base_url=BASE)
    started = planter.get("/auth/github", follow_redirects=False)
    (sealed,) = started.cookies.values()
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    victim = TestClient(app_, base_url=BASE)
    back = victim.get(
        f"/auth/github/callback?code=c&state={state}",
        headers={"cookie": f"{signin_cookie(branded=True)}={sealed}"},
        follow_redirects=False,
    )
    assert back.status_code == 400 and not [
        line
        for line in back.headers.get_list("set-cookie")
        if f"{session_cookie(branded=True)}=" in line
    ]


def test_a_refusal_sentence_names_this_server_under_a_brand():
    assert server_name(None) == SOFTWARE and server_name(object()) == NEUTRAL  # type: ignore[arg-type]


def test_in_the_light_theme_the_plate_does_not_indent_the_logo():
    """In the light theme the plate is the card's own colour and cannot be seen, so its
    padding would only push the logo to the right of the heading under it. There the plate
    hangs back by its own padding and the logo starts where the heading starts; in the dark
    theme the plate is seen and keeps its place. Its colour is the same in both."""
    sheet = templating.BRANDED_CSS
    (plate,) = rules(sheet, ".plate")
    padding = re.search(r"padding:\s*\S+\s+([^;]+);", plate).group(1).strip()
    light = re.search(r"@media \(prefers-color-scheme: light\)\s*\{(.*?\})\s*\}", sheet, re.S)
    assert light, "no light-theme block"
    assert re.search(
        r"\.branded \.plate\s*\{\s*margin-left:\s*calc\(-1 \* " + re.escape(padding) + r"\);",
        light.group(1),
    )
    assert "background" not in light.group(1)


# The masthead on a phone


def phone_blocks(css: str) -> str:
    """The text inside every @media block for a phone (the 34rem breakpoint), joined."""
    found = []
    for match in re.finditer(r"@media[^{]*max-width:\s*34rem[^{]*\{", css):
        depth, at = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[at], 0)
            at += 1
        found.append(css[match.end() : at - 1])
    return "\n".join(found)


def test_on_a_phone_the_wordmark_keeps_clear_of_the_person_and_the_way_out():
    """On a phone the person and "Sign out" used to sit at the top right, out of the flow, and
    nothing stopped a long wordmark from running under them: seen with a real logo and a
    two-word name, which no test without a browser shows. Now all of the first line is in
    the flow: the nav gives up its box, the wordmark, the person and the way out share one
    flex line, and the places start a line of their own. The person's name gives way first,
    down to a few letters; then the wordmark's name; each is cut with an ellipsis. Seen in a
    browser at 320, 375 and 390px, with and without a logo, with short and long names."""
    phone = phone_blocks(templating.BRANDED_CSS)
    (who,) = rules(phone, ".who")
    assert "position" not in who and "margin-left: auto" in who
    # Its floor can be wider than what it holds (a session opened with a key has no person's
    # name, only "Sign out"): what it holds sits at the right edge, where the eye looks for it.
    assert "justify-content: flex-end" in who
    assert "display: contents" in rules(phone, ".masthead:has(.who) .nav")[0]
    (line_break,) = rules(phone, ".masthead:has(.who)::after")
    assert "flex-basis: 100%" in line_break and 'content: ""' in line_break
    (mark,) = rules(phone, ".masthead:has(.who) .wordmark")
    (places,) = rules(phone, ".masthead:has(.who) .nav > a")

    def order(rule: str) -> int:
        return int(re.search(r"order:\s*(\d+)", rule).group(1))

    assert order(mark) == order(who) < order(line_break) < order(places)

    def grow(rule: str) -> int:
        # Both start from nothing, so the line never wraps between them, and grow to what
        # they hold and no further.
        assert "max-width: max-content" in rule
        return int(re.search(r"flex:\s*(\d+) 1 0;", rule).group(1))

    assert grow(mark) >= 100 * grow(who) > 0  # the wordmark is whole first, then the person
    assert "min-width: 0" in mark
    (person,) = rules(phone, ".nav .person")
    least = float(re.search(r"min-width:\s*([\d.]+)rem", person).group(1))
    assert 2 <= least <= 4 and "text-overflow: ellipsis" in person and "nowrap" in person
    # The person and the way out say the least they take outright: the name at its floor, a
    # 1rem gap and "Sign out" (under 4rem). Left to the browser, a long name's own width
    # counts as the least, and the wordmark is cut first (seen: 134px of 156).
    floor = float(re.search(r"min-width:\s*([\d.]+)rem", who).group(1))
    assert floor == least + 1 + 4
    assert "flex: none" in rules(phone, ".who form")[0]  # "Sign out" is never cut
    (name,) = rules(phone, ".wordmark .name")
    for declared in ("min-width: 0", "overflow: hidden", "text-overflow: ellipsis", "nowrap"):
        assert declared in name, declared
    # A logo does not shrink, so it is capped by the screen's width (a percentage would be
    # one of the image itself): the line less that floor, a 1rem gap, the page's two 1rem
    # margins and the plate's padding (1rem).
    assert "flex: none" in rules(phone, ".wordmark .plate")[0]
    (image,) = rules(phone, ".wordmark .plate img")
    cap = re.search(r"max-width:\s*([^;]+);", image).group(1)
    taken = float(re.search(r"calc\(100vw - ([\d.]+)rem\)", cap).group(1))
    assert "%" not in cap and taken >= floor + 1 + 2 + 1
    assert 320 - 16 * taken >= 96, "on a 320px screen the logo keeps at least 96px"


def test_on_a_phone_a_logo_stands_for_the_name_and_a_screen_reader_still_hears_it(store):
    browser = TestClient(branded(store), base_url=BASE)
    signed_in(browser)
    mark = browser.get("/").text.split('<a class="wordmark', 1)[1].split("</a>", 1)[0]
    assert mark.startswith(' has-logo" href="/">')
    assert 'alt=""' in mark and mark.endswith(f'<span class="name">{BRAND}</span>')
    (hidden,) = rules(phone_blocks(templating.BRANDED_CSS), ".wordmark.has-logo .name")
    assert "display" not in hidden and "visibility" not in hidden  # either would silence it
    for declared in ("position: absolute", "width: 1px", "height: 1px", "clip-path: inset(50%)"):
        assert declared in hidden, declared


def test_without_a_logo_the_name_is_what_the_masthead_shows_on_a_phone(store):
    browser = TestClient(branded(store, logo=None), base_url=BASE)
    signed_in(browser)
    mark = browser.get("/").text.split('<a class="wordmark', 1)[1].split("</a>", 1)[0]
    assert mark == f'" href="/"><span class="name">{BRAND}</span>'


def test_with_no_brand_the_softwares_name_is_cut_the_same_way_on_a_phone(store):
    browser = TestClient(create_app(store), base_url=BASE)
    mark = browser.get("/login").text.split('<a class="wordmark', 1)[1].split("</a>", 1)[0]
    assert mark.startswith('" href="/"><img src="/static/cairn.svg"')
    assert mark.endswith(f'<span class="name">{SOFTWARE}</span>')
