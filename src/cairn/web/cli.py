"""The command line: serve, key-create, key-revoke, the source commands, plan-set, usage
and workspace-list (python -m cairn.server); export, import, compact and repair are in
cairn.web.cli_data. `google-authorize` (C8), which obtains one person's Google credential for
a Google source and opens no database, is in cairn.web.cli_google, whose docstring holds its
contract.
`connector-check` (C9), which holds a connector of the operator's own to protocol 1 and opens
no database, is in cairn.sources.connector_check, whose docstring holds its contract.

`serve --sign-in PROVIDER` (repeatable: github, google, oidc) turns sign-in on. A provider's
settings come from the environment, never from arguments, so no secret lands in a process
list or a shell history: CAIRN_<PROVIDER>_CLIENT_ID and CAIRN_<PROVIDER>_CLIENT_SECRET, and
for oidc also CAIRN_OIDC_ISSUER, with CAIRN_OIDC_NAME (route name, "sso"), CAIRN_OIDC_LABEL
("Single sign-on"), CAIRN_OIDC_GROUPS_CLAIM and CAIRN_OIDC_ALLOWED_GROUPS (comma-separated,
which needs the claim) optional; either one set but blank is an error, never a group rule
turned off. CAIRN_ALLOWED_EMAILS (addresses separated by commas, for every provider) limits
sign-in to the people it names (SignIn.allowed_emails); there is no option for it, so an
address never lands on a command line. Set but blank, holding an entry that is not one
address, or set without --sign-in, it is an error. `--sign-in oidc --join default` also
needs one of --allowed-email-domain, CAIRN_OIDC_ALLOWED_GROUPS, CAIRN_ALLOWED_EMAILS or
--closed-directory (the operator's word that only their own people can get an account at
that provider): an OIDC directory may allow public self-registration. A missing variable or
a configuration that cannot work exits 2 and names the problem, never a value.

`serve` writes the access log (Memory.access) to stderr, one plain line per MCP tool call or
REST request: "cairn.access: <action> by <actor kind> '<actor>' (<actor id>), project '<name>':
<status>". The project reads None, without quotes, when the call acted on no project. A
request refused before anyone was identified has no caller and no project, and its line says
so in words ("denied by an unknown caller: 401"). It is the only request log: uvicorn's own
stays off
(see `_serve`), and so do httpx's lines for the requests cairn sends (to a source, to a
sign-in provider), which carry query strings.

`serve --trusted-proxy ADDRESS_OR_NETWORK` (repeatable) names the reverse proxies that may say
who the client is: a request from one of them is counted under the address its
X-Forwarded-For names (the last one the proxies did not add), through uvicorn's own
proxy-header handling, which also takes its X-Forwarded-Proto as the request's scheme.
Without the option no peer is trusted, loopback included (uvicorn alone would trust
127.0.0.1). What reads the client: the per-client limits (agent registration, sign-in). What
reads the scheme: a URL built from the request, which only happens without --public-url
(an invitation's link) or in Starlette's redirect of a path with a trailing slash. The
session and sign-in cookies' Secure flag follows --public-url, never the request's scheme.
An entry that is neither an address nor a network (no host bits set: 172.30.0.0/24, not
172.30.0.1/24) stops serve (exit 2), because uvicorn would silently never match it.

`serve --sync-every SECONDS` (at least 1) syncs every workspace's sources in the background
(cairn.sources.sync_forever); a failing source is logged on "cairn.sources" by name and
error class. Sources, acting as the command line (no person) in the default workspace or in
`--workspace ACCOUNT`:
- `source-add --name N --kind K --config JSON`: the config as the kind checks it; refused
  (exit 1) with the reason: the rule the config broke, and the setting when one setting broke
  it (a value that looks like a credential is refused without saying where, except in
  base_url, whose host and path are checked piece by piece and which the refusal names, so
  no source added puts a key where `source-list` prints its address). It may repeat the
  source's name, its kind or a project's name the workspace does not have; it never repeats a
  value that looks like a credential, in any setting, nor what a `*_env` setting's variable
  holds. A config names the environment variables that hold a source's keys and never holds
  a key. A source of a kind that starts off (SourceKind.starts_off) is added off, and the
  line says so and how to switch it on (naming the kind's default project, if any, as the
  --project to give); any other is on from the moment it is added.
- `source-enable --name N [--project B]...` switches it on (Store.enable_source): each
  --project replaces the projects its config names, else they are kept; what it reads then is
  its kind's to say (SourceKind.switching_on: a kind that starts off reads only what
  happens from then on; Langfuse goes on from where it stopped). `source-disable --name N`
  switches it off (Store.disable_source): its cursor and what it wrote stay. Exit 0 and one
  line: the source, its state and its projects. Exit 1 and one line on stderr for no such
  source, no such project, or a kind that refuses the projects. Owners and admins do the same
  from GET /sources; only the command line adds a source from a config (one made from an
  account is added over the API: Credentials, below).
- `source-list`: one line per source: name, kind, its state ("on since <when>" or "off"),
  address, projects, and the last run's counts or error ("never run" before the first).
- `source-sync --name N`: one run, its counts printed; a source that is off is not read and
  the line says so (exit 0). Exit 1 with the reason (a SourceError's
  message, or the error's class) when it fails, or for an unknown source. A source another run
  holds is left alone and said so (exit 0). `--all`: every source of every workspace (no
  `--workspace`), one line each; exit 1 when any failed. The keys are read from this
  process's environment; nothing printed holds one.
- `source-remove --name N`: the notes it wrote stay. Exit 1 when there is no such source.
A missing database or an unknown workspace exits 1.

Credentials (R2): an account at a service, registered once, that owners and admins then make
sources from over the API (POST /api/integrations), choosing what it reads and one project,
without naming a variable, an address or a subject (Store.add_source_from).
- `credential-add --name N --kind K --config JSON [--for EMAIL]`: the kind's account settings
  only (cairn.sources.CHOOSING: the variables that hold its secrets, its address, Google's
  subject); a selector, `projects`, a reading option, or a value that holds what one of the
  variables it names holds in this process's environment is refused (exit 1). `--for` makes it
  that member's: only they route it. It reads nothing and makes no source.
- `credential-list`: one line per credential: name, kind, whose, its account's address when
  it names one, and how many sources were made from it.
- `credential-remove --name N`: disconnects it; its sources are switched off in the same
  write and stay listed, off; their notes stay. Exit 1 when there is no such credential.

`serve --compact-every HOURS` (24; 0: never) compacts the database in the background when a
workspace was deleted since the last compaction and that many hours have passed since it
(cairn.web.compactor); each run is one line on the access log with how long it took.

A page's history (Q1): `serve` without --plans keeps the newest KEEP_VERSIONS (50) versions
of each page; `--keep-versions N` (a whole number, 1 or more) keeps N and `--keep-versions
all` every version; anything else exits 2 (argparse). It builds the app with
`create_app(store, keep_versions=N)` (None for all). With --plans each plan's
versions_per_page rules and `--keep-versions`, whatever its value, exits 2 with one line on
stderr (KEEP_VERSIONS_WITH_PLANS) before anything is opened or made. At start, before it
listens, `serve` records in the database what it keeps (Store.record_history_bound: the
number, every version, or each plan's versions_per_page by name and the default plan) and,
when pages hold more versions than it keeps, says so on one line of stderr: how many pages,
the bound, and that `--keep-versions all` keeps them. It removes nothing then. `compact
--db PATH` first removes, in one transaction, what the last such record says no page keeps
(Store.prune_by_record), and only that: with plans, each workspace by its plan in force,
and a workspace on a plan the record does not name keeps every version and is named on a
second line. Its first line says how many versions it removed and by what rule; on a
database no `serve` of this version has recorded on it removes none, says so naming
`serve`, and compacts as before (exit 0).

Numbers an operator can read (Q1): `stats --db PATH [--json]` prints the install's numbers
and each workspace's (cairn.web.stats) and only reads: it opens the file with SQLite alone,
read-only, immutable when no "-wal" file is beside it (cairn.web.cli_stats), so it runs
beside a running `serve` and on a volume it may not write. It refuses as `key-list` does,
with the same line (a missing database or a folder, a file that is not a database, one it
may not read, one of a newer schema), and refuses an older schema too, on purpose, with
cli_stats.OLDER_DATABASE, which says to start the server once. `serve --stats-every MINUTES`
(STATS_EVERY, 15; 0: off; anything that is not a number of 0 or more exits 2) logs the
install's totals as one line on cairn.web.stats.STATS_LOGGER, written to stderr beside the
access log, at start and then that often (create_app's `stats_every`).
`stats --other-projects` adds the pages that cite a passage not of their own project's
notes; `repair --db PATH` (cairn.web.cli_data) rewrites them as the upgrade to schema
32 did, in one transaction, and prints counts only.

`serve --sweep-every SECONDS` (600; 0: never; otherwise at least 1) removes, in the
background, the rows that have run out in every workspace: sessions, invitations, OAuth codes
and tokens, grants whose refresh token expired, and agents never approved within 7 days
(Store.sweep_expired; cairn.web.sweeper). The first sweep runs at start. A sweep that removed
rows is one line on the access log with how many; a negative, non-numeric, nan or inf value,
or one between 0 and 1, exits 2.

`serve --compiles N` (2; at least 1) compiles at most N notes at a time (G1; cairn.web.turns):
the others wait their turn in the order they came, holding no thread, and nothing that is not
a note's compile waits for them.

`serve --registrations-per-hour N` (20; at least 1) is how many agents one client address may
register in any hour before /register answers 429 (cairn.oauth). The apps of some vendors
register from the vendor's servers, so many people's agents arrive from a few addresses: a
hosted install raises it.

Plans (H6): `serve --plans FILE` caps every workspace by its plan (cairn.plans.load; a file
that cannot be read or is not a plans file exits 2 and says why). Without it nothing is
capped. `plan-set --db PATH --account ID --plan NAME` records a workspace's plan: a name of 1
to 64 characters with no blank at either end (cairn.store.PLAN_NAME_MAX; any other exits 1),
not checked against a plans file, so one the running server's file does not list falls back
to its default. `--default` clears it. `usage --db PATH --account ID` prints the workspace's
usage this UTC month as one JSON object: account, plan, period, notes, note_bytes,
model_tokens_in, model_tokens_out, model_micros, stored_bytes, passages (spans that become
claims, every project) and passages_cap, and reservations: each model call's reservation not
settled (id, period, micros, reserved_at), counted in model_micros in full. passages_cap is
the passages the storage cap of the workspace's plan in force allows (one per
cairn.store.BYTES_PER_PASSAGE bytes; cairn.plans.plan_cap) when `usage --plans FILE` names
the server's plans, else None, as for a plan with no storage cap; a file it cannot use exits
2. Both exit 1 for a missing database or an unknown account; plan-set reads no plans file.
`workspace-list --db PATH` (H10) prints the ids those two take: a JSON array, one object per
workspace in creation order (the default one first), each with exactly account, name,
created_at, members (how many people belong to it), plan (its plan in force by
Store.plan_in_force's rule: the one plan-set recorded; null for the server's default), from
one read (Store.list_account_summaries). No email address; it reads no plans file, so a plan
name the server's file no longer lists is printed as recorded. It writes nothing beyond what
opening the store does: an older schema is migrated, as by every command.
No database: exit 1, one line on stderr, none made.
`source-sync --plans FILE` (--name or --all) holds each workspace to its plan in force now
(the operator's, else the default), as `serve --plans` does: a draft
past a cap waits for the next run ("<n> left for the next run at the plan's cap"); a bad
file exits 2 and syncs nothing. Without --plans it caps nothing, and says so first on one
line of stderr (NO_PLAN).

A model that arranges pages (T4b; cairn.web.cli_arrange): `serve --model-env PREFIX
[--model-price IN,OUT]` runs it in the background, and `arrange --db PATH --account ID
[--project NAME] --model-env PREFIX` makes one pass on the command line. The model's settings
come from the environment, never from arguments. `--tidy` in place of `--model-env` (T4f)
arranges with no model: each heading's claims together and claims that repeat folded, with
no key and no budget. `serve` tidies by default (decision 76): `serve --no-tidy` runs no
worker and loads no arranging code, and `serve --model-env` runs the model in its place.

A database a newer cairn wrote that this one may not run on (its schema version above
cairn.store.SCHEMA_VERSION, and the oldest build it records too; the store raises
SchemaTooNew before it reads or writes anything) stops every command with exit 1 and one line
on stderr (NEWER_DATABASE), nothing on stdout: "SchemaTooNew: the database has schema version
<found> and needs a cairn of schema version <floor> or later; this cairn knows up to <known>.
Run the cairn that wrote it or a newer one, or restore the database from before the
upgrade." The file is left as it was, nothing is made beside it (no key file, no export) and
`serve` never starts listening. A newer database whose oldest build is at or below
SCHEMA_VERSION (its newer steps only added) is opened and used as any other.

A database at this cairn's schema version that lacks a table of that schema (the store
raises UnknownSchema before it writes anything) stops every command the same way, with the
store's sentence as the line: "the database was not made by this version of cairn: it has
schema version <version> but no table <table>".

Only `serve` brings a database of an older schema to SCHEMA_VERSION, and only once it holds
the server's lock (R1, below): its open, the store's default, comes after take_serve_lock.
Every other command opens the store with upgrade=False, which reads the version under the
write lock of the step that would have run and raises SchemaTooOld before anything is
written: exit 1, one line on stderr (cli_data.OLDER_SCHEMA, which names the command and says
to start the server once on it), nothing on stdout, nothing written (a log a killed server
left beside the file is folded into it by SQLite as the connection closes, its rows
unchanged). `stats` and
`audit-verify` read with SQLite alone and refuse one in their own words. A busy database is
SQLite's error, as before; an empty or new file is still made at this schema by the commands
that make one. An older build's `serve` took the same lock on the same file (since the first
public version), so a new server cannot upgrade beneath one; an older build's other
commands take no lock, and one still open across the upgrade can still write into the
upgraded database.

A --db path the store cannot open as a cairn database (the store raises CannotOpen from
SQLite's own refusal before it writes anything, and a folder cairn may not make for it is the
same) stops every command with exit 1 and one line on stderr, nothing on stdout, chosen by
what the disk shows: "the file at PATH is not a cairn database; --db names the database
file" (from serve, key-create and import, which create one: "the file at PATH is not a cairn
database; --db names the database file, or a path where none exists yet"); "cairn may not
read or write PATH; run cairn as the file's owner, or let cairn's user own the file and its
folder"; "PATH is a folder; --db names a file"; "cairn may not create the database under
FOLDER; run cairn as the folder's owner, or let cairn's user own the folder". The commands
that need a database still answer a folder or a missing file with "no database at PATH".
Nothing is written, made or removed, and `serve` never starts listening. A busy database, a
refusal nothing on disk explains, or an error once it is open, surfaces as before.

One `serve` per database (R1). What one server keeps in memory (rate limits, the arranging
queue, the sign-in limits) is right only while it is the only server on its
database, so `serve` takes an exclusive lock for as long as it runs, before it opens the
store: flock(2) on a file beside the database, `<db>.serve.lock` (SERVE_LOCK, in the folder
of the database's real path, so two spellings of one path or a link to the file find the
same lock). Not the database file itself: SQLite holds POSIX locks on it, and a process that
closes any descriptor of that file loses them. A second `serve` on the same database exits 2
at once with one line on stderr (ANOTHER_SERVER, naming --db as given and no other path),
having opened and changed nothing. The lock is the operating system's, released when the
process ends however it ends, so a file left beside the database blocks nothing. A killed
server leaves it, and a stopped one can: uvicorn can end the process by the signal it was
stopped with, and then serve's own clean-up does not run. When the clean-up runs (a start
that fails after taking the lock, or a server that returns from uvicorn) it removes the file
while it still holds the lock, and whoever takes the lock checks that the file it locked is
still the one at that path. A lock file cairn may not make is answered as the database it
could not open (the lines above), or else with SERVE_LOCK_REFUSED, exit 1. Only `serve`
takes it: every other command runs beside a running server as before."""

