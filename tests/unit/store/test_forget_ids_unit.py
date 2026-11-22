"""An importable fold can sit beside users of both ids the old eraser tried."""

import hashlib
import io
import json
from contextlib import closing

import pytest

from cairn.compiler import render_body
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import claim_id
from cairn.store import Claim, Store
from support.compiler.assembled_fence import exported
from support.store.reviewer4_forget_roundtrip import assert_round_trip


@pytest.mark.parametrize(
    ("place", "reserve_fallback"),
    [(0, True), (2, True), (0, False)],
    ids=["fold-first", "fold-last", "only-first-choice-taken"],
)
def test_every_supported_claim_survives_with_its_own_id(place, reserve_fallback):
    with closing(Store(":memory:")) as source, closing(Store(":memory:")) as store:
        project = source.create_project("alpha")
        passages = []
        for text in ("Leaf   \nfalls.", "Leaf\nfalls.", "The ledger is blue.", "The ink is green."):
            note, _ = source.add_note(project.id, text, topic="Ledger")
            (span,) = source.note_spans(note.id)
            passages.append(span)
        gone, kept, ledger, ink = passages
        fold = Claim(id=claim_id(gone.id), text=gone.text, span_ids=(gone.id, kept.id))
        # Reserve the old fallback in the input. The replacement id is the writer's choice.
        old_key = json.dumps([place, [kept.id]]).encode()
        fallback = "cl_" + hashlib.sha256(old_key).hexdigest()[:16]
        others = [
            Claim(id=claim_id(kept.id), text=ledger.text, span_ids=(ledger.id,)),
            Claim(
                id=fallback if reserve_fallback else claim_id(ink.id),
                text=ink.text,
                span_ids=(ink.id,),
            ),
        ]
        claims = others.copy()
        claims.insert(place, fold)
        assert len({c.id for c in claims}) == len(claims)
        body = render_body("Ledger", claims, {s.id: s for s in passages})
        source.save_page(project.id, "ledger", "Ledger", body, claims)
        assert store.import_export(io.BytesIO(exported(source))).pages == 1
        project = store.get_project("alpha")
        assert store.get_page(project.id, "ledger").claims == tuple(claims)

        result = Memory(store, lint_gate).forget("alpha", gone.source_id)
        assert result["rebuilt"] == ["ledger"]
        assert_round_trip(store, project)
        survivors = store.get_page(project.id, "ledger").claims
        expected = {(s.text, (s.id,)) for s in (kept, ledger, ink)}
        assert {(c.text, c.span_ids) for c in survivors} == expected
        assert len({c.id for c in survivors}) == len(survivors) == len(expected)
