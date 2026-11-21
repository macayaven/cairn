"""A person's walk through cairn in a real browser (finding 10.3).

Signing in with a key, making a project, writing a note with a passage before its first
heading and two headings, reading the page with each claim under its heading, opening a
claim's source, searching, the graph, the sources and the audit pages, forgetting the note
through its confirmation page, and signing out. Each step looks for what a person sees, by
role and accessible name. Every page reached passes browser_checks.inspect: axe-core in the
light and the dark scheme, and a 375 pixel wide screen.
"""

from __future__ import annotations

from axe_playwright_python.sync_playwright import Axe
from browser_checks import inspect
from browser_server import Server, Visit, close_visit, open_visit
from playwright.sync_api import Browser, Locator, expect

NOTE = """The tide gauge at the north quay reads in centimetres.

## Moorings

The red buoy was moved to berth 7 on 2026-03-02.

## Lighting

The pier lamps run on a 12 V circuit.
"""


def before(first: Locator, second: Locator) -> bool:
    """`first` comes before `second` in the document."""
    return first.evaluate(
        "(a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)",
        second.element_handle(),
    )


def test_a_person_walks_from_sign_in_to_sign_out(visit: Visit, axe: Axe):
    page = visit.page

    visit.go("/login")
    expect(page.get_by_role("heading", level=1, name="Sign in")).to_be_visible()
    inspect(axe, page, "sign-in")
    page.get_by_label("API key").fill(visit.server.key)
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", level=1, name="Projects")).to_be_visible()
    inspect(axe, page, "projects")

    page.get_by_label("Name", exact=True).fill("harbour-log")
    page.get_by_role("button", name="Create project").click()
    expect(page.get_by_role("heading", level=1, name="harbour-log")).to_be_visible()
    inspect(axe, page, "project")

    page.get_by_role("link", name="Write a note").click()
    expect(page.get_by_role("heading", level=1, name="New note")).to_be_visible()
    inspect(axe, page, "new note")
    page.get_by_label("Title").fill("Harbour survey")
    page.get_by_label("Note", exact=True).fill(NOTE)
    page.get_by_role("button", name="Save note").click()

    # The page: the passage before the first heading, then each claim under its own heading.
    expect(page.get_by_role("heading", level=1, name="Harbour survey")).to_be_visible()
    claims = page.get_by_role("region", name="Claims")
    gauge = claims.get_by_role("article").filter(has_text="The tide gauge at the north quay")
    buoy = claims.get_by_role("article").filter(has_text="The red buoy was moved")
    lamps = claims.get_by_role("article").filter(has_text="The pier lamps run on")
    moorings = claims.get_by_role("heading", level=2, name="Moorings")
    lighting = claims.get_by_role("heading", level=2, name="Lighting")
    for shown in (gauge, buoy, lamps, moorings, lighting):
        expect(shown).to_be_visible()
    assert before(gauge, moorings) and before(moorings, buoy)
    assert before(buoy, lighting) and before(lighting, lamps)
    inspect(axe, page, "page")

    # A claim's source: the exact lines of the note, the cited one among them.
    buoy.get_by_text("See the source line").click()
    source = buoy.get_by_role("figure")
    expect(source).to_be_visible()
    expect(source).to_contain_text("Source 2: line 5")
    lines = source.inner_text()
    for line in ("## Moorings", "The red buoy was moved to berth 7 on 2026-03-02.", "## Lighting"):
        assert line in lines
    assert "tide gauge" not in lines and "pier lamps" not in lines
    inspect(axe, page, "page with a source open")

    page.get_by_role("navigation", name="Main").get_by_role("link", name="Search").click()
    expect(page.get_by_role("heading", level=1, name="Search")).to_be_visible()
    page.get_by_role("searchbox", name="Words to look for").fill("buoy")
    page.get_by_role("search").get_by_role("button", name="Search").click()
    expect(page.get_by_text("1 claim matched.")).to_be_visible()
    hit = page.get_by_role("listitem").filter(has_text="The red buoy was moved")
    expect(hit.get_by_role("link", name="Harbour survey")).to_be_visible()
    inspect(axe, page, "search")

    hit.get_by_role("link", name="harbour-log").click()
    expect(page.get_by_role("heading", level=1, name="harbour-log")).to_be_visible()
    page.get_by_role("link", name="Graph", exact=True).click()
    expect(page.get_by_role("heading", level=1, name="harbour-log graph")).to_be_visible()
    expect(page.get_by_role("region", name="Graph diagram")).to_be_visible()
    expect(page.get_by_role("link", name="Harbour survey", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="3D view")).to_be_visible()
    inspect(axe, page, "graph")

    page.get_by_role("navigation", name="Main").get_by_role("link", name="Workspace").click()
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    inspect(axe, page, "workspace")
    page.get_by_role("main").get_by_role("link", name="Sources", exact=True).click()
    expect(page.get_by_role("heading", level=1, name="Sources")).to_be_visible()
    inspect(axe, page, "sources")

    page.get_by_role("navigation", name="Breadcrumb").get_by_role("link", name="Workspace").click()
    page.get_by_role("main").get_by_role("link", name="Audit log", exact=True).click()
    expect(page.get_by_role("heading", level=1, name="Audit log")).to_be_visible()
    saved = page.get_by_role("row").filter(has_text="note.saved")
    expect(saved.first).to_contain_text("harbour-log")
    inspect(axe, page, "audit")

    # Forgetting the note, through the page that says what goes.
    visit.go("/b/harbour-log")
    page.get_by_role("link", name="Notes", exact=True).click()
    expect(page.get_by_role("heading", level=1, name="harbour-log notes")).to_be_visible()
    inspect(axe, page, "notes")
    page.get_by_role("link", name="Forget Harbour survey").click()
    expect(page.get_by_role("heading", level=1, name="Forget a note")).to_be_visible()
    expect(page.get_by_role("link", name="Harbour survey")).to_be_visible()
    inspect(axe, page, "forget")
    page.get_by_role("button", name="Forget this note").click()
    expect(page.get_by_role("heading", level=1, name="harbour-log")).to_be_visible()
    expect(page.get_by_role("heading", level=2, name="No pages yet")).to_be_visible()
    inspect(axe, page, "project after forgetting")

    page.get_by_role("button", name="Sign out").click()
    expect(page.get_by_role("heading", level=1, name="Sign in")).to_be_visible()
    inspect(axe, page, "signed out")
    visit.go("/b/harbour-log")
    expect(page.get_by_role("heading", level=1, name="Sign in")).to_be_visible()


