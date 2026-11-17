"""Offline documentation helper behavior."""

import contextlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store


def _footer(html):
    return html.split("<footer", 1)[1].split("</footer>", 1)[0]


def test_without_a_release_there_is_no_help_route_and_no_link(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("reader")
    with TestClient(create_app(store), base_url="http://testserver") as client:
        client.post("/login", data={"key": key}, headers={"sec-fetch-site": "same-origin"})
        assert 'href="/help"' not in _footer(client.get("/").text)
        unknown = client.get("/no-such-address")
        for method in ("get", "post"):
            help_page = getattr(client, method)("/help", headers={"sec-fetch-site": "same-origin"})
            assert help_page.status_code == unknown.status_code == 404
            assert help_page.text == unknown.text
    store.close()


def test_help_is_discoverable_in_signed_in_footer_outside_main_navigation(release):
    client = release["client"]
    home = client.get("/").text
    assert 'href="/help">Help</a>' in _footer(home)
    navigation = home.split('aria-label="Main"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/help"' not in navigation
    assert client.get("/help").status_code == 200
    client.post("/logout", headers={"sec-fetch-site": "same-origin"})
    assert 'href="/help"' not in client.get("/login").text


def test_an_app_without_a_release_shows_no_link_beside_one_with_it(release, tmp_path):
    store = Store(tmp_path / "other.db")
    key = store.create_key("reader")
    with TestClient(create_app(store), base_url="http://testserver") as off:
        off.post("/login", data={"key": key}, headers={"sec-fetch-site": "same-origin"})
        assert 'href="/help"' in _footer(release["client"].get("/").text)
        assert 'href="/help"' not in _footer(off.get("/").text)
        assert 'href="/help"' in _footer(release["client"].get("/").text)
    store.close()


REPO = "https://github.com/macayaven/cairn"
SHA = "a" * 40


def _artifact_of(large=False, sha=SHA, lines=None):
    """`lines`: a guide's one line in place of "Read the <name> guide.", by name."""
    notes = []
    for name in ("connect", "patterns", "sources", "security", "run", "privacy"):
        path = f"src/cairn/guides/{name}.md"
        page = f"docs-{name}-0001"
        content = (
            f"# {name}\n\n" + "".join(f"- Guide item {i} in {name}.\n" for i in range(60))
            if large
            else f"# {name}\n\n{(lines or {}).get(name, f'Read the {name} guide.')}\n"
        )
        end_line = 62 if large else 3
        notes.append(
            {
                "path": path,
                "start_line": 1,
                "end_line": end_line,
                "page": page,
                "payload": {
                    "text": content,
                    "title": page,
                    "topic": page,
                    "label": "cairn-docs-release-v1",
                    "source_ref": f"{REPO}/blob/{sha}/{path}#L1-L{end_line}",
                },
            }
        )
    return {
        "schema": "cairn-docs-release-v1",
        "repository": REPO,
        "release": sha,
        "project": {
            "name": f"cairn-docs-{sha}",
            "description": f"cairn documentation release {sha}; cairn-docs-release-v1",
        },
        "notes": notes,
    }


@contextlib.contextmanager
def _released(folder, monkeypatch, *, artifact=None, env="DOCS_READER", **options):
    """An app serving `artifact` (the small synthetic release by default), its notes stored
    through the API with a writer key, and a caller signed in."""
    artifact = artifact or _artifact_of()
    sha, project_info = artifact["release"], artifact["project"]
    folder.mkdir(parents=True, exist_ok=True)
    artifact_path = folder / "release.json"
    artifact_path.write_text(json.dumps(artifact))
    store = Store(folder / "cairn.db")
    project = store.create_project(**project_info, kind="documents")
    store.set_project_models(project.id, "never")
    writer = store.create_key("publisher", [project.id], "write")
    reader = store.create_key("docs-reader", [project.id], "read")
    caller = store.create_key("caller")
    monkeypatch.setenv(env, reader)
    now = [datetime(2026, 11, 16, 12, tzinfo=UTC)]
    app = create_app(
        store,
        docs_artifact=artifact_path,
        docs_release=sha,
        docs_reader_env=env,
        clock=lambda: now[0],
        **options,
    )
    with TestClient(app, base_url="http://testserver") as client:
        for note in artifact["notes"]:
            result = client.post(
                f"/api/projects/{project_info['name']}/notes",
                headers={"x-api-key": writer},
                json=note["payload"],
            )
            assert result.status_code in (200, 201), result.text
        if "sign_in" not in options:  # else the test signs in through the provider
            client.post("/login", data={"key": caller}, headers={"sec-fetch-site": "same-origin"})
        yield {
            "client": client,
            "store": store,
            "project": project,
            "project_info": project_info,
            "writer": writer,
            "reader": reader,
            "artifact": artifact,
            "path": artifact_path,
            "repo": REPO,
            "sha": sha,
            "monkeypatch": monkeypatch,
            "now": now,
        }
    store.close()


@pytest.fixture
def release(tmp_path, monkeypatch, request):
    large = getattr(request, "param", None) == "large"
    with _released(tmp_path, monkeypatch, artifact=_artifact_of(large)) as made:
        yield made


@pytest.mark.parametrize("backing_available", [True, False])
def test_configured_idle_help_invites_a_search_without_validating_backing(
    release, backing_available
):
    if not backing_available:
        release["path"].unlink()
        release["monkeypatch"].delenv("DOCS_READER")
    response = release["client"].get("/help")
    assert response.status_code == 200
    assert "Enter a question or topic to search the documentation." in response.text
    assert "Documentation search is unavailable" not in response.text
    assert "No documentation passages matched" not in response.text


def test_verified_release_searches_only_its_passages(release):
    response = release["client"].post(
        "/help", data={"q": "connect"}, headers={"sec-fetch-site": "same-origin"}
    )
    assert response.status_code == 200
    assert "Read the connect guide." in response.text
    assert (
        f"{release['repo']}/blob/{release['sha']}/src/cairn/guides/connect.md#L3-L3"
        in response.text
    )
    assert "Read the security guide." not in response.text


def _logged(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "cairn.web.docs_helper"]


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("writer", "reader_not_read_only"),
        ("broad", "reader_too_wide"),
        ("set", "reader_too_wide"),
        ("revoked", "reader_unknown"),
        ("wrong", "reader_unknown"),
        ("unset", "reader_unknown"),
    ],
)
def test_reader_key_must_be_current_read_only_and_literal_project(release, caplog, kind, reason):
    store = release["store"]
    key = release["reader"]
    if kind == "writer":
        key = release["writer"]
    elif kind == "broad":
        key = store.create_key("broad-reader", access="read")
    elif kind == "set":
        project_set = store.create_set("docs-only", [release["project"].id])
        key = store.create_key("set-reader", [project_set.id], "read")
    elif kind == "revoked":
        store.revoke_key("docs-reader")
    elif kind == "wrong":
        key = "not-a-key-of-this-install"
    else:
        key = ""
    release["monkeypatch"].setenv("DOCS_READER", key)
    caplog.set_level("INFO", logger="cairn.web.docs_helper")
    response = release["client"].post(
        "/help", data={"q": "connect"}, headers={"sec-fetch-site": "same-origin"}
    )
    assert "Documentation search is unavailable" in response.text
    assert "Read the connect guide." not in response.text
    assert _logged(caplog) == [f"help unavailable: {reason}"]
    assert all(key not in record.getMessage() for record in caplog.records if key)
    assert all("connect" not in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("kind", ["release", "traversal", "partial", "extra", "unsafe"])
def test_invalid_or_partial_release_is_unavailable(release, caplog, kind):
    artifact = release["artifact"]
    if kind == "release":
        artifact["release"] = "b" * 40
    elif kind == "traversal":
        artifact["notes"][0]["path"] = "../../private.md"
    elif kind == "partial":
        artifact["notes"].pop()
    elif kind == "unsafe":
        artifact["notes"][0]["payload"]["source_ref"] = "https://evil.example.test/"
    else:
        result = release["client"].post(
            f"/api/projects/{release['project_info']['name']}/notes",
            headers={"x-api-key": release["writer"]},
            json={"text": "An unexpected page.", "title": "extra", "topic": "extra"},
        )
        assert result.status_code in (200, 201)
    release["path"].write_text(json.dumps(artifact))
    caplog.set_level("INFO", logger="cairn.web.docs_helper")
    response = release["client"].post(
        "/help", data={"q": "connect"}, headers={"sec-fetch-site": "same-origin"}
    )
    assert "Documentation search is unavailable" in response.text
    assert "Read the connect guide." not in response.text
    reason = "release_too_large" if kind == "extra" else "release"
    assert _logged(caplog) == [f"help unavailable: {reason}"]


def test_no_match_and_query_length_are_bounded(release):
    client = release["client"]
    no_match = client.post(
        "/help", data={"q": "nonexistentword"}, headers={"sec-fetch-site": "same-origin"}
    )
    assert "No documentation passages matched" in no_match.text
    assert "A search is 1 to 200 characters." not in no_match.text


@pytest.mark.parametrize(
    "body,reason",
    [
        ({"data": {"q": "x" * 201}}, None),
        ({"data": {"q": "   "}}, None),
        ({"data": {"q": ""}}, None),
        ({"json": {"q": "connect"}}, "form"),
        ({"data": {"q": "connect", "other": "x"}}, "form"),
    ],
)
def test_an_empty_or_too_long_query_is_told_what_a_query_may_be(release, caplog, body, reason):
    caplog.set_level("INFO", logger="cairn.web.docs_helper")
    page = release["client"].post("/help", headers={"sec-fetch-site": "same-origin"}, **body)
    assert page.status_code == 200
    assert "Enter a question or topic to search the documentation." in page.text
    assert "A search is 1 to 200 characters." in page.text
    assert "Documentation search is unavailable" not in page.text
    assert _logged(caplog) == ([f"help unavailable: {reason}"] if reason else [])


def test_help_requires_session_and_keeps_csp(release):
    client = release["client"]
    response = client.post(
        "/help", data={"q": "<script>"}, headers={"sec-fetch-site": "same-origin"}
    )
    assert "&lt;script&gt;" in response.text
    assert "<script>" not in response.text
    assert "form-action 'self'" in response.headers["content-security-policy"]
    assert 'for="docs-query"' in response.text
    client.post("/logout", headers={"sec-fetch-site": "same-origin"})
    assert client.get("/help", follow_redirects=False).status_code in (302, 303, 401)


def test_customer_notes_are_not_used_as_documentation(release):
    customer = release["store"].create_project("customer")
    key = release["store"].create_key("customer-writer", [customer.id], "write")
    result = release["client"].post(
        "/api/projects/customer/notes",
        headers={"x-api-key": key},
        json={"text": "Private zebra material.", "title": "customer"},
    )
    assert result.status_code in (200, 201)
    page = release["client"].post(
        "/help", data={"q": "zebra"}, headers={"sec-fetch-site": "same-origin"}
    )
    assert "Private zebra material" not in page.text
    assert "No documentation passages matched" in page.text


def test_whole_file_section_context_is_available_to_search(release):
    artifact = release["artifact"]
    first = artifact["notes"][0]
    first["payload"]["text"] = "# Guides\n\n## Special section\n\nFirst passage.\n"
    first["end_line"] = 5
    first["payload"]["source_ref"] = first["payload"]["source_ref"].replace("#L1-L3", "#L1-L5")
    second = json.loads(json.dumps(first))
    second["start_line"] = second["end_line"] = 6
    second["page"] = "docs-connect-0002"
    second["payload"].update(
        {
            "text": "Second passage.\n",
            "title": second["page"],
            "topic": second["page"],
            "source_ref": first["payload"]["source_ref"].replace("#L1-L5", "#L6-L6"),
        }
    )
    # Keep the two passages distinct across the group boundary.
    first["payload"]["text"] += "\n"
    first["end_line"] = 6
    first["payload"]["source_ref"] = first["payload"]["source_ref"].replace("#L1-L5", "#L1-L6")
    second["start_line"] = second["end_line"] = 7
    second["payload"]["source_ref"] = second["payload"]["source_ref"].replace("#L6-L6", "#L7-L7")
    artifact["notes"].insert(1, second)
    release["path"].write_text(json.dumps(artifact))
    from cairn.web.docs_helper import _artifact

    _, entries = _artifact(release["path"], release["sha"])
    assert entries[second["page"]]["contexts"][(1, 1, "Second passage.")] == (
        "Guides",
        "Special section",
    )


@pytest.mark.parametrize("change", ["boundary", "heading_reinterpretation"])
def test_release_rejects_divided_markdown_blocks(release, change):
    artifact = release["artifact"]
    note = artifact["notes"][0]
    note2 = json.loads(json.dumps(note))
    note["payload"]["text"] = "# Connect\n\n```python\nprint('hi')\n"
    note["end_line"] = 4
    note["payload"]["source_ref"] = note["payload"]["source_ref"].replace("#L1-L3", "#L1-L4")
    note2["start_line"] = 5
    note2["end_line"] = 5
    note2["page"] = "docs-connect-0002"
    note2["payload"].update(
        {
            "text": "```\n",
            "title": note2["page"],
            "topic": note2["page"],
            "source_ref": note["payload"]["source_ref"].replace("#L1-L4", "#L5-L5"),
        }
    )
    if change == "heading_reinterpretation":
        note2["payload"]["text"] = "# fake heading\n"
    artifact["notes"].insert(1, note2)
    release["path"].write_text(json.dumps(artifact))
    page = release["client"].post(
        "/help", data={"q": "connect"}, headers={"sec-fetch-site": "same-origin"}
    )
    assert "Documentation search is unavailable" in page.text


def test_search_uses_post_body_so_query_is_not_in_url(release):
    response = release["client"].post(
        "/help", data={"q": "connect"}, headers={"sec-fetch-site": "same-origin"}
    )
    assert response.status_code == 200
    assert 'method="post"' in response.text
    assert "Read the connect guide." in response.text


def test_unexpected_new_page_version_rejects_the_release(release):
    store = release["store"]
    project = release["project"]
    page = store.get_page(project.id, "docs-connect-0001")
    store.save_page(project.id, page.slug, page.title, page.body, page.claims)
    result = release["client"].post(
        "/help", data={"q": "connect"}, headers={"sec-fetch-site": "same-origin"}
    )
    assert "Documentation search is unavailable" in result.text
    assert "Read the connect guide." not in result.text


def test_full_file_secret_split_across_groups_is_rejected(release):
    from cairn.web.docs_helper import _artifact

    artifact = release["artifact"]
    note = artifact["notes"][0]
    note["payload"]["text"] = "# Connect\n\npassword = \n"
    second = json.loads(json.dumps(note))
    second["start_line"] = second["end_line"] = 4
    second["page"] = "docs-connect-0002"
    second["payload"].update(
        {
            "text": "SyntheticCredentialValue123456\n",
            "title": second["page"],
            "topic": second["page"],
            "source_ref": note["payload"]["source_ref"].replace("#L1-L3", "#L4-L4"),
        }
    )
    artifact["notes"].insert(1, second)
    release["path"].write_text(json.dumps(artifact))
    with pytest.raises(ValueError):
        _artifact(release["path"], release["sha"])


def test_default_no_model_tidy_is_accepted(release):
    project = release["project"]
    store = release["store"]
    page = store.get_page(project.id, "docs-connect-0001")
    store.save_page(
        project.id,
        page.slug,
        page.title,
        page.body,
        page.claims,
        {**page.metrics, "arranged_by": "cairn"},
    )
    assert store.page_versions(project.id, "docs-connect-0001") == [1, 2]
    result = release["client"].post(
        "/help", data={"q": "connect"}, headers={"sec-fetch-site": "same-origin"}
    )
    assert "Read the connect guide." in result.text


@pytest.mark.parametrize("release", ["large"], indirect=True)
def test_many_passages_complete_verification_and_search_offline(release):
    result = release["client"].post(
        "/help", data={"q": "item 59 in privacy"}, headers={"sec-fetch-site": "same-origin"}
    )
    assert "Guide item 59 in privacy." in result.text
    assert "#L62-L62" in result.text


@pytest.mark.parametrize(
    "setting",
    ["docs_model_enabled", "docs_model", "docs_model_budget_micros", "docs_model_price"],
)
def test_the_helper_takes_no_model(tmp_path, setting):
    store = Store(tmp_path / "cairn.db")
    with pytest.raises(TypeError):
        create_app(store, **{setting: None})
    store.close()


def _search(client, query="connect"):
    return client.post("/help", data={"q": query}, headers={"sec-fetch-site": "same-origin"})


class Seen(list):
    """The statements run, with `rows`, the rows they returned, and `text`, the characters
    of text and bytes those rows held."""

    rows = 0
    text = 0


@contextlib.contextmanager
def watched(store):
    """The statements run on the store's connection, transactions' own left out."""
    seen = Seen()
    quiet = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}
    con, before = store._con, store._con.row_factory

    def traced(statement):
        text = statement.lstrip()
        if text and not text.startswith("--") and text.split()[0].upper() not in quiet:
            seen.append(text)

    def rows(cursor, row):
        seen.rows += 1
        seen.text += sum(len(value) for value in row if isinstance(value, (str, bytes)))
        return row if before is None else before(cursor, row)

    con.set_trace_callback(traced)
    con.row_factory = rows
    try:
        yield seen
    finally:
        con.set_trace_callback(None)
        con.row_factory = before


