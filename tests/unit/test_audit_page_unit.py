"""The audit page shows what it has (counts, parts by a cursor, filters), the trail leaves the
server (CSV and JSON downloads, GET /api/audit), reads are events when the operator asks
(create_app's `audit_reads`), and the number kept is the server's (`audit_keep`)."""

import csv
import html
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import cairn
from cairn.server import create_app
from cairn.store import Claim, Store

BASE = "http://127.0.0.1:8765"
ISSUER = "https://github.com"
CLAIM = "cl_000000000001"
NOW = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)


def person(store, n: int, name: str) -> SimpleNamespace:
    signed = store.sign_in(
        "github", str(n), f"person{n}@example.test", name, issuer=ISSUER, join="default"
    )
    return SimpleNamespace(
        id=signed.user.id,
        view=store.for_account(signed.account_id, signed.user.id),
        cookie={"cookie": f"cairn_session={signed.token}"},
    )


def build(tmp_path, **options) -> SimpleNamespace:
    folder = tmp_path / ("reads" if options.get("audit_reads") else "plain")
    folder.mkdir(exist_ok=True)
    store = Store(folder / "cairn.db", clock=lambda: NOW)
    ada, brook, cy = (person(store, n, name) for n, name in enumerate(("Ada", "Brook", "Cy")))
    ada.view.set_role(brook.id, "admin")
    ops = store.create_project("ops")
    note, _ = store.add_note(ops.id, "# Deploy\n\nThe zebrafinch job restarts at 09:00.\n")
    span = next(s.id for s in store.note_spans(note.id) if s.kind == "paragraph")
    claim = Claim(id=CLAIM, text="The job restarts at 09:00.", span_ids=(span,))
    store.save_page(ops.id, "deploy", "Deploy", "# Deploy\n\nThe job restarts.\n", [claim], {})
    key = store.create_key("laptop")
    app = create_app(store, public_url=BASE, **options)
    return SimpleNamespace(
        store=store, app=app, ada=ada, brook=brook, cy=cy, ops=ops, note=note, key=key
    )


@pytest.fixture
def env(tmp_path):
    world = build(tmp_path)
    with TestClient(world.app, base_url=BASE) as client:
        world.client = client
        yield world
    world.store.close()


def page(env, query: str = "", who=None) -> str:
    r = env.client.get(f"/audit{query}", headers=(who or env.ada).cookie)
    assert r.status_code == 200, r.text
    return html.unescape(r.text)


def many(env, count: int) -> None:
    for n in range(count):
        env.store.record_note(env.ops.id, "unchanged", {"page": f"p{n}"})


# 1. The page shows what it has


def test_the_page_says_how_many_events_it_shows_and_how_many_are_kept(env):
    many(env, 250)
    total = len(env.ada.view.list_audit(1000))
    text = page(env)
    assert f"Showing events 1 to 200 of {total:,}, newest first." in text
    assert "It keeps at most the newest 100,000 events of this workspace" in text


def test_older_events_are_a_link_with_a_cursor_and_no_script(env):
    many(env, 250)
    text = page(env)
    assert "<script" not in text
    (older,) = re.findall(r'<a rel="next" href="(/audit\?[^"]+)">Older events</a>', text)
    assert re.fullmatch(r"/audit\?before=\d+", older)
    second = page(env, older[len("/audit") :])
    total = len(env.ada.view.list_audit(1000))
    assert f"Showing events 201 to {total:,} of {total:,}, newest first." in second
    assert 'href="/audit">Newest events</a>' in second
    assert "p49" in second and "p50" not in second  # the 201st newest is p49
    assert 'rel="next"' not in second


def test_filters_by_action_project_and_who_through_an_ordinary_form(env):
    many(env, 3)
    text = page(env)
    form = re.search(r'<form[^>]*method="get"[^>]*action="/audit"[^>]*>(.*?)</form>', text, re.S)
    assert form is not None
    for field, label in (("action", "What"), ("project", "Project"), ("who", "Who")):
        assert re.search(rf'<label for="audit-{field}">{label}</label>', form.group(1))
        assert f'id="audit-{field}" name="{field}"' in form.group(1)
    filtered = page(env, "?action=note&project=ops&who=")
    assert "Showing events 1 to 4 of 4 that match, newest first." in filtered  # and note.saved
    assert "project.created" not in filtered.split("<tbody>")[1]
    assert 'value="note"' in filtered  # the form keeps what was asked


def test_the_older_link_keeps_the_filters(env):
    many(env, 210)
    text = page(env, "?action=note.unchanged")
    (older,) = re.findall(r'<a rel="next" href="(/audit\?[^"]+)">Older events</a>', text)
    assert "action=note.unchanged" in older and "before=" in older


def test_a_filter_that_matches_nothing_says_so(env):
    text = page(env, "?who=Quill")
    assert "No event matches these filters" in text
    assert 'href="/audit">Show every event</a>' in text
    assert "Nothing has been recorded yet" not in text


def test_a_member_still_gets_no_audit_page(env):
    r = env.client.get("/audit?action=note", headers=env.cy.cookie)
    assert r.status_code == 403