def test_default_graph_explorer_works_with_scripts_disabled(browser: Browser, server: Server):
    with server.api() as api:
        assert api.post("/api/projects", json={"name": "no-js-graph"}).status_code == 201
        made = api.post(
            "/api/projects/no-js-graph/notes",
            json={"title": "Beacon notes", "text": "The south beacon blinks every 4 seconds."},
        )
        assert made.status_code == 201, made.text

    visit = open_visit(browser, server, java_script_enabled=False)
    try:
        visit.sign_in()
        visit.go("/b/no-js-graph/graph")
        page = visit.page
        expect(page.get_by_role("heading", level=1, name="no-js-graph graph")).to_be_visible()
        expect(page.get_by_role("region", name="Graph diagram")).to_be_visible()
        expect(page.get_by_role("link", name="Beacon notes", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="3D view")).to_be_hidden()

        page.get_by_role("link", name="Notes", exact=True).click()
        expect(page).to_have_url(f"{server.base}/b/no-js-graph/graph?focus=notes")
        note_link = page.get_by_role("link", name="Beacon notes", exact=True)
        expect(note_link).to_be_visible()
        note_link.click()
        expect(page.get_by_text("The south beacon blinks every 4 seconds.")).to_be_visible()
    finally:
        close_visit(visit)
