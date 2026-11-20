"""RT1: a differential harness for forgetting a note (red team, round seven).

The property, the reviewer's own: after a forget, every page the note was on keeps exactly
what a plain loop keeps. The loop: the page's claims without the note's (a claim folded from
passages that say the same keeps the passages left, and the first one's text), then, while
the body (compiler.left_to_run, titled as the forget titles it: the `topic or title` of the
oldest stored note that a claim still cites, else the slug) names a claim whose fence runs
on, drop that one claim and render again. Nothing of store.erasure is used to say what is
expected.

One difference is declared (docs, security guide, "What the checks on pages catch"), and only
once the forget has rendered sixteen times naming a claim: the claim it names then "leaves
with every claim written after it on the page that begins with indentation and has a line
that opens a fence", and "a claim written before those, whose fence covers what follows once
they have left, leaves then too". Written here from those sentences (`declared`), never from
erasure._may_open_a_fence.

Besides: the forget's result exports and imports to the same pages, claims and search hits;
the audit event counts the claims that left for fences (`claims_left`) and the pages; the
preview says what the forget does; and the work is measured (renders, parses of the Markdown
parser, the characters handed to it, against the body's size).

A page is a Plan: notes in the order they are stored, each with the notes filed inside its
gate (stored after it, filed before it: the stored order is not the order of the claims),
then steps (forget, tidy, send a note's text again, a new note). Everything goes through the
public path: Memory.note, Memory.forget, Memory.recall, Store.export and import_export,
arrange.tidy_page (what `serve` runs on its own). Made-up notes only.

Run by hand: uv run python tests/unit/test_rt1_harness.py --seeds 0:200 [--shrink]"""

from __future__ import annotations

import io
import itertools
import random
import re
import sys
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, replace
from typing import Any

from markdown_it import MarkdownIt

from cairn import compiler
from cairn.arrange import tidy_page
from cairn.gate import lint_gate
from cairn.memory import Memory, RequestError
from cairn.spans import CLOSERS_MAX, compared_text
from cairn.store import Store
from cairn.store.portable import Refusal, version_faults

PROJECT = "alpha"
CEILING = CLOSERS_MAX  # renders that name a claim before the declared class may differ


# The plan of a page


@dataclass
class Note:
    name: str
    text: str
    topic: str | None = None
    title: str | None = None
    inner: list[Note] = field(default_factory=list)  # filed inside this note's gate


@dataclass
class Step:
    kind: str  # "forget", "tidy", "again" (send a note's text again), "note" (a new note)
    name: str = ""  # the note forgotten or sent again
    note: Note | None = None  # a new note


@dataclass
class Plan:
    notes: list[Note]
    steps: list[Step]

    def stored(self) -> list[Note]:
        """Every note, in the order it is stored."""

        def walk(notes: list[Note]) -> Iterator[Note]:
            for note in notes:
                yield note
                yield from walk(note.inner)

        return [*walk(self.notes), *(s.note for s in self.steps if s.note)]

    def show(self) -> str:
        lines = []

        def walk(notes: list[Note], depth: int) -> None:
            for note in notes:
                lines.append(
                    f"{'  ' * depth}{note.name} topic={note.topic!r} title={note.title!r}"
                    f" text={note.text!r}"
                )
                walk(note.inner, depth + 1)

        walk(self.notes, 0)
        for step in self.steps:
            extra = f" {step.note.text!r} topic={step.note.topic!r}" if step.note else ""
            lines.append(f"step {step.kind} {step.name}{extra}")
        return "\n".join(lines)


# What a forget does, watched


class Watch:
    """The renders (compiler.left_to_run) and parses (MarkdownIt.parse) inside a block."""

    def __init__(self) -> None:
        self.renders: list[tuple[str, tuple, int | None, str]] = []
        self.inputs: list[int] = []

    @contextmanager
    def on(self) -> Iterator[Watch]:
        render, parse = compiler.left_to_run, MarkdownIt.parse

        def rendered(title, claims, spans):
            body, place = render(title, claims, spans)
            self.renders.append((title, tuple(claims), place, body))
            return body, place

        def parsed(reader, source, *args, **kwargs):
            self.inputs.append(len(source))
            return parse(reader, source, *args, **kwargs)

        compiler.left_to_run, MarkdownIt.parse = rendered, parsed
        try:
            yield self
        finally:
            compiler.left_to_run, MarkdownIt.parse = render, parse


def key(claim) -> tuple[str, tuple[str, ...]]:
    return claim.text, tuple(claim.span_ids)


# The oracle


@dataclass
class Expected:
    slug: str
    before: Any  # the page before the forget
    start: list  # its claims without the note's
    kept: list  # what the plain loop keeps
    dropped: list  # what it drops, in its order
    titles: list[str]  # the title of each render
    tops: list[frozenset]  # the keys of the claims with no section, each render
    title: str
    body: str | None  # None: the page is removed
    held: dict