# 2. A name in the "Who" column is not broken inside a word on a wide screen


def test_a_name_in_the_who_column_breaks_only_between_words_on_a_wide_screen():
    css = (Path(cairn.__file__).parent / "static" / "cairn.css").read_text()
    wide = css[css.index("@media (min-width: 40.01rem)") :]
    block = wide[: wide.index("}\n}") + 3]
    rule = re.search(r'\.rows td\[data-label="Who"\] \.actor-name \{([^}]*)\}', block)
    assert rule is not None and "overflow-wrap: normal" in rule.group(1)
    assert "word-break" not in rule.group(1) or "normal" in rule.group(1)


# 3. Downloads: CSV and JSON, with the same filters, and recorded


@pytest.mark.parametrize("who", ["ada", "brook"])
def test_owners_and_admins_download_every_event_they_see_as_json(env, who):
    many(env, 1205)  # more than one part of the store's reading
    viewer = getattr(env, who)
    seen = viewer.view.list_audit(1000).total
    r = env.client.get("/audit/download?format=json", headers=viewer.cookie)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers["content-disposition"]
    events = json.loads(r.text)
    assert len(events) == seen
    assert [e["n"] for e in events] == sorted((e["n"] for e in events), reverse=True)
    assert {"n", "at", "actor_kind", "actor", "actor_id", "action", "project", "detail"} <= set(
        events[0]
    )


def test_a_download_as_csv_has_a_header_and_one_row_per_event(env):
    many(env, 3)
    r = env.client.get("/audit/download?format=csv&action=note.unchanged", headers=env.ada.cookie)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["n", "at", "actor_kind", "actor", "actor_id", "action", "project", "detail"]
    assert [row[5] for row in rows[1:]] == ["note.unchanged"] * 3
    assert json.loads(rows[1][7]) == {"page": "p2"}


def test_a_cell_a_spreadsheet_would_run_is_written_as_text(env):
    env.store.acting_as("key", "=HYPERLINK(1)", "key_x").create_project("web")
    r = env.client.get("/audit/download?format=csv&project=web", headers=env.ada.cookie)
    (row,) = list(csv.reader(io.StringIO(r.text)))[1:]
    assert row[3] == "'=HYPERLINK(1)"


def test_a_download_is_itself_an_event(env):
    env.store.acting_as("key", "laptop", "key_1").create_project("web")
    expected = env.brook.view.list_audit(who="laptop").total
    assert expected == 1
    env.client.get("/audit/download?format=csv&who=laptop", headers=env.brook.cookie)
    event = env.store.list_audit(1)[0]
    assert (event.action, event.actor, event.project) == ("audit.exported", "Brook", None)
    assert event.detail == {"format": "csv", "events": expected, "user": env.brook.id}
    assert "laptop" not in json.dumps(event.detail)  # what was typed in a filter is not kept


def test_deleted_projects_events_download_to_owners_only(env):
    gone = env.store.create_project("hiring")
    env.store.delete_project(gone.id)
    owner = env.client.get("/audit/download?format=json", headers=env.ada.cookie).text
    admin = env.client.get("/audit/download?format=json", headers=env.brook.cookie).text
    assert f"{gone.id} (deleted)" in owner and gone.id not in admin


def test_a_member_and_an_unknown_format_get_no_download(env):
    assert env.client.get("/audit/download?format=csv", headers=env.cy.cookie).status_code == 403
    r = env.client.get("/audit/download?format=xml", headers=env.ada.cookie)
    assert r.status_code == 400
    assert [e.action for e in env.store.list_audit(1)] != ["audit.exported"]


def test_the_page_links_to_both_downloads_with_its_filters(env):
    text = page(env, "?action=note")
    assert 'href="/audit/download?format=csv&action=note"' in text
    assert 'href="/audit/download?format=json&action=note"' in text


# 4. GET /api/audit


def test_a_key_that_manages_the_workspace_reads_the_trail_over_rest(env):
    many(env, 5)
    r = env.client.get("/api/audit?limit=2&action=note.unchanged", headers={"x-api-key": env.key})
    assert r.status_code == 200
    body = r.json()
    assert [e["detail"]["page"] for e in body["events"]] == ["p4", "p3"]
    assert body["events_total"] == 5 and body["events_next"] == body["events"][-1]["n"]
    after = env.client.get(
        f"/api/audit?limit=10&action=note.unchanged&before={body['events_next']}",
        headers={"x-api-key": env.key},
    ).json()
    assert [e["detail"]["page"] for e in after["events"]] == ["p2", "p1", "p0"]
    assert "events_next" not in after
    ops = env.client.get("/api/audit?project=ops", headers={"x-api-key": env.key}).json()
    assert {e["project"] for e in ops["events"]} == {"ops"}


def test_a_narrowed_key_is_refused_and_a_query_it_does_not_take_is_400(env):
    narrow = env.store.create_key("reader", None, "read")
    r = env.client.get("/api/audit", headers={"x-api-key": narrow})
    assert r.status_code == 403
    r = env.client.get("/api/audit?who=laptop", headers={"x-api-key": env.key})
    assert r.status_code == 400 and "'before', 'limit', 'action' and 'project'" in r.text
    r = env.client.get("/api/audit?limit=0", headers={"x-api-key": env.key})
    assert r.status_code == 400


