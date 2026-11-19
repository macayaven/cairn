"""Graph projection reads only bounded, explicitly cited project content."""

import importlib.util

from cairn.store import Claim, Store


def test_graph_contains_only_the_explicit_page_claim_and_note_evidence(tmp_path):
    assert importlib.util.find_spec("cairn.graph") is not None, "graph projection is missing"
    from cairn.graph import project_graph

    store = Store(tmp_path / "graph.db")
    try:
        project = store.create_project("alpha")
        note, _ = store.add_note(
            project.id, "# Source\n\nThe lantern is blue.\n", source_ref="desk:1", via="email"
        )
        passage = store.note_spans(note.id)[-1]
        store.save_page(
            project.id,
            "lantern",
            "Lantern",
            "",
            [Claim(id="cl_one", text="The lantern is blue.", span_ids=(passage.id,))],
        )
        graph = project_graph(store, "alpha")
        kinds = {node["kind"] for node in graph["nodes"]}
        assert kinds == {"project", "page", "claim", "passage", "note"}
        assert {(edge["kind"], edge["from"], edge["to"]) for edge in graph["edges"]} == {
            ("has page", "project:" + project.id, "page:lantern"),
            ("has note", "project:" + project.id, "note:" + note.id),
            ("has claim", "page:lantern", "claim:0:lantern"),
            ("cites", "claim:0:lantern", "passage:" + passage.id),
            ("has passage", "note:" + note.id, "passage:" + passage.id),
        }
        shown_note = next(n for n in graph["nodes"] if n["kind"] == "note")
        assert shown_note["source_ref"] == "desk:1"
        assert shown_note["via"] == "email"
        assert graph["truncated"] is False
        assert graph["counts"] == {
            "project": 1,
            "page": 1,
            "claim": 1,
            "passage": 1,
            "note": 1,
            "edges": 5,
        }
    finally:
        store.close()


def test_duplicate_citations_do_not_create_duplicate_edges(tmp_path):
    from cairn.graph import project_graph

    store = Store(tmp_path / "duplicates.db")
    try:
        project = store.create_project("alpha")
        note, _ = store.add_note(project.id, "A blue lantern.\n")
        passage = store.note_spans(note.id)[0]
        store.save_page(
            project.id,
            "lantern",
            "Lantern",
            "",
            [Claim(id="one", text="Blue", span_ids=(passage.id, passage.id))],
        )
        graph = project_graph(store, "alpha")
        cites = [edge for edge in graph["edges"] if edge["kind"] == "cites"]
        assert len(cites) == 1
    finally:
        store.close()


def test_many_missing_citations_cannot_force_unbounded_database_reads(tmp_path):
    from cairn.graph import project_graph

    store = Store(tmp_path / "citations.db")
    try:
        project = store.create_project("alpha")
        store.save_page(
            project.id,
            "page",
            "Page",
            "",
            [Claim(id="one", text="A claim", span_ids=tuple(f"missing-{i}" for i in range(500)))],
        )
        statements = []
        store._observe(trace=statements.append)
        try:
            graph = project_graph(store, "alpha")
        finally:
            store._observe(trace=None)
        lookups = [s for s in statements if "FROM spans s JOIN notes" in s]
        assert len(lookups) <= 100
        assert graph["truncated"] is True
    finally:
        store.close()


def test_nested_scope_depth_fails_closed_before_database_work(tmp_path):
    import pytest

    from cairn.graph import GraphUnavailable, project_graph

    store = Store(tmp_path / "scope.db")
    try:
        project = store.create_project("alpha")
        view = store
        for _ in range(9):
            view = view.narrow([project.id])
        with pytest.raises(GraphUnavailable):
            project_graph(view, "alpha")
    finally:
        store.close()


