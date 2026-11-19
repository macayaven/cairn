"""The keyboard on a page: the skip link comes first and leads into the main content, and
every element the Tab key reaches shows that it has focus."""

from __future__ import annotations

import pytest
from browser_checks import focus_unseen
from browser_server import Server, Visit
from playwright.sync_api import expect

NOTE = """The ferry timetable changes on the first Monday of April.

## Crossings

The early crossing leaves at 06:40.

## Fares

A return ticket costs 9 EUR.
"""


@pytest.fixture(scope="module")
def page_path(server: Server) -> str:
    """A project with one page of claims, made through the API; its address read back."""
    with server.api() as api:
        assert api.post("/api/projects", json={"name": "ferry-log"}).status_code == 201
        made = api.post(
            "/api/projects/ferry-log/notes", json={"title": "Ferry timetable", "text": NOTE}
        )
        assert made.status_code == 201, made.text  # compiled: its page is there
    return "/b/ferry-log"


def open_the_page(visit: Visit, project: str) -> None:
    visit.sign_in()
    visit.go(project)
    visit.page.get_by_role("link", name="Ferry timetable").click()
    expect(visit.page.get_by_role("heading", level=1, name="Ferry timetable")).to_be_visible()
    visit.page.reload()  # a fresh load: nothing has focus, nothing was clicked


def test_the_first_tab_reaches_the_skip_link_and_it_leads_into_the_main_content(
    visit: Visit, page_path: str
):
    open_the_page(visit, page_path)
    page = visit.page
    page.keyboard.press("Tab")
    skip = page.get_by_role("link", name="Skip to the main content")
    expect(skip).to_be_focused()
    expect(skip).to_be_visible()
    page.keyboard.press("Enter")
    assert page.url.endswith("#main")
    # The next Tab goes on from the main content, not from the header.
    page.keyboard.press("Tab")
    assert page.evaluate("document.querySelector('main').contains(document.activeElement)")


def test_every_element_the_tab_key_reaches_on_the_page_view_shows_focus(
    visit: Visit, page_path: str
):
    open_the_page(visit, page_path)
    reached, unseen = focus_unseen(visit.page)
    assert len(reached) > 10, reached
    assert unseen == [], unseen


def test_the_focus_check_finds_an_element_whose_focus_cannot_be_seen(visit: Visit, page_path: str):
    open_the_page(visit, page_path)
    visit.page.get_by_role("link", name="Source 2").evaluate(
        "el => el.style.setProperty('outline', 'none', 'important')"
    )
    _, unseen = focus_unseen(visit.page)
    assert [element for element in unseen if 'aria-label="Source 2"' in element], unseen
    assert len(unseen) == 1, unseen
