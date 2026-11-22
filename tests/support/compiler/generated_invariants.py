"""Shared test helpers extracted from test_generated_invariants_unit."""

import json
import random
import re

from markdown_it import MarkdownIt

from cairn.compiler import (
    CandidatePage,
    provenance_gate,
    render_body,
)
from cairn.gate import lint_gate, strict_lint_gate, unclaimed_line, verbatim_gate
from cairn.llm import Reply
from cairn.store import Store

GATES = (provenance_gate, lint_gate, strict_lint_gate, verbatim_gate)

SPACES = [" ", "  ", "\t", "\u00a0", "\u2003", "\u3000", " \u00a0"]

WORDS = ["Ada", "Brook", "Quill", "keeps", "the", "nightly", "log", "Fable", "Works", "port"]

ODD = ["\ue000", "\uf8ff", "\ufdd0", "\ufffe", "\U000f0000", "\U0010fffd"]

OPENERS = ["```", "~~~", "````", "``` sh", "~~~~"]

BODY = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads


def words(rng: random.Random, n: int | None = None) -> str:
    picked = [rng.choice(WORDS) for _ in range(n or rng.randint(1, 5))]
    out = picked[0]
    for word in picked[1:]:
        out += rng.choice(SPACES[:1] * 6 + SPACES) + word
    return out + rng.choice([".", "", ":"])


def inline_code(rng: random.Random) -> str:
    inside = rng.choice(["a  b", " a ", "a\tb", "a\u00a0b", "x`y", "", " ", "a\nb"])
    run = "``" if "`" in inside else rng.choice(["`", "``"])
    return f"{words(rng, 2)} {run}{inside}{run} {words(rng, 1)}"


def fence(rng: random.Random) -> str:
    opener = rng.choice(OPENERS)
    mark = opener.split()[0]
    closer = rng.choice(
        [
            mark,
            mark + mark[0],
            " " * rng.randint(1, 4) + mark,
            "\u00a0" + mark,
            "\t" + mark,
            mark + "\u00a0x",
            ("~~~" if mark[0] == "`" else "```"),
            mark[:-1] if len(mark) > 3 else mark + " x",
            None,  # left open
        ]
    )
    lines = [opener, words(rng), *(["", words(rng)] if rng.random() < 0.3 else [])]
    return "\n".join(lines + ([closer] if closer is not None else []))


def block(rng: random.Random, made: list[str]) -> str:
    kind = rng.randrange(16)
    if kind == 0 and made:
        return rng.choice(made)  # a paragraph repeated
    if kind == 1 and made:
        first = rng.choice(made)
        return first + "\n" + words(rng)  # one paragraph a prefix of another
    if kind == 2:
        return "\n".join("> " + words(rng) for _ in range(rng.randint(1, 3)))
    if kind == 3:
        return "> " + words(rng) + "\n>\n> " + inline_code(rng)
    if kind == 4:
        return "\n".join(f"- {words(rng)}" for _ in range(rng.randint(1, 3)))
    if kind == 5:
        return f"- {words(rng)}\n  - {inline_code(rng)}\n    - {words(rng)}"
    if kind == 6:
        return fence(rng)
    if kind == 7:
        return f"- {words(rng)}\n  " + fence(rng).replace("\n", "\n  ")
    if kind == 8:
        return inline_code(rng)
    if kind == 9:
        return "#" * rng.randint(1, 8) + " " + words(rng, 2)
    if kind == 10:
        return words(rng) + "\n" + rng.choice(["===", "---"])
    if kind == 12 and len(made) > 1:  # the first lines of two earlier passages, joined
        one, two = rng.sample(made, 2)
        return one.split("\n")[0] + "\n" + two.split("\n")[0]
    if kind == 11:  # two passages that differ only by white space inside inline code
        spaced = rng.choice(["a  b.txt", "a\tb.txt", "a \u00a0b.txt"])
        lead = rng.choice(["", "> Stray `\n>\n> "])  # a backtick a quote leaves as text
        return f"{lead}Keep `{spaced}` here.\n\n{lead}Keep `a b.txt` here."
    if kind in (13, 14):  # the shapes that got through before CORE5, each next to its twin
        shape = rng.choice([link, escaped, lazy, nested, definition, odd])
        one = shape(rng)
        return one + "\n\n" + spaced_once(rng, one)
    return words(rng)


def link(rng: random.Random) -> str:
    """Backticks in a link's destination and title, around inline code (finding 1)."""
    inside = rng.choice(["a  b.txt", "a b.txt", "a\tb.txt"])
    title = rng.choice(["", ' "t`"', " 'x`y'"])
    return f"[x](a`{title}) `{inside}` [y](b`)"


def escaped(rng: random.Random) -> str:
    """A literal backtick before code-like text (finding 4)."""
    return f"Keep \\`{rng.choice(['a  b.txt', 'a b.txt'])}` here."


