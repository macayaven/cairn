"""Unit tests for K1 and B3: no sentence a kind or the runner writes names the software.
Under a brand, a run's reason, a skip's reason, a kind's `describe` and its switch-on text
reach the Sources page, so each says "this server" where it once said the name
(tests/acceptance/test_b3_brand_inside.py). The sentences are string constants and the
literal parts of f-strings in cairn.sources: every module there is walked, a new one
included, and every string that is not a docstring is held to the same spelling check as
the templates (test_brand_inside_unit.spelled)."""

import ast
from pathlib import Path

import pytest

from cairn.sources import KINDS
from support.paths import REPO_ROOT
from support.web.brand_inside import spelled

SOURCES = REPO_ROOT / "src" / "cairn" / "sources"

# Strings that name the software and are no sentence: the runner's logger, and Langfuse's
# default score name, which a person types at Langfuse to mark a trace (frozen by
# tests/acceptance/test_c2_langfuse.py).
IDENTIFIERS = {"cairn.sources", "cairn"}


def strings(path: Path) -> list[tuple[int, str]]:
    """Each string literal of the module at `path` (an f-string's literal parts included),
    with its line, leaving out docstrings and other strings that stand as a statement."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    standing = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in standing
    ]


@pytest.mark.parametrize("module", sorted(p.name for p in SOURCES.glob("*.py")))
def test_no_sentence_of_a_kind_or_the_runner_names_the_software(module):
    named = [
        (line, text)
        for line, text in strings(SOURCES / module)
        if text not in IDENTIFIERS and spelled(text)
    ]
    assert named == [], named


def test_the_walk_sees_the_sentences_it_checks():
    """The walk reads f-strings' parts and constants, and leaves docstrings out."""
    source = (SOURCES / "github.py").read_text(encoding="utf-8")
    found = [text for _, text in strings(SOURCES / "github.py")]
    assert "unreadable: this server failed on what GitHub gave" in " ".join(found)
    assert "this server reads in one pull" in " ".join(found)  # RELEASES_PAST, a constant
    docstring = ast.get_docstring(ast.parse(source))
    assert docstring and docstring.startswith("The GitHub source:")
    assert not any(text.startswith("The GitHub source:") for text in found)


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_each_switch_on_sentence_says_the_product_it_is_given(kind):
    said = KINDS[kind].switching_on("Made-Up Field Notes")
    assert said is None or not spelled(said), said
