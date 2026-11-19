"""What every page reached in a browser test must pass: axe-core's rules for landmarks,
labels, names, heading order and colour contrast in the light and the dark scheme, and a
375 pixel wide screen with no sideways scroll and every control still within reach."""

from __future__ import annotations

from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page

# axe-core's ids for the five kinds of rule the walk holds every page to.
RULES = {
    "landmarks": (
        "bypass",
        "landmark-banner-is-top-level",
        "landmark-complementary-is-top-level",
        "landmark-contentinfo-is-top-level",
        "landmark-main-is-top-level",
        "landmark-no-duplicate-banner",
        "landmark-no-duplicate-contentinfo",
        "landmark-no-duplicate-main",
        "landmark-one-main",
        "landmark-unique",
        "region",
    ),
    "labels": (
        "aria-input-field-name",
        "form-field-multiple-labels",
        "label",
        "label-title-only",
        "select-name",
    ),
    "names": (
        "aria-command-name",
        "aria-toggle-field-name",
        "button-name",
        "document-title",
        "image-alt",
        "input-button-name",
        "link-name",
        "role-img-alt",
        "summary-name",
        "svg-img-alt",
    ),
    "heading order": ("empty-heading", "heading-order", "page-has-heading-one"),
    "colour contrast": ("color-contrast",),
}

# The one table of rules switched off: (page, axe rule id) -> why. A rule is switched off
# only for the one page named, never everywhere. Empty: every page reached passes them all.
SWITCHED_OFF: dict[tuple[str, str], str] = {}

SCHEMES = ("light", "dark")
# Resolves once every transition running has ended. (wait_for_function would run text as
# code in the page, which the pages' own policy refuses.)
SETTLED = "() => Promise.all(document.getAnimations().map(a => a.finished))"


def rule_ids() -> list[str]:
    return [rule for rules in RULES.values() for rule in rules]


def violations(axe: Axe, page: Page, name: str) -> dict[str, list[str]]:
    """The rules this page breaks in each colour scheme, with the elements that break them."""
    rules = [rule for rule in rule_ids() if (name, rule) not in SWITCHED_OFF]
    found: dict[str, list[str]] = {}
    for scheme in SCHEMES:
        page.emulate_media(color_scheme=scheme)
        # The buttons' colours ease for 120 ms when the scheme changes: read them settled.
        page.evaluate(SETTLED)
        result = axe.run(page, options={"runOnly": {"type": "rule", "values": rules}})
        for violation in result.response["violations"]:
            targets = [str(node["target"]) for node in violation["nodes"]]
            found[f"{scheme}: {violation['id']}"] = targets
    page.emulate_media(color_scheme="light")
    return found


# The controls a person can reach with the keyboard, in the order the page has them.
CONTROLS = (
    "a[href], button, input:not([type=hidden]), select, textarea, summary, "
    "[tabindex]:not([tabindex='-1'])"
)

_REACH = """(selector) => {
  const width = document.documentElement.clientWidth;
  const shown = el => {
    const box = el.getBoundingClientRect(), look = getComputedStyle(el);
    return box.width > 0 && box.height > 0 && look.visibility !== 'hidden';
  };
  const across = el => {
    const box = el.getBoundingClientRect();
    if (box.left >= -1 && box.right <= width + 1) return true;
    // Inside a box that scrolls sideways and sits on the screen, a control is scrolled to
    // when the keyboard reaches it, and a finger scrolls the box.
    for (let up = el.parentElement; up; up = up.parentElement) {
      const overflow = getComputedStyle(up).overflowX;
      if ((overflow === 'auto' || overflow === 'scroll') && up.scrollWidth > up.clientWidth) {
        const outer = up.getBoundingClientRect();
        return outer.left >= -1 && outer.right <= width + 1;
      }
    }
    return false;
  };
  const named = el => (el.getAttribute('aria-label') || el.textContent || '').trim() !== '';
  return [...document.querySelectorAll(selector)].map(el => {
    if (el.closest('[aria-hidden="true"], [inert]')) return 'hidden';
    if (shown(el)) return across(el) ? 'reachable' : 'off the screen';
    const closed = el.closest('details:not([open])');
    const summary = closed && closed.querySelector(':scope > summary');
    if (summary && summary !== el && shown(summary) && across(summary) && named(summary))
      return 'reachable';
    return 'hidden';
  });
}"""


def narrow_problems(page: Page) -> list[str]:
    """At 375 pixels wide: the document does not scroll sideways, and every control shown
    at full width is shown on the screen, or behind a control with a name that opens it."""
    full = page.viewport_size
    assert full is not None
    wide = page.evaluate(_REACH, CONTROLS)
    page.set_viewport_size({"width": 375, "height": full["height"]})
    try:
        assert page.evaluate("document.documentElement.clientWidth") == 375
        problems = []
        scroll = page.evaluate(
            "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
        )
        if scroll[0] > scroll[1]:
            problems.append(f"the document scrolls sideways: {scroll[0]} > {scroll[1]}")
        narrow = page.evaluate(_REACH, CONTROLS)
        names = page.evaluate(
            "(s) => [...document.querySelectorAll(s)].map(el => el.outerHTML.slice(0, 120))",
            CONTROLS,
        )
        for index, (before, after) in enumerate(zip(wide, narrow, strict=True)):
            if before == "reachable" and after != "reachable":
                problems.append(f"{after} at 375 pixels: {names[index]}")
        return problems
    finally:
        page.set_viewport_size(full)


def inspect(axe: Axe, page: Page, name: str) -> None:
    """Every check a page reached in the walk is held to."""
    broken = violations(axe, page, name)
    assert broken == {}, f"{name}: {broken}"
    problems = narrow_problems(page)
    assert problems == [], f"{name}: {problems}"
