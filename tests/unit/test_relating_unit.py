"""Related pages as the store reads them (Store.related_pages, cairn.store.relating): what
relates two pages, what does not, the evidence each relation carries, and that the read is
one project's, in one snapshot. Real stores in temporary files, notes filed through
Memory.note as `serve` files them; made-up names."""

import json
import sqlite3
import threading
import unicodedata
from contextlib import closing

import pytest

from cairn import relations
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.relations import CLAIMS_COMMON, COMMON, LINE_MAX, MENTIONED_BY, SHARES, TEXT_MAX
from cairn.store import Claim, Store, ranking, relating
from earlier_build import earlier_page


@pytest.fixture
def store(tmp_path):
    with closing(Store(tmp_path / "cairn.db")) as made:
        yield made


@pytest.fixture
def ops(store):
    return store.create_project("ops")


def file(store: Store, project: str, title: str, text: str) -> str:
    """A note filed on the page of its title; the page's slug."""
    answer = Memory(store, lint_gate).note(project, text, title=title, topic=title)
    assert answer["status"] in ("saved", "unchanged"), answer
    return answer["page"]


def related(store: Store, project_id: str, slug: str, limit: int = 20):
    found = store.related_pages(project_id, slug, limit)
    assert found is not None
    return found


NOTHING_LEFT_OUT = relating.Left(
    terms_looked_up=0, terms_not_looked_up=0, claims_read=0, claims_not_read=0, pages_left_out=0
)


def reasons(found) -> dict[str, list[tuple]]:
    """slug -> (kind, term, term kind, pages) of each reason given."""
    return {
        page.slug: [(r.kind, r.term, r.term_kind, r.pages) for r in page.why]
        for page in found.pages
    }


# The match: ranking's own way


def test_the_index_is_given_the_text_itself_as_one_phrase_and_reads_its_words_alone(store, ops):
    """What the index is asked for a term or a title: the text as it is, as one quoted phrase
    on the text column. Nothing here splits it into words; the index's own reading of the
    phrase decides them, as it decided the words of the claims. Only what a query cannot
    write as it is is changed: a double quote is doubled, and a null character, where a
    query ends, is a space (the index reads a null as a separator, as it reads a space)."""
    assert ranking.quoted("Deploy runbook") == 'text : "Deploy runbook"'
    assert ranking.quoted("os_login_2fa") == 'text : "os_login_2fa"'
    assert ranking.quoted('say "hi" now') == 'text : "say ""hi"" now"'
    assert ranking.quoted("a1\x00b2") == 'text : "a1 b2"'
    decomposed = unicodedata.normalize("NFD", "R\u00e9sum\u00e9.cfg")
    assert ranking.quoted(decomposed) == f'text : "{decomposed}"'  # not "Re sume cfg"
    assert ranking.quoted("---") == 'text : "---"' and not hasattr(ranking, "phrase")
    store.save_page(
        ops.id,
        "deploy",
        "Deploy runbook",
        "",
        [
            Claim(id="cl_a", text="Quill reads the deploy runbook twice.", span_ids=()),
            Claim(id="cl_b", text="The runbook says when to deploy.", span_ids=()),
            Claim(
                id="cl_c", text="Set os_login_2fa before the Deploy; Runbook follows.", span_ids=()
            ),
            Claim(id="cl_d", text='Then say "hi" now, and NEAR(a b) or text : x.', span_ids=()),
        ],
    )

    def held(text: str) -> list[str]:
        with store._read() as con:
            rows = con.execute(
                f"SELECT claim_id FROM {store._index} WHERE {store._index} MATCH ? ORDER BY rowid",
                (ranking.quoted(text),),
            )
            return [row[0] for row in rows]

    # every row holds the title "Deploy runbook"; only the text decides, and punctuation between
    # two words is no word (the index's own reading, as a search's)
    assert held("deploy runbook") == held("Deploy; RUNBOOK") == ["cl_a", "cl_c"]
    assert held("os login 2fa") == held("os_login_2fa") == ["cl_c"]  # the index splits at "_"
    assert held("runbook deploy") == []
    # nothing of a text is read as a query: quotes, a column, an operator are words or none
    assert held('say "hi" now') == held("NEAR(a b)") == held("text : x") == ["cl_d"]
    assert held("---") == held("") == held('"') == []  # no word: nothing matches, and no error


# What relates two pages


def test_two_pages_that_state_one_term_are_related_each_with_its_claim(store, ops):
    deploy = file(store, "ops", "Deploy", "Run the job with `--audit-keep` set to 7.\n")
    audit = file(
        store, "ops", "Retention", "Ada keeps old events.\n\n`--audit-keep` says how many.\n"
    )
    file(store, "ops", "Queue", "Brook drains the queue at noon.\n")

    found = related(store, ops.id, deploy)
    assert (found.slug, found.title, found.version) == (deploy, "Deploy", 1)
    (page,) = found.pages
    assert (page.slug, page.title, page.version, page.more) == (audit, "Retention", 1, 0)
    (reason,) = page.why
    assert (reason.kind, reason.term, reason.term_kind, reason.pages) == (
        SHARES,
        "--audit-keep",
        "inline_code",
        2,
    )
    assert reason.here.text == "Run the job with `--audit-keep` set to 7."
    assert reason.there.text == "`--audit-keep` says how many."
    latest = {c.id: c for c in store.get_page(ops.id, audit).claims}
    assert reason.there.span_ids == latest[reason.there.claim_id].span_ids
    assert found.left.complete and found.left == relating.Left(
        terms_looked_up=1,
        terms_not_looked_up=0,
        claims_read=1,
        claims_not_read=0,
        pages_left_out=0,
    )
    # and the other way round, by the same term
    back = related(store, ops.id, audit)
    assert reasons(back) == {deploy: [(SHARES, "--audit-keep", "inline_code", 2)]}
    assert (back.pages[0].why[0].here.text, back.pages[0].why[0].there.text) == (
        reason.there.text,
        reason.here.text,
    )


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("`-L`", "`-l`"),  # a local forward, a login name
        ("-L", "-l"),
        ("/srv/Quill/notes", "/srv/quill/notes"),  # a path
        ("`Make lint`", "`make lint`"),  # code
        ("https://Fable.example/runbook", "https://fable.example/runbook"),  # an address's host
        ("https://fable.example/Runbook", "https://fable.example/runbook"),  # and its path
    ],
)
def test_terms_that_differ_only_by_a_capital_are_two_terms(store, ops, one, other):
    """The index proposes each page's claim to the other, reading without regard to case;
    the passages say they are two terms, and neither page is said to state the other's.
    One rule for every kind, an address's host included: exact."""
    first = file(store, "ops", "First", f"Ada uses {one} here.\n")
    second = file(store, "ops", "Second", f"Brook uses {other} there.\n")
    for slug in (first, second):
        found = related(store, ops.id, slug)
        assert found.pages == () and found.left.complete and found.left.claims_read == 1
    third = file(store, "ops", "Third", f"Quill uses {one} too.\n")
    written = one.strip("`")
    for slug, with_it in ((first, third), (third, first)):
        (page,) = related(store, ops.id, slug).pages
        (reason,) = page.why
        assert (page.slug, reason.term, reason.pages) == (with_it, written, 2)  # as it is read
        assert written in reason.here.text and written in reason.there.text
    assert related(store, ops.id, second).pages == ()


def test_the_same_text_of_another_kind_is_another_term(store, ops):
    """A flag in prose and the same letters in backticks are two terms: equal by kind and by
    what was read. The index finds both claims; the passage decides."""
    bare = file(store, "ops", "Bare", "Start it with --dry-run first.\n")
    coded = file(store, "ops", "Coded", "Start it with `--dry-run` first.\n")
    prose = file(store, "ops", "Prose", "A dry run costs nothing.\n")
    twin = file(store, "ops", "Twin", "Brook passes --dry-run to the job.\n")
    assert reasons(related(store, ops.id, bare)) == {twin: [(SHARES, "--dry-run", "flag", 2)]}
    assert reasons(related(store, ops.id, coded)) == {}
    assert reasons(related(store, ops.id, prose)) == {}


