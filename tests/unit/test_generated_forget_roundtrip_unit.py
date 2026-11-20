"""What a walk of a user's operations leaves always exports and imports, every version, and a
page holds no byte of a note its project forgot (third report, must-fix 1; CORE6A).

Each case is a workspace of its own, with one project and two topics. Its notes come from the
grammar of test_generated_invariants_unit (imported, not copied) and from short notes of one
two-line paragraph, each beside twins: the same text under cairn.spans.compared_text, other
bytes. Spaces and tabs at a line's end reach a twin's passages. A CR before a line feed and
blank lines around the text make another note of the same passages, byte for byte
(cairn.spans.chunk reads lines as str.splitlines does and ends no passage on a blank line). A
text and its twins share a topic, in one of two spellings of its key.

Then a seeded walk of what a user does: file a note, send one again, tidy, arrange (a fake
model that reverses the order and calls "same" what differs by white space), forget a note.

After every step, for every version of every page the store holds (`held_faults`):

3. every claim cites only passages the store holds; its text is, byte for byte, the text of
   the first passage it cites; and it says what every passage it cites says, under the rule
   as test_generated_invariants_unit states it (`rows`). The version's body is what
   render_body writes of its title, claims and passages, its title is the topic of a note it
   cites, and no two of its claims share an id. No code fence of the body covers a line of
   another claim (test_generated_assembled_body_unit's own reading of whose line each is).

After every forget and at the end of the walk:

1. the workspace exports, and the archive imports into an empty store;
2. every version of every page is equal after the import: title, body, claim ids, texts and
   the order of citations;
and the latest version of every page holds invariant (a) of test_generated_invariants_unit
(its `faults`: every gate passes it, the verbatim gate included).

And of the walk itself: no note is refused its page; a note sent again changes nothing; no
tidy or arrangement is refused, but a fold the arranger must not make; a forget does what
its preview said; every passage of every note still held is cited by the latest version of a
page; and no forget drops a claim because an import would refuse it (the store's log has no
such line): a forget takes the forgotten note's claims and no other, so (1) is never true
only because a claim left.

The fence walks (muse's third reading, finding 1; CORE7). The grammar above holds the three
notes of the third report's fourth fault (a list item, an indented fence, an indented line
after it), and a second kind of case files 15 to 19 rounds of them in order on one topic,
some rounds with a note at the margin between the item and the fence, so that the body
closes at most spans.CLOSERS_MAX (16) fences that run from one claim into the next. Then a
walk of plain notes at the margin, notes sent again, tidies and forgets, half of them of a
note between. A forget may bring a seventeenth such fence together. There a claim does leave
with a forget, the one that would leave the fence open, and the walk asks exactly that: the
passages a forget takes off the page are as many as its line on the store's log counts,
their notes stay stored, and every check above holds after it, the next plain note filed.

(3) is the stronger of two properties. The import asks that a claim say what each passage it
cites says under the rule, and that a version's claims hold at most twice the bytes of its
passages (store.portable.CLAIM_TEXT_FACTOR): white space at a line's end is free up to that
bound. Privacy asks more: the white space the rule leaves out is where a forgotten note's
spelling stays, so a claim is the bytes of a passage the store still holds, with no allowance.
Every writer this walk runs copies a passage (the extractive compile), keeps the first claim's
text with its citation first (a tidy, an arrangement) or takes the first passage that is left
(a forget), so the bytes are the first cited passage's. A claim that is the bytes of a passage
it alone cites is within the import's bound, so (3) holds what the import needs too.

Seeded: a case's random numbers depend on its number alone, so one case replays without the
others. A failure names pages, versions, claim ids and the steps of a case to replay (notes by
their place in `pool`), never a note's text. Made-up notes."""

import io
import itertools
import json
import logging
import random
from collections import Counter

import pytest
from test_generated_assembled_body_unit import crossings
from test_generated_invariants_unit import faults as latest_faults
from test_generated_invariants_unit import note as grammar_note
from test_generated_invariants_unit import reversed_order, rows, words

from cairn.arrange import arrange_page, tidy_page
from cairn.compiler import CONTENT_KINDS, render_body
from cairn.gate import lint_gate
from cairn.llm import FakeModel
from cairn.memory import Memory, RequestError
from cairn.spans import CLOSERS_MAX, chunk, compared_text
from cairn.store import Page, Store

