"""The GitHub source: the finished work of one repository, an item a note, each once.

Contract (frozen by tests/acceptance/test_c11_github.py, whose docstring is the contract in
full, and for `backfill_days` by tests/acceptance/test_k2_backfill.py; the API's shapes,
version 2022-11-28, are recorded in docs/github-api.md, and where
those notes say [not stated] or CONFLICT this module depends on neither reading). Nothing
is ever written to GitHub. This is not sign-in with GitHub (cairn.identity).

The kind ("github" in cairn.sources.KINDS)
- It starts off: added off, read only once an owner or admin switches it on with a project,
  and then only work finished at or after the floor: that switch-on (`since`) less
  `backfill_days`, which is 0 unless given, so that switching a source on never pours an
  unknown amount of history into a project unasked. Below, `since` means that floor; it
  moves with each switch-on. Its notes are written
  via "github", with the label "github". It is withheld from a model unless the operator
  allows it, as every kind is (`SourceKind.withheld`). The
  project it proposes for a source that names none is DEFAULT_PROJECT, "github", whatever the
  repository: a repository's name can be longer than a project's may be, the other kinds
  propose one project a kind, and the repository is in every note's title.
- The config: `token_env` (required: the environment variable that holds the token),
  `repo` (required: owner/name as GitHub allows each part; no address, no ".git"),
  `projects` (at most one), `items` (a non-empty subset of "pulls", "issues", "releases",
  kept in that order; all three unless given), `comments` (true), `settle_minutes` (30, 0
  to 1440), `lookback_days` (30, 1 to 365), `backfill_days` (0, 0 to 365) and `base_url`
  (DEFAULT_BASE_URL; for GitHub
  Enterprise Server https://HOST/api/v3; https, or http on a loopback host; no user,
  password, query or fragment). No other setting. A refusal names the setting and never
  repeats a value: one shaped like a GitHub token (TOKEN_SHAPE) or matching the "secrets"
  guard is refused as such wherever it is.
- `open` reads the token from `env` (missing or blank: SourceError naming the variable). It
  is sent only as `Authorization: Bearer <token>`, only to base_url; the token and that
  header are the source's `secrets`. Every request carries `Accept: ACCEPT`,
  `X-GitHub-Api-Version: API_VERSION` and USER_AGENT.

What it reads
- A pull request once merged (`pull_request.merged_at` set and at or after `since`); one
  closed without merging, never. An issue once closed as completed, as not planned or with
  no reason (`closed_at` at or after `since`); closed as a duplicate, never. An entry of
  the issues listing with a `pull_request` key is a pull request. A release once published
  (`draft` false, `published_at` at or after `since`); a prerelease is written and says so.
  Review comments on lines of code are never asked for.
- Only once settled: finished at least `settle_minutes` ago by the injected clock.
- Once, for as long as the source exists, in two layers. Each draft names its item
  (`NoteDraft.item`: "pull:<number>", "issue:<number>", "release:<id>") and the runner
  never writes an item its source wrote before (I1). This module also keeps a digest of
  each item it handled (below), for lookback_days and KEPT_PAST_LOOKBACK after: it spares
  a pull fetching again the details of what it wrote lately. Past that, an item closed
  again or edited may be proposed again, and the runner declines it. An item that finished
  at or after `since` is written however late it is found.

How it finds them
- Every pull that reads pulls or issues lists GET /repos/{repo}/issues with state=closed,
  sort=updated, direction=asc, since (OVERLAP before the cursor's mark, whole seconds, "Z")
  and per_page=PAGE, and follows each `Link` rel="next" as given, up to MAX_LIST_PAGES
  pages; a next page not under base_url (its scheme, host, port and path) is a SourceError
  and is never asked. Every pull that reads releases lists GET /repos/{repo}/releases with
  per_page=PAGE, every page up to MAX_LIST_PAGES (the list's order is not stated, so none
  is skipped on a guess; past that, the pull says RELEASES_PAST in its `stopped` sentence).
- The daily check. GitHub does not say that closing or merging moves `updated_at`, so a
  pull also asks GET /search/issues with `repo:OWNER/NAME is:pr is:merged merged:>=T` and
  `repo:OWNER/NAME is:issue is:closed closed:>=T` (as `items` asks), every page up to
  MAX_LIST_PAGES, T with its time and "+00:00". It is due on the first pull, on every pull
  while a check is pending (not refused), and CHECK_EVERY after the last one otherwise.
  T is the pending check's, or CHECK_BACK before the last check whose finds were all
  written (the first check: `since`), less OVERLAP, however long ago that is:
  lookback_days bounds the digests and nothing else. A check whose eligible finds are not
  all handled after the pull (past the pull's PER_PULL, or still settling) stays pending
  with the same T, and while one is pending no digest is let go. Past search's results
  (`total_count` above what its pages gave, or `incomplete_results`) the check takes what
  it was given, is done once that is handled, and the pull skips SEARCH_PAST: the window is
  not split (a skip, not a `stopped` sentence: tests/acceptance/test_c11_github.py reads it
  among the pull's skips). A backfill's first window, from the floor, is as long as the
  backfill and can pass search's 1,000 in a busy repository: the check says so the same
  way, and the issues listing, also from the floor, still reads everything whose update
  time moved. A search refused (403, 422) is not a failure: the pull says
  SEARCH_REFUSED in its `stopped` sentence, writes what the listings found, and the check
  is due again a day later with the same T.
- A pull's candidates are kept by item (one given twice: as last updated), then read in
  this module's order, oldest finish first, then pull requests, issues, releases, then by
  number or id: listings shuffled or repeating an item give the same drafts. At most
  PER_PULL (25) a pull; the rest wait, none lost or reordered. A backfill is such a rest,
  read over several pulls in finish order: the order holds among what one pull's listings
  and check found (the listing reads at most MAX_LIST_PAGES pages a pull).
- `describe` and the switch-on sentence (SWITCH_ON, or switch_on_with for a config) say
  the backfill when it is not 0, as "what finished in the 30 days before it is switched
  on".

Reading one item
- A merged pull request costs one GET .../pulls/{number} (its `merged_by` and base branch:
  only there). With `comments`: GET .../issues/{number}/comments and, for a pull request,
  GET .../pulls/{number}/reviews, every page up to MAX_TALK_PAGES each.
- An item's own address answering 404 or 410 skips the item (GONE), a redirect skips it
  (MOVED): both are recorded as handled, so it is skipped once. A 404 on a listing is a
  failure (below). A shape error is the malformed failure; anything else this module fails
  on while it makes a note skips the item (UNREADABLE). One item never stops the source.

The note (`title`, `item`, `source_ref` = the item's `html_url`, `label` "github"; no topic)
- A page an item: the draft has no topic, so its page is named by its title. (A page a
  kind and month filled at 2,000 claims and lost what came after.) Title: "owner/name#123:
  The title", or "owner/name v1.2.3: Name" ("owner/name v1.2.3" without a name, or a name
  the same as the tag), on one line, cut at a word to TITLE_MAX characters and TITLE_BYTES
  as its heading is stored, with an ELLIPSIS when cut; "owner/name#123" is never cut.
- Text: "# <title>", the facts as a list (a pull request: Opened by, Merged by, Merged,
  Into, Labels; an issue: Opened by, Closed, Closed as "completed" or "not planned", Labels;
  a release: Tag, Published, Published by, and "Prerelease: yes"), the item's `body` as
  GitHub gave it (none when null, empty or absent), then with `comments` a "## Comments"
  section: each comment and each review that carries text (not PENDING), in time order,
  each list keeping its own order on a tie, after a line "<who> wrote on <date>:" or
  "<who> approved / asked for changes / reviewed on <date>:" (VERDICTS). A comment GitHub
  hides (`minimized`) or without text is not written, and not counted as left out.
- People by `login` on one line; a `type` "Bot" account's followed by "(bot)"; null, the
  "ghost" user, or no login a note may hold (cairn.sources.names): DELETED. Never a name or
  an email address.
- Signed addresses: before a body or a comment is written, `unsigned` takes the query off
  every address on a GitHub host (github.com, githubusercontent.com, base_url's host, and
  their subdomains) whose query has a parameter named with jwt, token or X-Amz-Signature;
  the address and its fragment stay, and every other address is left as it is.
- Bounds: the body is cut at BODY_PASSAGES passages as the store counts them or BODY_CHARS
  characters, each comment at COMMENT_PASSAGES or COMMENT_CHARS, the whole note held under
  NOTE_PASSAGES and within a page's bounds (K4: cairn.sources.fit, read when the note is
  made, so a change of the store's bounds moves the cuts; NOTE_CUT ends a note cut at
  the end): between two spans where it can (never inside a fence, a
  list item or a table), else at a line with a fence it opened closed, and a fence left
  open at the end of a text is closed so that what follows is not code. A cut is followed
  by a line saying so (CUT_LINES); comments not written are counted ("2,700 more comments
  are left out.", from the item's own count of its comments) or, with no count, said
  (MORE_COMMENTS, MORE_REVIEWS). Every line this module writes is plain text: no emphasis,
  which the lint gate misreads around a number.

The cursor (opaque JSON this module owns, version 1; never a number, an id, a title or the
token)
- `mark`: the issues listing's next since is OVERLAP before it. After a pull it is
  `settle_minutes` before the pull's clock, or the `updated_at` of a listed item still
  waiting (settling, or past the pull's 25) when earlier, or the last listed `updated_at`
  when the listing had more pages than were read; never earlier than it was, or `since`.
- `releases`: a release published before it counts as read. After a pull: lookback_days
  ago, or the publish time of a release still waiting when earlier; never lower than it
  was, or `since`.
- `checked`, `pending`, `tried`, `refused`: when the last check whose finds were all
  written ran; the T of a check not finished; when a check last ran; whether its search
  was refused.
- `seen`: the items handled, as the first 16 hex digits of the SHA-256 of the lowercased
  repository, a newline and the item; each with when it was handled in Unix seconds, kept
  lookback_days and KEPT_PAST_LOOKBACK, and all of them while a check is pending.
- Each draft's `resume` is the cursor the pull started from with `seen` as it stood right
  after that draft. At the pull's deadline (no request starts after it; one under way is
  given what is left, cairn.sources.timing) a pull returns what it finished with that same
  cursor; one that finished nothing raises SourceError.

When GitHub says no (a message never holds the token or a body; GitHub's own `message` is
added only when shown_message passes it: one line of at most MESSAGE_MAX characters with
none of the source's secrets, as holds_secret looks for them, and nothing shaped like a
credential)
- A rate limit (a 429, or a 403 with `x-ratelimit-remaining: 0` or a `retry-after`): the
  pull stops at once, with no request after it and no wait. What it finished is returned,
  as at the deadline; when it finished nothing it returns no draft and the cursor it was
  given, so the run does not fail and its cursor does not move. Either way the pull says
  RATE_LIMITED, that GitHub asked it to slow down and the next run goes on, in its
  `stopped` sentence (Pulled.stopped), which the run's report keeps and the Sources page
  shows; it is no skipped item. A pull with more than one thing to say (a refused search,
  then a rate limit) says them in that one sentence, in the order they happened: the seam
  carries one.
- 401: names the variable and the repository. 404 on a listing: the same, and that the
  token may lack the permission or still wait for the organisation's approval. 403 that is
  not a limit: names what was asked for and the permission it needs (PERMISSIONS). A
  redirect on a listing, 5xx, another status, a network error (by its class), an answer
  past MAX_RESPONSE_BYTES, not JSON or not the documented shape: a SourceError. A failed
  pull returns nothing, so no cursor moves; the next pull writes the rest, once.
"""

