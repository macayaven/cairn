"""G2: a number that normalize() joins across a comma never gets a note thrown away.

The contract. A note in ordinary prose is saved, every claim grounded, when it holds a number
that normalize() joins across a thousands separator but the extractor cannot read whole:
`build_1,250`, `run1,250`, `logs/batch_10,000/out.txt` (round one, names), and `a 1,250x
speedup`, `2,500,000rows`, `1234,567`, `/logs/batch_10,000/out.txt`, `v1.2,000` (round two).
Each was rejected (grounding.fact_not_in_cited_passages, retention.fact_dropped) for a
typographic detail. normalize(), which both sides of the check are read through, takes out a
comma that has a digit before it and exactly three digits after it. The extractor read a piece
of the joined run instead (250 of build_1,250, 1 of 1,250x, 1234 and 567 of 1234,567, the path
/logs/batch_10, the version v1.2). normalize() joins that piece to a digit, and lint finds no
whole token there, so a claim that was its passage word for word failed against that passage.

The rule. No fact is read that starts or ends at a comma normalize() takes out when the digit
across that comma is 0 to 9: such a fact is a piece of the joined run, and lint can never find
it. A fact that holds the whole run (1,250, $1,250, 2,500 ms) reads as before, and so does
normalize(). Digits of other scripts are not lint's token characters, so a piece next to one is
found and stays as it was.

Rejected:
- Reading 1,250 whole in build_1,250: lint's whole-token rule is frozen and never finds 1250
  inside build_1250.
- Keeping the comma in normalize(): it changes what normalize() reads for release_2,048_final
  and the code span `build_1,250`, where nothing was wrong.
- Reading 1234,567 as one number, or reading /logs/batch_10,000/out.txt as one path through the
  comma: lint would find either. Each changes exactly the same inputs as reading nothing (the
  ones that fail today), not fewer, so the one rule holds for every kind.

What it costs. A piece of such a run gives no fact, so the lint gate does not check those digits.
A draft that writes build_1,251 where its passage says build_1,250 passes `lint_gate`. It was
rejected before, by the misread 250; build_1251 for build_1250 has always passed. The verbatim
gate, which judges every draft a model makes, refuses it. `serve` gives `lint_gate` only the
extractive draft, which is the passage itself. A real number in the same sentence is still
checked by both gates, beside every shape.

Controls: a grouped number alone, after a hyphen, inside a word on both sides, in a code span,
names with a plain number, a decimal, a date or a version, and ten ordinary sentences that were
saved before (an ISO timestamp, v2026-03-14, 1250x, 3,14, a percent, a time of day, an ordinal,
$1,250, a hex id, an IPv4 address) read exactly as before.

Everything runs offline over real stores in temporary files, through REST and MCP over HTTP in
process, with the app built with the lint gate as `serve` builds it, on made-up notes.
"""

import json

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.compiler import CandidatePage
from cairn.facts import extract_facts, normalize
from cairn.gate import lint_gate, verbatim_gate
from cairn.server import create_app
from cairn.spans import chunk
from cairn.store import Claim, Store

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}

# The three shapes that were rejected, as the reproduction wrote them.
SHAPES = [
    "The failing job is build_1,250 on the main branch.",
    "The failing job is run1,250 on the main branch.",
    "The log is at logs/batch_10,000/out.txt on the host.",
]

# Their neighbours, each a whole note body under its own heading.
NEIGHBOURS = {
    "start of a sentence": "build_1,250 failed on the main branch.",
    "end before a full stop": "The failing job is build_1,250.",
    "bullets": "- build_1,250 failed\n- run1,250 passed\n- logs/batch_10,000/out.txt was kept",
    "table cell": "| job | state |\n| --- | --- |\n| build_1,250 | failed |\n| run1,250 | passed |",
    "twice in one note": (
        "build_1,250 failed.\n\nThen build_1,250 failed again, and so did run1,250."
    ),
    "beside a real number": "Job build_1,250 failed after 3,400 retries.",
    "a longer grouping": "The trace id is build_1,250,000 for the run.",
    "in a quote": "> The failing job is build_1,250 on the main branch.",
    "in emphasis": "The failing job is **build_1,250** and then _run1,250_ on the main branch.",
}

