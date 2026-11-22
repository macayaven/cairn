"""Unit tests for export, import and compaction (cairn.store.portable): the inner TDD loop
under tests/acceptance/test_h5_data.py. Every archive here is built in the test, from
synthetic notes."""

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from cairn.compiler import compile_note, render_body
from cairn.store import Store, portable

NOTE = "# Heron runbook\n\nHeron restarts the queue after 3 failures.\n\nHeron logs to stderr.\n"
LATER = "# Heron runbook\n\nHeron pages the on-call after 5 failures.\n"
OTHER = "# Kestrel notes\n\nKestrel batches writes every 10 s.\n"


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


def add(view: Store, project_id: str, text: str, **fields) -> None:
    note, _ = view.add_note(project_id, text, **fields)
    assert compile_note(view, note).status == "saved"


@pytest.fixture
def source(store) -> Store:
    view = store.for_account(store.create_account("Source").id)
    ops = view.create_project("ops", "operations", kind="repo")
    add(view, ops.id, NOTE, source_ref="session:1")
    team = view.create_project("team")
    add(view, team.id, OTHER)
    add(view, ops.id, LATER, source_ref="session:2")
    return view


def exported(view: Store) -> bytes:
    out = io.BytesIO()
    view.export(out)
    return out.getvalue()


def rebuilt(archive: bytes, change=None, extra: dict[str, bytes] | None = None, **options) -> bytes:
    """The archive with each entry passed through `change(name, data)` (None drops it), and
    `extra` entries added at the end."""
    source, out = zipfile.ZipFile(io.BytesIO(archive)), io.BytesIO()
    with zipfile.ZipFile(out, "w", **options) as target:
        for name in source.namelist():
            data = source.read(name)
            data = change(name, data) if change else data
            if data is not None:
                target.writestr(name, data)
        for name, data in (extra or {}).items():
            target.writestr(name, data)
    return out.getvalue()


def fresh_import(tmp_path, archive: bytes) -> Store:
    target = Store(tmp_path / "target.db")
    try:
        target.import_export(io.BytesIO(archive))
    except BaseException:
        target.close()
        raise
    return target


def refused(tmp_path, archive: bytes, match: str) -> None:
    db = tmp_path / "refused.db"
    target = Store(db)
    try:
        before = dump(db)
        with pytest.raises(ValueError, match=match):
            target.import_export(io.BytesIO(archive))
        assert dump(db) == before
    finally:
        target.close()


def statistics(view: Store, db: Path) -> tuple[list, list]:
    """The search statistics of a workspace, by project name: what save_page keeps."""
    con = sqlite3.connect(db)
    try:
        projects = con.execute(
            "SELECT b.name, s.claims, s.tokens FROM search_projects AS s"
            " JOIN projects AS b ON b.id = s.project_id WHERE s.account_id = ? ORDER BY b.name",
            (view.account_id,),
        ).fetchall()
        claims = con.execute(
            "SELECT b.name, c.slug, c.tokens FROM search_claims AS c"
            " JOIN projects AS b ON b.id = c.project_id WHERE c.account_id = ? ORDER BY c.row",
            (view.account_id,),
        ).fetchall()
    finally:
        con.close()
    return projects, claims


# What an import writes


def test_an_import_ranks_exactly_as_the_notes_did_where_they_were_written(source, db, tmp_path):
    target = fresh_import(tmp_path, exported(source))
    try:
        assert statistics(target, tmp_path / "target.db") == statistics(source, db)
        for query in ("heron failures", "kestrel", "after logs every"):
            ours = [
                (h.project, h.slug, h.claim_id, h.score) for h in source.search(query, limit=50)
            ]
            theirs = [(h.project, h.slug, h.claim_id, h.score) for h in target.search(query)]
            assert theirs == ours[:10] != []
    finally:
        target.close()


def test_an_import_is_one_audit_event_with_counts(source, tmp_path):
    target = fresh_import(tmp_path, exported(source))
    try:
        events = target.list_audit()
        assert [e.action for e in events] == ["workspace.imported"]
        assert events[0].detail == {
            "projects": 2,
            "notes": 3,
            "pages": 2,
            "fields_cut": 0,
            "kept_from_models": 0,
        }
        assert events[0].project is None
    finally:
        target.close()


LONG = {"title": "T" * 260, "topic": "P" * 201, "label": "L" * 90, "source_ref": "o" * 600,
        "via": "c" * 41}  # fmt: skip


def from_before_the_bounds(name: str, data: bytes) -> bytes:
    """Each note line of the ops project as an export written before FIELD_MAX may hold it."""
    if name != "projects/ops/notes.jsonl":
        return data
    lines = [json.loads(line) | LONG for line in data.decode().splitlines()]
    return "".join(json.dumps(line) + "\n" for line in lines).encode()


def test_an_old_export_imports_with_its_fields_cut_and_its_claims_still_cite_spans(
    source, tmp_path
):
    target = Store(tmp_path / "target.db")
    try:
        summary = target.import_export(
            io.BytesIO(rebuilt(exported(source), from_before_the_bounds))
        )
        assert summary.fields_cut == 2 * len(LONG)  # two ops notes, five fields each
        (event,) = target.list_audit()
        assert event.detail["fields_cut"] == 10
        ops = target.get_project("ops")
        for note in target.list_notes(ops.id):
            assert {f: getattr(note, f) for f in LONG} == {
                f: value[: portable.FIELD_MAX[f]] for f, value in LONG.items()
            }
            assert note.text in (NOTE, LATER)  # the text is never cut
        spans = {
            span.id: " ".join(span.text.split())
            for project in target.list_projects()
            for note in target.list_notes(project.id)
            for span in target.note_spans(note.id)
        }
        pages = [
            page for project in target.list_projects() for page in target.list_pages(project.id)
        ]
        claims = [claim for page in pages for claim in page.claims]
        assert claims
        for claim in claims:
            assert " ".join(claim.text.split()) in {spans[s] for s in claim.span_ids}
    finally:
        target.close()


