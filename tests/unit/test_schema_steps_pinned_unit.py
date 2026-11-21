"""The schema's steps are history: their list, their numbers, the function each runs and the
SQL each executes are pinned here, as they were before schema.py became a package of one
module per step (cairn.store.schema). A move that changed one character a step executes, or
the order of the steps, fails here.

Each step runs on the database the steps before it left, starting from v0's tables with no
row in them, and every statement it executes through the connection (execute, executemany,
create_function) is recorded with its parameters and digested. The recording is the
connection's own methods, not SQLite's trace, so what FTS5 or a trigger runs inside SQLite,
which can differ between SQLite versions, is not part of it. The clock is pinned. Step 2 asks
FTS5 for secure-delete only on an SQLite that has it (_FTS5_SECURE_DELETE), so it is pinned
for both."""

import hashlib
import importlib
import json
import sqlite3
from typing import Any

from test_store_unit import V0_SCHEMA

import cairn.store.schema as schema

STAMP = "2026-01-02T03:04:05.000000+00:00"

# (number, the function the step runs), in the order the upgrade runs them.
STEPS = (
    (2, "_add_accounts"),
    (3, "_add_oauth"),
    (4, "_add_people"),
    (5, "_add_reach"),
    (6, "_add_audit"),
    (7, "_add_actor_ids"),
    (8, "_add_kinds"),
    (9, "_add_sources"),
    (10, "_add_access"),
    (11, "_add_metering"),
    (12, "_add_billing"),
    (13, "_add_subscription_times"),
    (14, "_add_reservations"),
    (15, "_add_forgotten"),
    (16, "_add_disk_counters"),
    (17, "_end_key_sessions"),
    (18, "_charge_headings"),
    (19, "_add_grant_audience"),
    (20, "_add_manage_level"),
    (21, "_add_source_switch"),
    (22, "_add_vouched_email"),
    (23, "_add_source_items"),
    (24, "_add_topic_keys"),
    (25, "_add_writers"),
    (26, "_add_page_heads"),
    (27, "_add_checkouts"),
    (28, "_add_credential_tokens"),
    (29, "_add_slack"),
    (30, "_add_last_use"),
    (31, "_drop_retired"),
    (32, "_hold_to_own_projects"),
)

# The SHA-256 of what each step executed (see recorded), at the head before the move.
DIGESTS: dict[int, str] = {
    2: "ae11a5c2e58d26d491302016cc38d86eb2242d3f94c1f22be114f5256b334619",
    3: "dc4e2d0d5a9b44694cdae0fb6ccec2139a1f1c85a578e6e42383f6a8f9654df4",
    4: "8676e7f6ea3fa08992125c9c44fd86b20691b57a334b664ee8be7412543955d6",
    5: "6e90c59d3be978f9d7cf8f0986887bd9c601b643bac80b6be117f6efb06d06cf",
    6: "bb78872dd85fe3ab5620a65eed9ee664645c231ec8fceaceb8b83c6608ccfe82",
    7: "274dc45f65c57df6dadd382b25914ecf7953edc943e48c764336c749b324b496",
    8: "9bd65d3a6e6cdcb4c1e63694f9e2a4355cddc5a9c38393b78cb563c6a4221ddb",
    9: "da92068ddf9aca881cc7bf5da092660389469499e0bc416d79c5dd8a65a69210",
    10: "bc27e6320e4cc7e0e4635b02cb27a337878ffce389456147fa462880dfdb969a",
    11: "c628580e9680cda239c891bd148a0d03e81607d707ff50e76fd94d9a5bb37fff",
    12: "0407ba91077c975acdb5eeda6034d8b0c17de41853774a05887810f9885113e4",
    13: "3187ac5459214f58117c75ecc2d1c38c6dddaae453e084956aab613cb936c6a3",
    14: "365e99a4f3ce0983003cb64001fd8c19b2f93278e789c95c7b2726ec7ddda46a",
    15: "aa42af75518926489c87afa92903e40345e754c89053dbe0aa7b82ac5dcb4221",
    16: "bc7934afd08dab49c92f9a46b8dcfbdb812e96b59ca77fda9ebae7cc215d70ed",
    17: "d0021ca9868e3ef38d7ec7c748b1fd4a705e5a8542ebedbc0e76ba6093e56aec",
    18: "89904968899b8b34af5ab3039ecb4e68935a6ea5fa37fd6ff189f3c2c7030d10",
    19: "0c09a00577e24ba5d381eca91194e723c57945bd7edf54f44bdfdb518062b792",
    20: "f681b686628e25722e88018a9273ad19edb6f2ee84f2671fbe67494ee3f6c7c6",
    21: "63be5596e61be0a19abde488d5c70aa77a067f2e800c6ffeef5e63eeefe38138",
    22: "7234ef7dbe80c4f53006e0183cc58f1d7639dcdb9a49dc9e5f90ef0ba151e85a",
    23: "15f77c790cde2dea68a6519fd1f8b8cf3ad81410108667ab4976af03f95d41ef",
    24: "9d5e9658be9194223fe7c043ab160c41468f1b46651bd9db1813a2ec0a375450",
    25: "b546dcab17625efba9ede549fc11873d785f0e941d0160da8c30f3be5f48246f",
    26: "a162908f3b6e7a6e263c8fc3e0b63fd832070bf5169f51b89d4c3d1dad9b55ee",
    27: "22dc85d8d7ca9c2c66a1efc6fc2c74e442e93b8f099e3aa77d656f2a79f3658b",
    28: "2bd9d6d0eea678a003c77e01920f8bce894ae7a8468ed3b358d01e9a655104fc",
    29: "1f3d95b4f97b9ded1e6add0f92ca28ff49db3cc5a674fb4f8a3fa63e28192c41",
    30: "26dfa79f46e54c6e00f60480200897847cd58c7c38864c07299f92f5b85b739e",
    31: "310d03faf566751f7a5ad906da1517d171f8a95eb05f47c7594e6108b69e52bb",
    # Step 32 on an empty history: the one search (cairn.store.erasure.hold_to_own_projects).
    # It finds a passage no note of the page's own project holds, one no longer held too, in
    # one pass, each project sought from the last (unreleased; it was 04f10fb46aee27cf9dea5f14
    # 36201230816e69c7ebe77281633e2b01077cb520, 02927bac0d49d453549b853a3710e21a69ce1a4aba0c01
    # 95e9500c42d33c8f03, then e1abaf07447dfdddc81a816d842b11c5c860268e6e19c7db17d0ceaa28febbc0).
    32: "31fb0f5c02be369fe625e58c25959d82422c2f5c2ff4a1558294d892ebbab677",
}
# Step 2 on an SQLite without FTS5's secure-delete.
STEP_2_WITHOUT_SECURE_DELETE = "f021460c7a3a60b2e002d9c9d782e2382ce8e5eb25d04dcde68481ebfc4e3dc3"
# What _create executes in an empty database: the current schema, the default account, the
# moment recording began and the floor (32 since step 32; at 31 it was
# 36ead68fa515b0134daa40fb3429e8aa6e99b0868bbe91ba7d90fb60cab2186a).
CREATED = "6543475df475e337c295ceb21e07e6e31c70f2a04495e5234b2f90a8aa0bfd4c"


