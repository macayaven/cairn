"""A bounded, deterministic projection of a single project's explicit evidence.

This module is not a web endpoint: cairn.web.graph_routes serves it. Admission is the
application's, as for every read: a key's or an agent's stored reach names at most REACH_MAX
(cairn.store.reach) projects or sets, and admission reads no more of it than that can take.
This module rechecks the current person's and connection's reach inside the same snapshot as
the data, and a reach over REACH_MAX entries, or a set too large to read within its bounds,
fails closed here. Pages are visited by slug, claims and citations in stored order; only the
latest page version is read. A limit stops traversal, never adds dangling edges, and is
reported as truncation. No totals for omitted material are computed.
"""

import json
import sqlite3
from typing import Any

from cairn.store import Store
from cairn.store.reach import REACH_MAX

PAGE_LIMIT = 20
CLAIM_LIMIT = 100
PASSAGE_LIMIT = 100
NOTE_LIMIT = 100
EDGE_LIMIT = 400
CITATION_LIMIT = 100
SCOPE_DEPTH_LIMIT = 8
SCOPE_BYTES_LIMIT = 65_536
SCOPE_TOTAL_BYTES_LIMIT = 131_072
PAGE_BYTES_LIMIT = 262_144
TEXT_BYTES_LIMIT = 512


class GraphUnavailable(Exception):
    """The representation exceeds a safe work bound; it must not be partially authorized."""


def _blob(con: sqlite3.Connection, table: str, column: str, rowid: int, limit: int) -> bytes:
    with con.blobopen(table, column, rowid, readonly=True) as blob:
        if len(blob) > limit:
            raise GraphUnavailable
        return blob.read()


def _resolved_scopes(store: Store, con: sqlite3.Connection) -> list[set[str]]:
    """Bound and resolve every narrowing before looking up a target.

    This ensures an oversized set has the same result for unknown and hidden projects.
    """
    if len(store._reaches) > SCOPE_DEPTH_LIMIT:
        raise GraphUnavailable
    total = 0
    resolved = []
    for reach in store._reaches:
        if len(reach) > REACH_MAX:
            raise GraphUnavailable
        visible = set()
        for ref in reach:
            if not isinstance(ref, str) or len(ref) > 128:
                raise GraphUnavailable
            visible.add(ref)
            row = con.execute(
                "SELECT seq FROM project_sets WHERE id = ? AND account_id = ?",
                (ref, store._account_id),
            ).fetchone()
            if row:
                data = _blob(con, "project_sets", "project_ids", row[0], SCOPE_BYTES_LIMIT)
                total += len(data)
                if total > SCOPE_TOTAL_BYTES_LIMIT:
                    raise GraphUnavailable
                members = json.loads(data)
                if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
                    raise GraphUnavailable
                visible.update(members)
        resolved.append(visible)
    return resolved