def test_a_plain_word_in_backticks_a_number_and_a_date_relate_nothing(store, ops):
    one = file(store, "ops", "One", "Set `name` to `true` on 2026-03-14 for 5 min, 2,700 times.\n")
    file(
        store, "ops", "Two", "Set `name` to `true` on 2026-03-14 for 5 min, 2,700 times.\n\nMore.\n"
    )
    found = related(store, ops.id, one)
    assert found.pages == () and found.left.complete and found.left.terms_looked_up == 0


def test_a_term_more_than_common_pages_hold_relates_none(store, ops):
    slugs = [
        file(store, "ops", f"Page {n}", f"Page {n} sets `os_login_2fa`.\n")
        for n in range(COMMON + 1)
    ]
    assert COMMON == 5
    found = related(store, ops.id, slugs[0])
    assert found.pages == () and found.left.complete  # six pages hold it: one too many
    store.forget_note(store.list_notes(ops.id)[-1].id)  # five hold it now
    found = related(store, ops.id, slugs[0])
    assert reasons(found) == {
        slug: [(SHARES, "os_login_2fa", "inline_code", COMMON)] for slug in slugs[1:COMMON]
    }


def test_pages_are_given_heaviest_first_and_the_rest_are_counted(store, ops):
    asked = file(store, "ops", "Asked", "Use `a.toml`, `b.toml` and `c.toml`.\n")
    two = file(store, "ops", "Two", "Use `a.toml` and `b.toml`.\n")
    one = file(store, "ops", "One", "Use `c.toml`.\n")
    also = file(store, "ops", "Also", "Use `a.toml`.\n")
    found = related(store, ops.id, asked)
    # a.toml is held by three pages (1/2 each), b.toml and c.toml by two (1 each)
    assert [page.slug for page in found.pages] == [two, one, also]
    first = related(store, ops.id, asked, limit=1)
    assert [page.slug for page in first.pages] == [two]
    assert first.left.pages_left_out == 2 and not first.left.complete


# A mention: the index's own match of the title, in a claim's text


def test_a_page_whose_claim_holds_this_pages_title_mentions_it(store, ops):
    runbook = file(store, "ops", "Deploy runbook", "Ada wrote it.\n")
    notes = file(store, "ops", "Notes", "See the deploy runbook first.\n\nThen ask Brook.\n")
    found = related(store, ops.id, runbook)
    (page,) = found.pages
    (reason,) = page.why
    assert (page.slug, reason.kind, reason.term, reason.term_kind) == (
        notes,
        MENTIONED_BY,
        "Deploy runbook",
        "title",
    )
    assert reason.here is None and reason.there.text == "See the deploy runbook first."
    # this version lists who mentions a page, not whom the page mentions
    assert related(store, ops.id, notes).pages == ()


def test_titles_that_share_words_relate_nothing_and_a_page_does_not_mention_itself(store, ops):
    """The index holds each claim's page title beside its text: a match there is no mention."""
    quill = file(store, "ops", "Quill", "Quill keeps the nightly log.\n")
    other = file(store, "ops", "Quill notes", "Ada reads them.\n")
    assert related(store, ops.id, quill).pages == ()  # "Quill" is in the other page's title only
    assert related(store, ops.id, other).pages == ()
    assert related(store, ops.id, quill).left.complete


# Reach, and what there is not


def test_a_page_that_is_not_there_and_a_project_out_of_reach_answer_nothing(store, ops):
    deploy = file(store, "ops", "Deploy", "Run `--audit-keep`.\n")
    assert store.related_pages(ops.id, "nowhere") is None
    assert store.related_pages("br_000000000000", deploy) is None
    other = store.create_project("other")
    seen: list[str] = []
    store._observe(trace=seen.append)
    try:
        assert store.narrow(projects=(other.id,)).related_pages(ops.id, deploy) is None
    finally:
        store._observe(trace=None)
    # out of reach: its reach and the page's head are asked, and no claim, passage or index
    assert not [s for s in seen if "claim_search" in s or "spans" in s or "search_claims" in s]
    assert sum("page_heads" in s for s in seen) == 1
    assert store.narrow(projects=(ops.id,), access="read").related_pages(ops.id, deploy).pages == ()


def claims_read(store: Store, project_id: str, slug: str) -> tuple[object, list[str]]:
    """The answer, and the statements FTS5 ran to read a claim's row (it reads one row with one
    statement, or scans a range of them), as tests/unit/test_search_unit.py counts them."""
    read: list[str] = []
    store._observe(trace=lambda s: read.append(s) if "_content'" in s else None)
    try:
        return related(store, project_id, slug), read
    finally:
        store._observe(trace=None)


def test_another_project_with_the_same_term_and_title_never_appears_and_is_never_read(store, ops):
    deploy = file(store, "ops", "Deploy", "Run `--audit-keep` at night.\n")
    file(store, "ops", "Retention", "`--audit-keep` says how many.\n")
    alone, read_alone = claims_read(store, ops.id, deploy)
    journal = store.create_project("journal")
    for n in range(30):  # the same term, and this page's title, on thirty pages out of the project
        file(store, "journal", f"Diary {n}", f"Day {n}: `--audit-keep`, as Deploy says.\n")
        file(store, "journal", f"Deploy {n}", f"Run `--audit-keep` on night {n}.\n")
    found, read = claims_read(store, ops.id, deploy)
    assert found == alone and reasons(found) == {
        "retention": [(SHARES, "--audit-keep", "inline_code", 2)]
    }
    assert [s for s in read if not s.endswith("WHERE T.'rowid'=?")] == []  # no scan of claims
    # A claim is read by its row, and each read is one or two such statements: the page's own
    # claim, the one candidate, and the two claims given. Sixty more claims that match, out of
    # the project, add none.
    assert read == read_alone and 0 < len(read) <= 2 * 4
    theirs = store.narrow(projects=(journal.id,)).find("audit keep", limit=50)
    assert len(theirs.hits) == 50  # they are there, and the index matches them
    # and a key that reaches both projects is answered for the one it asked about
    both = store.narrow(projects=(ops.id, journal.id))
    assert both.related_pages(ops.id, deploy, 20) == alone


# A title is named in a claim's prose: not inside code, a path, a flag, a version or an address


def named_by(store: Store, ops, slug: str) -> list[str]:
    found = related(store, ops.id, slug)
    return [page.slug for page in found.pages if page.why[0].kind == MENTIONED_BY]


def test_the_places_the_index_marked_are_read_in_the_texts_own_characters():
    opened, closed = relating.MARKS
    marked = f"ab{opened}cd{closed}e{opened}f{closed}"
    assert relating._places(marked) == [(2, 4), (5, 6)]
    assert relating._places("no mark at all") == []
    # marks that do not pair say nothing more: the read never asks for such a text, and
    # reading one ends all the same
    assert relating._places(f"a{opened}b") == []
    assert relating._places(f"a{opened}b{closed}c{opened}d") == [(1, 2)]
    text = "Quill ledger, then the QUILL\u2014ledger."
    marked = f"{opened}Quill ledger{closed}, then the {opened}QUILL\u2014ledger{closed}."
    assert [text[slice(*place)] for place in relating._places(marked)] == [
        "Quill ledger",
        "QUILL\u2014ledger",
    ]


