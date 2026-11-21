"""H5 acceptance tests (frozen): a workspace's data is its owner's to take and to erase.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/store/__init__.py and src/cairn/server.py.

Export
- `view.export(out)` writes a zip of everything the workspace's projects hold to a binary
  file object and returns an ExportSummary (projects, notes, pages). Format version 1:
    export.json                              {"format": "cairn-export", "version": 1,
                                              "exported_at", "workspace", "projects": [{"id",
                                              "name", "description", "kind", "created_at",
                                              "notes", "pages"}]}
    projects/<name>/notes/<NNNN>-<note id>.md   each note exactly as it was written
    projects/<name>/notes.jsonl             one line per note, oldest first: id, title,
                                              topic, label, source_ref, via,
                                              created_at, file, sha256
    projects/<name>/pages/<slug>.md            each page's latest body
    projects/<name>/pages.jsonl                one line per page version: slug, version,
                                              title, body, claims, metrics, created_at
  Keys, sessions, grants and the audit log are not exported. Exporting never writes to the
  database. Two exports of the same data differ only in export.json's `exported_at`.
- `view.import_export(source)` restores such a zip into the workspace, all or nothing, and
  returns the same summary. Notes keep their ids, so every claim still cites the same
  spans; projects keep their kind; pages keep every version; search works at once. Every
  note passes the workspace's note guard on the way in, as any note does. ValueError,
  with nothing written: a note the guard refuses,
  an unknown format or version, a project name the workspace already uses, a note id the
  database already holds, or a note file that does not match its sha256.

Erasure
- The database zeroes what it deletes (SQLite's secure_delete, and FTS5's own where the
  library has it). `delete_account` then truncates the write-ahead log when nothing else
  holds the file, so the text of the workspace's notes, its pages' bodies and its search
  index are gone from the database's files at once. Its cost grows with the workspace,
  not with the database.
- What can outlive that: short values SQLite keeps as index keys, such as a page's slug
  (made from a note's title) or a project's name, can remain in index pages that other
  workspaces still use. `store.compact()` rewrites the database file (VACUUM), truncates
  the write-ahead log and removes those too, and gives the freed space back to the disk.
  It is the operator's to run; `serve --compact-every HOURS` (24 by default, 0 for never)
  runs it in the background when a workspace has been deleted since the last time.
  Nothing in the web view runs it.

The service
- GET /account (web session): the workspace's name, a link to GET /account/export, and a
  form posting to /account/delete. The export downloads as a zip
  (`Content-Disposition: attachment`); GET /api/account/export does the same for an API
  key. A connection limited to some projects or to reading cannot export (403).
- POST /account/delete with `confirm` equal to the workspace's name deletes it, as
  `delete_account` does, ends the session and redirects to /. Any
  other `confirm` is 400. The default workspace cannot be deleted this way (403, with the
  reason).
- Command line: `export --db PATH --account ID --out FILE` (refuses to overwrite, mode
  0600), `import --db PATH --account ID --in FILE`, `compact --db PATH`.
"""

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.server import create_app, main
from cairn.store import DEFAULT_ACCOUNT_ID, Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RUNBOOK = (FIXTURES / "runbook_synthetic.md").read_text(encoding="utf-8")
SLUG = "quill-and-lumen-runbook"
FOLLOWUP = "# Quill and Lumen runbook\n\nQuill retries 3 times before failing.\n"
POLICY = (
    "# Retry policy\n\nLumen never retries: it reports and stops.\n\n"
    "```bash\nlumen run --once\n```\n"
)


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def add(view: Store, project_id: str, text: str, **fields) -> None:
    note, _ = view.add_note(project_id, text, **fields)
    assert compile_note(view, note).status == "saved"


def populate(view: Store) -> dict:
    """Two projects: one with a page that has two versions, one with a single note."""
    ops = view.create_project("ops", "operations", kind="repo")
    add(view, ops.id, RUNBOOK, source_ref="session:1", label="runbook")
    add(view, ops.id, FOLLOWUP, source_ref="session:2")
    notes = view.create_project("notes", "")
    add(view, notes.id, POLICY, topic="Policies", via="web")
    return {"ops": ops, "notes": notes}