SEED, CASES, STEPS = 20261121, 300, 14
FENCE_CASES = 20
TRIPLE = ("- step {n}", "    ~~~\n    sample {n}", "  After {n}.")  # the third report's notes
TOPICS = (("Fable Works log", "fable works LOG"), ("Quill ports", "Quill  ports."))
# The reviewer's input: the first spelling is seven bytes, the one that stays is three.
REVIEWED = ("A    \nB", "A\nB")
SHORT = ["A\nB", "Ada\nlog", "x\nB", "Quill\nport", "A\nlog"]
ENDS = ["", "", " ", "    ", "\t", " \t", "\r", "  \r", " " * 12]
KINDS = ["file", "file", "file", "again", "tidy", "tidy", "arrange", "forget"]
FENCE_KINDS = ["file", "again", "tidy", "forget", "forget"]  # no arrangement: it may make more

NOT_HELD = "a claim cites a passage the store does not hold"
NOT_BYTES = "a claim is not the bytes of the first passage it cites"
NOT_SAID = "a claim does not say what a passage it cites says"
NOT_RENDERED = "a body is not what render_body writes of its claims"
NOT_TITLED = "a title is not the topic of a note the version cites"
DOUBLED = "two claims of a version share an id"
REFUSED_IMPORT = "the import refused the export"
CHANGED = "a version changed on import"
NOT_FILED = "a note was refused its page"
NOT_TIDIED = "a tidy was refused"
NOT_ARRANGED = "an arrangement was refused"
LATEST = "a latest version breaks the saved-page invariants"
NOT_AGAIN = "a note sent again changed its page"
NOT_PREVIEWED = "a forget did not do what its preview said"
NOT_CITED = "a passage of a note still held is on no page"
DROPPED = "a forget dropped a claim an import would refuse"
CROSSED = "a code fence of a body covers a line of another claim"
MISCOUNTED = "a forget took other passages off the page than its log line counts"

Fault = tuple[str, str]  # (which kind, where: a page, a version, a claim id; never text)


def respelled(rng: random.Random, text: str) -> str:
    """`text` in other bytes, the same under compared_text and of the same passages: spaces,
    tabs or a CR at a line's end, blank lines (empty or of white space) before and after."""
    lines = [line + rng.choice(ENDS) for line in text.split("\n")]
    lead = rng.choice(["", "\n", "  \n", "\t\n\n"])
    made = lead + "\n".join(lines) + rng.choice(["", "\n", "\n  ", "\n\n\t"])
    assert compared_text(made) == compared_text(text)
    assert passages(made) == passages(text)  # each passage a twin of its passage, in place
    return made


def passages(text: str) -> list[tuple[str, tuple[str, ...], str]]:
    return [(s.kind, s.heading_path, compared_text(s.text)) for s in chunk("", text)]


def pool(rng: random.Random) -> list[tuple[str, str]]:
    """A case's notes, as (text, topic): for each topic, short notes where a respelling
    outweighs the passage, or notes from the grammar; each with one or two twins."""
    out = []
    short = rng.sample(SHORT, len(TOPICS))
    for spellings, brief in zip(TOPICS, short, strict=True):
        if rng.random() < 0.5:
            bases = [brief]
        else:
            bases = [grammar_note(rng), words(rng, rng.randint(1, 3)) + "\n" + words(rng, 1)]
        for text in bases:
            for made in (text, *(respelled(rng, text) for _ in range(rng.randint(1, 2)))):
                out.append((made, rng.choice(spellings)))
        if rng.random() < 0.3:  # the three notes whose fence opens only on the page
            out += [(text.format(n=0), spellings[0]) for text in TRIPLE]
    rng.shuffle(out)
    return out


def fence_pool(rng: random.Random) -> tuple[list[tuple[str, str]], int, list[int]]:
    """A fence case's notes, as (text, topic); how many of them are filed first, in order;
    and the places of the notes between. 15 to 19 rounds of TRIPLE on one topic; as many
    rounds as are past CLOSERS_MAX, and up to three more, hold a note at the margin between
    the item and the fence, so the page closes at most CLOSERS_MAX fences while those notes
    stay. After them, plain notes at the margin for the walk to file."""
    topic, rounds = TOPICS[0][0], rng.randint(CLOSERS_MAX - 1, CLOSERS_MAX + 3)
    apart = set(rng.sample(range(rounds), max(0, rounds - CLOSERS_MAX) + rng.randint(0, 3)))
    out, between = [], []
    for n in range(rounds):
        item, fence, after = (text.format(n=n) for text in TRIPLE)
        out.append((item, topic))
        if n in apart:
            between.append(len(out))
            out.append((f"Between {n}.", topic))
        out += [(fence, topic), (after, topic)]
    first = len(out)
    out += [(f"Brook keeps log {n}.", topic) for n in range(STEPS)]
    return out, first, between


