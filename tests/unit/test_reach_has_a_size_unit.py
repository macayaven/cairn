"""A stored reach has a size: a key or an agent's connection names at most REACH_MAX projects or
sets where it is made, and admission never decodes a stored reach longer than that could be."""

import json
import re
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cairn.store.reach import REACH_MAX, REACH_TEXT_MAX

BASE = "http://127.0.0.1:8765"
REDIRECT = "http://127.0.0.1:33418/callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
GRANTS = ["authorization_code", "refresh_token"]
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
SAID = "reaches at most 64 projects or sets; make a set of them, or leave it out to reach every"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def env(tmp_path):
    """A workspace of REACH_MAX + 1 projects, p00 to p64."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    ids = [store.create_project(f"p{n:02d}").id for n in range(REACH_MAX + 1)]
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, store=store, db=db, ids=ids)
    store.close()


def post(env, path: str, data: dict | None = None):
    return env.client.post(path, data=data or {}, headers=SAME_ORIGIN, follow_redirects=False)


def sign_in(env) -> None:
    key = env.store.create_key("owner")
    assert post(env, "/login", {"key": key}).status_code == 303


def pending_consent(env) -> str:
    """An agent registered and sent to the consent page by a signed-in owner: its request."""
    c = env.client
    client_id = c.post(
        "/register",
        json={"client_name": "bot", "redirect_uris": [REDIRECT], "grant_types": GRANTS},
    ).json()["client_id"]
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": "s",
    }
    sign_in(env)
    r = c.get("/authorize?" + urlencode(params), follow_redirects=False)
    page = c.get(r.headers["location"])
    return re.search(r'name="request" value="([^"]+)"', page.text).group(1)


def tools_list(env, key: str):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    return env.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)


# The constant


def test_the_graph_reads_the_one_constant():
    from cairn import graph

    assert REACH_MAX == 64
    assert not hasattr(graph, "SCOPE_ITEMS_LIMIT")
    assert graph.REACH_MAX is REACH_MAX


def test_the_byte_bound_is_what_64_of_the_longest_ids_take_as_stored():
    longest = ["set_" + "f" * 12] * REACH_MAX  # sets' ids, one longer than projects'
    assert len(json.dumps(longest)) == REACH_TEXT_MAX == 1280


# Where a reach is made


def test_the_store_refuses_a_key_of_65_and_makes_one_of_64(env):
    before = dump(env.db)
    with pytest.raises(ValueError, match="a key " + SAID):
        env.store.create_key("wide", env.ids)
    assert dump(env.db) == before
    key = env.store.create_key("widest", env.ids[:REACH_MAX])
    assert tools_list(env, key).status_code == 200


def test_the_store_refuses_an_agents_code_of_65_and_makes_one_of_64(env):
    client = env.store.register_client("bot", [REDIRECT])
    before = dump(env.db)
    with pytest.raises(ValueError, match="an agent " + SAID):
        env.store.create_code(client.id, REDIRECT, CHALLENGE, projects=env.ids)
    assert dump(env.db) == before
    assert env.store.create_code(client.id, REDIRECT, CHALLENGE, projects=env.ids[:REACH_MAX])


def test_the_key_form_refuses_65_saying_the_number_and_makes_64(env):
    sign_in(env)
    before = dump(env.db)
    r = post(env, "/keys", {"label": "wide", "scope": "selected", "projects": env.ids})
    assert r.status_code == 400 and SAID in r.text
    assert dump(env.db) == before
    r = post(env, "/keys", {"label": "wide", "scope": "selected", "projects": env.ids[:64]})
    assert r.status_code == 200 and "wide" in r.text


def test_the_consent_refuses_65_saying_the_number_and_leaves_the_request_open(env):
    request = pending_consent(env)
    ticked = {"request": request, "decision": "approve", "scope": "selected"}
    r = post(env, "/oauth/consent", {**ticked, "projects": env.ids})
    assert r.status_code == 400 and "location" not in r.headers
    assert SAID in r.text
    assert env.store.list_grants() == []
    r = post(env, "/oauth/consent", {**ticked, "projects": env.ids[:REACH_MAX]})
    assert r.status_code == 303
    assert "code" in parse_qs(urlsplit(r.headers["location"]).query)


def test_key_create_on_the_command_line_refuses_65_and_makes_no_key(tmp_path, capsys):
    """The store refuses before writing, and key-create removes its file and says the
    store's sentence as one line of stderr, with exit 1."""
    from cairn.web import cli

    db = tmp_path / "cli.db"
    store = Store(db)
    for n in range(REACH_MAX + 1):
        store.create_project(f"p{n:02d}")
    store.close()
    names = [arg for n in range(REACH_MAX + 1) for arg in ("--project", f"p{n:02d}")]
    before = dump(db)
    wide = tmp_path / "wide.env"
    args = ["key-create", "--db", str(db), "--label", "wide", "--out", str(wide)]
    assert cli.main([*args, *names]) == 1
    said = capsys.readouterr()
    assert re.search("a key " + SAID, said.err) and said.err.count("\n") == 1 and not said.out
    assert not wide.exists() and dump(db) == before
    ok = tmp_path / "ok.env"
    args = ["key-create", "--db", str(db), "--label", "ok", "--out", str(ok)]
    assert cli.main([*args, *names[:-2]]) == 0 and ok.exists()


