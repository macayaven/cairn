"""Two invariants over notes generated from a small grammar (second report, core4 item 3).

(a) Every page the compiler, a tidy or the arranger (a fake model) saves passes every gate,
its body is what render_body writes of its claims, no code fence in the parsed body runs
across two claims or a heading the body writes, and filing the same notes again changes
nothing, claim ids and the order of citations included. (b) Every such page exports and
imports, and the imported page is equal, claim ids and citation order included. Every claim is
the text of each passage it cites under the one rule (CORE5: only a CR before a line feed,
spaces and tabs at a line's end and blank lines around the text are ignored), checked by a
statement of that rule written here, not by the gate's function.

The same notes are also compiled by two composers of this test, standing in for what a model
may answer through LLMComposer (which only the evaluation script uses): one folds every
passage of one text into one claim (finding 5a), one reuses a passage, as a second claim with
the same text or with a space before it (the cross-reader's item 6). What either saves holds
both invariants; a draft the gate refuses saves nothing.

The grammar: repeated paragraphs, one a prefix of another and one joined from the first lines
of two earlier ones, quotes and lazy lines after quotes and lists, lists and nested lists with
varying indentation, fences with every closer shape, inline code with odd white space, links
with backticks in their destinations and titles, escaped backticks, setext headings, link
definitions, private-use and non-character code points, headings to depth eight, titles with
line breaks, no-break and other Unicode spaces. Seeded, so every run builds the same notes.
Made-up notes."""

import hashlib
import io
import json
import random
import re

import pytest
from markdown_it import MarkdownIt

from cairn.arrange import arrange_page, tidy_page
from cairn.compiler import (
    CandidatePage,
    Draft,
    DraftStats,
    claim_id,
    compile_note,
    provenance_gate,
    render_body,
)
from cairn.gate import lint_gate, strict_lint_gate, unclaimed_line, verbatim_gate
from cairn.llm import FakeModel, Reply
from cairn.memory import Memory, RequestError
from cairn.store import Claim, Store
from support.fuzz import fuzz_budget

pytestmark = pytest.mark.fuzz

SEED, CASES = 20261120, fuzz_budget(60, 300)
GATES = (provenance_gate, lint_gate, strict_lint_gate, verbatim_gate)
SPACES = [" ", "  ", "\t", "\u00a0", "\u2003", "\u3000", " \u00a0"]
WORDS = ["Ada", "Brook", "Quill", "keeps", "the", "nightly", "log", "Fable", "Works", "port"]
# The old probe character, other private-use and non-character code points.
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


def cases(seed: int, count: int):
    rng = random.Random(seed)
    for n in range(count):
        yield f"p{n:03d}", title(rng), [note(rng) for _ in range(rng.randint(1, 3))]


def snapshot(store: Store, project_id: str) -> dict:
    pages = {}
    for slug in sorted(p.slug for p in store.list_pages(project_id)):
        page = store.get_page(project_id, slug)
        claims = [(c.id, c.text, c.span_ids) for c in page.claims]
        pages[slug] = (page.title, page.body, claims, page.version)
    return pages


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


def folding(title: str, spans) -> Draft:
    """A composer as a model may answer: one claim for each text, citing every passage that
    says it, under whatever heading (the fold of finding 5a)."""
    by_text: dict[str, list] = {}
    for span in spans:
        by_text.setdefault(span.text, []).append(span)
    claims = tuple(
        Claim(id=claim_id(same[0].id), text=text, span_ids=tuple(s.id for s in same))
        for text, same in by_text.items()
    )
    return Draft(claims=claims, stats=DraftStats(composer="folding"))


def reusing(title: str, spans) -> Draft:
    """A composer as a model may answer, with LLMComposer's ids (a hash of text and citations,
    so a claim the same as an earlier one is dropped): each passage as a claim, and each text
    that several passages say once more, as a claim that cites them all (a reuse the gate
    and the import take). A note with no such text reuses its first passage instead, with a
    space before it (` X.` beside `X.`) or after it (`X. `, the whole passage, but past what
    the import takes of one version): the gate refuses the draft, and nothing is saved."""
    claims = {}

    def add(text: str, cited: tuple[str, ...]) -> None:
        made = "cl_" + hashlib.sha256(json.dumps([text, cited]).encode()).hexdigest()[:16]
        claims.setdefault(made, Claim(id=made, text=text, span_ids=cited))

    by_text: dict[str, list[str]] = {}
    for span in spans:
        add(span.text, (span.id,))
        by_text.setdefault(span.text, []).append(span.id)
    repeated = [(text, ids) for text, ids in by_text.items() if len(ids) > 1]
    for text, ids in repeated:
        add(text, tuple(ids))
    if not repeated:
        first = spans[0]
        add(f" {first.text}" if len(spans) % 2 else f"{first.text} ", (first.id,))
    return Draft(claims=tuple(claims.values()), stats=DraftStats(composer="reusing"))


