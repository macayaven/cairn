"""Unit tests for reach: project sets, the reach of keys and grants, and narrowed views. The
inner TDD loop under tests/acceptance/test_s1_scopes.py."""

import inspect
import sqlite3
from pathlib import Path

import pytest

from cairn.store import Claim, Found, ProjectSet, Store, reach

REDIRECT = "http://127.0.0.1:33418/callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
UNKNOWN = "br_000000000000"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


@pytest.fixture
def ids(store) -> dict[str, str]:
    return {name: store.create_project(name).id for name in ("api", "web", "journal")}


@pytest.fixture
def agent(store):
    return store.register_client("test-agent", [REDIRECT])


def page(view, project_id: str, slug: str, *texts: str):
    claims = [Claim(id=f"cl_{slug}_{n}", text=t, span_ids=()) for n, t in enumerate(texts)]
    return view.save_page(project_id, slug, slug.title(), "", claims)


# Sets


def test_a_set_keeps_its_projects_in_the_order_given_once_each(store, ids):
    made = store.create_set("work", (ids["web"], ids["api"], ids["web"]))
    assert isinstance(made, ProjectSet) and made.project_ids == (ids["web"], ids["api"])
    assert store.get_set("work") == made


@pytest.mark.parametrize("name", ["set_abc", "SET_x", "br_x", "", "a b", "-x", "x" * 65])
def test_set_names_follow_the_project_name_rules_and_never_read_as_an_id(store, name):
    with pytest.raises(ValueError):
        store.create_set(name)


def test_a_set_holds_projects_only(store, ids):
    inner = store.create_set("inner", (ids["api"],))
    with pytest.raises(ValueError):
        store.create_set("outer", (inner.id,))
    other = store.for_account(store.create_account("Brook").id)
    theirs = other.create_project("theirs")
    with pytest.raises(ValueError):
        store.create_set("mixed", (ids["api"], theirs.id))
    assert [s.name for s in store.list_sets()] == ["inner"]


def test_sets_are_listed_by_name_and_found_by_id_or_name(store, ids):
    beta, alpha = store.create_set("beta"), store.create_set("Alpha", (ids["api"],))
    assert store.list_sets() == [alpha, beta]
    assert store.get_set("ALPHA") == alpha == store.get_set(alpha.id)
    assert store.get_set(alpha.id.upper()) is None and store.get_set("gamma") is None


def test_update_set_replaces_its_projects(store, ids, db):
    work = store.create_set("work", (ids["api"],))
    updated = store.update_set(work.id, (ids["web"],))
    assert updated == store.get_set("work") and updated.project_ids == (ids["web"],)
    before = dump(db)
    with pytest.raises(KeyError):
        store.update_set("set_000000000000", (ids["api"],))
    with pytest.raises(ValueError):
        store.update_set(work.id, (UNKNOWN,))
    assert dump(db) == before


def test_sets_belong_to_their_workspace(store, ids):
    work = store.create_set("work", (ids["api"],))
    brook = store.for_account(store.create_account("Brook").id)
    brook.create_set("work")  # the same name in another workspace
    with pytest.raises(KeyError):
        brook.update_set(work.id, ())
    assert brook.delete_set(work.id) is False and store.get_set(work.id) == work
    assert store.delete_account(brook.account_id) is True
    assert [s.name for s in store.list_sets()] == ["work"]


# The reach of keys and grants


def test_a_key_reaches_every_project_unless_told_otherwise(store, ids):
    key = store.create_key("owner")
    who = store.resolve_key(key)
    assert (who.projects, who.access) == (None, "write")
    assert [(k.label, k.projects, k.access) for k in store.list_keys()] == [
        ("owner", None, "write")
    ]


