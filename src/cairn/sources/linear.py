"""The Linear source: finished issues read from Linear's GraphQL API, a page an issue.

Contract (frozen by tests/acceptance/test_c10_linear.py, and for `backfill_days` by
tests/acceptance/test_k2_backfill.py; the API's shapes are recorded in docs/linear-api.md,
with what Linear's documentation does not state marked as such). This module never writes
to Linear.

The kind ("linear" in cairn.sources.KINDS)
- It starts off: added off, read only once an owner or admin switches it on with a project,
  and then only issues finished at or after the floor: that switch-on (`since`) less
  `backfill_days`, which is 0 unless given, so that switching a source on never pours an
  unknown amount of history into a project unasked. Its notes are written via
  "linear", with the label "linear". It is withheld from a model unless the operator allows
  it, as every kind is (`SourceKind.withheld`).
- The config: `key_env` (required: the name of the environment variable that holds a
  personal API key, capitals, digits and "_"), `projects` (at most one project name; none while
  it is off), `teams` (1 to TEAMS_MAX team keys, each 1 to 10 letters, digits or "_", as
  Linear writes the prefix of an identifier, kept as given) or `linear_project` (one Linear
  project's id, a UUID, kept as given), at most one of the two, `comments` (true),
  `settle_minutes` (30, 0 to 1440), `lookback_days` (30, 1 to 365), `backfill_days` (0, 0 to
  365) and `base_url` (DEFAULT_BASE_URL unless given;
  https, or http on a loopback host; no user, password, query or fragment; a path is kept,
  a trailing "/" dropped). A null `teams` or `linear_project` is left out. No other setting.
  `check` returns the config with the defaults filled in. A refusal names the setting at
  fault and never repeats a value: a value that looks like a credential (a pattern of the
  "secrets" guard), in key_env, a project's name, a team, the Linear project, or base_url as a whole
  or in a host label or path segment, is refused as such; a setting it does not take is
  named only when its name is a short plain word that is not one. Linear states no format
  for its keys (docs/linear-api.md, headline 5), so no prefix of its own is looked for.
- `open` reads the key from `env` (a missing or blank variable is a SourceError naming it)
  and sends it only as `Authorization: <key>`, with no "Bearer" (a personal key takes none),
  and only to `base_url`. The key is the source's `secrets`.

Finding the issues
- Every pull asks one listing, operation FinishedIssues: `issues(first: 25, after: $after,
  includeArchived: true, orderBy: updatedAt, filter: $filter)`, the filter being the
  state's type in ("completed", "canceled") and the `or` of `completedAt >= T` and
  `canceledAt >= T`, with `team: {key: {in: teams}}` or `project: {id: {eq: linear_project}}` beside
  them when one is set. T is OVERLAP before the cursor's mark (the first pull's mark is
  the floor; a mark older than the floor, after the source was switched off and on, counts
  as the floor, which moves with each switch-on). Archived issues are asked for: Linear
  archives finished issues by itself. Every page is followed while `pageInfo.hasNextPage`
  is true, `after` being the page's `endCursor`, at most MAX_LIST_PAGES pages a scan (more:
  a SourceError that says to name teams or a Linear project, or lower backfill_days or
  lookback_days).
- At most once a day by the injected clock (RESCAN_EVERY; recorded in the cursor) a pull
  also asks with T OVERLAP before the later of the floor and `lookback_days` ago, which finds
  an issue the first listing could not see (one the key was given late, a listing that
  showed it late). When that moment is not before the mark (the first pull, say), the first
  listing covers it and the rescan is only recorded.
- Why an hour of overlap: Linear states neither the tie-break of its pages nor how soon a
  finish shows in a listing, and the mark is this server's clock set against Linear's
  times; an hour covers a clock some minutes off and a listing a little late. What shows up
  later than that is the rescan's to find.
- The listing's order across pages is not stated and pages shift while issues change, so a
  pull collects every candidate (an issue listed twice is one) and orders them itself:
  oldest finish first, then by id ignoring case. Its own checks, whatever the listing did:
  the state's type is "completed" (finished at `completedAt`) or "canceled" (at
  `canceledAt`); never "duplicate" (though Linear sets its `canceledAt`) or any other type;
  never a trashed issue (`trashed: true`), whether the listing returns it or not; finished
  at or after the floor; not read already (its digest is in the cursor). An issue finished
  less than `settle_minutes` ago waits, and the mark does not pass it: it is read with what
  it holds once it has settled. An issue reopened before it was read is not finished; it is
  read when it finishes again and settles.
- At most PER_PULL (25) issues a pull; the rest wait, and the mark does not pass them. A
  rest the next first listing cannot reach (found by the rescan, finished before the mark)
  leaves the rescan unrecorded, so the next pull rescans and finds it. A backfill is such a
  rest: read over several pulls, 25 a pull, in finish order, nothing skipped to catch up,
  and an issue finished meanwhile takes its place in that order.

Reading an issue, once
- With `comments`, operation IssueComments: `issue(id: $id) { comments(first: 50, after:
  $after) { ... } }`, every page, at most MAX_COMMENT_PAGES (more: the note says so and
  holds none of them, since pages come newest first and the oldest were not reached). The
  listing never asks for comments (one query may cost at most 10,000 points), and no
  request asks for `email`. The comments are ordered here: roots oldest first (`createdAt`,
  then id), each root's replies after it, oldest first. A reply whose root is not among
  them stands where its time puts it, still marked as a reply.
- Once, for as long as the source exists, in two layers. The kind's own: the cursor keeps a
  digest of each issue it wrote, with the finish it was written for, for `lookback_days`
  after that finish (or while the mark is older), whatever `since` is, so a pull does not
  propose again what it wrote lately, nor read its comments again. The runner's: each
  draft names its item (NoteDraft.item, the issue's `id`, which stays when an issue moves
  teams; the identifier does not), and the runner never writes an item its source wrote
  before (task I1). So an issue reopened and finished again long after, whose digest is
  gone, is proposed and not written. A later edit, comment, reopening, second finish or
  move to another team writes nothing. The digest of a backfilled issue finished more than
  `lookback_days` ago goes as soon as the mark has passed it; the runner's record keeps it
  written once.
- `describe` and the switch-on sentence (SWITCH_ON, or switch_on_with for a config) say
  the backfill when it is not 0, as "what finished in the 30 days before it is switched
  on".
- An issue it fails on while making its note (anything but a SourceError) is skipped once,
  UNREADABLE, and counted as read. Comments over the response bound (MAX_RESPONSE_BYTES)
  leave the note without them, saying so.

The cursor (opaque JSON this module owns, version 1; never an id, an identifier, a title
or a key)
- `mark`: the next first listing asks from OVERLAP before it. After a pull it is
  `settle_minutes` before the pull's clock, or the earliest finish still waiting (settling,
  or past PER_PULL) when that is earlier, and never earlier than it was.
- `rescan`: when the last rescan ran (null before the first).
- `seen`: the issues read, as the first 16 hex digits of the SHA-256 of the lowercased id,
  each with its finish in whole Unix seconds rounded down; one is let go once it is OVERLAP
  older than both `lookback_days` ago and the mark, so the cursor holds about one lookback
  window of issues, however many pass through.
- Each draft carries `resume`: the cursor the pull started from with `seen` as it stood
  right after that draft. At its `deadline` (no request starts after it; a request under way
  is given what is left until it, cairn.sources.timing) a pull returns the issues it
  finished, with the cursor it started from plus their digests; one that finished none
  raises a SourceError.

When Linear says no (a reason names the operation, never a key, a body or an address)
- HTTP 401 or 403, or an error whose `extensions.code` or `extensions.type` says
  authentication or forbidden (either spelling, any case: Linear's docs and its own client
  disagree, docs/linear-api.md B.1), on any status: a SourceError naming `key_env`.
- A rate limit, HTTP 429 or an error RATELIMITED (code or type, any case) on any status,
  400 included (the old form): the pull stops at once, with no request after it and no
  wait (Retry-After means only "not before the next run"). It returns the issues it had
  finished, with a cursor that goes on from there; when it had finished none, nothing, with
  the cursor it was given, so the run does not fail. Either way it says RATE_LIMITED as
  the pull's `stopped` sentence (Pulled.stopped): the run's report keeps it and the Sources
  page shows it. It is no skipped item, and nothing is logged.
- A response with both `data` and `errors` fails that request: nothing of it is written.
- 5xx, a redirect (never followed), a network error (by its class), a timeout, an answer
  past MAX_RESPONSE_BYTES, not JSON, or not the shape the notes describe: a SourceError, so
  the run fails and no cursor moves; the next run writes what this one did not.
- Linear's own `message` is shown only on one line, cut to MESSAGE_MAX characters, and only
  when it holds neither a credential of the source (holds_secret) nor an "@".

What it writes, for each issue, into the config's one project
- `title`: "<identifier>: <title>" as they are when read ("ENG-123: Fix the retry loop"),
  on one line; past FIELD_MAX["title"] (200) characters the issue's title is cut at a word
  with an ellipsis, the identifier never. `source_ref`: the issue's `url`. `label`:
  "linear". No `topic`: each issue is a page of its own, named by the note's title (a page a
  project and month would fill: a page holds 2,000 claims). `item`: the issue's `id`.
- `text`: "# " and the title (cut at a word to HEADING_BYTES UTF-8 bytes, so the heading
  stored above every passage stays well inside its bound), a blank line, the facts as a
  list ("- Team: ", its name; "- Project: " when there is one; "- State: " the workflow
  state's name; "- Finished: " the date, UTC, YYYY-MM-DD; "- Assignee: " and "- Creator: "
  by name when there are any; "- Labels: " by name), then the description as Linear gives
  it (Markdown), unchanged, then with `comments`, when there are any, "## Comments" and each
  comment as a line saying who wrote it and when (a reply says "replied"), a blank line and
  its body as Linear gives it.
- People: `displayName`, else `name`; a name that holds an address, or none, counts as no
  name (cairn.sources.names), so no address is written: an assignee or creator without one
  is left out, a commenter is "Someone whose name is not shown". A bot (`botActor`) is its
  `name` with its service (`subType`), one made through an integration for an outside person
  (`externalUser`) that person's name "from outside the workspace", and a comment with
  nobody recorded "Someone Linear did not record".
- Bounds (decision 91), so the note fits its page: a description is cut past
  DESCRIPTION_CHARS characters or DESCRIPTION_PASSAGES passages, a comment past
  COMMENT_CHARS or COMMENT_PASSAGES (as cairn.spans counts them, headings included), at the
  last passage break within the bound, or at the bound when there is none, with a line
  after it saying how many characters were left out. At most COMMENTS_KEPT comments, and
  none past NOTE_PASSAGES passages in all or a page's bounds (K4: cairn.sources.fit, read
  when the note is made, a description's and a comment's own cut lowered to the room
  left); then a line says how many more there are. A note still past a page's bounds is
  cut at its end with NOTE_CUT (fit.fitted). The
  lines this module writes are plain text: no emphasis, numbers written plainly.
"""