class Recording(sqlite3.Connection):
    """A connection that records each statement run through it, with its parameters."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[list[Any]] = []

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        self.calls.append(["execute", sql, list(parameters)])
        return super().execute(sql, parameters)

    def executemany(self, sql: str, parameters: Any, /) -> sqlite3.Cursor:
        rows = [list(row) for row in parameters]
        self.calls.append(["executemany", sql, rows])
        return super().executemany(sql, rows)

    def create_function(self, name: str, narg: int, func: Any, **kwargs: Any) -> None:
        self.calls.append(["create_function", name, narg, sorted(kwargs.items())])
        super().create_function(name, narg, func, **kwargs)


def digest(calls: list[list[Any]]) -> str:
    text = json.dumps(calls, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def recorded() -> dict[int, str]:
    """Each step's digest, run one after the other from v0's empty tables."""
    con = sqlite3.connect(":memory:", factory=Recording, isolation_level=None)
    stamped = schema._STAMP.set(lambda: STAMP)
    found = {}
    try:
        con.executescript(V0_SCHEMA)
        for number, _ in STEPS:
            con.calls.clear()
            con.execute("BEGIN")
            schema._MIGRATIONS[number](con)
            con.execute("COMMIT")
            found[number] = digest(con.calls[1:-1])
    finally:
        schema._STAMP.reset(stamped)
        con.close()
    return found


def created() -> str:
    con = sqlite3.connect(":memory:", factory=Recording, isolation_level=None)
    stamped = schema._STAMP.set(lambda: STAMP)
    try:
        schema._create(con)
        return digest(con.calls)
    finally:
        schema._STAMP.reset(stamped)
        con.close()


def test_the_steps_their_numbers_and_functions_are_as_they_were():
    assert [(n, step.__name__) for n, step in sorted(schema._MIGRATIONS.items())] == list(STEPS)
    assert list(schema._MIGRATIONS) == [n for n, _ in STEPS]  # in the order they run
    assert STEPS[-1][0] == schema.SCHEMA_VERSION


def test_each_step_executes_what_it_did():
    expected = dict(DIGESTS)
    if not schema._FTS5_SECURE_DELETE:
        expected[2] = STEP_2_WITHOUT_SECURE_DELETE
    assert recorded() == expected


def test_step_2_without_secure_delete_executes_what_it_did(monkeypatch):
    """The other branch of step 2, on any SQLite: _delete_securely reads the flag where it is
    defined."""
    monkeypatch.setattr(_secure_delete_home(), "_FTS5_SECURE_DELETE", False)
    assert recorded()[2] == STEP_2_WITHOUT_SECURE_DELETE


def test_a_new_database_is_made_as_it_was():
    assert created() == CREATED


def _secure_delete_home() -> Any:
    """The module whose _delete_securely reads _FTS5_SECURE_DELETE."""
    return importlib.import_module(schema._delete_securely.__module__)