def build(store: Store) -> dict[str, int]:
    """File every case's notes, check, tidy, check, arrange, check, file again; then compile
    the same notes in a project of their own with a composer that folds, and check. The
    counts of what happened, and an AssertionError naming the case at the first fault."""
    memory = Memory(store, lint_gate)
    counts = {"cases": 0, "notes": 0, "refused": 0, "pages": 0, "tidied": 0, "arranged": 0}
    counts |= {"composed": 0, "folds saved": 0, "arranger refused a fold": 0}
    counts |= {"reuses saved": 0, "reuses refused": 0}
    for name, page_title, notes in cases(SEED, CASES):
        project = store.create_project(name)
        counts["cases"] += 1
        filed = []
        for text in notes:
            counts["notes"] += 1
            try:
                answer = memory.note(name, text, title=page_title, topic=page_title)
                filed.append(text)
            except RequestError:
                counts["refused"] += 1
                continue
            # an extractive note is never refused by the gate (finding 1 was one)
            assert answer["status"] in ("saved", "unchanged"), (name, answer["violations"])
        assert faults(store, project.id) == [], (name, "compiled")
        for slug in [p.slug for p in store.list_pages(project.id)]:
            counts["pages"] += 1
            tidied = tidy_page(store, project.id, slug)
            assert tidied.status in ("arranged", "unchanged"), (name, tidied.problem)
            counts["tidied"] += tidied.status == "arranged"
            assert faults(store, project.id) == [], (name, "tidied")
            page = store.get_page(project.id, slug)
            reply = reversed_order(page)
            folds = json.loads(reply.text)["same"]
            arranged = arrange_page(store, project.id, slug, FakeModel([reply, reply]))
            if arranged.status in ("rejected", "invalid"):  # only a fold it must not make
                assert folds, (name, arranged.problem)
                counts["arranger refused a fold"] += 1
            else:
                assert arranged.status in ("arranged", "unchanged"), (name, arranged.problem)
            counts["arranged"] += arranged.status == "arranged"
            assert faults(store, project.id) == [], (name, "arranged")
        before = snapshot(store, project.id)
        for text in filed:
            memory.note(name, text, title=page_title, topic=page_title)
        assert snapshot(store, project.id) == before, (name, "filed again")
        composed = store.create_project(f"{name}-composed")
        for text in filed:
            note, _ = store.add_note(composed.id, text, title=page_title, topic=page_title)
            counts["composed"] += compile_note(store, note, compose=folding).status == "saved"
        assert faults(store, composed.id) == [], (name, "composed")
        counts["folds saved"] += sum(
            len(c.span_ids) > 1 for p in store.list_pages(composed.id) for c in p.claims
        )
        reused = store.create_project(f"{name}-reused")
        for text in filed:
            note, _ = store.add_note(reused.id, text, title=page_title, topic=page_title)
            result = compile_note(store, note, compose=reusing)
            assert result.status in ("saved", "unchanged", "rejected"), name
            counts["reuses saved"] += result.status == "saved"
            if result.status == "rejected":  # only a space around a reused passage
                codes = {v.split(":")[0] for v in result.gate.violations}
                assert codes <= {"grounding.not_verbatim", "grounding.passage_repeated"}, (
                    name,
                    codes,
                )
                counts["reuses refused"] += 1
        assert faults(store, reused.id) == [], (name, "reused")
    return counts


@pytest.fixture(scope="module")
def built_world(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("invariants") / "cairn.db")
    try:
        counts = build(store)
        yield store, counts
    finally:
        store.close()


def test_every_saved_page_passes_every_gate_and_filing_again_changes_nothing(built_world):
    _, counts = built_world
    assert counts["cases"] == CASES and counts["pages"] > CASES // 2, counts
    assert counts["tidied"] and counts["arranged"], counts  # both paths saved pages
    assert counts["composed"] and counts["folds saved"], counts
    assert counts["reuses saved"] and counts["reuses refused"], counts


def test_every_saved_page_exports_and_imports_equal(built_world, tmp_path):
    store, _ = built_world
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        names = [
            n
            for name, _, _ in cases(SEED, CASES)
            for n in (name, f"{name}-composed", f"{name}-reused")
        ]
        for name in names:
            source, imported = store.get_project(name), target.get_project(name)
            mine = {s: p[:3] for s, p in snapshot(store, source.id).items()}
            theirs = {s: p[:3] for s, p in snapshot(target, imported.id).items()}
            assert theirs == mine, name
    finally:
        target.close()