def test_the_command_line_import_says_how_many_fields_it_cut(source, tmp_path, capsys):
    from cairn.server import main

    path = tmp_path / "export.zip"
    path.write_bytes(rebuilt(exported(source), from_before_the_bounds))
    db = str(tmp_path / "cli.db")
    assert main(["import", "--db", db, "--account", "acct_default", "--in", str(path)]) == 0
    out = capsys.readouterr().out.splitlines()
    said = "cut 10 note fields to their bounds (title, topic, label, source_ref, via)"
    assert said in out


def test_an_export_reads_one_moment_and_writes_nothing(source, db):
    before = dump(db)
    summary = source.export(io.BytesIO())
    assert (summary.projects, summary.notes, summary.pages) == (2, 3, 2)
    assert dump(db) == before
    assert source.list_audit()[0].action != "workspace.exported"


def test_an_empty_workspace_exports_and_imports(store, tmp_path):
    empty = store.for_account(store.create_account("Empty").id)
    archive = exported(empty)
    assert zipfile.ZipFile(io.BytesIO(archive)).namelist() == ["export.json"]
    target = fresh_import(tmp_path, archive)
    try:
        assert target.list_projects() == []
    finally:
        target.close()


def test_a_project_with_no_notes_travels_with_its_kind(store, tmp_path):
    view = store.for_account(store.create_account("Kinds").id)
    view.create_project("meetings", "weekly", kind="meetings")
    target = fresh_import(tmp_path, exported(view))
    try:
        (project,) = target.list_projects()
        assert (project.name, project.description, project.kind) == (
            "meetings",
            "weekly",
            "meetings",
        )
        assert target.list_notes(project.id) == [] and target.list_pages(project.id) == []
    finally:
        target.close()


# What an import refuses, with nothing written


def manifest_changed(archive: bytes, change) -> bytes:
    def edit(name, data):
        if name != "export.json":
            return data
        manifest = json.loads(data)
        change(manifest)
        return json.dumps(manifest).encode()

    return rebuilt(archive, edit)


def test_a_claim_may_cite_only_spans_of_its_own_projects_notes(source, tmp_path):
    archive = exported(source)
    team_span = next(
        span.id
        for note in source.list_notes(source.get_project("team").id)
        for span in source.note_spans(note.id)
    )

    def cite_elsewhere(name, data):
        if name != "projects/ops/pages.jsonl":
            return data
        lines = [json.loads(line) for line in data.splitlines()]
        lines[-1]["claims"][0]["passage_ids"] = [team_span]
        return "".join(json.dumps(line) + "\n" for line in lines).encode()

    refused(tmp_path, rebuilt(archive, cite_elsewhere), "passage")


def claims_changed(archive: bytes, change) -> bytes:
    """The archive with `change(claim)` applied to every claim of the ops project's pages."""

    def apply(name, data):
        if name != "projects/ops/pages.jsonl":
            return data
        lines = [json.loads(line) for line in data.splitlines()]
        for line in lines:
            for claim in line["claims"]:
                change(claim)
        return "".join(json.dumps(line) + "\n" for line in lines).encode()

    return rebuilt(archive, apply)


def test_a_claim_must_say_what_a_span_it_cites_says(source, tmp_path):
    """A claim cairn writes is the text of a span it cites; one word flipped is not."""
    flipped = {"done": False}

    def flip(claim):
        words = claim["text"].split(" ")
        if not flipped["done"] and len(words) > 1:
            words[1] = "never"
            claim["text"], flipped["done"] = " ".join(words), True

    refused(tmp_path, claims_changed(exported(source), flip), "does not say what")
    assert flipped["done"]


def test_a_claim_may_differ_from_its_span_in_whitespace_only(source, tmp_path):
    """White space the verbatim gate reads as the same: prose wrapped elsewhere, blank lines
    at its ends (review blocker 2: a tab that indents a line is structure, refused)."""

    def respace(claim):
        claim["text"] = "\n" + claim["text"].replace(" ", " \n ") + "\n"

    target = fresh_import(tmp_path, claims_changed(exported(source), respace))
    target.close()


def test_a_page_with_a_secret_is_refused_like_a_note(source, tmp_path):
    token = "ghp_" + "Zq7xR2" * 6

    def leak(line):  # in the title, which the body cairn writes repeats (H6c)
        title = f"{line['title']} token: {token}"
        # the note's heading is no longer the title: the body names it as a section
        old = line["title"]
        line["body"] = line["body"].replace(f"# {old}\n\n", f"# {title}\n\n## {old}\n\n", 1)
        line["title"] = title

    db = tmp_path / "refused.db"
    target = Store(db)
    try:
        archive = pages_changed(exported(source), leak, lambda n, last: n == 0)
        with pytest.raises(ValueError, match="github_token") as error:
            target.import_export(io.BytesIO(archive))
        assert token not in str(error.value)
    finally:
        target.close()


