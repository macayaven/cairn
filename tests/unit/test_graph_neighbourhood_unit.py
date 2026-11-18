"""Bounded exploration reaches synthetic evidence beyond the legacy graph budgets."""

import json

import pytest

from cairn.store import Claim, Store


def batches(store, project, **query):
    from cairn.graph import graph_neighbourhood

    after = None
    for _ in range(1000):
        batch = graph_neighbourhood(store, project, after=after, **query)
        ids = {node["id"] for node in batch["nodes"]}
        assert all(edge["from"] in ids and edge["to"] in ids for edge in batch["edges"])
        assert len(batch["edges"]) <= 400
        assert len(json.dumps(batch).encode()) < 100_000
        yield batch
        after = batch["next"]
        if after is None:
            assert batch["state"] == "complete"
            return
    pytest.fail("continuation did not complete")


def test_all_page_heads_and_uncompiled_notes_are_reachable_past_legacy_caps(tmp_path):
    store = Store(tmp_path / "pages.db")
    try:
        project = store.create_project("alpha")
        for i in range(47):
            store.save_page(project.id, f"page-{i:03}", f"Page {i}", "", [])
        notes = [store.add_note(project.id, f"Synthetic note {i}.")[0] for i in range(113)]
        pages = [
            node
            for batch in batches(store, "alpha", limit=7)
            for node in batch["nodes"]
            if node["kind"] == "page"
        ]
        assert [node["slug"] for node in pages] == [f"page-{i:03}" for i in range(47)]
        assert all(node["focus"] == "page:" + node["slug"] for node in pages)
        shown_notes = [
            node["id"]
            for batch in batches(store, "alpha", focus="notes", limit=11)
            for node in batch["nodes"]
            if node["kind"] == "note"
        ]
        assert len(shown_notes) == len(set(shown_notes)) == 113
        assert set(shown_notes) == {"note:" + note.id for note in notes}
    finally:
        store.close()


def test_oversized_page_uses_current_indexed_claims_without_reading_page_json(tmp_path):

    store = Store(tmp_path / "claims.db")
    try:
        project = store.create_project("alpha")
        claims = [
            Claim(id=f"c{i}", text=f"Claim {i}: " + "x" * 2500, span_ids=()) for i in range(125)
        ]
        store.save_page(
            project.id,
            "large",
            "Large",
            "",
            claims,
            event="page.arranged",
            detail={"from_version": 0, "merged": 0, "moved": 0, "model": "synthetic"},
        )
        assert store._con.execute("SELECT length(claims) FROM page_versions").fetchone()[0] > 262144
        found = [
            node
            for batch in batches(store, "alpha", focus="page:large", limit=13)
            for node in batch["nodes"]
            if node["kind"] == "claim"
        ]
        assert [node["claim_id"] for node in found] == [f"c{i}" for i in range(125)]
        assert len({node["id"] for node in found}) == 125
        assert all(node["omitted"] and node["focus"] == node["id"] for node in found)
    finally:
        store.close()


def test_dense_claim_citations_continue_past_all_old_budgets_without_duplicates(tmp_path):
    from cairn.graph import graph_neighbourhood

    store = Store(tmp_path / "citations.db")
    try:
        project = store.create_project("alpha")
        spans = []
        for i in range(141):
            note, _ = store.add_note(project.id, f"Synthetic evidence {i}.")
            spans.append(store.note_spans(note.id)[0].id)
        store.save_page(
            project.id,
            "dense",
            "Dense",
            "",
            [Claim(id="many", text="Dense claim", span_ids=tuple(spans))],
        )
        first = graph_neighbourhood(store, "alpha", focus="page:dense")
        focus = next(node["focus"] for node in first["nodes"] if node["kind"] == "claim")
        cited = [
            edge["to"]
            for batch in batches(store, "alpha", focus=focus, limit=17)
            for edge in batch["edges"]
            if edge["kind"] == "cites"
        ]
        assert cited == ["passage:" + span for span in spans]
        assert len(cited) == len(set(cited)) == 141
    finally:
        store.close()


def test_search_continues_past_empty_batches_inside_authorized_scope(tmp_path):
    store = Store(tmp_path / "search.db")
    try:
        alpha = store.create_project("alpha")
        beta = store.create_project("beta")
        for i in range(31):
            store.save_page(alpha.id, f"page-{i:03}", "Match" if i == 30 else "Other", "", [])
        store.save_page(beta.id, "hidden", "Match foreign", "", [])
        results = list(batches(store.narrow([alpha.id]), "alpha", limit=3, search="match"))
        assert len(results) > 1
        assert results[0]["counts"]["page"] == 0 and results[0]["next"]
        assert [
            node["slug"] for batch in results for node in batch["nodes"] if node["kind"] == "page"
        ] == ["page-030"]
        assert "foreign" not in json.dumps(results)
    finally:
        store.close()


