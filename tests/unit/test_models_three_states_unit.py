"""A project's Models choice has three states (review: Privacy 4): "allowed" when its people
chose it, "never", and not decided when nobody did. Choosing allowed is a row of its own, so
`<PREFIX>_UNDECIDED_PROJECTS=withhold` can tell the two apart; every place that asks whether a
project is kept from every model asks for the value never. Fixtures are synthetic."""

import io
import json
import re
import sqlite3
import zipfile
from html import unescape
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.server import create_app
from cairn.store import Limits, Store
from cairn.store.access import MODELS_PREFIX, KeptFromModels
from cairn.store.portable import IMPORTED_ALLOWED_UNSAID

TEXT = "# Drift sync\n\nQuill syncs at noon.\n\n- Brook checks the log.\n- Ada signs off.\n"


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


@pytest.fixture
def after(tmp_path):
    s = Store(tmp_path / "after.db")
    yield s
    s.close()


def rows(db: Path) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT substr(name, length(?) + 1), value FROM settings WHERE name LIKE ?"
            " ORDER BY name",
            (MODELS_PREFIX, MODELS_PREFIX + "%"),
        ).fetchall()
    finally:
        con.close()


def with_page(store, name: str):
    project = store.create_project(name)
    note, _ = store.add_note(project.id, TEXT)
    assert compile_note(store, note, gate=lint_gate).status == "saved"
    return project, store.get_page(project.id, "drift-sync")


def changes(store) -> list:
    return [e for e in store.list_audit(limit=500) if e.action == "project.models_changed"]


# 4. The three states, stored


def test_allowed_is_a_row_never_is_a_row_and_nobody_deciding_is_none(store, db):
    chosen, shut, open_ = (store.create_project(n) for n in ("chosen", "shut", "open"))
    store.set_project_models(chosen.id, "allowed")
    store.set_project_models(shut.id, "never")
    assert sorted(rows(db)) == sorted([(chosen.id, "allowed"), (shut.id, "never")])
    ids = [chosen.id, shut.id, open_.id]
    assert store.models_choices(ids) == {chosen.id: "allowed", shut.id: "never"}
    assert store.kept_from_models(ids) == {shut.id}


def test_allowed_then_never_then_allowed_holds_each_time(store, db):
    project = store.create_project("swing")
    for state in ("allowed", "never", "allowed", "never"):
        assert store.set_project_models(project.id, state) == state
        assert rows(db) == [(project.id, state)]
        assert store.models_choices([project.id]) == {project.id: state}
        assert store.kept_from_models([project.id]) == (
            {project.id} if state == "never" else frozenset()
        )


def test_choosing_allowed_where_nobody_decided_is_a_change_and_again_is_none(store):
    project = store.create_project("fresh")
    assert store.set_project_models(project.id, "allowed") == "allowed"
    (event,) = changes(store)
    assert event.detail == {"project_id": project.id, "models": "allowed"}
    store.set_project_models(project.id, "allowed")
    assert len(changes(store)) == 1


def test_a_personal_project_still_starts_at_never(store, db):
    diary = store.create_project("diary", kind="personal")
    assert rows(db) == [(diary.id, "never")]
    assert store.models_choices([diary.id]) == {diary.id: "never"}


def test_the_choices_are_read_within_the_reach(store):
    one, two = (store.create_project(n) for n in ("one", "two"))
    store.set_project_models(one.id, "allowed")
    store.set_project_models(two.id, "never")
    assert store.narrow([one.id]).models_choices([one.id, two.id]) == {one.id: "allowed"}
    other = store.for_account(store.create_account("Other").id)
    assert other.models_choices([one.id, two.id]) == {}
    assert store.models_choices([]) == {}


def test_reading_the_choices_is_one_statement_whatever_their_number(store):
    ids = [store.create_project(f"p{i}").id for i in range(30)]
    for project_id in ids[:10]:
        store.set_project_models(project_id, "allowed")
    seen: list[str] = []
    store._observe(trace=seen.append)
    try:
        store.models_choices(ids)
    finally:
        store._observe(trace=None)
    assert len([s for s in seen if s.lstrip().upper().startswith("SELECT")]) == 1


# Each place that asks "kept from every model?" asks for never, not for a row


def test_a_page_of_an_allowed_project_is_read_as_allowed_and_not_kept(store):
    project, page = with_page(store, "chosen")
    assert store.page_for_model(project.id, "drift-sync") == (page, False, False)
    store.set_project_models(project.id, "allowed")
    assert store.page_for_model(project.id, "drift-sync") == (page, False, True)
    store.set_project_models(project.id, "never")
    assert store.page_for_model(project.id, "drift-sync") == (page, True, False)


def test_a_models_answer_is_saved_in_an_allowed_project(store):
    project, page = with_page(store, "chosen")
    store.set_project_models(project.id, "allowed")
    saved = store.save_page(
        project.id, page.slug, page.title, page.body, page.claims, by_model=True
    )
    assert saved.version == page.version + 1
    store.set_project_models(project.id, "never")
    with pytest.raises(KeptFromModels):
        store.save_page(project.id, page.slug, page.title, page.body, page.claims, by_model=True)


@pytest.mark.parametrize("budget", [None, 10**9])
def test_a_call_is_admitted_for_an_allowed_project(store, budget):
    project = store.create_project("chosen")
    store.set_project_models(project.id, "allowed")
    view = store if budget is None else store.limited(Limits(model_budget_micros=budget))
    assert view.reserve_model_use(10, project.id) is not None
    store.set_project_models(project.id, "never")
    with pytest.raises(KeptFromModels):
        view.reserve_model_use(10, project.id)