def versions(store: Store, project_id: str) -> dict[tuple[str, int], Page]:
    return {
        (page.slug, version): store.get_page(project_id, page.slug, version)
        for page in store.list_pages(project_id)
        for version in store.page_versions(project_id, page.slug)
    }


def compared(page: Page) -> tuple:
    return page.title, page.body, [(c.id, c.text, c.span_ids) for c in page.claims]


def held_faults(store: Store, project_id: str, seen: set | None = None) -> list[Fault]:
    """(3), of every version the store holds that is not in `seen`, which then holds them: a
    version is written once and never changed, so a walk checks each once."""
    out = []
    seen = set() if seen is None else seen
    for (slug, version), page in versions(store, project_id).items():
        if (slug, version) in seen:
            continue
        seen.add((slug, version))
        where = f"{slug} v{version}"
        spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
        for claim in page.claims:
            if any(i not in spans for i in claim.span_ids):
                out.append((NOT_HELD, f"{where} {claim.id}"))
                continue
            if spans[claim.span_ids[0]].text != claim.text:
                out.append((NOT_BYTES, f"{where} {claim.id}"))
            if any(rows(spans[i].text) != rows(claim.text) for i in claim.span_ids):
                out.append((NOT_SAID, f"{where} {claim.id}"))
        if len({claim.id for claim in page.claims}) != len(page.claims):
            out.append((DOUBLED, where))
        if page.body != render_body(page.title, page.claims, spans):
            out.append((NOT_RENDERED, where))
        notes = [store.get_note(n) for n in {span.source_id for span in spans.values()}]
        if page.title not in {note.topic or note.title for note in notes if note}:
            out.append((NOT_TITLED, where))
        if all(i in spans for claim in page.claims for i in claim.span_ids):
            out += [(CROSSED, f"{where}: {c}") for c in crossings(page.body, page.claims, spans)]
    return out


def round_trip_faults(store: Store, name: str, path) -> list[Fault]:
    """(1) and (2): the export imports into an empty store at `path`, every version equal."""
    archive = io.BytesIO()
    store.export(archive)
    target = Store(path)
    try:
        try:
            target.import_export(io.BytesIO(archive.getvalue()))
        except ValueError as refused:  # the import's words: a page and a version, never text
            return [(REFUSED_IMPORT, str(refused))]
        mine = versions(store, store.get_project(name).id)
        theirs = versions(target, target.get_project(name).id)
        out = [(CHANGED, f"{s} v{v} is not held") for s, v in sorted(mine.keys() ^ theirs.keys())]
        for (slug, version), page in mine.items():
            imported = theirs.get((slug, version))
            for part, a, b in zip(
                ("title", "body", "claims"),
                compared(page),
                compared(imported or page),
                strict=True,
            ):
                if a != b:
                    out.append((CHANGED, f"{slug} v{version}: its {part}"))
        return out
    finally:
        target.close()


def loses_first_spelling(store: Store, project_id: str, note_id: str) -> bool:
    """Whether forgetting the note takes the passage a fold's text is the bytes of, and leaves
    the fold a passage of other bytes: the reviewer's shape."""
    for page in store.list_pages(project_id):
        spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
        for claim in page.claims:
            first, rest = spans[claim.span_ids[0]], [spans[i] for i in claim.span_ids[1:]]
            left = [span for span in rest if span.source_id != note_id]
            if first.source_id == note_id and left and left[0].text != first.text:
                return True
    return False