def test_a_title_inside_a_path_in_backticks_is_not_named(store, ops):
    """The case from the pages in a browser: the claim's only "deploy" is inside a path."""
    deploy = file(store, "ops", "Deploy", "Ada ships on Fridays.\n")
    file(
        store,
        "ops",
        "Replica",
        "The replica is configured in `litestream.yml` and started by `deploy/compose.yaml`.\n",
    )
    found = related(store, ops.id, deploy)
    assert found.pages == () and found.left.complete
    assert found.left.claims_read == 1  # the claim was read, with its passage, to say so


def test_a_one_word_title_inside_a_flag_is_not_named(store, ops):
    """The index reads `--audit-keep` as the words audit and keep, as a search does, and
    lists the claim; the passage says the words stand inside a flag, in backticks or bare."""
    audit = file(store, "ops", "Audit", "Ada reads the log.\n")
    file(store, "ops", "Deploy", "Run the job with `--audit-keep` set to 7.\n")
    file(store, "ops", "Nightly", "The job runs with --audit-keep 7 at night.\n")
    assert named_by(store, ops, audit) == []
    review = file(store, "ops", "Review", "The audit of March found `--audit-keep` unset.\n")
    assert named_by(store, ops, audit) == [review]  # in prose too, in this claim


@pytest.mark.parametrize(
    "inside",
    [
        "See https://fable.example/deploy/guide first.",  # an address
        "Start it with ./deploy/run.sh at noon.",  # a path with a root
        "It lives under /srv/deploy/current now.",
        "Run `deploy` and wait.",  # a plain word in backticks: no term, and still code
        "Pass --deploy to ship.",  # a flag
        "Then:\n\n```sh\nquill deploy --now\n```",  # a code block
        "Then:\n\n```deploy\nquill run\n```",  # the word after a fence's opening marks
        "> ~~~deploy\n> quill run\n> ~~~",  # and in a quote
    ],
)
def test_a_title_inside_an_address_a_path_a_flag_or_code_is_not_named(store, ops, inside):
    deploy = file(store, "ops", "Deploy", "Ada ships on Fridays.\n")
    file(store, "ops", "Other", inside + "\n")
    assert named_by(store, ops, deploy) == []
    file(store, "ops", "Prose", "Before a deploy, read this. " + inside + "\n")
    assert named_by(store, ops, deploy) == ["prose"]


@pytest.mark.parametrize(
    "said",
    [
        "Started by deploy/compose.yaml at dawn.",  # a path written with no root
        "Write to ada@deploy.dev at dawn.",  # an email address
        "Brook edits C:\\Deploy\\notes.txt daily.",  # a Windows path
        "Brook opens deploy.example.com daily.",  # a host with no scheme
        "Brook runs it:\n\n    deploy --now\n\nThen rests.",  # an indented block
    ],
)
def test_what_the_facts_do_not_own_is_prose_and_names_the_title(store, ops, said):
    """Pinned as it is: what the passage's facts do not read as a url, a path, a flag, a
    version, inline code or a fenced block is prose (cairn.facts reads an indented block as
    lines of prose too)."""
    deploy = file(store, "ops", "Deploy", "Ada ships on Fridays.\n")
    other = file(store, "ops", "Other", said + "\n")
    assert named_by(store, ops, deploy) == [other]


def test_a_title_that_reads_as_code_is_named_in_prose_and_not_in_backticks(store, ops):
    compose = file(store, "ops", "compose.yaml", "Brook keeps it short.\n")
    prose = file(store, "ops", "Prose", "Edit compose.yaml before the release.\n")
    file(store, "ops", "Ticks", "Edit `compose.yaml` before the release.\n")
    file(store, "ops", "Path", "It is `deploy/compose.yaml`, read at start.\n")
    both = file(store, "ops", "Both", "Run `compose.yaml` checks; compose.yaml must parse.\n")
    assert named_by(store, ops, compose) == [both, prose]


def test_a_title_of_two_words_with_one_in_code_is_not_named(store, ops):
    """The index matches the two words side by side whatever stands between them; a match
    that lies partly inside code is inside it."""
    ledger = file(store, "ops", "Quill ledger", "Brook balances it at dusk.\n")
    file(store, "ops", "First", "Run `quill` ledger checks at noon.\n")
    file(store, "ops", "Second", "Open quill `ledger` and wait.\n")
    file(store, "ops", "Across", "Open quill `--x` ledger and wait.\n")  # code between: no match
    assert named_by(store, ops, ledger) == []
    whole = file(store, "ops", "Whole", "Run `quill` ledger checks, then the Quill ledger.\n")
    assert named_by(store, ops, ledger) == [whole]


def test_a_claim_reworded_from_its_passage_mentions_as_the_index_says(store, ops):
    """Where a claim's text is not the text of the first passage it cites (a page saved by
    hand), the places of the one are not the places of the other: the mention stands as the
    index has it. So does that of a claim that cites no passage, or another project's."""
    deploy = file(store, "ops", "Deploy", "Ada ships on Fridays.\n")
    note, _ = store.add_note(ops.id, "# Replica\n\nStarted by `deploy/compose.yaml` at dawn.\n")
    (span,) = [s.id for s in store.note_spans(note.id) if s.kind == "paragraph"]
    said = "The replica is started by `deploy/compose.yaml`."
    store.save_page(
        ops.id, "reworded", "Reworded", "", [Claim(id="cl_r", text=said, span_ids=[span])]
    )
    store.save_page(ops.id, "uncited", "Uncited", "", [Claim(id="cl_u", text=said, span_ids=())])
    other = store.create_project("other")
    far, _ = store.add_note(other.id, "# Far\n\n" + said + "\n")
    (theirs,) = [s.id for s in store.note_spans(far.id) if s.kind == "paragraph"]
    earlier_page(
        store, ops.id, "outside", "Outside", "", [Claim(id="cl_o", text=said, span_ids=[theirs])]
    )
    assert named_by(store, ops, deploy) == ["outside", "reworded", "uncited"]
    # and a long one of them is given by its head: no place of it is known to be prose, so
    # there is no line to give (here the title's only place is the path)
    long = "Ada waits for the nightly log. " * 30 + said
    store.save_page(
        ops.id, "reworded", "Reworded", "", [Claim(id="cl_r", text=long, span_ids=[span])]
    )
    (there,) = [
        page.why[0].there
        for page in related(store, ops.id, deploy).pages
        if page.slug == "reworded"
    ]
    assert (there.text, there.cut, there.line, there.line_cut) == (
        long[:TEXT_MAX],
        True,
        None,
        False,
    )
    # the same words as the text of its own passage: not named
    store.save_page(
        ops.id,
        "same",
        "Same",
        "",
        [Claim(id="cl_s", text="Started by `deploy/compose.yaml` at dawn.", span_ids=[span])],
    )
    assert named_by(store, ops, deploy) == ["outside", "reworded", "uncited"]


def test_a_cut_claim_gives_the_line_of_the_place_that_names_the_title(store, ops):
    """Not the first place the index matched, which stands in a path: the first in prose."""
    lines = (
        ["Started by `deploy/compose.yaml` at dawn."]
        + [f"Ada waits for nightly log {n}." for n in range(60)]
        + ["After each deploy she rests."]
        + [f"Brook rests {n}." for n in range(20)]
        + ["A second deploy follows."]
    )
    whole = "\n".join(lines)
    there = mention(store, ops, whole, title="Deploy")
    assert (there.cut, there.line, there.line_cut) == (True, "After each deploy she rests.", False)


def test_a_title_six_pages_hold_in_code_and_three_name_is_not_common(store, ops):
    """The pages that count toward a common title are those that name it. (The claims that
    count toward CLAIMS_COMMON are the index's: every claim that holds its words.)"""
    deploy = file(store, "ops", "Deploy", "Ada ships on Fridays.\n")
    for n in range(6):
        file(store, "ops", f"Code {n}", f"Page {n} runs `deploy --now`.\n")
    for n in range(3):
        file(store, "ops", f"Prose {n}", f"Page {n} waits for a deploy.\n")
    found = related(store, ops.id, deploy)
    assert named_by(store, ops, deploy) == ["prose-0", "prose-1", "prose-2"]
    assert found.left.complete and found.left.claims_read == 9
    for n in range(3, 6):
        file(store, "ops", f"Prose {n}", f"Page {n} waits for a deploy.\n")
    assert named_by(store, ops, deploy) == []  # six name it now: common