def kept_claims(claims, gone: set[str], held: dict) -> list:
    """The claims once the spans `gone` are forgotten (store contract, written plainly)."""
    out = []
    for claim in claims:
        left = [i for i in claim.span_ids if i not in gone]
        if len(left) == len(claim.span_ids):
            out.append(claim)
        elif left and all(
            i in held and compared_text(held[i].text) == compared_text(claim.text) for i in left
        ):
            out.append(
                claim.model_copy(update={"span_ids": tuple(left), "text": held[left[0]].text})
            )
    return out


def title_of(claims, held: dict, notes: list, slug: str) -> str:
    sources = {held[i].source_id for claim in claims for i in claim.span_ids if i in held}
    oldest = next((note for note in notes if note.id in sources), None)
    return (oldest.topic or oldest.title if oldest else None) or slug


def tops_of(title: str, claims, held: dict) -> dict:
    """Whether the body writes each claim with no section under `title`, by key."""
    out = {}
    for claim in claims:
        path = next((held[i].heading_path for i in claim.span_ids if i in held), ())
        out[key(claim)] = not (path[1:] if path[:1] == (title,) else path)
    return out


def moved(tops: list[dict]) -> bool:
    """Whether a claim went between the top and a section from one render to the next."""
    return any(a[k] != b[k] for a, b in itertools.pairwise(tops) for k in a.keys() & b.keys())


def plain_loop(claims, held: dict, notes: list, slug: str):
    """(kept, dropped, titles, tops, title, body, other faults) of the plain loop."""
    left, dropped, titles, tops, other = list(claims), [], [], [], []
    title, body = slug, None
    while left:
        title = title_of(left, held, notes, slug)
        body, place = compiler.left_to_run(title, left, held)
        titles.append(title)
        tops.append(tops_of(title, left, held))
        faults = list(version_faults(left, held, legacy=True, run_on=place))
        named = sorted({n for n, _ in faults if n})
        if not named:
            break
        other += [fault for _, fault in faults if fault is not Refusal.FENCE_RUNS_ON]
        for n in reversed(named):
            dropped.append(left.pop(n - 1))
    return left, dropped, titles, tops, title, (body if left else None), other


def expected(store: Store, pid: str, note_id: str) -> dict[str, Expected]:
    gone = {span.id for span in store.note_spans(note_id)}
    notes = store.list_notes(pid)  # oldest stored first
    out = {}
    for page in store.list_pages(pid):
        cited = {
            i
            for version in store.page_versions(pid, page.slug)
            for claim in store.get_page(pid, page.slug, version).claims
            for i in claim.span_ids
        }
        if not cited & gone:
            continue
        held = store.get_spans(i for claim in page.claims for i in claim.span_ids if i not in gone)
        held = {i: span for i, span in held.items() if i not in gone}
        start = kept_claims(page.claims, gone, held)
        kept, dropped, titles, tops, title, body, other = plain_loop(
            start, held, [n for n in notes if n.id != note_id], page.slug
        )
        out[page.slug] = Expected(
            page.slug, page, start, kept, dropped, titles, tops, title, body, held
        )
        if other:
            out[page.slug].other = other  # type: ignore[attr-defined]
    return out


# The declared class, from the guide's sentences

_OPENS = re.compile(r"^(?:[ \t]*(?:>[ \t]?|[-*+][ \t]+|\d{1,9}[.)][ \t]+))*[ \t]*(`{3,}|~{3,})")


def opens_a_fence_line(text: str) -> bool:
    """Whether a line of `text` opens a code fence, read leniently: after any indentation,
    quote markers and list markers, three backticks or three tildes."""
    return any(_OPENS.match(line) for line in re.split(r"\r\n?|\n", text))


def declared(watch: Watch, held: dict, notes: list, slug: str) -> set:
    """The claims the guide lets the forget take besides the plain loop's: at each of its
    renders past the sixteenth that names a claim, those written after the named one that
    begin with indentation and have a line that opens a fence; and those written before,
    whose fence covers what follows once those have left (the plain loop on the rest)."""
    out: set = set()
    naming = [r for r in watch.renders if r[2] is not None]
    for title, claims, place, _ in naming[CEILING:]:
        blocks = compiler._blocks(
            title, claims, held, str, compiler.section_headings, compiler.heading_text
        )
        order = [claim for _, claim in blocks if claim is not None]
        named = claims[place - 1]
        at = next(n for n, claim in enumerate(order) if claim is named)
        after = [
            c for c in order[at + 1 :] if c.text[:1] in (" ", "\t") and opens_a_fence_line(c.text)
        ]
        out |= {key(c) for c in after}
        gone = {id(named), *map(id, after)}
        rest = [c for c in claims if id(c) not in gone]
        _, dropped, *_ = plain_loop(rest, held, notes, slug)
        out |= {key(c) for c in dropped if order.index(c) < at}
    return out


# Running a plan