import hashlib
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from cairn.guard import PatternGuard
from cairn.sources import (
    Clock,
    NoteDraft,
    Pulled,
    Skip,
    SourceError,
    SourceKind,
    fit,
    names,
)
from cairn.sources import url as source_url
from cairn.sources.config import check_projects
from cairn.sources.http_policy import read_bounded, request_failure, safe_error_text
from cairn.sources.timing import limit
from cairn.store.memory import FIELD_MAX

DEFAULT_BASE_URL = "https://api.linear.app/graphql"
VIA = LABEL = "linear"
DEFAULT_PROJECT = "linear"  # what /sources proposes as the project a source writes into
LISTING = "FinishedIssues"
COMMENTS = "IssueComments"
FINISHED_TYPES = ("completed", "canceled")
SETTINGS = (
    "key_env",
    "projects",
    "teams",
    "linear_project",
    "comments",
    "settle_minutes",
    "lookback_days",
    "backfill_days",
    "base_url",
)
SETTLE_MINUTES = (30, 0, 1440)  # the default, the least, the most
LOOKBACK_DAYS = (30, 1, 365)
BACKFILL_DAYS = (0, 0, 365)
TEAMS_MAX = 20
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
SETTING_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")  # a name a refusal may repeat
TEAM_KEY = re.compile(r"[A-Za-z0-9_]{1,10}")
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


# The config


def _looks_like_a_credential(value: str) -> bool:
    """The rule every setting is held to: a pattern of the "secrets" guard."""
    return bool(PatternGuard("secrets").check(value))