def test_a_set_is_one_entry_whatever_it_holds(env):
    every = env.store.create_set("every", env.ids)
    key = env.store.create_key("by-set", [every.id])
    assert tools_list(env, key).status_code == 200


# Where a reach is loaded


def oversize(env, table: str, where: str, value: str) -> None:
    """A reach from before the cap, longer than REACH_TEXT_MAX, written into the row."""
    wide = json.dumps(env.ids * 2)
    assert len(wide) > REACH_TEXT_MAX
    con = sqlite3.connect(env.db)
    try:
        con.execute(f"UPDATE {table} SET projects = ? WHERE {where} = ?", (wide, value))
        con.commit()
    finally:
        con.close()


class Decoded:
    """json.loads in a store module, recording the length of each text it is given."""

    def __init__(self, monkeypatch, module) -> None:
        self.lengths: list[int] = []
        real = json.loads

        def loads(text, *args, **kwargs):
            self.lengths.append(len(text))
            return real(text, *args, **kwargs)

        monkeypatch.setattr(module.json, "loads", loads)


def test_an_oversized_stored_key_reach_is_never_decoded_and_the_key_is_refused(env, monkeypatch):
    key = env.store.create_key("old", env.ids[:2])
    oversize(env, "api_keys", "label", "old")
    from cairn.store import reach

    decoded = Decoded(monkeypatch, reach)
    assert env.store.identify_key(key) is None
    r = env.client.get("/api/projects", headers={"x-api-key": key})
    assert r.status_code == 401
    assert all(length <= REACH_TEXT_MAX for length in decoded.lengths), decoded.lengths


def test_an_oversized_stored_grant_reach_is_never_decoded_and_the_token_is_refused(
    env, monkeypatch
):
    import base64
    import hashlib
    import secrets

    client = env.store.register_client("bot", [REDIRECT])
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    code = env.store.create_code(client.id, REDIRECT, challenge, projects=env.ids[:2])
    token = env.store.redeem_code(code, client.id).access_token
    assert env.store.identify_token(token) is not None
    grant_id = env.store.list_grants()[0].id
    oversize(env, "oauth_grants", "id", grant_id)
    from cairn.store import reach

    decoded = Decoded(monkeypatch, reach)
    assert env.store.identify_token(token) is None
    assert all(length <= REACH_TEXT_MAX for length in decoded.lengths), decoded.lengths


def test_a_row_from_before_the_cap_within_it_works_as_before(env):
    key = env.store.create_key("old", env.ids[:3])
    principal, _ = env.store.identify_key(key)
    assert principal.projects == tuple(env.ids[:3])


