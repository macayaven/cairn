"""The checks the walk holds every page to find what they look for, on a page the server
sends with its own headers. Without this, a walk with no violation could mean a check that
looks at nothing."""

from __future__ import annotations

import pytest
from axe_playwright_python.sync_playwright import Axe
from browser_checks import RULES, SWITCHED_OFF, narrow_problems, rule_ids, violations
from browser_server import Visit
from playwright.sync_api import Error as PlaywrightError

# One fault of each kind, put into the sign-in page through the DOM and CSSOM: the page's
# policy refuses inline style attributes and scripts, not these.
FAULTS = """() => {
  const main = document.querySelector('main');
  const field = document.createElement('input');           // labels: no label
  main.append(field);
  const button = document.createElement('button');         // names: no name
  main.append(button);
  const faint = document.createElement('p');               // colour contrast
  faint.textContent = 'Faint words on the sheet';
  faint.style.color = 'rgb(200, 200, 200)';
  faint.style.backgroundColor = 'rgb(255, 255, 255)';
  main.append(faint);
  const deep = document.createElement('h4');               // heading order: h1 then h4
  deep.textContent = 'A heading three levels down';
  main.append(deep);
  const loose = document.createElement('p');               // landmarks: outside them all
  loose.textContent = 'Words outside every landmark';
  document.body.append(loose);
}"""


def test_axe_finds_one_fault_of_each_kind_in_both_schemes(visit: Visit, axe: Axe):
    visit.go("/login")
    assert violations(axe, visit.page, "sign-in") == {}
    visit.page.evaluate(FAULTS)
    found = violations(axe, visit.page, "sign-in")
    for scheme in ("light", "dark"):
        for rule in ("label", "button-name", "color-contrast", "heading-order", "region"):
            assert f"{scheme}: {rule}" in found, (scheme, rule, sorted(found))


def test_the_narrow_check_finds_a_sideways_scroll_and_a_control_pushed_off_the_screen(
    visit: Visit,
):
    visit.go("/login")
    assert narrow_problems(visit.page) == []
    visit.page.evaluate(
        """() => {
          const button = document.createElement('button');
          button.textContent = 'Far to the right';
          button.style.position = 'absolute';
          button.style.left = '600px';
          button.style.top = '0';
          document.querySelector('main').append(button);
        }"""
    )
    problems = narrow_problems(visit.page)
    assert any("scrolls sideways" in problem for problem in problems), problems
    assert any(
        "off the screen" in problem and "Far to the right" in problem for problem in problems
    )


def test_the_table_of_rules_switched_off_names_one_page_a_known_rule_and_why():
    known = set(rule_ids())
    assert len(known) == sum(len(rules) for rules in RULES.values())
    for (page, rule), why in SWITCHED_OFF.items():
        assert page and rule in known and len(why.split()) >= 5, (page, rule, why)


def test_a_request_to_any_other_address_is_refused_and_kept(visit: Visit):
    with pytest.raises(PlaywrightError):
        visit.page.goto("http://192.0.2.1/elsewhere")  # a documentation address, never reached
    assert visit.elsewhere == ["http://192.0.2.1/elsewhere"]
    visit.elsewhere.clear()  # the fixture fails a test that leaves one here
