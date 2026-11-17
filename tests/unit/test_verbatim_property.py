"""A seeded property test for cairn.gate.verbatim_gate (T5c, T5d).

Random notes are built from a small grammar of Markdown blocks. For each, the extractive draft
must pass the gate, and every single-character deletion, insertion or swap inside a claim must
either be refused or leave the claim equal to its passage under the rules: for a block, the
same lines, blank lines at the two ends aside (exactly for prose that holds structure, white
space at the end of each line aside for code and tables); for plain prose, the same characters
once each run of space, tab, CR and LF is one space, with every list or quote marker kept and
still at the start of its line.
"""

import random
import re

import pytest

from cairn.compiler import CandidatePage, extractive, render_body
from cairn.gate import LINE_KINDS, PROSE_KINDS, _not_verbatim, _structure, verbatim_gate
from cairn.spans import Span, chunk
from cairn.store import Claim

WORDS = ("drift", "mirrors", "two", "buckets", "safe", "run", "it", "then", "go", "now", "e.g.")
EXTRAS = ("`a. B`", "[c. D](https://example.test/e.f)", "[g][h]", "--dry-run", "a|b", "C:\\x")
INSERTS = "aZ .`[]()->|\\\n#*!=_<:1"
_SPACE = re.compile(r"[ \t\r]+")
_MARK = re.compile(r"(?:[-*+](?: |$)|> ?)")


def sentence(rng: random.Random) -> str:
    words = [rng.choice(WORDS) for _ in range(rng.randint(1, 4))]
    if rng.random() < 0.4:
        words.insert(rng.randint(0, len(words)), rng.choice(EXTRAS))
    words[0] = words[0][0].upper() + words[0][1:]
    return " ".join(words) + rng.choice(".!?")


def sentences(rng: random.Random, most: int = 3) -> list[str]:
    return [sentence(rng) for _ in range(rng.randint(1, most))]


def block(rng: random.Random) -> str:
    kind = rng.choice([0, 0, 0, 1, 1, 2, 3, 3, *range(4, 11)])  # plain prose most often
    if kind == 0:  # a paragraph, maybe hard-wrapped
        words = " ".join(sentences(rng)).split(" ")
        cut = rng.randint(1, len(words))
        return " ".join(words[:cut]) + ("\n" + " ".join(words[cut:]) if words[cut:] else "")
    if kind == 1:  # a list, maybe nested
        lines = [f"- {s}" for s in sentences(rng)]
        if rng.random() < 0.5:
            lines.insert(1, f"  - {sentence(rng)}")
        return "\n".join(lines)
    if kind == 2:  # an ordered list
        return "\n".join(f"{n}. {s}" for n, s in enumerate(sentences(rng), 1))
    if kind == 3:  # a quote
        return "\n".join(f"> {s}" for s in sentences(rng))
    if kind == 4:
        return f"```bash\ndrift sync --src ./{rng.choice('ab')}\n```"
    if kind == 5:
        return "| A | B |\n|---|---|\n| x | y |"
    if kind == 6:  # a hard line break
        return f"{sentence(rng)}{rng.choice(['  ', '\\'])}\n{sentence(rng)}"
    if kind == 7:  # a rule between two lines
        return f"{sentence(rng)}\n---\n{sentence(rng)}"
    if kind == 8:  # indented code
        return f"    {rng.choice(WORDS)}()\n    {sentence(rng)}"
    if kind == 9:
        return f"<details>\n{' '.join(sentences(rng))}\n</details>"
    return f"{' '.join(sentences(rng))}\n[h]: https://example.test/h"


def note(rng: random.Random) -> str:
    return "# P\n\n" + "\n\n".join(block(rng) for _ in range(rng.randint(1, 4))) + "\n"


def as_read(text: str, span: Span) -> str | tuple[str, ...]:
    """The claim text as the rules read it for the passage it cites. A block's lines, blank
    lines at the two ends aside: exactly for prose that holds structure, white space at the end
    of each line aside for code and tables; otherwise the prose itself, normalized."""
    exact = span.kind in PROSE_KINDS and _structure(span.text.split("\n")) is not None
    if exact or span.kind in LINE_KINDS:
        rows = [line.rstrip("\r") if exact else line.rstrip(" \t\r") for line in text.split("\n")]
        while rows and not rows[0].strip(" \t\r"):
            rows.pop(0)
        while rows and not rows[-1].strip(" \t\r"):
            rows.pop()
        return tuple(rows)
    return normalized(text)


def normalized(text: str) -> str:
    """Prose as the rules read it: each run of white space one space, the ends off, and a line
    that opens with a list or quote marker, or follows a line that is a marker alone, still on
    a line of its own."""
    out = ""
    for line in text.split("\n"):
        line = _SPACE.sub(" ", line).strip(" ")
        if not line:
            continue
        if out and (_MARK.match(line) or _MARK.fullmatch(out.rsplit("\n", 1)[-1])):
            out += "\n" + line
        else:
            out += (" " if out else "") + line
    return out


def mutations(text: str, rng: random.Random, count: int):
    for _ in range(count):
        at = rng.randrange(len(text))
        how = rng.randrange(3)
        if how == 0:
            yield text[:at] + text[at + 1 :]
        elif how == 1:
            yield text[:at] + rng.choice(INSERTS) + text[at:]
        elif at + 1 < len(text) and text[at] != text[at + 1]:
            yield text[:at] + text[at + 1] + text[at] + text[at + 2 :]


@pytest.mark.parametrize("seed", range(100))
def test_the_extractive_draft_passes_and_any_one_character_edit_is_refused(seed):
    rng = random.Random(seed)
    text = note(rng)
    spans = [s for s in chunk("cap_000000000000", text) if s.kind not in ("heading", "frontmatter")]
    by_id = {s.id: s for s in spans}
    claims = extractive("P", spans).claims
    page = CandidatePage(slug="p", title="P", body=render_body("P", claims, by_id), claims=claims)
    result = verbatim_gate(page, by_id)
    assert result.passed, (text, result.violations)
    for claim in claims:
        for edited in mutations(claim.text, rng, 40):
            changed = Claim(id=claim.id, text=edited, span_ids=claim.span_ids)
            if _not_verbatim(changed, by_id) is None:
                (span,) = [by_id[i] for i in claim.span_ids]
                assert as_read(edited, span) == as_read(claim.text, span), (text, edited)