def test_a_row_of_more_than_64_ids_within_the_byte_bound_is_refused_too(env):
    """65 project ids (15 characters each) fit in REACH_TEXT_MAX: their count still refuses."""
    key = env.store.create_key("old", env.ids[:2])
    narrow = json.dumps(env.ids)
    assert len(narrow) <= REACH_TEXT_MAX
    con = sqlite3.connect(env.db)
    try:
        con.execute("UPDATE api_keys SET projects = ? WHERE label = 'old'", (narrow,))
        con.commit()
    finally:
        con.close()
    assert env.store.identify_key(key) is None


def test_the_graph_still_refuses_a_set_reach_over_its_own_bound(tmp_path):
    from cairn.graph import GraphUnavailable, project_graph

    store = Store(tmp_path / "graph.db")
    try:
        alpha = store.create_project("alpha")
        view = store.narrow([alpha.id, *(f"x{n}" for n in range(REACH_MAX))])
        with pytest.raises(GraphUnavailable):
            project_graph(view, "alpha")
        assert project_graph(store.narrow([alpha.id]), "alpha")["nodes"][0]["label"] == "alpha"
    finally:
        store.close()


class ObservedConnection:
    """Delegate to real SQLite, recording only admission costs, never SQL or credentials."""

    def __init__(self, con, before_blob=None):
        self.con = con
        self.before_blob = before_blob
        self.opens = 0
        self.reads = []
        self.closed = 0
        self.verbs = []
        self.value_length_requested = False

        def traced(statement):
            self.verbs.append(statement.split()[0].upper())
            self.value_length_requested |= any(
                operation in statement.upper() for operation in ("LENGTH(", "CAST(")
            )

        con.set_trace_callback(traced)

    def __getattr__(self, name):
        return getattr(self.con, name)

    def blobopen(self, table, column, row, *, readonly=False, name="main"):
        assert table in ("api_keys", "oauth_grants")
        assert column == "projects" and readonly and name == "main"
        assert self.con.in_transaction, "reach read escaped the metadata snapshot"
        self.opens += 1
        if self.before_blob:
            self.before_blob()
        blob = self.con.blobopen(table, column, row, readonly=readonly, name=name)
        owner = self

        class ObservedBlob:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                blob.close()
                owner.closed += 1

            def __len__(self):
                return len(blob)

            def read(self, size=-1):
                owner.reads.append(size)
                assert 0 <= size <= REACH_TEXT_MAX, "unbounded stored reach read"
                return blob.read(size)

        return ObservedBlob()


@pytest.fixture(params=["key", "token"])
def admission(tmp_path, request):
    """Both real admission paths, with an unrelated grant to distinguish joined rowids."""
    db = tmp_path / "admission.db"
    store = Store(db)
    project = store.create_project("alpha")
    other = store.create_project("beta")
    user = store.sign_in(
        "sso",
        "subject-ada",
        "ada@example.test",
        "Ada",
        issuer="https://id.example.test",
        join="default",
    )
    person = store.for_account(user.account_id, user.user.id)
    if request.param == "key":
        secret = person.create_key("scoped", [project.id], access="read")
        table = "api_keys"
        identity = store._con.execute("SELECT id FROM api_keys WHERE label = 'scoped'").fetchone()[
            0
        ]

        def identify():
            return store.identify_key(secret)
    else:
        client = store.register_client("bot", [REDIRECT])
        # The token rowid differs from its grant's rowid (access + refresh per grant).
        for projects in ([other.id], [project.id]):
            code = person.create_code(
                client.id, REDIRECT, CHALLENGE, projects=projects, access="read", audience=BASE
            )
            secret = store.redeem_code(code, client.id).access_token
        table = "oauth_grants"
        identity = store.list_grants()[-1].id

        def identify():
            return store.identify_token(secret, audience=BASE)

    try:
        yield SimpleNamespace(
            store=store,
            db=db,
            table=table,
            identity=identity,
            identify=identify,
            project=project.id,
            other=other.id,
            user=user.user.id,
            kind=request.param,
            account=user.account_id,
        )
    finally:
        store._con.set_trace_callback(None)
        store.close()