def test_a_workspaces_own_guard_policy_decides(source, tmp_path):
    """A note with an email address passes "secrets" and is refused by "secrets+personal"."""
    view = source
    project = view.create_project("people")
    add(view, project.id, "# Contacts\n\nWrite to ops@example.test for access.\n")
    archive = exported(view)
    db = tmp_path / "strict.db"
    strict = Store(db)
    try:
        strict.set_setting("guard.policy", "secrets+personal")
        before = dump(db)
        with pytest.raises(ValueError, match="email_address"):
            strict.import_export(io.BytesIO(archive))
        assert dump(db) == before
        strict.set_setting("guard.policy", "secrets")
        assert strict.import_export(io.BytesIO(archive)).notes == 4
    finally:
        strict.close()


@pytest.mark.parametrize(
    "change, match",
    [
        (lambda m: m.update(format="cairn-export", version=2), "version"),
        (lambda m: m["projects"][0].update(kind="no-such-kind"), "kind"),
        (lambda m: m["projects"][0].update(name="br_ops"), "project name"),
        (lambda m: m["projects"][0].update(notes=99), "notes"),
        (lambda m: m["projects"][1].update(pages=7), "pages"),
        (lambda m: m["projects"].append(dict(m["projects"][0])), "twice"),
        (lambda m: m.pop("projects"), "projects"),
    ],
)
def test_a_manifest_that_does_not_match_its_files_is_refused(source, tmp_path, change, match):
    refused(tmp_path, manifest_changed(exported(source), change), match)


def test_a_note_file_name_must_be_the_one_its_line_gives(source, tmp_path):
    def renumber(name, data):
        if name != "projects/ops/notes.jsonl":
            return data
        lines = [json.loads(line) for line in data.splitlines()]
        lines[0]["file"] = lines[0]["file"].replace("0001-", "0009-")
        return "".join(json.dumps(line) + "\n" for line in lines).encode()

    refused(tmp_path, rebuilt(exported(source), renumber), "file")


def test_page_versions_run_from_one_without_a_gap(source, tmp_path):
    def gap(name, data):
        if name != "projects/ops/pages.jsonl":
            return data
        lines = [json.loads(line) for line in data.splitlines()]
        lines[-1]["version"] = 3
        return "".join(json.dumps(line) + "\n" for line in lines).encode()

    refused(tmp_path, rebuilt(exported(source), gap), "version")


def test_a_page_file_must_hold_the_latest_body(source, tmp_path):
    def stale(name, data):
        return b"# something else\n" if name.endswith(".md") and "/pages/" in name else data

    refused(tmp_path, rebuilt(exported(source), stale), "latest")


def test_bad_json_is_a_value_error_that_names_the_file(source, tmp_path):
    def broken(name, data):
        return b"{not json" if name == "projects/team/notes.jsonl" else data

    refused(tmp_path, rebuilt(exported(source), broken), "projects/team/notes.jsonl")


def test_a_note_that_is_not_utf8_is_refused(source, tmp_path):
    archive = exported(source)
    source_zip = zipfile.ZipFile(io.BytesIO(archive))
    note = next(n for n in source_zip.namelist() if n.startswith("projects/team/notes/"))
    bad = b"\xff\xfe not utf-8\n"

    def swap(name, data):
        if name == note:
            return bad
        if name == "projects/team/notes.jsonl":
            line = json.loads(data)
            line["sha256"] = hashlib.sha256(bad).hexdigest()
            return (json.dumps(line) + "\n").encode()
        return data

    refused(tmp_path, rebuilt(archive, swap), "UTF-8")


# The bounds on a zip that is read


def test_more_entries_than_the_bound_are_refused_before_any_is_read(source, tmp_path, monkeypatch):
    archive = exported(source)
    monkeypatch.setattr(portable, "ARCHIVE_ENTRIES_MAX", 3)
    refused(tmp_path, archive, "entries")


def test_an_entry_larger_than_the_bound_is_refused(source, tmp_path, monkeypatch):
    archive = exported(source)
    largest = max(i.file_size for i in zipfile.ZipFile(io.BytesIO(archive)).infolist())
    monkeypatch.setattr(portable, "ENTRY_BYTES_MAX", largest - 1)
    refused(tmp_path, archive, "larger")


def test_an_entry_whose_header_understates_its_size_is_still_cut_at_the_bound(
    source, tmp_path, monkeypatch
):
    """The bound is enforced on the bytes read, not on what the archive claims."""
    monkeypatch.setattr(portable, "ENTRY_BYTES_MAX", 64)
    big = b"x" * 10_000

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        target.writestr("export.json", big)
    data = bytearray(out.getvalue())
    # Rewrite the uncompressed size in the central directory (offset 24 of its record) and
    # the local header (offset 22) to claim 10 bytes.
    central = data.rfind(b"PK\x01\x02")
    data[central + 24 : central + 28] = (10).to_bytes(4, "little")
    data[22:26] = (10).to_bytes(4, "little")
    refused(tmp_path, bytes(data), "larger|not a cairn export")


def test_the_total_read_is_bounded(source, tmp_path, monkeypatch):
    archive = exported(source)
    sizes = [i.file_size for i in zipfile.ZipFile(io.BytesIO(archive)).infolist()]
    monkeypatch.setattr(portable, "ARCHIVE_BYTES_MAX", sum(sizes) - 1)
    refused(tmp_path, archive, "in all")


def test_an_import_reads_at_most_256_mib_unless_told_to_read_more(source, tmp_path, monkeypatch):
    assert portable.ARCHIVE_BYTES_MAX == 256 * 1024 * 1024
    archive = exported(source)
    total = sum(i.file_size for i in zipfile.ZipFile(io.BytesIO(archive)).infolist())
    monkeypatch.setattr(portable, "ARCHIVE_BYTES_MAX", total - 1)
    refused(tmp_path, archive, "in all")
    target = Store(tmp_path / "raised.db")
    try:
        assert target.import_export(io.BytesIO(archive), max_bytes=total).notes == 3
        with pytest.raises(ValueError, match="at least one byte"):
            target.import_export(io.BytesIO(archive), max_bytes=0)
    finally:
        target.close()


