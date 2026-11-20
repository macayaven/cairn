"""The graph explorer's labels do not cover each other (the owner saw them do so in 3D).

A project of 40 pages with long titles, drawn 20 to a batch by `serve --graph-explorer`.
In each batch, no two labels the drawing shows overlap: as it loads, in 3D, after the
arrow keys turn it, and zoomed out. A label hidden for a nearer one shows again when the
drawing is back as it was. Every page's full title stays reachable by the keyboard, as a
link in the "Graph content" list the page has below the drawing.
"""

from __future__ import annotations

import re

import pytest
from axe_playwright_python.sync_playwright import Axe
from browser_checks import inspect
from browser_server import Server, Visit, close_visit, open_visit
from playwright.sync_api import Browser, Page, expect

PAGES = 40
TITLES = [
    f"Winter maintenance schedule for the eastern breakwater lamps, section {number:02d}"
    for number in range(PAGES)
]

# The box each label is drawn in: its text, cut by the label's own frame; and each node's mark.
LABELS = """() => {
  const boxes = [];
  const marks = [];
  let hidden = 0;
  for (const node of document.querySelectorAll('[data-graph-interactive] g[data-graph-node]')) {
    const mark = node.querySelector('.graph-mark');
    if (mark) {
      const m = mark.getBoundingClientRect();
      marks.push([node.dataset.graphNode, [m.left, m.top, m.right, m.bottom]]);
    }
    const frame = node.querySelector('svg'), text = frame.querySelector('text');
    if (getComputedStyle(frame).visibility === 'hidden') { hidden++; continue; }
    const a = frame.getBoundingClientRect(), b = text.getBoundingClientRect();
    const box = [Math.max(a.left, b.left), Math.max(a.top, b.top),
                 Math.min(a.right, b.right), Math.min(a.bottom, b.bottom)];
    if (box[2] > box[0] && box[3] > box[1]) boxes.push([node.dataset.graphNode, box]);
  }
  const crossing = [];
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const [p, a] = boxes[i], [q, b] = boxes[j];
    if (a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]) crossing.push([p, q]);
  }
  const mark_crossing = [];
  for (const [p, a] of boxes) for (const [q, b] of marks) {
    if (p !== q && a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]) {
      mark_crossing.push([p, q]);
    }
  }
  return {shown: boxes.length, hidden, crossing, mark_crossing};
}"""


@pytest.fixture(scope="module")
def project(explorer_server: Server) -> str:
    with explorer_server.api() as api:
        assert api.post("/api/projects", json={"name": "lamp-survey"}).status_code == 201
        for number, title in enumerate(TITLES):
            text = f"Lamp {number} on the eastern breakwater is checked every second week."
            made = api.post("/api/projects/lamp-survey/notes", json={"title": title, "text": text})
            assert made.status_code == 201, made.text
    return "/b/lamp-survey/graph"


def labels(page: Page) -> dict:
    return page.evaluate(LABELS)


def assert_apart(page: Page, when: str, fewest: int) -> dict:
    seen = labels(page)
    assert seen["crossing"] == [], (when, seen)
    assert seen["mark_crossing"] == [], (when, seen)
    assert seen["shown"] >= fewest, (when, seen)
    return seen


def walk_one_batch(page: Page) -> None:
    three_d = page.get_by_role("button", name="3D view")
    expect(three_d).to_be_visible()  # the script has run
    assert_apart(page, "as it loads", fewest=21)
    assert labels(page)["hidden"] == 0

    three_d.click()
    expect(three_d).to_have_attribute("aria-pressed", "true")
    assert_apart(page, "in 3D", fewest=10)
    page.get_by_role("region", name="Graph diagram").focus()
    page.keyboard.press("ArrowRight")
    assert_apart(page, "in 3D, turned once by the keyboard", fewest=10)
    page.keyboard.press("ArrowDown")
    assert_apart(page, "in 3D, tilted once by the keyboard", fewest=10)
    page.get_by_role("button", name="Zoom out").click()
    assert_apart(page, "in 3D, zoomed out", fewest=10)

    three_d.click()
    page.get_by_role("button", name="Fit graph").click()
    expect(three_d).to_have_attribute("aria-pressed", "false")
    assert_apart(page, "back in 2D, fitted", fewest=21)
    assert labels(page)["hidden"] == 0  # every label hidden for a nearer one is back
    for _ in range(4):
        page.get_by_role("button", name="Zoom out").click()
    assert_apart(page, "in 2D, zoomed out four times", fewest=5)


def titles_the_keyboard_reaches(page: Page) -> list[str]:
    page.evaluate("() => { window.reached = new Set(); document.activeElement?.blur(); }")
    names = []
    for _ in range(400):
        page.keyboard.press("Tab")
        again, name = page.evaluate(
            """() => { const el = document.activeElement;
              const again = el === document.body || window.reached.has(el);
              window.reached.add(el);
              return [again, (el.textContent || '').trim()]; }"""
        )
        if again:
            break
        names.append(name)
    return names


def test_no_two_labels_the_drawing_shows_overlap_in_either_batch(
    explorer_visit: Visit, project: str, axe: Axe
):
    page = explorer_visit.page
    explorer_visit.sign_in()
    explorer_visit.go(project)
    expect(page.get_by_role("heading", level=1, name="lamp-survey graph")).to_be_visible()
    expect(page.get_by_role("button", name="3D view")).to_be_visible()
    inspect(axe, page, "graph explorer")
    batches = []
    for _ in range(2):
        walk_one_batch(page)
        listed = [
            title
            for title in TITLES
            if page.get_by_role("link", name=title, exact=True).count() == 1
        ]
        assert len(listed) == 20, listed
        reached = titles_the_keyboard_reaches(page)
        assert [title for title in listed if title not in reached] == []
        batches.append(listed)
        next_batch = page.get_by_role("link", name="Next batch")
        if not batches[1:]:
            next_batch.click()
            expect(page).to_have_url(re.compile(r"[?&]after="))
    assert sorted(batches[0] + batches[1]) == sorted(TITLES)


def test_with_scripts_off_the_drawing_is_as_sent_and_hides_no_label(
    browser: Browser, explorer_server: Server, project: str
):
    visit = open_visit(browser, explorer_server, java_script_enabled=False)
    try:
        visit.sign_in()
        visit.go(project)
        page = visit.page
        expect(page.get_by_role("heading", level=1, name="lamp-survey graph")).to_be_visible()
        expect(page.get_by_role("button", name="3D view")).to_be_hidden()
        seen = labels(page)
        assert seen["hidden"] == 0 and seen["shown"] == 21, seen
    finally:
        close_visit(visit)


def test_no_label_covers_a_mark_in_3d_at_375px(browser: Browser, explorer_server: Server):
    with explorer_server.api() as api:
        assert api.post("/api/projects", json={"name": "alpha-ops"}).status_code == 201
        for title, body in (
            ("Deploy", "Run deploy checks before promoting."),
            ("Ports guide", "Open port 443 for the proxy."),
        ):
            made = api.post("/api/projects/alpha-ops/notes", json={"title": title, "text": body})
            assert made.status_code == 201, made.text
    visit = open_visit(browser, explorer_server, viewport={"width": 375, "height": 812})
    try:
        visit.sign_in()
        visit.go("/b/alpha-ops/graph")
        page = visit.page
        three_d = page.get_by_role("button", name="3D view")
        expect(three_d).to_be_visible()
        three_d.click()
        expect(three_d).to_have_attribute("aria-pressed", "true")
        assert_apart(page, "375px in 3D", fewest=1)
    finally:
        close_visit(visit)