def write_reach(admission, value):
    with sqlite3.connect(admission.db) as con:
        con.execute(
            f"UPDATE {admission.table} SET projects = ? WHERE id = ?",
            (value, admission.identity),
        )


@pytest.mark.parametrize("unrestricted", [False, True])
def test_admission_records_lookup_cost_and_reads_only_scoped_bounded_blobs(admission, unrestricted):
    """Eager text/CAST reads regress allocation; NULL must remain every project with no blob."""
    if unrestricted:
        write_reach(admission, None)
    observed = ObservedConnection(admission.store._con)
    admission.store._con = observed
    result = admission.identify()
    assert result is not None
    principal, identity = result
    assert principal.projects == (None if unrestricted else (admission.project,))
    assert (principal.account_id, principal.kind, principal.label) == (
        admission.account,
        admission.kind,
        "scoped" if admission.kind == "key" else "bot",
    )
    assert (principal.user_id, principal.access, identity) == (
        admission.user,
        "read",
        admission.identity,
    )
    assert observed.verbs == (
        ["BEGIN", "SELECT", "COMMIT"] if unrestricted else ["BEGIN", "SELECT", "SELECT", "COMMIT"]
    )
    assert not observed.value_length_requested
    assert observed.opens == observed.closed == (0 if unrestricted else 1)
    assert len(observed.reads) == (0 if unrestricted else 1)


@pytest.mark.parametrize(
    ("value", "admitted", "read"),
    [
        ("[]", True, True),
        (json.dumps(["br_" + "f" * 12] * 65), False, True),
        (json.dumps(["set_" + "f" * 12] * 64), True, True),
        (json.dumps(["set_" + "f" * 12] * 64) + " ", False, False),
        ('["' + "é" * 639 + '"]', False, False),  # characters fit; UTF-8 bytes do not
    ],
    ids=["empty", "65-short-ids", "exact-byte-bound", "one-byte-over", "utf8-over"],
)
def test_stored_reach_byte_and_entry_boundaries(admission, value, admitted, read):
    """A byte or entry overflow must refuse, never become the unrestricted NULL reach."""
    write_reach(admission, value)
    observed = ObservedConnection(admission.store._con)
    admission.store._con = observed
    result = admission.identify()
    assert (result is not None) is admitted
    if admitted:
        assert result[0].projects == tuple(json.loads(value))
    assert not observed.value_length_requested
    assert observed.opens == observed.closed == 1
    assert len(observed.reads) == int(read)


@pytest.mark.parametrize("value", ["", "null"])
def test_malformed_stored_reach_never_becomes_every_project(admission, value):
    """Keep existing decode errors for invalid legacy scope; never return NULL scope."""
    write_reach(admission, value)
    with pytest.raises((json.JSONDecodeError, TypeError)):
        admission.identify()


def test_reach_and_identity_share_a_snapshot_against_a_second_connection(admission):
    """A split lookup outside _read would combine old identity with a new grant/key scope."""

    def change_after_metadata():
        with sqlite3.connect(admission.db) as other:
            other.execute(
                f"UPDATE {admission.table} SET projects = ?, access = 'write' WHERE id = ?",
                (json.dumps([admission.other]), admission.identity),
            )

    observed = ObservedConnection(admission.store._con, before_blob=change_after_metadata)
    admission.store._con = observed
    result = admission.identify()
    assert result is not None
    assert (result[0].projects, result[0].access) == ((admission.project,), "read")
    assert observed.opens == observed.closed == 1
    observed.before_blob = None
    changed = admission.identify()
    assert (changed[0].projects, changed[0].access) == ((admission.other,), "write")


