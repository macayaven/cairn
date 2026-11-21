"""On a phone, a link that wraps keeps the text's own line, and every link stays a target.

The outside reviewer's: on the related page at 375 pixels, the link "Open this claim on" a
page of a 70-character title ran over two lines of 44 pixels each, 88 pixels for two lines
of text. The 34rem block of the stylesheet makes the links of running text 44 pixels high to
touch; a link that wraps is now as high as its lines of text, each the text's own height,
and never less than 44 pixels. Measured in Chromium: the lines of the link's text (a range
over it), their pitch, and the link's box. The same reviewer's walk after that fix: a link
laid out as a flex box made its words and the title (`<bdi>`) two items side by side, so a
short title lost the space before it and a long one wrapped as two columns. The link's text
flows as one text: the title's second line starts at the link's left edge, and a space's
width stands between the words and a short title.
"""

from __future__ import annotations

from browser_server import Visit

LONG = "Harbour moorings and the tide gauge readings of the north quay in 2026"  # 70
TERM = "/srv/harbour/moorings.cfg"

_MEASURE = """link => {
  const range = document.createRange();
  range.selectNodeContents(link);
  const tops = [...new Set([...range.getClientRects()].map(r => Math.round(r.top)))]
    .sort((a, b) => a - b);
  const box = link.getBoundingClientRect();
  const words = link.firstChild;  // the text node before the title
  const said = document.createRange();
  said.setStart(words, 0);
  said.setEnd(words, words.data.trimEnd().length);
  const title = [...link.querySelector("bdi").getClientRects()];
  return {
    lines: tops.length,
    pitch: tops.length > 1 ? tops[1] - tops[0] : null,
    height: box.height,
    text: parseFloat(getComputedStyle(link.parentElement).lineHeight),
    left: box.left,
    title_lines: title.map(r => r.left),
    gap: title[0].left - Math.max(...[...said.getClientRects()].map(r => r.right)),
    read: link.innerText,
  };
}"""

RUNNING = ".crumbs a, .meta a, .hit-where a, .source-head a, .sources .back a, .parts a, .toc a"
_TARGETS = """selectors => [...document.querySelectorAll(selectors)]
  .filter(a => a.getClientRects().length)
  .map(a => [a.textContent.trim(), a.getBoundingClientRect().height])"""


def test_a_wrapped_link_on_the_related_page_keeps_the_texts_line_at_375_pixels(visit: Visit):
    with visit.server.api() as api:
        assert api.post("/api/projects", json={"name": "quay"}).status_code == 201
        slugs = {}
        for title in ("Deploy", LONG):
            made = api.post(
                "/api/projects/quay/notes",
                json={"title": title, "text": f"The settings live in `{TERM}` for {title}."},
            )
            assert made.status_code == 201, made.text
            slugs[title] = made.json()["page"]
    visit.sign_in()
    page = visit.page
    page.set_viewport_size({"width": 375, "height": 900})
    visit.go("/b/quay/p/deploy/related")
    link = page.locator(".source-head a").filter(has_text=LONG)
    measured = link.evaluate(_MEASURE)
    assert measured["lines"] >= 2, measured  # the title wraps at this width
    assert measured["pitch"] == round(measured["text"]), measured  # the text's own line
    # as high as its lines of text, and never under 44 pixels: a target to touch
    assert abs(measured["height"] - max(44, measured["lines"] * measured["text"])) <= 1, measured
    # one text: the title's second line starts at the link's left edge, not beside the words
    assert len(measured["title_lines"]) >= 2, measured
    assert measured["title_lines"][1] <= measured["left"] + 1, measured
    for text, height in page.evaluate(_TARGETS, RUNNING + ", .project-foot a"):
        assert height >= 44, (text, height)
    visit.go(f"/b/quay/p/{slugs[LONG]}/related")  # a short title: Deploy
    short = page.locator(".source-head a").filter(has_text="Deploy").evaluate(_MEASURE)
    assert short["read"] == "Open this claim on Deploy", short
    assert short["gap"] >= 2, short  # a space's width between the words and the title
    assert short["lines"] == 1 and abs(short["height"] - 44) <= 1, short
