"""The research notes on other services quote a source only where its licence allows it. For
Granola's pages, Linear's pages and Google's Help Center (support.google.com) no such licence
was found, and Langfuse's documentation and API definitions are MIT-licensed, which asks for a
notice to travel with a substantial copy. So docs/granola-api.md, docs/linear-api.md and
docs/langfuse-api.md hold no quoted span longer than 60 characters, and no line of a note that
links support.google.com does either. A span inside a
code block or a code span is an identifier, not a quote, and is not counted. The tests that
check the Granola and Linear guides against those notes pin our sentences, not the provider's:
no string in them other than a docstring (which quotes our own guide) opens a quoted span of
more than 60 characters. Each check is a function of the text, so test_the_check_sees_a_quote
can run it on a doctored copy."""

import ast
import re

import pytest

from support.paths import REPO_ROOT

ROOT = REPO_ROOT
DOCS = ROOT / "docs"
LONGEST = 60
QUOTED = re.compile(r'["“]([^"“”]*)["”]')
OPENED = re.compile(r'["“][^"“”]{61,}')  # more than LONGEST characters
GUIDE_TESTS = ["guides/test_guide_facts_granola_unit.py", "guides/test_guide_facts_linear_unit.py"]


def without_code(text: str) -> str:
    """The text with every fenced code block and every code span blanked, line for line."""
    lines, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            lines.append("")
        else:
            lines.append("" if fenced else re.sub(r"`[^`\n]*`", "``", line))
    return "\n".join(lines)


def quoted_spans(text: str) -> list[tuple[int, int, str]]:
    """Each quoted span of more than LONGEST characters, with the lines it starts and ends on.
    A span may run over several lines of one paragraph, never over a blank line."""
    found, plain, offset = [], without_code(text), 0
    for paragraph in re.split(r"(\n\s*\n)", plain):
        for match in QUOTED.finditer(paragraph):
            span = " ".join(match.group(1).split())
            if len(span) > LONGEST:
                first = plain.count("\n", 0, offset + match.start()) + 1
                last = plain.count("\n", 0, offset + match.end()) + 1
                found.append((first, last, span))
        offset += len(paragraph)
    return found


def long_quotes(text: str) -> list[tuple[int, str]]:
    return [(first, span) for first, _, span in quoted_spans(text)]


def help_center_quotes(text: str) -> list[tuple[int, str]]:
    """Each long quoted span that is on, or runs over, a line linking support.google.com."""
    lines = text.splitlines()
    linking = {n for n, line in enumerate(lines, 1) if "support.google.com" in line}
    return [
        (first, span)
        for first, last, span in quoted_spans(text)
        if linking.intersection(range(first, last + 1))
    ]


def quoting_strings(source: str) -> list[str]:
    """Each string of a Python source, docstrings left out, that opens a quoted span of more
    than LONGEST characters (closed or not: a test may pin the start of a sentence)."""
    tree = ast.parse(source)
    documented = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                documented.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in documented
        and OPENED.search(" ".join(node.value.split()))
    ]


@pytest.mark.parametrize("name", ["granola-api.md", "linear-api.md", "langfuse-api.md"])
def test_the_provider_notes_quote_no_long_span(name):
    quotes = long_quotes((DOCS / name).read_text(encoding="utf-8"))
    assert quotes == [], f"{name}: {len(quotes)} quoted spans, first {quotes[:3]}"


def test_no_line_linking_the_help_center_holds_a_long_quote():
    found = {}
    for path in sorted(DOCS.glob("*.md")):
        quotes = help_center_quotes(path.read_text(encoding="utf-8"))
        if quotes:
            found[path.name] = quotes
    assert found == {}


@pytest.mark.parametrize("name", GUIDE_TESTS)
def test_the_granola_and_linear_guide_tests_pin_no_long_quote(name):
    strings = quoting_strings((ROOT / "tests" / "unit" / name).read_text(encoding="utf-8"))
    assert strings == [], f"{name}: {strings[:3]}"


def test_the_check_sees_a_quote():
    sentence = "The service returns at most one hundred items in a page, sorted by their id."
    assert long_quotes(f'It says "{sentence}" [doc: X]') == [(1, sentence)]
    assert long_quotes(f"It says\n“{sentence[:40]}\n{sentence[40:]}”") != []
    assert long_quotes(f'It says `"{sentence}"` [doc: X]') == []
    assert long_quotes(f'```\n"{sentence}"\n```') == []
    assert long_quotes('A "short one" and "another"') == []
    linked = f'- **H** = https://support.google.com/x/answer/1 "{sentence}"'
    assert help_center_quotes(linked) != []
    assert help_center_quotes(f'- **H** = https://example.com/1 "{sentence}"') == []

    pinned = f"""def test_x():
    \"\"\"The guide says "{sentence}".\"\"\"
    assert (
        '"{sentence[:40]}'
        '{sentence[40:]}"' in NOTES
    )
    assert "a short span" in NOTES
"""
    assert quoting_strings(pinned) == [f'"{sentence}"']