# Controls: saved before and after, each read as listed (kind, value), and normalized as listed.
CONTROLS = [
    ("The run processed 1,250 rows on the main branch.", [("number", "1,250")]),
    ("The failing job is build-1,250 on the main branch.", [("number", "1,250")]),
    ("The failing job is build_1250 on the main branch.", []),
    ("Deploy tag is release_2,048_final for the service.", []),
    ("The failing job is `build_1,250` on the main branch.", [("inline_code", "build_1,250")]),
    ("The threshold key is limit_0.75 in the config.", []),
    ("The snapshot is backup_2099-03-02 on the bucket.", [("number", "03"), ("number", "02")]),
    ("Build v2.4.1-rc1_final shipped.", [("version", "v2.4.1")]),
]
NORMALIZED = {
    "Deploy tag is release_2,048_final for the service.": (
        "deploy tag is release_2048_final for the service."
    ),
    "The failing job is `build_1,250` on the main branch.": (
        "the failing job is `build_1250` on the main branch."
    ),
    "The failing job is build_1,250 on the main branch.": (
        "the failing job is build_1250 on the main branch."
    ),
}


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cairn.db"


@pytest.fixture
def key(db):
    store = Store(db)
    try:
        store.create_project("ops")
        return store.create_key("laptop")
    finally:
        store.close()


def served(db) -> TestClient:
    """The app as `serve` builds it, with the lint gate: create_app's own default gate checks
    provenance only and would save these notes either way."""
    return TestClient(create_app(Store(db), gate=lint_gate, public_url=BASE), base_url=BASE)