def test_the_rest_surface_documents_get_api_audit():
    import cairn.server

    doc = cairn.server.__doc__
    assert "- GET /api/audit[?before=N][&limit=N][&action=...][&project=...]" in doc


# 6. Reads, when the operator asks


@pytest.fixture
def reading(tmp_path):
    world = build(tmp_path, audit_reads=True)
    with TestClient(world.app, base_url=BASE) as client:
        world.client = client
        yield world
    world.store.close()


def reads(world) -> list[tuple]:
    return [
        (e.action, e.actor, e.project, {k: v for k, v in e.detail.items() if k != "user"})
        for e in reversed(world.store.list_audit(1000))
        if e.action.startswith("read.")
    ]


def test_with_reads_audited_each_read_over_the_web_rest_and_a_key_is_an_event(reading):
    w, key = reading, {"x-api-key": reading.key}
    assert w.client.get("/b/ops/p/deploy", headers=w.ada.cookie).status_code == 200
    assert w.client.get("/search?q=zebrafinch", headers=w.ada.cookie).status_code == 200
    assert w.client.get(f"/b/ops/notes/{w.note.id}/forget", headers=w.ada.cookie).status_code == 200
    evidence = f"/api/projects/ops/pages/deploy/claims/{CLAIM}/evidence"
    assert w.client.get(evidence, headers=key).status_code == 200
    assert w.client.get("/api/search?q=zebrafinch&project=ops", headers=key).status_code == 200
    assert w.client.get("/api/graph?project=ops", headers=key).status_code == 200
    assert reads(w) == [
        ("read.page", "Ada", "ops", {"page": "deploy"}),
        ("read.search", "Ada", None, {}),
        ("read.note", "Ada", "ops", {"note": w.note.id}),
        ("read.evidence", "laptop", "ops", {"page": "deploy", "claim": CLAIM}),
        ("read.search", "laptop", "ops", {}),
        ("read.graph", "laptop", "ops", {}),
    ]
    stored = json.dumps([e.model_dump() for e in w.store.list_audit(1000)])
    assert "zebrafinch" not in stored


def test_the_graph_page_of_one_note_names_the_note(tmp_path):
    world = build(tmp_path, audit_reads=True, graph_explorer=True)
    try:
        with TestClient(world.app, base_url=BASE) as client:
            r = client.get(f"/b/ops/graph/notes/{world.note.id}", headers=world.ada.cookie)
            assert r.status_code == 200, r.text
        assert reads(world) == [("read.graph", "Ada", "ops", {"note": world.note.id})]
    finally:
        world.store.close()


def test_a_read_of_a_synchronous_handler_is_recorded_off_the_event_loop(reading, monkeypatch):
    """The event is written behind the store's lock: on the worker thread that runs the
    handler, never on the event loop, where it would hold up every other request."""
    import asyncio

    on_the_loop = []
    record = Store._record_read

    def watched(self, *args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_the_loop.append(True)
        except RuntimeError:
            on_the_loop.append(False)
        return record(self, *args, **kwargs)

    monkeypatch.setattr(Store, "_record_read", watched)
    w = reading
    assert w.client.get("/b/ops/p/deploy", headers=w.ada.cookie).status_code == 200
    key = {"x-api-key": w.key}
    assert w.client.get("/api/graph?project=ops", headers=key).status_code == 200
    assert on_the_loop == [False, False]


def test_without_it_reads_are_no_events(env):
    env.client.get("/b/ops/p/deploy", headers=env.ada.cookie)
    env.client.get("/api/search?q=zebrafinch", headers={"x-api-key": env.key})
    assert reads(env) == []


def test_the_page_says_whether_reads_are_recorded(env, reading):
    assert "Reads are not recorded here." in page(env)
    text = page(reading)
    assert "Reads are not recorded here." not in text
    assert "Reading a page, a note, a claim's evidence, a search and a graph" in text


# 7. How many events a workspace keeps


def test_the_number_kept_is_the_servers_and_the_page_says_it(tmp_path):
    world = build(tmp_path, audit_keep=2_500)
    assert world.store.audit_keep == 2_500
    with TestClient(world.app, base_url=BASE) as client:
        world.client = client
        assert "It keeps at most the newest 2,500 events of this workspace" in page(world)
    world.store.close()


def test_all_keeps_every_event_and_the_page_says_so(tmp_path):
    world = build(tmp_path, audit_keep=None)
    assert world.store.audit_keep is None
    with TestClient(world.app, base_url=BASE) as client:
        world.client = client
        assert "It keeps every event of this workspace." in page(world)
    world.store.close()


@pytest.mark.parametrize("keep", [0, -5, 1.5, "all", True])
def test_create_app_refuses_another_number_kept(tmp_path, keep):
    with pytest.raises(ValueError, match="audit"):
        build(tmp_path, audit_keep=keep)


def test_create_app_refuses_reads_audited_that_is_not_true_or_false(tmp_path):
    with pytest.raises(ValueError, match="audit"):
        build(tmp_path, audit_reads="yes")