def test_the_command_line_import_takes_a_bound(source, tmp_path, capsys):
    from cairn.server import main

    path = tmp_path / "export.zip"
    path.write_bytes(exported(source))
    db, account = str(tmp_path / "cli.db"), "acct_default"
    command = ["import", "--db", db, "--account", account, "--in", str(path)]
    assert main([*command, "--max-bytes", "100"]) == 1
    assert "larger than 100 bytes" in capsys.readouterr().err  # the archive file itself
    assert main([*command, "--max-bytes", str(64 * 1024 * 1024)]) == 0
    with pytest.raises(SystemExit):
        main([*command, "--max-bytes", "0"])


def plans_yaml(tmp_path, **caps) -> Path:
    path = tmp_path / "plans.yaml"
    fields = "".join(f", {name}: {cap}" for name, cap in caps.items())
    path.write_text(f"default: tiny\nplans:\n  tiny: {{label: Tiny{fields}}}\n")
    return path


def test_the_command_line_import_without_plans_says_none_is_applied(source, tmp_path, capsys):
    from cairn.server import main

    path = tmp_path / "export.zip"
    path.write_bytes(exported(source))
    db = str(tmp_path / "cli.db")
    assert main(["import", "--db", db, "--account", "acct_default", "--in", str(path)]) == 0
    err = capsys.readouterr().err.splitlines()
    assert [line for line in err if not line.startswith("cairn.audit: ")] == [
        "no plan is applied: this import caps nothing (--plans FILE holds it to one)"
    ]  # besides the audit lines, which every command but serve writes on stderr


@pytest.mark.parametrize(
    "caps, limit",
    [({"projects": 1}, "projects"), ({"notes_per_month": 2}, "notes_per_month")],
)
def test_the_command_line_import_holds_the_workspace_to_its_plan(
    source, tmp_path, capsys, caps, limit
):
    from cairn.server import main

    path = tmp_path / "export.zip"
    path.write_bytes(exported(source))  # two projects, three notes
    db = tmp_path / "cli.db"
    command = ["import", "--db", str(db), "--account", "acct_default", "--in", str(path)]
    assert main([*command, "--plans", str(plans_yaml(tmp_path, **caps))]) == 1
    printed = capsys.readouterr()
    assert f"nothing was imported: over the workspace's {limit} limit" in printed.err
    assert "no plan is applied" not in printed.err
    target = Store(db)
    try:
        assert target.list_projects() == []
    finally:
        target.close()
    assert main([*command, "--plans", str(plans_yaml(tmp_path, projects=2))]) == 0


def test_the_command_line_import_refuses_a_bad_plans_file(source, tmp_path, capsys):
    from cairn.server import main

    path = tmp_path / "export.zip"
    path.write_bytes(exported(source))
    bad = tmp_path / "bad.yaml"
    bad.write_text("plans: {}\n")
    db = tmp_path / "cli.db"
    command = ["import", "--db", str(db), "--account", "acct_default", "--in", str(path)]
    assert main([*command, "--plans", str(bad)]) == 2
    assert capsys.readouterr().err.startswith("cannot use those plans:")
    assert main([*command, "--plans", str(tmp_path / "missing.yaml")]) == 2


def refused_import(tmp_path, db: Path, archive: Path, why: str, capsys) -> None:
    """Run an import that is refused for `why`, and check its exit and its one line."""
    from cairn.server import main

    command = ["import", "--db", str(db), "--account", "acct_default", "--in", str(archive)]
    if why == "unknown workspace":
        command[4] = "acct_nobody"
    if why == "over the plan":
        command += ["--plans", str(plans_yaml(tmp_path, projects=1))]
    assert main(command) == 1
    err = capsys.readouterr().err
    assert ("no workspace" if why == "unknown workspace" else "nothing was imported") in err


@pytest.mark.parametrize("why", ["unknown workspace", "a malformed archive", "over the plan"])
def test_an_import_refused_into_a_new_database_leaves_nothing_it_made(
    source, tmp_path, capsys, why
):
    """The database is created when missing, and a refused import removes it again, with
    every directory this run made for it: nothing written, not even an empty database."""
    archive = tmp_path / "export.zip"
    archive.write_bytes(b"not a zip" if why == "a malformed archive" else exported(source))
    db = tmp_path / "new" / "deeper" / "b.db"
    refused_import(tmp_path, db, archive, why, capsys)
    assert not (tmp_path / "new").exists()
    # A directory that was there already stays, and so does what else it holds.
    (tmp_path / "kept").mkdir()
    (tmp_path / "kept" / "other.txt").write_text("x")
    db = tmp_path / "kept" / "b.db"
    refused_import(tmp_path, db, archive, why, capsys)
    assert sorted(p.name for p in (tmp_path / "kept").iterdir()) == ["other.txt"]


@pytest.mark.parametrize("why", ["unknown workspace", "a malformed archive", "over the plan"])
def test_an_import_refused_into_a_database_that_was_there_leaves_it_as_it_was(
    source, store, db, tmp_path, capsys, why
):
    archive = tmp_path / "export.zip"
    archive.write_bytes(b"not a zip" if why == "a malformed archive" else exported(source))
    before = dump(db)
    refused_import(tmp_path, db, archive, why, capsys)
    assert db.is_file() and dump(db) == before


