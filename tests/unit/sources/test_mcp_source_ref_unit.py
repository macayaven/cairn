"""The MCP server tells an agent what `source_ref` to write for a note copied from a meeting, a
mail thread, a calendar, a document or a to-do list: the recipe's form (M3,
tests/acceptance/test_m3_notes_that_name_a_source.py), in the `note` tool's description of
`source_ref` and in the instructions. It says what to write, never what a model is sent."""

import re

from cairn.sources import SOURCE_REF_FORMS
from cairn.web.brand import Brand
from cairn.web.mcp import INSTRUCTIONS, build_mcp

COPIED = "copied from a mail thread, a meeting, a calendar, a document or a to-do list"


def note_s_source_ref(mcp) -> str:
    (tool,) = [t for t in mcp._tool_manager.list_tools() if t.name == "note"]
    return tool.parameters["properties"]["source_ref"]["description"]


def check_tells_the_forms(said: str) -> None:
    flat = " ".join(said.split())
    assert COPIED in flat, flat
    assert set(re.findall(r"`?([a-z]+):`?", flat)) >= SOURCE_REF_FORMS, flat
    assert "model" not in flat.lower(), flat


def test_the_note_tool_s_source_ref_says_the_recipe_s_forms():
    said = note_s_source_ref(build_mcp())
    check_tells_the_forms(said)
    assert said.startswith("Where the note came from: a session, commit or file")


def test_the_instructions_say_the_recipe_s_forms_where_they_mention_source_ref():
    (bullet,) = [p for p in INSTRUCTIONS.split("\n- ") if "`source_ref`" in p]
    check_tells_the_forms(bullet)
    assert "(a session, commit or file" in " ".join(bullet.split())


def test_under_a_brand_it_says_the_same():
    branded = build_mcp(Brand("Knowledge Base"))
    check_tells_the_forms(note_s_source_ref(branded))
    assert "cairn" not in note_s_source_ref(branded).lower()


def test_the_check_sees_a_doctored_sentence():
    said = note_s_source_ref(build_mcp())
    for doctored in (said.replace("`todo:`", ""), said.replace(", a calendar", "")):
        try:
            check_tells_the_forms(doctored)
        except AssertionError:
            continue
        raise AssertionError(f"not seen: {doctored}")