def test_a_title_whose_claims_were_not_all_read_mentions_none_and_counts_them(
    store, ops, monkeypatch
):
    """As a term: a page that may name the title or not leaves it unsettled, and the title
    gives no reason; the claims not read are counted and the answer is not complete."""
    deploy = file(store, "ops", "Deploy", "Ada ships on Fridays.\n")
    file(store, "ops", "Near", "Brook waits for a deploy.\n")
    file(store, "ops", "Far", "Quill waits for a deploy, " + "and waits, " * 60 + "then rests.\n")
    assert named_by(store, ops, deploy) == ["far", "near"]
    monkeypatch.setattr(relating, "BYTES_MAX", 300)  # the long passage does not fit
    found = related(store, ops.id, deploy)
    assert found.pages == () and not found.left.complete
    assert (found.left.claims_read, found.left.claims_not_read) == (1, 1)
    monkeypatch.setattr(relating, "CLAIMS_MAX", 1)  # nor do two claims
    found = related(store, ops.id, deploy)
    assert found.pages == () and (found.left.claims_read, found.left.claims_not_read) == (0, 2)


def test_an_account_with_no_page_answers_nothing(tmp_path):
    with closing(Store(tmp_path / "empty.db")) as store:
        project = store.create_project("ops")
        assert store.related_pages(project.id, "deploy") is None


def test_a_claim_that_cites_no_passage_states_no_term_and_can_still_mention(store, ops):
    store.save_page(
        ops.id, "bare", "Bare", "", [Claim(id="cl_a", text="Use `a.toml`; see Quill.", span_ids=())]
    )
    quill = file(store, "ops", "Quill", "Use `a.toml` too.\n")
    assert related(store, ops.id, "bare").pages == ()
    assert reasons(related(store, ops.id, quill)) == {"bare": [(MENTIONED_BY, "Quill", "title", 2)]}


def test_the_answer_names_the_note_of_every_passage_its_claims_cite(store, ops):
    deploy = file(store, "ops", "Deploy", "Run `--audit-keep`.\n")
    audit = file(store, "ops", "Audit", "`--audit-keep` says how many.\n")
    found = related(store, ops.id, deploy)
    (reason,) = found.pages[0].why
    cited = {*reason.here.span_ids, *reason.there.span_ids}
    notes = {note.id for note in store.list_notes(ops.id)}
    assert set(found.notes_of) == cited and set(found.notes_of.values()) == notes
    assert set(found.origins) == notes and audit
    assert json.dumps(found.left._asdict())  # counts only


# One moment


def test_a_write_in_the_middle_of_a_read_changes_nothing_of_its_answer(tmp_path, monkeypatch):
    """Another connection writes between the read of the page and the read of the other pages'
    claims: it takes the term off the page the relation rests on, and files a note that
    states the term on a new page. The answer is the one of the moment the read began, whole:
    the page, the other page's claim, how many pages hold the term and who wrote the notes.
    The next read is the one after, whole."""
    db = tmp_path / "cairn.db"
    with closing(Store(db)) as store, closing(Store(db)) as other:
        ops = store.create_project("ops")
        deploy = file(store, "ops", "Deploy", "Run `--audit-keep` at night.\n")
        file(store, "ops", "Retention", "`--audit-keep` says how many; see Deploy.\n")
        before = related(store, ops.id, deploy)
        assert reasons(before) == {
            "retention": [
                (MENTIONED_BY, "Deploy", "title", 2),
                (SHARES, "--audit-keep", "inline_code", 2),
            ]
        }
        assert set(before.origins) == {note.id for note in store.list_notes(ops.id)}

        listed, wrote = relating._listed, []

        def written_meanwhile(*args, **named):
            if not wrote:  # once: after the page was read, before any other page's claim is
                wrote.append(None)
                bare = [Claim(id="cl_bare", text="Ada keeps old events.", span_ids=())]
                other.save_page(ops.id, "retention", "Retention", "", bare)
                file(other, "ops", "Newcomer", "Brook sets `--audit-keep` too.\n")
                wrote[0] = reasons(related(other, ops.id, deploy))  # a read that begins now
            return listed(*args, **named)

        monkeypatch.setattr(relating, "_listed", written_meanwhile)
        during = related(store, ops.id, deploy)
        later = {"newcomer": [(SHARES, "--audit-keep", "inline_code", 2)]}
        assert wrote == [later]  # the writes had landed while the read was under way
        assert during == before  # and it holds nothing of them: no claim, count or writer
        after = related(store, ops.id, deploy)
        assert reasons(after) == later and after.left.complete and len(after.origins) == 2


# The bounds, each met and each said