import argparse
import asyncio
import contextlib
import fcntl
import ipaddress
import json
import logging
import math
import os
import re
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import uvicorn

from cairn import __version__
from cairn.gate import lint_gate
from cairn.identity import OIDC, GitHub, Google, IdentityProvider
from cairn.memory import ACCESS_LOGGER
from cairn.plans import Plans, plan_cap
from cairn.plans import load as load_plans
from cairn.sources import CHOOSING, KINDS, connector_check
from cairn.sources.runner import (
    MIN_SYNC_EVERY,
    PREVIEW_DAYS,
    PREVIEW_DAYS_MAX,
    PreviewBusy,
    SyncReport,
    kept_error,
    preview_source,
    run_source,
    source_client,
    sync_all,
)
from cairn.sources.url import private_addresses_allowed
from cairn.store import (
    JOINS,
    SCHEMA_VERSION,
    CannotOpen,
    SchemaTooNew,
    SchemaTooOld,
    SourceInfo,
    Store,
    UnknownSchema,
)
from cairn.store.base import AUDIT_KEEP, AUDIT_LOGGER
from cairn.store.schema import ForeignSchema, Uninitialized
from cairn.store.sources import FORMER_MEMBER, HOSTED_LEFT
from cairn.web import (
    cli_arrange,
    cli_audit_verify,
    cli_data,
    cli_google,
    cli_reseal,
    cli_stats,
    google_connect,
)
from cairn.web import slack as web_slack
from cairn.web.app import create_app
from cairn.web.auth import SESSION_TTL
from cairn.web.brand import make_brand
from cairn.web.components import made_with_words, scope_of, unrecorded_words
from cairn.web.filing import FILE_EVERY, TRIES_MAX
from cairn.web.public import read_operator_notes
from cairn.web.signin import SignIn, email_entries
from cairn.web.stats import STATS_LOGGER
from cairn.web.turns import COMPILES
from cairn.web.ui import SOURCE_COUNTS

PROVIDERS = ("github", "google", "oidc")
PROVIDER_TIMEOUT = 10.0  # seconds for each call to an identity provider
COMPACT_EVERY_MAX = 1_000_000  # hours `serve --compact-every` stays under (some 114 years)
DB_EXISTS = cli_data.DB_EXISTS  # the --db help of a command that never creates the database
# What `source-sync` without --plans says, on one line of stderr, before it syncs.
NO_PLAN = "no plan is applied: this sync caps nothing (--plans FILE holds workspaces to theirs)"
# What every command says, on one line of stderr (exit 1), when a newer cairn wrote the
# database; {refusal} is SchemaTooNew's message, which gives both schema versions.
NEWER_DATABASE = (
    "SchemaTooNew: {refusal}. Run the cairn that wrote it or a newer one, or restore the"
    " database from before the upgrade."
)
# The commands whose --db may name a path where no database is yet: they create it.
CREATES = ("serve", "key-create", "import")
# What every command says, on one line of stderr (exit 1), when the store cannot open --db
# as a cairn database (CannotOpen), by what the disk shows (_cannot_open). Each names the
# path once and nothing from inside the file.
NOT_A_DATABASE = "the file at {path} is not a cairn database; --db names the database file"
NOT_A_DATABASE_YET = NOT_A_DATABASE + ", or a path where none exists yet"
MAY_NOT_OPEN = (
    "cairn may not read or write {path}; run cairn as the file's owner, or let cairn's user"
    " own the file and its folder"
)
A_FOLDER = "{path} is a folder; --db names a file"
MAY_NOT_CREATE = (
    "cairn may not create the database under {folder}; run cairn as the folder's owner, or"
    " let cairn's user own the folder"
)
# One `serve` per database (the module's docstring): the lock file beside it, and what a
# second `serve` says, on one line of stderr (exit 2).
SERVE_LOCK = ".serve.lock"
ANOTHER_SERVER = (
    "another server is running on this database ({db}): stop it before starting another"
)
SERVE_LOCK_REFUSED = "cannot take the lock that keeps one server on {db}: {reason}"
# Q1: how many versions of each page `serve` keeps without plans, unless --keep-versions says.
KEEP_VERSIONS = 50
ALL_VERSIONS = "all"  # --keep-versions all: every version
STATS_EVERY = 15.0  # minutes between the lines `serve` logs with the install's numbers (Q1)
GRAPH_EXPLORER_BY_DEFAULT = True  # G2: `serve` opens the explorer; --no-graph-explorer turns it off
KEEP_VERSIONS_WITH_PLANS = (
    "--keep-versions does not go with --plans: each plan's versions_per_page says how many"
    " versions of a page it keeps"
)
_source_http = source_client  # the client sources are read through (a test swaps it)