def _verifications(statements):
    """How many verifications ran: each counts the project's pages once, first."""
    return sum("updated_at FROM page_heads" in statement for statement in statements)


@pytest.mark.parametrize("mutation", ["passage", "source"])
def test_evidence_that_differs_from_the_release_is_refused(release, mutation):
    note = release["artifact"]["notes"][0]["payload"]
    tampered = {**note, "text": "# connect\n\nInjected customer content.\n"}
    if mutation == "source":
        tampered = {**note, "source_ref": "https://evil.example.test/"}
    result = release["client"].post(
        f"/api/projects/{release['project_info']['name']}/notes",
        headers={"x-api-key": release["writer"]},
        json=tampered,
    )
    assert result.status_code in (200, 201), result.text
    page = _search(release["client"])
    assert "Documentation search is unavailable" in page.text
    assert "Read the connect guide." not in page.text
    assert "evil.example.test" not in page.text
    assert "Injected customer content" not in page.text


def test_one_verification_then_each_search_costs_the_same_fixed_number_of_statements(release):
    client, store = release["client"], release["store"]
    costs = []
    for _ in range(4):
        with watched(store) as seen:
            assert "Read the connect guide." in _search(client).text
        costs.append(seen)
    assert [_verifications(seen) for seen in costs] == [1, 0, 0, 0]
    assert len(costs[1]) == len(costs[2]) == len(costs[3]) < len(costs[0])