def test_each_focus_and_continuation_rechecks_current_reach_and_person_access(tmp_path):
    from cairn.graph import graph_neighbourhood

    store = Store(tmp_path / "revocation.db")
    try:
        issuer = "https://id.example.test"
        store.sign_in("sso", "owner", "owner@example.test", "Owner", issuer=issuer, join="default")
        signed = store.sign_in(
            "sso", "member", "member@example.test", "Member", issuer=issuer, join="default"
        )
        alpha = store.create_project("alpha", everyone="none")
        beta = store.create_project("beta")
        group = store.create_set("group", [alpha.id])
        store.set_project_access(alpha.id, people={signed.user.id: "read"})
        person = store.for_account(signed.account_id, signed.user.id).narrow([group.id])
        for i in range(3):
            store.save_page(alpha.id, f"p{i}", "A", "", [Claim(id=f"c{i}", text="A", span_ids=())])
        first = graph_neighbourhood(person, "alpha", limit=1)
        page = graph_neighbourhood(person, "alpha", focus="page:p0")
        claim = next(node["focus"] for node in page["nodes"] if node["kind"] == "claim")
        for focus in ("project", "notes", "page:p0", claim):
            with pytest.raises(KeyError):
                graph_neighbourhood(person, "beta", focus=focus)
        store.update_set(group.id, [beta.id])
        with pytest.raises(KeyError):
            graph_neighbourhood(person, "alpha", after=first["next"], limit=1)
        store.update_set(group.id, [alpha.id])
        store.set_project_access(alpha.id, people={})
        for focus in ("project", "notes", "page:p0", claim):
            with pytest.raises(KeyError):
                graph_neighbourhood(person, "alpha", focus=focus)
    finally:
        store.close()


def test_claim_evidence_never_returns_foreign_citations_even_when_explicitly_stored(tmp_path):
    from cairn.graph import graph_neighbourhood

    store = Store(tmp_path / "foreign.db")
    try:
        alpha, beta = store.create_project("alpha"), store.create_project("beta")
        foreign, _ = store.add_note(
            beta.id, "Private foreign passage.", source_ref="foreign-source"
        )
        sid = store.note_spans(foreign.id)[0].id
        store.save_page(alpha.id, "a", "A", "", [Claim(id="c", text="A", span_ids=(sid,))])
        page = graph_neighbourhood(store, "alpha", focus="page:a")
        focus = next(node["focus"] for node in page["nodes"] if node["kind"] == "claim")
        evidence = list(batches(store.narrow([alpha.id]), "alpha", focus=focus))
        assert all(batch["counts"]["passage"] == batch["counts"]["note"] == 0 for batch in evidence)
        assert sid not in json.dumps(evidence) and "foreign" not in json.dumps(evidence)
    finally:
        store.close()


def test_continuation_is_scoped_and_changed_page_cannot_return_reused_index_rows(tmp_path):
    from cairn.graph import GraphStale, graph_neighbourhood

    store = Store(tmp_path / "stale.db")
    try:
        alpha, beta = store.create_project("alpha"), store.create_project("beta")
        claims = [Claim(id=f"c{i}", text=f"Claim {i}", span_ids=()) for i in range(3)]
        store.save_page(alpha.id, "a", "A", "", claims)
        first = graph_neighbourhood(store, "alpha", focus="page:a", limit=1)
        old_claim = next(node["focus"] for node in first["nodes"] if node["kind"] == "claim")
        for change in (
            {"ref": beta.id},
            {"focus": "notes"},
            {"search": "different"},
            {"after": first["next"][:-3] + "bad"},
        ):
            query = {"ref": alpha.id, "focus": "page:a", "after": first["next"], **change}
            with pytest.raises(ValueError):
                graph_neighbourhood(store, **query)
        store.save_page(
            alpha.id, "a", "Changed", "", [Claim(id="replacement", text="New", span_ids=())]
        )
        with pytest.raises(GraphStale):
            graph_neighbourhood(store, "alpha", focus="page:a", after=first["next"])
        with pytest.raises(GraphStale):
            graph_neighbourhood(store, "alpha", focus=old_claim)
    finally:
        store.close()