def test_a_key_reach_names_projects_and_sets_of_its_workspace(store, ids, db):
    work = store.create_set("work", (ids["web"],))
    key = store.create_key("ci", projects=[ids["api"], work.id, ids["api"]], access="read")
    assert (store.resolve_key(key).projects, store.resolve_key(key).access) == (
        (ids["api"], work.id),
        "read",
    )
    brook = store.for_account(store.create_account("Brook").id)
    before = dump(db)
    for bad in (
        {"projects": ()},  # a reach of nothing is no key
        {"projects": ("api",)},  # a name is not an id
        {"projects": (UNKNOWN,)},
        {"access": "admin"},
    ):
        with pytest.raises(ValueError):
            store.create_key("bad", **bad)
    with pytest.raises(ValueError):
        brook.create_key("theirs", projects=(ids["api"],))  # another workspace's project
    assert dump(db) == before


def test_a_grant_carries_the_reach_its_code_was_approved_with(store, ids, agent):
    work = store.create_set("work", (ids["api"],))
    code = store.create_code(agent.id, REDIRECT, CHALLENGE, projects=(work.id,), access="read")
    pair = store.redeem_code(code, agent.id)
    who = store.resolve_token(pair.access_token)
    assert (who.projects, who.access) == ((work.id,), "read")
    renewed = store.rotate_refresh_token(pair.refresh_token, agent.id)
    again = store.resolve_token(renewed.access_token)
    assert (again.projects, again.access) == ((work.id,), "read")
    [grant] = store.list_grants()
    assert (grant.projects, grant.access) == ((work.id,), "read")

    whole = store.redeem_code(store.create_code(agent.id, REDIRECT, CHALLENGE), agent.id)
    assert (store.resolve_token(whole.access_token).projects, store.list_grants()[1].access) == (
        None,
        "write",
    )


def test_an_approval_with_a_reach_outside_the_workspace_writes_nothing(store, ids, agent, db):
    before = dump(db)
    for bad in ({"projects": (UNKNOWN,)}, {"projects": ()}, {"access": "all"}):
        with pytest.raises(ValueError):
            store.create_code(agent.id, REDIRECT, CHALLENGE, **bad)
    assert dump(db) == before


def test_a_deleted_set_drops_out_of_a_reach_silently(store, ids):
    work = store.create_set("work", (ids["api"],))
    key = store.create_key("ci", projects=(work.id, ids["web"]))
    assert store.delete_set(work.id) is True
    assert store.resolve_key(key).projects == (work.id, ids["web"])  # stored as given
    who = store.resolve_key(key)
    view = store.narrow(who.projects, who.access)
    assert [b.name for b in view.list_projects()] == ["web"]


# Narrowed views


def test_every_public_store_method_is_classified_and_guarded():
    public = {name for name, _ in inspect.getmembers(Store, callable) if not name.startswith("_")}
    kinds = (
        reach.UNAFFECTED,
        reach.ACCOUNT_BOUND,
        reach.MANAGEMENT,
        reach.LIMITED_READS,
        reach.PROJECT_WRITES,
    )
    assert set().union(*kinds) == public, "classify every new store method in cairn.store.reach"
    assert sum(len(kind) for kind in kinds) == len(public)  # each in exactly one class
    for name in public - reach.UNAFFECTED - reach.PROJECT_DATA:
        assert name in vars(reach.ReachMethods), name  # the narrowing sees the call first
    # the calls on a project's data take the reach inside their own section: see
    # test_a_narrowed_call_resolves_its_reach_in_the_section_it_works_in


@pytest.mark.parametrize(
    "narrowing, message",
    [
        ({"projects": ("API",)}, "limited"),  # the fixture's ids are filled in below
        ({"projects": ("API",), "access": "read"}, "limited"),
        ({"access": "read"}, "read-only"),
    ],
)
def test_a_narrowed_view_manages_nothing(store, ids, agent, db, narrowing, message):
    narrowing = {**narrowing, **({"projects": (ids["api"],)} if "projects" in narrowing else {})}
    view = store.narrow(**narrowing)
    before = dump(db)
    for name in sorted(reach.MANAGEMENT):
        with pytest.raises(PermissionError, match=message):
            getattr(view, name)("anything")
    assert dump(db) == before