import hashlib
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from cairn import SOFTWARE
from cairn.guard import PatternGuard
from cairn.sources import (
    Clock,
    NoteDraft,
    Pulled,
    Skip,
    SourceError,
    SourceKind,
    fit,
    holds_secret,
    names,
)
from cairn.sources import url as source_url
from cairn.sources.fit import cut, fenced  # noqa: F401 (the cuts this kind makes, by name)
from cairn.sources.timing import limit
from cairn.store.memory import FIELD_MAX, is_project_name
from cairn.store.usage import PASSAGE_KINDS, path_bytes  # noqa: F401 (named in its guide)

DEFAULT_BASE_URL = "https://api.github.com"
VIA = LABEL = "github"
DEFAULT_PROJECT = "github"
ITEMS = ("pulls", "issues", "releases")  # in the order they are kept and said
SETTINGS = (
    "token_env",
    "repo",
    "projects",
    "items",
    "comments",
    "settle_minutes",
    "lookback_days",
    "backfill_days",
    "base_url",
)
SETTLE_MINUTES = (30, 0, 1440)  # the default, the least, the most
LOOKBACK_DAYS = (30, 1, 365)
BACKFILL_DAYS = (0, 0, 365)
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
SETTING_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")  # a name a refusal may repeat
# GitHub's account names: letters, digits and single hyphens, not at either end, at most 39.
OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")
# A GitHub token's prefix (docs/github-api.md B.1) and enough after it to be one.
TOKEN_SHAPE = re.compile(r"(?i)(?<![a-z0-9])(?:gh[pousr]_|github_pat_)[a-z0-9_]{20,}")


# The config


def _looks_like_a_credential(value: str) -> bool:
    """The rule every setting is held to: a GitHub token's shape, or a pattern of the
    "secrets" guard."""
    return bool(TOKEN_SHAPE.search(value)) or bool(PatternGuard("secrets").check(value))


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
        raise ValueError("a github source's config is an object of settings")
    for setting in config:
        if setting in SETTINGS:
            continue
        if (
            isinstance(setting, str)
            and SETTING_NAME.fullmatch(setting)
            and not _looks_like_a_credential(setting)
        ):
            raise ValueError(
                f"a github source has no setting {setting!r}: it takes {', '.join(SETTINGS)}"
            )
        raise ValueError(
            f"a github source does not take one of the settings given: it takes"
            f" {', '.join(SETTINGS)}"
        )
    checked: dict[str, Any] = {
        "token_env": _check_token_env(config),
        "repo": _check_repo(config),
        "projects": _check_projects(config),
        "items": _check_items(config),
    }
    comments = config.get("comments", True)
    if not isinstance(comments, bool):
        raise ValueError("comments is true or false")
    checked["comments"] = comments
    checked["settle_minutes"] = _whole(config, "settle_minutes", SETTLE_MINUTES)
    checked["lookback_days"] = _whole(config, "lookback_days", LOOKBACK_DAYS)
    checked["backfill_days"] = _whole(config, "backfill_days", BACKFILL_DAYS)
    checked["base_url"] = _check_base_url(config.get("base_url", DEFAULT_BASE_URL))
    return checked


def _check_token_env(config: Mapping[str, Any]) -> str:
    if "token_env" not in config:
        raise ValueError(
            "a github source needs token_env: the name of the environment variable that holds"
            " the GitHub token"
        )
    token_env = config["token_env"]
    if isinstance(token_env, str) and _looks_like_a_credential(token_env):
        raise ValueError(
            "token_env holds what looks like a token: put the token in an environment variable"
            " of the process that syncs, and name that variable here"
        )
    if not isinstance(token_env, str) or not ENV_NAME.fullmatch(token_env):
        raise ValueError(
            "token_env names an environment variable (capitals, digits and _, at most 128),"
            " which holds the GitHub token: the token itself never goes in a config"
        )
    return token_env