def _label(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("a label can't be blank")
    return value


def _url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise argparse.ArgumentTypeError("use a full URL, such as https://cairn.example.net")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cairn.server",
        description="Run cairn's web service; manage its keys, sources and plans; arrange its"
        " pages, tidied by default or with a model; export, import, compact and repair its data.",
        allow_abbrev=False,  # a mistyped option fails; it is never read as a longer one
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser(
        "serve", help="run the REST API, the MCP endpoint and the UI", allow_abbrev=False
    )
    serve.add_argument("--db", type=Path, required=True, help="SQLite file, created if missing")
    serve.add_argument("--host", default="127.0.0.1", help="interface to bind (127.0.0.1)")
    serve.add_argument("--port", type=int, default=8765, help="port to bind (8765)")
    serve.add_argument(
        "--public-url", type=_url, help="the URL clients use, e.g. https://cairn.example.net"
    )
    serve.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        dest="allowed_hosts",
        metavar="HOST",
        help="one more Host header /mcp accepts (repeatable)",
    )
    serve.add_argument(
        "--trusted-proxy",
        action="append",
        default=[],
        dest="trusted_proxies",
        metavar="ADDRESS_OR_NETWORK",
        help="a reverse proxy whose X-Forwarded-For names the client (repeatable); without it"
        " no peer is trusted",
    )
    serve.add_argument(
        "--sign-in",
        action="append",
        default=[],
        choices=PROVIDERS,
        dest="sign_in",
        metavar="PROVIDER",
        help="people sign in through this provider (repeatable: github, google, oidc); its"
        " client id and secret come from CAIRN_<PROVIDER>_CLIENT_ID and _CLIENT_SECRET",
    )
    serve.add_argument(
        "--join",
        choices=JOINS,
        default="personal",
        help="where a new person lands: a workspace of their own (personal) or the default one",
    )
    serve.add_argument(
        "--allowed-email-domain",
        action="append",
        default=[],
        dest="domains",
        metavar="DOMAIN",
        help="only people with a verified email in this domain sign in (repeatable)",
    )
    serve.add_argument(
        "--closed-directory",
        action="store_true",
        help="with --sign-in oidc --join default: you state that only your own people can get"
        " an account at that provider (no public self-registration)",
    )
    serve.add_argument(
        "--compact-every",
        type=_hours,
        default=24.0,
        metavar="HOURS",
        help="after a workspace is deleted, compact the database at most this often (24; 0:"
        f" never; otherwise fewer than {COMPACT_EVERY_MAX}); it blocks every request for as"
        " long as it runs",
    )
    serve.add_argument(
        "--sweep-every",
        type=_sweep_seconds,
        default=600.0,
        metavar="SECONDS",
        help="remove expired sessions, invitations and agent credentials this often (600; 0:"
        " never; otherwise at least 1)",
    )
    serve.add_argument(
        "--compiles",
        type=_at_least_one,
        default=COMPILES,
        metavar="N",
        help=f"compile at most N notes at a time ({COMPILES}); the others wait their turn, in"
        " the order they came, and nothing else waits for them",
    )
    serve.add_argument(
        "--registrations-per-hour",
        type=_at_least_one,
        default=20,
        metavar="N",
        help="how many agents one client address may register in an hour (20); raise it when"
        " many people's agents register from a few addresses, as a vendor's cloud does",
    )
    _wall_options(serve)
    google_connect.add_options(serve)
    web_slack.add_options(serve)
    create = commands.add_parser(
        "key-create",
        help="mint an API key into a new 0600 file",
        description=KEY_CREATE_HELP,
        allow_abbrev=False,
    )
    create.add_argument("--db", type=Path, required=True, help="SQLite file, created if missing")
    create.add_argument("--label", type=_label, required=True, help="who or what holds the key")
    create.add_argument("--out", type=Path, required=True, help="a file that must not exist")
    create.add_argument(
        "--project",
        action="append",
        dest="projects",
        metavar="NAME",
        help="the key reaches this project, by name (repeatable); it reaches no other",
    )
    create.add_argument("--read-only", action="store_true", help="the key reads and never writes")
    listed = commands.add_parser(
        "key-list",
        help="list the workspace's keys and what each reaches",
        description=KEY_LIST_HELP,
        allow_abbrev=False,
    )
    listed.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
    revoke = commands.add_parser(
        "key-revoke", help="revoke every key with a label", allow_abbrev=False
    )
    revoke.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
    revoke.add_argument(
        "--label", type=_label, required=True, help="the label the keys were created with"
    )
    revoke.add_argument(
        "--with-made",
        action="store_true",
        help="also revoke every key and agent made with these keys, or with a key they made,"
        " in one transaction (without it they stay, and are listed)",
    )
    ended = commands.add_parser(
        "session-end",
        help="end every web session of one person, in every workspace",
        description=SESSION_END_HELP,
        allow_abbrev=False,
    )
    ended.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
    ended.add_argument(
        "--email",
        required=True,
        metavar="ADDRESS",
        help="the person's address: the whole address, in any case, as the allowlist reads it",
    )
    serve.add_argument(
        "--sync-every",
        type=_seconds,
        metavar="SECONDS",
        help="sync every workspace's sources in the background, a pass every SECONDS (>= 1)",
    )
    serve.add_argument(
        "--plans",
        type=Path,
        metavar="FILE",
        help="cap every workspace by its plan, from this YAML file;"
        " deploy/plans.example.yaml is an example",
    )
    serve.add_argument(  # Q1: a page's history bound without plans
        "--keep-versions",
        type=_keep_versions,
        metavar="N",
        help=f"keep the newest N versions of each page ({KEEP_VERSIONS}; all: every version);"
        " not with --plans, where each plan says",
    )
    serve.add_argument(  # each read an event of its workspace's audit log
        "--audit-reads",
        action="store_true",
        help="record each read of a page, a note, a claim's evidence, a search and a graph"
        " in its workspace's audit log, with who read and what (never the words searched);"
        " each read then costs one more write",
    )
    serve.add_argument(
        "--audit-keep",
        type=_audit_keep,
        metavar="N",
        help=f"keep the newest N events of each workspace's audit log ({AUDIT_KEEP};"
        " all: every event)",
    )
    serve.add_argument(  # Q1: the install's numbers, a line in the log
        "--stats-every",
        type=_stats_minutes,
        default=STATS_EVERY,
        metavar="MINUTES",
        help=f"log the install's counts and sizes this often ({STATS_EVERY:g}; 0: never)",
    )
    serve.add_argument(
        "--operator-notes",
        type=Path,
        metavar="FILE",
        help="Markdown (64 KiB at most) shown as the last section of /docs/privacy: who runs"
        " this install, where, and how to reach them",
    )
    serve.add_argument(
        "--docs-artifact",
        type=Path,
        metavar="PATH",
        help="trusted local documentation release artifact; requires both other --docs options",
    )
    serve.add_argument(
        "--docs-release",
        metavar="FULLSHA",
        help="expected full lowercase documentation release commit",
    )
    serve.add_argument(
        "--docs-reader-env",
        metavar="NAME",
        help="name of the runtime environment variable holding the read-only documentation key",
    )
    graph_explorer = serve.add_mutually_exclusive_group()
    graph_explorer.add_argument(  # G2
        "--graph-explorer",
        action="store_true",
        help="make a project's graph page the explorer, the one page that loads a script"
        " (cairn's own, by a nonce and its hash); the default",
    )
    graph_explorer.add_argument(
        "--no-graph-explorer",
        action="store_true",
        help="draw a project's graph page on the server alone with no script; serve shows"
        " the explorer unless this is given",
    )
    cli_arrange.add_options(serve)
    _source_parsers(commands)
    _credential_parsers(commands)
    cli_data.add_commands(commands)
    _plan_parsers(commands)
    _waiting_parser(commands)
    cli_stats.add_command(commands)  # Q1
    cli_audit_verify.add_command(commands)  # reads only, as stats
    cli_arrange.add_commands(commands)
    cli_google.add_command(commands)
    cli_reseal.add_command(commands)
    connector_check.add_command(commands)
    return parser


def _wall_options(serve: argparse.ArgumentParser) -> None:
    """B1: the wall, the brand and how long a sign-in lasts (cairn.web.brand, cairn.web.auth);
    and what a visitor to a test instance is told (create_app's `test_instance`)."""
    serve.add_argument(
        "--test-instance",
        action="store_true",
        help="with --sign-in limited to the people you approve: a browser that is not signed in"
        " is told this is a private test instance for approved testers and offered the guide"
        " to run cairn itself; who can sign in does not change",
    )
    serve.add_argument(
        "--wall",
        action="store_true",
        help="with --sign-in: a browser that is not signed in is shown the sign-in page and"
        " nothing else; what agents and scripts use answers as without it",
    )
    serve.add_argument(
        "--brand-name",
        metavar="NAME",
        help="with --sign-in: this name (1 to 60 characters, no < or >) in place of cairn's"
        " wherever a browser that is not signed in can look, and in each page's frame",
    )
    serve.add_argument(
        "--brand-logo",
        type=Path,
        metavar="FILE",
        help="with --brand-name: an SVG or PNG file of at most 256 KB, told by its content,"
        " shown on the sign-in page and in each page's frame",
    )
    serve.add_argument(
        "--brand-logo-alt",
        metavar="TEXT",
        help="with --brand-logo: what the logo says, for people who cannot see it (the brand"
        " name when not given)",
    )
    serve.add_argument(
        "--idle-minutes",
        type=_idle_minutes,
        metavar="N",
        help="with --sign-in: a sign-in not used for N minutes (1 to 1440) ends, up to a minute"
        " late, whatever its length; without it, use is not recorded and nothing ends for it",
    )
    serve.add_argument(
        "--require-mfa",
        action="store_true",
        help="with --sign-in through oidc only: a sign-in is refused unless the provider's ID"
        " token shows a second factor (amr holding mfa, or a multi-factor acr); github and"
        " google do not say, so serve refuses to start with them",
    )
    serve.add_argument(
        "--session-days",
        type=_days,
        metavar="N",
        help="with --sign-in: a sign-in lasts N days (1 to 365), and a use more than a day"
        " after it was made or last renewed starts the N days again (so 1 is never renewed);"
        " without it, 12 hours and no renewal. At start, a sign-in made under a longer length"
        " is cut to end at most this length from then",
    )


def _idle_minutes(text: str) -> int:
    try:
        minutes = int(text)
    except ValueError:
        minutes = 0
    if not 1 <= minutes <= 1440:
        raise argparse.ArgumentTypeError("a whole number of minutes from 1 to 1440")
    return minutes


def _days(text: str) -> int:
    try:
        days = int(text)
    except ValueError:
        days = 0
    if not 1 <= days <= 365:
        raise argparse.ArgumentTypeError("a whole number of days from 1 to 365")
    return days


def _would_do_nothing(args: argparse.Namespace) -> str | None:
    """The sentence for a B1 setting given without the one it needs; None when none is."""
    if not args.sign_in:
        for option, given in (
            ("--wall", args.wall),
            ("--test-instance", args.test_instance),
            ("--brand-name", args.brand_name is not None),
            ("--session-days", args.session_days is not None),
            ("--require-mfa", args.require_mfa),
            ("--idle-minutes", args.idle_minutes is not None),
        ):
            if given:
                return f"{option} needs --sign-in: without it nobody signs in through a provider"
    if args.brand_logo is not None and args.brand_name is None:
        return "--brand-logo needs --brand-name"
    if args.brand_logo_alt is not None and args.brand_logo is None:
        return "--brand-logo-alt needs --brand-logo"
    return None


def _plan_parsers(commands: Any) -> None:
    plan = commands.add_parser(
        "plan-set", help="record which plan a workspace is on", allow_abbrev=False
    )
    plan.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
    plan.add_argument("--account", required=True, help="the workspace's id")
    which = plan.add_mutually_exclusive_group(required=True)
    which.add_argument("--plan", help="the plan's name in the server's plans file")
    which.add_argument("--default", action="store_true", help="the plans file's default plan")
    usage = commands.add_parser(
        "usage", help="print a workspace's usage this month as JSON", allow_abbrev=False
    )
    usage.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
    usage.add_argument("--account", required=True, help="the workspace's id")
    usage.add_argument(
        "--plans",
        type=Path,
        metavar="FILE",
        help="the server's plans file, for the caps of the workspace's plan in force",
    )
    listed = commands.add_parser(
        "workspace-list",
        help="print every workspace's id, name, members and plan as JSON",
        description=WORKSPACE_LIST_HELP,
        allow_abbrev=False,
    )
    listed.add_argument("--db", type=Path, required=True, help=DB_EXISTS)