class Run:
    """A plan, filed and stepped. `faults` holds every fault found, by step."""

    def __init__(self, plan: Plan, *, check_restore: bool = True) -> None:
        self.plan = plan
        self.store = Store(":memory:")
        self.store.create_project(PROJECT)
        self.pid = self.store.get_project(PROJECT).id
        self.ids: dict[str, str] = {}
        self.status: dict[str, str] = {}
        self.faults: list[tuple[int, str, str]] = []  # (step, kind, what)
        self.stats: list[dict[str, Any]] = []
        self.check_restore = check_restore
        for note in plan.notes:
            self.file(note)

    def close(self) -> None:
        self.store.close()

    def file(self, note: Note, store: Store | None = None) -> None:
        store = store or self.store
        pending = [note.inner] if note.inner else []

        def gate(candidate, spans):
            if pending:
                for inner in pending.pop():
                    self.file(inner, store)
            return lint_gate(candidate, spans)

        try:
            answer = Memory(store, gate).note(
                PROJECT, note.text, title=note.title, topic=note.topic
            )
            if store is self.store:
                self.ids[note.name] = answer["note_id"]
                self.status[note.name] = answer["status"]
        except RequestError as error:  # refused on the public path: nothing stored
            if store is self.store:
                self.status[note.name] = type(error).__name__
        if pending:
            for inner in pending.pop():
                self.file(inner, store)

    def fault(self, step: int, kind: str, what: str) -> None:
        self.faults.append((step, kind, what))

    def run(self) -> Run:
        for n, step in enumerate(self.plan.steps):
            if step.kind == "forget":
                if step.name in self.ids and self.store.get_note(self.ids[step.name]):
                    self.forget(n, self.ids[step.name])
            elif step.kind == "tidy":
                for page in self.store.list_pages(self.pid):
                    tidy_page(self.store, self.pid, page.slug)
            elif step.kind == "again":
                note = next((x for x in self.plan.stored() if x.name == step.name), None)
                if note is not None and note.name in self.ids:
                    self.file(replace(note, inner=[], name=note.name + "+"))
            elif step.kind == "note" and step.note is not None:
                self.file(step.note)
        return self

    # One forget, checked

    def forget(self, step: int, note_id: str) -> None:
        store, pid = self.store, self.pid
        want = expected(store, pid, note_id)
        notes_before = [n for n in store.list_notes(pid) if n.id != note_id]
        on_page_before = self.notes_on_pages()
        preview = store.forget_preview(note_id)
        archive = io.BytesIO()
        if self.check_restore:
            store.export(archive)
        watch = Watch()
        with watch.on():
            answer = Memory(store, lint_gate).forget(PROJECT, note_id)
        if self.check_restore:
            self.forget_restored(step, archive, note_id)
        stat: dict[str, Any] = {"step": step, "pages": len(want)}
        if set(answer["rebuilt"]) != set(preview.rebuilt) or set(answer["removed"]) != set(
            preview.removed
        ):
            self.fault(step, "preview", f"preview {preview} against {answer}")
        removed = {slug for slug, e in want.items() if e.body is None}
        if set(answer["removed"]) != removed or set(answer["rebuilt"]) != set(want) - removed:
            self.fault(step, "pages", f"rebuilt/removed {answer} against oracle {sorted(want)}")
        left_total = 0
        self.allowed: set = set()
        body0 = max((len(r[3]) for r in watch.renders), default=1)
        for slug, e in want.items():
            page = store.get_page(pid, slug)
            stat.setdefault("dropped", 0)
            stat["dropped"] += len(e.dropped)
            stat["title_changed"] = stat.get("title_changed", False) or len(set(e.titles)) > 1
            stat["top_moved"] = stat.get("top_moved", False) or moved(e.tops)
            stat["retitled"] = stat.get("retitled", False) or (
                bool(e.titles) and e.titles[0] != e.before.title
            )
            if getattr(e, "other", None):
                self.fault(step, "oracle-other", f"{slug}: faults besides fences {e.other}")
            actual = list(page.claims) if page else []
            left_total += len(e.start) - len(actual)
            self.compare(step, slug, e, page, watch, notes_before)
        stat["renders"] = len(watch.renders)
        stat["naming"] = sum(1 for r in watch.renders if r[2] is not None)
        stat["parses"] = len(watch.inputs)
        stat["chars"] = sum(watch.inputs)
        stat["largest"] = max(watch.inputs, default=0)
        stat["body"] = body0
        self.stats.append(stat)
        if stat["largest"] > 2 * body0 + 64:
            self.fault(
                step, "work-input", f"a parser input of {stat['largest']} on a body of {body0}"
            )
        if stat["renders"] > CEILING + 3:
            self.fault(step, "work-renders", f"{stat['renders']} renders")
        self.audit(step, note_id, len(want), left_total)
        self.orphans(step, note_id, on_page_before, want)
        if self.check_restore:
            self.restored(step)

    def forget_restored(self, step: int, archive: io.BytesIO, note_id: str) -> None:
        """The same forget, on a copy restored from the export made just before it."""
        with closing(Store(":memory:")) as copy:
            try:
                copy.import_export(io.BytesIO(archive.getvalue()))
            except ValueError as refused:
                self.fault(step, "import", f"the export before the forget: {refused}")
                return
            Memory(copy, lint_gate).forget(PROJECT, note_id)
            pid = copy.get_project(PROJECT).id
            mine = {p.slug: (p.title, p.body, p.claims) for p in self.store.list_pages(self.pid)}
            theirs = {p.slug: (p.title, p.body, p.claims) for p in copy.list_pages(pid)}
            if mine != theirs:
                slugs = sorted(
                    s for s in mine.keys() | theirs.keys() if mine.get(s) != theirs.get(s)
                )
                self.fault(
                    step, "restored-forget", f"the forget on a restored copy differs: {slugs}"
                )

    def compare(self, step, slug, e: Expected, page, watch: Watch, notes) -> None:
        want = [key(c) for c in e.kept]
        if e.body is None:
            if page is not None:
                self.fault(
                    step, "survivors", f"{slug}: oracle removes the page, the forget keeps it"
                )
            return
        if page is None:
            self.fault(
                step, "survivors", f"{slug}: the forget removed the page, oracle keeps {len(want)}"
            )
            return
        have = [key(c) for c in page.claims]
        if have != want:
            took = [k for k in want if k not in have]
            extra = [k for k in have if k not in want]
            allowed = declared(watch, e.held, notes, slug)
            naming = sum(1 for r in watch.renders if r[2] is not None)
            self.allowed |= allowed
            if took or extra:
                bad = [k for k in took if k not in allowed]
                if bad:
                    self.fault(
                        step,
                        "survivors",
                        f"{slug}: forget took {len(took)} the oracle keeps ({len(bad)} not of the"
                        f" declared class) after {naming} naming renders; oracle dropped"
                        f" {len(e.dropped)}; first {bad[0][0]!r}",
                    )
                if extra and naming <= CEILING:
                    self.fault(
                        step,
                        "kept-extra",
                        f"{slug}: forget kept {len(extra)} the oracle drops, {naming} naming"
                        f" renders; first {extra[0][0]!r}",
                    )
                elif extra:
                    self.fault(step, "past-ceiling-kept", f"{slug}: kept {len(extra)} more")
                return
            self.fault(step, "order", f"{slug}: the claims are in another order")
            return
        if page.title != e.title:
            self.fault(step, "title", f"{slug}: title {page.title!r} against {e.title!r}")
        if page.body != e.body:
            self.fault(step, "body", f"{slug}: the body is not the oracle's")
        ids = [c.id for c in page.claims]
        if len(set(ids)) != len(ids):
            self.fault(step, "ids", f"{slug}: two claims hold one id")

    def notes_on_pages(self) -> dict[str, int]:
        count: dict[str, int] = {}
        for page in self.store.list_pages(self.pid):
            spans = self.store.get_spans(i for c in page.claims for i in c.span_ids)
            for claim in page.claims:
                for source in {spans[i].source_id for i in claim.span_ids if i in spans}:
                    count[source] = count.get(source, 0) + 1
        return count

    def orphans(self, step: int, note_id: str, before: dict[str, int], want) -> None:
        """No note but the forgotten one loses its last claim unless the oracle agrees."""
        after = self.notes_on_pages()
        oracle: dict[str, int] = {}
        for _slug, e in want.items():
            for claim in e.kept:
                for source in {e.held[i].source_id for i in claim.span_ids if i in e.held}:
                    oracle[source] = oracle.get(source, 0) + 1
        touched = {
            e.held[i].source_id
            for e in want.values()
            for c in e.start
            for i in c.span_ids
            if i in e.held
        }
        # the notes of the claims the oracle keeps that the declared class does not cover
        undeclared = {
            e.held[i].source_id
            for e in want.values()
            for c in e.kept
            if key(c) not in self.allowed
            for i in c.span_ids
            if i in e.held
        }
        for source in touched:
            if (
                source != note_id
                and before.get(source)
                and not after.get(source)
                and oracle.get(source)
            ):
                kind = "orphan" if source in undeclared else "orphan-declared"
                self.fault(step, kind, f"note {source} lost its last claim; the oracle keeps one")

    def audit(self, step: int, note_id: str, pages: int, left: int) -> None:
        events = [e for e in self.store.list_audit(50) if e.action == "note.forgotten"]
        event = next((e for e in events if e.detail.get("note") == note_id), None)
        if event is None:
            self.fault(step, "audit", "no note.forgotten event")
            return
        if event.detail.get("pages") != pages:
            self.fault(step, "audit", f"pages {event.detail.get('pages')} against {pages}")
        if event.detail.get("claims_left", 0) != left:
            self.fault(
                step, "audit", f"claims_left {event.detail.get('claims_left', 0)} against {left}"
            )

    def restored(self, step: int) -> None:
        archive = io.BytesIO()
        self.store.export(archive)
        with closing(Store(":memory:")) as copy:
            try:
                copy.import_export(io.BytesIO(archive.getvalue()))
            except ValueError as refused:
                self.fault(step, "import", f"the export does not import: {refused}")
                return
            pid = copy.get_project(PROJECT).id
            mine = {p.slug: p for p in self.store.list_pages(self.pid)}
            theirs = {p.slug: p for p in copy.list_pages(pid)}
            if mine.keys() != theirs.keys():
                self.fault(step, "import", "the restored copy has other pages")
                return
            for slug, page in mine.items():
                other = theirs[slug]
                if (page.title, page.body, page.claims) != (other.title, other.body, other.claims):
                    self.fault(step, "import", f"{slug}: the restored page differs")
            for token in sorted(tokens(self.store, self.pid)):
                here = hits(self.store, token)
                there = hits(copy, token)
                want = {
                    (slug, c.id, c.text)
                    for slug, page in mine.items()
                    for c in page.claims
                    if re.search(rf"\b{token}\b", c.text)
                }
                if here != want:
                    self.fault(step, "recall", f"{token}: hits {len(here)} against {len(want)}")
                if here != there:
                    self.fault(step, "recall-restored", f"{token}: hits differ after restore")


