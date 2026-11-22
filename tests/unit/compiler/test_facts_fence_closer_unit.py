"""The fact reader closes a fence by cairn.spans.fence_closes, the splitter's own rule: over a
table of openers and closers, a code block's facts end where the splitter's code passage ends.
Made-up text."""

import itertools

import pytest

from cairn import facts, spans
from cairn.facts import extract_facts
from cairn.spans import chunk

OPENERS = ["```", "````", "~~~", "  ```", "```py"]
CLOSERS = ["```", "````", "``", "~~~", "   ```", "    ```", "      ```", "\t```", "``` x", "```  "]


def test_the_fact_reader_calls_the_shared_closer_rule():
    assert facts.fence_closes is spans.fence_closes


@pytest.mark.parametrize(("opener", "closer"), list(itertools.product(OPENERS, CLOSERS)))
def test_a_code_block_ends_where_the_splitters_passage_ends(opener, closer):
    text = f"{opener}\nrun it\n{closer}\nafter"
    (first, *_) = chunk("n1", text)
    by_splitter = first.end_line == 3  # the passage ends at the closer
    (block,) = [f for f in extract_facts(first) if f.kind == "code_block"]
    by_facts = block.value.strip("\n") == "run it"
    assert by_facts == by_splitter, (opener, closer)