def test_scoped_sets_and_person_access_are_intersected_and_current(tmp_path):
    import pytest

    from cairn.graph import project_graph

    store = Store(tmp_path / "reach.db")
    try:
        issuer = "https://id.example.test"
        store.sign_in("sso", "owner", "owner@example.test", "Owner", issuer=issuer, join="default")
        signed = store.sign_in(
            "sso", "member", "member@example.test", "Member", issuer=issuer, join="default"
        )
        person = store.for_account(signed.account_id, signed.user.id)
        alpha = store.create_project("alpha", everyone="none")
        beta = store.create_project("beta", everyone="none")
        group = store.create_set("group", [alpha.id])
        view = person.narrow([group.id])
        for name in ("alpha", "beta", "missing"):
            with pytest.raises(KeyError) as exc:
                project_graph(view, name)
            assert exc.value.args == (name,)
        store.set_project_access(alpha.id, people={signed.user.id: "read"})
        assert project_graph(view, "alpha")["nodes"][0]["label"] == "alpha"
        store.update_set(group.id, [beta.id])
        with pytest.raises(KeyError):
            project_graph(view, "alpha")
        store.update_set(group.id, [alpha.id])
        store.set_project_access(alpha.id, people={})
        with pytest.raises(KeyError):
            project_graph(view, "alpha")
    finally:
        store.close()


def test_scope_set_larger_than_representation_bound_is_unavailable(tmp_path):
    import pytest

    from cairn.graph import GraphUnavailable, project_graph

    store = Store(tmp_path / "large-set.db")
    try:
        project = store.create_project("alpha")
        group = store.create_set("group", [project.id])
        with store._write() as con:
            con.execute(
                "UPDATE project_sets SET project_ids = ? WHERE id = ?",
                ('["' + "a" * 70000 + '"]', group.id),
            )
        with pytest.raises(GraphUnavailable):
            project_graph(store.narrow([group.id]), "alpha")
    finally:
        store.close()


def test_page_selection_is_deterministic_and_reports_truncation(tmp_path):
    from cairn.graph import project_graph

    store = Store(tmp_path / "order.db")
    try:
        project = store.create_project("alpha")
        for slug in ("c", "a", "b"):
            store.save_page(project.id, slug, slug.upper(), "", [])
        first = project_graph(store, "alpha", limit=2)
        second = project_graph(store, "alpha", limit=2)
        assert first == second
        assert [n["slug"] for n in first["nodes"] if n["kind"] == "page"] == ["a", "b"]
        assert first["truncated"] is True
        assert all(
            e["from"] in {n["id"] for n in first["nodes"]}
            and e["to"] in {n["id"] for n in first["nodes"]}
            for e in first["edges"]
        )
    finally:
        store.close()


def test_database_work_does_not_grow_with_inaccessible_projects(tmp_path):
    from cairn.graph import project_graph

    store = Store(tmp_path / "work.db")
    try:
        issuer = "https://id.example.test"
        store.sign_in("sso", "owner", "owner@example.test", "Owner", issuer=issuer, join="default")
        signed = store.sign_in(
            "sso", "member", "member@example.test", "Member", issuer=issuer, join="default"
        )
        person = store.for_account(signed.account_id, signed.user.id)
        project = store.create_project("alpha", everyone="read")
        view = person.narrow([project.id])

        def measure():
            operations = [0]
            statements = []

            def count():
                operations[0] += 1
                return 0

            store._observe(progress=(count, 1))
            store._observe(trace=statements.append)
            try:
                result = project_graph(view, "alpha")
            finally:
                store._observe(progress=None)
                store._observe(trace=None)
            return result, len(statements), operations[0]

        small, small_statements, small_operations = measure()
        for i in range(300):
            store.create_project(f"hidden-{i:04}", everyone="none")
        large, large_statements, large_operations = measure()
        assert large == small
        assert large_statements == small_statements
        assert large_operations <= small_operations + 50
    finally:
        store.close()