TOKEN = re.compile(r"\bkq[bcdfghjklmnpqrtvwxz]{4}\b")


def tokens(store: Store, pid: str) -> set[str]:
    return {t for note in store.list_notes(pid) for t in TOKEN.findall(note.text)}


def hits(store: Store, token: str) -> set:
    found = Memory(store, lint_gate).recall(token, project=PROJECT, limit=50)["hits"]
    return {(h["page"], h["claim_id"], h["text"]) for h in found}


# The generator

FAMILIES = (
    ("Ops", "OPS", "ops", "Ops.", "Óps", "(ops)"),
    ("Field notes", "FIELD NOTES", "field-notes", "Field_Notes", "field notes!"),
)
MARKERS = ("-", "*", "+", "1.", "1)", "10.")
WORDS = ("almanac", "sundial", "compass", "harbour", "lantern", "meadow", "quarry", "orchard")


class Gen:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(f"rt1:{seed}")
        self.count = itertools.count()
        self.names = itertools.count()

    def tok(self) -> str:
        n = next(self.count)
        letters = "bcdfghjklmnpqrtvwxz"
        out = ""
        for _ in range(4):
            out = letters[n % len(letters)] + out
            n //= len(letters)
        return "kq" + out

    def word(self) -> str:
        return f"{self.rng.choice(WORDS)} {self.tok()}"

    def fence_run(self) -> str:
        char = self.rng.choice("~~`")
        run = char * self.rng.choice((3, 3, 3, 4, 5))
        info = self.rng.choice(("", "", "", "text", "py"))
        return run + info

    # pieces: each is text that starts a passage; `ind` its indentation

    def sibling(self, ind: str) -> str:
        marker = self.rng.choice(MARKERS)
        gap = self.rng.choice((" ", " ", "   ", "  ", "\t" if self.rng.random() < 0.1 else " "))
        inner = ind + " " * (len(marker) + len(gap.expandtabs(4)))
        fence = self.fence_run()
        if self.rng.random() < 0.5:
            return f"{ind}{marker}{gap}{fence}\n{inner}{self.word()}"
        return f"{ind}{marker}{gap}item {self.tok()}\n{inner}{fence}\n{inner}{self.word()}"

    def nested(self, ind: str) -> str:
        """Items nested up to nine deep, the last with a fence no line closes."""
        depth = self.rng.choice((2, 3, 5, 9))
        lines, at = [], ind
        for d in range(depth):
            marker = self.rng.choice(MARKERS)
            lines.append(f"{at}{marker} level {d} {self.tok()}")
            at += " " * (len(marker) + 1)
        lines.append(f"{at}{self.fence_run()}")
        lines.append(f"{at}{self.word()}")
        return "\n".join(lines)

    def running(self, ind: str) -> str:
        return f"{ind}{self.fence_run()}\n{ind}{self.word()}"

    def closed(self, ind: str) -> str:
        fence = self.fence_run()
        run = re.match(r"[`~]+", fence).group()
        return f"{ind}{fence}\n{ind}{self.word()}\n{ind}{run}"

    def plain(self, ind: str) -> str:
        return f"{ind}{self.word()}"

    def quoted(self, ind: str) -> str:
        return f"{ind}> {self.fence_run()}\n{ind}> {self.word()}"

    def tabbed(self, ind: str) -> str:
        return (
            f"\t{self.fence_run()}\n\t{self.word()}"
            if self.rng.random() < 0.5
            else (f"{ind}-\t{self.fence_run()}\n{ind}\t{self.word()}")
        )

    def margin(self) -> str:
        kind = self.rng.choice(
            ("inline", "fenced", "setext", "rule", "linkdef", "html", "long", "quote", "para")
        )
        w = self.word()
        return {
            "inline": f"Keep `{w}` open.",
            "fenced": f"~~~text\n{w}\n~~~",
            "setext": f"Heading {w}\n{self.rng.choice(('---', '==='))}",
            "rule": f"{w}\n\n{self.rng.choice(('***', '___', '* * *'))}",
            "linkdef": f"[{w.split()[1]}]: /notes/{w.split()[1]}",
            "html": self.rng.choice((f"<div>{w}</div>", f"<!-- {w}")),
            "long": f"{w} " + "x" * self.rng.choice((200, 2000, 9000)),
            "quote": f"> ~~~\n> {w}",
            "para": f"Note on {w}.",
        }[kind]

    def tail(self) -> str:
        return " " * self.rng.choice((1, 2, 3, 4, 5, 6, 7, 8, 8, 9, 10, 12)) + self.word()

    # segments: lists of note texts, in the order of their claims

    def list_segment(self, at_risk: bool = False) -> list[tuple[str, str]]:
        """(role, text) of each note: a list item at the margin, notes under it, a
        paragraph at the margin, notes after it. `at_risk`: the last note under the item is
        one sibling, and the first tail is deep enough for its fence to cover."""
        marker = self.rng.choice(MARKERS)
        gap = self.rng.choice((" ", " ", "  ", "   ", "    "))
        column = len(marker) + len(gap)
        ind = " " * max(4 if self.rng.random() < 0.5 else column, column)
        out = [("parent", f"{marker}{gap}parent {self.tok()}")]
        kinds = self.rng.choice(
            (
                ["sibling"],
                ["sibling"] * 4 + ["running", "closed"],
                ["sibling", "nested", "running", "plain", "closed", "quoted", "tabbed"],
                ["running"] * 3 + ["sibling"],
            )
        )
        group: list[str] = []
        for _ in range(self.rng.choice((1, 2, 3, 4, 6, 8, 12, 17, 20, 24, 30))):
            kind = self.rng.choice(kinds)
            piece = getattr(self, kind)(ind)
            group.append(piece)
            if self.rng.random() < (0.85 if at_risk else 0.6) or kind in ("tabbed",):
                out.append(("under", "\n\n".join(group)))
                group = []
        if group:
            out.append(("under", "\n\n".join(group)))
        if at_risk:
            out.append(("under", self.sibling(ind)))
        out.append(("separator", f"Between {self.tok()}."))
        if at_risk:
            deep = " " * (len(ind) + self.rng.choice((5, 6, 8)))
            if self.rng.random() < 0.2:  # a long first line: few claims fit in a question
                deep += "y" * self.rng.choice((1000, 3000, 6000)) + " "
            out.append(("tail", deep + self.word()))
            if self.rng.random() < 0.4:  # an indented example with its own closing line
                out.append(("tail", self.closed(" " * self.rng.choice((2, 6, 8)))))
        for _ in range(self.rng.choice((1, 1, 2, 3))):
            if self.rng.random() < 0.7:
                out.append(("tail", self.tail()))
            else:
                out.append(
                    (
                        "tail",
                        getattr(self, self.rng.choice(("running", "sibling", "closed")))(
                            " " * self.rng.choice((2, 4, 6, 8))
                        ),
                    )
                )
        return out

    def spender(self, prespent: bool | None = None) -> list[tuple[str, str]]:
        """A list item and indented fences that spend the body's closers: at once (no
        paragraph between, sixteen at most, or the note is refused its page), or once the
        paragraph at the margin between them is forgotten."""
        if prespent if prespent is not None else self.rng.random() < 0.5:
            k = self.rng.choice((15, 16, 16, 16))
            runs = [f"    {self.fence_run()}\n    {self.word()}" for _ in range(k)]
            return [
                ("parent", f"- item {self.tok()}"),
                ("under", "\n\n".join([*runs, f"  End {self.tok()}."])),
            ]
        k = self.rng.choice((16, 16, 17, 20, 33, 40))
        runs = [f"    {self.fence_run()}\n    {self.word()}" for _ in range(k)]
        return [
            ("parent", f"- item {self.tok()}"),
            ("separator", f"Between {self.tok()}."),
            ("under", "\n\n".join([*runs, f"  End {self.tok()}."])),
        ]

    def plan(self) -> Plan:
        """A page. On about half, the oldest note (it gives the title) is the last note of
        a list's siblings, filed after the notes before it, under a spelling of the topic
        that no other note uses; then the list's paragraph at the margin is forgotten."""
        rng = self.rng
        family = rng.choice(FAMILIES)
        giver = rng.random() < 0.55
        spellings = rng.sample(family, rng.choice((2, 2, 3) if giver else (1, 2, 2, 3)))
        sections = [*spellings, "Steps", "First"]
        spent = self.spender(rng.random() < 0.8 if giver else None)
        if giver and rng.random() < 0.85:
            segments = [(spent, None)]  # in the body before the list: its closers go first
        else:
            segments = [(spent, rng.choice((None, None, *spellings, "First")))]
        for n in range(rng.choice((1, 1, 2))):
            seg = self.list_segment(at_risk=giver and n == 0)
            segments.append((seg, rng.choice((None, None, *spellings, "Steps"))))
        if not giver or rng.random() < 0.15:
            rng.shuffle(segments)
        if rng.random() < 0.5:
            at = rng.randrange(len(segments) + 1)
            segments.insert(at, ([("margin", self.margin())], None))
        roles: list[tuple[str, str, int]] = []
        for number, (seg, heading) in enumerate(segments):
            shape = rng.choices(("plain", "deeper", "first"), (0.85, 0.1, 0.05))[0]
            sub = rng.choice(sections)
            for role, text in seg:
                here = heading
                if rng.random() < 0.06:  # one note of the run under another spelling
                    here = rng.choice((None, *spellings))
                if role == "tail" and rng.random() < 0.4:  # a tail under its own heading
                    here = rng.choice((None, *spellings))
                if here:
                    head = {
                        "plain": f"## {here}\n\n",
                        "deeper": f"## {here}\n\n### {sub}\n\n",
                        "first": f"# {here}\n\n",
                    }[shape]
                    text = head + text
                roles.append((role, text, number))
        # passages that differ only by white space, for a tidy to fold
        if rng.random() < 0.25:
            role, text, number = rng.choice(roles)
            spaced = re.sub(r"(\S)$", r"\1  ", text, count=1, flags=re.M)
            roles.insert(
                rng.randrange(len(roles) + 1),
                ("copy", spaced + f"\n\nCopy {self.tok()}.", number),
            )
        notes, segment_of = [], {}
        for role, text, number in roles:
            name = f"{role}{next(self.names)}"
            topic, title = rng.choice(spellings), None
            r = rng.random()
            if r < 0.2:
                topic, title = None, rng.choice(spellings)
            elif r < 0.26 and not text.startswith("#"):  # titled by its own first heading
                topic, text = None, f"# {rng.choice(spellings)}\n\n{text}"
            if rng.random() < 0.05:
                text = text.replace("\n", "\r\n")
            notes.append(Note(name, text, topic, title))
            segment_of[name] = number
        steps: list[Step] = []
        top = notes
        if giver:
            first, rest = spellings[0], spellings[1:]
            lists = [
                n
                for n, note in enumerate(notes)
                if note.name.startswith("separator") and n and notes[n - 1].name.startswith("under")
            ]
            if lists:
                sep = rng.choice(lists)
                j = sep - 1
                for n, note in enumerate(notes):
                    spelled = first if n == j else rng.choice(rest)
                    if note.topic is None and note.title is None:
                        continue
                    if note.topic is None:
                        notes[n] = replace(note, title=spelled)
                    else:
                        notes[n] = replace(note, topic=spelled)
                spender = [
                    n.name
                    for n in notes
                    if n.name.startswith("separator")
                    and n.name != notes[sep].name
                    and segment_of[n.name] != segment_of[notes[sep].name]
                ]
                if spender and rng.random() < 0.5:
                    steps.append(Step("forget", rng.choice(spender)))
                steps.append(Step("forget", notes[sep].name))
                run = j
                while run and notes[run - 1].name.startswith("under"):
                    run -= 1
                if rng.random() < 0.4 and run < j:
                    # a chain: each note of the run files the ones before it in its gate, so
                    # the run is stored in the reverse of its order on the page, and older
                    # than every note before it: each, named, is the oldest note's last claim
                    inner = notes[:run]
                    for n in range(run, j + 1):
                        inner = [replace(notes[n], inner=inner)]
                    top = [*inner, *notes[j + 1 :]]
                else:
                    i = 0 if rng.random() < 0.85 else rng.randrange(0, j)
                    top = [*notes[:i], replace(notes[j], inner=notes[i:j]), *notes[j + 1 :]]
        if not steps:
            for _ in range(rng.choice((0, 1, 1, 1, 2))):
                if len(top) < 2:
                    break
                under = [n for n in range(1, len(top)) if top[n].name.startswith("under")]
                j = (
                    rng.choice(under)
                    if under and rng.random() < 0.6
                    else rng.randrange(1, len(top))
                )
                i = 0 if rng.random() < 0.4 else rng.randrange(0, j)
                top = [*top[:i], replace(top[j], inner=[*top[j].inner, *top[i:j]]), *top[j + 1 :]]
        copied = any(note.name.startswith("copy") for note in notes)
        if rng.random() < (0.7 if copied else 0.15):
            steps.insert(0, Step("tidy"))
        flat = Plan(top, []).stored()
        for _ in range(rng.choice((0, 1, 1, 2) if steps else (1, 1, 1, 2, 3))):
            r = rng.random()
            pool = [n.name for n in flat if n.name.startswith("separator")]
            if r < 0.6 and pool:
                target = rng.choice(pool)
            elif r < 0.72:
                target = flat[0].name  # the oldest: it titles the page
            elif r < 0.8:
                target = flat[-1].name
            elif r < 0.86:
                target = top[0].name
            else:
                target = rng.choice(flat).name
            if rng.random() < 0.1:
                steps.append(Step("again", rng.choice(flat).name))
            if rng.random() < 0.1:
                late = Note(f"late{next(self.names)}", self.tail(), rng.choice(spellings))
                steps.append(Step("note", note=late))
            steps.append(Step("forget", target))
        return Plan(top, steps)


