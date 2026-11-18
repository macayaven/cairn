"""`serve --test-instance` against the frozen rules for pages: P1's (tests/acceptance/
test_p1_pages.py: one `<h1>`, no script, no `style` attribute, every field named, every link
leads somewhere) for a visitor who is not signed in, and B1's (tests/acceptance/
test_b1_wall.py: under a brand behind the wall, a visitor reaches the sign-in page and
nothing that names the software). Neither frozen file is edited: this file runs their
checks with the flag on."""

import httpx
import pytest
import test_b1_wall as b1
import test_p1_pages as p1
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store

BASE = "https://cairn.example.test"
NOTICE = "This is a private test instance. Sign-in is limited to approved testers."


def visitor_crawl(client: TestClient, starts: tuple[str, ...]) -> dict[str, tuple[int, str]]:
    """Every page a visitor reaches by links from `starts`, as {path: (status, html)}."""
    seen: dict[str, tuple[int, str]] = {}
    queue = list(starts)
    while queue and len(seen) < 400:
        path = queue.pop(0)
        if path in seen:
            continue
        r = client.get(path, follow_redirects=False)
        html = r.text if "text/html" in r.headers.get("content-type", "") else ""
        seen[path] = (r.status_code, html)
        if r.status_code == 200 and html:
            for href in p1.Page(html).links:
                href = href.split("#")[0]
                if href.startswith("/") and not href.startswith(("//", "/static/")):
                    queue.append(href)
    return seen


@pytest.mark.parametrize("wall", [False, True])
def test_with_the_flag_every_page_a_visitor_reaches_keeps_the_p1_rules(tmp_path, wall):
    store = Store(tmp_path / "cairn.db")
    offline = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    sign_in = SignIn(
        providers=(GitHub("client-id", "client-secret"),),
        http=offline,
        join="personal",
        allowed_emails=("ada@example.test",),
    )
    app = create_app(store, public_url=BASE, sign_in=sign_in, test_instance=True, wall=wall)
    try:
        with TestClient(app, base_url=BASE) as client:
            pages = visitor_crawl(client, ("/", "/login", "/docs"))
    finally:
        store.close()
    assert NOTICE in pages["/login"][1]
    if not wall:
        assert NOTICE in pages["/"][1] and "/docs/run" in pages and "/docs/privacy" in pages
    for path, (status, html) in pages.items():
        assert status in (200, 303), (path, status)
        if not html:
            continue
        page = p1.Page(html)
        assert page.lang == "en" and page.h1 == 1, (path, page.h1)
        assert page.scripts == 0 and page.styled == 0, path
        assert page.unnamed == [], (path, page.unnamed)
        assert page.unlabelled_cells == 0, path


def test_under_a_brand_behind_the_wall_the_flag_shows_nothing_but_the_sign_in_page(tmp_path):
    """B1's first crawl, with the flag on: the route table walked as a signed-out browser,
    then sign-ins that fail, are cancelled, refused and succeed."""
    world = b1.World(tmp_path / "one", (b1.ADA, b1.CY, b1.DEE))
    try:
        banned = b1.own_words(world)
        app = world.app(wall=True, test_instance=True, **world.branded())
        control = world.app(test_instance=True, **world.branded())  # without the wall
        browser, control_browser = world.browser(app), world.browser(control)
        signin = b1.sign_in_address(browser)
        params = b1.crawl_params(world)
        before = b1.dump(world.db)

        crawl = b1.Crawl(
            browser, signin=signin, walled=True, banned=banned, control=control_browser
        )
        assert crawl.everything(app, params) > 60
        assert b1.dump(world.db) == before
        page = crawl.seen[("GET", signin, False)]
        assert page.status_code == 200
        shown = b1.Page(page.text)
        assert shown.title == b1.BRAND and shown.h1 == [b1.BRAND]
        assert set(shown.texts) == {b1.BRAND, "Continue with GitHub"}

        cancelled = b1.fresh(app)
        sent = b1.start_sign_in(cancelled)
        r = cancelled.get(
            f"/auth/github/callback?error=access_denied&state={sent['state']}",
            follow_redirects=False,
        )
        crawl.check(r, "GET", "/auth/github/callback", False)
        told = cancelled.get(r.headers["location"], follow_redirects=False)
        crawl.check(told, "GET", r.headers["location"], False)
        refused = b1.sign_in(world, b1.fresh(app), b1.BROOK_GITHUB)
        assert refused.status_code == 403
        crawl.check(refused, "GET", "/auth/github/callback", False)
        r = b1.sign_in(world, b1.fresh(app), b1.ADA_GITHUB)
        assert r.status_code == 200
        crawl.check(r, "GET", "/auth/github/callback", False)
        crawl.follow_refs()
    finally:
        for client in world.browsers:
            client.__exit__(None, None, None)
        world.store.close()