def test_uncompiled_note_is_in_the_project_graph(tmp_path):
    from cairn.graph import project_graph

    store = Store(tmp_path / "uncompiled.db")
    try:
        project = store.create_project("alpha")
        note, _ = store.add_note(project.id, "A note awaiting compilation.\n", source_ref="desk:2")
        graph = project_graph(store, "alpha")
        assert any(n["id"] == "note:" + note.id for n in graph["nodes"])
        assert {
            "kind": "has note",
            "from": "project:" + project.id,
            "to": "note:" + note.id,
        } in graph["edges"]
    finally:
        store.close()


def test_foreign_passages_and_notes_are_never_returned_even_when_cited(tmp_path):
    from cairn.graph import project_graph

    store = Store(tmp_path / "foreign.db")
    try:
        alpha = store.create_project("alpha")
        beta = store.create_project("beta")
        foreign, _ = store.add_note(beta.id, "Foreign secret.\n", source_ref="foreign-ref")
        passage = store.note_spans(foreign.id)[0]
        store.save_page(alpha.id, "a", "A", "", [Claim(id="one", text="A", span_ids=(passage.id,))])
        graph = project_graph(store, "alpha")
        assert "Foreign secret" not in str(graph)
        assert "foreign-ref" not in str(graph)
        assert foreign.id not in str(graph)
        assert passage.id not in str(graph)
        assert graph["counts"] == {
            "project": 1,
            "page": 1,
            "claim": 1,
            "passage": 0,
            "note": 0,
            "edges": 2,
        }
    finally:
        store.close()


def test_scoped_key_view_cannot_reach_another_project(tmp_path):
    import pytest

    from cairn.graph import project_graph

    store = Store(tmp_path / "key.db")
    try:
        alpha = store.create_project("alpha")
        store.create_project("beta")
        key = store.create_key("reader", projects=[alpha.id], access="read")
        principal = store.resolve_key(key)
        view = store.for_account(principal.account_id).narrow(principal.projects, principal.access)
        assert project_graph(view, "alpha")["nodes"][0]["label"] == "alpha"
        with pytest.raises(KeyError):
            project_graph(view, "beta")
    finally:
        store.close()


def test_oversize_page_claims_are_omitted_without_deserializing_them(tmp_path):
    from cairn.graph import project_graph

    store = Store(tmp_path / "large-page.db")
    try:
        project = store.create_project("alpha")
        store.save_page(project.id, "a", "A", "", [])
        with store._write() as con:
            con.execute(
                "UPDATE page_versions SET claims = ? WHERE project_id = ?",
                ('["' + "a" * 300000 + '"]', project.id),
            )
        graph = project_graph(store, "alpha")
        assert graph["counts"]["claim"] == 0
        assert graph["truncated"] is True
    finally:
        store.close()


def test_edge_cap_applies_even_to_pages_after_citation_truncation(tmp_path):
    from cairn.graph import project_graph

    store = Store(tmp_path / "edge-cap.db")
    try:
        project = store.create_project("alpha")
        claims = []
        for i in range(100):
            note, _ = store.add_note(project.id, f"Unique passage number {i}.\n")
            passage = store.note_spans(note.id)[0]
            claims.append(Claim(id=f"c{i}", text=f"Claim {i}", span_ids=(passage.id,)))
        store.save_page(project.id, "a", "A", "", claims)
        for i in range(19):
            store.save_page(project.id, f"page-{i:02}", f"Page {i}", "", [])
        graph = project_graph(store, "alpha")
        assert len(graph["edges"]) <= 400
        assert graph["truncated"] is True
    finally:
        store.close()


def test_oversized_scope_does_not_reveal_whether_a_project_exists(tmp_path):
    import pytest

    from cairn.graph import GraphUnavailable, project_graph

    store = Store(tmp_path / "scope-disclosure.db")
    try:
        project = store.create_project("alpha")
        group = store.create_set("group", [project.id])
        with store._write() as con:
            con.execute(
                "UPDATE project_sets SET project_ids = ? WHERE id = ?",
                ('["' + "a" * 70000 + '"]', group.id),
            )
        view = store.narrow([group.id])
        for ref in ("alpha", "missing"):
            with pytest.raises(GraphUnavailable):
                project_graph(view, ref)
    finally:
        store.close()