def _whole(config: Mapping[str, Any], setting: str, bounds: tuple[int, int, int]) -> int:
    default, least, most = bounds
    value = config.get(setting, default)
    if isinstance(value, bool) or not isinstance(value, int) or not least <= value <= most:
        raise ValueError(f"{setting} is a whole number from {least} to {most}")
    return value


def check_config(config: object) -> dict[str, Any]:
    """The config as stored, with the defaults filled in, or ValueError. A refusal names the
    setting at fault and never repeats a value."""
    if not isinstance(config, Mapping):
        raise ValueError("a linear source's config is an object of settings")
    for setting in config:
        if setting in SETTINGS:
            continue
        if (
            isinstance(setting, str)
            and SETTING_NAME.fullmatch(setting)
            and not _looks_like_a_credential(setting)
        ):
            raise ValueError(
                f"a linear source has no setting {setting!r}: it takes {', '.join(SETTINGS)}"
            )
        raise ValueError(
            f"a linear source does not take one of the settings given: it takes"
            f" {', '.join(SETTINGS)}"
        )
    if "key_env" not in config:
        raise ValueError(
            "a linear source needs key_env: the name of the environment variable that holds"
            " the Linear API key"
        )
    key_env = config["key_env"]
    if isinstance(key_env, str) and _looks_like_a_credential(key_env):
        raise ValueError(
            "key_env holds what looks like a key: put the key in an environment variable of"
            " the process that syncs, and name that variable here"
        )
    if not isinstance(key_env, str) or not ENV_NAME.fullmatch(key_env):
        raise ValueError(
            "key_env names an environment variable (capitals, digits and _, at most 128),"
            " which holds the Linear API key: the key itself never goes in a config"
        )
    checked: dict[str, Any] = {
        "key_env": key_env,
        "projects": check_projects(
            config.get("projects", []), kind="linear", looks_like=_looks_like_a_credential
        ),
    }
    checked["base_url"] = _check_base_url(config.get("base_url", DEFAULT_BASE_URL))
    teams, linear_project = config.get("teams"), config.get("linear_project")
    if teams is not None and linear_project is not None:
        raise ValueError(
            "teams and linear_project are both given: a source reads teams or one Linear project"
        )
    if teams is not None:
        checked["teams"] = _check_teams(teams)
    if linear_project is not None:
        if not isinstance(linear_project, str) or not UUID.fullmatch(linear_project):
            raise ValueError(
                "linear_project is one Linear project's id, a UUID, as Linear gives it"
            )
        checked["linear_project"] = linear_project
    comments = config.get("comments", True)
    if not isinstance(comments, bool):
        raise ValueError("comments is true or false")
    checked["comments"] = comments
    checked["settle_minutes"] = _whole(config, "settle_minutes", SETTLE_MINUTES)
    checked["lookback_days"] = _whole(config, "lookback_days", LOOKBACK_DAYS)
    checked["backfill_days"] = _whole(config, "backfill_days", BACKFILL_DAYS)
    return checked


def _check_teams(teams: object) -> list[str]:
    if not isinstance(teams, list | tuple) or not 1 <= len(teams) <= TEAMS_MAX:
        raise ValueError(f"teams is a list of 1 to {TEAMS_MAX} team keys, such as ENG")
    for team in teams:
        if not isinstance(team, str) or not TEAM_KEY.fullmatch(team):
            raise ValueError(
                "each of teams is a team's key: 1 to 10 letters, digits or _, as an issue's"
                " identifier starts (ENG in ENG-123)"
            )
    return list(teams)


def _check_base_url(value: object) -> str:
    return source_url.check_base_url(
        value,
        what="Linear's GraphQL API",
        comes_from="the key comes from the environment",
        named_by="key_env",
        holds="a credential",
        http_said="on this machine",
        looks_like=_looks_like_a_credential,
    )


def describe(config: Mapping[str, Any]) -> str:
    """What a source reads, for GET /sources: which issues, whether their comments, from
    where, and when an issue is read."""
    if config.get("teams"):
        where = f"the teams {', '.join(config['teams'])}"
    elif config.get("linear_project"):
        where = f"the Linear project {config['linear_project']}"
    else:
        where = "every team the key can read"
    extent = "with their comments" if config.get("comments", True) else "without their comments"
    minutes = config.get("settle_minutes", SETTLE_MINUTES[0])
    when = (
        f"{minutes} minute{'s' if minutes != 1 else ''} after it is finished"
        if minutes
        else "as soon as it is listed"
    )
    said = (
        f"Issues completed or cancelled in {where}, {extent}, at {config['base_url']}; each"
        f" is read once, {when}"
    )
    days = config.get("backfill_days", 0)
    return f"{said}, and so is {backfilled(days)}" if days else said


def backfilled(days: int) -> str:
    """What a backfill of `days` reads, in words: "what finished in the 30 days before it is
    switched on" ("in the day before" for one)."""
    span = "the day" if days == 1 else f"the {days} days"
    return f"what finished in {span} before it is switched on"


SWITCH_ON = (
    "Switching it on reads only issues finished from then on: nothing finished before, and"
    " nothing finished while it was off."
)


def switch_on_with(config: Mapping[str, Any]) -> str:
    """What switching on a source with this config reads: SWITCH_ON, or with a backfill the
    issues finished from then on and those finished in the days before."""
    days = config.get("backfill_days", 0)
    if not days:
        return SWITCH_ON
    return (
        f"Switching it on reads issues finished from then on and {backfilled(days)}: nothing"
        " finished earlier."
    )


# The kind's settings as `source-add --help` lists them (SourceKind.settings).
SETTINGS_HELP = (
    '{"key_env": "A_VARIABLE", "projects": [], "teams": ["ENG"],'
    ' "linear_project": "a Linear project\'s id", "comments": true, "settle_minutes": 30,'
    ' "lookback_days": 30, "backfill_days": 0, "base_url": "https://api.linear.app/graphql"};'
    " everything but key_env is optional (teams or linear_project, at most one; without"
    " either, every team the key can read), the"
    " variable holds a personal API key, best read-only and limited to the teams wanted,"
    " each issue is read once when it is completed or cancelled, backfill_days (0 to 365)"
    " also reads what finished that many days before the switch-on, and the source starts"
    " off: switch it on with source-enable --project"
)