def generated(seed: int) -> Plan:
    return Gen(seed).plan()


# Shrinking


# What the guide declares, seen and counted, never a fault: a note that lost its last claim to
# the declared class, and a claim kept past the ceiling that the plain loop drops.
OBSERVED = frozenset({"orphan-declared", "past-ceiling-kept"})


def signature(run: Run) -> set[str]:
    return {kind for _, kind, _ in run.faults if kind not in OBSERVED}


def observed(run: Run) -> set[str]:
    return {kind for _, kind, _ in run.faults if kind in OBSERVED}


def fails(plan: Plan, kinds: set[str], restore: bool) -> bool:
    try:
        run = Run(plan, check_restore=restore).run()
    except Exception:  # a plan the shrinker broke: not the same failure
        return False
    try:
        return bool(signature(run) & kinds)
    finally:
        run.close()


def candidates(plan: Plan) -> Iterator[Plan]:
    """Smaller plans: a step less, a note less (its inner notes kept in its place, or
    dropped with it), a note un-nested, a piece of a note's text less, a simpler topic."""
    for n in range(len(plan.steps)):
        yield Plan(plan.notes, plan.steps[:n] + plan.steps[n + 1 :])

    def edits(notes: list[Note]) -> Iterator[list[Note]]:
        for n, note in enumerate(notes):
            yield notes[:n] + notes[n + 1 :]
            if note.inner:
                yield notes[:n] + note.inner + notes[n + 1 :]
                yield [*notes[:n], replace(note, inner=[]), *note.inner, *notes[n + 1 :]]
            for inner in edits(note.inner):
                yield [*notes[:n], replace(note, inner=inner), *notes[n + 1 :]]
            pieces = note.text.split("\n\n")
            for k in range(len(pieces)):
                if len(pieces) > 1:
                    text = "\n\n".join(pieces[:k] + pieces[k + 1 :])
                    yield [*notes[:n], replace(note, text=text), *notes[n + 1 :]]
            if note.title is not None:
                yield [*notes[:n], replace(note, title=None, topic=note.title), *notes[n + 1 :]]

    for notes in edits(plan.notes):
        yield Plan(notes, plan.steps)