def test_unknown_or_revoked_credentials_open_no_reach_blob(admission):
    if admission.kind == "key":
        admission.store.revoke_key("scoped")
    else:
        admission.store.revoke_grant(admission.identity)
    observed = ObservedConnection(admission.store._con)
    admission.store._con = observed
    assert admission.identify() is None
    assert observed.opens == 0


@pytest.mark.parametrize(
    "refusal", ["expired", "wrong-audience", "wrong-audience-malformed", "refresh"]
)
def test_non_admitted_tokens_open_no_grant_blob(tmp_path, refusal):
    store = Store(tmp_path / "tokens.db")
    try:
        client = store.register_client("bot", [REDIRECT])
        code = store.create_code(client.id, REDIRECT, CHALLENGE, audience=BASE)
        pair = store.redeem_code(code, client.id)
        if refusal == "expired":
            with store._write() as con:
                con.execute("UPDATE oauth_tokens SET expires_at = '2000-01-01T00:00:00+00:00'")
        if refusal == "wrong-audience-malformed":
            with store._write() as con:
                con.execute("UPDATE oauth_grants SET projects = 'null'")
        observed = ObservedConnection(store._con)
        store._con = observed
        result = store.identify_token(
            pair.refresh_token if refusal == "refresh" else pair.access_token,
            audience="https://other.example.test" if refusal.startswith("wrong-audience") else BASE,
        )
        assert result is None
        if refusal != "wrong-audience-malformed":
            assert observed.opens == 0
    finally:
        store._con.set_trace_callback(None)
        store.close()


@pytest.mark.parametrize("kind", ["key", "token"])
def test_huge_legacy_reach_is_refused_with_a_fixed_sqlite_heap(tmp_path, kind):
    """The old CAST length guard exhausts SQLite heap before it can refuse a legacy scope.

    SQLite's heap cap is process-global: only this disposable child sets it. The child
    exercises actual Store admission; no timing threshold or million-project setup.
    """
    script = textwrap.dedent(
        """\
        import json
        import sqlite3
        import sys

        kind, path, source = sys.argv[1:]
        # Pytest's import path changes do not propagate to child interpreters.
        sys.path.insert(0, source)
        from cairn.store import Store

        store = Store(path)
        signed = store.sign_in(
            "sso", "synthetic-person", "ada@example.test", "Ada",
            issuer="https://id.example.test", join="default",
        )
        person = store.for_account(signed.account_id, signed.user.id)
        if kind == "key":
            credential = person.create_key("synthetic")
            table = "api_keys"
            identify = lambda: store.identify_key(credential)
        else:
            client = store.register_client("synthetic", ["http://localhost/callback"])
            code = person.create_code(
                client.id, "http://localhost/callback", "synthetic",
                audience="https://cairn.example.test/mcp",
            )
            credential = store.redeem_code(code, client.id).access_token
            table = "oauth_grants"
            identify = lambda: store.identify_token(
                credential, audience="https://cairn.example.test/mcp"
            )
        # ~4 MiB of synthetic valid JSON; allocation occurs before setting the cap.
        value = json.dumps(["synthetic" * 1024] * 450)
        with sqlite3.connect(path) as writer:
            writer.execute(f"UPDATE {table} SET projects = ?", (value,))
        del value
        # Release the write connection's page cache before limiting SQLite allocations.
        writer.close()
        store._con.execute("PRAGMA shrink_memory")
        cap = store._con.execute("PRAGMA hard_heap_limit = 2097152").fetchone()[0]
        if cap != 2097152:
            print("heap_cap_unavailable")
            sys.exit(2)
        try:
            result = identify()
        except MemoryError:
            print("sqlite_heap_exhausted")
            sys.exit(3)
        except Exception:
            print("admission_error")
            sys.exit(4)
        finally:
            store.close()
        print("refused" if result is None else "admitted")
        """
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            kind,
            str(tmp_path / "heap.db"),
            str(Path(__file__).resolve().parents[2] / "src"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"isolated admission: {result.stdout.strip()}"
    assert result.stdout.strip() == "refused"