def open_source(
    name: str,
    config: Mapping[str, Any],
    *,
    http: httpx.AsyncClient,
    env: Mapping[str, str],
    clock: Clock | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    deadline: datetime | None = None,
    since: datetime | None = None,
) -> "LinearSource":
    """The source a stored config describes, with its key read from `env`. SourceError
    names a variable that is missing or blank, never a value. `sleep` is never called: a
    rate limit ends the pull instead of being waited out."""
    variable = config["key_env"]
    key = (env.get(variable) or "").strip()
    if not key:
        raise SourceError(
            f"set {variable} in the environment of the process that syncs source {name!r}:"
            " it holds the Linear API key"
        )
    return LinearSource(name, config, key, http=http, clock=clock, deadline=deadline, since=since)


KIND = SourceKind(
    check=check_config,
    open=open_source,
    via=VIA,
    starts_off=True,
    describe=describe,
    switch_on=SWITCH_ON,
    default_project=DEFAULT_PROJECT,
    settings=SETTINGS_HELP,
    switch_on_with=switch_on_with,
    withheld=True,
)


# Bounds and wording

PER_PULL = 25  # issues one pull reads and writes
LIST_PAGE = 25  # issues a listing page asks for
COMMENT_PAGE = 50
MAX_LIST_PAGES = 200  # of one scan: 5,000 issues
MAX_COMMENT_PAGES = 40  # of one issue: 2,000 comments
COMMENTS_KEPT = 200  # comments one note holds, at most
NOTE_PASSAGES = 1_500  # spans one note holds, at most: a page holds 2,000 claims
DESCRIPTION_CHARS, DESCRIPTION_PASSAGES = 100_000, 600
COMMENT_CHARS, COMMENT_PASSAGES = 20_000, 100
RESERVE = 1_000  # characters kept for the lines written after a cut (K4)
NOTE_CUT = "The note is cut here: the rest of it is longer than a page holds."
TITLE_CHARS = FIELD_MAX["title"]  # 200
HEADING_BYTES = 160  # of the 400 UTF-8 bytes a passage's headings may take in all
IDENTIFIER = re.compile(r"\S{1,40}")
MESSAGE_MAX = 200  # characters of Linear's own message that a reason may show
OVERLAP = timedelta(hours=1)  # every bound sent is this far before the moment it stands for
RESCAN_EVERY = timedelta(days=1)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024  # of an error's body, read for its codes and message
ERROR_WORD = re.compile(r"[A-Za-z][A-Za-z _-]{0,63}")
AUTH_ERRORS = frozenset({"authenticationerror", "forbidden"})  # letters only, lower case
RATE_ERRORS = frozenset({"ratelimited"})
CURSOR_VERSION = 1
DIGEST_HEX = 16
DIGEST = re.compile(r"[0-9a-f]{16}")
ELLIPSIS = "…"
UNREADABLE = (
    "unreadable: this server failed on what Linear gave for this issue and could not make a"
    " note of it; it is not read again"
)
TOO_MANY_COMMENTS = (
    f"The issue has more than {MAX_COMMENT_PAGES * COMMENT_PAGE} comments, more than this"
    " server reads, so none of them is written."
)
TOO_LARGE_COMMENTS = (
    "The issue's comments are larger than this server reads, so none of them is written."
)
RATE_LIMITED = (
    "Linear asked source {name!r} to slow down (rate limited) when it asked {op}: this pull"
    " stopped there and kept what it had finished, and the next run goes on."
)

