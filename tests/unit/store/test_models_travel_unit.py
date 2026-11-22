"""A project's Models choice travels with an export (M4, an outside review's S2): an export
says, for each project, whether it is at "never", and an import puts it there in the write
that brings it. An archive written before says nothing, imports as before, and its notice
says so. Fixtures are synthetic; nothing leaves the process."""

import io
import json
import zipfile

import pytest

from cairn.store import Store
from cairn.store.portable import VERSION


@pytest.fixture
def before(tmp_path):
    opened = Store(tmp_path / "before.db")
    yield opened
    opened.close()


@pytest.fixture
def after(tmp_path):
    opened = Store(tmp_path / "after.db")
    yield opened
    opened.close()


def exported(view) -> bytes:
    out = io.BytesIO()
    view.export(out)
    return out.getvalue()


def rewritten(archive: bytes, change) -> bytes:
    """The archive with its manifest passed through `change` (a function of the dict)."""
    source = zipfile.ZipFile(io.BytesIO(archive))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "export.json":
                manifest = json.loads(data)
                change(manifest)
                data = json.dumps(manifest).encode()
            target.writestr(info, data)
    return out.getvalue()


def unsaid(manifest) -> None:
    """An archive as a build before this one wrote it: no project says its choice."""
    for project in manifest["projects"]:
        project.pop("models", None)


def test_the_reviews_reproduction_a_general_project_at_never_arrives_at_never(
    before, after, tmp_path
):
    view = before.for_account(before.account_id)
    project = view.create_project("synthetic-general")
    view.set_project_models(project.id, "never")
    archive = tmp_path / "archive.zip"
    with archive.open("wb") as out:
        view.export(out)
    view = after.for_account(after.account_id)
    with archive.open("rb") as source:
        summary = view.import_export(source)
    imported = view.get_project("synthetic-general")
    assert imported.id in view.kept_from_models([imported.id])
    assert "model" in summary.notice.casefold()


def test_an_export_says_each_projects_choice(before):
    general = before.create_project("open-plans")
    shut = before.create_project("shut-plans")
    before.set_project_models(shut.id, "never")
    diary = before.create_project("diary", kind="personal")
    assert {general.id, diary.id} - before.kept_from_models([general.id, diary.id]) == {general.id}
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(exported(before))).read("export.json"))
    assert manifest["version"] == VERSION == 1  # the format extended, not a new version
    # A build before this one reads a project by these keys alone and passes any other by:
    # the entry is what it was, with one key more.
    old = {"id", "name", "description", "kind", "created_at", "notes", "pages"}
    assert all(
        set(project) == old | {"models", "models_decided"} for project in manifest["projects"]
    )
    said = {project["name"]: project["models"] for project in manifest["projects"]}
    assert said == {"open-plans": "allowed", "shut-plans": "never", "diary": "never"}


def test_each_kind_arrives_where_the_archive_says_and_a_private_kind_at_never(before, after):
    kept_general = before.create_project("kept-general")
    before.set_project_models(kept_general.id, "never")
    before.create_project("open-general")
    kept_repo = before.create_project("kept-repo", kind="repo")
    before.set_project_models(kept_repo.id, "never")
    diary = before.create_project("diary", kind="personal")
    before.set_project_models(diary.id, "allowed")
    summary = after.import_export(io.BytesIO(exported(before)))
    projects = {p.name: p.id for p in after.list_projects()}
    kept = after.kept_from_models(projects.values())
    assert {name for name, pid in projects.items() if pid in kept} == {
        "kept-general",
        "kept-repo",
        "diary",  # the archive says allowed; a Personal notes project arrives at never
    }
    assert "3 of the 4 projects imported arrived at Models never" in summary.notice


def test_the_archive_cannot_say_a_personal_project_is_allowed(before, after):
    diary = before.create_project("diary", kind="personal")
    before.set_project_models(diary.id, "allowed")
    archive = exported(before)
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(archive)).read("export.json"))
    assert manifest["projects"][0]["models"] == "allowed"
    after.import_export(io.BytesIO(archive))
    brought = after.get_project("diary")
    assert after.kept_from_models([brought.id]) == {brought.id}


def test_an_archive_written_before_imports_as_before_and_its_notice_says_so(before, after):
    kept_general = before.create_project("kept-general")
    before.set_project_models(kept_general.id, "never")
    before.create_project("diary", kind="personal")
    archive = rewritten(exported(before), unsaid)
    summary = after.import_export(io.BytesIO(archive))
    projects = {p.name: p.id for p in after.list_projects()}
    assert after.kept_from_models(projects.values()) == {projects["diary"]}
    notice = summary.notice
    assert notice.startswith("Every imported project is private")  # the access sentence stays
    assert (
        "The archive does not say which projects were kept from models, so each arrived at"
        " Models as a new project of its kind does: Personal notes at never, the others not"
        " decided."
    ) in notice
    assert "arrived at Models never" not in notice


def test_an_import_counts_the_projects_at_never_in_its_audit_event(before, after):
    kept_general = before.create_project("kept-general")
    before.set_project_models(kept_general.id, "never")
    before.create_project("open-general")
    after.import_export(io.BytesIO(exported(before)))
    (event,) = after.list_audit()
    assert event.action == "workspace.imported"
    assert event.detail["kept_from_models"] == 1
    assert [e.action for e in after.list_audit()] == ["workspace.imported"]  # no other kind


@pytest.mark.parametrize("value", ["off", None, 0, True, ["never"]])
def test_a_choice_that_is_not_one_of_the_two_is_refused_and_nothing_written(before, after, value):
    before.create_project("plans")
    archive = rewritten(exported(before), lambda m: m["projects"][0].update(models=value))
    with pytest.raises(ValueError, match="models"):
        after.import_export(io.BytesIO(archive))
    assert after.list_projects() == []


def test_an_archive_that_says_it_for_some_projects_only_is_refused(before, after):
    before.create_project("one")
    before.create_project("two")
    archive = rewritten(exported(before), lambda m: m["projects"][1].pop("models"))
    with pytest.raises(ValueError, match="models"):
        after.import_export(io.BytesIO(archive))
    assert after.list_projects() == []


def test_an_import_never_moves_an_existing_project_from_never(before, after):
    """The import's target is never an existing project: a name the workspace uses is
    refused, nothing is written, and the project there keeps its choice."""
    before.create_project("plans")  # allowed in the archive
    held = after.create_project("plans")
    after.set_project_models(held.id, "never")
    with pytest.raises(ValueError, match="already has a project named"):
        after.import_export(io.BytesIO(exported(before)))
    assert after.kept_from_models([held.id]) == {held.id}