def _check_repo(config: Mapping[str, Any]) -> str:
    repo = config.get("repo")
    if repo is None:
        raise ValueError("a github source needs repo: owner/name, the one repository it reads")
    if isinstance(repo, str) and _looks_like_a_credential(repo):
        raise ValueError("repo holds what looks like a token: give the repository's owner/name")
    if not isinstance(repo, str) or repo.count("/") != 1:
        raise ValueError(
            "repo is owner/name, as in github.com/owner/name: no address, no .git, nothing more"
        )
    owner, name = repo.split("/")
    if (
        not OWNER.fullmatch(owner)
        or not NAME.fullmatch(name)
        or name in (".", "..")
        or name.lower().endswith(".git")
    ):
        raise ValueError(
            "repo is owner/name, as in github.com/owner/name: no address, no .git, nothing more"
        )
    return repo


def _check_projects(config: Mapping[str, Any]) -> list[str]:
    projects = config.get("projects", [])
    if not isinstance(projects, list | tuple):
        raise ValueError("projects is a list of at most one project's name")
    if len(projects) > 1:
        raise ValueError("projects names at most one project: a github source writes into one")
    for project in projects:
        if isinstance(project, str) and _looks_like_a_credential(project):
            raise ValueError("projects holds a value that looks like a credential")
        if not isinstance(project, str) or not is_project_name(project):
            raise ValueError("projects holds a project's name (not its id)")
    return list(projects)


def _check_items(config: Mapping[str, Any]) -> list[str]:
    items = config.get("items", list(ITEMS))
    if (
        not isinstance(items, list | tuple)
        or not items
        or not all(isinstance(item, str) and item in ITEMS for item in items)
    ):
        raise ValueError('items is a list of one or more of "pulls", "issues" and "releases"')
    return [item for item in ITEMS if item in items]


def _check_base_url(value: object) -> str:
    return source_url.check_base_url(
        value,
        what="GitHub's API",
        comes_from="the token comes from the environment",
        named_by="token_env",
        holds="a token",
        http_said="on this machine",
        looks_like=_looks_like_a_credential,
    )


def describe(config: Mapping[str, Any]) -> str:
    """What a source reads, for GET /sources: which items of which repository, from where,
    with or without their conversations, and when an item is read. Never the variable."""
    finished = {
        "pulls": "pull requests merged",
        "issues": "issues closed",
        "releases": "releases published",
    }
    said = [finished[item] for item in config.get("items", ITEMS)]
    which = said[0] if len(said) == 1 else ", ".join(said[:-1]) + " and " + said[-1]
    extent = ""
    if set(config.get("items", ITEMS)) - {"releases"}:
        talk = config.get("comments", True)
        extent = ", with their conversations" if talk else ", without their conversations"
    minutes = config.get("settle_minutes", SETTLE_MINUTES[0])
    when = (
        f"{minutes} minute{'s' if minutes != 1 else ''} after it is finished"
        if minutes
        else "as soon as it is finished"
    )
    said = (
        f"{which[0].upper()}{which[1:]} in {config['repo']}, at {config['base_url']}{extent};"
        f" each is read once, {when}"
    )
    days = config.get("backfill_days", 0)
    return f"{said}, and so is {backfilled(days)}" if days else said


def backfilled(days: int) -> str:
    """What a backfill of `days` reads, in words: "what finished in the 30 days before it is
    switched on" ("in the day before" for one)."""
    span = "the day" if days == 1 else f"the {days} days"
    return f"what finished in {span} before it is switched on"


SWITCH_ON = (
    "Switching it on reads what is finished from then on, each item once it has settled: a"
    " pull request when it is merged, an issue when it is closed, a release when it is"
    " published. Nothing finished before, and nothing finished while it was off."
)


def switch_on_with(config: Mapping[str, Any]) -> str:
    """What switching on a source with this config reads: SWITCH_ON, or with a backfill what
    is finished from then on and what finished in the days before."""
    days = config.get("backfill_days", 0)
    if not days:
        return SWITCH_ON
    return (
        f"Switching it on reads what is finished from then on and {backfilled(days)}, each"
        " item once it has settled: a pull request when it is merged, an issue when it is"
        " closed, a release when it is published. Nothing finished earlier is read."
    )


# The kind's settings as `source-add --help` lists them (SourceKind.settings).
SETTINGS_HELP = (
    '{"token_env": "A_VARIABLE", "repo": "owner/name", "projects": [], "items": ["pulls",'
    ' "issues", "releases"], "comments": true, "settle_minutes": 30, "lookback_days": 30,'
    ' "backfill_days": 0, "base_url": "https://api.github.com"}; everything but token_env and'
    " repo is optional (these are the defaults; base_url is https://HOST/api/v3 for GitHub"
    " Enterprise Server), the variable holds a fine-grained token limited to that one"
    " repository, read-only for pull requests, issues and contents, each item is read once"
    " when it is finished, backfill_days (0 to 365) also reads what finished that many days"
    " before the switch-on, and the source starts off: switch it on with source-enable"
    " --project"
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
) -> "GitHubSource":
    """The source a stored config describes, with its token read from `env`. SourceError
    names a variable that is missing or blank, never a value. `sleep` is not used: a rate
    limit stops the pull instead of waiting."""
    variable = config["token_env"]
    token = (env.get(variable) or "").strip()
    if not token:
        raise SourceError(
            f"set {variable} in the environment of the process that syncs source {name!r}:"
            " it holds the GitHub token"
        )
    return GitHubSource(name, config, token, http=http, clock=clock, deadline=deadline, since=since)


# Signed addresses

# An address in Markdown or HTML: it ends at white space, a quote, an angle bracket, a
# parenthesis or a square bracket. Punctuation that ends a sentence is taken off after.
ADDRESS = re.compile(r"""https?://[^\s<>"'()\[\]`]+""", re.IGNORECASE)
TRAILING = ".,;:!?"
SIGNED_NAMES = ("jwt", "token", "x-amz-signature")  # a query parameter whose name holds one
GITHUB_DOMAINS = ("github.com", "githubusercontent.com")


def github_hosts(base_url: str) -> tuple[str, ...]:
    """The domains whose addresses `unsigned` looks at: GitHub's own, and base_url's host
    (an Enterprise Server), each with its subdomains."""
    host = (urlsplit(base_url).hostname or "").lower()
    return tuple(dict.fromkeys([*GITHUB_DOMAINS, *([host] if host else [])]))