def test_the_archive_file_itself_is_bounded(source, tmp_path, monkeypatch):
    archive = exported(source)
    monkeypatch.setattr(portable, "ARCHIVE_BYTES_MAX", len(archive) - 1)
    refused(tmp_path, archive, "archive is larger")


@pytest.mark.parametrize(
    "name",
    [
        "../escape.md",
        "/etc/passwd",
        "projects/ops/notes/../../export.json",
        "projects/ops/secrets.txt",
        "projects/.hidden/notes.jsonl",
        "projects/ops/",
        "projects/ops/pages/Upper.md",
        "projects/ops/notes/1-cap_000000000000.md",
        "README.md",
    ],
)
def test_a_name_outside_the_layout_is_refused(source, tmp_path, name):
    refused(tmp_path, rebuilt(exported(source), extra={name: b"x"}), "layout")


def test_a_name_given_twice_is_refused(source, tmp_path):
    archive = exported(source)
    with pytest.warns(UserWarning):  # zipfile itself warns of a duplicate name
        twice = rebuilt(archive, extra={"projects/ops/pages.jsonl": b""})
    refused(tmp_path, twice, "twice")


def test_a_note_file_no_line_names_is_refused(source, tmp_path):
    stray = {"projects/ops/notes/0099-cap_0123456789ab.md": b"# Stray\n\nNobody lists me.\n"}
    refused(tmp_path, rebuilt(exported(source), extra=stray), "no line")


def test_a_missing_note_file_is_refused(source, tmp_path):
    def drop_first_note(name, data):
        return None if name.startswith("projects/ops/notes/0001-") else data

    refused(tmp_path, rebuilt(exported(source), drop_first_note), "missing")


def test_only_stored_and_deflated_entries_are_read(source, tmp_path):
    archive = rebuilt(exported(source), compression=zipfile.ZIP_BZIP2)
    refused(tmp_path, archive, "compression")


def test_what_is_not_a_zip_is_a_value_error(tmp_path):
    refused(tmp_path, b"PK\x03\x04 truncated", "not a cairn export")
    refused(tmp_path, b"", "not a cairn export")


# Who may export and import


def test_a_narrowed_view_may_neither_export_nor_import(source, tmp_path):
    archive = exported(source)
    ops = source.get_project("ops").id
    for view in (source.narrow((ops,)), source.narrow(access="read")):
        with pytest.raises(PermissionError):
            view.export(io.BytesIO())
        with pytest.raises(PermissionError):
            view.import_export(io.BytesIO(archive))


def test_a_view_of_a_deleted_workspace_imports_nothing(store, source, tmp_path):
    archive = exported(source)
    gone = store.for_account(store.create_account("Gone").id)
    store.delete_account(gone.account_id)
    target_db = tmp_path / "t.db"
    other = Store(target_db)
    try:
        orphan = other.for_account(other.create_account("Soon gone").id)
        other.delete_account(orphan.account_id)
        before = dump(target_db)
        with pytest.raises(KeyError):
            orphan.import_export(io.BytesIO(archive))
        assert dump(target_db) == before
    finally:
        other.close()


# Compaction


def test_compact_belongs_to_the_store_that_opened_the_database(store, source):
    with pytest.raises(PermissionError):
        source.compact()
    with pytest.raises(PermissionError):
        store.for_account(source.account_id).compact()
    store.compact()


def test_compact_says_so_when_another_reader_keeps_the_log(store, db, source):
    store._con.execute("PRAGMA busy_timeout = 0")  # refuse at once, not after 5 s of retries
    reader = sqlite3.connect(db)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM notes").fetchone()
        with pytest.raises(sqlite3.OperationalError):
            store.compact()
    finally:
        reader.close()
    store.compact()  # with the reader gone it runs
    assert (db.parent / (db.name + "-wal")).stat().st_size == 0


def test_compact_works_on_a_database_in_memory():
    store = Store(":memory:")
    try:
        store.compact()
    finally:
        store.close()


# The command line


def test_export_and_compact_never_create_a_database(tmp_path, capsys):
    from cairn.server import main

    missing = tmp_path / "missing.db"
    out = tmp_path / "out.zip"
    assert (
        main(["export", "--db", str(missing), "--account", "acct_default", "--out", str(out)]) == 1
    )
    assert main(["compact", "--db", str(missing)]) == 1
    assert not missing.exists() and not out.exists()
    assert "no database" in capsys.readouterr().err


def test_an_import_refused_by_the_guard_names_the_rule_and_not_the_text(source, tmp_path, capsys):
    from cairn.server import main

    token = "ghp_" + "Zq7xR2" * 6
    archive = tmp_path / "leaky.zip"
    archive.write_bytes(with_a_token(exported(source), token))
    db = tmp_path / "target.db"
    Store(db).close()
    assert main(["import", "--db", str(db), "--account", "acct_default", "--in", str(archive)]) == 1
    printed = capsys.readouterr()
    assert "github_token" in printed.err and token not in printed.out + printed.err