def _waiting_parser(commands: Any) -> None:
    waiting = commands.add_parser(
        "waiting",
        help="list the notes stored and not yet filed on their page",
        description=WAITING_HELP,
        allow_abbrev=False,
    )
    waiting.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
    waiting.add_argument(
        "--workspace", metavar="NAME", help="only this workspace's notes, by its name or id"
    )
    waiting.add_argument("--json", action="store_true", help="print a JSON array instead")


# What `waiting --help` says (N1).
WAITING_HELP = (
    "List the notes that are stored and not yet filed on their page, oldest first, one line"
    " each: the note's id, its workspace and project, when it was stored (UTC), and how many"
    " tries to file it ended with no outcome. Such a note is not searchable yet: serve files"
    " it by itself, at start and then every few minutes, and one left after"
    f" {TRIES_MAX} tries stays listed here. It prints no note's text or title, and changes"
    " nothing."
)


def _waiting(db: Path, workspace: str | None, as_json: bool) -> int:
    """waiting (N1): one line per note waiting for its page, or a JSON array; never a note's
    text or title. 1 for a missing database or an unknown workspace."""
    if not db.is_file():
        print(f"no database at {db}", file=sys.stderr)
        return 1
    store = Store.open_existing(db)
    try:
        notes = store.waiting_notes()
        if workspace is not None:
            accounts = store.list_accounts()
            found = [a for a in accounts if workspace in (a.id, a.name)]
            if not found:
                print(f"no workspace named {workspace!r}", file=sys.stderr)
                return 1
            ids = {account.id for account in found}
            notes = [note for note in notes if note.account_id in ids]
    finally:
        store.close()
    if as_json:
        print(json.dumps([note.model_dump() for note in notes], indent=2))
    elif not notes:
        print("No note is waiting for its page.")
    for note in [] if as_json else notes:
        print(
            f"{note.note_id}  workspace {note.workspace!r} ({note.account_id})"
            f"  project {note.project!r}  stored {note.stored_at}  tries {note.tries}"
        )
    return 0


# What `workspace-list --help` says: one sentence a field.
WORKSPACE_LIST_HELP = (
    "Print every workspace as a JSON array, one object each, in the order they were made"
    " (the default one first). account: the workspace's id, which plan-set and usage take."
    " name: the workspace's name. created_at: when it was made (UTC). members: how many"
    " people belong to it. plan: the plan it is held to now, the one plan-set recorded;"
    " null: the server's default. No email address. It reads no plans file and changes"
    " nothing."
)


def _plan_command(args: argparse.Namespace) -> int:
    """plan-set, usage and workspace-list, as the command line. Exit 1 for a missing
    database, an unknown account or a plan name the store refuses."""
    try:
        plans = _plans(getattr(args, "plans", None))
    except ValueError as error:
        print(f"cannot use those plans: {error}", file=sys.stderr)
        return 2
    if not args.db.is_file():
        print(f"no database at {args.db}", file=sys.stderr)
        return 1
    store = Store.open_existing(args.db)
    try:
        if args.command == "workspace-list":
            rows = [
                {"account": s.id, **s.model_dump(exclude={"id"})}
                for s in store.list_account_summaries()
            ]
            print(json.dumps(rows))
            return 0
        if args.command == "plan-set":
            store.set_plan(args.account, None if args.default else args.plan)
            print(f"{args.account}: {'the default plan' if args.default else args.plan}")
            return 0
        view = store.for_account(args.account)
        account = view.get_account(args.account)
        used = view.usage()
        printed = {
            "account": args.account,
            "plan": account.plan if account else None,
            "period": used.period,
            "notes": used.notes,
            "note_bytes": used.note_bytes,
            "model_tokens_in": used.model_tokens_in,
            "model_tokens_out": used.model_tokens_out,
            "model_micros": used.model_micros,
            "stored_bytes": view.stored_bytes(),
            "passages": view.passages(),
            "passages_cap": None
            if plans is None
            else plan_cap(plans.resolve(store.plan_in_force(args.account))[1], "passages"),
            "reservations": [held._asdict() for held in view.model_reservations()],
        }
        print(json.dumps(printed))
        return 0
    except KeyError:
        print(f"no workspace {args.account!r}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"cannot set that plan: {error}", file=sys.stderr)
        return 1
    finally:
        store.close()


def _plans(path: Path | None) -> Plans | None:
    """The plans file of `serve --plans`, or None without one. ValueError says what is wrong
    with it, never more of it than a plan's name or a field."""
    if path is None:
        return None
    try:
        return load_plans(path)
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error.strerror}") from None


def _seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("give a number of seconds") from None
    if not seconds >= MIN_SYNC_EVERY:
        raise argparse.ArgumentTypeError(f"sync at most every {MIN_SYNC_EVERY:g} s")
    return seconds


def _config(value: str) -> dict[str, Any]:
    try:
        config = json.loads(value)
    except ValueError:
        raise argparse.ArgumentTypeError("the config is not JSON") from None
    if not isinstance(config, dict):
        raise argparse.ArgumentTypeError("the config is a JSON object")
    return config


def _source_parsers(commands: Any) -> None:
    def command(name: str, help: str) -> argparse.ArgumentParser:
        parser = commands.add_parser(name, help=help, allow_abbrev=False)
        parser.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
        parser.add_argument(
            "--workspace", metavar="ACCOUNT", help="the workspace's id (the default workspace)"
        )
        return parser

    add = command("source-add", "add a source cairn reads notes from")
    add.add_argument(
        "--name",
        required=True,
        help="the source's name in its workspace: 1 to 64 letters, digits, '.', '_' or '-'",
    )
    add.add_argument("--kind", required=True, help=f"one of: {', '.join(KINDS)}")
    add.add_argument(
        "--config",
        type=_config,
        required=True,
        help="the source's settings as a JSON object; credentials are named, never given."
        ' langfuse: {"base_url": "https://...", "projects": ["a project\'s name"],'
        ' "public_key_env": "A_VARIABLE", "secret_key_env": "A_VARIABLE",'
        ' "score_name": "cairn"}; score_name is optional, and the two variables hold the'
        " keys in the environment of the process that syncs (serve --sync-every, or"
        " source-sync)."
        ' granola: {"key_env": "A_VARIABLE", "projects": [], "base_url":'
        ' "https://public-api.granola.ai/v1", "folder_id": "fol_...", "space_id": "spa_...",'
        ' "transcripts": false, "settle_minutes": 30, "lookback_days": 30}; everything but'
        " key_env is optional (these are the defaults; folder_id or space_id, at most one),"
        " the variable holds the API key, and the source starts off: switch it on with"
        " source-enable --project."
        ' google_meet: {"credentials_env": "A_VARIABLE", "subject": "person@example.com",'
        ' "projects": [], "base_url": "https://meet.googleapis.com/v2", "wait_hours": 48,'
        ' "meeting_code": "abc-mnop-xyz"}; everything but credentials_env is optional (these'
        " are the defaults; meeting_code reads only that one meeting, and is left out to read"
        " every meeting Google lists), the variable"
        " holds a Google credential as JSON text (a service account's key file, or an"
        " authorized_user with a refresh token), subject is the person whose meetings a"
        " service account reads and is left out with a refresh token, and the source starts"
        " off: switch it on with source-enable --project."
        ' google_drive: {"credentials_env": "A_VARIABLE", "folder_id": "the folder\'s id",'
        ' "subject": "person@example.com", "settle_hours": 24, "existing": false, "projects": [],'
        ' "base_url": "https://www.googleapis.com/drive/v3"}; everything but credentials_env'
        " and folder_id is optional (these are the defaults, and subject is left out unless a"
        " service account acts for that person), folder_id is the last part of the folder's"
        " address, the variable holds a Google credential as for google_meet, and the source"
        " starts off: switch it on with source-enable --project."
        ' google_calendar: {"credentials_env": "A_VARIABLE", "subject": "person@example.com",'
        ' "calendar_id": "primary", "projects": [], "base_url":'
        ' "https://www.googleapis.com/calendar/v3"}; everything but credentials_env is optional'
        " (these are the defaults, and subject is left out unless given), the variable holds a"
        " Google credential as JSON text as for google_meet, calendar_id names the one calendar"
        " it reads, a service account without subject reads a calendar shared with its own"
        " address, and the source starts off: switch it on with source-enable --project."
        ' gmail: {"credentials_env": "A_VARIABLE", "label": "the label\'s name", "subject":'
        ' "person@example.com", "lookback_days": 30, "projects": [], "base_url":'
        ' "https://gmail.googleapis.com/gmail/v1"}; everything but credentials_env and label is'
        " optional (these are the defaults, subject aside), the variable holds a Google"
        " credential as JSON text as for google_meet, label is a label a person made in Gmail,"
        " never one of Gmail's own, a service account must name subject and a refresh token"
        " leaves it out, only messages that arrive from switch-on and carry the label are read,"
        " each once, and the source starts off: switch it on with source-enable --project"
        + "".join(f". {name}: {kind.settings}" for name, kind in KINDS.items() if kind.settings),
    )
    command("source-list", "list a workspace's sources and their last run")
    sync = command("source-sync", "read new notes from one source, or from every source")
    which = sync.add_mutually_exclusive_group(required=True)
    which.add_argument("--name", help="the source to read from, by its name")
    which.add_argument("--all", action="store_true", help="every source of every workspace")
    sync.add_argument(
        "--plans",
        type=Path,
        metavar="FILE",
        help="hold each workspace to its plan in this file, as `serve --plans` does",
    )
    command("source-remove", "remove a source (what it wrote stays)").add_argument(
        "--name", required=True, help="the source to remove, by its name"
    )
    enable = command("source-enable", "switch a source on and choose the projects it writes into")
    enable.add_argument("--name", required=True, help="the source to switch on, by its name")
    enable.add_argument(
        "--project",
        action="append",
        dest="projects",
        metavar="PROJECT",
        help="a project it writes into, by name (repeatable; replaces the ones it names;"
        " leave out to keep them)",
    )
    command("source-disable", "switch a source off (what it wrote stays)").add_argument(
        "--name", required=True, help="the source to switch off, by its name"
    )
    preview = command(
        "source-preview", "show what a source would bring, by count and titles; stores nothing"
    )
    preview.add_argument("--name", required=True, help="the source to preview, by its name")
    preview.add_argument(
        "--days",
        type=int,
        default=None,
        help=f"for a source that is off, how many days back to read: 1 to {PREVIEW_DAYS_MAX}"
        f" ({PREVIEW_DAYS} unless given); a source that is on shows its next run",
    )