def shrink(plan: Plan, kinds: set[str], budget: float = 240.0) -> Plan:
    restore = bool(kinds & {"import", "recall", "recall-restored"})
    end = time.monotonic() + budget
    better = True
    while better and time.monotonic() < end:
        better = False
        for smaller in candidates(plan):
            if time.monotonic() > end:
                break
            if fails(smaller, kinds, restore):
                plan, better = smaller, True
                break
    return plan


# By hand


def main(argv: list[str]) -> None:
    lo, hi = 0, 100
    for arg in argv:
        if arg.startswith("--seeds="):
            lo, hi = map(int, arg.split("=", 1)[1].split(":"))
    do_shrink = "--shrink" in argv
    totals: dict[str, int] = {}
    worst: dict[str, tuple] = {}
    found: dict[str, list[int]] = {}
    started = time.monotonic()
    for seed in range(lo, hi):
        plan = generated(seed)
        run = Run(plan).run()
        for stat in run.stats:
            totals["forgets"] = totals.get("forgets", 0) + 1
            totals["dropped"] = totals.get("dropped", 0) + bool(stat.get("dropped"))
            totals["title_changed"] = totals.get("title_changed", 0) + bool(
                stat.get("title_changed")
            )
            totals["top_moved"] = totals.get("top_moved", 0) + bool(stat.get("top_moved"))
            totals["retitled"] = totals.get("retitled", 0) + bool(stat.get("retitled"))
            totals["past16"] = totals.get("past16", 0) + (stat.get("dropped", 0) > CEILING)
            for m in ("renders", "parses", "chars", "largest"):
                ratio = stat[m] / max(stat["body"], 1) if m in ("chars", "largest") else stat[m]
                if ratio > worst.get(m, (0,))[0]:
                    worst[m] = (ratio, seed, stat["body"])
        for kind in signature(run):
            found.setdefault(kind, []).append(seed)
        for kind in observed(run):
            totals[kind] = totals.get(kind, 0) + 1
        run.close()
    print(f"seeds {lo}:{hi} in {time.monotonic() - started:.0f}s")
    print("coverage", totals)
    print("worst", worst)
    for kind, seeds in sorted(found.items()):
        print("FAULT", kind, len(seeds), seeds[:20])
    if do_shrink:
        for kind, seeds in sorted(found.items()):
            plan = generated(seeds[0])
            small = shrink(plan, {kind})
            run = Run(small).run()
            print(f"--- {kind} from seed {seeds[0]}")
            print(small.show())
            for fault in run.faults:
                print("   ", fault)
            run.close()


if __name__ == "__main__":
    main(sys.argv[1:])
