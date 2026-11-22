"""G1 item 5: a number, version or date that an underscore of emphasis touches is read whole.

`_2,700 more comments are left out._` was rejected by the gate. The number pattern may not
start right after a word character, and "_" is one, so it could not start at "2"; finditer went
on and started after the comma, reading 700. normalize() then took the thousands separator out
of the span's text ("_2700 more"), where 700 is no whole token, and the claim's fact was in none
of its cited spans. Reading 2,700 whole was not enough either: lint matches a fact as a whole
token of [0-9a-z_], and "2700" after "_" is not one.

So cairn.facts reads an underscore run that opens or closes emphasis next to a number, a version
or a date as "*", the same emphasis, both where it extracts facts and in normalize(), which lint
uses for every text. An underscore inside a word (max_retries_3, v1_2, a file name) opens and
closes nothing and reads as it did."""

import importlib.util
import sys

import pytest

from cairn.facts import extract_facts, normalize
from cairn.gate import lint_gate
from cairn.lint import _occurs
from cairn.memory import Memory
from cairn.spans import chunk
from cairn.store import Store
from support.paths import REFERENCE_ROOT

REFERENCE = REFERENCE_ROOT / "facts_before_p1.py"


def _reference():
    spec = importlib.util.spec_from_file_location("g1_reference_facts", REFERENCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


old = _reference()


def read(text: str, facts=extract_facts) -> list[tuple[str, str]]:
    return [(f.kind, f.value) for span in chunk("s", text) for f in facts(span)]


EMPHASIS = [  # (line, what it reads now)
    ("_2,700 more comments are left out._", [("number", "2,700")]),
    ("_about 1,250_ jobs ran.", [("number", "1,250")]),
    ("_They left out 2,700._", [("number", "2,700")]),
    ("_They left out 2,700_.", [("number", "2,700")]),
    ("The queue held _1,250_ jobs at noon.", [("number", "1,250")]),
    ("__2,700__ more comments are left out.", [("number", "2,700")]),
    ("___2,700___ more comments are left out.", [("number", "2,700")]),
    ("(_2,700_) comments.", [("number", "2,700")]),
    ("_$1,250.50 was billed._", [("number", "$1,250.50")]),
    ("_5 min_ to warm up.", [("number", "5 min")]),
    ("_1.2 GB_ of logs.", [("number", "1.2 GB")]),
    ("_2099-03-02_ is the day.", [("date", "2099-03-02")]),
    ("_2099-03-02T10:00Z_ is the hour.", [("date", "2099-03-02T10:00Z")]),
    ("_v1.2.3_ shipped.", [("version", "v1.2.3")]),
    ("_1.2.3-rc1_ shipped.", [("version", "1.2.3-rc1")]),
    ("_2,700-word essays_ are long.", [("number", "2,700")]),
]


@pytest.mark.parametrize(("line", "facts"), EMPHASIS)
def test_a_fact_touched_by_emphasis_is_read_whole(line, facts):
    assert read(line) == facts


@pytest.mark.parametrize(("line", "facts"), EMPHASIS)
def test_it_reads_as_the_same_line_with_stars(line, facts):
    """`_x_` and `*x*` are the same emphasis: the fact read is the one read with stars."""
    starred = line.replace("_", "*")
    assert read(line) == read(starred) == read(starred, old.extract_facts)


@pytest.mark.parametrize(("line", "facts"), EMPHASIS)
def test_the_fact_is_found_whole_in_its_own_span(line, facts):
    for span in chunk("s", line):
        body = normalize(span.text)
        for fact in extract_facts(span):
            assert _occurs(fact.norm, body), (fact.value, body)


IDENTIFIERS = [
    "max_retries_3 is set.",
    "v1_2 is a tag.",
    "notes_2026-03-14.md is a file.",
    "file_2.txt is a file.",
    "x_1.2.3 is a name.",
    "run_2026-03-14_a ran.",
    "Build v2.4.1-rc1_final shipped.",
    "release_2,048_final is a name.",  # build_1,250 changed in G2: PIECE_SHAPES
    "__init__ runs first; _private_2 is kept.",
    "snake_case_10 and _hello_ and a_ 3_b.",
    "_--json_ is a flag, _/srv/app_ a path, _https://h.example/x_ a link.",
    "/srv/v2_ and /srv/2_ end paths.",
]


@pytest.mark.parametrize("line", IDENTIFIERS)
def test_an_underscore_inside_a_word_reads_as_it_did(line):
    assert read(line) == read(line, old.extract_facts)
    assert normalize(line) == old.normalize(line)


def test_code_keeps_its_value_and_reads_emphasis_in_its_norm_as_its_page_does():
    """Code is one fact, as written; normalize() cannot tell code from prose, so its norm and
    the text that holds it read the run alike, and lint still finds it."""
    line = "`_2_` is code, and so is `(_2,700_)`."
    assert (
        read(line)
        == read(line, old.extract_facts)
        == [
            ("inline_code", "_2_"),
            ("inline_code", "(_2,700_)"),
        ]
    )
    (span,) = chunk("s", line)
    norms = [f.norm for f in extract_facts(span)]
    assert norms == ["*2*", "(*2700*)"]
    assert all(_occurs(norm, normalize(span.text)) for norm in norms)


def test_text_with_no_underscore_is_normalized_as_before():
    for text in ("2,700 more", "The Queue held 1,250 jobs.", "*2,700*", "a b\n\tc"):
        assert normalize(text) == old.normalize(text)


@pytest.fixture
def memory(tmp_path):
    store = Store(tmp_path / "cairn.db")
    store.create_project("probe")
    yield Memory(store, lint_gate)
    store.close()


SAVED_BEFORE = [  # the reproduction's seven neighbours, saved before and after
    "2,700 more comments are left out.",
    "*2,700 more comments are left out.*",
    "_2700 more comments are left out._",
    "__2,700__ more comments are left out.",
    "The queue held _1,250_ jobs at noon.",
    "The queue held 1,250 jobs at noon.",
    "_Version 2.10 shipped on 2099-03-02._",
]


@pytest.mark.parametrize("line", [line for line, _ in EMPHASIS] + SAVED_BEFORE)
def test_the_gate_saves_the_note(memory, line):
    answer = memory.note("probe", f"# Probe\n\nA first line.\n\n{line}\n")
    assert answer["status"] == "saved", answer.get("violations")