def test_neither_a_search_nor_a_verification_grows_with_the_release(tmp_path, monkeypatch):
    measured = []
    for large in (False, True):
        folder = tmp_path / ("large" if large else "small")
        with _released(folder, monkeypatch, artifact=_artifact_of(large)) as made:
            with watched(made["store"]) as first:
                assert "connect" in _search(made["client"], "connect").text
            with watched(made["store"]) as second:
                _search(made["client"], "connect")
            assert _verifications(first) == 1
            measured.append((len(first), len(second)))
    assert measured[0] == measured[1]


def test_the_release_is_verified_again_only_after_verify_every(release):
    from cairn.web.docs_helper import VERIFY_EVERY

    client, store, now = release["client"], release["store"], release["now"]
    start = now[0]
    with watched(store) as seen:
        _search(client)
        now[0] = start + VERIFY_EVERY - timedelta(seconds=1)
        _search(client)
    assert _verifications(seen) == 1
    with watched(store) as seen:
        now[0] = start + VERIFY_EVERY
        assert "Read the connect guide." in _search(client).text
        _search(client)
    assert _verifications(seen) == 1
    assert timedelta(minutes=10) == VERIFY_EVERY


def test_a_failed_verification_is_tried_again_on_the_next_search(release):
    client, store = release["client"], release["store"]
    release["monkeypatch"].delenv("DOCS_READER")
    assert "Documentation search is unavailable" in _search(client).text
    release["monkeypatch"].setenv("DOCS_READER", release["reader"])
    from cairn.web.docs_helper import RETRY_AFTER

    with watched(store) as seen:
        assert "Documentation search is unavailable" in _search(client).text  # remembered
        release["now"][0] += RETRY_AFTER
        assert "Read the connect guide." in _search(client).text
    assert _verifications(seen) == 1
    assert timedelta(seconds=60) == RETRY_AFTER