def test_a_limited_view_reads_only_its_projects(store, ids):
    for name in ("api", "journal"):
        store.add_note(ids[name], f"# {name}\n\nA {name} fact.\n")
        page(store, ids[name], name, f"A {name} fact.")
    api_note, journal_note = (store.list_notes(ids[n])[0] for n in ("api", "journal"))
    view = store.narrow(projects=(ids["api"],))

    assert view.get_note(api_note.id) == api_note
    assert view.get_note(journal_note.id) is None
    assert view.note_spans(journal_note.id) == [] and view.list_notes(ids["journal"]) == []
    spans = [*store.note_spans(api_note.id), *store.note_spans(journal_note.id)]
    seen = view.get_spans(s.id for s in reversed(spans))
    assert list(seen) == [s.id for s in reversed(spans) if s.source_id == api_note.id]
    assert view.get_page(ids["journal"], "journal") is None
    assert (
        view.list_pages(ids["journal"]) == []
        and view.page_versions(ids["journal"], "journal") == []
    )
    assert view.get_page(ids["api"], "api") == store.get_page(ids["api"], "api")
    assert view.get_project("JOURNAL") is None and view.get_project("api").id == ids["api"]


def test_a_limited_view_counts_and_lists_only_its_projects(store, ids):
    """Q1: the reads that cost what they return are limited as the reads beside them."""
    for name in ("api", "journal"):
        store.add_note(ids[name], f"# {name}\n\nA {name} fact.\n")
        page(store, ids[name], name, f"A {name} fact.")
    api_note, journal_note = (store.list_notes(ids[n])[0] for n in ("api", "journal"))
    view = store.narrow(projects=(ids["api"],))
    assert view.page_counts() == {ids["api"]: 1} != store.page_counts()
    assert view.count_notes(ids["journal"]) == 0 < store.count_notes(ids["journal"])
    assert view.page_heads(ids["journal"], 10) == ([], 0)
    assert view.page_heads(ids["api"], 10) == store.page_heads(ids["api"], 10)
    assert view.version_numbers(ids["journal"], "journal", 10) == ([], 0, None)
    assert view.version_numbers(ids["api"], "api", 10) == ([1], 1, 1)
    both = [api_note.id, journal_note.id]
    assert list(view.get_notes(both)) == [api_note.id] and len(store.get_notes(both)) == 2
    assert list(view.projects_sharing([ids["journal"], ids["api"]])) == [ids["api"]]


def test_a_limited_view_sees_no_credentials_or_sets_and_a_read_only_one_does(store, ids, agent):
    store.create_key("owner")
    store.create_set("work", (ids["journal"],))
    store.redeem_code(store.create_code(agent.id, REDIRECT, CHALLENGE), agent.id)
    limited = store.narrow(projects=(ids["api"],))
    assert limited.list_keys() == [] and limited.list_grants() == [] and limited.list_sets() == []
    assert limited.get_set("work") is None
    reader = store.narrow(access="read")
    assert [k.label for k in reader.list_keys()] == ["owner"] and len(reader.list_grants()) == 1
    assert reader.get_set("work") == store.get_set("work") and reader.list_sets()


def test_writes_into_a_project_out_of_reach_are_unknown_and_read_only_comes_first(store, ids, db):
    view = store.narrow(projects=(ids["api"],))
    before = dump(db)
    for write in (
        lambda v, b: v.add_note(b, "# x\n\ny\n"),
        lambda v, b: v.save_page(b, "x", "X", "", ()),
    ):
        with pytest.raises(KeyError):
            write(view, ids["journal"])
        with pytest.raises(KeyError):
            write(store, UNKNOWN)  # exactly as an unknown project
        for project_id in (ids["api"], ids["journal"], UNKNOWN):
            with pytest.raises(PermissionError, match="read-only"):
                write(view.narrow(access="read"), project_id)
    assert dump(db) == before


def test_a_narrowed_view_passes_on_the_keywords_it_does_not_read(store, ids):
    view = store.narrow(projects=(ids["api"],))
    saved = view.save_page(ids["api"], "x", "X", "", (), metrics={"claims": 0})
    assert saved.metrics == {"claims": 0}
    with pytest.raises(TypeError):  # the area's own method saw it, and refused it
        view.save_page(ids["api"], "x", "X", "", (), not_a_keyword=True)
    note, created = view.add_note(ids["api"], "# t\n\nu\n", source_ref="here")
    assert created and note.source_ref == "here"