def everything(view: Store) -> dict:
    """All a workspace's projects hold, in a form two workspaces can be compared by."""
    out = {}
    for project in view.list_projects():
        notes = view.list_notes(project.id)
        pages = {}
        for page in view.list_pages(project.id):
            versions = view.page_versions(project.id, page.slug)
            pages[page.slug] = [
                view.get_page(project.id, page.slug, v).model_dump(exclude={"project_id"})
                for v in versions
            ]
        out[project.name] = {
            "description": project.description,
            "kind": project.kind,
            "notes": [c.model_dump(exclude={"project_id"}) for c in notes],
            "spans": [[s.model_dump() for s in view.note_spans(c.id)] for c in notes],
            "pages": pages,
        }
    return out


def hits(view: Store, query: str) -> list:
    return [(h.project, h.slug, h.claim_id, h.text) for h in view.search(query, limit=50)]


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


@pytest.fixture
def ada(store):
    view = store.for_account(store.create_account("Ada").id)
    populate(view)
    return view


def exported(view: Store) -> bytes:
    out = io.BytesIO()
    view.export(out)
    return out.getvalue()


# Export


def test_an_export_holds_every_note_and_every_page_version(ada, db):
    before = dump(db)
    out = io.BytesIO()
    summary = ada.export(out)
    assert (summary.projects, summary.notes, summary.pages) == (2, 3, 2)
    assert dump(db) == before  # exporting writes nothing

    archive = zipfile.ZipFile(io.BytesIO(out.getvalue()))
    assert archive.testzip() is None
    manifest = json.loads(archive.read("export.json"))
    assert (manifest["format"], manifest["version"], manifest["workspace"]) == (
        "cairn-export",
        1,
        "Ada",
    )
    assert manifest["exported_at"]
    listed = {
        b["name"]: (b["notes"], b["pages"], b["description"], b["kind"])
        for b in manifest["projects"]
    }
    assert listed == {"ops": (2, 1, "operations", "repo"), "notes": (1, 1, "", "general")}

    lines = [json.loads(line) for line in archive.read("projects/ops/notes.jsonl").splitlines()]
    assert [c["source_ref"] for c in lines] == ["session:1", "session:2"]  # oldest first
    first = lines[0]
    assert first["label"] == "runbook" and first["file"].startswith("notes/0001-cap_")
    note = archive.read("projects/ops/" + first["file"])
    assert note.decode() == RUNBOOK and hashlib.sha256(note).hexdigest() == first["sha256"]

    versions = [json.loads(line) for line in archive.read("projects/ops/pages.jsonl").splitlines()]
    assert [(v["slug"], v["version"]) for v in versions] == [(SLUG, 1), (SLUG, 2)]
    assert versions[1]["claims"] and versions[1]["claims"][0]["passage_ids"]
    latest = ada.get_page(ada.get_project("ops").id, SLUG)
    assert archive.read(f"projects/ops/pages/{SLUG}.md").decode() == latest.body
    assert json.loads(archive.read("projects/notes/notes.jsonl"))["via"] == "web"

    names = archive.namelist()
    assert not any("key" in n or "session" in n or "audit" in n for n in names)
    text = out.getvalue()
    ada.create_key("laptop")
    again = zipfile.ZipFile(io.BytesIO(exported(ada)))
    assert again.namelist() == names
    for name in names:
        if name != "export.json":
            assert again.read(name) == archive.read(name), name
    assert len(text) > 0


def test_an_export_restores_into_another_install_exactly(ada, tmp_path):
    archive = exported(ada)
    other = Store(tmp_path / "elsewhere.db")
    try:
        target = other.for_account(other.create_account("Ada, moved").id)
        summary = target.import_export(io.BytesIO(archive))
        assert (summary.projects, summary.notes, summary.pages) == (2, 3, 2)
        assert everything(target) == everything(ada)
        for query in ("quill", "lumen retries", "run --once"):
            assert hits(target, query) == hits(ada, query) != []

        # It is a working workspace: a new note on the same topic adds version 3.
        ops = target.get_project("ops")
        add(target, ops.id, "# Quill and Lumen runbook\n\nQuill logs to stderr.\n")
        assert target.get_page(ops.id, SLUG).version == 3
        assert other.list_projects() == []  # nothing landed in the default workspace
    finally:
        other.close()