def test_a_title_of_many_words_is_looked_up_whole_and_one_past_the_bound_is_not(store, ops):
    """No count of words decides: the title is given to the index as it is. 66 words in 200
    characters are a phrase like any other; a title longer than relations.TERM_MAX characters
    (none the product writes: a title is 200 at most) is not looked up, and relates none."""
    words = [chr(97 + n // 26) + chr(97 + n % 26) for n in range(67)]
    title = " ".join(words[:66]) + " zz"
    assert len(title) == relations.TERM_MAX and len(title.split()) > ranking.WORDS_MAX
    store.save_page(ops.id, "long", title, "", [Claim(id="cl_a", text="Ada.", span_ids=())])
    store.save_page(
        ops.id, "first", "First", "", [Claim(id="cl_b", text=f"Read {title} twice.", span_ids=())]
    )
    store.save_page(
        ops.id, "most", "Most", "", [Claim(id="cl_c", text=" ".join(words[:66]), span_ids=())]
    )
    assert reasons(related(store, ops.id, "long")) == {"first": [(MENTIONED_BY, title, "title", 2)]}
    longer = title + "z"
    store.save_page(ops.id, "longer", longer, "", [Claim(id="cl_d", text="Brook.", span_ids=())])
    store.save_page(
        ops.id, "second", "Second", "", [Claim(id="cl_e", text=f"Read {longer} too.", span_ids=())]
    )
    found = related(store, ops.id, "longer")
    assert found.pages == () and found.left == NOTHING_LEFT_OUT  # by rule: not looked up
    store.save_page(ops.id, "dash", "---", "", [Claim(id="cl_f", text="Brook ---.", span_ids=())])
    assert related(store, ops.id, "dash").left == NOTHING_LEFT_OUT  # no word: none matches


COMPOSED = "R\u00e9sum\u00e9"
DECOMPOSED = unicodedata.normalize("NFD", COMPOSED)


@pytest.mark.parametrize("title", [COMPOSED, DECOMPOSED])
@pytest.mark.parametrize("written", [COMPOSED, DECOMPOSED])
def test_a_title_is_mentioned_where_the_index_matches_it_composed_or_not(
    store, ops, title, written
):
    """The index reads an accented letter and the same letter with a combining accent as one
    word, and it alone reads the title: the page is mentioned whichever way each is written."""
    assert COMPOSED != DECOMPOSED and len(DECOMPOSED) == 8
    asked = file(store, "ops", title, "Brook keeps the template.\n")
    file(store, "ops", "Hiring", f"Send the {written} to Ada.\n")
    found = related(store, ops.id, asked)
    assert reasons(found) == {"hiring": [(MENTIONED_BY, title, "title", 2)]}
    assert found.left.complete and found.title == title  # kept as it was written


@pytest.mark.parametrize("form", ["NFC", "NFD"])
def test_a_term_with_an_accent_relates_the_pages_that_write_it_the_same_way(store, ops, form):
    """Both pages write it composed, or both decomposed: one term, found by the index's own
    words (two words by a splitter of ours, where the index reads one). Written the other
    way on a third page it is another term, as a capital makes another: exact, as read."""
    term = unicodedata.normalize(form, "R\u00e9sum\u00e9.cfg")
    other = unicodedata.normalize("NFD" if form == "NFC" else "NFC", term)
    draft = file(store, "ops", "Draft", f"Brook edits `{term}` first.\n")
    review = file(store, "ops", "Review", f"Ada reads `{term}` after.\n")
    third = file(store, "ops", "Third", f"Quill reads `{other}` last.\n")
    assert reasons(related(store, ops.id, draft)) == {review: [(SHARES, term, "inline_code", 2)]}
    assert reasons(related(store, ops.id, review)) == {draft: [(SHARES, term, "inline_code", 2)]}
    found = related(store, ops.id, third)
    assert found.pages == () and found.left.complete and found.left.claims_read == 2


def test_a_term_and_a_title_that_hold_a_quote_or_a_null_are_found_and_nothing_fails(store, ops):
    """A double quote would end the phrase and a null the query: the one is doubled, the
    other given as the space the index reads it as. A claim that names a title holding a null
    in prose writes the space (one that writes the null names no page: relating._mentioning)."""
    for n, term in enumerate(['say "hi" now --x', "a1\x00b2"]):
        one = file(store, "ops", f"One {n}", f"Ada runs `{term}` here.\n")
        two = file(store, "ops", f"Two {n}", f"Brook runs `{term}` there.\n")
        assert reasons(related(store, ops.id, one)) == {two: [(SHARES, term, "inline_code", 2)]}
    for n, title in enumerate(['The "Quill" ledger', "Night\x00run"]):
        store.save_page(
            ops.id, f"named-{n}", title, "", [Claim(id="cl_n", text="Ada.", span_ids=())]
        )
        written = title.replace("\x00", " ")
        by = file(store, "ops", f"Namer {n}", f"Brook opens {written} at dusk.\n")
        assert reasons(related(store, ops.id, f"named-{n}")) == {
            by: [(MENTIONED_BY, title, "title", 2)]
        }


def test_a_term_of_many_parts_is_looked_up_like_any_other(store, ops):
    """65 parts joined by "_", which a search's words may not hold: a term is not a search's
    words. It is looked up as it is, the two pages are related, and nothing is left out."""
    parts = [chr(97 + n // 26) + chr(97 + n % 26) for n in range(65)]
    term = "_".join(parts)
    assert len(term) == 194 and len(parts) > ranking.PARTS_MAX
    one = file(store, "ops", "Keys one", f"The key is `{term}` here.\n")
    two = file(store, "ops", "Keys two", f"Set `{term}` there too.\n")
    found = related(store, ops.id, one)
    assert reasons(found) == {two: [(SHARES, term, "inline_code", 2)]}
    assert found.left == NOTHING_LEFT_OUT._replace(terms_looked_up=1, claims_read=1)


def test_a_title_more_than_five_other_pages_name_relates_none_by_mention(store, ops):
    """As a term more than COMMON pages hold: its claims are read whole, six of them, so the
    answer is complete."""
    quill = file(store, "ops", "Quill", "Ada wrote it.\n")
    for n in range(COMMON):
        file(store, "ops", f"Notes {n}", f"Quill line {n}.\n")
    found = related(store, ops.id, quill)
    assert reasons(found) == {
        f"notes-{n}": [(MENTIONED_BY, "Quill", "title", 2)] for n in range(COMMON)
    }
    file(store, "ops", "Notes more", "One more names Quill.\n")  # the sixth other page
    found = related(store, ops.id, quill)
    assert found.pages == () and found.left == NOTHING_LEFT_OUT._replace(claims_read=COMMON + 1)
    assert found.left.complete


def test_a_title_more_than_sixty_four_claims_of_other_pages_hold_relates_none(store, ops):
    """Its own claims do not count: a page says its own title in every claim, and that is no
    mention. 64 claims of two other pages name it; the 65th makes it common."""
    lines = "\n".join(f"- Quill does thing {n}." for n in range(70))
    quill = file(store, "ops", "Quill", lines + "\n")
    for page in range(2):
        lines = "\n".join(f"- On {page}.{n} Quill is named." for n in range(32))
        file(store, "ops", f"Notes {page}", lines + "\n")
    assert CLAIMS_COMMON == 64
    found = related(store, ops.id, quill)
    assert reasons(found) == {
        "notes-0": [(MENTIONED_BY, "Quill", "title", 2)],
        "notes-1": [(MENTIONED_BY, "Quill", "title", 2)],
    }
    assert found.left == NOTHING_LEFT_OUT._replace(claims_read=CLAIMS_COMMON)  # each was read
    file(store, "ops", "Third", "Quill once more.\n")
    found = related(store, ops.id, quill)
    assert found.pages == () and found.left == NOTHING_LEFT_OUT  # and here none: 65 list it


# A term many claims hold


def lines_of(count: int, term: str, mark: str) -> str:
    return "\n".join(f"- {mark} {n} sets {term}." for n in range(count)) + "\n"


def test_a_term_more_than_sixty_four_claims_of_the_project_hold_relates_none_from_any_side(
    store, ops
):
    """The claims of the whole project count, the asking page's too, so the count is the same
    whichever page asks: 58 claims of one page and 3 of each of two others are 64, and the
    three pages are related by the term; one claim more and none is, from any side."""
    many = file(store, "ops", "Many", lines_of(58, "`x.cfg`", "Step"))
    few = file(store, "ops", "Few", lines_of(3, "`x.cfg`", "Line"))
    other = file(store, "ops", "Other", lines_of(3, "`x.cfg`", "Row"))
    for slug, others in ((many, {few, other}), (few, {many, other}), (other, {many, few})):
        found = related(store, ops.id, slug)
        assert reasons(found) == {at: [(SHARES, "x.cfg", "inline_code", 3)] for at in others}
        assert found.left.complete and found.left.claims_not_read == 0
    file(store, "ops", "Aside", "Brook saw `x.cfg` once.\n")  # the 65th claim, on a fourth page
    for slug in (many, few, other, "aside"):
        found = related(store, ops.id, slug)
        assert found.pages == () and found.left.complete, slug
        assert found.left.claims_read == 0  # known from the rows listed: no claim was read


def test_the_claims_that_count_hold_the_terms_words_whatever_their_passage_holds(store, ops):
    """What makes a term common by its claims is the index's count of the claims whose text
    holds its words: 64 claims that say "dry run" in a sentence make `--dry-run` common."""
    asked = file(store, "ops", "Asked", "Start with --dry-run first.\n")
    twin = file(store, "ops", "Twin", "Brook passes --dry-run too.\n")
    assert reasons(related(store, ops.id, asked)) == {twin: [(SHARES, "--dry-run", "flag", 2)]}
    prose = "\n".join(f"- A dry run of step {n} costs nothing." for n in range(63))
    file(store, "ops", "Prose", prose + "\n")
    for slug in (asked, twin):
        found = related(store, ops.id, slug)
        assert found.pages == () and found.left.complete


def test_a_term_whose_claims_were_not_all_read_relates_none_and_the_claims_are_counted(
    store, ops, monkeypatch
):
    """No count of pages is given that the read did not see whole: past the claims read in
    all, a term gives no reason, and its claims are counted as not read."""
    asked = file(store, "ops", "Asked", "Use `a.cfg` and `b.cfg`.\n")
    file(store, "ops", "One", "Set `a.cfg`.\n")
    file(store, "ops", "Two", lines_of(3, "`b.cfg`", "Line"))
    monkeypatch.setattr(relating, "CLAIMS_MAX", 2)
    found = related(store, ops.id, asked)
    assert reasons(found) == {"one": [(SHARES, "a.cfg", "inline_code", 2)]}
    assert found.left == relating.Left(
        terms_looked_up=2,
        terms_not_looked_up=0,
        claims_read=1,
        claims_not_read=3,
        pages_left_out=0,
    )
    assert not found.left.complete
    monkeypatch.setattr(relating, "CLAIMS_MAX", 4)
    found = related(store, ops.id, asked)
    assert set(reasons(found)) == {"one", "two"} and found.left.complete


def test_a_passage_past_the_bytes_read_is_not_fetched_and_its_term_relates_none(
    store, ops, monkeypatch
):
    """A passage is measured from its row, without its text (a handle on the stored value, as
    cairn.graph measures one): no statement asks SQLite for a length, and a passage that does
    not fit is never fetched. The bound is in bytes as stored: an accented letter is two."""
    asked = file(store, "ops", "Asked", "Use `a.cfg` and `b.cfg`.\n")
    file(store, "ops", "Short", "Set `a.cfg`.\n")
    long = "Set `b.cfg` " + "é" * 60 + " then stop."  # 83 characters, 143 bytes
    file(store, "ops", "Long", long + "\n")
    assert (len(long), len(long.encode())) == (83, 143)
    monkeypatch.setattr(relating, "BYTES_MAX", 100)
    statements, rows = [], []
    store._observe(trace=statements.append, rows=lambda cursor, row: rows.append(row) or row)
    try:
        found = related(store, ops.id, asked)
    finally:
        store._observe(trace=None, rows=None)
    assert reasons(found) == {"short": [(SHARES, "a.cfg", "inline_code", 2)]}
    assert (found.left.claims_read, found.left.claims_not_read) == (1, 1)
    assert not found.left.complete
    assert not [s for s in statements if "length(" in s.lower()]
    assert not [row for row in rows if any(isinstance(v, str) and "é" in v for v in row)]
    monkeypatch.setattr(relating, "BYTES_MAX", 143 + len("Set `a.cfg`."))
    found = related(store, ops.id, asked)
    assert set(reasons(found)) == {"short", "long"} and found.left.complete


def test_a_claim_whose_passage_is_another_projects_states_no_term(store, ops):
    """A page saved by hand whose claim cites a passage of another project: the passage is
    not read for terms, whatever the claim's text says. Who wrote it is named as `page`
    names it: for a view that reaches the other project, and not for one that does not."""
    other = store.create_project("other")
    mail = store.writing_as("source", "team-mail")
    theirs, _ = mail.add_note(other.id, "# Mail\n\nUse `a.cfg` with Quill.\n", via="mail")
    span = next(s.id for s in store.note_spans(theirs.id) if s.kind == "paragraph")
    borrowed = Claim(id="cl_m", text="Use `a.cfg` with Quill.", span_ids=(span,))
    earlier_page(store, ops.id, "mixed", "Mixed", "", [borrowed])
    twin = file(store, "ops", "Twin", "Set `a.cfg` here.\n")
    quill = file(store, "ops", "Quill", "Ada wrote it.\n")

    assert related(store, ops.id, "mixed").left == NOTHING_LEFT_OUT
    found = related(store, ops.id, twin)  # the index proposes the claim; its passage is not ours
    assert found.pages == () and found.left.claims_read == 1 and found.left.complete
    mentioned = related(store, ops.id, quill)
    assert reasons(mentioned) == {"mixed": [(MENTIONED_BY, "Quill", "title", 2)]}
    assert mentioned.notes_of == {span: theirs.id} and set(mentioned.origins) == {theirs.id}
    assert mentioned.origins[theirs.id].kind == "source"
    alone = store.narrow(projects=(ops.id,)).related_pages(ops.id, quill)
    assert reasons(alone) == reasons(mentioned)
    assert alone.notes_of == {} and alone.origins == {}  # out of its reach: no one is named


def test_a_term_written_with_a_thousands_separator_relates_nothing_from_either_side(store, ops):
    """A limit, pinned: `1,250x` is compared as `1250x`, and the index reads `1,250x` as other
    words, so the claim that writes the separator states no term the index finds."""
    comma = file(store, "ops", "Comma", "The cap is `1,250x` here.\n")
    plain = file(store, "ops", "Plain", "The cap is `1250x` here.\n")
    twin = file(store, "ops", "Twin", "Brook saw `1250x` twice.\n")
    assert related(store, ops.id, comma).pages == ()
    assert reasons(related(store, ops.id, plain)) == {twin: [(SHARES, "1250x", "inline_code", 2)]}
    assert reasons(related(store, ops.id, twin)) == {plain: [(SHARES, "1250x", "inline_code", 2)]}
    assert all(related(store, ops.id, slug).left.complete for slug in (comma, plain, twin))


def test_the_first_passage_a_claim_cites_stands_for_the_others(store, ops):
    """A claim is the whole of each passage it cites; one that is not (a page saved by hand)
    states the terms of the first."""
    first, _ = store.add_note(ops.id, "# One\n\nUse `a.cfg`.\n")
    second, _ = store.add_note(ops.id, "# Two\n\nUse `b.cfg`.\n")
    spans = [
        next(s.id for s in store.note_spans(note.id) if s.kind == "paragraph")
        for note in (first, second)
    ]
    store.save_page(
        ops.id,
        "both",
        "Both",
        "",
        [Claim(id="cl_x", text="Use `a.cfg`. Use `b.cfg`.", span_ids=spans)],
    )
    file(store, "ops", "Ada", "Set `a.cfg`.\n")
    file(store, "ops", "Brook", "Set `b.cfg`.\n")
    assert reasons(related(store, ops.id, "both")) == {"ada": [(SHARES, "a.cfg", "inline_code", 2)]}
    assert reasons(related(store, ops.id, "brook")) == {}


def test_a_term_longer_than_two_hundred_characters_relates_none(store, ops):
    at_bound = "https://fable.example/" + "a" * 178
    past = "https://fable.example/" + "b" * 179
    one = file(store, "ops", "One", f"Ada reads {at_bound} daily.\n\nAnd {past} weekly.\n")
    two = file(store, "ops", "Two", f"Brook reads {at_bound} too.\n\nAnd {past} yearly.\n")
    assert (len(at_bound), len(past)) == (200, 201)
    for slug, other in ((one, two), (two, one)):
        assert reasons(related(store, ops.id, slug)) == {other: [(SHARES, at_bound, "url", 2)]}


# A long claim: cut at the bound, with the line of its passage that holds the term


def test_a_claim_longer_than_the_bound_is_given_cut_with_the_line_that_holds_the_term(store, ops):
    """The term stands past the cut, on the 92nd line of the passage: the line is taken by
    where the fact stands, so it is the right one whatever the text given holds, and
    whatever line holds the same letters and is no fact (the first, here)."""
    lines = (
        ["Brook says quill.lock twice: QUILL.LOCK."]
        + [f"Brook checks ledger {n} at dawn." for n in range(90)]
        + ["Ada pins `quill.lock` at noon."]
        + [f"Quill files report {n}, where `quill.lock` is named again." for n in range(40)]
    )
    whole = "\n".join(lines)
    assert len(whole) > TEXT_MAX and whole.index("`quill.lock`") > TEXT_MAX
    long = file(store, "ops", "Long", whole + "\n")
    short = file(store, "ops", "Short", "Ada reads `quill.lock` daily.\n")

    (page,) = related(store, ops.id, short).pages
    (reason,) = page.why
    (held,) = store.get_page(ops.id, long).claims
    assert held.text == whole
    assert reason.there == relating.Stated(
        held.id,
        whole[:TEXT_MAX],
        held.span_ids,
        cut=True,
        line="Ada pins `quill.lock` at noon.",
        line_cut=False,
    )
    assert reason.here.text == "Ada reads `quill.lock` daily."
    assert (reason.here.cut, reason.here.line, reason.here.line_cut) == (False, None, False)

    (back,) = related(store, ops.id, long).pages  # asked of the long page: its own side is cut
    assert (back.why[0].here, back.why[0].there) == (reason.there, reason.here)


@pytest.mark.parametrize("filler", ["a", "\u00e9", "\u20ac", "\U0001f600", '"'])
@pytest.mark.parametrize("more", [-1, 0, 1, 2])
def test_a_claim_is_whole_to_the_bound_and_cut_one_character_past_it(store, ops, filler, more):
    """Counted in characters, whatever bytes each is stored in."""
    lead = "Brook pins `quill.lock` "
    whole = lead + filler * (TEXT_MAX + more - len(lead) - 1) + "."
    assert len(whole) == TEXT_MAX + more
    file(store, "ops", "Long", whole + "\n")
    short = file(store, "ops", "Short", "Ada reads `quill.lock` daily.\n")
    there = related(store, ops.id, short).pages[0].why[0].there
    assert (there.text, there.cut) == (whole[:TEXT_MAX], more > 0)
    assert (there.line is not None) == (more > 0) and there.line_cut == (more > 0)


READ = 4 * (TEXT_MAX + 1)  # the bytes of a claim's text one read takes
LEADS = ("Brook pins `quill.lock` ", "Brook pins `quill.lock` x", "Brook pins `quill.lock` xy")


def test_of_three_leads_a_byte_apart_two_end_the_read_inside_a_character():
    """Before characters of three bytes each: so the test below meets a character read in
    part, whatever the bound on a claim's text is."""
    inside = 0
    for lead in LEADS:
        try:
            (lead + "\u20ac" * 3_000).encode()[:READ].decode()
        except UnicodeDecodeError:
            inside += 1
    assert inside == 2


@pytest.mark.parametrize(
    "whole",
    [
        "\U0001f600" * TEXT_MAX + " Brook pins `quill.lock`.",  # 4 bytes each, to the bound
        *(lead + "\u20ac" * 3_000 for lead in LEADS),  # the bytes read end inside a character
        LEADS[0] + "\U0001f600" * 3_000,  # and here between two
    ],
)
def test_a_claim_is_cut_by_characters_wherever_the_bytes_read_end(store, ops, whole):
    assert len(whole.encode()) > READ
    file(store, "ops", "Long", whole + "\n")
    short = file(store, "ops", "Short", "Ada reads `quill.lock` daily.\n")
    there = related(store, ops.id, short).pages[0].why[0].there
    assert (there.text, there.cut) == (whole[:TEXT_MAX], True)
    assert (there.line_cut, len(there.line)) == (True, LINE_MAX)
    assert "`quill.lock`" in there.line and there.line in whole


def test_a_long_line_is_given_as_a_window_that_holds_the_term_as_written(store, ops):
    whole = "Brook waits for the nightly log, " * 80 + "pins `Quill.Lock` and " + "stops, " * 90
    assert "\n" not in whole and len(whole) > TEXT_MAX
    file(store, "ops", "Long", whole + "\n")
    short = file(store, "ops", "Short", "Ada reads `Quill.Lock` daily.\n")
    (reason,) = related(store, ops.id, short).pages[0].why
    there = reason.there
    assert (reason.term, there.cut, there.line_cut) == ("Quill.Lock", True, True)  # as read
    assert len(there.line) == LINE_MAX and there.line in whole
    assert "`Quill.Lock`" in there.line and "Quill.Lock" not in there.text


def mention(store: Store, ops, whole: str, title: str = "Quill ledger"):
    """The claim of `whole`, as the page `title` is answered that it is mentioned by it."""
    named = file(store, "ops", title, "Brook balances it at dusk.\n")
    file(store, "ops", "Long", whole + "\n")
    (page,) = related(store, ops.id, named).pages
    (reason,) = page.why
    assert (reason.kind, reason.here) == (MENTIONED_BY, None)
    assert (reason.there.text, reason.there.cut) == (whole[:TEXT_MAX], len(whole) > TEXT_MAX)
    return reason.there


def test_a_long_claim_that_mentions_the_page_gives_the_line_that_names_it(store, ops):
    """The line is where the index itself matched the title's words in the claim's text: the
    first such place, here words in capitals with a dash and emphasis between them. A search
    for the title's letters would take the last line."""
    lines = (
        [f"Ada waits for nightly log {n}." for n in range(60)]
        + ["Then she opens the QUILL\u2014*ledger* first."]
        + [f"Brook rests {n}." for n in range(30)]
        + ["Last of all, the Quill ledger again."]
    )
    whole = "\n".join(lines)
    assert whole.index("QUILL") > TEXT_MAX and whole.count("Quill ledger") == 1
    there = mention(store, ops, whole)
    assert (there.line, there.line_cut) == ("Then she opens the QUILL\u2014*ledger* first.", False)


def test_a_claim_that_mentions_the_page_and_is_not_cut_has_no_line(store, ops):
    there = mention(store, ops, "See the Quill ledger first.")
    assert (there.cut, there.line, there.line_cut) == (False, None, False)


PLACES = [0, 1, LINE_MAX - 15, LINE_MAX - 14, LINE_MAX - 13, LINE_MAX, LINE_MAX + 1, 3 * LINE_MAX]


@pytest.mark.parametrize("before", PLACES)
@pytest.mark.parametrize("after", [0, 1, LINE_MAX - 15, LINE_MAX - 14, LINE_MAX, 3 * LINE_MAX])
def test_the_line_of_a_mention_is_the_rules_line_wherever_the_title_stands(
    store, ops, before, after
):
    """Against relations.line_of on the whole text, at the place the title is written: at the
    line's start and its end, and with the title as far from either as a line given whole
    lets it be (the 12 characters of the title and LINE_MAX - 12 more, with the "; "), one
    character nearer and one farther: what is asked of the index is just enough."""
    lead = "Ada waits for the nightly log. " * 30 + "\n"
    line = "b" * before + ("; " if before else "") + "Quill ledger" + ("; " if after else "")
    whole = lead + line + "a" * after + "\nBrook rests."
    start = whole.index("Quill ledger")
    there = mention(store, ops, whole)
    assert (there.line, there.line_cut) == relations.line_of(whole, start, start + 12)
    assert "Quill ledger" in there.line and len(there.line) <= LINE_MAX


def test_a_title_whose_words_stand_on_two_lines_is_given_with_both(store, ops):
    whole = "Ada waits for the nightly log. " * 30 + "\nShe opens the Quill\nledger at noon."
    there = mention(store, ops, whole)
    assert (there.line, there.line_cut) == ("She opens the Quill\nledger at noon.", False)


def test_a_title_whose_words_stand_far_apart_is_given_by_the_first_of_the_match(store, ops):
    """The index matches the title's words side by side whatever stands between them that is
    no word. What is given of such a match is LINE_MAX characters from its start, never
    more: a line given has one bound, whoever writes the claim."""
    apart = "Quill" + " \u2014" * 300 + " ledger"
    whole = "Ada waits for the nightly log. " * 30 + "She opens the " + apart + " at noon."
    there = mention(store, ops, whole)
    assert (there.line, there.line_cut) == (apart[:LINE_MAX], True)


NO_LINE = [
    *((mark, where) for mark in relating.MARKS for where in ("before the title", "after it")),
    *(("\x00", where) for where in ("before the title", "after it", "between its words")),
]


@pytest.mark.parametrize(("mark", "where"), NO_LINE)
def test_a_claim_whose_text_holds_a_mark_of_the_index_or_a_null_names_no_page(
    store, ops, mark, where
):
    """The index says where it matched by putting two marks around the match (relating.MARKS,
    two characters Unicode keeps out of every text exchanged). In a claim that holds one of
    them the place cannot be told from the marks; and SQLite's own reading of a text stops
    at a null character, so of a claim that holds one the place would be wrong. Whether such
    a claim names the title in its prose cannot be checked, so it names no page, short or
    long, whatever else it holds: a path cannot make a mention by holding a mark. (A claim
    may hold any of the three: a note is stored as it is written.)"""
    named = {
        "before the title": f"She opens {mark} the Quill ledger.",
        "after it": f"She opens the Quill ledger {mark} at noon.",
        "between its words": f"She opens the Quill{mark}ledger at noon.",
    }[where]
    ledger = file(store, "ops", "Quill ledger", "Brook balances it at dusk.\n")
    file(store, "ops", "Short", named + "\n")
    file(store, "ops", "Long", "Ada waits for the nightly log. " * 30 + named + "\n")
    file(store, "ops", "Path", f"Brook reads ~/quill/ledger.md {mark} daily.\n")
    plain = file(store, "ops", "Plain", "She opens the Quill ledger at noon.\n")
    assert named_by(store, ops, ledger) == [plain]
    (held,) = store.get_page(ops.id, "long").claims
    assert held.text.endswith(named)  # stored whole, the character with it


@pytest.mark.parametrize(
    "lead", ["\U0001f600" * 500, "\u20ac" * 300 + "\u00e9" * 300, "\x01" * 500]
)
def test_the_place_of_a_mention_is_counted_in_characters(store, ops, lead):
    """Characters of four, three, two and one bytes before the title, on its own long line:
    the line given is the rule's, at the place the title stands in characters."""
    whole = "Ada waits for the nightly log. " * 30 + "\n" + lead + " the Quill ledger at noon."
    start = whole.index("Quill ledger")
    there = mention(store, ops, whole)
    assert (there.line, there.line_cut) == relations.line_of(whole, start, start + 12)
    assert there.line_cut and "Quill ledger" in there.line


def test_one_claim_cut_gives_each_term_its_own_line(store, ops):
    lines = (
        ["Brook pins `quill.lock` at dawn."]
        + [f"Brook checks ledger {n} at dawn." for n in range(90)]
        + ["Ada sets `--audit-keep` at noon."]
    )
    file(store, "ops", "Long", "\n".join(lines) + "\n")
    short = file(store, "ops", "Short", "Ada reads `quill.lock` and `--audit-keep`.\n")
    (page,) = related(store, ops.id, short).pages
    assert {
        r.term: (r.there.line, r.there.claim_id == page.why[0].there.claim_id) for r in page.why
    } == {
        "quill.lock": ("Brook pins `quill.lock` at dawn.", True),
        "--audit-keep": ("Ada sets `--audit-keep` at noon.", True),
    }


def test_a_person_is_answered_for_the_projects_their_rule_reaches(store):
    issuer = "https://id.example.test/pool-1"
    store.sign_in("sso", "sub-ada", "ada@example.test", "Ada", issuer=issuer, join="default")
    signed = store.sign_in(
        "sso", "sub-brook", "brook@example.test", "Brook", issuer=issuer, join="default"
    )
    brook = store.for_account(signed.account_id, signed.user.id)
    shared = store.create_project("shared")
    private = store.create_project("private", everyone="none")
    for name in ("shared", "private"):
        file(store, name, "Deploy", "Run `--audit-keep`.\n")
        file(store, name, "Retention", "`--audit-keep` says how many.\n")
    assert reasons(brook.related_pages(shared.id, "deploy")) == {
        "retention": [(SHARES, "--audit-keep", "inline_code", 2)]
    }
    assert brook.related_pages(private.id, "deploy") is None


# One read of related pages at a time: the store's turn, taken before a connection


class Asking:
    """The store's turn, saying when the thread named `who` asks for it."""

    def __init__(self, turn, who: str) -> None:
        self.turn, self.who, self.asked = turn, who, threading.Event()

    def __enter__(self):
        if threading.current_thread().name == self.who:
            self.asked.set()
        return self.turn.__enter__()

    def __exit__(self, *raised):
        return self.turn.__exit__(*raised)


def test_a_second_read_of_related_pages_waits_its_turn_and_a_page_view_does_not(
    store, ops, monkeypatch
):
    """Two reads of related pages at once: the second takes no connection of the pool until
    the first has given its connection back, and a page read in between is served at once,
    on another connection. Threads meet at events; nothing sleeps."""
    deploy = file(store, "ops", "Deploy", "Run `--audit-keep` at night.\n")
    file(store, "ops", "Retention", "`--audit-keep` says how many; see Deploy.\n")
    readers, seen = store._state.readers, []
    took, gave = readers.take, readers.give

    def taking(observers):
        con = took(observers)
        seen.append((threading.current_thread().name, "take", id(con)))
        return con

    def giving(con):
        seen.append((threading.current_thread().name, "give", id(con)))
        gave(con)

    monkeypatch.setattr(readers, "take", taking)
    monkeypatch.setattr(readers, "give", giving)
    inside, go_on = threading.Event(), threading.Event()
    whole = relating.related

    def held(*arguments):
        if threading.current_thread().name == "first":
            inside.set()
            assert go_on.wait(timeout=60)
        return whole(*arguments)

    monkeypatch.setattr(relating, "related", held)
    asking = Asking(store._state.related_turn, "second")
    monkeypatch.setattr(store._state, "related_turn", asking)
    answers = {}

    def read() -> None:
        answers[threading.current_thread().name] = related(store, ops.id, deploy)

    first = threading.Thread(target=read, name="first")
    second = threading.Thread(target=read, name="second")
    first.start()
    assert inside.wait(timeout=60)
    second.start()
    assert asking.asked.wait(timeout=60)  # the second asks while the first holds the turn
    page = store.get_page(ops.id, deploy)  # a page view, meanwhile
    assert page is not None and second.is_alive()
    go_on.set()
    first.join(timeout=60)
    second.join(timeout=60)
    assert not first.is_alive() and not second.is_alive()
    assert reasons(answers["first"]) == reasons(answers["second"]) != {}
    ((_, _, at_first), (here, _, at_page), (_, _, back)) = seen[:3]
    assert [entry[:2] for entry in seen] == [
        ("first", "take"),
        ("MainThread", "take"),
        ("MainThread", "give"),
        ("first", "give"),
        ("second", "take"),
        ("second", "give"),
    ]
    assert here == "MainThread" and at_page != at_first and back == at_page


def test_a_read_of_related_pages_that_fails_gives_its_turn_back(store, ops, monkeypatch):
    """An error inside the read, or a page that is not there: the turn is given back, and the
    next read is served."""
    deploy = file(store, "ops", "Deploy", "Run `--audit-keep` at night.\n")
    file(store, "ops", "Retention", "`--audit-keep` says how many; see Deploy.\n")

    def failing(*arguments):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(relating, "related", failing)
    with pytest.raises(sqlite3.OperationalError):
        store.related_pages(ops.id, deploy)
    assert not store._state.related_turn.locked()
    monkeypatch.undo()
    assert store.related_pages(ops.id, "nothing-here") is None
    assert not store._state.related_turn.locked()
    assert reasons(related(store, ops.id, deploy)) != {}


def test_the_rules_and_the_read_ask_no_model_and_nothing_of_the_web_layer():
    """Their imports, read: the fact extractor, the passages' model and the index's own match.
    No model, no network and no web layer can be asked by code that does not import them."""
    import ast
    from pathlib import Path

    from cairn import relations

    def imported(module) -> set[str]:
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
            elif isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
        return {name for name in found if name.split(".")[0] == "cairn"}

    assert imported(relations) == {"cairn.facts", "cairn.spans"}
    assert imported(relating) == {"cairn", "cairn.relations", "cairn.spans", "cairn.store"}
    source = Path(relating.__file__).read_text(encoding="utf-8")
    assert "from cairn import relations\n" in source
    assert "from cairn.store import ranking\n" in source