def test_narrowing_can_only_narrow(store, ids):
    work = store.create_set("work", (ids["api"], ids["web"]))
    view = store.narrow(projects=(work.id,), access="read")
    assert [b.name for b in view.narrow(projects=(ids["web"], ids["journal"])).list_projects()] == [
        "web"
    ]
    assert [b.name for b in view.narrow().list_projects()] == ["api", "web"]
    with pytest.raises(PermissionError):
        view.narrow(access="write").add_note(ids["api"], "x")
    own = view.for_account(store.account_id)  # the narrowing goes along
    assert [b.name for b in own.list_projects()] == ["api", "web"]
    with pytest.raises(PermissionError):
        own.add_note(ids["api"], "x")
    assert store.narrow(projects=()).list_projects() == []  # a reach of nothing reaches nothing


@pytest.mark.parametrize(
    "narrowing", [{"access": "read"}, {"projects": ("API",)}, {"projects": ()}]
)  # "API" is the fixture's id, filled in below
def test_a_narrowed_view_cannot_leave_its_workspace(store, ids, narrowing):
    """A reach names ids of one workspace: carried into another, "every project" would be all of
    that one's. A narrowed view stays in its own account; the root store and a plain view
    switch accounts as before."""
    narrowing = {**narrowing, **({"projects": (ids["api"],)} if narrowing.get("projects") else {})}
    brook = store.create_account("Brook").id
    store.for_account(brook).create_project("theirs")
    view = store.narrow(**narrowing)
    with pytest.raises(PermissionError):
        view.for_account(brook)
    with pytest.raises(PermissionError):
        view.narrow().for_account(brook)
    assert view.for_account(store.account_id).list_projects() == view.list_projects()
    assert [b.name for b in store.for_account(brook).list_projects()] == ["theirs"]
    plain = store.for_account(brook).for_account(store.account_id)
    assert [b.name for b in plain.list_projects()] == ["api", "journal", "web"]
    assert [b.name for b in store.narrow().for_account(brook).list_projects()] == ["theirs"]


# Every call that reads or writes a project's data, with its arguments for the test below, as
# a function of the fixture's ids and of a note, span and page in "api".
PROJECT_CALLS = {
    "list_projects": lambda d: (),
    "writable_projects": lambda d: (),  # K1: those the person may write to
    "get_project": lambda d: (d["api"],),
    "get_note": lambda d: (d["note"],),
    "holds_note": lambda d: (d["api"], "# Retries\n\nQuill retries.\n"),
    "list_notes": lambda d: (d["api"],),
    "notes_part": lambda d: (d["api"], 10),  # UI and UX 8: a part of the notes
    "note_page": lambda d: (d["api"], d["note"]),  # whether a note waits, and the page citing it
    "note_spans": lambda d: (d["note"],),
    "get_spans": lambda d: ([d["span"]],),
    "spans_within": lambda d: ([d["span"]], 10_000),  # Help's narrow reads
    "note_writers": lambda d: ([d["note"]],),  # A1: who wrote a note
    "note_origins": lambda d: ([d["note"]],),  # 8.1, 8.5: and when, and whether from Slack
    "get_page": lambda d: (d["api"], "retries"),
    "page_claims": lambda d: (d["api"], "retries", 10_000),
    "page_for_model": lambda d: (d["api"], "retries"),  # M4: the page and its project's switch
    "kept_from_models": lambda d: ([d["api"]],),
    "models_choices": lambda d: ([d["api"]],),  # Privacy 4: the three states
    "list_pages": lambda d: (d["api"],),
    "page_versions": lambda d: (d["api"], "retries"),
    "page_counts": lambda d: (),  # Q1: the bounded reads
    "count_notes": lambda d: (d["api"],),
    "page_heads": lambda d: (d["api"], 10),
    "first_page_heads": lambda d: (d["api"], 10),
    "version_numbers": lambda d: (d["api"], "retries", 10),
    "get_notes": lambda d: ([d["note"]],),
    "note_sources": lambda d: ([d["note"]], 10_000),
    "topic_page": lambda d: (d["api"], "retries"),  # the page "Retries" made by `page`
    "taken_slugs": lambda d: (d["api"], ["retries", "later"]),
    "search": lambda d: ("quill",),
    "find": lambda d: ("quill",),
    "related_pages": lambda d: (d["api"], "retries"),  # the pages related to "retries"
    "add_note": lambda d: (d["api"], "# Later\n\nQuill again.\n"),
    "save_page": lambda d: (d["api"], "later", "Later", "", ()),
    "record_note": lambda d: (d["api"], "refused", {"rules": ["jwt"]}),
    "tried_waiting": lambda d: (d["note"],),
    "project_access": lambda d: (d["api"],),
    "project_sharing": lambda d: (d["api"],),
    "projects_sharing": lambda d: ([d["api"]],),
    "may_erase": lambda d: (d["api"],),
    "forget_preview": lambda d: (d["note"],),
    "forget_note": lambda d: (d["note"],),
    "delete_project": lambda d: (d["api"],),
}