def _credential_parsers(commands: Any) -> None:
    """credential-add, credential-list and credential-remove (R2)."""

    def command(name: str, help: str) -> argparse.ArgumentParser:
        parser = commands.add_parser(name, help=help, allow_abbrev=False)
        parser.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
        parser.add_argument(
            "--workspace", metavar="ACCOUNT", help="the workspace's id (the default workspace)"
        )
        return parser

    add = command(
        "credential-add",
        "register an account at a service that people may make sources from; reads nothing",
    )
    add.add_argument(
        "--name",
        required=True,
        help="the credential's name in its workspace: 1 to 64 letters, digits, '.', '_' or '-'",
    )
    add.add_argument("--kind", required=True, help=f"one of: {', '.join(KINDS)}")
    add.add_argument(
        "--config",
        type=_config,
        required=True,
        help="the kind's account settings only, as a JSON object: the names of the environment"
        " variables that hold its secrets, its address (base_url or url) and, for Google, the"
        " subject; never a selector, projects or a secret's value. "
        + "; ".join(f"{kind}: {', '.join(c.credential)}" for kind, c in CHOOSING.items()),
    )
    add.add_argument(
        "--for",
        dest="owner",
        metavar="EMAIL",
        help="the member whose account it is: only they choose what its sources read and where"
        " (left out: the workspace's own, which owners and admins route)",
    )
    command("credential-list", "list a workspace's credentials: whose, and how many sources")
    command(
        "credential-remove",
        "disconnect a credential: its sources are switched off and stay listed; notes stay",
    ).add_argument("--name", required=True, help="the credential to disconnect, by its name")


def _credential_command(args: argparse.Namespace) -> int:
    """credential-add, credential-list, credential-remove: one line each, or one line on
    stderr and exit 1. Nothing printed names a variable, an address or a secret, beyond the
    account's own address (its subject) on credential-list."""
    try:
        store = _workspace(args)
    except KeyError as error:
        print(error.args[0], file=sys.stderr)
        return 1
    try:
        if args.command == "credential-add":
            named = (v for k, v in args.config.items() if k.endswith("_env") and isinstance(v, str))
            secrets = tuple(s for s in (os.environ.get(n, "").strip() for n in named) if s)
            try:
                info = store.add_credential(
                    args.name, args.kind, args.config, owner=args.owner, secrets=secrets
                )
            except (ValueError, PermissionError) as error:
                print(f"cannot add credential {args.name!r}: {error}", file=sys.stderr)
                return 1
            print(
                f"added credential {info.name!r} ({info.kind}, {_whose(info)}); it reads nothing"
                " until a source is made from it"
            )
        elif args.command == "credential-list":
            for info in store.list_credentials():
                account = f" ({info.account})" if info.account else ""
                count = f"{info.sources} source{'' if info.sources == 1 else 's'}"
                print(f"{info.name}: {info.kind}, {_whose(info)}{account}; {count}")
        else:
            try:
                hosted = store.credential_hosted(args.name)
                off = store.remove_credential(args.name)
            except KeyError:
                print(f"no credential named {args.name!r}", file=sys.stderr)
                return 1
            said = f"switched off: {', '.join(off)}" if off else "no source of it was on"
            print(
                f"disconnected credential {args.name!r}; {said}; its sources stay listed, off,"
                " and their notes stay"
            )
            if hosted:
                print(HOSTED_LEFT)
    finally:
        store.close()
    return 0


def _whose(info: Any) -> str:
    """Whose account a credential is, in words: "Ada's", or "the workspace's"."""
    if info.owner is None:
        return "the workspace's"
    return f"{info.owner_name or FORMER_MEMBER}'s"


def _workspace(args: argparse.Namespace) -> Store:
    """The workspace the command acts on, as the command line (no person): KeyError when
    there is no database or no such workspace."""
    if not args.db.is_file():
        raise KeyError(f"no database at {args.db}")
    store = Store.open_existing(args.db)
    if args.workspace is None:
        return store
    try:
        return store.for_account(args.workspace)
    except KeyError:
        store.close()
        raise KeyError(f"no workspace {args.workspace!r}") from None


def _counts(report: Mapping[str, Any]) -> str:
    counts = ", ".join(f"{report.get(name, 0)} {name}" for name in SOURCE_COUNTS)
    reasons = report.get("reasons") or {}
    why = "; ".join(f"{n} {reason}" for reason, n in sorted(reasons.items()))
    said = f"{counts} ({why})" if why else counts
    left, capped = report.get("left") or 0, report.get("capped") or 0
    if left:
        said += f"; {left} left for the next run"
    if capped:
        said += f"; {capped} left for the next run at the plan's cap"
    hold = report.get("hold")
    if isinstance(hold, dict) and isinstance(hold.get("until"), str):
        said += f"; held at the plan's monthly cap until {hold['until'][:10]} (UTC)"
    if isinstance(report.get("said"), str):  # the pull's own sentence (SyncReport.said)
        said += f"; {report['said']}"
    return said


def _ran(name: str, report: SyncReport) -> str:
    if report.off:
        return f"{name}: off, so not read (source-enable switches it on)"
    if report.busy:
        return f"{name}: another run holds this source, so this one did nothing"
    if report.held:
        until = (report.hold or {}).get("until", "")[:10]
        return f"{name}: held at the plan's monthly cap until {until} (UTC), so not pulled"
    said = f"{name}: {_counts(report.counts())}"
    if report.superseded:
        said += " (another run took the source over meanwhile, so its cursor was not stored)"
    return said


async def _sync(args: argparse.Namespace, store: Store, plans: Plans | None) -> int:
    if plans is None:
        print(NO_PLAN, file=sys.stderr)
    async with _source_http() as http:
        if not args.all:
            if store.get_source(args.name) is None:
                print(f"no source named {args.name!r}", file=sys.stderr)
                return 1
            view, plan = store, None
            if plans is not None:  # the workspace's plan in force now
                plan = plans.in_force(store, store.account_id)
                view = plans.limit(store, plan[1])
            try:
                report = await run_source(
                    view,
                    args.name,
                    http=http,
                    env=os.environ,
                    gate=lint_gate,
                    plan=plan,
                    plans=plans,
                )
            except Exception as error:
                print(f"{args.name}: failed: {kept_error(error)}", file=sys.stderr)
                return 1
            print(_ran(args.name, report))
            return 0
        failed = 0
        outcomes = await sync_all(store, http=http, env=os.environ, gate=lint_gate, plans=plans)
        for outcome in outcomes:
            where = f" (workspace {outcome.account_id})"
            if outcome.report is None:
                print(f"{outcome.source}: failed: {outcome.error}{where}", file=sys.stderr)
                failed += 1
            else:
                print(_ran(outcome.source, outcome.report) + where)
        return 1 if failed else 0


def _source_command(args: argparse.Namespace) -> int:
    if args.command == "source-sync" and args.all and args.workspace is not None:
        print("--all syncs every workspace: leave out --workspace", file=sys.stderr)
        return 2
    plans = None
    if args.command == "source-sync":
        try:
            plans = _plans(args.plans)
        except ValueError as error:
            print(f"cannot use those plans: {error}", file=sys.stderr)
            return 2
    try:
        store = _workspace(args)
    except KeyError as error:
        print(error.args[0], file=sys.stderr)
        return 1
    try:
        if args.command == "source-add":
            try:
                store.add_source(args.name, args.kind, args.config)
            except (ValueError, PermissionError) as error:
                print(f"cannot add source {args.name!r}: {error}", file=sys.stderr)
                return 1
            added = store.get_source(args.name)
            if added is not None and not added.enabled:
                project = KINDS[args.kind].proposed_project(args.name) or "PROJECT"
                print(
                    f"added source {args.name!r} ({args.kind}); it is off and reads nothing"
                    f" until it is switched on: source-enable --name {args.name}"
                    f" --project {project}"
                )
            else:
                print(f"added source {args.name!r} ({args.kind})")
        elif args.command == "source-list":
            for info in store.list_sources():
                print(_listed(info))
        elif args.command == "source-remove":
            if not store.remove_source(args.name):
                print(f"no source named {args.name!r}", file=sys.stderr)
                return 1
            print(f"removed source {args.name!r}; the notes it wrote stay")
        elif args.command in ("source-enable", "source-disable"):
            return _switch(args, store)
        elif args.command == "source-preview":
            return asyncio.run(_preview(args, store))
        else:
            return asyncio.run(_sync(args, store, plans))
    finally:
        store.close()
    return 0


async def _preview(args: argparse.Namespace, store: Store) -> int:
    """source-preview (R1): one line of counts and a line per title on stdout, or one line on
    stderr saying why there is none. Writes nothing."""
    try:
        async with _source_http() as http:
            shown = await preview_source(
                store, args.name, http=http, env=os.environ, days=args.days
            )
    except KeyError:
        print(f"no source named {args.name!r}", file=sys.stderr)
        return 1
    except (ValueError, PermissionError, PreviewBusy) as error:
        print(f"cannot preview source {args.name!r}: {error}", file=sys.stderr)
        return 1
    if not shown.read:
        print(f"{shown.source}: could not be read: {shown.said}", file=sys.stderr)
        return 1
    info = store.get_source(shown.source)
    window = "for its next run" if info and info.enabled else f"in the last {shown.days} days"
    print(
        f"{shown.source}: {shown.count} new {window}; {shown.written_before} written before;"
        f" {shown.out_of_reach} for a project out of reach"
    )
    for title in shown.titles:
        print(f"  {title}")
    if shown.more:
        print(f"  and {shown.more} more")
    return 0


def _switch(args: argparse.Namespace, store: Store) -> int:
    """source-enable and source-disable: one line saying the state and the projects, or one
    line on stderr saying why nothing changed."""
    try:
        if args.command == "source-enable":
            info = store.enable_source(args.name, args.projects)
        else:
            info = store.disable_source(args.name)
    except KeyError:
        print(f"no source named {args.name!r}", file=sys.stderr)
        return 1
    except (ValueError, PermissionError) as error:
        print(f"cannot switch source {args.name!r}: {error}", file=sys.stderr)
        return 1
    print(f"{info.name}: {_state(info)} -> {_projects(info)}")
    return 0


def _state(info: SourceInfo) -> str:
    """A source's state in words: "on since <stamp>" or "off"."""
    return f"on since {info.enabled_at}" if info.enabled else "off"


def _projects(info: SourceInfo) -> str:
    return ", ".join(info.config.get("projects", [])) or "no project yet"


def _listed(info: SourceInfo) -> str:
    where = info.config.get("base_url", "")
    projects = ", ".join(info.config.get("projects", []))
    if info.last_run_at is None:
        last = "never run"
    elif info.last_error is not None:
        last = f"last run {info.last_run_at} failed: {info.last_error}"
    else:
        last = f"last run {info.last_run_at}: {_counts(info.last_report or {})}"
    return f"{info.name} ({info.kind}) {_state(info)}; {where} -> {projects}; {last}"


class ServeLock:
    """The exclusive lock one `serve` holds on its database (take_serve_lock)."""

    def __init__(self, path: Path | None, fd: int | None) -> None:
        self.path, self._fd = path, fd

    def release(self) -> None:
        """Remove the lock file while it is still held, then let go of it. Whoever takes it
        next makes a new one, and checks that it locked the file at the path."""
        if self._fd is None:
            return
        try:
            if self.path is not None and _same_file(self._fd, self.path):
                self.path.unlink()
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None


