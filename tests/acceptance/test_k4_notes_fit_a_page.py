"""K4 acceptance tests: what a source writes fits a page, by both of a page's bounds.

Written kind by kind, each kind's tests before its change; frozen when it is merged.

A page holds at most 2,000 claims (cairn.store.PAGE_CLAIMS_MAX) and 400,000 characters of
claims (PAGE_CHARS_MAX, G1), and a note past either alone is refused before it is stored. Every
kind of source bounded what it wrote by its own numbers, chosen when the only bound was the
claims, so some of what they wrote was refused as a full page: a merged pull request of
600,000 characters, a part of a long document, a long meeting's transcript.

The contract
1. No kind writes a note that a page's bounds refuse for its size alone. A kind that cuts
   cuts to fit both bounds, and a line in the note says it was cut (GitHub, Linear, Google
   Calendar, Granola's summary, Langfuse). A kind that splits into parts makes each part fit
   both bounds, and the parts hold all of the text between them (Google Drive, Gmail, and
   Granola's and Google Meet's transcripts); one passage, or one turn, too long for a page is
   split inside itself. The numbers come from the store's constants, read when a note is made
   (cairn.sources.fit): lowered by a test, they move every kind's cut and parts with them.
2. Facts. A note may hold at most 30,000 facts (cairn.store.FACTS_MAX). A source's note with
   more is still skipped, not cut (a document of 30,000 numbers is not prose to keep), and
   the run's report says why: "facts past their bound".
3. A connector's item is not cut: its text is the connector's to shape. One whose text is
   past a page's bounds is skipped with a reason of its own, which says the item is too long
   and never "page full"; nothing is full. The protocol (docs/connector-protocol.md) states
   that bound, and connector-check holds an item to it under "every item within its bounds".
4. Nothing already stored changes, and every kind's own acceptance file passes as it is.

The table (the largest note each kind could write by its own bounds before K4, what a page's
bounds did with it, and what the kind writes now; characters are of the note's text, passages
as the store counts claims) is TABLE below, and names every kind in KINDS. Each kind's test
here writes that note through `run_source` with the kind's own stand-in, imported from its
frozen acceptance file as test_k2_backfill.py does, and checks that it is saved (not
skipped), holds its first words and the line that says it was cut (or, for parts, all of the
text between them), and fits; then lowers a page's bounds and checks that the kind's cut moves
with them. The connector's tests check the skip and its reason, through the runner and
connector-check; the facts tests write a note of 30,001 numbers through GitHub and Google
Drive. Everything is made up and offline; every clock is in 2099.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import test_c2_langfuse as c2
import test_c3_granola as c3
import test_c4_google_meet as c4
import test_c5_google_calendar as c5
import test_c6_gmail as c6
import test_c7_google_drive as c7
import test_c9_connector as c9
import test_c10_linear as c10
import test_c11_github as c11
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import cairn.memory
import cairn.store.memory
from cairn.sources import KINDS, fit, run_source
from cairn.sources import connector as conn
from cairn.sources import github as gh
from cairn.sources import gmail as gm
from cairn.sources import google_calendar as cal
from cairn.sources import google_drive as gd
from cairn.sources import granola as gr
from cairn.sources import linear as lin
from cairn.store import FACTS_MAX, PAGE_CHARS_MAX, PAGE_CLAIMS_MAX, Store

MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)

# kind: (its largest note before K4, in characters and passages as the store counts claims,
# worked out from the kind's own constants; what a page's bounds did with it, seen by running
# this file's tests against the kind before its change; what the kind writes now, the sizes
# printed once from this file's largest-note tests, which assert only that each note fits)
TABLE = {
    "github": (
        "about 1,000,000 characters, at most 1,500 passages",
        "refused: past 400,000 characters",
        "saved, cut to fit with a line: 399,055 characters, 92 passages",
    ),
    "linear": (
        "about 4,100,000 characters, at most 1,510 passages",
        "refused: past 400,000 characters",
        "saved, cut to fit with a line: 399,125 characters, 118 passages",
    ),
    "langfuse": (
        "about 60,300 characters (three values of 20,000), about 10 passages",
        "taken",
        "taken as before: 60,276 characters, 7 passages; each value cut at a third of a page",
    ),
    "google_calendar": (
        "up to about 8,000,000 characters (bounded only by the 8 MiB answer), passages unbounded",
        "refused: past 400,000 characters or 2,000 claims",
        "saved, the description cut to fit with a line: 398,642 characters, 83 passages",
    ),
    "granola summary": (
        "up to about 8,000,000 characters (bounded only by the 8 MiB answer), passages unbounded",
        "refused: past 400,000 characters or 2,000 claims",
        "saved, the summary cut to fit with a line",
    ),
    "granola transcript part": (
        "unbounded characters (1,500 turns, a turn as long as one speaker talks), 1,500 passages",
        "refused: past 400,000 characters",
        "parts that fit and hold every turn: the largest 400,046 characters, 84 passages",
    ),
    "google_meet transcript part": (
        "unbounded characters (the facts and 1,500 turns), at most 1,504 passages",
        "refused: past 400,000 characters",
        "parts that fit and hold every turn: the largest 400,049 characters, 5 passages",
    ),
    "google_drive part": (
        "up to about 10,000,000 characters (its 10 MB), at most 1,500 passages",
        "refused: past 400,000 characters",
        "parts that fit and hold all the text: the largest 399,868 characters, 83 passages;"
        " past 20 parts (about 8,000,000 characters) it is skipped as too long",
    ),
    "gmail part": (
        "up to about 6,000,000 characters (the 8 MB answer), at most 1,500 passages",
        "refused: past 400,000 characters",
        "parts that fit and hold the whole body: the largest 400,006 characters, 83 passages",
    ),
    "connector item": (
        "up to about 4,000,000 characters (the 4 MiB answer), passages unbounded",
        "skipped as 'page full', though nothing was full",
        "not cut; skipped as an item whose text is longer than a page holds",
    ),
}


def test_the_table_names_every_kind():
    assert {row.split()[0] for row in TABLE} == set(KINDS)


def test_the_bounds_are_the_store_s():
    assert (fit.claims(), fit.chars()) == (PAGE_CLAIMS_MAX, PAGE_CHARS_MAX) == (2_000, 400_000)


@pytest.fixture
def lowered(monkeypatch):
    """A page's bounds lowered to `chars` characters and `claims` claims, where the store and
    the note path read them."""

    def lower(chars: int, claims: int) -> None:
        for module in (cairn.store.memory, cairn.memory):
            monkeypatch.setattr(module, "PAGE_CHARS_MAX", chars)
            monkeypatch.setattr(module, "PAGE_CLAIMS_MAX", claims)

    return lower


SENTENCE = "the ledger holds every entry once, as before. "


def filler(chars: int, mark: str, paragraph: int = 5_000) -> str:
    """Paragraphs of plain words of about `paragraph` characters each, `chars` characters in
    all or a little more, the first opening with `mark`: one number a paragraph, so a long
    text stays far inside a note's facts and its length, not its passages, is what counts."""
    said, size, k = [], 0, 0
    while size < chars:
        lead = f"{mark if k == 0 else 'Then'} {k:05d}: "
        words = SENTENCE * max(1, (min(paragraph, chars - size) - len(lead)) // len(SENTENCE))
        said.append(lead + words.rstrip())
        size += len(said[-1]) + 2
        k += 1
    return "\n\n".join(said)


def fitting(text: str) -> bool:
    passages, chars = fit.measure(text)
    return passages <= fit.claims() and chars <= fit.chars()


# GitHub: a merged pull request with a long description and many long comments


@pytest.fixture
def github(tmp_path):
    fake = c11.FakeGitHub(c11.Clock(c11.NOW))
    world = c11.make_world(tmp_path, fake)
    c11.start(world)
    yield SimpleNamespace(world=world, fake=fake)
    world.store.close()
    assert fake.problems() == []


def long_pull_request(fake, body_chars: int, comments: int, comment_chars: int) -> None:
    item = fake.pull_request(
        1, "A long description", body=filler(body_chars, "Opening"),
        merged=datetime(2099, 3, 2, tzinfo=UTC),
    )  # fmt: skip
    for n in range(comments):
        at = datetime(2099, 2, 1, tzinfo=UTC) + n * MINUTE
        fake.comment(item, at, "cy-moss", filler(comment_chars, f"Comment{n}"))


def test_github_the_largest_note_is_saved_cut_to_a_page(github):
    long_pull_request(github.fake, 260_000, 20, 70_000)  # 1,660,000 characters given
    report = c11.run(github.world, github.fake, c11.NOW)
    assert (report.saved, report.skipped) == (1, 0), report
    (held,) = c11.notes(github.world)
    text = held.text
    assert fitting(text) and len(text) > 300_000
    assert "Opening 00000: the ledger" in text and gh.CUT_LINES["pull"] in text
    assert "Comment0 00000" in text and "more comments are left out." in text


def test_github_a_lowered_page_moves_the_cut(github, lowered):
    long_pull_request(github.fake, 260_000, 3, 70_000)
    lowered(50_000, 120)
    report = c11.run(github.world, github.fake, c11.NOW)
    assert (report.saved, report.skipped) == (1, 0), report
    (held,) = c11.notes(github.world)
    assert fitting(held.text) and "Opening 00000" in held.text
    assert gh.CUT_LINES["pull"] in held.text


# Linear: an issue with a long description and two hundred long comments


@pytest.fixture
def linear(tmp_path):
    c10._MADE.clear()
    fake = c10.FakeLinear(c10.Clock(c10.NOW))
    clock = c10.Clock(c10.SINCE - DAY)
    store = Store(tmp_path / "cairn.db", clock=clock)
    store.create_project("linear")
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    world = SimpleNamespace(store=store, clock=clock, http=http)
    c10.switched_on(world)
    yield SimpleNamespace(world=world, fake=fake)
    store.close()
    assert fake.violations == []


def long_issue(fake, description_chars: int, comments: int, comment_chars: int) -> None:
    issue = fake.issue(1, "A long issue", description=filler(description_chars, "Opening"))
    for n in range(comments):
        at = c10.SINCE - 2 * DAY + n * MINUTE
        fake.comment(issue, n + 1, filler(comment_chars, f"Comment{n}"), at)


def run_linear(linear):
    linear.fake.clock.now = c10.NOW
    linear.fake.new_pull()
    return asyncio.run(
        run_source(
            linear.world.store, "linear", http=linear.world.http, env=c10.ENV,
            clock=linear.fake.clock, sleep=linear.fake.sleep,
        )
    )  # fmt: skip


def test_linear_the_largest_note_is_saved_cut_to_a_page(linear):
    long_issue(linear.fake, 110_000, 200, 21_000)  # 4,310,000 characters given
    report = run_linear(linear)
    assert (report.saved, report.skipped) == (1, 0), report
    (held,) = c10.notes(linear.world)
    text = held.text
    assert fitting(text) and len(text) > 300_000
    assert "Opening 00000: the ledger" in text and "The description is cut here" in text
    assert "Comment0 00000" in text and "more comments, not written here." in text


def test_linear_a_lowered_page_moves_the_cut(linear, lowered):
    long_issue(linear.fake, 110_000, 3, 21_000)
    lowered(50_000, 120)
    report = run_linear(linear)
    assert (report.saved, report.skipped) == (1, 0), report
    (held,) = c10.notes(linear.world)
    assert fitting(held.text) and "Opening 00000" in held.text
    assert "The description is cut here" in held.text
    assert lin.DESCRIPTION_CHARS > 50_000


# Langfuse: a marked trace whose comment, input and output are each long


LF_NOW = datetime(2099, 11, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def langfuse(tmp_path, monkeypatch):
    monkeypatch.setattr(c2, "NOW", LF_NOW)  # the stand-in stamps its answers from this
    fake = c2.FakeLangfuse()
    store = Store(tmp_path / "cairn.db")
    for name in ("payments-api", "story-bible"):
        store.create_project(name)
    store.add_source("traces", "langfuse", c2.CONFIG)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    yield SimpleNamespace(store=store, fake=fake, http=http)
    store.close()


def long_trace(fake, chars: int) -> None:
    fake.trace("t-long", "long-flow", filler(chars, "Input"), filler(chars, "Output"))
    fake.score("s1", "payments-api", "t-long", 20, comment=filler(chars, "Because"))


def run_langfuse(langfuse):
    async def sleep(seconds: float) -> None:
        return None

    return asyncio.run(
        run_source(
            langfuse.store, "traces", http=langfuse.http, env=c2.ENV, clock=lambda: LF_NOW,
            sleep=sleep,
        )
    )  # fmt: skip


def langfuse_notes(langfuse) -> list:
    return langfuse.store.list_notes(langfuse.store.get_project("payments-api").id)


def test_langfuse_the_largest_note_is_saved_each_value_cut(langfuse):
    long_trace(langfuse.fake, 30_000)
    report = run_langfuse(langfuse)
    assert (report.saved, report.skipped) == (1, 0), report
    (held,) = langfuse_notes(langfuse)
    text = held.text
    assert fitting(text) and "Because 00000" in text and "Input 00000" in text
    assert "Output 00000" in text and text.count("more characters were left out.") == 3


def test_langfuse_a_lowered_page_moves_the_cut(langfuse, lowered):
    long_trace(langfuse.fake, 30_000)
    lowered(24_000, 120)  # a third of it is less than the 20,000 a value is cut at
    report = run_langfuse(langfuse)
    assert (report.saved, report.skipped) == (1, 0), report
    (held,) = langfuse_notes(langfuse)
    assert fitting(held.text) and "Input 00000" in held.text and "Output 00000" in held.text
    assert held.text.count("more characters were left out.") == 3


# The Google kinds' stand-ins sign in with a key pair made for the run, as their files do


@pytest.fixture(scope="module")
def keys():
    """A key pair made for this run: no key is written in this file."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    body = [line for line in pem.splitlines() if len(line) >= 16 and not line.startswith("-----")]
    return SimpleNamespace(pem=pem, public=private.public_key(), body=body)


def google_world(tmp_path, module, fake, name: str, kind: str, project: str, on: datetime, cfg):
    """A store with `project`, the source `name` added and switched on at `on`, and a runner."""
    clock = module.Clock(on - DAY)
    store = Store(tmp_path / "cairn.db", clock=clock)
    store.create_project(project)
    store.add_source(name, kind, cfg(projects=[]))
    clock.now = on
    store.enable_source(name, projects=[project])
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))

    def run(at: datetime, cred: str):
        fake.clock.now = at
        fake.new_pull()
        return asyncio.run(
            run_source(
                store, name, http=http, env=fake.env(cred), clock=fake.clock, sleep=fake.sleep
            )
        )

    def notes() -> list:
        return store.list_notes(store.get_project(project).id)

    return SimpleNamespace(store=store, fake=fake, run=run, notes=notes)


# Google Calendar: an event with a long description


CAL_NOW = datetime(2099, 11, 13, 15, 30, tzinfo=UTC)


@pytest.fixture
def calendar(tmp_path, keys):
    fake = c5.FakeGoogle(c5.Clock(CAL_NOW), keys)
    made = google_world(
        tmp_path, c5, fake, "calendar", "google_calendar", "calendar", CAL_NOW - DAY, c5.config
    )
    yield made
    made.store.close()


def long_event(fake, chars: int) -> None:
    start = CAL_NOW - 5 * HOUR
    fake.event(1, start, start + HOUR, summary="Long review", description=filler(chars, "Agenda"))


def test_google_calendar_the_largest_note_is_saved_cut_to_a_page(calendar):
    long_event(calendar.fake, 600_000)
    report = calendar.run(CAL_NOW, c5.SA)
    assert (report.saved, report.skipped) == (1, 0), report
    (held,) = calendar.notes()
    text = held.text
    assert fitting(text) and len(text) > 300_000 and "Agenda 00000" in text
    assert text.rstrip().endswith(cal.DESCRIPTION_CUT)


def test_google_calendar_a_lowered_page_moves_the_cut(calendar, lowered):
    long_event(calendar.fake, 600_000)
    lowered(50_000, 120)
    report = calendar.run(CAL_NOW, c5.SA)
    assert (report.saved, report.skipped) == (1, 0), report
    (held,) = calendar.notes()
    assert fitting(held.text) and "Agenda 00000" in held.text
    assert held.text.rstrip().endswith(cal.DESCRIPTION_CUT)


# Granola: a meeting with a long summary, and a transcript with one very long turn


GR_NOW = datetime(2099, 11, 13, 12, 0, tzinfo=UTC)
GR_ON = GR_NOW - 10 * DAY


@pytest.fixture
def granola(tmp_path):
    fake = c3.FakeGranola(c3.Clock(GR_NOW))
    clock = c3.Clock(GR_ON - DAY)
    store = Store(tmp_path / "cairn.db", clock=clock)
    store.create_project("meetings")
    store.add_source("granola", "granola", c3.config(projects=[], transcripts=True))
    clock.now = GR_ON
    store.enable_source("granola", projects=["meetings"])
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))

    def run():
        fake.clock.now = GR_NOW
        fake.new_pull()
        return asyncio.run(
            run_source(store, "granola", http=http, env=c3.ENV, clock=fake.clock, sleep=fake.sleep)
        )

    def notes() -> list:
        return store.list_notes(store.get_project("meetings").id)

    yield SimpleNamespace(store=store, fake=fake, run=run, notes=notes)
    store.close()


def long_meeting(fake, summary_chars: int, turn_chars: int) -> str:
    """A meeting whose summary has `summary_chars` characters and whose transcript is a short
    turn, one of `turn_chars` characters in forty items, and a short one after it; the long
    turn's words, as one line."""
    start = GR_ON + DAY
    said = [c3.segment("Brook Lane", "Shall we start?", start)]
    words = filler(turn_chars, "Ledger", paragraph=turn_chars // 40).split("\n\n")
    said += [c3.segment("Ada Park", w, start + (k + 1) * MINUTE) for k, w in enumerate(words)]
    said.append(c3.segment("Brook Lane", "Thanks, that is all.", start + HOUR))
    summary = filler(summary_chars, "Summary")
    fake.note(1, "Ledger review", created=start, segments=said, summary_markdown=summary)
    return " ".join(words)


def by_part(notes: list, under: str) -> list[str]:
    """The texts of the notes whose source_ref holds `under`, in the order of their parts
    ("<ref>", "<ref>/2", "<ref>/3", ...)."""

    def number(ref: str) -> int:
        last = ref.rsplit("/", 1)[-1]
        return int(last) if last.isdigit() else 1

    found = [note for note in notes if under in note.source_ref]
    return [note.text for note in sorted(found, key=lambda note: number(note.source_ref))]


def spoken(texts: list[str], who: str) -> list[str]:
    """What `who` said, line by line, across the texts in order."""
    lead = f"{who}: "
    return [line.split(lead, 1)[1] for text in texts for line in text.splitlines() if lead in line]


def test_granola_the_largest_notes_are_saved_the_summary_cut_and_the_transcript_whole(granola):
    turn = long_meeting(granola.fake, 600_000, 900_000)
    report = granola.run()
    assert report.skipped == 0 and report.saved >= 4, report
    held = granola.notes()
    assert all(fitting(note.text) for note in held)
    (summary,) = [note.text for note in held if note.source_ref.endswith(c3.nid(1))]
    assert "Summary 00000" in summary and summary.rstrip().endswith(gr.SUMMARY_CUT)
    parts = by_part(held, "/transcript")
    lines = [line for text in parts for line in text.splitlines() if line.startswith("- ")]
    assert lines[0].endswith("Brook Lane: Shall we start?")
    assert lines[-1].endswith("Brook Lane: Thanks, that is all.")
    long_lines = spoken(parts, "Ada Park")
    assert len(long_lines) >= 3 and " ".join(long_lines).split() == turn.split()


def test_granola_a_lowered_page_moves_the_cut_and_the_parts(granola, lowered):
    turn = long_meeting(granola.fake, 60_000, 90_000)
    lowered(20_000, 120)
    report = granola.run()
    assert report.skipped == 0 and report.saved >= 6, report
    held = granola.notes()
    assert all(fitting(note.text) for note in held)
    assert any(note.text.rstrip().endswith(gr.SUMMARY_CUT) for note in held)
    said = spoken(by_part(held, "/transcript"), "Ada Park")
    assert " ".join(said).split() == turn.split()


# Google Meet: a transcript with one very long turn


MEET_ON = datetime(2099, 11, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def meet(tmp_path, keys):
    fake = c4.FakeGoogle(c4.Clock(MEET_ON + DAY), keys)
    made = google_world(tmp_path, c4, fake, "calls", "google_meet", "meetings", MEET_ON, c4.config)
    yield made
    made.store.close()


def long_transcript(fake, turn_chars: int) -> str:
    """A meeting whose transcript is a short turn, one of `turn_chars` characters in forty
    entries, and a short one after it; the long turn's words, as one line."""
    start = MEET_ON + HOUR
    second = timedelta(seconds=1)
    words = filler(turn_chars, "Ledger", paragraph=turn_chars // 40).split("\n\n")
    said = [c4.Entry("p2", "Shall we start?", start)]
    said += [c4.Entry("p1", w, start + (k + 1) * second) for k, w in enumerate(words)]
    said.append(c4.Entry("p2", "Thanks, that is all.", start + HOUR))
    transcript = c4.Transcript(c4.tid(1), start, start + 3 * HOUR, said)
    fake.meeting(1, start, start + 2 * HOUR, transcripts=[transcript])
    return " ".join(words)


def test_google_meet_the_largest_note_is_written_in_parts_that_hold_all_of_it(meet):
    turn = long_transcript(meet.fake, 900_000)
    report = meet.run(MEET_ON + 4 * HOUR, c4.SA)
    assert report.skipped == 0 and report.saved >= 3, report
    held = meet.notes()
    assert all(fitting(note.text) for note in held)
    parts = by_part(held, c4.ref(1))
    assert "- Participants: " in parts[0]  # the facts, in the first part only
    lines = [line for text in parts for line in text.splitlines() if line.startswith("- 0")]
    assert lines[0].endswith("Brook Lane: Shall we start?")
    assert lines[-1].endswith("Brook Lane: Thanks, that is all.")
    said = spoken(parts, "Ada Park")
    assert len(said) >= 3 and " ".join(said).split() == turn.split()


def test_google_meet_a_lowered_page_moves_the_parts(meet, lowered):
    turn = long_transcript(meet.fake, 90_000)
    lowered(20_000, 120)
    report = meet.run(MEET_ON + 4 * HOUR, c4.SA)
    assert report.skipped == 0 and report.saved >= 5, report
    held = meet.notes()
    assert all(fitting(note.text) for note in held)
    said = spoken(by_part(held, c4.ref(1)), "Ada Park")
    assert " ".join(said).split() == turn.split()


# Gmail: a labelled message whose body is long, one paragraph of it longer than a page


GM_ON = datetime(2099, 11, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def gmail(tmp_path, keys):
    fake = c6.FakeGoogle(c6.Clock(GM_ON), keys)
    made = google_world(tmp_path, c6, fake, "mail", "gmail", "mail", GM_ON, c6.config)
    yield made
    made.store.close()


def long_body(chars: int, paragraph: int) -> str:
    """Paragraphs of `chars` characters in all, then one paragraph of `paragraph` characters
    with no blank line in it, then a closing paragraph."""
    one = " ".join(f"Word{k:06d}" for k in range(paragraph // 11 + 1))[:paragraph]
    return f"{filler(chars, 'Opening')}\n\n{one}\n\nClosing paragraph, the last words."


def body_of(parts: list[str]) -> list[str]:
    """The words of a message's body across its parts in order: each part's lines but its
    heading and its facts."""
    lines = [line for text in parts for line in text.splitlines()]
    return " ".join(line for line in lines if not line.startswith(("# ", "- "))).split()


def test_gmail_the_largest_note_is_written_in_parts_that_hold_all_of_it(gmail):
    body = long_body(700_000, 450_000)  # 1,150,000 characters, one passage of 450,000
    gmail.fake.message(1, GM_ON + HOUR, body=body, subject="Ledger export")
    report = gmail.run(GM_ON + 2 * HOUR, c6.SA)
    assert report.skipped == 0 and report.saved >= 3, report
    held = gmail.notes()
    assert all(fitting(note.text) for note in held)
    parts = by_part(held, c6.mid(1))
    assert len(parts) == report.saved and "Opening 00000: the ledger" in parts[0]
    assert parts[-1].rstrip().endswith("Closing paragraph, the last words.")
    assert not any(gm.NOTE_CUT in text for text in parts)
    assert body_of(parts) == body.split()


def test_gmail_a_lowered_page_moves_the_parts(gmail, lowered):
    body = long_body(70_000, 45_000)
    gmail.fake.message(1, GM_ON + HOUR, body=body, subject="Ledger export")
    lowered(20_000, 120)
    report = gmail.run(GM_ON + 2 * HOUR, c6.SA)
    assert report.skipped == 0 and report.saved >= 6, report
    held = gmail.notes()
    assert all(fitting(note.text) for note in held)
    assert body_of(by_part(held, c6.mid(1))) == body.split()


# Google Drive: a document whose text is long, one paragraph of it longer than a page


DR_ON = datetime(2099, 11, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def drive(tmp_path, keys):
    fake = c7.FakeGoogle(c7.Clock(DR_ON), keys)
    made = google_world(tmp_path, c7, fake, "drive", "google_drive", "documents", DR_ON, c7.config)
    yield made
    made.store.close()


def test_google_drive_the_largest_note_is_written_in_parts_that_hold_all_of_it(drive):
    text = long_body(700_000, 450_000)  # 1,150,000 characters, one passage of 450,000
    drive.fake.doc(1, DR_ON + HOUR, text)
    report = drive.run(DR_ON + 2 * DAY, c7.USER)
    assert report.skipped == 0 and report.saved >= 3, report
    held = drive.notes()
    assert all(fitting(note.text) for note in held)
    parts = by_part(held, c7.fid(1))
    assert len(parts) == report.saved and "Opening 00000: the ledger" in parts[0]
    assert parts[-1].rstrip().endswith("Closing paragraph, the last words.")
    assert not any(gd.NOTE_CUT in part for part in parts)
    assert body_of(parts) == text.split()


def test_google_drive_a_lowered_page_moves_the_parts(drive, lowered):
    text = long_body(70_000, 45_000)
    drive.fake.doc(1, DR_ON + HOUR, text)
    lowered(20_000, 120)
    report = drive.run(DR_ON + 2 * DAY, c7.USER)
    assert report.skipped == 0 and report.saved >= 6, report
    held = drive.notes()
    assert all(fitting(note.text) for note in held)
    assert body_of(by_part(held, c7.fid(1))) == text.split()


# A connector's item: not cut, but skipped with a reason of its own when a page cannot hold it


@pytest.fixture
def erp(tmp_path):
    clock = c9.Clock(c9.SWITCH_ON - DAY)
    store = Store(tmp_path / "cairn.db", clock=clock)
    store.create_project("invoices")
    connector = c9.Connector()
    router = c9.Router(connector)
    http = httpx.AsyncClient(transport=httpx.MockTransport(router.handle))

    async def sleep(seconds: float) -> None:
        return None

    world = SimpleNamespace(
        store=store, clock=clock, connector=connector, router=router, http=http, sleep=sleep
    )
    c9.add(world)
    c9.switch_on(world)
    yield world
    store.close()


def items_past_a_page(chars: int, claims: int) -> list[dict]:
    """A short item, one whose text is one character past `chars`, one whose text is
    `claims` + 1 short list items, one at both bounds exactly, and a short one."""
    head = "# Long invoice\n\n"
    long = head + "x" * (chars + 1)  # its one passage holds chars + 1
    many = "\n".join(f"- line {k}" for k in range(claims + 1)) + "\n"
    at = "x" * chars  # one passage of chars, the bound itself
    return [
        c9.item(1),
        c9.item(2, text=long),
        c9.item(3, text=many),
        c9.item(4, text=at),
        c9.item(5),
    ]


def test_a_connector_s_item_past_a_page_is_skipped_with_a_reason_of_its_own(erp):
    feed = items_past_a_page(PAGE_CHARS_MAX, PAGE_CLAIMS_MAX)
    assert fit.measure(feed[1]["text"])[1] == PAGE_CHARS_MAX + 1
    assert fit.measure(feed[2]["text"])[0] == PAGE_CLAIMS_MAX + 1
    erp.connector.feed = feed
    report = c9.run(erp, c9.SWITCH_ON + HOUR)
    assert (report.saved, report.skipped) == (3, 2), report
    assert report.reasons == {conn.too_long(): 2}
    said = conn.too_long()
    assert "400,000 characters" in said and "2,000 passages" in said
    assert "full" not in said and "page full" not in report.reasons
    assert [note.text for note in c9.notes(erp)] == [
        feed[0]["text"],
        feed[3]["text"],
        feed[4]["text"],
    ]


def test_a_lowered_page_moves_the_bound_a_connector_s_item_is_held_to(erp, lowered):
    lowered(20_000, 120)
    feed = items_past_a_page(20_000, 120)
    erp.connector.feed = feed
    report = c9.run(erp, c9.SWITCH_ON + HOUR)
    assert (report.saved, report.skipped) == (3, 2), report
    assert report.reasons == {conn.too_long(): 2}
    assert "20,000 characters" in conn.too_long() and "120 passages" in conn.too_long()


def test_connector_check_holds_an_item_to_a_page_under_every_item_within_its_bounds(
    tmp_path, monkeypatch, capsys
):
    connector = c9.Connector()
    connector.feed = items_past_a_page(PAGE_CHARS_MAX, PAGE_CLAIMS_MAX)[:3]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(c9.SECRET_ENV, c9.SECRET)
    # The stand-in connector is on loopback, which an operator has to allow.
    monkeypatch.setenv("CAIRN_SOURCE_PRIVATE_ADDRESSES", "allow")
    with c9.Loopback(connector):
        argv = ["connector-check", "--url", connector.url, "--secret-env", c9.SECRET_ENV]
        code = c9.call([*argv, "--since", c9.CHECK_SINCE, "--limit", "3"])
    said = capsys.readouterr().out
    assert code == 1, said
    (failed,) = [line for line in said.splitlines() if "FAILED" in line]
    assert "every item within its bounds" in failed and "'INV-2'" in failed
    assert conn.too_long() in failed


# Facts: a note past its facts is still skipped, not cut


FACTS_PAST = "facts past their bound"


def counts(n: int) -> str:
    """`n` numbers on one line: one passage of about 6n characters, n facts."""
    return "Counts " + " ".join(str(10_000 + i) for i in range(n))


def test_github_a_note_past_its_facts_is_skipped_for_its_facts(github):
    github.fake.pull_request(
        1, "A table of counts", body=counts(FACTS_MAX + 1), merged=datetime(2099, 3, 2, tzinfo=UTC)
    )
    report = c11.run(github.world, github.fake, c11.NOW)
    assert (report.saved, report.skipped, report.reasons) == (0, 1, {FACTS_PAST: 1}), report
    assert c11.notes(github.world) == []


def test_google_drive_a_note_past_its_facts_is_skipped_for_its_facts(drive):
    drive.fake.doc(1, DR_ON + HOUR, counts(FACTS_MAX + 1) + "\n")
    report = drive.run(DR_ON + 2 * DAY, c7.USER)
    assert (report.saved, report.skipped, report.reasons) == (0, 1, {FACTS_PAST: 1}), report
    assert drive.notes() == []


# The claims bound alone: many short passages, far inside a page's characters


def points(count: int, mark: str = "Point") -> str:
    """`count` short paragraphs, one line each."""
    return "\n\n".join(f"{mark} {k:04d} of the plan is agreed." for k in range(count))


def test_google_calendar_a_description_past_a_page_s_claims_is_cut_to_them(calendar):
    start = CAL_NOW - 5 * HOUR
    calendar.fake.event(1, start, start + HOUR, summary="Plan", description=points(2_500))
    report = calendar.run(CAL_NOW, c5.SA)
    assert (report.saved, report.skipped) == (1, 0), report
    (held,) = calendar.notes()
    assert fitting(held.text) and fit.measure(held.text)[0] > 1_900
    assert "Point 0000 of the plan" in held.text
    assert held.text.rstrip().endswith(cal.DESCRIPTION_CUT)


def test_granola_a_summary_past_a_page_s_claims_is_cut_to_them(granola):
    start = GR_ON + DAY
    said = [c3.segment("Brook Lane", "Shall we start?", start)]
    granola.fake.note(1, "Plan", created=start, segments=said, summary_markdown=points(2_500))
    report = granola.run()
    assert report.skipped == 0, report
    (summary,) = [n.text for n in granola.notes() if n.source_ref.endswith(c3.nid(1))]
    assert fitting(summary) and fit.measure(summary)[0] > 1_900
    assert "Point 0000 of the plan" in summary and summary.rstrip().endswith(gr.SUMMARY_CUT)


def test_github_a_lowered_page_s_claims_alone_move_the_cut(github, lowered):
    github.fake.pull_request(
        1, "Many points", body=points(200), merged=datetime(2099, 3, 2, tzinfo=UTC)
    )
    lowered(PAGE_CHARS_MAX, 40)
    report = c11.run(github.world, github.fake, c11.NOW)
    assert (report.saved, report.skipped) == (1, 0), report
    (held,) = c11.notes(github.world)
    assert fitting(held.text) and fit.measure(held.text)[0] <= 40
    assert "Point 0000 of the plan" in held.text and gh.CUT_LINES["pull"] in held.text


def test_gmail_a_lowered_page_s_claims_alone_move_the_parts(gmail, lowered):
    body = points(200)
    gmail.fake.message(1, GM_ON + HOUR, body=body, subject="Many points")
    lowered(PAGE_CHARS_MAX, 40)
    report = gmail.run(GM_ON + 2 * HOUR, c6.SA)
    assert report.skipped == 0 and report.saved >= 5, report
    held = gmail.notes()
    assert all(fit.measure(note.text)[0] <= 40 for note in held)
    assert body_of(by_part(held, c6.mid(1))) == body.split()


def test_google_drive_a_lowered_page_s_claims_alone_move_the_parts(drive, lowered):
    text = points(200) + "\n"
    drive.fake.doc(1, DR_ON + HOUR, text)
    lowered(PAGE_CHARS_MAX, 40)
    report = drive.run(DR_ON + 2 * DAY, c7.USER)
    assert report.skipped == 0 and report.saved >= 5, report
    held = drive.notes()
    assert all(fit.measure(note.text)[0] <= 40 for note in held)
    assert body_of(by_part(held, c7.fid(1))) == text.split()