def test_the_command_line_prints_counts_and_paths(source, db, tmp_path, capsys):
    from cairn.server import main

    out = tmp_path / "source.zip"
    assert main(["export", "--db", str(db), "--account", source.account_id, "--out", str(out)]) == 0
    assert str(out) in capsys.readouterr().out
    target = tmp_path / "target.db"
    assert main(["import", "--db", str(target), "--account", "acct_default", "--in", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "2 projects, 3 notes, 2 pages" in printed
    # the archive carries no access rules: the command says what the imported projects became
    assert "Every imported project is private: only the workspace's owners reach it" in printed
    assert main(["compact", "--db", str(target)]) == 0
    assert str(target) in capsys.readouterr().out


def test_a_malformed_file_is_refused_as_malformed_and_never_quoted(source, tmp_path):
    """Nothing of a file that does not parse is written, so it is not screened: the refusal
    names the file, never its text, whatever the workspace's policy."""
    token = "ghp_" + "Zq7xR2" * 6

    def append(name, data):
        return data + f"github_token = {token}\n".encode() if name.endswith(".jsonl") else data

    archive = rebuilt(exported(source), append)
    for policy in ("secrets", "off"):
        db = tmp_path / f"{policy}.db"
        target = Store(db)
        try:
            target.set_setting("guard.policy", policy)
            before = dump(db)
            with pytest.raises(ValueError, match="not valid JSON") as error:
                target.import_export(io.BytesIO(archive))
            assert token not in str(error.value) and dump(db) == before
        finally:
            target.close()


def test_what_add_note_accepts_an_import_accepts_whatever_json_makes_of_it(store, tmp_path):
    """JSON writes "ó" as an escape with digits in it: screening an index file as it stands
    would read "token: autenticaci\\u00f3n-b\\u00e1sica" as an assigned secret."""
    view = store.for_account(store.create_account("Escapes").id)
    project = view.create_project("auth")
    add(view, project.id, "# Auth\n\nThe scheme is token: autenticación-básica.\n",
        label="token: autenticación-básica")  # fmt: skip
    target = fresh_import(tmp_path, exported(view))
    try:
        assert target.list_notes(target.get_project("auth").id)[0].label == (
            "token: autenticación-básica"
        )
    finally:
        target.close()


def test_a_secret_only_a_decoded_field_shows_is_refused(store, tmp_path):
    """Written where the policy was off, refused where it is not: JSON's escapes hide it from
    the index file's text, never from the field."""
    view = store.for_account(store.create_account("Lax").id)
    view.set_setting("guard.policy", "off")
    project = view.create_project("ops")
    add(view, project.id, "# Deploy\n\nDeploy on Fridays.\n", label='password="Abc123456789xyz"')
    refused(tmp_path, exported(view), "assigned_secret")


def with_a_token(archive: bytes, token: str) -> bytes:
    """The archive with a token added to the ops project's first note, its checksum made to
    match: well formed, so only the guard can refuse it."""
    note = "projects/ops/notes/0001-"

    def leak(name, data):
        if name.startswith(note):
            return data + f"\ngithub_token = {token}\n".encode()
        if name == "projects/ops/notes.jsonl":
            lines = [json.loads(line) for line in data.splitlines()]
            leaked = zipfile.ZipFile(io.BytesIO(archive)).read("projects/ops/" + lines[0]["file"])
            leaked += f"\ngithub_token = {token}\n".encode()
            lines[0]["sha256"] = hashlib.sha256(leaked).hexdigest()
            return "".join(json.dumps(line) + "\n" for line in lines).encode()
        return data

    return rebuilt(archive, leak)


def test_a_token_added_to_a_note_with_its_checksum_made_to_match_is_refused(source, tmp_path):
    token = "ghp_" + "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zAaB3d"
    db = tmp_path / "refused.db"
    target = Store(db)
    try:
        before = dump(db)
        with pytest.raises(ValueError, match="github_token") as error:
            target.import_export(io.BytesIO(with_a_token(exported(source), token)))
        assert token not in str(error.value) and dump(db) == before
    finally:
        target.close()


def test_an_export_after_compaction_is_the_same_and_imports(store, source, tmp_path):
    """Pages are exported in the order they were saved (page_versions' rowid), which VACUUM
    may renumber but keeps in order."""
    before = zipfile.ZipFile(io.BytesIO(exported(source)))
    store.compact()
    archive = exported(source)
    after = zipfile.ZipFile(io.BytesIO(archive))
    assert after.namelist() == before.namelist()
    for name in before.namelist():
        if name != "export.json":
            assert after.read(name) == before.read(name), name
    fresh_import(tmp_path, archive).close()


# H6c, finding 4: a page as cairn would have written it


def pages_changed(archive: bytes, change, which=lambda n, last: True) -> bytes:
    """The archive with `change(line)` applied to the ops project's page lines that `which(n,
    last)` picks (n from 0; last: the page's latest), and each page file kept equal to its
    latest body, so only the rule under test can refuse it."""
    lines = [
        json.loads(line)
        for line in zipfile.ZipFile(io.BytesIO(archive))
        .read("projects/ops/pages.jsonl")
        .splitlines()
    ]
    latest = {line["slug"]: n for n, line in enumerate(lines)}
    for n, line in enumerate(lines):
        if which(n, latest[line["slug"]] == n):
            change(line)
    bodies = {line["slug"]: line["body"] for line in lines}

    def apply(name, data):
        if name == "projects/ops/pages.jsonl":
            return "".join(json.dumps(line) + "\n" for line in lines).encode()
        if name.startswith("projects/ops/pages/"):
            return bodies[name.removeprefix("projects/ops/pages/").removesuffix(".md")].encode()
        return data

    return rebuilt(archive, apply)


def test_a_page_body_must_be_what_cairn_writes_of_its_claims(source, tmp_path):
    def link(line):
        line["body"] += "\n[Reset your password](https://login.example.test)\n"

    refused(tmp_path, pages_changed(exported(source), link), "body")
    older = pages_changed(exported(source), link, lambda n, last: not last)
    refused(tmp_path, older, "body")  # every version, not only the latest


def test_a_body_that_differs_in_whitespace_only_imports_as_cairn_writes_it(source, tmp_path):
    def respace(line):
        line["body"] = line["body"].replace("\n\n", "\n\n\n").replace(" ", "  ")

    target = fresh_import(tmp_path, pages_changed(exported(source), respace))
    try:
        for project in source.list_projects():
            (copy,) = [b for b in target.list_projects() if b.name == project.name]
            for page in source.list_pages(project.id):
                assert target.get_page(copy.id, page.slug).body == page.body
    finally:
        target.close()


@pytest.mark.parametrize(
    "metrics",
    [
        {"claims": {"nested": 1}},
        {"claims": [1, 2]},
        {"claims": True},
        {"claims": None},
        {"claims": float("nan")},
        {"claims": float("inf")},
        {"arranged_by": "x" * (portable.METRIC_TEXT_MAX + 1)},
        {"n" * (portable.METRIC_NAME_MAX + 1): 1},
        {"": 1},
        {f"m{i}": i for i in range(portable.METRICS_MAX + 1)},
    ],
    ids=lambda m: next(iter(m))[:12] + ":" + type(next(iter(m.values()))).__name__,
)
def test_a_versions_metrics_are_a_short_flat_map_of_numbers_and_short_words(
    source, tmp_path, metrics
):
    def swap(line):
        line["metrics"] = metrics

    refused(tmp_path, pages_changed(exported(source), swap), "metrics")


def test_metrics_cairn_writes_import(source, tmp_path):
    def most(line):
        line["metrics"] = {
            **{f"m{i}": i for i in range(portable.METRICS_MAX - 2)},
            "retention": 0.5,
            "arranged_by": "x" * portable.METRIC_TEXT_MAX,
        }

    fresh_import(tmp_path, pages_changed(exported(source), most)).close()


def test_a_page_title_longer_than_cairn_writes_is_cut_and_its_body_follows(source, tmp_path):
    bound = portable.FIELD_MAX["topic"]  # a page's title is its note's topic or title

    def lengthen(line):
        title = "Heron " + "runbook " * 60
        # the note's heading is no longer the title: the body names it as a section
        old = line["title"]
        line["body"] = line["body"].replace(f"# {old}\n\n", f"# {title}\n\n## {old}\n\n", 1)
        line["title"] = title

    target = fresh_import(tmp_path, pages_changed(exported(source), lengthen))
    try:
        ops = target.get_project("ops")
        for page in target.list_pages(ops.id):
            assert len(page.title) <= bound
            assert page.body.startswith(f"# {page.title}\n")
    finally:
        target.close()


def test_export_never_follows_a_link_at_its_out_path(store, db, tmp_path, capsys):
    """A link at --out to a file not there yet: the export refuses it and makes nothing."""
    from cairn.server import main

    target = tmp_path / "elsewhere" / "taken.zip"
    target.parent.mkdir()
    link = tmp_path / "out.zip"
    link.symlink_to(target)
    assert main(["export", "--db", str(db), "--account", "acct_default", "--out", str(link)]) == 1
    assert "already exists: export never overwrites a file" in capsys.readouterr().err
    assert not target.exists() and link.is_symlink()


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (OSError(28, "No space left on device", "/elsewhere/spill.tmp"), "No space left on device"),
        (RuntimeError("the export broke at /elsewhere/spill.tmp: Heron"), "RuntimeError"),
    ],
    ids=["the disk refuses", "anything else"],
)
def test_an_export_that_fails_says_so_in_one_line_and_leaves_no_file(
    store, db, tmp_path, monkeypatch, capsys, error, said
):
    """A failure the command did not expect is one plain line, exit 1: the system's reason
    for an OSError, the error's class otherwise, never its message (which may name a path
    the operator did not give, or quote a note). The file it had begun is removed."""
    from cairn.server import main

    def broken(self, out):
        out.write(b"PK half an archive")
        raise error

    monkeypatch.setattr(portable.PortableMethods, "export", broken)
    out = tmp_path / "out.zip"
    assert main(["export", "--db", str(db), "--account", "acct_default", "--out", str(out)]) == 1
    printed = capsys.readouterr()
    assert (printed.out, printed.err) == ("", f"cannot export to {out}: {said}\n")
    assert not out.exists()