def test_an_import_is_all_or_nothing(ada, store, db, tmp_path):
    archive = exported(ada)
    brook = store.for_account(store.create_account("Brook").id)
    before = dump(db)
    with pytest.raises(ValueError):  # these notes already exist in this database
        brook.import_export(io.BytesIO(archive))
    with pytest.raises(ValueError):  # and the workspace they came from has these projects
        ada.import_export(io.BytesIO(archive))
    assert dump(db) == before and brook.list_projects() == []

    def rewritten(change) -> bytes:
        source, out = zipfile.ZipFile(io.BytesIO(archive)), io.BytesIO()
        with zipfile.ZipFile(out, "w") as target:
            for name in source.namelist():
                target.writestr(name, change(name, source.read(name)))
        return out.getvalue()

    def other_version(name, data):
        if name == "export.json":
            return json.dumps({**json.loads(data), "version": 99})
        return data

    def tampered_note(name, data):
        return data + b"\nan extra line\n" if "/notes/" in name else data

    def not_cairns(name, data):
        return json.dumps({"format": "something-else"}) if name == "export.json" else data

    def with_a_secret() -> bytes:
        """The same export with a token added to the end of one note, checksum made to match."""
        source, out = zipfile.ZipFile(io.BytesIO(archive)), io.BytesIO()
        note_name = next(n for n in source.namelist() if "/notes/" in n and n.endswith(".md"))
        token = "ghp_" + "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zAaB3d"
        leaked = source.read(note_name) + f"\ngithub_token = {token}\n".encode()
        index_name = note_name.rsplit("/notes/", 1)[0] + "/notes.jsonl"
        assert index_name in source.namelist(), index_name
        with zipfile.ZipFile(out, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == note_name:
                    data = leaked
                elif name == index_name:
                    lines = [json.loads(line) for line in data.splitlines()]
                    matched = [line for line in lines if note_name.endswith(line["file"])]
                    assert len(matched) == 1, note_name
                    matched[0]["sha256"] = hashlib.sha256(leaked).hexdigest()
                    data = "".join(json.dumps(line) + "\n" for line in lines).encode()
                target.writestr(name, data)
        return out.getvalue()

    # In a fresh database nothing collides, so each of these fails for its own defect.
    fresh_db = tmp_path / "fresh.db"
    fresh = Store(fresh_db)
    try:
        empty = dump(fresh_db)
        for broken in (other_version, tampered_note, not_cairns):
            with pytest.raises(ValueError):
                fresh.import_export(io.BytesIO(rewritten(broken)))
        with pytest.raises(ValueError):
            fresh.import_export(io.BytesIO(b"not a zip at all"))
        with pytest.raises(ValueError) as guarded:  # an import is not a way round the guard
            fresh.import_export(io.BytesIO(with_a_secret()))
        assert "github_token" in str(guarded.value) and "ghp_" not in str(guarded.value)
        assert dump(fresh_db) == empty and fresh.list_projects() == []
        fresh.import_export(io.BytesIO(rewritten(lambda name, data: data)))  # untouched: fine
        assert everything(fresh) == everything(ada)
    finally:
        fresh.close()


# Erasure


def test_a_deleted_workspaces_text_is_gone_from_the_files(store, db):
    keep = store.for_account(store.create_account("Keeper").id)
    gone = store.for_account(store.create_account("Leaver").id)
    # Titles (and so page slugs) carry one word, the text under them another.
    for view, word, inside in ((keep, "heron", "kingfisher"), (gone, "zephyrmark", "vellumquartz")):
        project = view.create_project("core")
        for n in range(120):
            body = f"The {inside} figure number {n} says retries were {n % 7}.\n" * 3
            add(view, project.id, f"# {word} report {n}\n\n{body}")
    assert store.delete_account(gone.account_id) is True

    def files() -> list[Path]:
        return [p for p in db.parent.iterdir() if p.name.startswith(db.name)]

    for path in files():  # the notes' text is gone at once, without rewriting the database
        assert b"vellumquartz" not in path.read_bytes().lower(), path.name
    before = db.stat().st_size
    store.compact()
    assert db.stat().st_size < before  # compacting gives the space back
    for path in files():  # and takes what was left in index pages with it
        data = path.read_bytes().lower()
        assert b"zephyrmark" not in data and b"vellumquartz" not in data, path.name
    assert len(keep.search("heron", limit=50)) == 50  # the other workspace is untouched
    assert keep.list_notes(keep.get_project("core").id)[119].title == "heron report 119"
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


# The service


@pytest.fixture
def env(store, db, ada):
    key = ada.create_key("laptop")
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        r = client.post("/login", data={"key": key}, headers=SAME_ORIGIN, follow_redirects=False)
        assert r.status_code == 303
        yield SimpleNamespace(client=client, store=store, db=db, view=ada, key=key)


def test_the_account_page_exports_the_workspace(env):
    page = env.client.get("/account")
    assert page.status_code == 200 and "Ada" in page.text
    assert 'href="/account/export"' in page.text and 'action="/account/delete"' in page.text

    before = dump(env.db)
    download = env.client.get("/account/export")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert "attachment" in download.headers["content-disposition"]
    assert ".zip" in download.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(download.content)).namelist()
    assert "export.json" in names and f"projects/ops/pages/{SLUG}.md" in names
    by_key = env.client.get("/api/account/export", headers={"x-api-key": env.key})
    assert zipfile.ZipFile(io.BytesIO(by_key.content)).namelist() == names
    # An export writes one thing: its audit event, who and when (each download is one).
    events = [e.action for e in env.view.list_audit(2)]
    assert events == ["workspace.exported", "workspace.exported"]
    unaudited = [line for line in dump(env.db).splitlines() if "workspace.exported" not in line]
    assert unaudited == before.splitlines()

    env.client.cookies.clear()
    assert env.client.get("/account/export", follow_redirects=False).status_code == 303
    assert env.client.get("/api/account/export").status_code == 401
    # Only a connection to the whole workspace, with write access, can take everything.
    ops = env.view.get_project("ops")
    for reach in ({"projects": (ops.id,)}, {"access": "read"}):
        limited = env.view.create_key("limited", **reach)
        r = env.client.get("/api/account/export", headers={"x-api-key": limited})
        assert r.status_code == 403, reach
        env.view.revoke_key("limited")