@pytest.fixture
def section_hook(monkeypatch):
    """Counts the lock sections (_read and _write) entered while `watching`, and runs `hook`
    as the second one is entered, before it takes the lock: the moment another thread could
    change the workspace between a check and the operation it guards."""
    from cairn.store.base import StoreBase

    state = {"watching": False, "sections": 0, "hook": None, "fired": False}

    def counted(original):
        def section(self):
            if state["watching"]:
                state["sections"] += 1
                if state["sections"] == 2 and state["hook"] is not None:
                    state["watching"] = False  # the hook's own sections are not counted
                    state["hook"]()
                    state["fired"] = True
            return original(self)

        return section

    monkeypatch.setattr(StoreBase, "_read", counted(StoreBase._read))
    monkeypatch.setattr(StoreBase, "_write", counted(StoreBase._write))
    return state


def answered(result):
    """What a call gave of a project's data: a search's hits, without what it left out; and
    nothing for note_page's answer about a note out of reach, (False, None)."""
    if result == (False, None):
        return None
    return result.hits if isinstance(result, Found) else result


def test_every_call_on_a_projects_data_is_tested_below():
    assert set(PROJECT_CALLS) == reach.PROJECT_DATA


@pytest.mark.parametrize("method", sorted(PROJECT_CALLS))
def test_a_narrowed_call_resolves_its_reach_in_the_section_it_works_in(
    store, ids, section_hook, method
):
    """The reach is resolved, checked and used under one lock (one transaction for a write):
    a set changed by another thread can never fall between the check and the operation."""
    work = store.create_set("work", (ids["api"],))
    note, _ = store.add_note(ids["api"], "# Retries\n\nQuill retries.\n")
    span = store.note_spans(note.id)[0]
    page(store, ids["api"], "retries", "Quill retries three times.")
    view = store.narrow(projects=(work.id,))
    args = PROJECT_CALLS[method]({**ids, "note": note.id, "span": span.id})
    section_hook["hook"] = lambda: store.update_set(work.id, ())  # "api" leaves the reach
    section_hook["watching"] = True
    try:
        result = answered(getattr(view, method)(*args))
    except KeyError:
        result = None  # refused: "api" was out of reach when the call looked
    except PermissionError:  # erasure refuses any narrowed connection, once the project is in reach
        assert method in reach.ERASURES, method
        result = None
    section_hook["watching"] = False
    assert section_hook["sections"] == 1, f"{method} entered {section_hook['sections']} sections"
    assert not (section_hook["fired"] and result), "used a project after it left the reach"