def test_an_export_stopped_by_the_operator_stops_and_leaves_no_file(
    store, db, tmp_path, monkeypatch
):
    from cairn.server import main

    def stopped(self, out):
        out.write(b"PK half an archive")
        raise KeyboardInterrupt

    monkeypatch.setattr(portable.PortableMethods, "export", stopped)
    out = tmp_path / "out.zip"
    with pytest.raises(KeyboardInterrupt):
        main(["export", "--db", str(db), "--account", "acct_default", "--out", str(out)])
    assert not out.exists()


def test_a_compaction_another_process_kept_from_finishing_exits_1_and_says_so(
    store, db, monkeypatch, capsys
):
    from cairn.server import main

    def kept(self):
        raise portable.CompactIncomplete("another connection is reading the log")

    monkeypatch.setattr(portable.PortableMethods, "compact", kept)
    assert main(["compact", "--db", str(db)]) == 1
    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err == f"{db} was not fully compacted: another connection is reading the log\n"


# N1: topic keys travel with the archive


def keyed_lines(change):
    """An entry change for `rebuilt`: each JSON line of an index passed through `change(name,
    line)`, which edits it in place."""

    def edit(name: str, data: bytes) -> bytes:
        if not name.endswith(".jsonl"):
            return data
        lines = [json.loads(line) for line in data.decode().splitlines()]
        for line in lines:
            change(name, line)
        return "".join(json.dumps(line) + "\n" for line in lines).encode()

    return edit