def _on(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _signed(query: str) -> bool:
    for part in query.split("&"):
        name = unquote(part.split("=", 1)[0]).lower()
        if any(word in name for word in SIGNED_NAMES):
            return True
    return False


def unsigned(text: str, domains: tuple[str, ...]) -> str:
    """`text` with the query taken off every address on one of `domains` whose query has a
    parameter named with jwt, token or X-Amz-Signature (in any case): GitHub signs the
    addresses of a private repository's images and files so, for a few minutes. The address
    is kept, and its fragment; every other address is left as it is."""

    def bare(found: re.Match[str]) -> str:
        address = found.group()
        stripped = address.rstrip(TRAILING)
        tail = address[len(stripped) :]
        try:
            parts = urlsplit(stripped)
            host = (parts.hostname or "").lower()
        except ValueError:
            return address
        if not parts.query or not _on(host, domains) or not _signed(parts.query):
            return address
        kept = stripped.split("?", 1)[0]
        return kept + (f"#{parts.fragment}" if parts.fragment else "") + tail

    return ADDRESS.sub(bare, text)


# Cuts and titles

TITLE_MAX = FIELD_MAX["title"]  # characters
TITLE_BYTES = 200  # as a heading is stored, of the HEADINGS_MAX (400) a passage's may take
ELLIPSIS = "…"


def _one_line(value: object) -> str:
    """Text from GitHub on one line: a title, a name or a tag never starts a heading."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def _too_long(title: str) -> bool:
    return len(title) > TITLE_MAX or path_bytes([title]) > TITLE_BYTES


def title_of(head: str, name: object) -> str:
    """A note's title: `head` ("owner/name#123", or "owner/name v1.2.3" for a release), a
    colon and the item's name on one line. Past a title's bound (TITLE_MAX characters, and
    TITLE_BYTES as its heading is stored) the name is cut at a word, or at a character when
    it is one word, and an ellipsis ends it; `head` is never cut, unless it is too long
    alone (a release's tag can be), and then it is cut at a character after the repository."""
    named = _one_line(name)
    title = f"{head}: {named}" if named else head
    if not _too_long(title):
        return title
    floor = len(head) if not _too_long(head + ELLIPSIS) else head.index(" ") + 1
    text = title[: TITLE_MAX + 1]
    while len(text) > floor and _too_long(text.rstrip(" :,;") + ELLIPSIS):
        space = text.rstrip(" ").rfind(" ")
        text = text[:space] if space > floor + 1 else text[:-1]  # a word left after ": "
    return text.rstrip(" :,;") + ELLIPSIS


# What a note says

NOTE_PASSAGES = 1_500  # of a whole note, as the store counts them, within a page's claims
BODY_PASSAGES = 1_000  # of an item's own text
COMMENT_PASSAGES = 200  # of one comment or review
BODY_CHARS = 250_000
COMMENT_CHARS = 65_536
DELETED = "a deleted account"  # an author GitHub no longer gives, null or its "ghost" user
GHOST = "ghost"
VERDICTS = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "asked for changes",
    "COMMENTED": "reviewed",
    "DISMISSED": "reviewed (a review since dismissed)",
}
CUT_LINES = {
    "pull": "The rest of the description is left out: it is longer than a note holds.",
    "issue": "The rest of the description is left out: it is longer than a note holds.",
    "release": "The rest of the release notes is left out: they are longer than a note holds.",
    "talk": "The rest of this is left out: it is longer than a note holds.",
}
NOTE_CUT = "The rest of this note is left out: it is longer than a page holds."
# Kept for what a note says after a cut: a cut line, "## Comments" and what is left out.
RESERVE, RESERVE_PASSAGES = 1_000, 3
MORE_COMMENTS = "More comments are left out."
MORE_REVIEWS = "More reviews are left out."


def _moment(value: object) -> datetime | None:
    """A timestamp from the API as an aware datetime in UTC; None when it is not one with a
    time zone."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
        return moment.astimezone(UTC) if moment.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _day(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"


def person(user: object) -> str:
    """Who someone is in a note: their login on one line, a bot's followed by "(bot)", and
    DELETED for an account GitHub no longer gives (null, its "ghost" user, or no login a
    note may hold: none, blank, or holding "@" by cairn.sources.names's check). Never a
    name or an email address, which a person may have typed as their name."""
    if not isinstance(user, Mapping):
        return DELETED
    login = names.shown(user.get("login"))
    if login is None or login.lower() == GHOST:
        return DELETED
    return f"{login} (bot)" if user.get("type") == "Bot" else login


def _labels(labels: object) -> list[str]:
    """The labels' names, each on one line: a label is an object with a name, or a string."""
    named = []
    for label in labels if isinstance(labels, list) else []:
        name = label.get("name") if isinstance(label, Mapping) else label
        shown = _one_line(name)
        if shown:
            named.append(shown)
    return named


@dataclass(frozen=True)
class Said:
    """One comment or review, as the note says it: who, when, how, and its text."""

    at: datetime
    order: int  # its place in the list GitHub gave, ties of `at` kept in that order
    header: str
    text: str
    review: bool


def comment_said(entry: object, order: int) -> Said | None:
    """A comment as the note says it; None for one without text, or hidden on GitHub.
    ValueError when it is not the documented shape."""
    if not isinstance(entry, Mapping):
        raise ValueError("a comment is an object")
    at, body = _moment(entry.get("created_at")), entry.get("body")
    if at is None or not (body is None or isinstance(body, str)):
        raise ValueError("a comment has its time and a text or none")
    if not body or not body.strip() or isinstance(entry.get("minimized"), Mapping):
        return None
    return Said(at, order, f"{person(entry.get('user'))} wrote on {_day(at)}:", body, False)


def review_said(entry: object, order: int) -> Said | None:
    """A review that carries a text, as the note says it, its verdict in words; None for one
    without text or not yet submitted. ValueError when it is not the documented shape."""
    if not isinstance(entry, Mapping):
        raise ValueError("a review is an object")
    body, state = entry.get("body"), entry.get("state")
    if not (body is None or isinstance(body, str)) or not isinstance(state, str):
        raise ValueError("a review has a state and a text or none")
    at = _moment(entry.get("submitted_at"))
    if at is None or not body or not body.strip() or state.upper() == "PENDING":
        return None
    verdict = VERDICTS.get(state.upper(), "reviewed")
    return Said(at, order, f"{person(entry.get('user'))} {verdict} on {_day(at)}:", body, True)


def _left(count: int | None, what: str) -> str:
    """The plain line that says how many comments or reviews are left out."""
    if count is None:
        return MORE_COMMENTS if what == "comment" else MORE_REVIEWS
    verb = "is" if count == 1 else "are"
    return f"{count:,} more {what}{'' if count == 1 else 's'} {verb} left out."


def note_text(
    title: str,
    facts: list[str],
    body: object,
    kind: str,
    domains: tuple[str, ...],
    talk: "Talk | None" = None,
) -> str:
    """A note's Markdown: the title as its heading, the facts as a list, the item's own text
    as GitHub gave it (signed addresses unsigned, cut at BODY_PASSAGES with a line saying
    so), then with `talk` a "Comments" section: each comment and review in time order, cut
    at COMMENT_PASSAGES, while the note holds fewer than NOTE_PASSAGES, and a line saying
    how many were left out. Every line the source writes itself is plain text. The whole
    note stays within a page's bounds, read now (cairn.sources.fit): RESERVE characters and
    passages are kept for the lines written after a cut, and a note that would still pass
    them is cut at the end with NOTE_CUT (fit.fitted)."""
    passages, chars = min(NOTE_PASSAGES, fit.claims()), fit.chars()
    parts = [f"# {title}", "\n".join(f"- {line}" for line in facts)]
    used, size = len(facts), sum(len(part) + 2 for part in parts)
    text = unsigned(body, domains) if isinstance(body, str) else ""
    if text.strip():
        kept, count, was_cut = cut(
            text,
            max(1, min(BODY_PASSAGES, passages - used - RESERVE_PASSAGES)),
            max(1, min(BODY_CHARS, chars - size - RESERVE)),
        )
        parts.append(kept)
        used, size = used + count, size + len(kept) + 2
        if was_cut:
            parts.append(CUT_LINES[kind])
            used, size = used + 1, size + len(CUT_LINES[kind]) + 2
    if talk is not None:
        said = sorted(talk.said, key=lambda s: (s.at, s.review, s.order))
        written, shown_comments, shown_reviews = [], 0, 0
        size += len("## Comments") + 2
        for one in said:
            room = passages - used - RESERVE_PASSAGES  # its header, a cut line, what is left out
            space = chars - size - len(one.header) - 2 - RESERVE
            if room < 1 or space < 1:
                break
            kept, count, was_cut = cut(
                unsigned(one.text, domains),
                min(COMMENT_PASSAGES, room),
                min(COMMENT_CHARS, space),
            )
            if not count:
                continue
            written += [one.header, kept, *([CUT_LINES["talk"]] if was_cut else [])]
            used += 1 + count + was_cut
            size += len(one.header) + len(kept) + 4
            size += len(CUT_LINES["talk"]) + 2 if was_cut else 0
            shown_reviews += one.review
            shown_comments += not one.review
        left = []
        with_text = sum(not one.review for one in said)
        total = max(talk.comments_total or 0, talk.comments_read)
        unsaid = total - (talk.comments_read - with_text) - shown_comments
        if talk.comments_more and total <= talk.comments_read:
            left.append(MORE_COMMENTS)  # more pages, and no count to say how many
        elif unsaid > 0:
            left.append(_left(unsaid, "comment"))
        if talk.reviews_more:
            left.append(MORE_REVIEWS)
        elif sum(one.review for one in said) > shown_reviews:
            left.append(_left(sum(one.review for one in said) - shown_reviews, "review"))
        if written or left:
            parts += ["## Comments", *written, *left]
    return fit.fitted("\n\n".join(part for part in parts if part) + "\n", NOTE_CUT)


@dataclass(frozen=True)
class Talk:
    """An item's conversation as read: what can be said, and what is known of the rest."""

    said: list[Said]  # the comments and reviews that carry a text
    comments_read: int  # every comment read, with a text or without
    comments_total: int | None  # the item's own count of its comments, when it gives one
    comments_more: bool = False  # pages of comments past those read
    reviews_more: bool = False  # pages of reviews past those read


# Finding the items

PER_PULL = 25  # items one pull handles; the rest wait for the next
OVERLAP = timedelta(hours=1)  # every bound sent is this far before the moment it stands for
CHECK_EVERY = timedelta(days=1)
CHECK_BACK = timedelta(days=1)  # a check reaches back this far before the last finished one
KEPT_PAST_LOOKBACK = timedelta(days=2)  # a digest is kept lookback_days and this after it
PAGE = 100  # the most a page holds (docs/github-api.md I.1)
MAX_LIST_PAGES = 10  # of the issues listing, of releases, of each search (1,000: its ceiling)
MAX_TALK_PAGES = 3  # of an item's comments, and of a pull request's reviews
RANK = {"pull": 0, "issue": 1, "release": 2}  # the connector's order on a tie of finish time
REASONS = frozenset({"completed", "not_planned", None})  # an issue's close that is written
CLOSED_AS = {"completed": "completed", "not_planned": "not planned"}


@dataclass(frozen=True)
class Found:
    """A finished item as a listing or a search gave it."""

    kind: str  # "pull", "issue" or "release"
    key: int  # a pull request's or an issue's number, a release's id
    finished: datetime  # merged, closed or published
    updated: datetime | None  # `updated_at`, for what the issues listing and search give
    url: str  # `html_url`: the note's source_ref
    entry: Mapping[str, Any]

    @property
    def item(self) -> str:
        return f"{self.kind}:{self.key}"

    def order(self) -> tuple[datetime, int, int]:
        return (self.finished, RANK[self.kind], self.key)


class Malformed(ValueError):
    """An answer that is not the shape docs/github-api.md describes."""


def _whole_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _address(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= FIELD_MAX["source_ref"]
        and value.lower().startswith(("https://", "http://"))
        and not any(c.isspace() for c in value)
    )


def found_issue(entry: object) -> Found | None:
    """An entry of the issues listing or of search as a finished item: a pull request when it
    has `pull_request` and was merged, an issue when closed as completed, as not planned or
    with no reason. None for anything else. Malformed when it is not the documented shape."""
    if not isinstance(entry, Mapping):
        raise Malformed("an issue is an object")
    number, url, state = entry.get("number"), entry.get("html_url"), entry.get("state")
    updated = _moment(entry.get("updated_at"))
    if not _whole_number(number) or not _address(url) or not isinstance(state, str):
        raise Malformed("an issue has a number, an address and a state")
    if updated is None:
        raise Malformed("an issue has its update time")
    if "pull_request" in entry:
        about = entry["pull_request"]
        merged = _moment(about.get("merged_at")) if isinstance(about, Mapping) else None
        return None if merged is None else Found("pull", number, merged, updated, url, entry)
    reason = entry.get("state_reason")
    if not (reason is None or isinstance(reason, str)):
        raise Malformed("an issue's close reason is a string or null")
    if state != "closed" or reason not in REASONS:
        return None
    closed = _moment(entry.get("closed_at"))
    if closed is None:
        raise Malformed("a closed issue has its close time")
    return Found("issue", number, closed, updated, url, entry)


def found_release(entry: object) -> Found | None:
    """A release once published; None for a draft. Malformed when not the documented shape."""
    if not isinstance(entry, Mapping):
        raise Malformed("a release is an object")
    key, url, tag = entry.get("id"), entry.get("html_url"), entry.get("tag_name")
    draft = entry.get("draft")
    if not _whole_number(key) or not _address(url) or not isinstance(tag, str):
        raise Malformed("a release has an id, an address and a tag")
    if not isinstance(draft, bool):
        raise Malformed("a release says whether it is a draft")
    published = _moment(entry.get("published_at"))
    if draft or published is None:
        return None
    return Found("release", key, published, None, url, entry)


def _bound(moment: datetime) -> str:
    """The issues listing's `since`: OVERLAP before `moment`, whole seconds, "Z"."""
    return (moment - OVERLAP).astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _qualifier(moment: datetime) -> str:
    """A search qualifier's moment: a date with its time and "+00:00", as SEARCH-ISSUES
    writes one (a date alone could be read in any time zone)."""
    return moment.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _next_page(link: str | None) -> str | None:
    """The address a `Link` header gives as rel="next", or None."""
    for found in re.finditer(r"<([^>]*)>\s*;\s*rel=\"([^\"]*)\"", link or ""):
        if "next" in found.group(2).split():
            return found.group(1)
    return None


# The cursor

CURSOR_VERSION = 1
DIGEST_HEX = 16
DIGEST = re.compile(r"[0-9a-f]{16}")


def digest(repo: str, item: str) -> str:
    """What the cursor keeps of an item: never its number, id or title."""
    return hashlib.sha256(f"{repo.lower()}\n{item}".encode()).hexdigest()[:DIGEST_HEX]


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat() if moment is not None else None


@dataclass(frozen=True)
class Cursor:
    """What a pull starts from (see the module docstring)."""

    mark: datetime  # the issues listing's next `since` is OVERLAP before it
    releases: datetime  # a release published before it counts as read
    checked: datetime | None = None  # when the last check whose finds were all written ran
    pending: datetime | None = None  # the search bound of a check whose finds are not
    tried: datetime | None = None  # when a check last ran, refused or not
    refused: bool = False  # the last check's search was refused
    seen: dict[str, int] = field(default_factory=dict)  # digest: when written, Unix seconds

    @classmethod
    def first(cls, since: datetime) -> "Cursor":
        return cls(mark=since, releases=since)

    def encode(self) -> str:
        return json.dumps(
            {
                "v": CURSOR_VERSION,
                "mark": _iso(self.mark),
                "releases": _iso(self.releases),
                "checked": _iso(self.checked),
                "pending": _iso(self.pending),
                "tried": _iso(self.tried),
                "refused": self.refused,
                "seen": {d: self.seen[d] for d in sorted(self.seen)},
            },
            separators=(",", ":"),
        )

    @classmethod
    def decode(cls, text: str) -> "Cursor":
        broken = SourceError(
            "the stored cursor is not one this GitHub source wrote: remove the source and add"
            " it again to start afresh"
        )
        try:
            state = json.loads(text)
        except (ValueError, RecursionError):
            raise broken from None
        if not isinstance(state, dict) or state.get("v") != CURSOR_VERSION:
            raise broken
        mark, releases = _moment(state.get("mark")), _moment(state.get("releases"))
        moments = {}
        for name in ("checked", "pending", "tried"):
            moments[name] = _moment(state.get(name))
            if state.get(name) is not None and moments[name] is None:
                raise broken
        seen, refused = state.get("seen"), state.get("refused")
        if mark is None or releases is None or not isinstance(refused, bool):
            raise broken
        if not isinstance(seen, dict) or not all(
            DIGEST.fullmatch(d) and isinstance(s, int) and not isinstance(s, bool)
            for d, s in seen.items()
        ):
            raise broken
        return cls(mark, releases, refused=refused, seen=dict(seen), **moments)


# Requests and what GitHub says


API_VERSION = "2022-11-28"
ACCEPT = "application/vnd.github+json"
USER_AGENT = f"{SOFTWARE}-github-source"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024  # of an error's body, read only for its message
MESSAGE_MAX = 300  # characters of GitHub's own message that a refusal may show
PERMISSIONS = {
    "listing": "Issues and Pull requests",
    "pull": "Pull requests",
    "comments": "Issues and Pull requests",
    "reviews": "Pull requests",
    "releases": "Contents",
    "search": "Issues and Pull requests",
}
GONE = (
    "gone: GitHub no longer gives this item at its own address (deleted, or moved out of"
    " the repository, after it was listed); it is not read again"
)
MOVED = (
    "moved: GitHub answered this item's own address with a redirect (it was moved to another"
    " repository after it was listed), which is not followed; it is not read again"
)
UNREADABLE = (
    "unreadable: this server failed on what GitHub gave for this item and could not make a"
    " note of it; it is not read again"
)
RATE_LIMITED = (
    "GitHub asked this server to slow down (a rate limit): the pull stopped there, and the"
    " next run goes on from where it stopped."
)
SEARCH_REFUSED = (
    "GitHub refused the daily check's search (HTTP {status}): what the issues listing found"
    " was written, and what was finished without a change to its update time waits for the"
    " next check, a day later."
)
SEARCH_PAST = (
    "the daily check found more than GitHub's search returns (it gives at most 1,000 results,"
    " or said its results were incomplete): what it gave was written; the issues listing still"
    " reads everything whose update time moved"
)
RELEASES_PAST = (
    "GitHub listed more releases than this server reads in one pull (1,000): only those on"
    " the pages read were looked at, and the next run reads the same pages."
)


def _sentence(parts: list[str]) -> str | None:
    """The pull's `stopped` sentence: each thing it has to say, in order, or None. The seam
    carries one sentence (Pulled.stopped), so two are said one after the other in it."""
    return " ".join(parts) or None


class _Stopped(Exception):
    """The pull's deadline came, or GitHub asked it to slow down: what is left waits."""

    def __init__(self, limited: bool) -> None:
        self.limited = limited


@dataclass(frozen=True)
class Reply:
    """GitHub's answer: the JSON of a 200 and its next page, or another status and the
    message it carried when that may be shown."""

    status: int
    body: Any = None
    next: str | None = None
    message: str | None = None


def shown_message(message: object, secrets: tuple[str, ...]) -> str | None:
    """GitHub's own `message`, when it may be shown: one line of at most MESSAGE_MAX
    characters that holds none of the source's secrets (as holds_secret looks for them), no
    GitHub token's shape and no pattern of the "secrets" guard. None otherwise."""
    if not isinstance(message, str):
        return None
    message = message.strip()
    if not message or len(message) > MESSAGE_MAX or any(c in message for c in "\r\n"):
        return None
    if holds_secret(message, secrets) or _looks_like_a_credential(message):
        return None
    return message


async def _bounded(response: httpx.Response, most: int) -> bytes | None:
    """The body, or None past `most` bytes."""
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > most:
        return None
    body = bytearray()
    async for part in response.aiter_bytes():
        body += part
        if len(body) > most:
            return None
    return bytes(body)


def _limited(status: int, headers: httpx.Headers) -> bool:
    """A rate limit, primary or secondary (docs/github-api.md J.4): a 429, or a 403 with
    `x-ratelimit-remaining: 0` or a `retry-after` header. A 403 without either is a refusal."""
    if status == 429:
        return True
    return status == 403 and (
        headers.get("x-ratelimit-remaining", "").strip() == "0" or "retry-after" in headers
    )


class GitHubSource:
    """Reads the finished work of one repository (see the module docstring)."""

    def __init__(
        self,
        name: str,
        config: Mapping[str, Any],
        token: str,
        *,
        http: httpx.AsyncClient,
        clock: Clock | None = None,
        deadline: datetime | None = None,
        since: datetime | None = None,
    ) -> None:
        self.name = name
        self.repo = config["repo"]
        self.base_url = config["base_url"]
        self.variable = config["token_env"]
        self.project = config["projects"][0] if config["projects"] else ""
        self.items = tuple(config["items"])
        self.comments = config["comments"]
        self.settle = timedelta(minutes=config["settle_minutes"])
        self.lookback = timedelta(days=config["lookback_days"])
        self.backfill = timedelta(days=config.get("backfill_days", 0))
        self.domains = github_hosts(self.base_url)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": ACCEPT,
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        self.secrets = (token, f"Bearer {token}")
        self._http = http
        self._clock = clock or _utc_now
        self.deadline = deadline
        self.since = since

    def __repr__(self) -> str:
        return f"GitHubSource({self.repo!r}, name={self.name!r})"

    def _late(self) -> bool:
        return self.deadline is not None and self._clock() >= self.deadline

    # The pull

    async def pull(self, cursor: str | None) -> Pulled:
        now = self._clock().astimezone(UTC)
        # The floor: the switch-on, less backfill_days. Nothing finished before it is read.
        since = (self.since or now).astimezone(UTC) - self.backfill
        start = Cursor.first(since) if cursor is None else Cursor.decode(cursor)
        start = replace(start, mark=max(start.mark, since), releases=max(start.releases, since))
        reading = "pulls" in self.items or "issues" in self.items
        due = reading and (
            start.tried is None
            or (start.pending is not None and not start.refused)
            or now - start.tried >= CHECK_EVERY
        )
        skipped: list[Skip] = []
        said: list[str] = []  # the pull's `stopped` sentence, a part for each thing to say
        found: dict[str, Found] = {}
        listed: set[str] = set()  # the items the issues listing gave
        last_listed: datetime | None = None  # the listing's last update time, when cut short
        finds: set[str] = set()
        bound = refused = None
        section = "listing"
        try:
            if reading:
                entries, whole = await self._pages(
                    "/issues",
                    {
                        "state": "closed",
                        "sort": "updated",
                        "direction": "asc",
                        "since": _bound(start.mark),
                        "per_page": str(PAGE),
                    },
                    "listing",
                )
                for entry in entries:
                    one = self._wanted(found_issue(entry), since)
                    if one is not None:
                        self._keep(found, one)
                        listed.add(one.item)
                if not whole and entries:
                    last_listed = _moment(entries[-1].get("updated_at"))
            if due:
                section = "search"
                bound = (
                    start.pending
                    or (max(start.checked - CHECK_BACK, since) if start.checked else since)
                    - OVERLAP
                )
                refused = await self._check(bound, since, found, finds, skipped)
                if refused is not None:
                    said.append(SEARCH_REFUSED.format(status=refused))
            if "releases" in self.items:
                section = "releases"
                entries, whole = await self._pages("/releases", {"per_page": str(PAGE)}, "releases")
                if not whole:
                    said.append(RELEASES_PAST)
                for entry in entries:
                    one = self._wanted(found_release(entry), since)
                    if one is not None and one.finished >= start.releases:
                        self._keep(found, one)
        except _Stopped as stop:
            if stop.limited:
                return Pulled([], [], cursor, _sentence([*said, RATE_LIMITED]))
            raise SourceError(
                f"GitHub's lists of {self.repo} could not be read within one pull's bounds (its"
                " deadline), so the cursor cannot move: the next run tries again"
            ) from None
        except Malformed:
            raise SourceError(self._malformed(section)) from None

        settled_by = now - self.settle
        waiting = [f for f in found.values() if digest(self.repo, f.item) not in start.seen]
        todo = sorted((f for f in waiting if f.finished <= settled_by), key=Found.order)
        seen = dict(start.seen)
        drafts: list[NoteDraft] = []
        handled: set[str] = set()
        for one in todo:
            if len(handled) == PER_PULL:
                break
            try:
                made = await self._read(one)
            except _Stopped as stop:
                if not handled:
                    if stop.limited:
                        return Pulled([], [], cursor, _sentence([*said, RATE_LIMITED]))
                    raise SourceError(
                        f"GitHub did not answer for one item of {self.repo} within one pull's"
                        " bounds (its deadline), so the cursor cannot move: the next run tries"
                        " again"
                    ) from None
                if stop.limited:
                    said.append(RATE_LIMITED)
                return Pulled(drafts, skipped, replace(start, seen=seen).encode(), _sentence(said))
            seen[digest(self.repo, one.item)] = math.floor(now.timestamp())
            handled.add(one.item)
            if isinstance(made, Skip):
                skipped.append(made)
            else:
                drafts.append(replace(made, resume=replace(start, seen=dict(seen)).encode()))

        left = [f for f in waiting if f.item not in handled]
        mark = min([settled_by, *(f.updated for f in left if f.item in listed and f.updated)])
        if last_listed is not None:
            mark = min(mark, last_listed)
        floor = min([now - self.lookback, *(f.finished for f in left if f.kind == "release")])
        following = replace(
            start, mark=max(mark, start.mark), releases=max(floor, start.releases), seen=seen
        )
        if due:
            following = replace(following, tried=now, refused=refused is not None)
            if refused is None:
                done = all(digest(self.repo, item) in seen for item in finds)
                following = replace(
                    following,
                    checked=now if done else start.checked,
                    pending=None if done else bound,
                )
        if following.pending is None:  # a check not finished keeps every digest it may need
            following = replace(following, seen=self._pruned(seen, now))
        return Pulled(drafts, skipped, following.encode(), _sentence(said))

    def _wanted(self, one: Found | None, since: datetime) -> Found | None:
        """`one` when it is of the kinds the config reads and finished at or after `since`."""
        kinds = {"pull": "pulls", "issue": "issues", "release": "releases"}
        if one is None or kinds[one.kind] not in self.items or one.finished < since:
            return None
        return one

    @staticmethod
    def _keep(found: dict[str, Found], one: Found) -> None:
        """`one` among what a pull found: an item given twice is kept as last updated."""
        known = found.get(one.item)
        if known is None or (one.updated or one.finished) >= (known.updated or known.finished):
            found[one.item] = one

    def _pruned(self, seen: dict[str, int], now: datetime) -> dict[str, int]:
        """The digests written at most lookback_days (and KEPT_PAST_LOOKBACK) ago."""
        least = (now - self.lookback - KEPT_PAST_LOOKBACK).timestamp()
        return {d: at for d, at in seen.items() if at >= least}

    async def _check(
        self,
        bound: datetime,
        since: datetime,
        found: dict[str, Found],
        finds: set[str],
        skipped: list[Skip],
    ) -> int | None:
        """The daily check: search for what was merged and closed at or after `bound`. Its
        finds go into `found` and `finds`; the HTTP status when search was refused (the
        caller says so in the pull's `stopped` sentence), else None."""
        queries = []
        if "pulls" in self.items:
            queries.append(f"repo:{self.repo} is:pr is:merged merged:>={_qualifier(bound)}")
        if "issues" in self.items:
            queries.append(f"repo:{self.repo} is:issue is:closed closed:>={_qualifier(bound)}")
        past = False
        for query in queries:
            given = await self._search(query)
            if isinstance(given, int):
                return given
            entries, whole = given
            past = past or not whole
            for entry in entries:
                one = self._wanted(found_issue(entry), since)
                if one is not None:
                    self._keep(found, one)
                    finds.add(one.item)
        if past:
            skipped.append(Skip(self.repo, SEARCH_PAST))
        return None

    # Reading one item

    async def _read(self, one: Found) -> NoteDraft | Skip:
        """The item's draft, or why it has none (GONE, MOVED, UNREADABLE)."""
        number = one.key
        detail: Mapping[str, Any] = {}
        talk = None
        if one.kind == "pull":
            reply = await self._get(f"/pulls/{number}", "pull", number, item=True)
            if reply.status != 200:
                return Skip(one.url, GONE if reply.status in (404, 410) else MOVED)
            if (
                not isinstance(reply.body, Mapping)
                or reply.body.get("number") != number
                or not isinstance(reply.body.get("base"), Mapping)
                or not (
                    reply.body.get("merged_by") is None
                    or isinstance(reply.body.get("merged_by"), Mapping)
                )
            ):
                raise SourceError(self._malformed("pull", number))
            detail = reply.body
        if self.comments and one.kind != "release":
            talk = await self._talk(one)
            if isinstance(talk, Skip):
                return talk
        try:
            return self._draft(one, detail, talk)
        except Exception:  # one item this source fails on must not stop it for good
            return Skip(one.url, UNREADABLE)

    async def _talk(self, one: Found) -> "Talk | Skip":
        """An item's comments and, for a pull request, its reviews, every page up to
        MAX_TALK_PAGES each."""
        said: list[Said] = []
        number = one.key
        given = await self._pages(
            f"/issues/{number}/comments",
            {"per_page": str(PAGE)},
            "comments",
            number,
            most=MAX_TALK_PAGES,
            item=True,
        )
        if isinstance(given, int):
            return Skip(one.url, GONE if given in (404, 410) else MOVED)
        comments, whole = given
        try:
            for order, entry in enumerate(comments):
                if (made := comment_said(entry, order)) is not None:
                    said.append(made)
        except ValueError:
            raise SourceError(self._malformed("comments", number)) from None
        reviews_more = False
        if one.kind == "pull":
            given = await self._pages(
                f"/pulls/{number}/reviews",
                {"per_page": str(PAGE)},
                "reviews",
                number,
                most=MAX_TALK_PAGES,
                item=True,
            )
            if isinstance(given, int):
                return Skip(one.url, GONE if given in (404, 410) else MOVED)
            reviews, all_reviews = given
            reviews_more = not all_reviews
            try:
                for order, entry in enumerate(reviews):
                    if (made := review_said(entry, order)) is not None:
                        said.append(made)
            except ValueError:
                raise SourceError(self._malformed("reviews", number)) from None
        total = one.entry.get("comments")
        return Talk(
            said=said,
            comments_read=len(comments),
            comments_total=total
            if isinstance(total, int) and not isinstance(total, bool)
            else None,
            comments_more=not whole,
            reviews_more=reviews_more,
        )

    def _draft(self, one: Found, detail: Mapping[str, Any], talk: "Talk | None") -> NoteDraft:
        entry = one.entry
        if one.kind == "release":
            tag = _one_line(entry["tag_name"])
            name = _one_line(entry.get("name"))
            title = title_of(f"{self.repo} {tag}", None if name == tag else name)
            facts = [f"Tag: {tag}", f"Published: {_day(one.finished)}"]
            facts.append(f"Published by: {person(entry.get('author'))}")
            if entry.get("prerelease") is True:
                facts.append("Prerelease: yes")
        else:
            title = title_of(f"{self.repo}#{one.key}", entry.get("title"))
            facts = [f"Opened by: {person(entry.get('user'))}"]
            if one.kind == "pull":
                facts.append(f"Merged by: {person(detail.get('merged_by'))}")
                facts.append(f"Merged: {_day(one.finished)}")
                into = _one_line(detail["base"].get("ref"))
                if into:
                    facts.append(f"Into: {into}")
            else:
                facts.append(f"Closed: {_day(one.finished)}")
                reason = entry.get("state_reason")
                if reason in CLOSED_AS:
                    facts.append(f"Closed as: {CLOSED_AS[reason]}")
            labels = _labels(entry.get("labels"))
            if labels:
                facts.append(f"Labels: {', '.join(labels)}")
        text = note_text(title, facts, entry.get("body"), one.kind, self.domains, talk)
        return NoteDraft(
            project=self.project,
            title=title,
            text=text,
            source_ref=one.url,
            label=LABEL,
            item=one.item,
        )

    # Requests

    def _what(self, endpoint: str, number: int | None = None) -> str:
        """What a request asked for, in words: what a refusal names."""
        return {
            "listing": f"the list of closed issues and pull requests of {self.repo}",
            "search": f"the daily check's search of {self.repo}",
            "releases": f"the list of releases of {self.repo}",
            "pull": f"pull request #{number} of {self.repo}",
            "comments": f"the comments of #{number} of {self.repo}",
            "reviews": f"the reviews of pull request #{number} of {self.repo}",
        }[endpoint]

    def _malformed(self, endpoint: str, number: int | None = None) -> str:
        return (
            f"GitHub's answer to {self._what(endpoint, number)} is malformed: not the shape"
            " its documentation gives (API version 2022-11-28)"
        )

    async def _search(self, query: str) -> tuple[list[Any], bool] | int:
        """A search's items, every page, and whether they are all it found; or the status
        when search was refused (403, 422)."""
        url, params = self.base_url + "/search/issues", {"q": query, "per_page": str(PAGE)}
        items: list[Any] = []
        whole = True
        for _ in range(MAX_LIST_PAGES):
            reply = await self._get(url, "search", absolute=True, params=params)
            if reply.status in (403, 422):
                return reply.status
            if reply.status != 200:
                raise SourceError(self._refusal(reply, "search"))
            body = reply.body
            if (
                not isinstance(body, Mapping)
                or not isinstance(body.get("items"), list)
                or not isinstance(body.get("total_count"), int)
                or isinstance(body.get("total_count"), bool)
                or not isinstance(body.get("incomplete_results"), bool)
            ):
                raise SourceError(self._malformed("search"))
            items += body["items"]
            whole = whole and not body["incomplete_results"]
            total = body["total_count"]
            if reply.next is None:
                return items, whole and total <= len(items)
            url, params = self._following(reply.next, "search"), None
        return items, False

    async def _pages(
        self,
        path: str,
        params: dict[str, str],
        endpoint: str,
        number: int | None = None,
        *,
        most: int = MAX_LIST_PAGES,
        item: bool = False,
    ) -> tuple[list[Any], bool] | int:
        """Every entry of a list, page by page as its `Link` header gives them, up to `most`
        pages, and whether that was all; or, with `item`, the status of an item's own address
        that is gone or moved (404, 410, 3xx)."""
        url: str = self.base_url + f"/repos/{self.repo}" + path
        asked: dict[str, str] | None = params
        rows: list[Any] = []
        for _ in range(most):
            reply = await self._get(url, endpoint, number, absolute=True, params=asked, item=item)
            if reply.status != 200:
                return reply.status
            if not isinstance(reply.body, list):
                raise SourceError(self._malformed(endpoint, number))
            rows += reply.body
            if reply.next is None:
                return rows, True
            url, asked = self._following(reply.next, endpoint, number), None
        return rows, False

    def _following(self, address: str, endpoint: str, number: int | None = None) -> str:
        """A next page's address, when it is under base_url (its scheme, host, port and
        path); SourceError otherwise: the token goes nowhere else."""
        try:
            given, base = urlsplit(address), urlsplit(self.base_url)
            same = (given.scheme.lower(), given.hostname, given.port) == (
                base.scheme.lower(),
                base.hostname,
                base.port,
            )
        except ValueError:
            same = False
        if not same or not given.path.startswith(base.path.rstrip("/") + "/"):
            raise SourceError(
                f"GitHub gave a next page of {self._what(endpoint, number)} that is not under"
                " base_url: it is not asked for, since the token goes only to base_url"
            )
        return address

    async def _get(
        self,
        path: str,
        endpoint: str,
        number: int | None = None,
        *,
        absolute: bool = False,
        params: dict[str, str] | None = None,
        item: bool = False,
    ) -> Reply:
        """GitHub's answer to one GET: a Reply for a 200 (its JSON) and, with `item`, for an
        item's own address that is gone or moved (404, 410, 3xx). Every other answer is a
        SourceError; _Stopped at the deadline or a rate limit."""
        url = path if absolute else self.base_url + f"/repos/{self.repo}" + path
        if self._late():
            raise _Stopped(limited=False)
        try:
            async with (
                limit(self.deadline, self._clock),
                self._http.stream(
                    "GET", url, params=params, headers=self._headers, follow_redirects=False
                ) as response,
            ):
                status = response.status_code
                if status == 200:
                    body = await _bounded(response, MAX_RESPONSE_BYTES)
                    if body is None:
                        raise SourceError(
                            f"GitHub's answer to {self._what(endpoint, number)} is larger than"
                            f" this server reads ({MAX_RESPONSE_BYTES // (1024 * 1024)} MB)"
                        )
                    try:
                        parsed = json.loads(body)
                    except (ValueError, RecursionError):
                        raise SourceError(
                            f"GitHub's answer to {self._what(endpoint, number)} is not JSON"
                        ) from None
                    return Reply(200, parsed, _next_page(response.headers.get("link")))
                if _limited(status, response.headers):
                    raise _Stopped(limited=True)
                error = await _bounded(response, MAX_ERROR_BYTES)
        except TimeoutError:
            raise _Stopped(limited=False) from None
        except httpx.HTTPError as failed:
            raise SourceError(
                f"cannot reach GitHub for {self._what(endpoint, number)}: {type(failed).__name__}"
            ) from None
        message = None
        try:
            parsed = json.loads(error or b"")
            message = shown_message(parsed.get("message"), self.secrets)
        except (ValueError, RecursionError, AttributeError):
            pass
        reply = Reply(status, message=message)
        if item and (status in (404, 410) or 300 <= status < 400):
            return reply
        if endpoint == "search" and status in (403, 422):
            return reply
        raise SourceError(self._refusal(reply, endpoint, number))

    def _refusal(self, reply: Reply, endpoint: str, number: int | None = None) -> str:
        """A SourceError's message for an answer that is not 200: what happened and what to
        do, never the token, and GitHub's own message only when shown_message passes it."""
        status, what = reply.status, self._what(endpoint, number)
        permission = PERMISSIONS[endpoint]
        if status == 401:
            said = (
                f"GitHub refused the token in {self.variable} (HTTP 401) when asked for {what}:"
                f" it is wrong, expired or revoked; put a valid token for {self.repo} in"
                f" {self.variable}"
            )
        elif status == 404 and endpoint in ("listing", "releases"):
            said = (
                f"GitHub answered 404 to {what}: the repository does not exist, or the token in"
                f" {self.variable} cannot see it. For a private repository of an organisation"
                f" the usual causes are that the token lacks the {permission} permission"
                " (read-only), or that it still waits for the organisation's approval"
            )
        elif status == 403:
            said = (
                f"GitHub refused {what} to the token in {self.variable} (HTTP 403): give the"
                f" token read-only access to {permission} on {self.repo}, and check that the"
                " organisation approved it"
            )
        elif 300 <= status < 400:
            said = (
                f"GitHub answered {what} with a redirect (HTTP {status}), which is not"
                " followed: if the repository was renamed or moved, set repo to its new"
                " owner/name"
            )
        elif status >= 500:
            said = (
                f"GitHub answered HTTP {status} to {what}, a fault on its side: the next run"
                " tries again"
            )
        else:
            said = f"GitHub answered HTTP {status} to {what}"
        if reply.message:
            said += f'. GitHub said: "{reply.message}"'
        return said


def _utc_now() -> datetime:
    return datetime.now(UTC)


KIND = SourceKind(
    check=check_config,
    open=open_source,
    via=VIA,
    starts_off=True,
    describe=describe,
    switch_on=SWITCH_ON,
    switch_on_with=switch_on_with,
    default_project=DEFAULT_PROJECT,
    settings=SETTINGS_HELP,
    withheld=True,
)
