"""Unit tests for what an imported page may cite: a passage is cited from one page only, and a
page version's claims hold at most CLAIM_TEXT_FACTOR times the text of the spans they cite.
cairn writes one page per note and a claim is its span's text, so an archive past either is
one cairn did not write, built to take far more disk than its notes allow."""

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from cairn.arrange import arrange_page
from cairn.compiler import compile_note, render_body
from cairn.gate import lint_gate
from cairn.llm import FakeModel, Reply
from cairn.memory import Memory, OverQuota
from cairn.store import BYTES_PER_PASSAGE, Limits, Store, portable

NOTE = "# Heron runbook\n\nHeron restarts the queue after 3 failures.\n"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def exported(view: Store) -> bytes:
    out = io.BytesIO()
    view.export(out)
    return out.getvalue()


def rebuilt(archive: bytes, change) -> bytes:
    """The archive with each entry passed through `change(name, data)`, which returns the
    entries to write in its place."""
    source, out = zipfile.ZipFile(io.BytesIO(archive)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for name in source.namelist():
            for written, data in change(name, source.read(name)).items():
                target.writestr(written, data)
    return out.getvalue()


def refused(tmp_path, archive: bytes, match: str) -> str:
    db = tmp_path / "refused.db"
    target = Store(db)
    try:
        before = dump(db)
        with pytest.raises(ValueError, match=match) as error:
            target.import_export(io.BytesIO(archive))
        assert dump(db) == before
        return str(error.value)
    finally:
        target.close()


@pytest.fixture
def source(tmp_path):
    view = Store(tmp_path / "source.db")
    project = view.create_project("ops")
    note, _ = view.add_note(project.id, NOTE)
    assert compile_note(view, note).status == "saved"
    yield view
    view.close()


def test_the_factor_is_two():
    assert portable.CLAIM_TEXT_FACTOR == 2


def test_forty_pages_citing_one_notes_passage_are_refused(source, tmp_path):
    """The reviewer's archive: one note, and forty pages whose claims cite its one span."""
    pages = 40

    def copies(name, data):
        if name == "export.json":
            manifest = json.loads(data)
            manifest["projects"][0]["pages"] = pages
            return {name: json.dumps(manifest).encode()}
        if name == "projects/ops/pages.jsonl":
            (line,) = [json.loads(row) for row in data.splitlines()]
            lines = [{**line, "slug": f"heron-runbook-{n}"} for n in range(2, pages + 1)]
            return {name: b"".join(json.dumps(x).encode() + b"\n" for x in [line, *lines])}
        if name == "projects/ops/pages/heron-runbook.md":
            return {f"projects/ops/pages/heron-runbook{s}.md": data for s in ["", *(
                f"-{n}" for n in range(2, pages + 1))]}  # fmt: skip
        return {name: data}

    said = refused(tmp_path, rebuilt(exported(source), copies), "cites a passage")
    assert "heron-runbook" in said
    assert "Heron restarts" not in said and "span_" not in said  # nothing of the note


def test_a_thousand_claims_repeating_one_passage_are_refused(source, tmp_path):
    project = source.get_project("ops")
    (claim,) = source.get_page(project.id, "heron-runbook").claims
    spans = source.get_spans(list(claim.span_ids))

    def repeated(name, data):
        if name not in ("projects/ops/pages.jsonl", "projects/ops/pages/heron-runbook.md"):
            return {name: data}
        (line,) = [json.loads(row) for row in source_lines()]
        claims = [{**claim.shown(), "id": f"cl_{n:016x}"} for n in range(1000)]
        body = render_body(
            line["title"],
            [type(claim)(id=c["id"], text=c["text"], span_ids=c["passage_ids"]) for c in claims],
            spans,
        )
        if name.endswith(".md"):
            return {name: body.encode()}
        return {name: json.dumps({**line, "claims": claims, "body": body}).encode() + b"\n"}

    archive = exported(source)

    def source_lines():
        return zipfile.ZipFile(io.BytesIO(archive)).read("projects/ops/pages.jsonl").splitlines()

    said = refused(tmp_path, rebuilt(archive, repeated), "twice the text")
    assert "heron-runbook" in said and "Heron restarts" not in said


# A claim cairn writes: an id shaped as cairn makes one, each span cited once, and a version
# with at least one claim (cairn removes a page with none).


def with_line(source: Store, change) -> bytes:
    """The export of `source` with `change(line)` applied to its one page version, and the
    page's body written again as cairn renders it."""
    project = source.get_project("ops")
    (claim,) = source.get_page(project.id, "heron-runbook").claims
    spans = source.get_spans(list(claim.span_ids))
    archive = exported(source)
    (line,) = [
        json.loads(row)
        for row in zipfile.ZipFile(io.BytesIO(archive))
        .read("projects/ops/pages.jsonl")
        .splitlines()
    ]
    change(line)
    claims = [
        type(claim)(id=c["id"], text=c["text"], span_ids=c["passage_ids"]) for c in line["claims"]
    ]
    line["body"] = render_body(line["title"], claims, spans)

    def apply(name, data):
        if name == "projects/ops/pages.jsonl":
            return {name: json.dumps(line).encode() + b"\n"}
        if name == "projects/ops/pages/heron-runbook.md":
            return {name: line["body"].encode()}
        return {name: data}

    return rebuilt(archive, apply)


def test_a_claim_id_is_shaped_as_cairn_makes_one():
    assert portable.CLAIM_ID.fullmatch("cl_0123456789abcdef")
    for made in ("c1", "cl_0123456789ABCDEF", "cl_0123456789abcde", "cl_" + "a" * 17, "x" * 10**6):
        assert not portable.CLAIM_ID.fullmatch(made)


@pytest.mark.parametrize(
    "made",
    ["c1", "cl_" + "0" * 15, "cl_" + "0" * 17, "cl_0123456789ABCDEF", "cl_" + "x" * 1_000_000],
    ids=["short", "15 hex", "17 hex", "upper case", "a megabyte"],
)
def test_a_claim_id_cairn_does_not_make_is_refused(source, tmp_path, made):
    def rename(line):
        line["claims"][0]["id"] = made

    said = refused(tmp_path, with_line(source, rename), "an id cairn does not make")
    assert "heron-runbook" in said and made[:12] not in said


def test_a_claim_citing_a_span_twice_is_refused(source, tmp_path):
    def twice(line):
        line["claims"][0]["passage_ids"] = line["claims"][0]["passage_ids"] * 2

    said = refused(tmp_path, with_line(source, twice), "cites a passage twice")
    assert "heron-runbook" in said and "sp_" not in said


def test_a_page_version_without_claims_is_refused(source, tmp_path):
    def empty(line):
        line["claims"] = []

    said = refused(tmp_path, with_line(source, empty), "no claims")
    assert "heron-runbook" in said


def test_a_claim_cairn_writes_imports_with_its_id(source, tmp_path):
    target = Store(tmp_path / "target.db")
    try:
        target.import_export(io.BytesIO(exported(source)))
        project = target.get_project("ops")
        (claim,) = target.get_page(project.id, "heron-runbook").claims
        assert portable.CLAIM_ID.fullmatch(claim.id)
    finally:
        target.close()


def test_claims_padded_with_whitespace_past_the_factor_are_refused(source, tmp_path):
    def padded(name, data):
        if name != "projects/ops/pages.jsonl":
            return {name: data}
        line = json.loads(data)
        for claim in line["claims"]:
            claim["text"] += "\n" * (2 * len(NOTE))  # the same words, blank lines aside
        return {name: json.dumps(line).encode() + b"\n"}

    refused(tmp_path, rebuilt(exported(source), padded), "twice the text")


def test_claims_at_twice_their_spans_import(source, tmp_path):
    project = source.get_project("ops")
    (claim,) = source.get_page(project.id, "heron-runbook").claims
    (span,) = source.get_spans(list(claim.span_ids)).values()
    room = 2 * len(span.text.encode()) - len(claim.text.encode())

    def padded(name, data):
        if name != "projects/ops/pages.jsonl":
            return {name: data}
        line = json.loads(data)
        line["claims"][0]["text"] += "\n" * room  # exactly the bound
        return {name: json.dumps(line).encode() + b"\n"}

    target = Store(tmp_path / "target.db")
    try:
        target.import_export(io.BytesIO(rebuilt(exported(source), padded)))
    finally:
        target.close()


# A version's citations (its claims' span lists, added up) are at most twice its claims and
# the distinct spans it cites, added up.

ITEMS = 10


@pytest.fixture
def listed(tmp_path):
    """One note of ten list items on one page, each saying the same: ten claims, each citing
    its own item, and each the whole of every other item too, so a claim may cite them all
    (an import takes a claim only when it is every passage it cites, review blocker 2)."""
    view = Store(tmp_path / "listed.db")
    project = view.create_project("ops")
    items = "- Heron restarts a queue.\n" * ITEMS
    note, _ = view.add_note(project.id, "# Heron runbook\n\n" + items)
    assert compile_note(view, note).status == "saved"
    assert len(view.get_page(project.id, "heron-runbook").claims) == ITEMS
    yield view
    view.close()


def cited_more(view: Store, extra: list[int]) -> bytes:
    """The export of `view` with claim n citing, after its own item, the next `extra[n]`
    items too; each claim still says what each item it cites says."""
    project = view.get_project("ops")
    page = view.get_page(project.id, "heron-runbook")
    spans = view.get_spans([i for claim in page.claims for i in claim.span_ids])
    ids = [claim.span_ids[0] for claim in page.claims]
    claims = [
        claim.model_copy(
            update={"span_ids": (ids[n], *(ids[(n + k) % ITEMS] for k in range(1, more + 1)))}
        )
        for n, (claim, more) in enumerate(zip(page.claims, extra, strict=True))
    ]
    body = render_body(page.title, claims, spans)

    def apply(name, data):
        if name == "projects/ops/pages.jsonl":
            (line,) = [json.loads(row) for row in data.splitlines()]
            line.update(claims=[c.shown() for c in claims], body=body)
            return {name: json.dumps(line).encode() + b"\n"}
        if name == "projects/ops/pages/heron-runbook.md":
            return {name: body.encode()}
        return {name: data}

    return rebuilt(exported(view), apply)


def test_the_citation_factor_is_two():
    assert portable.CITATION_FACTOR == 2


def test_citations_at_twice_the_claims_and_passages_import(listed, tmp_path):
    archive = cited_more(listed, [3] * ITEMS)  # 40 citations: 2 * (10 claims + 10 spans)
    target = Store(tmp_path / "target.db")
    try:
        target.import_export(io.BytesIO(archive))
        project = target.get_project("ops")
        claims = target.get_page(project.id, "heron-runbook").claims
        assert sum(len(claim.span_ids) for claim in claims) == 40
    finally:
        target.close()


def test_citations_past_twice_the_claims_and_passages_are_refused(listed, tmp_path):
    archive = cited_more(listed, [4] + [3] * (ITEMS - 1))  # 41 citations of 40
    said = refused(tmp_path, archive, "cites its passages")
    assert "heron-runbook" in said and "sp_" not in said and "Heron restarts" not in said


def test_every_claim_citing_every_passage_is_refused(listed, tmp_path):
    """The reviewer's shape at its smallest: 10 claims citing 10 spans each, 100 of 40."""
    refused(tmp_path, cited_more(listed, [ITEMS - 1] * ITEMS), "cites its passages")


# An honest export of a workspace filled to its caps imports


def ledger(n: int) -> str:
    """A note on one page: the same opening line each time, and an item of its own."""
    return f"# Ledger\n\nThe ledger is kept daily.\n\n- entry {n} is booked\n"


def topic(n: int) -> str:
    return f"# Topic {n}\n\nFact {n} is recorded here.\n"


def versions(view: Store) -> dict[tuple[str, str], list[int]]:
    return {
        (project.name, page.slug): view.page_versions(project.id, page.slug)
        for project in view.list_projects()
        for page in view.list_pages(project.id)
    }


def test_an_honest_export_of_a_workspace_at_its_caps_imports(tmp_path):
    caps = Limits(storage_bytes=120 * BYTES_PER_PASSAGE)
    store = Store(tmp_path / "full.db")
    try:
        account = store.create_account("Full").id
        view = store.for_account(account)
        ops = view.create_project("ops")
        view.create_project("team")
        memory = Memory(view.limited(caps), lint_gate)
        with pytest.raises(OverQuota) as full:
            for n in range(1000):
                memory.note("ops" if n % 3 else "team", ledger(n) if n < 9 else topic(n))
        assert full.value.fields()["limit"] == "passages" and view.passages() == 120
        page = view.get_page(ops.id, "ledger")
        assert len(view.page_versions(ops.id, "ledger")) >= 2  # versions of one page
        same = [c.id for c in page.claims if c.text == "The ledger is kept daily."]
        order = [c.id for c in page.claims if c.id not in same[1:]]
        reply = json.dumps({"order": order, "same": [same]})
        model = FakeModel([Reply(text=reply, prompt_tokens=1, completion_tokens=1)], name="f")
        assert arrange_page(view, ops.id, "ledger", model).status == "arranged"
        folded = view.get_page(ops.id, "ledger")
        assert any(len(c.span_ids) == len(same) for c in folded.claims)  # one claim, all spans
        kept = versions(view)
        archive = exported(view)
    finally:
        store.close()

    target = Store(tmp_path / "target.db")
    try:
        copy = target.for_account(target.create_account("Copy").id)
        copy.limited(caps).import_export(io.BytesIO(archive))
        copied = copy.get_project("ops")
        assert copy.get_page(copied.id, "ledger").claims == folded.claims
        assert copy.passages() == 120
        copied_versions = versions(copy)  # the history budget keeps what fits, oldest out
        assert {k: v[-1] for k, v in copied_versions.items()} == {k: v[-1] for k, v in kept.items()}
        assert len(copied_versions[("ops", "ledger")]) >= 2
    finally:
        target.close()