def test_a_workspace_is_deleted_only_on_typing_its_name(env):
    account = env.view.account_id
    for confirm in ("", "ada", "Ada ", "delete"):
        r = env.client.post("/account/delete", data={"confirm": confirm}, headers=SAME_ORIGIN)
        assert r.status_code == 400, confirm
    assert env.store.get_account(account) is not None
    cross = env.client.post(
        "/account/delete", data={"confirm": "Ada"}, headers={"sec-fetch-site": "cross-site"}
    )
    assert cross.status_code == 403 and env.store.get_account(account) is not None

    r = env.client.post(
        "/account/delete", data={"confirm": "Ada"}, headers=SAME_ORIGIN, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert env.store.get_account(account) is None
    assert env.client.get("/account", follow_redirects=False).status_code == 303  # signed out
    assert env.store.resolve_key(env.key) is None
    for path in [p for p in env.db.parent.iterdir() if p.name.startswith(env.db.name)]:
        assert b"reports and stops" not in path.read_bytes().lower(), path.name  # a note's text


def test_the_default_workspace_cannot_delete_itself(store, db):
    key = store.create_key("owner")
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        name = store.get_account(DEFAULT_ACCOUNT_ID).name
        r = client.post("/account/delete", data={"confirm": name}, headers=SAME_ORIGIN)
        assert r.status_code == 403 and 'role="alert"' in r.text
        assert store.get_account(DEFAULT_ACCOUNT_ID) is not None


# The command line


def test_the_command_line_exports_imports_and_compacts(ada, store, db, tmp_path, capsys):
    out = tmp_path / "ada.zip"
    account = ada.account_id
    assert main(["export", "--db", str(db), "--account", account, "--out", str(out)]) == 0
    assert out.stat().st_mode & 0o777 == 0o600
    assert zipfile.ZipFile(out).read("export.json")
    assert main(["export", "--db", str(db), "--account", account, "--out", str(out)]) == 1
    unknown = ["export", "--db", str(db), "--account", "acct_000000000000"]
    assert main([*unknown, "--out", str(tmp_path / "x.zip")]) == 1

    elsewhere = tmp_path / "elsewhere.db"
    Store(elsewhere).close()
    args = ["import", "--db", str(elsewhere), "--account", DEFAULT_ACCOUNT_ID, "--in", str(out)]
    assert main(args) == 0
    assert main(args) == 1  # a second time, everything already exists
    moved = Store(elsewhere)
    try:
        assert everything(moved) == everything(ada)
    finally:
        moved.close()
    assert main(["compact", "--db", str(elsewhere)]) == 0
    assert main(["compact", "--db", str(tmp_path / "missing.db")]) == 1
    printed = capsys.readouterr()
    assert "Quill" not in printed.out + printed.err  # paths and counts only, never note text