def page_keys(view: Store, db: Path) -> dict[tuple[str, str], str]:
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT b.name, k.slug, k.topic_key FROM page_keys AS k"
            " JOIN projects AS b ON b.id = k.project_id WHERE b.account_id = ?",
            (view.account_id,),
        ).fetchall()
    finally:
        con.close()
    return {(project, slug): key for project, slug, key in rows}


def test_an_archive_carries_each_page_s_and_each_note_s_topic_key(source, db, tmp_path):
    ops = source.get_project("ops")
    page = source.get_page(ops.id, "heron-runbook")
    spans = source.get_spans([i for claim in page.claims for i in claim.span_ids])
    body = render_body("Renamed", page.claims, spans)
    source.save_page(ops.id, page.slug, "Renamed", body, page.claims)  # the key stays
    archive = zipfile.ZipFile(io.BytesIO(exported(source)))
    lines = archive.read("projects/ops/pages.jsonl").decode().splitlines()
    assert {json.loads(line)["topic_key"] for line in lines} == {"heron runbook"}
    notes = archive.read("projects/ops/notes.jsonl").decode().splitlines()
    assert [json.loads(line)["topic_key"] for line in notes] == ["heron runbook"] * 2
    target = fresh_import(tmp_path, exported(source))
    try:
        assert page_keys(target, tmp_path / "target.db") == page_keys(source, db)
        moved = target.get_project("ops")
        assert target.topic_page(moved.id, "heron runbook") == "heron-runbook"
        assert {c.topic_key for c in target.list_notes(moved.id)} == {"heron runbook"}
    finally:
        target.close()


def test_an_archive_without_keys_keys_pages_by_title_and_notes_by_their_page(source, tmp_path):
    def unkeyed(name, line):
        line.pop("topic_key", None)

    target = fresh_import(tmp_path, rebuilt(exported(source), keyed_lines(unkeyed)))
    try:
        ops = target.get_project("ops")
        assert target.topic_page(ops.id, "heron runbook") == "heron-runbook"
        assert {c.topic_key for c in target.list_notes(ops.id)} == {"heron runbook"}
    finally:
        target.close()


def test_an_archive_carries_whether_each_note_was_stored_before_n1(source, tmp_path):
    archive = zipfile.ZipFile(io.BytesIO(exported(source)))
    notes = archive.read("projects/ops/notes.jsonl").decode().splitlines()
    assert [json.loads(line)["before_topics"] for line in notes] == [False, False]

    def marked(name, line):
        if name.endswith("notes.jsonl"):
            line["before_topics"] = True

    target = fresh_import(tmp_path, rebuilt(exported(source), keyed_lines(marked)))
    try:
        ops = target.get_project("ops")
        assert {c.before_topics for c in target.list_notes(ops.id)} == {True}
        again = zipfile.ZipFile(io.BytesIO(exported(target)))
        lines = again.read("projects/ops/notes.jsonl").decode().splitlines()
        assert {json.loads(line)["before_topics"] for line in lines} == {True}
    finally:
        target.close()


def test_every_note_of_an_archive_written_before_n1_was_stored_before_it(source, tmp_path):
    def older(name, line):
        line.pop("topic_key", None)
        line.pop("before_topics", None)

    target = fresh_import(tmp_path, rebuilt(exported(source), keyed_lines(older)))
    try:
        ops = target.get_project("ops")
        assert {c.before_topics for c in target.list_notes(ops.id)} == {True}
    finally:
        target.close()


def test_a_note_line_with_a_topic_key_and_no_mark_was_stored_after_n1(source, tmp_path):
    def unmarked(name, line):
        line.pop("before_topics", None)

    target = fresh_import(tmp_path, rebuilt(exported(source), keyed_lines(unmarked)))
    try:
        ops = target.get_project("ops")
        assert {c.before_topics for c in target.list_notes(ops.id)} == {False}
    finally:
        target.close()


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (
            lambda name, line: name.endswith("notes.jsonl") and line.update(before_topics=1),
            "before_topics on",
        ),
        (lambda name, line: line.update(topic_key="x" * 801), "not a topic key"),
        (lambda name, line: line.update(topic_key=7), "not a topic key"),
        (
            lambda name, line: (
                name.endswith("pages.jsonl")
                and line.update(topic_key=f"{line['slug']} {line['version']}")
            ),
            "two topic keys",
        ),
    ],
)
def test_an_archive_whose_keys_do_not_hold_together_is_refused(source, tmp_path, change, match):
    refused(tmp_path, rebuilt(exported(source), keyed_lines(change)), match)


def test_a_note_keeps_its_key_when_its_page_has_another(source, tmp_path):
    def other(name, line):
        if name.endswith("notes.jsonl"):
            line["topic_key"] = "other"

    target = fresh_import(tmp_path, rebuilt(exported(source), keyed_lines(other)))
    try:
        ops = target.get_project("ops")
        assert target.topic_page(ops.id, "heron runbook") == "heron-runbook"
        assert {c.topic_key for c in target.list_notes(ops.id)} == {"other"}
    finally:
        target.close()


def test_an_archive_with_two_pages_of_one_key_is_refused(store, tmp_path):
    view = store.for_account(store.create_account("Two").id)
    ops = view.create_project("ops")
    add(view, ops.id, NOTE)
    add(view, ops.id, OTHER)

    def one_key(name, line):
        line["topic_key"] = "one"

    refused(tmp_path, rebuilt(exported(view), keyed_lines(one_key)), "one topic key")