LISTING_QUERY = """query FinishedIssues($filter: IssueFilter, $after: String) {
  issues(first: 25, after: $after, includeArchived: true, orderBy: updatedAt, filter: $filter) {
    nodes {
      id identifier title description url completedAt canceledAt trashed
      state { name type }
      team { key name }
      project { id name }
      assignee { name displayName }
      creator { name displayName }
      labels(first: 50) { nodes { name } pageInfo { hasNextPage } }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""

COMMENTS_QUERY = """query IssueComments($id: String!, $after: String) {
  issue(id: $id) {
    comments(first: 50, after: $after) {
      nodes {
        id body createdAt
        user { name displayName }
        botActor { name subType }
        externalUser { name displayName }
        parent { id }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _moment(value: object) -> datetime | None:
    """A timestamp from the API as an aware datetime in UTC; None when it is not one with a
    time zone."""
    if not isinstance(value, str):
        return None
    try:  # a date that is not one, or a moment outside years 1 to 9999 once in UTC
        moment = datetime.fromisoformat(value)
        return moment.astimezone(UTC) if moment.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _stamp(moment: datetime) -> str:
    """A bound for the filter: UTC with milliseconds and "Z", as Linear writes a time."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _seconds(moment: datetime) -> int:
    """`moment` as whole Unix seconds, rounded down: how the cursor keeps a finish."""
    return math.floor(moment.timestamp())


def _one_line(value: object) -> str:
    """Text from Linear on one line: a title, a name or a label never starts a heading."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def _words_within(text: str, fits: Callable[[str], bool]) -> str:
    """`text` whole when it fits, else cut at a word (at a character when no word break is
    left) and closed with an ellipsis, as long as that fits."""
    if fits(text):
        return text
    cut = text
    while cut:
        shown = cut.rstrip(" ,.;:") + ELLIPSIS
        if fits(shown):
            return shown
        cut = cut.rsplit(" ", 1)[0] if " " in cut.strip() else cut[:-1]
    return ELLIPSIS


def note_title(identifier: str, title: str) -> str:
    """ "<identifier>: <title>", the issue's title cut at a word with an ellipsis past
    TITLE_CHARS, the identifier never (IDENTIFIER keeps it short)."""
    if not title:
        return identifier
    head = f"{identifier}: "
    room = TITLE_CHARS - len(head)
    return head + _words_within(title, lambda t: len(t) <= room)


def heading(title: str) -> str:
    """The note's first heading: its title, cut at a word to HEADING_BYTES UTF-8 bytes."""
    return _words_within(title, lambda t: len(t.encode()) <= HEADING_BYTES)


# The cursor


def digest(issue_id: str) -> str:
    """What the cursor keeps of an issue: never its id."""
    return hashlib.sha256(issue_id.lower().encode()).hexdigest()[:DIGEST_HEX]


@dataclass(frozen=True)
class Cursor:
    """What a pull starts from (see the module docstring): the mark, the last rescan and the
    issues read (digest: finish, in Unix seconds)."""

    mark: datetime
    rescan: datetime | None
    seen: dict[str, int] = field(default_factory=dict)

    def encode(self) -> str:
        return json.dumps(
            {
                "v": CURSOR_VERSION,
                "mark": self.mark.astimezone(UTC).isoformat(),
                "rescan": self.rescan.astimezone(UTC).isoformat() if self.rescan else None,
                "seen": {d: self.seen[d] for d in sorted(self.seen)},
            },
            separators=(",", ":"),
        )

    @classmethod
    def decode(cls, text: str) -> "Cursor":
        broken = SourceError(
            "the stored cursor is not one this Linear source wrote: remove the source and add"
            " it again to start afresh"
        )
        try:
            state = json.loads(text)
        except (ValueError, RecursionError):
            raise broken from None
        if not isinstance(state, dict) or state.get("v") != CURSOR_VERSION:
            raise broken
        mark, rescan, seen = _moment(state.get("mark")), _moment(state.get("rescan")), None
        seen = state.get("seen")
        if mark is None or (state.get("rescan") is not None and rescan is None):
            raise broken
        if not isinstance(seen, dict) or not all(
            DIGEST.fullmatch(d) and _count(c) for d, c in seen.items()
        ):
            raise broken
        return cls(mark, rescan, dict(seen))


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# What an answer holds, checked against the shapes in docs/linear-api.md


class _Shape(Exception):
    """An answer not of the documented shape; its message says which part, in this module's
    words (never a value)."""


def _dict(value: object, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _Shape(what)
    return value


def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise _Shape(what)
    return value


def _maybe_text(value: object, what: str) -> str | None:
    return None if value is None else _text(value, what)


def _person(value: object, what: str) -> str | None:
    """A User or an ExternalUser as a name that may be written: `displayName`, else `name`;
    None when neither is one (cairn.sources.names)."""
    if value is None:
        return None
    person = _dict(value, what)
    display = _maybe_text(person.get("displayName"), what)
    return names.shown(display) or names.shown(_maybe_text(person.get("name"), what))


@dataclass(frozen=True)
class Issue:
    """A finished issue as the listing gives it, as far as the connector uses it."""

    id: str
    identifier: str
    title: str  # on one line
    description: str | None
    url: str
    kind: str  # the workflow state's type
    state: str
    finished: datetime | None  # completedAt or canceledAt, by the type
    trashed: bool
    team: str
    project: str | None
    assignee: str | None
    creator: str | None
    labels: list[str]
    more_labels: bool


def parse_issue(node: object) -> Issue:
    """A listed issue, or _Shape when it is not the documented shape."""
    issue = _dict(node, "an issue")
    issue_id = _text(issue.get("id"), "an issue's id")
    identifier = _text(issue.get("identifier"), "an issue's identifier")
    if not UUID.fullmatch(issue_id):
        raise _Shape("an issue's id")
    if not IDENTIFIER.fullmatch(identifier):
        raise _Shape("an issue's identifier")
    url = _text(issue.get("url"), "an issue's url")
    if not url.lower().startswith(("https://", "http://")):
        raise _Shape("an issue's url")
    times: dict[str, datetime | None] = {}
    for name in ("completedAt", "canceledAt"):
        value = issue.get(name)
        times[name] = None if value is None else _moment(value)
        if value is not None and times[name] is None:
            raise _Shape(f"an issue's {name}")
    trashed = issue.get("trashed")
    if trashed is not None and not isinstance(trashed, bool):
        raise _Shape("an issue's trashed")
    state = _dict(issue.get("state"), "an issue's state")
    kind = _text(state.get("type"), "an issue's state")
    team = _dict(issue.get("team"), "an issue's team")
    project = issue.get("project")
    project_name = (
        None if project is None else _text(_dict(project, "a project").get("name"), "a project")
    )
    labels = _dict(issue.get("labels"), "an issue's labels")
    nodes, info = labels.get("nodes"), _dict(labels.get("pageInfo"), "an issue's labels")
    if not isinstance(nodes, list) or not isinstance(info.get("hasNextPage"), bool):
        raise _Shape("an issue's labels")
    label_names = [_one_line(_text(_dict(n, "a label").get("name"), "a label")) for n in nodes]
    return Issue(
        id=issue_id,
        identifier=identifier,
        title=_one_line(_text(issue.get("title"), "an issue's title")),
        description=_maybe_text(issue.get("description"), "an issue's description"),
        url=url,
        kind=kind,
        state=_one_line(_text(state.get("name"), "an issue's state")),
        finished=times["completedAt"] if kind == "completed" else times["canceledAt"],
        trashed=trashed is True,
        team=_one_line(_text(team.get("name"), "an issue's team")),
        project=None if project_name is None else _one_line(project_name),
        assignee=_person(issue.get("assignee"), "an issue's assignee"),
        creator=_person(issue.get("creator"), "an issue's creator"),
        labels=[name for name in label_names if name],
        more_labels=info["hasNextPage"],
    )


@dataclass(frozen=True)
class Comment:
    """A comment as IssueComments gives it: who wrote it (as a note says it), when, what,
    and the root comment it answers."""

    id: str
    body: str
    created: datetime
    author: str
    parent: str | None


def parse_comment(node: object) -> Comment:
    """A comment, or _Shape when it is not the documented shape."""
    comment = _dict(node, "a comment")
    comment_id = _text(comment.get("id"), "a comment's id")
    body = _text(comment.get("body"), "a comment's body")
    created = _moment(comment.get("createdAt"))
    if not comment_id or created is None:
        raise _Shape("a comment's id or time")
    parent = comment.get("parent")
    parent_id = None if parent is None else _text(_dict(parent, "a reply").get("id"), "a reply")
    return Comment(comment_id, body, created, _author(comment), parent_id)


def _author(comment: dict[str, Any]) -> str:
    """Who wrote a comment, as a note says it: a bot by its name and service, an outside
    person marked so, a member by name, and someone when no name may be written."""
    bot, outside, user = comment.get("botActor"), comment.get("externalUser"), comment.get("user")
    if bot is not None:
        actor = _dict(bot, "a comment's bot")
        name = names.shown(_maybe_text(actor.get("name"), "a comment's bot"))
        service = names.shown(_maybe_text(actor.get("subType"), "a comment's bot"))
        who = name or "A bot"
        return f"{who} ({service})" if service else who
    if outside is not None:
        named = _person(outside, "a comment's author")
        return f"{named} from outside the workspace" if named else "Someone outside the workspace"
    if user is not None:
        return _person(user, "a comment's author") or "Someone whose name is not shown"
    return "Someone Linear did not record"


def ordered(comments: list[Comment]) -> list[Comment]:
    """Roots oldest first (createdAt, then id), each root's replies right after it, oldest
    first; a reply whose root is not among them stands where its time puts it."""
    ids = {c.id for c in comments}
    replies: dict[str, list[Comment]] = {}
    roots = []
    for comment in comments:
        if comment.parent is not None and comment.parent in ids:
            replies.setdefault(comment.parent, []).append(comment)
        else:
            roots.append(comment)

    def when(c: Comment) -> tuple[datetime, str]:
        return (c.created, c.id)

    made = []
    for root in sorted(roots, key=when):
        made += [root, *sorted(replies.get(root.id, []), key=when)]
    return made


# What a note says


def spans_of(text: str) -> list[tuple[int, bool]]:
    """Each span of `text` as cairn.spans cuts it, as (the character it ends at, whether it
    is a passage). The text is read after a blank line, as in a note, so a leading "---"
    is never front matter."""
    from cairn.spans import chunk
    from cairn.store.usage import PASSAGE_KINDS

    lines = ("\n" + text).splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    return [
        (starts[span.end_line] - 1, span.kind in PASSAGE_KINDS)
        for span in chunk("cap_000000000000", "\n" + text)
    ]


def cut(text: str, chars: int, spans: int) -> tuple[str, int, int]:
    """`text` within `chars` characters and `spans` spans (headings count): whole, or cut at
    the end of the last passage that fits, or at `chars` when not even the first passage
    fits. Returns the text kept, the characters left out and the spans kept."""
    made = spans_of(text)
    if len(text) <= chars and len(made) <= spans:
        return text, 0, len(made)
    end, kept = 0, 0
    for count, (stop, passage) in enumerate(made, start=1):
        if stop > chars or count > spans:
            break
        if passage:
            end, kept = stop, count
    if end == 0:
        end, kept = min(chars, len(text)), 1
    shown = text[:end].rstrip()
    return shown, len(text) - len(shown), kept


def _cut_here(what: str, characters: int) -> str:
    """The line after a cut: plain words, the number written plainly."""
    return f"{what} is cut here: {characters} more characters of it are not written."


def note_text(issue: Issue, comments: list[Comment] | str | None) -> str:
    """The note's Markdown (see the module docstring). `comments`: the issue's comments in
    order, a sentence saying why none is written, or None when they are not read."""
    facts = [f"- Team: {issue.team}"]
    if issue.project:
        facts.append(f"- Project: {issue.project}")
    facts.append(f"- State: {issue.state}")
    if issue.finished is not None:
        facts.append(f"- Finished: {issue.finished:%Y-%m-%d}")
    if issue.assignee:
        facts.append(f"- Assignee: {issue.assignee}")
    if issue.creator:
        facts.append(f"- Creator: {issue.creator}")
    if issue.labels:
        more = ", and more" if issue.more_labels else ""
        facts.append(f"- Labels: {', '.join(issue.labels)}{more}")
    pieces = [f"# {heading(note_title(issue.identifier, issue.title))}", "\n".join(facts)]
    passages, chars = min(NOTE_PASSAGES, fit.claims()), fit.chars()  # K4: read now
    used = 1 + len(facts)  # spans, as cairn.spans counts them
    size = sum(len(piece) + 2 for piece in pieces)  # characters, the blank lines between included
    description = (issue.description or "").strip("\n")
    if description.strip():
        shown, left, kept = cut(
            description,
            max(1, min(DESCRIPTION_CHARS, chars - size - RESERVE)),
            max(1, min(DESCRIPTION_PASSAGES, passages - used - 3)),
        )
        pieces.append(shown)
        used, size = used + kept, size + len(shown) + 2
        if left:
            pieces.append(_cut_here("The description", left))
            used, size = used + 1, size + len(pieces[-1]) + 2
    if isinstance(comments, str):
        pieces += ["## Comments", comments]
    elif comments:
        pieces.append("## Comments")
        used, size = used + 1, size + len(pieces[-1]) + 2
        written = 0
        for comment in comments[:COMMENTS_KEPT]:
            room = min(COMMENT_PASSAGES, passages - used - 2)  # its line, and a cut's
            verb = "replied" if comment.parent is not None else "wrote"
            line = f"{comment.author} {verb} on {comment.created:%Y-%m-%d %H:%M} UTC:"
            space = min(COMMENT_CHARS, chars - size - len(line) - 2 - RESERVE)
            if room < 1 or space < 1:
                break
            pieces.append(line)
            used, size = used + 1, size + len(line) + 2
            body = comment.body.strip("\n")
            if body.strip():
                shown, left, kept = cut(body, space, room)
                pieces.append(shown)
                used, size = used + kept, size + len(shown) + 2
                if left:
                    pieces.append(_cut_here("This comment", left))
                    used, size = used + 1, size + len(pieces[-1]) + 2
            written += 1
        if written < len(comments):
            pieces.append(
                f"The issue has {len(comments) - written} more comments, not written here."
            )
    return fit.fitted("\n\n".join(pieces) + "\n", NOTE_CUT)


def make_draft(issue: Issue, comments: list[Comment] | str | None, project: str) -> NoteDraft:
    """The issue's one draft: a page of its own (no topic), named by its item, the id."""
    return NoteDraft(
        project=project,
        title=note_title(issue.identifier, issue.title),
        text=note_text(issue, comments),
        source_ref=issue.url,
        label=LABEL,
        item=issue.id,
    )


# Requests


class _Limited(Exception):
    """Linear asked the pull to slow down: it stops at once (see the module docstring)."""


class _Spent(Exception):
    """The pull's deadline came: what is left waits for the next pull."""


def _kinds(errors: list[Any]) -> set[str]:
    """The `extensions.code` and `extensions.type` of each GraphQL error, as letters only in
    lower case ("AUTHENTICATION_ERROR" and "authentication error" are one)."""
    found = set()
    for error in errors:
        extensions = error.get("extensions") if isinstance(error, dict) else None
        if not isinstance(extensions, dict):
            continue
        for name in ("code", "type"):
            value = extensions.get(name)
            if isinstance(value, str) and ERROR_WORD.fullmatch(value):
                found.add(re.sub(r"[^a-z]", "", value.lower()))
    return found


def _codes(errors: list[Any]) -> str:
    """The errors' codes as a reason shows them: " (CODE, ...)", or nothing."""
    shown = []
    for error in errors:
        extensions = error.get("extensions") if isinstance(error, dict) else None
        value = extensions.get("code") if isinstance(extensions, dict) else None
        if isinstance(value, str) and ERROR_WORD.fullmatch(value) and value not in shown:
            shown.append(value)
    return f" ({', '.join(shown[:3])})" if shown else ""


def said_by_linear(errors: list[Any], secrets: tuple[str, ...]) -> str:
    """The first error's own `message` as a reason may show it: ': Linear said "..."', on
    one line, at most MESSAGE_MAX characters; nothing when there is none, or when it holds
    a credential of the source (holds_secret) or an "@" (an address, of any width)."""
    for error in errors:
        message = error.get("message") if isinstance(error, dict) else None
        if not isinstance(message, str):
            continue
        line = _one_line(message)[:MESSAGE_MAX]
        if (
            not line
            or names.holds_an_address(message)
            or not safe_error_text(message, secrets, looks_like=_looks_like_a_credential)
        ):
            return ""
        return f': Linear said "{line}"'
    return ""


def _errors_of(raw: bytes | None) -> list[Any]:
    """The `errors` of a JSON body, or none."""
    if raw is None:
        return []
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        return []
    errors = body.get("errors") if isinstance(body, dict) else None
    return errors if isinstance(errors, list) else []


class LinearSource:
    """Reads the finished issues one Linear API key may read (see the module docstring)."""

    def __init__(
        self,
        name: str,
        config: Mapping[str, Any],
        key: str,
        *,
        http: httpx.AsyncClient,
        clock: Clock | None = None,
        deadline: datetime | None = None,
        since: datetime | None = None,
    ) -> None:
        self.name = name
        self.base_url = config["base_url"]
        self.variable = config["key_env"]
        self.project = config["projects"][0] if config["projects"] else ""
        self.teams = config.get("teams")
        self.linear_project = config.get("linear_project")
        self.comments = config["comments"]
        self.settle = timedelta(minutes=config["settle_minutes"])
        self.lookback = timedelta(days=config["lookback_days"])
        self.backfill = timedelta(days=config.get("backfill_days", 0))
        self._headers = {"Authorization": key, "Content-Type": "application/json"}
        self.secrets = (key,)
        self._http = http
        self._clock = clock or _utc_now
        self.deadline = deadline
        self.since = since

    def __repr__(self) -> str:
        return f"LinearSource({self.base_url!r}, name={self.name!r})"

    def _late(self) -> bool:
        return self.deadline is not None and self._clock() >= self.deadline

    async def pull(self, cursor: str | None) -> Pulled:
        now = self._clock().astimezone(UTC)
        # The floor: the switch-on, less backfill_days. Nothing finished before it is read.
        since = (self.since or now).astimezone(UTC) - self.backfill
        start = Cursor(since, None) if cursor is None else Cursor.decode(cursor)
        mark = max(start.mark, since)
        horizon = max(since, now - self.lookback)  # the oldest finish a rescan reaches
        rescanning = start.rescan is None or now - start.rescan >= RESCAN_EVERY
        try:
            listed = await self._scan(mark)
            if rescanning and horizon < mark:
                listed += await self._scan(horizon)
        except _Limited:
            return Pulled(cursor=cursor, stopped=RATE_LIMITED.format(name=self.name, op=LISTING))
        except _Spent as spent:
            raise SourceError(
                f"Linear's finished issues could not be listed within one pull's deadline"
                f" ({spent}), so the cursor cannot move: the next run tries again"
            ) from None

        found: dict[str, Issue] = {}  # an issue listed twice is one: the later finish
        for issue in listed:
            known = found.get(issue.id.lower())
            if known is None or (issue.finished or now) > (known.finished or now):
                found[issue.id.lower()] = issue
        settled_by = now - self.settle
        todo, waiting = [], []
        for issue in found.values():
            finished = issue.finished
            if issue.kind not in FINISHED_TYPES or issue.trashed or finished is None:
                continue
            if finished < since or digest(issue.id) in start.seen:
                continue
            (waiting if finished > settled_by else todo).append(issue)
        todo.sort(key=lambda issue: (issue.finished, issue.id.lower()))
        todo, rest = todo[:PER_PULL], todo[PER_PULL:]

        seen = dict(start.seen)
        drafts: list[NoteDraft] = []
        skipped: list[Skip] = []
        for issue in todo:
            try:
                comments = await self._comments(issue.id) if self.comments else None
            except (_Limited, _Spent) as stopped:
                said = None
                if isinstance(stopped, _Limited):
                    said = RATE_LIMITED.format(name=self.name, op=COMMENTS)
                    if not drafts and not skipped:
                        return Pulled(cursor=cursor, stopped=said)
                elif not drafts and not skipped:
                    raise SourceError(
                        f"Linear did not answer {COMMENTS} for one issue within one pull's"
                        f" deadline ({stopped}), so the cursor cannot move: the next run"
                        " tries again"
                    ) from None
                return Pulled(drafts, skipped, replace(start, seen=seen).encode(), said)
            seen[digest(issue.id)] = _seconds(issue.finished)  # type: ignore[arg-type]
            try:
                made = make_draft(issue, comments, self.project)
            except Exception:  # one issue it fails on must not stop the source for good
                skipped.append(Skip(issue.id, UNREADABLE))
                continue
            drafts.append(replace(made, resume=replace(start, seen=dict(seen)).encode()))

        held = [issue.finished for issue in [*waiting, *rest]]
        following = max(mark, min([settled_by, *held]))  # type: ignore[type-var]
        unreached = any(issue.finished < following - OVERLAP for issue in rest)  # type: ignore[operator]
        floor = _seconds(min(following, now - self.lookback) - OVERLAP)
        return Pulled(
            drafts=drafts,
            skipped=skipped,
            cursor=Cursor(
                mark=following,
                rescan=now if rescanning and not unreached else start.rescan,
                seen={d: f for d, f in seen.items() if f >= floor},
            ).encode(),
        )

    # Listing

    def _filter(self, bound: datetime) -> dict[str, Any]:
        T = _stamp(bound - OVERLAP)
        made: dict[str, Any] = {
            "state": {"type": {"in": list(FINISHED_TYPES)}},
            "or": [{"completedAt": {"gte": T}}, {"canceledAt": {"gte": T}}],
        }
        if self.teams:
            made["team"] = {"key": {"in": list(self.teams)}}
        if self.linear_project:
            made["project"] = {"id": {"eq": self.linear_project}}
        return made

    async def _scan(self, bound: datetime) -> list[Issue]:
        """Every issue FinishedIssues lists for a finish at or after OVERLAP before `bound`,
        every page."""
        variables: dict[str, Any] = {"filter": self._filter(bound)}
        listed: list[Issue] = []
        for _ in range(MAX_LIST_PAGES):
            data = await self._ask(LISTING, LISTING_QUERY, variables)
            try:
                nodes, after = _connection(data, "issues")
                listed += [parse_issue(node) for node in nodes]
            except _Shape as wrong:
                raise SourceError(_shape(LISTING, wrong)) from None
            if after is None:
                return listed
            variables = {**variables, "after": after}
        raise SourceError(
            f"Linear listed more than {MAX_LIST_PAGES * LIST_PAGE} finished issues in one scan:"
            " name teams or a Linear project, or lower backfill_days or lookback_days"
        )

    async def _comments(self, issue_id: str) -> list[Comment] | str:
        """Every comment of the issue, in order; or the sentence the note says instead."""
        read: list[Comment] = []
        variables: dict[str, Any] = {"id": issue_id}
        for _ in range(MAX_COMMENT_PAGES):
            try:
                data = await self._ask(COMMENTS, COMMENTS_QUERY, variables)
            except _TooLarge:
                return TOO_LARGE_COMMENTS
            try:
                issue = _dict(data.get("issue"), "the issue")
                nodes, after = _connection(issue, "comments")
                read += [parse_comment(node) for node in nodes]
            except _Shape as wrong:
                raise SourceError(_shape(COMMENTS, wrong)) from None
            if after is None:
                return ordered(read)
            variables = {"id": issue_id, "after": after}
        return TOO_MANY_COMMENTS

    # One request

    async def _ask(self, op: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """The `data` of Linear's answer to operation `op`. SourceError for a refusal or a
        failure, _Limited for a rate limit, _Spent at the deadline, _TooLarge for a 200 past
        MAX_RESPONSE_BYTES."""
        if self._late():
            raise _Spent("its deadline")
        payload = json.dumps({"query": query, "variables": variables, "operationName": op})
        raw: bytes | None
        try:
            async with (
                limit(self.deadline, self._clock),
                self._http.stream(
                    "POST",
                    self.base_url,
                    content=payload.encode(),
                    headers=self._headers,
                    follow_redirects=False,
                ) as response,
            ):
                status = response.status_code
                most = MAX_RESPONSE_BYTES if status == 200 else MAX_ERROR_BYTES
                raw = await read_bounded(response, most)
                if status == 200 and raw is None:
                    raise _TooLarge(
                        f"Linear's answer to {op} is larger than this server reads ({most} bytes)"
                    )
        except TimeoutError:
            raise _Spent("its deadline, which an answer still arriving passed") from None
        except httpx.HTTPError as error:
            raise SourceError(request_failure(error, f"cannot reach Linear for {op}")) from None
        errors = _errors_of(raw)
        kinds = _kinds(errors)
        if status in (401, 403) or kinds & AUTH_ERRORS:
            raise SourceError(
                f"Linear refused the API key in {self.variable} when asked {op} (HTTP"
                f" {status}){_codes(errors)}: check that {self.variable} holds a personal API"
                " key with Read access to the teams the source reads"
            )
        if status == 429 or kinds & RATE_ERRORS:
            raise _Limited()
        if status != 200:
            raise SourceError(self._refusal(op, status, errors))
        if raw is None:  # pragma: no cover - a 200 always has a body read
            raise SourceError(f"Linear's answer to {op} is not JSON")
        try:
            body = json.loads(raw)
        except (ValueError, RecursionError):
            raise SourceError(f"Linear's answer to {op} is not JSON") from None
        if not isinstance(body, dict):
            raise SourceError(_shape(op, _Shape("the answer")))
        if errors or body.get("errors") not in (None, []):
            raise SourceError(
                f"Linear answered {op} with an error{_codes(errors)}"
                f"{said_by_linear(errors, self.secrets)}: nothing of that answer is written,"
                " and the next run tries again"
            )
        data = body.get("data")
        if not isinstance(data, dict):
            raise SourceError(_shape(op, _Shape("its data")))
        return data

    def _refusal(self, op: str, status: int, errors: list[Any]) -> str:
        if 300 <= status < 400:
            return (
                f"Linear answered {op} with a redirect (HTTP {status}), which is not followed:"
                " set base_url to the address that answers"
            )
        if status >= 500:
            return (
                f"Linear answered HTTP {status} to {op}, a fault on its side: the next run"
                " tries again"
            )
        return (
            f"Linear refused {op} (HTTP {status}){_codes(errors)}"
            f"{said_by_linear(errors, self.secrets)}"
        )


class _TooLarge(SourceError):
    """A 200 past MAX_RESPONSE_BYTES."""


def _shape(op: str, wrong: _Shape) -> str:
    return f"Linear's answer to {op} is not the documented shape ({wrong})"


def _connection(holder: dict[str, Any], name: str) -> tuple[list[Any], str | None]:
    """A connection's nodes and the cursor of the next page (None: the last page)."""
    connection = _dict(holder.get(name), f"its {name}")
    nodes = connection.get("nodes")
    info = _dict(connection.get("pageInfo"), f"its {name}' pageInfo")
    more, after = info.get("hasNextPage"), info.get("endCursor")
    if not isinstance(nodes, list) or not isinstance(more, bool):
        raise _Shape(f"its {name}")
    if more and (not isinstance(after, str) or not after):
        raise _Shape(f"its {name}' next page")
    return nodes, after if more else None