def test_missing_index_does_not_report_false_completion(tmp_path):
    from cairn.graph import GraphUnavailable, graph_neighbourhood

    store = Store(tmp_path / "index.db")
    try:
        project = store.create_project("alpha")
        store.save_page(project.id, "a", "A", "", [Claim(id="c", text="A", span_ids=())])
        with store._write() as con:
            con.execute("DELETE FROM search_claims WHERE project_id = ?", (project.id,))
        assert graph_neighbourhood(store, "alpha")["counts"]["page"] == 1
        with pytest.raises(GraphUnavailable):
            graph_neighbourhood(store, "alpha", focus="page:a")
    finally:
        store.close()


def test_single_oversized_citation_scans_in_bounded_batches_then_reaches_valid_evidence(tmp_path):
    from cairn.graph import graph_neighbourhood

    store = Store(tmp_path / "large-citation.db")
    try:
        project = store.create_project("alpha")
        note, _ = store.add_note(project.id, "Eligible evidence after oversized item.")
        sid = store.note_spans(note.id)[0].id
        store.save_page(project.id, "a", "A", "", [Claim(id="c", text="A", span_ids=())])
        with store._write() as con:
            con.execute(
                f"UPDATE {store._index} SET span_ids = ?", (json.dumps(['\\"' * 100000, sid]),)
            )
        page = graph_neighbourhood(store, "alpha", focus="page:a")
        focus = next(node["focus"] for node in page["nodes"] if node["kind"] == "claim")
        results = list(batches(store, "alpha", focus=focus))
        assert results[0]["state"] == "scanning"
        assert len(results) > 4
        assert [
            edge["to"] for batch in results for edge in batch["edges"] if edge["kind"] == "cites"
        ] == ["passage:" + sid]
        assert any(batch["truncated"] for batch in results)
    finally:
        store.close()


def test_note_batch_work_stays_bounded_as_project_grows(tmp_path):
    from cairn.graph import graph_neighbourhood

    store = Store(tmp_path / "note-work.db")
    try:
        project = store.create_project("alpha")
        for i in range(30):
            store.add_note(project.id, f"Synthetic note {i}.")

        def work():
            operations = [0]

            def tick():
                operations[0] += 1
                return 0

            store._con.set_progress_handler(tick, 1)
            try:
                graph_neighbourhood(store, "alpha", focus="notes", limit=1)
            finally:
                store._con.set_progress_handler(None, 0)
            return operations[0]

        small = work()
        for i in range(30, 300):
            store.add_note(project.id, f"Synthetic note {i}.")
        assert work() <= small + 50
    finally:
        store.close()


@pytest.mark.parametrize("part", ["version", "row"])
def test_oversized_numeric_claim_focus_is_a_bad_request(tmp_path, part):
    from cairn.graph import graph_neighbourhood

    store = Store(tmp_path / "numeric-focus.db")
    try:
        project = store.create_project("alpha")
        store.save_page(project.id, "a", "A", "", [])
        huge = str(2**80)
        focus = f"claim:{huge}:1:identity:a" if part == "version" else f"claim:1:{huge}:identity:a"
        with pytest.raises(ValueError, match="invalid graph focus"):
            graph_neighbourhood(store, "alpha", focus=focus)
    finally:
        store.close()


def test_missing_fts_content_is_unavailable_for_claim_and_page_expansion(tmp_path):
    from cairn.graph import GraphUnavailable, graph_neighbourhood

    store = Store(tmp_path / "missing-content.db")
    try:
        project = store.create_project("alpha")
        store.save_page(project.id, "a", "A", "", [Claim(id="c", text="A", span_ids=())])
        page = graph_neighbourhood(store, "alpha", focus="page:a")
        claim = next(node["focus"] for node in page["nodes"] if node["kind"] == "claim")
        with store._write() as con:
            con.execute(f"DELETE FROM {store._index} WHERE project_id = ?", (project.id,))
        for focus in ("page:a", claim):
            with pytest.raises(GraphUnavailable):
                graph_neighbourhood(store, "alpha", focus=focus)
    finally:
        store.close()