def test_a_release_changed_after_verification_is_refused_at_the_next_one(release):
    from cairn.web.docs_helper import VERIFY_EVERY

    client, store, project = release["client"], release["store"], release["project"]
    assert "Read the connect guide." in _search(client).text
    page = store.get_page(project.id, "docs-connect-0001")
    store.save_page(project.id, page.slug, page.title, page.body, page.claims)
    assert "Read the connect guide." in _search(client).text  # kept until the next one
    release["now"][0] += VERIFY_EVERY
    assert "Documentation search is unavailable" in _search(client).text
    assert "Documentation search is unavailable" in _search(client).text


def test_a_revoked_reader_is_refused_at_the_next_search(release):
    client = release["client"]
    assert "Read the connect guide." in _search(client).text
    release["store"].revoke_key("docs-reader")
    page = _search(client)
    assert "Documentation search is unavailable" in page.text
    assert "Read the connect guide." not in page.text


def test_no_store_call_or_release_parsing_runs_on_the_event_loop(release, monkeypatch):
    import asyncio

    from cairn.web import docs_helper
    from cairn.web.auth import Accounts

    on_loop, used = [], []

    def watch(owner, name):
        original = getattr(owner, name)

        def called(*args, **kwargs):
            if name == "memory":
                if args[1].kind != "key":  # the caller's session, admitted by Auth
                    return original(*args, **kwargs)
                used.append(args[1].label)
            try:
                asyncio.get_running_loop()
                on_loop.append(name)
            except RuntimeError:
                pass
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, called)

    for name in ("get_project", "first_page_heads", "page_claims", "spans_within", "note_sources"):
        watch(Store, name)
    watch(docs_helper, "_artifact")
    watch(Accounts, "memory")
    for _ in range(2):
        assert "Read the connect guide." in _search(release["client"]).text
    assert on_loop == []
    assert used and set(used) == {"docs-reader"}