def call(client: TestClient, key: str, name: str, **arguments) -> dict:
    """An MCP tool called over HTTP, in process: the result."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    body["params"] = {"name": name, "arguments": arguments}
    answer = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
    return answer.json()["result"]


def post(client: TestClient, key: str, text: str) -> httpx.Response:
    return client.post("/api/projects/ops/notes", headers={"x-api-key": key}, json={"text": text})


def page(client: TestClient, key: str, slug: str) -> dict:
    answer = client.get(f"/api/projects/ops/pages/{slug}", headers={"x-api-key": key})
    assert answer.status_code == 200, answer.text
    return answer.json()


def read(text: str) -> list[tuple[str, str]]:
    return [(f.kind, f.value) for s in chunk("s", text) for f in extract_facts(s)]


def assert_saved_whole(client: TestClient, key: str, answer: dict, body: str) -> None:
    """Saved with no violation; each line of the body is on the page; nothing was dropped."""
    assert answer["status"] == "saved" and answer["violations"] == [], answer
    shown = page(client, key, answer["page"])
    for line in filter(str.strip, body.splitlines()):
        assert line in shown["body"], (line, shown["body"])
    assert shown["metrics"]["retention"] == 1.0, shown["metrics"]


def test_a_grouped_number_in_a_name_gives_no_fact():
    assert read(SHAPES[0]) == []
    assert read(SHAPES[1]) == []
    assert read(SHAPES[2]) == []
    assert read(NEIGHBOURS["beside a real number"]) == [("number", "3,400")]
    assert read("The trace id is build_1,250,000 for the run.") == []


@pytest.mark.parametrize("line", SHAPES)
def test_each_shape_is_saved_over_rest_and_mcp(db, key, line):
    with served(db) as client:
        rest = post(client, key, f"# Over REST\n\n{line}\n")
        assert rest.status_code == 201, rest.json()
        assert_saved_whole(client, key, rest.json(), line)
        tool = call(client, key, "note", project="ops", text=f"# Over MCP\n\n{line}\n")
        assert tool["isError"] is False, tool
        assert_saved_whole(client, key, json.loads(tool["content"][0]["text"]), line)


@pytest.mark.parametrize("name", sorted(NEIGHBOURS))
def test_each_neighbour_is_saved_with_every_claim_grounded(db, key, name):
    body = NEIGHBOURS[name]
    with served(db) as client:
        rest = post(client, key, f"# Neighbour\n\n{body}\n")
        assert rest.status_code == 201, rest.json()
        assert_saved_whole(client, key, rest.json(), body)
        tool = call(client, key, "note", project="ops", text=f"# Neighbour by tool\n\n{body}\n")
        assert tool["isError"] is False, tool
        assert_saved_whole(client, key, json.loads(tool["content"][0]["text"]), body)


@pytest.mark.parametrize(("line", "facts"), CONTROLS)
def test_the_controls_are_saved_and_read_as_before(db, key, line, facts):
    assert read(line) == facts
    if line in NORMALIZED:
        assert normalize(line) == NORMALIZED[line]
    with served(db) as client:
        rest = post(client, key, f"# Control\n\n{line}\n")
        assert rest.status_code == 201, rest.json()
        assert_saved_whole(client, key, rest.json(), line)


def test_normalize_reads_a_name_with_a_grouped_number_as_before():
    assert normalize(SHAPES[0]) == NORMALIZED[SHAPES[0]]


# A draft that changes a number, through the gates directly


def judge(gate, passage: str, draft: str):
    (span,) = [s for s in chunk("src", f"# Job\n\n{passage}\n") if s.kind == "paragraph"]
    claim = Claim(id="cl_1", text=draft, span_ids=(span.id,))
    candidate = CandidatePage(slug="job", title="Job", body=f"# Job\n\n{draft}\n", claims=(claim,))
    return gate(candidate, {span.id: span})


def codes(result) -> set[str]:
    return {v.split(":")[0] for v in result.violations}


PASSAGE = "Job build_1,250 failed after 3,400 retries."


def test_the_passage_word_for_word_passes_both_gates():
    assert judge(lint_gate, PASSAGE, PASSAGE).passed
    assert judge(verbatim_gate, PASSAGE, PASSAGE).passed


def test_a_draft_that_changes_the_real_number_beside_it_is_rejected_by_the_lint_gate():
    result = judge(lint_gate, PASSAGE, "Job build_1,250 failed after 3,401 retries.")
    assert not result.passed
    assert codes(result) == {"grounding.fact_not_in_cited_passages", "retention.fact_dropped"}


def test_a_draft_that_changes_the_name_is_rejected_by_the_gate_for_a_models_draft():
    draft = "Job build_1,251 failed after 3,400 retries."
    result = judge(verbatim_gate, PASSAGE, draft)
    assert not result.passed and "grounding.not_verbatim" in codes(result)
    # A name is not a fact: the lint gate treats build_1,251 for build_1,250 as it treats
    # build_1251 for build_1250, the same verdict and the same findings.
    plain = judge(lint_gate, PASSAGE.replace("1,250", "1250"), draft.replace("1,251", "1251"))
    grouped = judge(lint_gate, PASSAGE, draft)
    assert (grouped.passed, grouped.violations) == (plain.passed, plain.violations)


# Round two: the rest of the family, in ordinary prose

FAMILY = [
    "The new index gives a 1,250x speedup on the large table.",
    "The table holds 2,500,000rows after the import.",
    "The measured value was 1234,567 in the second run.",
    "The log is at /logs/batch_10,000/out.txt on the host.",
    "The range is v1.2,000 items wide in the config.",
]

# Saved before round two, each with what it reads (kind, value): it must read exactly so.
SAVED_BEFORE = [
    (
        "The deploy finished at 2026-03-14T10:00:00Z without errors.",
        [("date", "2026-03-14T10:00:00Z")],
    ),
    (
        "The deploy finished at 2026-03-14T10:00:00+01:00 without errors.",
        [("date", "2026-03-14T10:00:00+01:00")],
    ),
    ("The snapshot tag is v2026-03-14 on the bucket.", [("version", "v2026-03-14")]),
    ("The new index gives a 1250x speedup on the large table.", []),
    ("The measured value was 3,14 in the second run.", [("number", "3"), ("number", "14")]),
    ("Coverage rose to 97.5% after the change.", [("number", "97.5%")]),
    ("The job starts at 10:30 every morning.", [("number", "10"), ("number", "30")]),
    ("It failed on the 3rd attempt of 5.", [("number", "5")]),
    ("The plan costs $1,250 a month for 3 seats.", [("number", "$1,250"), ("number", "3")]),
    ("The commit is 0b153ea on the main branch.", []),
    ("The host answers at 10.0.0.12 on port 8443.", [("version", "10.0.0.12"), ("number", "8443")]),
]

MIXED = (
    "The failing job is build_1,250 and the new index gives a 1,250x speedup.\n\n"
    "- The table holds 2,500,000rows and the measured value was 1234,567.\n"
    "- The log is at /logs/batch_10,000/out.txt and the range is v1.2,000 items wide.\n\n"
    "| plan | price |\n| --- | --- |\n| team | $1,250 for 3 seats |\n| run1,250 | 97.5% |\n\n"
    "It finished at 2026-03-14T10:00:00Z on 10.0.0.12, tag v2026-03-14, value 3,14.\n"
)


def test_a_piece_of_a_joined_run_gives_no_fact():
    for line in FAMILY:
        assert read(line) == [], line
    assert normalize(FAMILY[2]) == "the measured value was 1234567 in the second run."


@pytest.mark.parametrize("line", FAMILY)
def test_each_sentence_of_the_family_is_saved_over_rest_and_mcp(db, key, line):
    with served(db) as client:
        rest = post(client, key, f"# Family over REST\n\n{line}\n")
        assert rest.status_code == 201, rest.json()
        assert_saved_whole(client, key, rest.json(), line)
        tool = call(client, key, "note", project="ops", text=f"# Family over MCP\n\n{line}\n")
        assert tool["isError"] is False, tool
        assert_saved_whole(client, key, json.loads(tool["content"][0]["text"]), line)


@pytest.mark.parametrize(("line", "facts"), SAVED_BEFORE)
def test_each_sentence_saved_before_is_saved_and_read_as_before(db, key, line, facts):
    assert read(line) == facts
    with served(db) as client:
        rest = post(client, key, f"# Saved before\n\n{line}\n")
        assert rest.status_code == 201, rest.json()
        assert_saved_whole(client, key, rest.json(), line)
        tool = call(client, key, "note", project="ops", text=f"# Saved by tool\n\n{line}\n")
        assert tool["isError"] is False, tool
        assert_saved_whole(client, key, json.loads(tool["content"][0]["text"]), line)


def test_a_note_that_mixes_them_is_saved_over_rest_and_mcp(db, key):
    with served(db) as client:
        rest = post(client, key, f"# Mixed\n\n{MIXED}")
        assert rest.status_code == 201, rest.json()
        assert_saved_whole(client, key, rest.json(), MIXED)
        tool = call(client, key, "note", project="ops", text=f"# Mixed by tool\n\n{MIXED}")
        assert tool["isError"] is False, tool
        assert_saved_whole(client, key, json.loads(tool["content"][0]["text"]), MIXED)


# Beside each shape, a real number in the same sentence: (passage, the draft that changes it).
BESIDE = [
    "The new index gives a 1,250x speedup on 3,400 rows.",
    "The table holds 2,500,000rows after 3,400 retries.",
    "The measured value was 1234,567 after 3,400 retries.",
    "The log at /logs/batch_10,000/out.txt holds 3,400 lines.",
    "The range is v1.2,000 items wide after 3,400 retries.",
    "The job run1,250 failed after 3,400 retries.",
]


@pytest.mark.parametrize("passage", BESIDE)
def test_a_real_number_beside_each_shape_is_still_checked(passage):
    assert judge(lint_gate, passage, passage).passed
    assert judge(verbatim_gate, passage, passage).passed
    result = judge(lint_gate, passage, passage.replace("3,400", "3,401"))
    assert not result.passed
    assert codes(result) == {"grounding.fact_not_in_cited_passages", "retention.fact_dropped"}