def test_large_project_reads_the_same_bounded_number_of_rows_and_statements(tmp_path):
    from cairn.graph import project_graph

    store = Store(tmp_path / "large-project.db")
    try:
        project = store.create_project("alpha")
        for i in range(30):
            store.save_page(project.id, f"p-{i:04}", f"Page {i}", "", [])

        def measure():
            operations = [0]
            statements = []

            def count():
                operations[0] += 1
                return 0

            store._observe(progress=(count, 1))
            store._observe(trace=statements.append)
            try:
                result = project_graph(store, "alpha")
            finally:
                store._observe(progress=None)
                store._observe(trace=None)
            return result, len(statements), operations[0]

        small, small_statements, small_work = measure()
        for i in range(30, 330):
            store.save_page(project.id, f"p-{i:04}", f"Page {i}", "", [])
        large, large_statements, large_work = measure()
        assert large == small
        assert large["counts"]["page"] == 20
        assert large["truncated"] is True
        assert large_statements == small_statements
        assert large_work <= small_work + 50
    finally:
        store.close()


def test_page_limit_rejects_nonintegers_and_out_of_range_values(tmp_path):
    import pytest

    from cairn.graph import project_graph

    store = Store(tmp_path / "limit.db")
    try:
        store.create_project("alpha")
        for value in (True, False, 0, -1, 21, "1", 1.2, None):
            with pytest.raises(ValueError):
                project_graph(store, "alpha", value)
    finally:
        store.close()


def test_uncompiled_notes_are_bounded_and_ordered_without_scanning_the_project(tmp_path):
    from cairn.graph import project_graph

    store = Store(tmp_path / "many-notes.db")
    try:
        project = store.create_project("alpha")
        for i in range(150):
            store.add_note(project.id, f"Unique note {i}.\n")
        operations = [0]

        def count():
            operations[0] += 1
            return 0

        store._observe(progress=(count, 1))
        try:
            first = project_graph(store, "alpha")
            work = operations[0]
        finally:
            store._observe(progress=None)
        assert first == project_graph(store, "alpha")
        assert first["counts"]["note"] == 100
        assert first["counts"]["edges"] == 100
        assert first["truncated"] is True
        assert work < 5000
    finally:
        store.close()


def test_claims_with_reused_ids_stay_distinct_in_the_graph(tmp_path):
    from cairn.graph import project_graph

    store = Store(tmp_path / "claim-ids.db")
    try:
        project = store.create_project("alpha")
        store.save_page(
            project.id,
            "page",
            "Page",
            "",
            [
                Claim(id="reused", text="First", span_ids=()),
                Claim(id="reused", text="Second", span_ids=()),
            ],
        )
        graph = project_graph(store, "alpha")
        claims = [node for node in graph["nodes"] if node["kind"] == "claim"]
        assert [node["label"] for node in claims] == ["First", "Second"]
        assert len({node["id"] for node in claims}) == 2
    finally:
        store.close()


def test_oversized_page_title_is_omitted_with_truncation(tmp_path):
    from cairn.graph import project_graph

    store = Store(tmp_path / "title.db")
    try:
        project = store.create_project("alpha")
        store.save_page(project.id, "page", "Page", "", [])
        with store._write() as con:
            con.execute(
                "UPDATE page_versions SET title = ? WHERE project_id = ?",
                ("x" * 300000, project.id),
            )
            con.execute(
                "UPDATE page_heads SET title = ? WHERE project_id = ?", ("x" * 300000, project.id)
            )
        graph = project_graph(store, "alpha")
        page = next(node for node in graph["nodes"] if node["kind"] == "page")
        assert page["label"] == "Page title omitted"
        assert graph["truncated"] is True
    finally:
        store.close()