def test_a_plan_rate_limit_does_not_fail_verification_and_it_writes_no_access_log(
    tmp_path, monkeypatch, caplog
):
    import logging

    from cairn.plans import Plan, Plans

    plans = Plans({"free": Plan(label="Free", requests_per_minute=12)}, "free")
    with (
        _released(tmp_path, monkeypatch, plans=plans) as made,
        caplog.at_level(logging.INFO, logger="cairn.access"),
    ):
        caplog.clear()
        made["client"].get("/api/projects", headers={"x-api-key": made["reader"]})
        assert [r for r in caplog.records if r.name == "cairn.access"]  # the log records
        caplog.clear()
        assert "Read the connect guide." in _search(made["client"]).text
        assert [r for r in caplog.records if r.name == "cairn.access"] == []
        with watched(made["store"]) as planned:
            _search(made["client"])
        with watched(made["store"]) as framed:
            made["client"].get("/help")
    with _released(tmp_path / "no-plans", monkeypatch) as made:
        _search(made["client"])
        with watched(made["store"]) as unplanned:
            _search(made["client"])
        with watched(made["store"]) as unframed:
            made["client"].get("/help")
    # With plans, the key's check reads the plan in force too: one statement more.
    assert len(planned) - len(framed) == len(unplanned) - len(unframed) + 1


def test_one_verification_at_a_time_and_the_second_search_uses_it(release, monkeypatch):
    import threading

    from cairn.compiler import provenance_gate
    from cairn.web import docs_helper
    from cairn.web.auth import Accounts

    store = release["store"]
    helper = docs_helper.Help(
        store.identify_key,
        Accounts(store, provenance_gate),
        release["path"],
        release["sha"],
        "DOCS_READER",
        lambda: release["now"][0],
    )
    entered, go, waiting = threading.Event(), threading.Event(), threading.Event()
    parsed = []
    original = docs_helper._artifact

    def slow(*args):
        parsed.append(1)
        entered.set()
        go.wait()
        return original(*args)

    monkeypatch.setattr(docs_helper, "_artifact", slow)

    class Watched:
        def __init__(self, lock):
            self.lock = lock

        def __enter__(self):
            if self.lock.locked():
                waiting.set()
            return self.lock.__enter__()

        def __exit__(self, *exc):
            return self.lock.__exit__(*exc)

    helper._lock = Watched(helper._lock)
    answers = []
    first = threading.Thread(target=lambda: answers.append(helper.search("connect")))
    second = threading.Thread(target=lambda: answers.append(helper.search("connect")))
    first.start()
    entered.wait()
    second.start()
    waiting.wait()
    go.set()
    first.join()
    second.join()
    assert len(parsed) == 1
    assert len(answers) == 2 and answers[0] == answers[1] and answers[0]


