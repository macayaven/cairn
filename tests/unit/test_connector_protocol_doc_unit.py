"""docs/connector-protocol.md against the code that keeps it (C9): each bound its schemas and
its sentences state is the one cairn.sources.connector holds an answer to, and the checker's
rules are listed in the order connector-check prints them. Read from the repository; offline."""

import json
import re
from pathlib import Path

from cairn.sources import connector as c
from cairn.sources import connector_check as check
from cairn.sources.runner import RUN_LIMIT, SOURCE_TIMEOUT
from cairn.store.memory import (
    HEADINGS_MAX,
    HEADINGS_OVER_PASSAGES,
    PAGE_CHARS_MAX,
    PAGE_CLAIMS_MAX,
    TITLE_MAX,
)
from cairn.store.sources import SOURCE_CLAIM_TTL

SPEC = Path(__file__).resolve().parents[2] / "docs" / "connector-protocol.md"
TEXT = SPEC.read_text()
FLAT = " ".join(TEXT.split())


def schemas() -> list[dict]:
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", TEXT, re.S)]
    return [b for b in blocks if "$schema" in b]


def schema(title_word: str) -> dict:
    (found,) = [s for s in schemas() if title_word in s["title"]]
    return found


def test_every_json_block_parses_and_the_two_schemas_are_there():
    blocks = re.findall(r"```json\n(.*?)```", TEXT, re.S)
    assert [json.loads(b) for b in blocks]
    assert len(schemas()) == 2


def test_the_request_schema_holds_the_kind_s_bounds():
    request = schema("request")["properties"]
    _, least, most = c.LIMIT
    assert (request["limit"]["minimum"], request["limit"]["maximum"]) == (least, most)
    assert request["cursor"]["maxLength"] == c.CURSOR_MAX
    assert request["protocol"] == {"const": c.PROTOCOL}
    assert set(schema("request")["required"]) == set(
        c.request_body(c.moment("2099-01-01T00:00:00Z"), None, 1)
    )


def test_the_answer_schema_holds_the_kind_s_bounds():
    answer = schema("answer")
    item = answer["$defs"]["item"]["properties"]
    assert answer["properties"]["cursor"]["maxLength"] == c.CURSOR_MAX
    assert answer["properties"]["items"]["maxItems"] == c.LIMIT[2]
    assert item["id"]["maxLength"] == c.ID_MAX
    assert item["title"]["maxLength"] == c.TITLE_MAX
    assert item["topic"]["maxLength"] == c.TOPIC_MAX
    assert item["source_ref"]["maxLength"] == c.SOURCE_REF_MAX
    assert set(answer["required"]) == {"protocol", "items", "more"}  # cursor may be left out
    assert set(answer["$defs"]["item"]["required"]) == {"id", "text"}


def test_the_example_answer_keeps_the_rules_it_states():
    (example,) = [b for b in map(json.loads, re.findall(r"```json\n(.*?)```", TEXT, re.S))
                  if "items" in b and "$schema" not in b]  # fmt: skip
    since = c.moment("2099-11-14T09:00:00Z")
    read = c.examine(example, sent=None, limit=50, since=since, secrets=("made-up",))
    assert read.findings == [] and all(isinstance(i, c.Item) for i in read.items)


def test_the_sentences_state_the_numbers_the_code_holds():
    assert f"{c.ANSWER_MAX_BYTES // (1024 * 1024)} MiB ({c.ANSWER_MAX_BYTES:,} bytes)" in FLAT
    assert f"at most {c.URL_MAX:,} characters" in FLAT
    assert f"at most {c.CURSOR_MAX:,} characters" in FLAT
    assert f"at most {c.ERROR_SENTENCE_MAX} characters" in FLAT
    assert f"at most {c.ERROR_BYTES // 1024} KiB" in FLAT
    assert f"at most {c.ANSWERS_PER_RUN} answers" in FLAT
    assert f"{c.PULL_BYTES // (1024 * 1024)} MiB of answers" in FLAT
    assert f"at most {int(SOURCE_TIMEOUT)} seconds to connect" in FLAT
    assert f"within {int(check.ANSWER_SECONDS)} seconds" in FLAT
    assert f"first {TITLE_MAX} characters" in FLAT
    assert f"take at most {HEADINGS_MAX} bytes in all" in FLAT
    assert f"outnumber its passages by at most {HEADINGS_OVER_PASSAGES}" in FLAT
    assert f"hold at most {PAGE_CHARS_MAX:,} characters in all" in FLAT  # K4: a page's bounds
    assert f"and at most {PAGE_CLAIMS_MAX:,} of them" in FLAT
    claim, given = SOURCE_CLAIM_TTL.seconds // 60, RUN_LIMIT.seconds // 60
    assert f"at most {claim} minutes, longer than the {given} minutes a run is given" in FLAT
    assert f"({c.LIMIT[0]} unless" in FLAT and f"`--limit` is {c.LIMIT[0]}" in FLAT
    assert f"{check.LOOK_BACK.days} days ago" in FLAT


def test_the_checker_s_rules_are_listed_in_the_order_it_prints_them():
    listed = re.findall(r"^\d+\. (.+?)(?::|;|\.|$)", TEXT, re.M)
    titles = [check.TITLES[rule] for rule in check.RULES]
    assert [t for t in listed if t in titles] == titles


def test_it_names_no_brand_and_says_what_it_is():
    assert TEXT.startswith("# The connector protocol, version 1")
    assert "cairn" in TEXT and "{{" not in TEXT
