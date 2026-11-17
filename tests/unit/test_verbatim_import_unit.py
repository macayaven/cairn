"""T5d: whatever the verbatim gate passes, an import passes too.

An import takes a claim when its text is the text of a passage it cites, white space aside
(cairn.store.portable._page_version, which collapses white space with str.split: every Unicode
white space character). The verbatim gate reads only space, tab, CR and LF as white space, and
asks the claim to be every passage it cites, so it is the stricter of the two. This test calls
the import's own check on every claim the gate passes, over generated passages that hold the
Unicode white space where the two comparisons part: a no-break space, a line or paragraph
separator, a form feed, a vertical tab, a next-line character, an ideographic space.
"""

import random

import pytest

from cairn.compiler import CONTENT_KINDS
from cairn.gate import _not_verbatim
from cairn.spans import Span, chunk
from cairn.store import Claim
from cairn.store.portable import _collapsed, _page_version

# Read as white space by the gate (the first four) or by str.split alone (the rest), by code
# point so that this file shows each one.
GATE_SPACES = (" ", "\t", "\r\n", "\n")
OTHER_SPACES = tuple(map(chr, (0x00A0, 0x2028, 0x2029, 0x000C, 0x000B, 0x0085, 0x3000, 0x1680)))
WORDS = ("drift", "mirrors", "two", "buckets.", "Safe", "only", "after", "review.", "-", ">", "1.")


def text_of(rng: random.Random, spaces: tuple[str, ...]) -> str:
    found = [rng.choice(WORDS) for _ in range(rng.randint(2, 9))]
    return found[0] + "".join(rng.choice(spaces) + word for word in found[1:])


def note(rng: random.Random) -> str:
    blocks = []
    for _ in range(rng.randint(1, 4)):
        body = text_of(rng, GATE_SPACES[:2] + OTHER_SPACES)
        kind = rng.randrange(5)
        if kind == 1:
            body = f"- {body}"
        elif kind == 2:
            body = f"> {body}"
        elif kind == 3:
            body = f"```\n{body}\n```"
        elif kind == 4:
            body = f"| {body} |\n|---|\n| x |"
        blocks.append(body)
    return "# P\n\n" + "\n\n".join(blocks) + "\n"


def claims_of(span: Span, rng: random.Random) -> list[str]:
    """The passage whole, rewrapped with the gate's white space, and with one space of either
    kind put in place of another."""
    text = span.text
    out = [text, f"\n{text}\n"]
    for _ in range(3):
        out.append("".join(c if c != " " else rng.choice(GATE_SPACES) for c in text))
    for spaces in (GATE_SPACES, OTHER_SPACES):
        at = [n for n, c in enumerate(text) if c.isspace()]
        if at:
            n = rng.choice(at)
            out.append(text[:n] + rng.choice(spaces) + text[n + 1 :])
    return out


def imported(text: str, cited: list[Span]) -> bool:
    """Whether an archive's page that holds this one claim, citing these passages, is taken."""
    line = {
        "slug": "p",
        "version": 1,
        "title": "P",
        "body": "",
        "claims": [{"id": "cl_" + "0" * 16, "text": text, "passage_ids": [s.id for s in cited]}],
        "metrics": {},
        "created_at": "2026-11-15T00:00:00+00:00",
    }
    try:
        _page_version("archive/", line, {}, {s.id: _collapsed(s.text) for s in cited})
    except ValueError:
        return False
    return True


@pytest.mark.parametrize("seed", range(20))
def test_whatever_the_gate_passes_an_import_takes(seed):
    rng = random.Random(seed)
    passed = differ = 0
    for _ in range(25):
        spans = [s for s in chunk("cap_000000000000", note(rng)) if s.kind in CONTENT_KINDS]
        for span in spans:
            again = chunk("cap_111111111111", f"# P\n\n{span.text}\n")
            fold = [span, *(s for s in again if s.text == span.text)]  # another note says it
            for cited in ([span], fold):
                by_id = {s.id: s for s in cited}
                for text in claims_of(span, rng):
                    claim = Claim(id="cl_" + "0" * 16, text=text, span_ids=tuple(by_id))
                    gate = _not_verbatim(claim, by_id) is None
                    takes = imported(text, cited)
                    if gate:
                        passed += 1
                        assert takes, (text, [s.text for s in cited])
                    elif takes:
                        differ += 1  # the import is the looser of the two
    assert passed > 100 and differ > 0, (passed, differ)