def _same_file(fd: int, path: Path) -> bool:
    try:
        return os.path.samestat(os.fstat(fd), os.lstat(path))
    except FileNotFoundError:
        return False


def serve_lock_path(db: Path) -> Path:
    """`<db>.serve.lock` beside the database's real path."""
    real = Path(os.path.realpath(db))
    return real.with_name(real.name + SERVE_LOCK)


def take_serve_lock(db: Path) -> ServeLock | None:
    """The exclusive lock of the one `serve` on `db`, or None when another process holds
    it; it never waits. A database in memory is no file and needs none. OSError when the
    lock file cannot be made or opened (O_NOFOLLOW: a link planted there is refused)."""
    if str(db) == ":memory:":
        return ServeLock(None, None)
    path = serve_lock_path(db)
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
    while True:
        fd = os.open(path, flags, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        except BaseException:
            os.close(fd)
            raise
        if _same_file(fd, path):
            return ServeLock(path, fd)
        os.close(fd)  # the server that held it removed it between our open and our lock


def _open_store(db: Path) -> Store:
    """The store at `db`, creating its directory first (a fresh checkout has none), each
    folder made 0700 and the file 0600 before SQLite opens it (H1)."""
    cli_data.make_folder(db)
    if cli_data.make_database(db):
        return Store(db, upgrade=False)
    return Store.open_existing(db)


# H1: what a key made on the command line can do, said by the help of key-create and key-list.
KEY_DEFAULT = (
    "A key made without --project or --read-only can read and write every project, export the"
    " workspace, and open a web session."
)
KEY_CREATE_HELP = (
    "Mint an API key of the default workspace into a new 0600 file. "
    + KEY_DEFAULT
    + " --project limits it to the projects it names and --read-only to reading; a key limited"
    " either way exports nothing and opens no web session."
)
KEY_LIST_HELP = (
    "List the default workspace's keys, one line each: its label, what it reaches and how,"
    " what it does not reach, who made it and when, as the Keys page shows them. No key is"
    " printed. " + KEY_DEFAULT
)
# What key-create says, on one line of stderr (exit 1), of a --project the workspace lacks.
NO_SUCH_PROJECT = "no project {name!r} in this workspace: no key was made"


class _NoSuchProject(Exception):
    """A --project of key-create names no project of the workspace; the message is the name."""


def _reach(store: Store, names: list[str] | None) -> list[str] | None:
    """The ids of the projects `names` names, each once; None (every project) without names.
    _NoSuchProject for the first name that is not a project of the workspace."""
    if not names:
        return None
    ids = []
    for name in dict.fromkeys(names):
        project = store.get_project(name)
        if project is None:
            raise _NoSuchProject(name)
        ids.append(project.id)
    return ids


def _key_create(
    db: Path, label: str, out: Path, projects: list[str] | None = None, read_only: bool = False
) -> int:
    """Open the file first (O_EXCL: never overwrite, never follow a link), then mint the key,
    so a refused file leaves no key behind. Prints the path; the key exists only in the file.
    The database's directory is made before that, as opening the store would: the quickstart
    puts the key file beside the database, in a directory a fresh checkout does not have.
    `projects` (names, --project) and `read_only` narrow the key (H1); a name that is no project of
    the workspace is NO_SUCH_PROJECT, exit 1, with no key and no key file made (and no
    database, when there was none: it would hold no project)."""
    cli_data.make_folder(db)
    if projects and not db.is_file():
        print(NO_SUCH_PROJECT.format(name=projects[0]), file=sys.stderr)
        return 1
    try:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"{out} already exists: key-create never overwrites a file", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"cannot create {out}: {error.strerror}", file=sys.stderr)
        return 1
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            os.fchmod(file.fileno(), 0o600)  # exactly 0600, whatever the umask
            store = _open_store(db)
            try:
                reach = _reach(store, projects)
                if reach is None and not read_only:  # the key it has always made
                    key = store.create_key(label)
                else:
                    key = store.create_key(label, reach, "read" if read_only else "write")
            finally:
                store.close()
            file.write(f"CAIRN_API_KEY={key}\n")
    except _NoSuchProject as missing:
        out.unlink(missing_ok=True)
        print(NO_SUCH_PROJECT.format(name=missing.args[0]), file=sys.stderr)
        return 1
    except ValueError as refused:  # the store's own sentence: a reach too long, a label
        out.unlink(missing_ok=True)
        print(refused, file=sys.stderr)
        return 1
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    print(out)
    return 0


def _key_list(db: Path) -> int:
    """key-list: one line per key of the default workspace, oldest first, in the words of the
    Keys page (components.scope_of): "<label>: <mode>: <what>[; <mode>: <what>]...; No
    access: <what it does not reach>; made by <maker> on <when>[; made with <label>]" (A1:
    the key it was made with, or "made with a revoked key"). Never a key or a digest. 1 for
    a missing database."""
    if not db.is_file():
        print(f"no database at {db}", file=sys.stderr)
        return 1
    store = Store.open_existing(db)
    try:
        projects, sets = store.list_projects(), store.list_sets()
        names = {project.id: project.name for project in projects} | {s.id: s.name for s in sets}
        makers = {member.user_id: member.name for member in store.list_members()}
        for info in store.list_keys():
            maker = makers.get(info.maker_id) if info.maker_id else None
            if maker:
                beyond = f"any project {maker} cannot reach"
            elif info.maker_id:
                beyond = "any project its maker cannot reach"
            else:
                beyond = "nothing in this workspace"
            found = scope_of(info.projects, info.access, names, sets, projects, beyond)
            rows = "; ".join(f"{row.mode}: {row.what}" for row in found.rows)
            made_with = made_with_words(info.made_with, info.made_with_revoked)
            print(
                f"{info.label}: {rows}; No access: {found.beyond}; made by"
                f" {maker or 'the workspace'} on {info.created_at}"
                + (f"; {made_with}" if made_with else "")
            )
    finally:
        store.close()
    return 0


# What `session-end --help` says.
SESSION_END_HELP = (
    "End every web session of the person with this address, in every workspace, and print"
    " how many. The list of allowed addresses decides only who signs in, so a sign-in that is"
    " open goes on after its address leaves the list: this ends it, and the person must sign"
    " in again. API keys and agents are not touched: end them on the Keys and Agents pages."
    " Each workspace where a session ended records sessions.ended in its audit log, with the"
    " member's id and the count. It prints no address."
)


def _session_end(db: Path, email: str) -> int:
    """session-end: every web session of the person with `email` ended (Store.end_person_
    sessions); one line with the count, and no address. 1 for a missing database."""
    if not db.is_file():
        print(f"no database at {db}", file=sys.stderr)
        return 1
    store = Store.open_existing(db)
    try:
        ended = sum(store.end_person_sessions(email).values())
    finally:
        store.close()
    print(f"ended {ended} web session{'' if ended == 1 else 's'} of that address")
    return 0


def _key_revoke(db: Path, label: str, with_made: bool = False) -> int:
    """key-revoke: every key labelled `label` revoked, in one line, as before. A1: when the
    keys made anything (Store.made_by), one line more says whether it stays or went with
    them, then one line for each key and each agent; when one of the keys was made before
    recording began, one line says so (components.unrecorded_words). `with_made`: the keys
    and agents they made are revoked with them, in the same transaction
    (Store.revoke_key). 1 for a missing database or no such key."""
    if not db.is_file():
        print(f"no database at {db}", file=sys.stderr)
        return 1
    store = Store.open_existing(db)
    try:
        made = store.made_by(label)
        revoked = store.revoke_key(label, with_made=with_made)
    finally:
        store.close()
    if not revoked:
        print(f"no key is labelled {label!r}", file=sys.stderr)
        return 1
    print(f"revoked every key labelled {label!r}")
    if made:
        if with_made:
            print("and, in the same step, what was made with it:")
        else:
            print("what was made with it stays (--with-made revokes it too):")
        for key in made.keys:
            print(f"  key {key}")
        for agent in made.agents:
            print(f"  agent {agent}")
    if made.unrecorded_before:
        print(unrecorded_words(made.unrecorded_before))
    return 0


class _Unset(Exception):
    """An environment variable sign-in needs is not set; the message is its name."""


def _env(environ: Mapping[str, str], name: str, default: str | None = None) -> str:
    value = environ.get(name, "").strip() or default
    if value is None:
        raise _Unset(name)
    return value


def _provider(name: str, environ: Mapping[str, str]) -> IdentityProvider:
    prefix = f"CAIRN_{name.upper()}_"
    client = (_env(environ, prefix + "CLIENT_ID"), _env(environ, prefix + "CLIENT_SECRET"))
    if name == "github":
        return GitHub(*client)
    if name == "google":
        return Google(*client)
    claim, groups = _groups(environ, prefix + "GROUPS_CLAIM", prefix + "ALLOWED_GROUPS")
    return OIDC(
        _env(environ, prefix + "NAME", "sso"),
        _env(environ, prefix + "LABEL", "Single sign-on"),
        _env(environ, prefix + "ISSUER"),
        *client,
        groups_claim=claim,
        allowed_groups=groups,
    )


def _groups(
    environ: Mapping[str, str], claim_name: str, groups_name: str
) -> tuple[str | None, tuple[str, ...]]:
    """The groups claim and the allowed groups. A variable that is set (even to nothing) but
    says nothing is an error, never a group rule silently turned off; so are allowed groups
    without the claim to read them from. ValueError names the variable, never its value."""
    claim = environ[claim_name].strip() if claim_name in environ else None
    if claim == "":
        raise ValueError(f"{claim_name} is set but blank: name the claim that lists groups")
    groups: tuple[str, ...] = ()
    if groups_name in environ:
        groups = tuple(g.strip() for g in environ[groups_name].split(",") if g.strip())
        if not groups:
            raise ValueError(
                f"{groups_name} is set but names no group: list them separated by commas"
            )
        if claim is None:
            raise ValueError(f"{groups_name} needs {claim_name}, the claim that lists groups")
    return claim, groups


ALLOWED_EMAILS = "CAIRN_ALLOWED_EMAILS"


def _allowed_emails(environ: Mapping[str, str]) -> tuple[str, ...]:
    """The addresses CAIRN_ALLOWED_EMAILS names, separated by commas; () when it is not set.
    Set but blank, an empty item or an entry that is not one address is a ValueError that
    names the variable, never a value."""
    if ALLOWED_EMAILS not in environ:
        return ()
    entries = tuple(entry.strip() for entry in environ[ALLOWED_EMAILS].split(","))
    if not any(entries):
        raise ValueError(
            f"{ALLOWED_EMAILS} is set but names nobody: list addresses, separated by commas"
        )
    try:
        email_entries(entries)
    except ValueError as error:
        raise ValueError(f"{ALLOWED_EMAILS}: {error}; separate addresses with commas") from None
    return entries