@pytest.mark.parametrize("method", sorted(PROJECT_CALLS))
def test_a_persons_call_resolves_their_reach_in_the_section_it_works_in(
    store, ids, section_hook, method
):
    """A person's reach (T1) is resolved the same way: a project's rule changed by another
    thread can never fall between the check and the operation."""
    issuer = "https://id.example.test/pool-1"
    store.sign_in("sso", "sub-ada", "ada@example.test", "Ada", issuer=issuer, join="default")
    signed = store.sign_in(
        "sso", "sub-brook", "brook@example.test", "Brook", issuer=issuer, join="default"
    )
    brook = store.for_account(signed.account_id, signed.user.id)
    store.set_project_access(ids["api"], people={signed.user.id: "write"})  # Brook's own rule
    note, _ = store.add_note(ids["api"], "# Retries\n\nQuill retries.\n")
    span = store.note_spans(note.id)[0]
    page(store, ids["api"], "retries", "Quill retries three times.")
    args = PROJECT_CALLS[method]({**ids, "note": note.id, "span": span.id})
    section_hook["hook"] = lambda: store.set_project_access(  # "api" leaves Brook's reach
        ids["api"], everyone="none", people={}
    )
    section_hook["watching"] = True
    try:
        result = answered(getattr(brook, method)(*args))
    except (KeyError, PermissionError):
        result = None  # refused: "api" was out of reach when the call looked
    section_hook["watching"] = False
    assert section_hook["sections"] == 1, f"{method} entered {section_hook['sections']} sections"
    assert not (section_hook["fired"] and result), "used a project after it left the reach"


def test_a_narrowed_read_sees_one_moment_even_against_another_connection(
    store, ids, db, monkeypatch
):
    """Another process (a second Store on the file) changes the set right after the view
    resolved its reach. The read that follows sees the database as it was when the reach was
    resolved: nothing written after the project left the reach."""
    work = store.create_set("work", (ids["api"],))
    store.add_note(ids["api"], "# Before\n\nIn reach.\n")
    view = store.narrow(projects=(work.id,))
    other = Store(db)
    resolve = reach.ReachMethods._visible

    def resolved_then_changed(self, con):
        visible = resolve(self, con)
        monkeypatch.setattr(reach.ReachMethods, "_visible", resolve)  # once
        other.update_set(work.id, ())
        other.add_note(ids["api"], "# After\n\nWritten out of reach.\n")
        return visible

    monkeypatch.setattr(reach.ReachMethods, "_visible", resolved_then_changed)
    try:
        assert [c.title for c in view.list_notes(ids["api"])] == ["Before"]
    finally:
        other.close()
    assert view.list_notes(ids["api"]) == []  # and now it is out of reach


@pytest.mark.parametrize("bad", [{"access": "admin"}, {"projects": "br_x"}])
def test_narrow_refuses_what_is_not_a_reach(store, bad):
    with pytest.raises((ValueError, TypeError)):
        store.narrow(**bad)


def test_a_narrowed_search_ranks_with_its_reach_alone(store, ids):
    """bm25 weighs a term by how many indexed claims hold it. Ranked in the account's index, a
    connection limited to "api" could learn from its own scores how often a word occurs in
    projects it cannot see. A limited view ranks as an account holding only its projects would."""
    page(store, ids["api"], "retries", "Quill retries three times.", "The queue holds jobs.")
    page(store, ids["web"], "checkout", "Quill renders the checkout.")
    view = store.narrow(projects=(ids["api"], ids["web"]))
    alone = store.for_account(store.create_account("Alone").id)
    for name in ("api", "web"):
        twin = alone.create_project(name)
        for p in store.list_pages(ids[name]):
            alone.save_page(twin.id, p.slug, p.title, p.body, p.claims)

    def ranked(where, query, project_ids=None):
        return [(h.project, h.claim_id, h.score) for h in where.search(query, project_ids)]

    before = ranked(view, "quill queue")
    assert before == ranked(alone, "quill queue")
    for n in range(5):  # out of reach, the journal fills with the query's words
        page(store, ids["journal"], f"diary-{n}", "Quill quill queue.", "My cat Quill.")
    assert ranked(view, "quill queue") == before
    assert ranked(view, "quill", [ids["api"]]) == ranked(
        alone, "quill", [alone.get_project("api").id]
    )
    assert ranked(store, "quill queue") != before  # the whole account ranks with all of it
    reader = store.narrow(access="read")  # every project: the account's own index, unchanged
    assert ranked(reader, "quill queue") == ranked(store, "quill queue")
