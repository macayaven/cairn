"""Related pages as the store reads them (Store.related_pages, cairn.store.relating): what
relates two pages, what does not, the evidence each relation carries, and that the read is
one project's, in one snapshot. Real stores in temporary files, notes filed through
Memory.note as `serve` files them; made-up names."""

import json
from contextlib import closing

import pytest

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.relations import CLAIMS_COMMON, COMMON, LINE_MAX, MENTIONED_BY, SHARES, TEXT_MAX
from cairn.store import Claim, Store, ranking, relating


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


def test_a_phrase_is_the_words_side_by_side_in_a_claims_text_and_never_its_title(store, ops):
    assert ranking.phrase(["Deploy", "runbook"]) == 'text : "Deploy runbook"'
    assert ranking.phrase(["os_login_2fa"]) == 'text : "os_login_2fa"'
    assert ranking.phrase([]) is None and ranking.phrase(["_", "___"]) is None  # no part
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
        ],
    )

    def held(words: list[str]) -> list[str]:
        with store._read() as con:
            rows = con.execute(
                f"SELECT claim_id FROM {store._index} WHERE {store._index} MATCH ? ORDER BY rowid",
                (ranking.phrase(words),),
            )
            return [row[0] for row in rows]

    # every row holds the title "Deploy runbook"; only the text decides, and punctuation between
    # two words is no word (the index's own reading, as a search's)
    assert held(["deploy", "runbook"]) == ["cl_a", "cl_c"]
    assert held(["os", "login", "2fa"]) == held(["os_login_2fa"]) == ["cl_c"]  # "_" splits
    assert held(["runbook", "deploy"]) == []


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


def test_a_one_word_title_is_found_inside_a_flag_that_holds_the_word(store, ops):
    """What the index's own match means for a short title: it reads `--audit-keep` as the words
    audit and keep, as a search does, so a page titled Audit is mentioned by that claim."""
    audit = file(store, "ops", "Audit", "Ada reads the log.\n")
    deploy = file(store, "ops", "Deploy", "Run the job with `--audit-keep` set to 7.\n")
    assert reasons(related(store, ops.id, audit)) == {deploy: [(MENTIONED_BY, "Audit", "title", 2)]}


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


def test_a_title_of_more_words_than_a_search_takes_relates_none_by_mention(store, ops):
    long_title = " ".join(f"w{n}" for n in range(ranking.WORDS_MAX + 1))
    store.save_page(ops.id, "long", long_title, "", [Claim(id="cl_a", text="Ada.", span_ids=())])
    first = " ".join(f"w{n}" for n in range(ranking.WORDS_MAX))
    store.save_page(
        ops.id, "other", "Other", "", [Claim(id="cl_b", text=f"{first} and more.", span_ids=())]
    )
    found = related(store, ops.id, "long")
    assert found.pages == ()  # its first 32 words alone would be found: that is no mention
    assert found.left == NOTHING_LEFT_OUT  # by rule, as a title of no word: nothing was left out
    store.save_page(ops.id, "dash", "---", "", [Claim(id="cl_c", text="Brook.", span_ids=())])
    assert related(store, ops.id, "dash").left == NOTHING_LEFT_OUT


def test_a_title_more_than_five_other_pages_name_relates_none_by_mention(store, ops):
    """As a term more than COMMON pages hold: read whole, so the answer is complete."""
    quill = file(store, "ops", "Quill", "Ada wrote it.\n")
    for n in range(COMMON):
        file(store, "ops", f"Notes {n}", f"Quill line {n}.\n")
    found = related(store, ops.id, quill)
    assert reasons(found) == {
        f"notes-{n}": [(MENTIONED_BY, "Quill", "title", 2)] for n in range(COMMON)
    }
    file(store, "ops", "Notes more", "One more names Quill.\n")  # the sixth other page
    found = related(store, ops.id, quill)
    assert found.pages == () and found.left == NOTHING_LEFT_OUT


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
    assert found.left == NOTHING_LEFT_OUT
    file(store, "ops", "Third", "Quill once more.\n")
    found = related(store, ops.id, quill)
    assert found.pages == () and found.left == NOTHING_LEFT_OUT


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
    store.save_page(ops.id, "mixed", "Mixed", "", [borrowed])
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


@pytest.mark.parametrize(
    "whole",
    [
        "\U0001f600" * TEXT_MAX + " Brook pins `quill.lock`.",  # 4 bytes each, to the bound
        "Brook pins `quill.lock` x" + "\u20ac" * 3_000,  # the bytes read end inside a character
        "Brook pins `quill.lock` " + "\U0001f600" * 3_000,  # and here between two
    ],
)
def test_a_claim_is_cut_by_characters_wherever_the_bytes_read_end(store, ops, whole):
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
    short = file(store, "ops", "Short", "Ada reads `quill.lock` daily.\n")
    (reason,) = related(store, ops.id, short).pages[0].why
    there = reason.there
    assert (reason.term, there.cut, there.line_cut) == ("quill.lock", True, True)
    assert len(there.line) == LINE_MAX and there.line in whole
    assert "`Quill.Lock`" in there.line and "Quill.Lock" not in there.text


def test_a_long_claim_that_mentions_the_page_is_cut_and_gives_no_line(store, ops):
    """A mention is the index's match, which has no place in a passage that this read holds:
    the claim says it is cut and `page` gives it whole."""
    ledger = file(store, "ops", "Quill ledger", "Brook balances it at dusk.\n")
    whole = "Ada waits for the nightly log, " * 80 + "then opens the Quill ledger."
    file(store, "ops", "Long", whole + "\n")
    (page,) = related(store, ops.id, ledger).pages
    (reason,) = page.why
    assert (reason.kind, reason.here) == (MENTIONED_BY, None)
    assert (reason.there.text, reason.there.cut) == (whole[:TEXT_MAX], True)
    assert (reason.there.line, reason.there.line_cut) == (None, False)


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