def _closed(
    args: argparse.Namespace, providers: tuple[IdentityProvider, ...], emails: tuple[str, ...]
) -> None:
    """An OIDC directory might let anyone register, so the shared workspace behind it needs
    one of four closings: an email domain, an allowed group, a list of addresses
    (CAIRN_ALLOWED_EMAILS), or the operator's word."""
    oidc = next((p for p in providers if isinstance(p, OIDC)), None)
    if args.join != "default" or oidc is None or args.domains or args.closed_directory:
        return
    if not oidc.allowed_groups and not emails:
        raise ValueError(
            "the shared workspace (--join default) is open to anyone who can get an account at"
            " the oidc provider: limit it with --allowed-email-domain, with"
            " CAIRN_OIDC_ALLOWED_GROUPS (and CAIRN_OIDC_GROUPS_CLAIM), with"
            f" {ALLOWED_EMAILS}, or state with --closed-directory that only your own people can"
            " sign in there"
        )


def _limited(
    args: argparse.Namespace, providers: tuple[IdentityProvider, ...], emails: tuple[str, ...]
) -> bool:
    """Whether only approved people sign in (`--test-instance` needs it): a listed address or
    domain, or OIDC alone, closed by an allowed group or the operator's word."""
    if args.domains or emails:
        return True
    return all(
        isinstance(p, OIDC) and bool(p.allowed_groups or args.closed_directory) for p in providers
    )


def _sign_in(args: argparse.Namespace, environ: Mapping[str, str]) -> SignIn | None:
    emails = _allowed_emails(environ)
    if not args.sign_in:
        if emails:
            raise ValueError(
                f"{ALLOWED_EMAILS} is set but nobody signs in through a provider: add --sign-in,"
                " or unset it"
            )
        return None
    providers = tuple(_provider(name, environ) for name in dict.fromkeys(args.sign_in))
    if args.test_instance and not _limited(args, providers, emails):
        raise ValueError(
            "--test-instance needs sign-in limited to the people you approve: list them with"
            f" {ALLOWED_EMAILS} or --allowed-email-domain or, when oidc is the only provider,"
            " limit it with CAIRN_OIDC_ALLOWED_GROUPS or state --closed-directory"
        )
    _closed(args, providers, emails)
    http = httpx.AsyncClient(timeout=PROVIDER_TIMEOUT, follow_redirects=False)
    return SignIn(
        providers=providers,
        http=http,
        join=args.join,
        allowed_email_domains=args.domains,
        allowed_emails=emails,
        require_mfa=args.require_mfa,
    )


@contextlib.contextmanager
def _access_log_on_stderr() -> Iterator[None]:
    """The access log on stderr, one line per call, the line with the install's numbers
    (Q1; cairn.web.stats.STATS_LOGGER), and each audit event as it is stored (AUDIT_LOGGER,
    "cairn.audit: {...}", one JSON object), while the server runs; each logger is put back
    as it was afterwards. None propagates: the MCP SDK configures the root logger, and its
    handler would print each record a second time."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    names = (ACCESS_LOGGER, STATS_LOGGER, AUDIT_LOGGER)  # Q1; the audit trail's copy
    loggers = [logging.getLogger(name) for name in names]
    kept = [(logger.level, logger.propagate) for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    try:
        yield
    finally:
        for logger, (level, propagate) in zip(loggers, kept, strict=True):
            logger.removeHandler(handler)
            logger.propagate = propagate
            logger.setLevel(level)  # through setLevel, which clears logging's level cache


@contextlib.contextmanager
def _audit_lines_on_stderr() -> Iterator[None]:
    """For every command but `serve`: each audit event it stores is one line on stderr,
    "cairn.audit: {...}", as `serve` writes it (_access_log_on_stderr), once the event's write
    has committed, so an operator who keeps the commands' output keeps those lines too.
    stdout, which scripts read, is unchanged. The logger is put back afterwards."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    logger = logging.getLogger(AUDIT_LOGGER)
    level, propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.propagate = propagate
        logger.setLevel(level)  # through setLevel, which clears logging's level cache


@contextlib.contextmanager
def _no_client_request_lines() -> Iterator[None]:
    """httpx logs every request it sends at INFO, the full URL with its query string, and
    httpcore traces every response's headers at DEBUG, where a provider may echo a
    credential; while the server runs, both stay out of its output, for a source, a sign-in
    provider and the model alike. Warnings still come through. The levels are put
    back afterwards."""
    clients = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    levels = [client.level for client in clients]
    for client in clients:
        client.setLevel(max(logging.WARNING, client.level))
    try:
        yield
    finally:
        for client, level in zip(clients, levels, strict=True):
            client.setLevel(level)


def _hours(text: str) -> float:
    try:
        hours = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("a number of hours") from None
    if not 0 <= hours < COMPACT_EVERY_MAX:  # nan and inf fail this too
        raise argparse.ArgumentTypeError(
            f"0 (never), or more hours but fewer than {COMPACT_EVERY_MAX}"
        )
    return hours


def _at_least_one(text: str) -> int:
    try:
        number = int(text)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError("a whole number, 1 or more")
    return number


def _keep_versions(text: str) -> int | str:
    """`serve --keep-versions`: a whole number of at least 1, or "all" (ALL_VERSIONS)."""
    if text == ALL_VERSIONS:
        return ALL_VERSIONS
    if not re.fullmatch(r"[0-9]+", text) or int(text) < 1:
        raise argparse.ArgumentTypeError("a whole number, 1 or more, or all")
    return int(text)


def _audit_keep(text: str) -> int | str:
    """`serve --audit-keep`: a whole number of at least 1, or "all" (ALL_VERSIONS's word)."""
    if text == ALL_VERSIONS:
        return ALL_VERSIONS
    if not re.fullmatch(r"[0-9]+", text) or int(text) < 1:
        raise argparse.ArgumentTypeError("a whole number, 1 or more, or all")
    return int(text)


def _stats_minutes(text: str) -> float:
    """`serve --stats-every`: a number of minutes, 0 (off) or more, never nan or inf."""
    try:
        minutes = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("a number of minutes, 0 or more") from None
    if not (math.isfinite(minutes) and minutes >= 0):
        raise argparse.ArgumentTypeError("a number of minutes, 0 or more")
    return minutes


def _sweep_seconds(text: str) -> float:
    try:
        seconds = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("a number of seconds") from None
    if not (seconds == 0 or 1 <= seconds < math.inf):  # nan fails this too
        raise argparse.ArgumentTypeError("0 (never), or 1 second or more: a sweep takes a write")
    return seconds


def trusted_proxies(values: list[str]) -> list[str]:
    """Each value as uvicorn's proxy-header handling reads it: an IP address, or a network
    with no host bits set. ValueError for anything else, which uvicorn would keep as a name
    that never matches."""
    kept = []
    for value in values:
        text = value.strip()
        try:
            kept.append(
                str(ipaddress.ip_network(text) if "/" in text else ipaddress.ip_address(text))
            )
        except ValueError:
            raise ValueError(
                f"{value!r} is neither an IP address nor a network with no host bits set"
                " (like 172.30.0.0/24)"
            ) from None
    return kept


# H1: what `serve` says, on one line of stderr just before it listens, when it listens where
# others can reach it and nothing says a proxy in front ends TLS. It does not refuse: the
# image's default command does exactly this, behind a proxy that ends TLS.
CLEAR_TEXT = (
    "serve listens on {host}, which is not loopback, over plain http: API keys, tokens and"
    " session cookies travel in clear text on this listener unless a proxy in front of it"
    " ends TLS; when one does, say so with --public-url https://..."
)


def clear_text(host: str, public_url: str | None) -> str | None:
    """CLEAR_TEXT when `host` is not loopback (127.0.0.0/8, ::1, localhost) and `public_url`
    is absent or not https; None otherwise. A name other than localhost is not loopback."""
    if (public_url or "").lower().startswith("https://"):
        return None
    name = host.strip().strip("[]")
    if name.lower() == "localhost":
        return None
    with contextlib.suppress(ValueError):
        if ipaddress.ip_address(name).is_loopback:
            return None
    return CLEAR_TEXT.format(host=host)


# What `serve` says at start, one line each on stderr, without stopping.
NO_TRUSTED_PROXY = (
    "serve answers at an https public URL with no --trusted-proxy: every caller reaches it"
    " from the proxy in front, so all callers share one count in the per-client limits"
    " (sign-ins, agent registrations, refused keys); name that proxy with --trusted-proxy"
)
NO_OPERATOR_NOTES = (
    "serve has no --operator-notes: the privacy guide shows people no operator, no country"
    " where the data is kept and no contact; write them in a file and pass --operator-notes"
)


def no_trusted_proxy(public_url: str | None, proxies: list[str]) -> str | None:
    """NO_TRUSTED_PROXY when `public_url` is https and no proxy is trusted; None otherwise."""
    if (public_url or "").lower().startswith("https://") and not proxies:
        return NO_TRUSTED_PROXY
    return None


def no_operator_notes(sign_in: bool, public_url: str | None, notes: str | None) -> str | None:
    """NO_OPERATOR_NOTES when people sign in or the server has a public URL, and no operator
    notes were given; None otherwise."""
    if (sign_in or public_url) and notes is None:
        return NO_OPERATOR_NOTES
    return None


