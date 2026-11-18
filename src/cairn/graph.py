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

import base64
import codecs
import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
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


# The explorer (G2, `serve --graph-explorer`): what follows serves only the explorer's pages.
# project_graph above, which /api/graph and the agent's resource read, is not called by it
# and does not call it. A page's claims are read from the search index's rows, which every
# save of the page replaces in its own transaction, never from the page's stored JSON.
NEIGHBOURHOOD_BYTES = 65_536
CURSOR_BYTES = 8192
NOTE_DETAIL_BYTES = 16_384
IDENTITY_FIELD_BYTES = 4096
# What signs a continuation, made once per process: a continuation is good for this process
# only, and a restart answers it as unreadable (the page that says so links the start).
_CURSOR_KEY = secrets.token_bytes(32)


def _authorized_project(store: Store, con: sqlite3.Connection, ref: str) -> tuple[str, str]:
    """The project `ref` names (id, else name) as (id, name), read in the caller's snapshot
    with the same checks project_graph makes: the person's level and every reach of the
    view. KeyError when it does not exist or is out of reach, the same for both."""
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
    if (
        project is None
        or store._level(con, project[0]) == "none"
        or any(project[0] not in scope for scope in scopes)
    ):
        raise KeyError(ref)
    return project


class GraphStale(ValueError):
    """The page changed during exploration; restart at its current page neighbourhood."""


class PageMissing(KeyError):
    """A focus names a page the project, which is in reach, does not have."""


@contextmanager
def _projection_blob(con: sqlite3.Connection, table: str, column: str, row: int):
    """A missing/corrupt projection is unavailable, never a raw SQLite response."""
    try:
        with con.blobopen(table, column, row, readonly=True) as blob:
            yield blob
    except sqlite3.Error:
        raise GraphUnavailable from None