def test_deleting_an_allowed_project_removes_its_row(store, db):
    project = store.create_project("chosen")
    store.set_project_models(project.id, "allowed")
    assert store.delete_project(project.id) is True
    assert rows(db) == []


# 7. Export and import


def exported(view) -> bytes:
    out = io.BytesIO()
    view.export(out)
    return out.getvalue()


def manifest_of(archive: bytes) -> dict:
    return json.loads(zipfile.ZipFile(io.BytesIO(archive)).read("export.json"))


def rewritten(archive: bytes, change) -> bytes:
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


@pytest.fixture
def three(store):
    chosen, shut, open_ = (store.create_project(n) for n in ("chosen", "shut", "open"))
    store.set_project_models(chosen.id, "allowed")
    store.set_project_models(shut.id, "never")
    return chosen, shut, open_


def arrived(view) -> dict[str, str | None]:
    projects = {p.name: p.id for p in view.list_projects()}
    said = view.models_choices(projects.values())
    return {name: said.get(project_id) for name, project_id in projects.items()}


def test_an_export_says_which_projects_people_decided(store, three):
    manifest = manifest_of(exported(store))
    said = {p["name"]: (p["models"], p["models_decided"]) for p in manifest["projects"]}
    assert said == {
        "chosen": ("allowed", True),
        "shut": ("never", True),
        "open": ("allowed", False),  # what a build before this one reads as before
    }


def test_an_import_brings_all_three_states(store, three, after):
    archive = exported(store)
    summary = after.import_export(io.BytesIO(archive))
    assert arrived(after) == {"chosen": "allowed", "shut": "never", "open": None}
    assert IMPORTED_ALLOWED_UNSAID not in summary.notice


def test_an_archive_from_before_says_nothing_of_allowed_and_imports_as_not_decided(
    store, three, after
):
    def from_before(manifest) -> None:
        for project in manifest["projects"]:
            project.pop("models_decided")

    archive = rewritten(exported(store), from_before)
    summary = after.import_export(io.BytesIO(archive))
    assert arrived(after) == {"chosen": None, "shut": "never", "open": None}
    assert IMPORTED_ALLOWED_UNSAID in summary.notice
    assert "1 of the 3 projects imported arrived at Models never." in summary.notice


def test_a_personal_project_arrives_at_never_whatever_the_archive_decided(store, after):
    diary = store.create_project("diary", kind="personal")
    store.set_project_models(diary.id, "allowed")
    archive = exported(store)
    after.import_export(io.BytesIO(archive))
    assert arrived(after) == {"diary": "never"}


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_a_decided_that_is_not_true_or_false_is_refused(store, three, after, value):
    def odd(manifest) -> None:
        manifest["projects"][0]["models_decided"] = value

    archive = rewritten(exported(store), odd)
    with pytest.raises(ValueError, match="decided"):
        after.import_export(io.BytesIO(archive))


def test_an_archive_that_says_decided_of_some_projects_only_is_refused(store, three, after):
    def some(manifest) -> None:
        manifest["projects"][0].pop("models_decided")

    archive = rewritten(exported(store), some)
    with pytest.raises(ValueError, match="decided"):
        after.import_export(io.BytesIO(archive))


# 6. The project page


BASE = "https://cairn.example.test"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
UNDECIDED_SAID = (
    "Models: not decided. Nobody has chosen for this project: a model the operator connects can"
    " be sent its pages, unless the operator sends only the pages of projects whose people chose"
    " allowed, and then it is sent none."
)
ALLOWED_SAID = "Models: allowed. A model the operator connects can be sent this project's pages"
NEVER_SAID = "Models: never. No model is sent this project's pages"


def visible(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split()).replace(" .", ".")


@pytest.fixture
def web(store):
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        yield client


def models_part(html: str) -> str:
    return html.split('class="row models"', 1)[1].split("</div>", 1)[0]


def test_the_project_page_says_not_decided_and_offers_both_choices(store, web):
    project = store.create_project("ops")
    shown = web.get("/b/ops")
    part = models_part(shown.text)
    assert visible(shown.text).count(UNDECIDED_SAID) == 1
    assert ALLOWED_SAID not in visible(shown.text) and NEVER_SAID not in visible(shown.text)
    assert 'name="models" value="allowed"' in part and 'name="models" value="never"' in part
    assert "Allow a model" in part and "Keep from every model" in part
    chose = web.post("/b/ops/models", data={"models": "allowed"}, headers=SAME_ORIGIN)
    assert chose.status_code == 200  # followed back to the project's page
    assert store.models_choices([project.id]) == {project.id: "allowed"}
    text = visible(chose.text)
    assert ALLOWED_SAID in text and UNDECIDED_SAID not in text
    assert 'value="allowed"' not in models_part(chose.text)  # the choice it has is not offered


def test_the_project_page_says_each_chosen_state(store, web):
    project = store.create_project("ops")
    states = (("allowed", ALLOWED_SAID, "never"), ("never", NEVER_SAID, "allowed"))
    for state, said, offered in states:
        store.set_project_models(project.id, state)
        shown = web.get("/b/ops")
        assert said in visible(shown.text) and UNDECIDED_SAID not in visible(shown.text)
        part = models_part(shown.text)
        assert f'value="{offered}"' in part and f'value="{state}"' not in part


def test_a_personal_project_s_page_says_never(store, web):
    store.create_project("diary", kind="personal")
    assert NEVER_SAID in visible(web.get("/b/diary").text)