def _serve(args: argparse.Namespace) -> int:
    if (said := cli_arrange.refused(args)) is not None:  # before anything is opened
        print(said, file=sys.stderr)
        return 2
    if args.plans is not None and args.keep_versions is not None:  # Q1, before anything
        print(KEEP_VERSIONS_WITH_PLANS, file=sys.stderr)
        return 2
    if (said := _would_do_nothing(args)) is not None:
        print(said, file=sys.stderr)
        return 2
    try:  # read before anything is opened; names a variable or an option, never a value
        google = google_connect.serve_switch(args, os.environ)
    except ValueError as error:
        print(google_connect.REFUSED.format(error), file=sys.stderr)
        return 2
    try:  # K1: read before anything is opened; names an option or the variable, never a value
        slack = web_slack.serve_switch(args, os.environ)
    except ValueError as error:
        print(web_slack.REFUSED.format(error), file=sys.stderr)
        return 2
    try:  # the logo is read here, once, before anything is opened
        brand = make_brand(args.brand_name, args.brand_logo, args.brand_logo_alt)
    except ValueError as error:  # names the option, never the file's path
        print(f"cannot use that brand: {error}", file=sys.stderr)
        return 2
    # <PREFIX>_SOURCE_NOTES, _SLACK_NOTES and _UNDECIDED_PROJECTS, with a model only
    send = slack_send = opt_in = False
    if args.model_env is not None:
        try:
            send = cli_arrange.sends_source_notes(args.model_env)
            slack_send = cli_arrange.sends_slack_notes(args.model_env)
            opt_in = cli_arrange.opts_in(args.model_env)
        except ValueError as error:  # names the setting, never its value
            print(f"cannot use that model: {error}", file=sys.stderr)
            return 2
    try:
        proxies = trusted_proxies(args.trusted_proxies)
    except ValueError as error:
        print(f"cannot trust that proxy: {error}", file=sys.stderr)
        return 2
    try:
        sign_in = _sign_in(args, os.environ)
    except _Unset as unset:
        print(f"set {unset} in the environment to sign in with that provider", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"cannot sign in that way: {error}", file=sys.stderr)
        return 2
    try:
        plans = _plans(args.plans)
    except ValueError as error:
        print(f"cannot use those plans: {error}", file=sys.stderr)
        return 2
    try:
        notes = read_operator_notes(args.operator_notes) if args.operator_notes else None
    except ValueError as error:
        print(f"cannot use those operator notes: {error}", file=sys.stderr)
        return 2
    cli_data.make_folder(args.db)  # the lock file goes beside the database
    try:
        lock = take_serve_lock(args.db)
    except OSError as error:
        said = _cannot_open(args.command, CannotOpen(args.db))
        reason = error.strerror or type(error).__name__
        print(said or SERVE_LOCK_REFUSED.format(db=args.db, reason=reason), file=sys.stderr)
        return 1
    if lock is None:
        print(ANOTHER_SERVER.format(db=args.db), file=sys.stderr)
        return 2
    try:
        cli_data.make_database(args.db)  # 0600 before SQLite opens it (H1)
        store = Store(args.db)  # the one open that upgrades: the lock above is held
    except BaseException:
        lock.release()
        raise
    try:
        try:
            arranger = cli_arrange.serve_arranger(
                args,
                store,
                plans,
                send_source_notes=send,
                send_slack_notes=slack_send,
                opt_in=opt_in,
            )
        except ValueError as error:  # a cairn.llm.LLMConfigError names the setting only
            print(f"cannot use that model: {error}", file=sys.stderr)
            return 2
        try:
            app = create_app(
                store,
                gate=lint_gate,
                docs_artifact=args.docs_artifact,
                docs_release=args.docs_release,
                docs_reader_env=args.docs_reader_env,
                allowed_hosts=args.allowed_hosts,
                public_url=args.public_url,
                sign_in=sign_in,
                sync_every=args.sync_every,
                source_http=_source_http,
                compact_every=args.compact_every,
                sweep_every=args.sweep_every or None,
                registrations_per_hour=args.registrations_per_hour,
                plans=plans,
                arranger=arranger,
                operator_notes=notes,
                wall=args.wall,
                test_instance=args.test_instance,
                brand_name=args.brand_name,
                brand_logo=brand.logo if brand else None,
                brand_logo_alt=args.brand_logo_alt,
                session_days=args.session_days,
                idle_minutes=args.idle_minutes,
                file_every=FILE_EVERY,  # N1: the notes waiting for their page are filed
                compiles=args.compiles,  # G1: notes compiled at once
                keep_versions=_kept(args, plans),  # Q1: a page's history bound
                stats_every=args.stats_every or None,  # Q1: the line with the numbers
                google_connect=google,  # a Google account connected in the browser, or off
                slack=slack,  # K1: Slack, both ways, or off
                # G2: serve shows the graph explorer unless --no-graph-explorer turns it off
                graph_explorer=args.graph_explorer
                or (GRAPH_EXPLORER_BY_DEFAULT and not args.no_graph_explorer),
                audit_reads=args.audit_reads,  # each read an event of its workspace
                audit_keep=_audit_kept(args),  # how many events each workspace keeps
            )
        except ValueError as error:
            print(f"cannot sign in that way: {error}", file=sys.stderr)
            return 2
        # B1: a sign-in made under a longer --session-days, or before the option was
        # removed, lasts no longer than the length in force now (renewal only extends).
        store.cap_web_sessions(args.session_days * 86_400 if args.session_days else SESSION_TTL)
        # No access log: uvicorn's prints each request's query string, and the OAuth pages
        # carry a client's `state` and the sealed authorization request there. Proxy headers
        # are read only from --trusted-proxy peers, and the list is always given, so neither
        # uvicorn's default (127.0.0.1) nor FORWARDED_ALLOW_IPS in the environment applies.
        if (says := cli_arrange.serve_says(args, arranger, send, opt_in)) is not None:
            print(says, file=sys.stderr, flush=True)  # what the model is sent, at a glance
        if (says := clear_text(args.host, args.public_url)) is not None:  # H1
            print(says, file=sys.stderr, flush=True)
        if (says := no_trusted_proxy(args.public_url, proxies)) is not None:
            print(says, file=sys.stderr, flush=True)
        if (says := no_operator_notes(sign_in is not None, args.public_url, notes)) is not None:
            print(says, file=sys.stderr, flush=True)
        if (says := _history_at_start(store, plans, _kept(args, plans))) is not None:  # Q1
            print(says, file=sys.stderr, flush=True)
        with _access_log_on_stderr(), _no_client_request_lines():
            uvicorn.run(
                app,
                host=args.host,
                port=args.port,
                access_log=False,
                proxy_headers=bool(proxies),
                forwarded_allow_ips=proxies,
            )
    finally:
        store.close()
        lock.release()
    return 0


def _audit_kept(args: argparse.Namespace) -> int | None:
    """How many events of its audit log each workspace keeps: AUDIT_KEEP unless
    `--audit-keep` says; None for `--audit-keep all` (every event)."""
    if args.audit_keep == ALL_VERSIONS:
        return None
    return AUDIT_KEEP if args.audit_keep is None else args.audit_keep


def _kept(args: argparse.Namespace, plans: Plans | None) -> int | None:
    """How many versions of each page `serve` keeps (Q1): None with plans (each plan says)
    or with `--keep-versions all`; else the option's number, KEEP_VERSIONS without it."""
    if plans is not None or args.keep_versions == ALL_VERSIONS:
        return None
    return KEEP_VERSIONS if args.keep_versions is None else args.keep_versions


def _history_at_start(store: Store, plans: Plans | None, keep: int | None) -> str | None:
    """Record in the database what this `serve` keeps of each page's history, for `compact`
    (Store.record_history_bound), and the line it says when pages hold more versions than
    it keeps; None when none does, with plans, or when it keeps every version. Nothing is
    removed here: a page's older versions go when it is next saved, or at a compaction."""
    if plans is not None:
        bounds = {name: plan.versions_per_page for name, plan in plans.table.items()}
        store.record_history_bound(plans=bounds, default=plans.default)
        return None
    store.record_history_bound(keep)
    over = 0 if keep is None else store.pages_over(keep)
    if not over:
        return None
    pages = "1 page holds" if over == 1 else f"{over} pages hold"
    return (
        f"{pages} more than {keep} versions: each keeps its newest {keep} once it is next"
        f" saved or the database is compacted; --keep-versions all keeps every version"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in (
        "serve", "source-add", "source-sync", "source-preview", "credential-add",
        connector_check.COMMAND, cli_google.COMMAND,
    ):  # fmt: skip
        try:
            private_addresses_allowed(os.environ)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
    if args.command == "serve":
        docs = (args.docs_artifact, args.docs_release, args.docs_reader_env)
        if any(value is not None for value in docs):
            if not all(value is not None for value in docs):
                print(
                    "--docs-artifact, --docs-release and --docs-reader-env require all three",
                    file=sys.stderr,
                )
                return 2
            if not re.fullmatch(r"[0-9a-f]{40}", args.docs_release):
                print(
                    "--docs-release requires a full lowercase 40-character commit", file=sys.stderr
                )
                return 2
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.docs_reader_env):
                print("--docs-reader-env requires an environment variable name", file=sys.stderr)
                return 2
            if args.docs_artifact == Path("."):
                print("--docs-artifact requires a file path", file=sys.stderr)
                return 2
    db = getattr(args, "db", None)  # H1: before any command opens or makes anything
    if (refused := cli_data.linked(db)) is not None:
        print(refused, file=sys.stderr)
        return 2
    if (said := cli_data.readable(db)) is not None:  # the command goes on
        print(said, file=sys.stderr, flush=True)
    try:
        if args.command == "serve":  # serve puts the line on stderr with its access log
            return _run(args)
        with _audit_lines_on_stderr():
            return _run(args)
    except SchemaTooNew as error:  # raised by Store(...) before it reads or writes anything
        print(NEWER_DATABASE.format(refusal=error), file=sys.stderr)
        return 1
    except SchemaTooOld as error:  # raised by Store(..., upgrade=False) before it writes
        found, known = error.found, SCHEMA_VERSION
        older = cli_data.OLDER_SCHEMA
        print(older.format(db=db, found=found, known=known, command=args.command), file=sys.stderr)
        return 1
    except Uninitialized:
        print(
            f"the database at {db} is uninitialized: start serve to initialize it", file=sys.stderr
        )
        return 1
    except ForeignSchema:
        line = NOT_A_DATABASE_YET if args.command in CREATES else NOT_A_DATABASE
        print(line.format(path=db), file=sys.stderr)
        return 1
    except UnknownSchema as error:  # raised by Store(...) before it writes anything
        print(error, file=sys.stderr)
        return 1
    except CannotOpen as error:  # raised by Store(...), or making its folder, before writing
        said = _cannot_open(args.command, error)
        if said is None:  # nothing on disk explains it: no line would be true
            raise
        print(said, file=sys.stderr)
        return 1


def _cannot_open(command: str, error: CannotOpen) -> str | None:
    """The line for a path the store cannot open, chosen by what the disk shows; None when
    nothing there explains SQLite's refusal. os.path's checks read a path they may not see
    as absent instead of raising."""
    path = error.path
    if error.not_a_database:
        return (NOT_A_DATABASE_YET if command in CREATES else NOT_A_DATABASE).format(path=path)
    if os.path.isdir(path):
        return A_FOLDER.format(path=path)
    folder = os.path.dirname(os.path.abspath(path))
    if os.path.lexists(path):
        if os.access(path, os.R_OK | os.W_OK) and os.access(folder, os.W_OK | os.X_OK):
            return None
        return MAY_NOT_OPEN.format(path=path)
    if command not in CREATES:
        return f"no database at {path}"
    while not os.path.isdir(folder):  # the folder the database would be made under
        folder = os.path.dirname(folder)
    if os.access(folder, os.W_OK | os.X_OK):
        return None
    return MAY_NOT_CREATE.format(folder=folder)


def _run(args: argparse.Namespace) -> int:
    if args.command == "key-create":
        return _key_create(args.db, args.label, args.out, args.projects, args.read_only)
    if args.command == "key-list":
        return _key_list(args.db)
    if args.command == "waiting":
        return _waiting(args.db, args.workspace, args.json)
    if args.command == cli_stats.COMMAND:  # Q1: reads only
        return cli_stats.run(args)
    if args.command == cli_audit_verify.COMMAND:  # reads only, as stats
        return cli_audit_verify.run(args)
    if args.command == "key-revoke":
        return _key_revoke(args.db, args.label, args.with_made)
    if args.command == "session-end":
        return _session_end(args.db, args.email)
    if args.command.startswith("source-"):
        return _source_command(args)
    if args.command.startswith("credential-"):
        return _credential_command(args)
    if args.command in cli_data.COMMANDS:
        return cli_data.run(args)
    if args.command in ("plan-set", "usage", "workspace-list"):
        return _plan_command(args)
    if args.command == "arrange":
        return cli_arrange.run(args)
    if args.command == cli_google.COMMAND:
        return cli_google.run(args)
    if args.command == cli_reseal.COMMAND:
        return cli_reseal.run(args)
    if args.command == connector_check.COMMAND:
        return connector_check.run(args)
    return _serve(args)