def test_one_verification_of_the_real_guides_costs_what_a_small_release_costs(
    tmp_path, monkeypatch
):
    import subprocess
    from importlib.resources import files

    from cairn import docs_publisher

    repo = tmp_path / "repo"

    def run(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    (repo / "src/cairn/guides").mkdir(parents=True)
    run("init", "-q")
    run("config", "user.name", "Synthetic Author")
    run("config", "user.email", "author@example.test")
    run("remote", "add", "origin", REPO + ".git")
    for name in ("connect", "patterns", "sources", "security", "run", "privacy"):
        text = files("cairn.guides").joinpath(f"{name}.md").read_text()
        (repo / f"src/cairn/guides/{name}.md").write_text(text)
    run("add", ".")
    run("commit", "-qm", "The guides")
    real = docs_publisher.build_release(repo, run("rev-parse", "HEAD"))
    costs = []
    for name, artifact in (("small", _artifact_of()), ("real", real)):
        with _released(tmp_path / name, monkeypatch, artifact=artifact) as made:
            with watched(made["store"]) as verified:
                page = _search(made["client"], "nonexistentword")
                assert "No documentation passages matched" in page.text
            with watched(made["store"]) as searched:
                _search(made["client"], "nonexistentword")
            with watched(made["store"]) as framed:  # the page with no search: the session's
                made["client"].get("/help")
            assert _verifications(verified) == 1
            pages = len(artifact["notes"])
            costs.append(
                {
                    "pages": pages,
                    "verification": len(verified) - len(framed),
                    "search": len(searched) - len(framed),
                    "rows": verified.rows - framed.rows,
                    "text": verified.text - framed.text,
                    "search_rows": searched.rows - framed.rows,
                }
            )
    small, real = costs
    # A search costs the same whatever the release; a verification reads one page per page
    # the artifact has (two statements each) and otherwise the same.
    assert real["search"] == small["search"]
    assert real["search_rows"] == small["search_rows"]
    assert real["verification"] - small["verification"] == 2 * (real["pages"] - small["pages"])
    # pytest -s shows them, the session's own reads left out
    print("real guides:", real)


def test_markers_are_filled_as_the_guides_fill_them(tmp_path, monkeypatch):
    line = "Read the {{name}} guide: name it YOUR-{{SLUG}} or {{slug}}-agent."
    artifact = _artifact_of(lines={"connect": line})
    with _released(tmp_path, monkeypatch, artifact=artifact) as made:
        page = _search(made["client"], "YOUR-CAIRN").text
    assert "Read the cairn guide: name it YOUR-CAIRN or cairn-agent." in page
    assert "{{" not in page
    assert f"{REPO}/blob/{SHA}/src/cairn/guides/connect.md#L3-L3" in page


class GitHubFake:
    def __call__(self, request):
        import httpx

        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_made_up"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={"id": 7, "login": "ada", "name": "Ada Quill"})
        if url == "https://api.github.com/user/emails":
            emails = [{"email": "ada@example.test", "primary": True, "verified": True}]
            return httpx.Response(200, json=emails)
        return httpx.Response(404)


def test_under_a_brand_no_passage_names_the_software_and_no_source_is_linked(tmp_path, monkeypatch):
    import httpx

    from cairn.identity import GitHub
    from cairn.server import SignIn

    lines = {
        "connect": "Read the {{name}} guide: name it YOUR-{{SLUG}}.",
        "sources": "Read the sources guide, then run uvx cairn.",
        "security": "Read the security guide; CAIRN_API_KEY holds the key.",
    }
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake()))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    options = {"public_url": "https://kb.example.test", "sign_in": sign_in}
    with _released(
        tmp_path,
        monkeypatch,
        artifact=_artifact_of(lines=lines),
        brand_name="Knowledge Base",
        **options,
    ) as made:
        client = made["client"]
        client.base_url = "https://kb.example.test"
        started = client.get("/auth/github", follow_redirects=False)
        state = dict(httpx.URL(started.headers["location"]).params)["state"]
        client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
        page = _search(client, "read the").text
    shown = page.split("<ul>", 1)[1].split("</ul>", 1)[0]
    assert "Read the Knowledge Base guide: name it YOUR-KNOWLEDGE-BASE." in shown
    assert "Read the patterns guide." in shown
    assert "Read the privacy guide." in shown
    assert "run guide" not in shown  # the operator's guide is not one of the guides then
    assert "sources guide" not in shown  # it names the software outside an identifier
    # An identifier stays as it is, as /docs keeps it (tests/unit/test_brand_inside_unit.py).
    assert "Read the security guide; CAIRN_API_KEY holds the key." in shown
    assert "cairn" not in page.replace("CAIRN_API_KEY", "").lower()
    assert "github.com" not in page
    assert ">Source</a>" not in page


def test_an_unexpected_error_is_logged_by_its_class_only(release, caplog, monkeypatch):
    from cairn.web import docs_helper

    def broken(*args):
        raise RuntimeError("DOCS_SECRET_TEXT connect")

    monkeypatch.setattr(docs_helper, "_artifact", broken)
    caplog.set_level("INFO", logger="cairn.web.docs_helper")
    page = _search(release["client"])
    assert "Documentation search is unavailable" in page.text
    assert _logged(caplog) == ["help unavailable: error RuntimeError"]