def lazy(rng: random.Random) -> str:
    """A lazy line after a quote or a list item (finding 2)."""
    mark = rng.choice(["> ", "- ", "1. "])
    return f"{mark}Stray `\n{rng.choice(['x  y', 'x y'])}` z` q``w``"


def nested(rng: random.Random) -> str:
    """Nested lists with varying indentation (finding 8 of the cross-reader)."""
    lines, depth = ["- " + words(rng, 1)], 0
    for _ in range(rng.randint(1, 3)):
        depth = rng.choice([depth, depth + 2, depth + 4, max(depth - 2, 0)])
        lines.append(" " * depth + "- " + words(rng, 1))
    return "\n".join(lines)


def definition(rng: random.Random) -> str:
    return f"[{rng.choice(['x', 'ref'])}]: /target{rng.choice(['', ' "t"', '`'])}"


def odd(rng: random.Random) -> str:
    return f"{words(rng, 2)}{rng.choice(ODD)}{rng.choice([' ', '  ', '\u00a0'])}{words(rng, 1)}"


def spaced_once(rng: random.Random, text: str) -> str:
    """The text with one white-space edit inside it: a space doubled, a no-break space for a
    space, or a line's indentation changed. The twin must stay a claim of its own."""
    places = [n for n, char in enumerate(text) if char == " " and 0 < n < len(text) - 1]
    if not places:
        return text + " " + words(rng, 1)
    at = rng.choice(places)
    return text[:at] + rng.choice(["  ", "\u00a0", "\t"]) + text[at + 1 :]


def note(rng: random.Random) -> str:
    made: list[str] = []
    for _ in range(rng.randint(1, 5)):
        made.append(block(rng, made))
    return "\n\n".join(made)


def title(rng: random.Random) -> str:
    return rng.choice([words(rng, 2), words(rng, 1) + "\n" + words(rng, 1), "Ada\u00a0Notes"])


def spans_of(store: Store, page) -> dict:
    return store.get_spans([i for claim in page.claims for i in claim.span_ids])


def fences_cross(page) -> list[str]:
    """Each fenced block the parser finds in the body, its lines from the opener to the line
    that ends it, must lie inside one claim's text, or be one claim's ending and the closer
    the body writes after it. A fence that runs on into the next claim or a heading does not."""
    lines = page.body.split("\n")
    found = []
    for token in BODY.parse(page.body):
        if token.type != "fence":
            continue
        start, end = token.map
        run = "\n".join(lines[start:end])
        before = "\n".join(lines[start : end - 1])
        if any(run in c.text for c in page.claims):
            continue
        if before and any(c.text.endswith(before) for c in page.claims):
            continue
        found.append(f"lines {start + 1}-{end}")
    return found


def rows(text: str) -> str:
    """The rule, stated here on its own and not as the code writes it: every run of spaces,
    tabs and CRs that ends a line deleted, then every line feed at the two ends."""
    return re.sub(r"[ \t\r]+$", "", text, flags=re.MULTILINE).strip("\n")


def faults(store: Store, project_id: str) -> list[str]:
    out = []
    for slug in [p.slug for p in store.list_pages(project_id)]:
        page = store.get_page(project_id, slug)
        spans = spans_of(store, page)
        candidate = CandidatePage(slug=slug, title=page.title, body=page.body, claims=page.claims)
        if page.body != render_body(page.title, page.claims, spans):
            out.append(f"{slug}: the body is not render_body's")
        if unclaimed_line(candidate, spans) is not None:
            out.append(f"{slug}: unclaimed line")
        for gate in GATES:
            verdict = gate(candidate, spans)
            if not verdict.passed:
                codes = [v.split(":")[0] for v in verdict.violations]
                out.append(f"{slug}: {gate.__name__} {codes}")
        out.extend(f"{slug}: a fence runs on, {where}" for where in fences_cross(page))
        for claim in page.claims:  # each claim is each passage it cites, under the rule
            if any(rows(spans[i].text) != rows(claim.text) for i in claim.span_ids):
                out.append(f"{slug}: {claim.id} is not the text of a passage it cites")
        if len({c.id for c in page.claims}) != len(page.claims):
            out.append(f"{slug}: a claim id is doubled")
    return out


def reversed_order(page) -> Reply:
    """The claims in reverse, and as "same" every group whose texts are equal but for white
    space, as a model may call them (the arranger refuses such a group unless its texts are
    the same under the rule, and the page stays as it was)."""
    numbers = list(range(len(page.claims), 0, -1))
    groups: dict[str, list[int]] = {}
    for n, claim in enumerate(page.claims, 1):
        groups.setdefault(" ".join(claim.text.split()), []).append(n)
    same = [group for group in groups.values() if len(group) > 1]
    folded = {n for group in same for n in group[1:]}
    numbers = [n for n in numbers if n not in folded]
    return Reply(text=json.dumps({"order": numbers, "same": same}), prompt_tokens=9)