class Lines(logging.Handler):
    """The store's log lines of forgets that dropped a claim, as the counts they give."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.counts: list[int] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage().startswith("forget:"):
            self.counts.append(record.args[0])


def walk(
    store: Store, name: str, rng: random.Random, tmp_path, fences: bool = False
) -> tuple[list[str], list, Counter]:
    """A seeded walk of a user's operations on the one project `name`: its steps (notes by
    their place in the case's pool), the faults found, and what happened, counted. With
    `fences`, a fence case: the rounds of fence_pool filed first, in order, then the walk."""
    memory = Memory(store, lint_gate)
    notes, first, between = fence_pool(rng) if fences else (pool(rng), 0, [])
    kinds = FENCE_KINDS if fences else KINDS
    project = store.get_project(name)
    filed: dict[str, int] = {}  # note id -> its place in `notes`, in the order they were filed
    steps: list[str] = []
    found: dict[Fault, None] = {}
    done: Counter = Counter()
    trips = itertools.count()  # each import into an empty store of its own
    seen: set = set()  # the versions checked
    off: set[str] = set()  # the passages a forget took off the page with a line on the log
    lines = Lines()

    def uncited() -> dict[str, int]:
        """Each content passage of a note still held that no latest version cites: its place."""
        cited = {
            span_id
            for page in store.list_pages(project.id)
            for claim in page.claims
            for span_id in claim.span_ids
        }
        return {
            span.id: place
            for note_id, place in filed.items()
            for span in store.note_spans(note_id)
            if span.kind in CONTENT_KINDS and span.id not in cited
        }

    def check(trip: bool) -> None:
        new = held_faults(store, project.id, seen)
        new += [(NOT_CITED, f"the note at {at}") for i, at in uncited().items() if i not in off]
        if trip:
            new += [(LATEST, fault) for fault in latest_faults(store, project.id)]
            new += round_trip_faults(store, name, tmp_path / f"{name}-{next(trips)}.db")
        found.update(dict.fromkeys(new))

    def file(kind: str, place: int) -> None:
        steps.append(f"{kind} {place}")
        text, topic = notes[place]
        try:
            answer = memory.note(name, text, title=topic, topic=topic)
        except RequestError:  # no passage, a text the project forgot, or one filed elsewhere
            done["refused"] += 1
            return
        filed[answer["note_id"]] = place
        done[kind] += 1
        if answer["status"] not in ("saved", "unchanged"):
            codes = sorted({v.split(":")[0] for v in answer["violations"]})
            found[NOT_FILED, f"{answer['status']} {codes}"] = None
        elif kind == "again" and answer["status"] != "unchanged":
            found[NOT_AGAIN, f"the note at {place}, on {answer['page']}"] = None

    for place in range(first):  # a fence case's rounds, in their order
        file("file", place)
    steps[:] = [f"file 0 to {first - 1}"] if first else []
    check(trip=bool(first))
    store_log = logging.getLogger("cairn.store")
    store_log.addHandler(lines)
    try:
        for step in range(STEPS):
            kind, forgot = "file" if step < 2 and not fences else rng.choice(kinds), False
            on_page = [n for n in filed if not off & {s.id for s in store.note_spans(n)}]
            if kind == "again" and not on_page:
                kind = "file"  # nothing is held to send again
            if kind == "file":
                file("file", first + step if fences else rng.randrange(len(notes)))
            elif kind == "again":
                file("again", filed[rng.choice(on_page)])
            elif kind in ("tidy", "arrange"):
                steps.append(kind)
                done[f"{kind} steps"] += 1
                for page in store.list_pages(project.id):
                    if kind == "tidy":
                        result = tidy_page(store, project.id, page.slug)
                        refused = result.status not in ("arranged", "unchanged")
                    else:
                        reply = reversed_order(page)
                        model = FakeModel([reply, reply])
                        result = arrange_page(store, project.id, page.slug, model)
                        folds = json.loads(reply.text)["same"]  # the arranger refuses a fold of
                        refused = result.status not in ("arranged", "unchanged") and not (
                            result.status == "invalid" and folds  # texts the rule tells apart
                        )
                    if refused:
                        which = NOT_TIDIED if kind == "tidy" else NOT_ARRANGED
                        found[which, f"{page.slug}: {result.status}: {result.problem}"] = None
                    done[kind] += result.status == "arranged"
            elif filed:
                # half the time the oldest note still held (a fold's text is its oldest
                # claim's) or, in a fence case, a note between an item and its fence
                apart = [n for n, place in filed.items() if place in between]
                first_held = rng.choice(apart) if apart else next(iter(filed))
                note_id = first_held if rng.random() < 0.5 else rng.choice(list(filed))
                steps.append(f"forget {filed.pop(note_id)}")
                done["a fold lost its first spelling"] += loses_first_spelling(
                    store, project.id, note_id
                )
                said, logged = store.forget_preview(note_id), len(lines.counts)
                if store.forget_note(note_id) != said:
                    found[NOT_PREVIEWED, steps[-1]] = None
                taken = uncited().keys() - off
                counted = sum(lines.counts[logged:])
                if len(taken) != counted:
                    found[MISCOUNTED, f"{steps[-1]}: {len(taken)} taken, {counted} logged"] = None
                if counted and not fences:
                    found[DROPPED, steps[-1]] = None
                off |= taken
                done["a forget took a claim that would leave a fence open"] += bool(counted)
                done["forget"] += 1
                forgot = True
            check(trip=forgot)
    finally:
        store_log.removeHandler(lines)
    check(trip=True)
    return steps, list(found), done


def report(found: dict[str, list[tuple[str, str, list[str]]]], totals: Counter) -> str:
    """Each kind of fault: how many, and the first, with the steps of its case to replay."""
    lines = []
    for kind, faults in found.items():
        name, where, steps = faults[0]
        lines.append(f"{kind}: {len(faults)}; first in {name}: {where}; steps: {', '.join(steps)}")
    return "\n".join([*lines, f"walked: {dict(totals)}"])


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def test_the_reviewers_input_forgotten_leaves_the_survivors_bytes_and_round_trips(store, tmp_path):
    """Two notes under one topic, `A`, four spaces, a line feed, `B` and then `A`, a line feed,
    `B`; a tidy folds them; the first is forgotten. Everything the walk checks is checked, then
    the surviving text and its evidence in a store the export was imported into."""
    memory, topic = Memory(store, lint_gate), TOPICS[0][0]
    project = store.create_project("alpha")
    first = memory.note("alpha", REVIEWED[0], title=topic, topic=topic)
    second = memory.note("alpha", REVIEWED[1], title=topic, topic=topic)
    (page,) = store.list_pages(project.id)
    assert tidy_page(store, project.id, page.slug).status == "arranged"
    (fold,) = store.get_page(project.id, page.slug).claims
    assert fold.text == REVIEWED[0] and len(fold.span_ids) == 2  # the first spelling, citing both
    assert loses_first_spelling(store, project.id, first["note_id"])

    store.forget_note(first["note_id"])

    found = held_faults(store, project.id)
    found += [(LATEST, fault) for fault in latest_faults(store, project.id)]
    found += round_trip_faults(store, "alpha", tmp_path / "checked.db")
    assert not found, "\n".join(f"{kind}: {where}" for kind, where in found)
    assert tidy_page(store, project.id, page.slug).status == "unchanged"  # and never refused

    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        (claim,) = target.get_page(target.get_project("alpha").id, page.slug).claims
        assert claim.text == REVIEWED[1]
        evidence = Memory(target, lint_gate).evidence("alpha", page.slug, claim.id)
        assert [p["text"] for p in evidence["passages"]] == [REVIEWED[1]]
        assert [p["note_id"] for p in evidence["passages"]] == [second["note_id"]]
    finally:
        target.close()


def test_every_walk_exports_and_imports_every_version_and_keeps_only_bytes_it_cites(
    tmp_path, caplog
):
    found: dict[str, list[tuple[str, str, list[str]]]] = {}
    totals: Counter = Counter()
    caplog.set_level(logging.WARNING, logger="cairn.store")
    for n in range(CASES):
        name = f"w{n:03d}"
        store = Store(tmp_path / f"{name}.db")  # its own, so a refused import hides no other
        caplog.clear()
        try:
            store.create_project(name)
            steps, faults, done = walk(store, name, random.Random(SEED * 1000 + n), tmp_path)
        finally:
            store.close()
        dropped = sum(r.getMessage().startswith("forget:") for r in caplog.records)
        faults += [(DROPPED, f"{dropped} lines on the store's log")] * bool(dropped)
        totals += done
        for kind, where in faults:
            found.setdefault(kind, []).append((name, where, steps))
    # every operation ran, and the path of the finding with it
    assert all(totals[k] for k in ("file", "again", "tidy", "arrange", "forget")), totals
    assert totals["a fold lost its first spelling"] >= CASES // 10, totals
    assert not found, report(found, totals)


def test_every_fence_walk_leaves_no_fence_running_from_one_claim_into_the_next(tmp_path):
    found: dict[str, list[tuple[str, str, list[str]]]] = {}
    totals: Counter = Counter()
    for n in range(FENCE_CASES):
        name = f"f{n:03d}"
        store = Store(tmp_path / f"{name}.db")
        try:
            store.create_project(name)
            rng = random.Random(SEED * 1000 + CASES + n)
            steps, faults, done = walk(store, name, rng, tmp_path, fences=True)
        finally:
            store.close()
        totals += done
        for kind, where in faults:
            found.setdefault(kind, []).append((name, where, steps))
    assert not found, report(found, totals)
    assert all(totals[k] for k in ("file", "again", "tidy steps", "forget")), totals
    # the path of the finding ran: a forget brought a seventeenth such fence together
    assert totals["a forget took a claim that would leave a fence open"] >= 5, totals