def _grown(made, pages=0, claims=0):
    """The release project holding `pages` more pages, each a copy of the connect page, or
    the connect page with `claims` claims, copies of its own (a writer could do either)."""
    from cairn.store.models import Claim

    store, project = made["store"], made["project"]
    page = store.get_page(project.id, "docs-connect-0001")
    for i in range(pages):
        store.save_page(project.id, f"extra-{i:04d}", f"extra-{i:04d}", page.body, page.claims)
    if claims:
        claim = page.claims[0]
        many = tuple(
            Claim(id=f"{claim.id}-{i}", text=claim.text, span_ids=claim.span_ids)
            for i in range(claims)
        )
        store.save_page(project.id, page.slug, page.title, page.body, many)


@pytest.mark.parametrize(
    "grown",
    [{"pages": 1}, {"pages": 500}, {"claims": 2}, {"claims": 2000}],
    ids=["1-page", "500-pages", "2-claims", "2000-claims"],
)
def test_a_release_project_that_holds_more_is_refused_after_a_fixed_number_of_rows(
    tmp_path, monkeypatch, caplog, grown
):
    measured = {}
    for name, grow in (("whole", {}), ("grown", grown)):
        with _released(tmp_path / name, monkeypatch) as made:
            _grown(made, **grow)
            caplog.clear()
            caplog.set_level("INFO", logger="cairn.web.docs_helper")
            with watched(made["store"]) as seen:
                page = _search(made["client"], "nonexistentword")
            with watched(made["store"]) as framed:  # the session's own reads
                made["client"].get("/help")
            measured[name] = (seen.rows - framed.rows, seen.text - framed.text)
            logged = _logged(caplog)
    assert "Documentation search is unavailable" in page.text
    assert logged == ["help unavailable: release_too_large"]
    rows, text = measured["grown"]
    # The scoped key's check (5 rows), the reach of the view (1) and at most
    # the release's six heads and one more: whatever the project holds, nothing else is read
    # before it is refused (pytest -s shows the numbers).
    print("refused after", rows, "rows,", text, "characters; a whole release", measured["whole"])
    assert rows <= 5 + 1 + 7
    assert text < 2_000
    assert rows < measured["whole"][0]


def test_a_failed_verification_is_remembered_for_retry_after(release):
    from cairn.web.docs_helper import RETRY_AFTER

    client, store, now = release["client"], release["store"], release["now"]
    _grown(release, pages=1)
    with watched(store) as failing:
        assert "Documentation search is unavailable" in _search(client).text
        now[0] += RETRY_AFTER - timedelta(seconds=1)
        assert "Documentation search is unavailable" in _search(client).text
    assert _verifications(failing) == 1
    with watched(store) as kept_back:
        assert "Documentation search is unavailable" in _search(client).text
    assert _verifications(kept_back) == 0
    with watched(store) as framed:
        client.get("/help")
    assert kept_back.rows - framed.rows == 5  # the scoped reader key's own check, nothing else
    now[0] += timedelta(seconds=1)
    with watched(store) as again:
        assert "Documentation search is unavailable" in _search(client).text
    assert _verifications(again) == 1


@contextlib.contextmanager
def stepped(store):
    """How many SQLite virtual machine steps the store's connection runs: `steps[0]`."""
    steps = [0]

    def step():
        steps[0] += 1
        return 0

    store._con.set_progress_handler(step, 1)
    try:
        yield steps
    finally:
        store._con.set_progress_handler(None, 1)


def _many_pages(made, count):
    """`count` pages more in the release project, each a copy of the connect page."""
    store, project = made["store"], made["project"]
    page = store.get_page(project.id, "docs-connect-0001")
    for i in range(count):
        store.save_page(project.id, f"extra-{i:05d}", f"extra-{i:05d}", page.body, page.claims)


STEP_TOLERANCE = 0  # the refused verification runs the same statements on the same rows


def test_a_refused_verification_costs_the_same_steps_however_many_pages_are_added(
    tmp_path, monkeypatch, caplog
):
    measured = []
    for count in (1, 500, 5000):
        with _released(tmp_path / str(count), monkeypatch) as made:
            _many_pages(made, count)
            caplog.clear()
            caplog.set_level("INFO", logger="cairn.web.docs_helper")
            with stepped(made["store"]) as steps:
                page = _search(made["client"], "nonexistentword")
            assert "Documentation search is unavailable" in page.text
            assert _logged(caplog) == ["help unavailable: release_too_large"]
            measured.append(steps[0])
    print("VM steps of a refused verification, 1, 500 and 5,000 pages added:", measured)
    assert max(measured) - min(measured) <= STEP_TOLERANCE


def _tidied(made, body=None, claims=None):
    """The connect page saved again as the no-model tidy would, with `body` and `claims` in
    place of its own when given."""
    store, project = made["store"], made["project"]
    page = store.get_page(project.id, "docs-connect-0001")
    store.save_page(
        project.id,
        page.slug,
        page.title,
        page.body if body is None else body,
        page.claims if claims is None else claims,
        {**page.metrics, "arranged_by": "cairn"},
    )


