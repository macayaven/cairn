"""Two invariants over notes generated from a small grammar (second report, core4 item 3).

(a) Every page the compiler, a tidy or the arranger (a fake model) saves passes every gate,
its body is what render_body writes of its claims, no code fence in the parsed body runs
across two claims or a heading the body writes, and filing the same notes again changes
nothing. (b) Every such page exports and imports, and the imported page is equal.

The grammar: repeated paragraphs and one a prefix of another, quotes, lists and nested
lists, fences with every closer shape, inline code with odd white space, headings to depth
eight, titles with line breaks, no-break and other Unicode spaces. Seeded, so every run
builds the same notes. Made-up notes."""

import io
import json
import random

import pytest
from markdown_it import MarkdownIt

from cairn.arrange import arrange_page, tidy_page
from cairn.compiler import CandidatePage, provenance_gate, render_body
from cairn.gate import lint_gate, strict_lint_gate, unclaimed_line, verbatim_gate
from cairn.llm import FakeModel, Reply
from cairn.memory import Memory, RequestError
from cairn.store import Store

SEED, CASES = 20261120, 300
GATES = (provenance_gate, lint_gate, strict_lint_gate, verbatim_gate)
SPACES = [" ", "  ", "\t", "\u00a0", "\u2003", "\u3000", " \u00a0"]
WORDS = ["Ada", "Brook", "Quill", "keeps", "the", "nightly", "log", "Fable", "Works", "port"]
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
    kind = rng.randrange(12)
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
    return words(rng)


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
        claims = [(c.text, tuple(sorted(c.span_ids))) for c in page.claims]
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
                out.append(f"{slug}: {gate.__name__} {[v[0] for v in verdict.violations]}")
        out.extend(f"{slug}: a fence runs on, {where}" for where in fences_cross(page))
    return out


def reversed_order(page) -> Reply:
    numbers = list(range(len(page.claims), 0, -1))
    return Reply(text=json.dumps({"order": numbers, "same": []}), prompt_tokens=9)


def build(store: Store) -> dict[str, int]:
    """File every case's notes, check, tidy, check, arrange, check, file again; the counts of
    what happened, and an AssertionError naming the case at the first fault."""
    memory = Memory(store, lint_gate)
    counts = {"cases": 0, "notes": 0, "refused": 0, "pages": 0, "tidied": 0, "arranged": 0}
    for name, page_title, notes in cases(SEED, CASES):
        project = store.create_project(name)
        counts["cases"] += 1
        filed = []
        for text in notes:
            counts["notes"] += 1
            try:
                memory.note(name, text, title=page_title, topic=page_title)
                filed.append(text)
            except RequestError:
                counts["refused"] += 1
        assert faults(store, project.id) == [], (name, "compiled")
        for slug in [p.slug for p in store.list_pages(project.id)]:
            counts["pages"] += 1
            counts["tidied"] += tidy_page(store, project.id, slug).status == "arranged"
            assert faults(store, project.id) == [], (name, "tidied")
            page = store.get_page(project.id, slug)
            model = FakeModel([reversed_order(page)])
            counts["arranged"] += arrange_page(store, project.id, slug, model).status == "arranged"
            assert faults(store, project.id) == [], (name, "arranged")
        before = snapshot(store, project.id)
        for text in filed:
            memory.note(name, text, title=page_title, topic=page_title)
        assert snapshot(store, project.id) == before, (name, "filed again")
    return counts


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def test_every_saved_page_passes_every_gate_and_filing_again_changes_nothing(store):
    counts = build(store)
    assert counts["cases"] == CASES and counts["pages"] > CASES // 2, counts
    assert counts["tidied"] and counts["arranged"], counts  # both paths saved pages


def test_every_saved_page_exports_and_imports_equal(store, tmp_path):
    build(store)
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        for name, _, _ in cases(SEED, CASES):
            source, imported = store.get_project(name), target.get_project(name)
            mine = {s: p[:3] for s, p in snapshot(store, source.id).items()}
            theirs = {s: p[:3] for s, p in snapshot(target, imported.id).items()}
            assert theirs == mine, name
    finally:
        target.close()