def test_note_detail_reaches_all_unicode_text_in_bounded_chunks(tmp_path):
    from cairn.graph import graph_note_detail

    store = Store(tmp_path / "note-detail.db")
    try:
        project = store.create_project("alpha")
        text = "a" * 16383 + "🧠" + "é漢" * 10000 + "\nend\n"
        note, _ = store.add_note(
            project.id,
            text,
            title="Synthetic Unicode",
            source_ref="https://example.test/source",
            via="test",
        )
        chunks, after = [], None
        for _ in range(20):
            detail = graph_note_detail(store, "alpha", note.id, after=after)
            assert detail["project_id"] == project.id and detail["project"] == "alpha"
            assert detail["note_id"] == note.id and detail["title"] == "Synthetic Unicode"
            assert detail["source_ref"] == "https://example.test/source" and detail["via"] == "test"
            assert len(detail["text"].encode()) <= 16384
            assert "\ufffd" not in detail["text"]
            chunks.append(detail["text"])
            after = detail["next"]
            if after is None:
                assert detail["state"] == "complete"
                break
            assert detail["state"] == "more"
        assert len(chunks) > 3 and "".join(chunks) == text
    finally:
        store.close()


def test_note_detail_rechecks_scope_and_binds_continuation_to_exact_note(tmp_path):
    from cairn.graph import graph_note_detail

    store = Store(tmp_path / "note-detail-scope.db")
    try:
        alpha, beta = store.create_project("alpha"), store.create_project("beta")
        note, _ = store.add_note(alpha.id, "a" * 20000, source_ref="https://example.test/alpha")
        other, _ = store.add_note(alpha.id, "Other eligible note.")
        foreign, _ = store.add_note(beta.id, "Foreign text.", source_ref="foreign")
        group = store.create_set("group", [alpha.id])
        view = store.narrow([group.id])
        first = graph_note_detail(view, "alpha", note.id)
        with pytest.raises(KeyError):
            graph_note_detail(view, "alpha", foreign.id)
        with pytest.raises(ValueError):
            graph_note_detail(view, "alpha", other.id, after=first["next"])
        with pytest.raises(ValueError):
            graph_note_detail(view, "alpha", note.id, after=first["next"][:-4] + "oops")
        store.update_set(group.id, [beta.id])
        with pytest.raises(KeyError):
            graph_note_detail(view, "alpha", note.id, after=first["next"])
    finally:
        store.close()


def test_forgotten_page_cannot_replay_old_focus_or_cursor_after_slug_recreation(tmp_path):
    from datetime import UTC, datetime

    from cairn.graph import GraphStale, graph_neighbourhood

    store = Store(tmp_path / "page-incarnation.db", clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    try:
        project = store.create_project("alpha")
        old_note, _ = store.add_note(project.id, "Old source evidence.")
        old_span = store.note_spans(old_note.id)[0].id
        claims = [Claim(id=f"c{i}", text=f"Old claim {i}", span_ids=(old_span,)) for i in range(2)]
        store.save_page(project.id, "a", "A", "", claims)
        first = graph_neighbourhood(store, "alpha", focus="page:a", limit=1)
        old_focus = next(node["focus"] for node in first["nodes"] if node["kind"] == "claim")
        store.forget_note(old_note.id)
        new_note, _ = store.add_note(project.id, "New source evidence.")
        new_span = store.note_spans(new_note.id)[0].id
        store.save_page(
            project.id,
            "a",
            "A",
            "",
            [Claim(id=f"c{i}", text=f"New claim {i}", span_ids=(new_span,)) for i in range(2)],
        )
        with pytest.raises(GraphStale):
            graph_neighbourhood(store, "alpha", focus=old_focus)
        with pytest.raises(GraphStale):
            graph_neighbourhood(store, "alpha", focus="page:a", after=first["next"])
    finally:
        store.close()


def test_stale_fts_mapping_cannot_expose_another_project_claim(tmp_path):
    from cairn.graph import GraphUnavailable, graph_neighbourhood

    store = Store(tmp_path / "stale-content.db")
    try:
        alpha, beta = store.create_project("alpha"), store.create_project("beta")
        for project, text in ((alpha, "Eligible"), (beta, "Foreign private claim")):
            store.save_page(project.id, "a", "A", "", [Claim(id="c", text=text, span_ids=())])
        with store._write() as con:
            foreign = con.execute(
                "SELECT row FROM search_claims WHERE project_id = ?", (beta.id,)
            ).fetchone()[0]
            con.execute("DELETE FROM search_claims WHERE project_id = ?", (beta.id,))
            con.execute(
                "UPDATE search_claims SET row = ? WHERE project_id = ?", (foreign, alpha.id)
            )
        with pytest.raises(GraphUnavailable):
            graph_neighbourhood(store.narrow([alpha.id]), "alpha", focus="page:a")
    finally:
        store.close()