def test_a_page_body_is_never_read(tmp_path, monkeypatch):
    measured = []
    for name, body in (("own", None), ("large", "x" * 524_288)):
        with _released(tmp_path / name, monkeypatch) as made:
            _tidied(made, body=body)
            with watched(made["store"]) as seen:
                page = _search(made["client"], "connect")
            assert "Read the connect guide." in page.text
            measured.append((seen.rows, seen.text))
    print("rows and characters of one verification, own and large body:", measured)
    assert measured[0] == measured[1]


def test_a_claim_text_longer_than_the_release_allows_is_refused_before_it_is_read(
    tmp_path, monkeypatch, caplog
):
    from cairn.store.models import Claim

    measured = {}
    for name in ("own", "long"):
        with _released(tmp_path / name, monkeypatch) as made:
            store, project = made["store"], made["project"]
            claim = store.get_page(project.id, "docs-connect-0001").claims[0]
            if name == "long":
                long = Claim(id=claim.id, text="y" * 200_000, span_ids=claim.span_ids)
                _tidied(made, claims=(long,))
            else:
                _tidied(made)
            caplog.clear()
            caplog.set_level("INFO", logger="cairn.web.docs_helper")
            with watched(made["store"]) as seen:
                page = _search(made["client"], "connect")
            measured[name] = (seen.rows, seen.text, _logged(caplog), page.text)
    assert "Read the connect guide." in measured["own"][3]
    assert "Documentation search is unavailable" in measured["long"][3]
    assert measured["long"][2] == ["help unavailable: release_too_large"]
    assert measured["long"][1] < measured["own"][1]  # the long text was not fetched
    assert measured["long"][1] < 10_000


def test_a_guide_the_publisher_accepts_is_one_help_reads(tmp_path, monkeypatch):
    """One bound for a guide's size, the publisher's: a release with a guide longer than
    64 KiB (and under the publisher's bound) is built, published and searched."""
    import subprocess
    from importlib.resources import files

    from cairn import docs_publisher

    repo = tmp_path / "repo"

    def run(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    (repo / "src/cairn/guides").mkdir(parents=True)
    run("init", "-q")
    run("config", "user.name", "Synthetic Author")
    run("config", "user.email", "author@example.test")
    run("remote", "add", "origin", REPO + ".git")
    for name in ("connect", "patterns", "sources", "security", "run", "privacy"):
        text = files("cairn.guides").joinpath(f"{name}.md").read_text()
        if name == "sources":
            more = "\n## Padding for the bound\n\n" + "".join(
                f"The quillwort ledger holds line {n} of made-up padding.\n\n" for n in range(260)
            )
            text += more
            assert 70_000 < len(text.encode()) < docs_publisher.MAX_FILE_BYTES
        (repo / f"src/cairn/guides/{name}.md").write_text(text)
    run("add", ".")
    run("commit", "-qm", "The guides, one of them long")
    long = docs_publisher.build_release(repo, run("rev-parse", "HEAD"))
    with _released(tmp_path / "long", monkeypatch, artifact=long) as made:
        page = _search(made["client"], "quillwort")
        assert "Documentation search is unavailable" not in page.text
        assert "quillwort ledger" in page.text


def test_a_passage_is_shown_as_the_guides_show_it(tmp_path, monkeypatch):
    """A passage is Markdown: shown rendered, as /docs and a claim are, never as its raw
    text on one long line. Raw HTML in it stays text."""
    line = "Use **bold words** and `a_code_span` with <b>raw</b> html in the quillwort."
    with _released(tmp_path, monkeypatch, artifact=_artifact_of(lines={"connect": line})) as made:
        page = _search(made["client"], "quillwort").text
    assert "<strong>bold words</strong>" in page and "<code>a_code_span</code>" in page
    assert "&lt;b&gt;raw&lt;/b&gt;" in page and "<b>raw</b>" not in page
    assert "**bold" not in page and "<pre>" not in page


def test_a_search_finds_passages_that_hold_every_word_and_the_phrase_first(tmp_path, monkeypatch):
    """A question is not a phrase: a passage that holds every word of the search is found,
    whatever their order; one that holds the words as typed comes first; one that lacks a
    word is not shown."""
    lines = {
        "connect": "Connect the quill to the ledger.",
        "patterns": "A ledger quill pairs well.",
        "sources": "A quill alone.",
    }
    with _released(tmp_path, monkeypatch, artifact=_artifact_of(lines=lines)) as made:
        page = _search(made["client"], "Ledger  quill").text
        nothing = _search(made["client"], "quill zebra").text
    assert "A ledger quill pairs well." in page and "Connect the quill to the ledger." in page
    assert page.index("A ledger quill pairs well.") < page.index("Connect the quill to the")
    assert "A quill alone." not in page
    assert "No documentation passages matched" in nothing


def test_a_link_to_a_section_of_its_guide_goes_to_that_guide(tmp_path, monkeypatch):
    """A passage's link to a heading of its own guide ("#claude-code") leads nowhere on the
    Help page: it goes to that guide's page."""
    line = "For the quillwort see [the section](#claude-code)."
    with _released(tmp_path, monkeypatch, artifact=_artifact_of(lines={"connect": line})) as made:
        page = _search(made["client"], "quillwort").text
    assert 'href="/docs/connect#claude-code"' in page and 'href="#claude-code"' not in page