def _field_identity(con, table, column, row):
    """Bounded identity material, including both ends of an oversized field.

    Ordinary claim fields are covered in full. This is a navigation-staleness guard,
    not an integrity checksum: equal-size edits confined to an unsampled middle of a
    field larger than IDENTITY_FIELD_BYTES can preserve this identity under a reused
    timestamp/row. Durable, collision-free page incarnations require a writer generation.
    """
    with _projection_blob(con, table, column, row) as blob:
        size = len(blob)
        if size <= IDENTITY_FIELD_BYTES:
            data = blob.read()
        else:
            data = blob.read(IDENTITY_FIELD_BYTES // 2)
            blob.seek(size - IDENTITY_FIELD_BYTES // 2)
            data += blob.read(IDENTITY_FIELD_BYTES // 2)
    return size.to_bytes(8, "big") + data


def _claim_identity(con, store, project_id, slug, row):
    table = store._index + "_content"
    try:
        scoped = con.execute(
            f"SELECT 1 FROM {table} WHERE id = ? AND c0 = ? AND c1 = ?",
            (row, project_id, slug),
        ).fetchone()
    except sqlite3.Error:
        raise GraphUnavailable from None
    if scoped is None:
        raise GraphUnavailable
    digest = hmac.new(_CURSOR_KEY, digestmod=hashlib.sha256)
    digest.update(json.dumps([project_id, slug, row]).encode())
    for column in ("c2", "c3", "c5"):
        digest.update(_field_identity(con, table, column, row))
    return digest.hexdigest()


def _page_identity(con, store, project_id, slug, page_row, version, count):
    digest = hmac.new(_CURSOR_KEY, digestmod=hashlib.sha256)
    digest.update(json.dumps([project_id, slug, version, count]).encode())
    digest.update(_field_identity(con, "page_versions", "created_at", page_row))
    for direction in ("ASC", "DESC"):
        row = con.execute(
            "SELECT row FROM search_claims WHERE account_id = ? AND project_id = ?"
            f" AND slug = ? ORDER BY row {direction} LIMIT 1",
            (store._account_id, project_id, slug),
        ).fetchone()
        if row is not None:
            digest.update(_claim_identity(con, store, project_id, slug, row[0]).encode())
    return digest.hexdigest()


def _preview(con: sqlite3.Connection, table: str, column: str, row: int) -> tuple[str, bool]:
    with _projection_blob(con, table, column, row) as blob:
        omitted = len(blob) > TEXT_BYTES_LIMIT
        return blob.read(TEXT_BYTES_LIMIT).decode("utf-8", errors="replace"), omitted


def _note_metadata(con, nid, rowid):
    title, short = _preview(con, "notes", "title", rowid)
    source_null, via_null = con.execute(
        "SELECT source_ref IS NULL, via IS NULL FROM notes WHERE seq = ?", (rowid,)
    ).fetchone()
    source, source_short = (
        (None, False) if source_null else _preview(con, "notes", "source_ref", rowid)
    )
    via, via_short = (None, False) if via_null else _preview(con, "notes", "via", rowid)
    return {
        "id": "note:" + nid,
        "kind": "note",
        "label": title,
        "source_ref": None if source_short else source,
        "via": via,
        "omitted": short or source_short or via_short,
        "note_id": nid,
    }


def graph_neighbourhood(
    store: Store,
    ref: str,
    *,
    focus: str = "project",
    after: str | None = None,
    limit: int = PAGE_LIMIT,
    search: str = "",
) -> dict[str, Any]:
    """Explore current authorized evidence, with independently bounded continuations.

    `project` visits page heads by slug; `notes` visits all notes by content hash and sequence;
    `page:<slug>` visits current indexed claims by row; a returned claim's `focus`
    visits its cited passages in stored order. Each response contains its anchor nodes,
    so every edge has both endpoints even when merging batches is unnecessary.

    `limit` (1..20) bounds candidates checked, including nonmatches and missing citations.
    Search matches case-insensitive slug or bounded label previews in this neighbourhood;
    follow `next` even when a batch has no matches. Counts describe only this response.
    No scope-wide totals are computed. Labels longer than 512 bytes have `omitted=True`;
    their source and expansion remain reachable. Citation scanning reads at most 64 KiB.

    `next` is opaque, tied to account/project/focus/search and valid for this process.
    Invalid tokens raise ValueError; GraphStale means restart the page after a change.
    Missing index data raises GraphUnavailable rather than claiming completion.
    Every request rechecks current authorization in the same snapshot as its content.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= PAGE_LIMIT:
        raise ValueError("limit must be a whole number from 1 to 20")
    if not isinstance(focus, str) or len(focus) > 512:
        raise ValueError("invalid graph focus")
    if not isinstance(search, str) or len(search.encode()) > TEXT_BYTES_LIMIT:
        raise ValueError("invalid graph search")
    with store._read() as con:
        project = _authorized_project(store, con, ref)
        context = [store._account_id, project[0], focus, search]
        cursor = _decode_cursor(after, context) if after is not None else {}
        return _neighbourhood(con, store, project, focus, cursor, limit, search, context)


def _neighbourhood(con, store, project, focus, cursor, limit, search, context):
    project_id, name = project
    nodes = [{"id": "project:" + project_id, "kind": "project", "label": name}]
    edges = []
    ids = {nodes[0]["id"]}
    omitted = False

    def add(item, kind=None, parent=None):
        nonlocal omitted
        if item["id"] not in ids:
            ids.add(item["id"])
            nodes.append(item)
            omitted |= item.get("omitted", False)
        if kind:
            relationship = {"kind": kind, "from": parent, "to": item["id"]}
            if relationship not in edges:
                edges.append(relationship)

    def matches(item):
        return (
            not search
            or search.casefold() in (item["label"] + " " + item.get("slug", "")).casefold()
        )

    def note(row):
        return _note_metadata(con, *row)

    def page(slug):
        row = con.execute(
            "SELECT p.rowid, p.version, h.claims FROM page_heads h JOIN page_versions p"
            " ON p.project_id = h.project_id AND p.slug = h.slug AND p.version = h.version"
            " WHERE h.project_id = ? AND h.slug = ?",
            (project_id, slug),
        ).fetchone()
        if row is None:
            raise PageMissing(slug)
        title, short = _preview(con, "page_versions", "title", row[0])
        return (
            {
                "id": "page:" + slug,
                "kind": "page",
                "label": title,
                "slug": slug,
                "version": row[1],
                "focus": "page:" + slug,
                "omitted": short,
            },
            row[2],
        )

    def claim(row, slug, version):
        table = store._index + "_content"
        cid, id_short = _preview(con, table, "c2", row)
        text, short = _preview(con, table, "c5", row)
        identity = _claim_identity(con, store, project_id, slug, row)
        key = f"claim:{version}:{row}:{identity}:{slug}"
        return {
            "id": key,
            "kind": "claim",
            "label": text,
            "slug": slug,
            "claim_id": None if id_short else cid,
            "version": version,
            "focus": key,
            "omitted": short or id_short,
        }

    next_cursor = None
    scanning = False
    if focus == "project":
        rows = con.execute(
            "SELECT slug FROM page_heads WHERE project_id = ? AND slug > ? ORDER BY slug LIMIT ?",
            (project_id, cursor.get("last", ""), limit + 1),
        ).fetchall()
        for (slug,) in rows[:limit]:
            item, _ = page(slug)
            if matches(item):
                add(item, "has page", nodes[0]["id"])
        if len(rows) > limit:
            next_cursor = {"last": rows[limit - 1][0]}
    elif focus == "notes":
        rows = con.execute(
            "SELECT id, seq, text_sha256 FROM notes WHERE project_id = ?"
            " AND (text_sha256, seq) > (?, ?) ORDER BY text_sha256, seq LIMIT ?",
            (project_id, *cursor.get("last", ["", 0]), limit + 1),
        ).fetchall()
        for nid, rowid, _ in rows[:limit]:
            item = note((nid, rowid))
            if matches(item):
                add(item, "has note", nodes[0]["id"])
        if len(rows) > limit:
            next_cursor = {"last": [rows[limit - 1][2], rows[limit - 1][1]]}
    else:
        wanted_row = None
        if focus.startswith("page:"):
            slug = focus[5:]
        elif focus.startswith("claim:"):
            try:
                _, wanted_version, wanted_row, wanted_identity, slug = focus.split(":", 4)
                wanted_version, wanted_row = int(wanted_version), int(wanted_row)
                if not (1 <= wanted_version <= 2**63 - 1 and 1 <= wanted_row <= 2**63 - 1):
                    raise ValueError
            except ValueError:
                raise ValueError("invalid graph focus") from None
        else:
            raise ValueError("invalid graph focus")
        anchor, expected_claims = page(slug)
        version = anchor["version"]
        page_row = con.execute(
            "SELECT rowid FROM page_versions WHERE project_id = ? AND slug = ? AND version = ?",
            (project_id, slug, version),
        ).fetchone()[0]
        page_identity = _page_identity(
            con, store, project_id, slug, page_row, version, expected_claims
        )
        if cursor.get("identity", page_identity) != page_identity:
            raise GraphStale("The page changed; restart its graph.")
        if cursor.get("version", version) != version or (
            wanted_row is not None and wanted_version != version
        ):
            raise GraphStale("The page changed; restart its graph.")
        add(anchor, "has page", nodes[0]["id"])
        # Page saves replace these index rows atomically. Validate continuation's
        # anchor row before seeking, and validate the observed count at completion;
        # never scan every claim merely to count it on each expansion.
        if cursor.get("last") is not None:
            previous = con.execute(
                "SELECT 1 FROM search_claims WHERE account_id = ? AND project_id = ?"
                " AND slug = ? AND row = ?",
                (store._account_id, project_id, slug, cursor["last"]),
            ).fetchone()
            if previous is None:
                raise GraphStale("The claim index changed; restart its page graph.")
        if wanted_row is None:
            rows = con.execute(
                "SELECT row FROM search_claims WHERE account_id = ? AND project_id = ?"
                " AND slug = ? AND row > ? ORDER BY row LIMIT ?",
                (store._account_id, project_id, slug, cursor.get("last", 0), limit + 1),
            ).fetchall()
            for (row,) in rows[:limit]:
                item = claim(row, slug, version)
                if matches(item):
                    add(item, "has claim", anchor["id"])
            visited = cursor.get("visited", 0) + min(len(rows), limit)
            if len(rows) > limit:
                next_cursor = {
                    "last": rows[limit - 1][0],
                    "version": version,
                    "visited": visited,
                    "identity": page_identity,
                }
            elif visited != expected_claims:
                raise GraphUnavailable
        else:
            row = con.execute(
                "SELECT row FROM search_claims WHERE account_id = ? AND project_id = ?"
                " AND slug = ? AND row = ?",
                (store._account_id, project_id, slug, wanted_row),
            ).fetchone()
            if row is None:
                raise GraphStale("The claim index changed; restart its page graph.")
            identity = _claim_identity(con, store, project_id, slug, wanted_row)
            if wanted_identity != identity:
                raise GraphStale("The claim changed; restart its page graph.")
            item = claim(wanted_row, slug, version)
            add(item, "has claim", anchor["id"])
            citations, continuation, skipped = _citation_batch(
                con, store._index + "_content", wanted_row, cursor.get("scan"), limit
            )
            omitted |= skipped
            for sid in citations:
                passage = con.execute(
                    "SELECT s.id, s.note_id, s.rowid, n.seq FROM spans s"
                    " JOIN notes n ON n.id = s.note_id WHERE s.id = ? AND n.project_id = ?",
                    (sid, project_id),
                ).fetchone()
                if passage is None:
                    continue
                sid, nid, rowid, note_rowid = passage
                text, short = _preview(con, "spans", "text", rowid)
                passage_node = {
                    "id": "passage:" + sid,
                    "kind": "passage",
                    "label": text,
                    "passage_id": sid,
                    "note_id": nid,
                    "omitted": short,
                    "slug": slug,
                    "claim_id": item["claim_id"],
                }
                if not matches(passage_node):
                    continue
                add(note((nid, note_rowid)), "has note", nodes[0]["id"])
                add(passage_node, "has passage", "note:" + nid)
                add(passage_node, "cites", item["id"])
            if continuation is not None:
                next_cursor = {"version": version, "scan": continuation, "identity": page_identity}
                scanning = not citations
    counts = {
        kind: sum(n["kind"] == kind for n in nodes)
        for kind in ("project", "page", "claim", "passage", "note")
    }
    counts["edges"] = len(edges)
    return {
        "nodes": nodes,
        "edges": edges,
        "counts": counts,
        "truncated": omitted or next_cursor is not None,
        "focus": focus,
        "next": _encode_cursor(context, next_cursor) if next_cursor is not None else None,
        "state": "scanning" if scanning else "more" if next_cursor else "complete",
    }


def _citation_batch(con, table, row, state, limit):
    """Resume a JSON string array with bounded bytes, memory, and citation lookups.

    Large individual identifiers are skipped across bounded scanning continuations. Their
    contents cannot name a stored span (generated span ids fit the bounded token buffer).
    The signed cursor preserves the string escape state even at a chunk boundary.
    """
    state = dict(state or {"pos": 0, "token": None, "escape": False, "large": False})
    result = []
    checked = 0
    skipped = False
    with _projection_blob(con, table, "c3", row) as blob:
        blob.seek(state["pos"])
        data = blob.read(NEIGHBOURHOOD_BYTES)
        for byte in data:
            state["pos"] += 1
            token = state["token"]
            if token is None:
                if byte == 34:
                    state["token"] = '"'
                    state["large"] = False
                elif byte == 93:
                    return result, None, skipped
                elif byte not in b"[ ,\t\r\n":
                    raise GraphUnavailable
            else:
                if not state["large"]:
                    state["token"] += chr(byte)
                    if len(state["token"]) > TEXT_BYTES_LIMIT * 2:
                        state["large"] = True
                        state["token"] = ""
                if state["escape"]:
                    state["escape"] = False
                elif byte == 92:
                    state["escape"] = True
                elif byte == 34:
                    checked += 1
                    if state["large"]:
                        skipped = True
                    else:
                        try:
                            result.append(json.loads(state["token"].encode("latin1")))
                        except (ValueError, UnicodeError):
                            raise GraphUnavailable from None
                    state["token"] = None
                    if checked >= limit:
                        return result, state, skipped
        if state["pos"] >= len(blob):
            raise GraphUnavailable
    return result, state, skipped


def _encode_cursor(context, position):
    payload = json.dumps([context, position], separators=(",", ":")).encode()
    signature = hmac.digest(_CURSOR_KEY, payload, hashlib.sha256)
    return base64.urlsafe_b64encode(signature + payload).decode().rstrip("=")


def _decode_cursor(token, context):
    try:
        if not isinstance(token, str) or len(token) > CURSOR_BYTES:
            raise ValueError
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        signature, payload = raw[:32], raw[32:]
        if not hmac.compare_digest(signature, hmac.digest(_CURSOR_KEY, payload, hashlib.sha256)):
            raise ValueError
        saved_context, position = json.loads(payload)
        if saved_context != context or not isinstance(position, dict):
            raise ValueError
        return position
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("Invalid graph continuation; restart this neighbourhood.") from None


def graph_note_detail(
    store: Store, ref: str, note_id: str, *, after: str | None = None
) -> dict[str, Any]:
    """One exact authorized note's metadata and a Unicode-safe text chunk.

    Text reads at most NOTE_DETAIL_BYTES per request, never a full note or its spans.
    Every continuation rechecks membership, project/reach and note identity in the same
    snapshot. Notes are immutable; the stored sequence additionally binds continuation
    to this row. Foreign and absent notes both raise KeyError. No source URL is fetched.
    """
    if not isinstance(note_id, str) or not note_id or len(note_id) > 128:
        raise ValueError("invalid note reference")
    with store._read() as con:
        project_id, project = _authorized_project(store, con, ref)
        row = con.execute(
            "SELECT seq FROM notes WHERE id = ? AND project_id = ?", (note_id, project_id)
        ).fetchone()
        if row is None:
            raise KeyError(note_id)
        context = [store._account_id, project_id, "note:" + note_id, ""]
        cursor = _decode_cursor(after, context) if after is not None else {}
        if cursor.get("row", row[0]) != row[0]:
            raise GraphStale("The note changed; restart its source detail.")
        metadata = _note_metadata(con, note_id, row[0])
        position = cursor.get("pos", 0)
        with _projection_blob(con, "notes", "text", row[0]) as blob:
            blob.seek(position)
            data = blob.read(NOTE_DETAIL_BYTES)
            decoder = codecs.getincrementaldecoder("utf-8")()
            try:
                text = decoder.decode(data, final=position + len(data) == len(blob))
            except UnicodeError:
                raise GraphUnavailable from None
            pending, _ = decoder.getstate()
            position += len(data) - len(pending)
            more = position < len(blob)
        return {
            "project_id": project_id,
            "project": project,
            "note_id": note_id,
            "title": metadata["label"],
            "source_ref": metadata["source_ref"],
            "via": metadata["via"],
            "text": text,
            "next": _encode_cursor(context, {"row": row[0], "pos": position}) if more else None,
            "state": "more" if more else "complete",
            "omitted": metadata["omitted"],
        }