def project_graph(store: Store, ref: str, limit: int = PAGE_LIMIT) -> dict[str, Any]:
    """Read up to `limit` page heads and their cited evidence, or raise KeyError.

    GraphUnavailable means a safe bound prevented authorization; callers should use a
    generic response. Oversized page claim data instead stops traversal with truncation.
    `limit` is a page bound, from 1 to 20. Every edge endpoint is in the returned nodes.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= PAGE_LIMIT:
        raise ValueError("limit must be a whole number from 1 to 20")
    with store._read() as con:
        scopes = _resolved_scopes(store, con)
        project = con.execute(
            "SELECT id, name FROM projects WHERE account_id = ? AND id = ?",
            (store._account_id, ref),
        ).fetchone()
        if project is None:
            project = con.execute(
                "SELECT id, name FROM projects WHERE account_id = ? AND name = ?",
                (store._account_id, ref),
            ).fetchone()
        if project is None or store._level(con, project[0]) == "none":
            raise KeyError(ref)
        if any(project[0] not in scope for scope in scopes):
            raise KeyError(ref)
        return _project_graph(con, project[0], project[1], limit)


def _project_graph(
    con: sqlite3.Connection, project_id: str, name: str, limit: int
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"id": "project:" + project_id, "kind": "project", "label": name}
    ]
    edges: list[dict[str, str]] = []
    seen: set[str] = {nodes[0]["id"]}
    counts = {"claim": 0, "passage": 0, "note": 0}
    truncated = False
    citations_checked = 0

    def node(item: dict[str, Any]) -> None:
        if item["id"] not in seen:
            seen.add(item["id"])
            nodes.append(item)
            if item["kind"] in counts:
                counts[item["kind"]] += 1

    def edge(kind: str, source: str, target: str) -> None:
        edges.append({"kind": kind, "from": source, "to": target})

    heads = con.execute(
        "SELECT slug, version FROM page_heads WHERE project_id = ? ORDER BY slug LIMIT ?",
        (project_id, limit + 1),
    ).fetchall()
    if len(heads) > limit:
        truncated = True
    for slug, version in heads[:limit]:
        if len(edges) >= EDGE_LIMIT:
            truncated = True
            break
        page_id = "page:" + slug
        row = con.execute(
            "SELECT rowid FROM page_versions WHERE project_id = ? AND slug = ? AND version = ?",
            (project_id, slug, version),
        ).fetchone()
        if row is None:
            truncated = True
            continue
        try:
            title = _blob(con, "page_versions", "title", row[0], TEXT_BYTES_LIMIT).decode("utf-8")
        except GraphUnavailable:
            title = "Page title omitted"
            truncated = True
        node({"id": page_id, "kind": "page", "label": title, "slug": slug})
        edge("has page", "project:" + project_id, page_id)
        try:
            claims = json.loads(_blob(con, "page_versions", "claims", row[0], PAGE_BYTES_LIMIT))
        except GraphUnavailable:
            truncated = True
            continue
        for ordinal, claim in enumerate(claims):
            if counts["claim"] >= CLAIM_LIMIT or len(edges) + 1 > EDGE_LIMIT:
                truncated = True
                break
            claim_id = f"claim:{ordinal}:{slug}"
            node(
                {
                    "id": claim_id,
                    "kind": "claim",
                    "label": claim["text"],
                    "slug": slug,
                    "claim_id": claim["id"],
                }
            )
            edge("has claim", page_id, claim_id)
            for span_id in dict.fromkeys(claim["span_ids"]):
                if citations_checked >= CITATION_LIMIT:
                    truncated = True
                    break
                citations_checked += 1
                if (
                    len(edges) + 3 > EDGE_LIMIT
                    or counts["passage"] >= PASSAGE_LIMIT
                    or counts["note"] >= NOTE_LIMIT
                ):
                    truncated = True
                    break
                passage = con.execute(
                    "SELECT s.id, s.note_id, s.rowid, n.title, n.source_ref, n.via"
                    " FROM spans s JOIN notes n ON n.id = s.note_id"
                    " WHERE s.id = ? AND n.project_id = ?",
                    (span_id, project_id),
                ).fetchone()
                if not passage:
                    continue
                sid, nid, rowid, note_title, source_ref, via = passage
                try:
                    text = _blob(con, "spans", "text", rowid, TEXT_BYTES_LIMIT).decode("utf-8")
                except GraphUnavailable:
                    text = "Passage text omitted"
                    truncated = True
                if "note:" + nid not in seen:
                    node(
                        {
                            "id": "note:" + nid,
                            "kind": "note",
                            "label": note_title,
                            "source_ref": source_ref,
                            "via": via,
                        }
                    )
                    edge("has note", "project:" + project_id, "note:" + nid)
                node({"id": "passage:" + sid, "kind": "passage", "label": text})
                relationship = {
                    "kind": "has passage",
                    "from": "note:" + nid,
                    "to": "passage:" + sid,
                }
                if relationship not in edges:
                    edges.append(relationship)
                edge("cites", claim_id, "passage:" + sid)
    notes = con.execute(
        "SELECT id, title, source_ref, via FROM notes WHERE project_id = ?"
        " ORDER BY text_sha256, seq LIMIT ?",
        (project_id, NOTE_LIMIT + 1),
    ).fetchall()
    if len(notes) > NOTE_LIMIT:
        truncated = True
    for nid, title, source_ref, via in notes[:NOTE_LIMIT]:
        if "note:" + nid in seen:
            continue
        if counts["note"] >= NOTE_LIMIT or len(edges) >= EDGE_LIMIT:
            truncated = True
            break
        node(
            {
                "id": "note:" + nid,
                "kind": "note",
                "label": title,
                "source_ref": source_ref,
                "via": via,
            }
        )
        edge("has note", "project:" + project_id, "note:" + nid)
    shown_counts = {
        kind: sum(n["kind"] == kind for n in nodes)
        for kind in ("project", "page", "claim", "passage", "note")
    }
    shown_counts["edges"] = len(edges)
    return {"nodes": nodes, "edges": edges, "counts": shown_counts, "truncated": truncated}
