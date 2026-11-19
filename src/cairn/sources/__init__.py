"""Sources: systems cairn reads notes from on its own, and the runner that writes them.

Contract (frozen by tests/acceptance/test_c2_langfuse.py and, for on and off,
tests/acceptance/test_s2_source_switch.py):

The seam
- A source has a `name` and `async pull(cursor) -> Pulled`. `Pulled` holds `drafts`
  (NoteDraft: `project`, `title`, `text`, `source_ref`, `label`, and `resume`: the cursor
  that picks up right after this draft, or None), `skipped` (Skip: `ref`, the item's id at
  the source, and `reason`) and `cursor`, an opaque string the next pull starts from. The
  source owns the cursor: the runner only stores it. A pull that fails raises
  SourceError, whose message never holds a credential (and never a response body), and
  returns nothing, so the stored cursor does not move.
- A source never decides where a note may go: `NoteDraft.project` is what the source says,
  and the runner writes only into projects its config allows and that exist. `NoteDraft.topic`
  (None) is passed to the note it writes, so drafts with one topic land on one page.
- Items (I1; frozen by tests/acceptance/test_i1_source_items.py). `NoteDraft.item` (None)
  is the source's own stable name for the thing a draft is, 1 to ITEM_MAX (200)
  characters: a kind that sets it promises that two drafts of one source with the same
  item are the same thing. The runner writes an item once for as long as the source
  exists, whatever its text, title or link later: a draft whose item the source already
  wrote counts as `unchanged` and nothing is written, declined or logged for it. An item is
  recorded once its note is stored ("saved", "unchanged" or "rejected"), never when
  nothing was stored, so such a draft is judged again when it comes again. The record is
  per source, in the store (Store.written_source_items, Store.record_source_item; the table
  source_items holds a digest of each item, never the item): two sources each write an
  item, a source switched off and on keeps its record, a source removed or a workspace
  deleted loses it, and an export carries none. The run's order and the window a run that
  dies leaves are in run_source's docstring. A draft without an item is written as before;
  a built-in kind that sets one says so in its module (linear and github do).
- On and off (S2). Only the operator adds a source from a config, on the command line
  (decision 25), or registers an account that people make sources from
  (Store.add_source_from, R2: CHOOSING and config_from below); owners and admins, and a
  member for the sources of their own account (R4), switch it on and off and choose its
  projects
  (Store.enable_source, Store.disable_source; source-enable and source-disable, and
  /integrations/<name>/on and /off in the web view). A source that is off is not read:
  `run_source` answers SyncReport(off=True) and nothing else, with no claim taken, `open`
  never called, no cursor moved, no run recorded and no audit event; `sync_all` lists it
  so, and `sync_forever` logs nothing for it. Switched on again, it keeps its cursor.
- Kinds. KINDS maps a kind's name to a SourceKind: `check(config) -> config` (the config as
  stored, or ValueError), `open(name, config, *, http, env, clock, sleep, deadline, since)
  -> Source` (SourceError when the environment lacks what the config names), `via`, and
  `starts_off` (False): a source of a kind that starts off is added off (Store.add_source)
  and its config may name no project yet, when its `check` allows it. `since` is the source's
  last switch-on (SourceInfo.enabled_at) as an aware UTC datetime: a kind that starts off
  reads nothing that ended before it (each connector's job, tested with it), except that
  Linear and GitHub read from `backfill_days` before it when their config says so (K2,
  tests/acceptance/test_k2_backfill.py); Langfuse ignores it, since a trace is read
  because a person marked it. `deadline` is
  when the source's pull should have returned (PULL_SHARE of the run's limit): a pull
  that reaches it, or another of its bounds, returns what it finished with a cursor that
  goes on from there, and raises only when it could not move at all. A kind may describe
  itself for GET /sources: `describe(config)` (None) says in one sentence what a source
  reads, and `switch_on` (None) what switching it on reads (`switching_on()`: FROM_THEN_ON
  for a kind that starts off when it says nothing; Langfuse says it goes on from where it
  stopped, traces marked meanwhile included). A kind whose switch-on sentence depends on
  the config says it through `switch_on_with` (None), and `switching_on(product, config)`
  gives that sentence for a source's checked config (GET /sources passes it; Linear and
  GitHub say their backfill so). `default_project` (None; Granola's is
  "meetings") is the project GET /sources proposes for a source that names none yet: ticked
  when the viewer reaches a project of that name (ignoring case), else typed into the
  new-project field, which makes it private on "Switch on". It is only a proposal: POST
  /integrations/<name>/on still needs a project or a new one, and a name taken by a project
  out of the viewer's reach is refused there as any taken name is. source-add's line for
  a source added off names it as the --project to give. `own_project` (False) makes that
  proposal the source's own name instead (SourceKind.proposed_project, which the page and
  source-add ask; the connector kind's). `settings` (None) is the kind's settings as
  `source-add --help` lists them, for a kind the command's own text does not hold. Every
  kind's config has
  `projects`, the names of the projects the source may write to. The store asks KINDS to check
  a config, so a second connector is one module and one entry here. Built in: "langfuse"
  (cairn.sources.langfuse), "granola" (cairn.sources.granola, which starts off and reads
  only notes created at or after `since`; tests/acceptance/test_c3_granola.py) and
  "google_meet" (cairn.sources.google_meet, which starts off and reads only meetings that
  ended at or after `since`; tests/acceptance/test_c4_google_meet.py).
  Also "google_drive" (cairn.sources.google_drive, which starts off and reads the documents
  put into one folder from `since` on; tests/acceptance/test_c7_google_drive.py).
  Also "google_calendar" (cairn.sources.google_calendar, which starts off and reads only
  events that ended at or after `since`; tests/acceptance/test_c5_google_calendar.py).
  Also "gmail" (cairn.sources.gmail, which starts off and reads only the messages of one
  mailbox that carry one label a person put on them and arrived at or after `since`;
  tests/acceptance/test_c6_gmail.py).
  Also "linear" (cairn.sources.linear, which starts off and reads the issues finished at or
  after `since`, each once; tests/acceptance/test_c10_linear.py).
  Also "github" (cairn.sources.github, which starts off and reads the work of one repository
  finished at or after `since`, each item once; tests/acceptance/test_c11_github.py).
  Linear and GitHub can also start with what finished in `backfill_days` before `since`
  (tests/acceptance/test_k2_backfill.py).
  Also "connector" (cairn.sources.connector, which starts off and reads, from a web service
  of the operator's own that keeps the connector protocol, each item once by its id;
  tests/acceptance/test_c9_connector.py).
- Withheld kinds (M2, frozen by tests/acceptance/test_m2_withheld_notes.py).
  `SourceKind.withheld` (False) says that notes of the kind are not sent to a model unless
  the operator allows it: True for every kind that reads Google Workspace data
  ("google_meet", "google_calendar", "google_drive", "gmail") and for "connector" (whose
  words cannot be known), False for "langfuse", "granola", "linear" and "github".
  `withheld_via()` is the `via` of every withheld kind in KINDS, read when called.
  The rule is applied in one place, cairn.arrange, by a note's `via`: a note a person or
  an agent stored with a withheld kind's `via` is withheld too.
  `HELD_VIA` is the `via` of notes held back the same way that no kind writes: Slack's
  ("slack", cairn.slack). Slack is not a kind, so it is not in KINDS or withheld_via(); the
  arranger holds it apart, by `via` alone, and only the operator's choice that names Slack
  (<PREFIX>_SLACK_NOTES=send) sends it.
- Notes that name a source (M3, tests/acceptance/test_m3_notes_that_name_a_source.py). The
  sources guide's recipes have an agent read a meeting, a mail thread, a calendar, a document
  or a to-do list with a connector of its own and write a note whose `source_ref` has a form:
  SOURCE_REF_FORMS, the word before the first ":" ("meeting", "mail", "calendar", "doc",
  "todo"; the guide's table is held against it). `source_ref_word(source_ref)` is the word
  before the first ":", white space at its ends dropped and letter case ignored (None with no
  colon); `source_ref_form` is that word when it is a recipe form. A withheld kind also says
  the word its own notes' `source_ref` starts with (`SourceKind.ref_form`: "gmail", "meet",
  "drive", "calendar"; the connector has none, its references being the operator's): an
  agent that writes "gmail:<id>" for the recipe's "mail:<id>" says the same thing.
  `withheld_forms()` is the recipe forms and the withheld kinds' words, read when called; a
  kind that is not withheld adds none. `withheld_by_ref(via, source_ref)` says whether a note
  is kept from a model by its `source_ref`: its word is one of withheld_forms() and no kind
  in KINDS writes via `via` (read when called). A note a kind wrote is
  judged by the kind alone, as M2 has it: Granola's "meeting:granola/<id>" notes are sent
  (decision 90). The arranger asks it in the same check, of the note it already read.
- Credentials are never in a config: a config names the environment variables of the
  process that syncs (the command line, or `serve`), and the source reads them from the
  `env` it is opened with.
- Nothing a run stores holds a credential, whatever the server sent back: a source may say
  what it authenticates with (`secrets`, a tuple of strings; Langfuse's: both keys, the
  Basic header and its token). The runner reads `secrets` once the source is opened and
  again after the pull, or after a pull that failed: a source that signs in during its pull
  (a Google source asks for its access token then) holds that token only from then on.
  Those and the values of the variables its config names (`*_env`) are what
  `holds_secret` looks for: as written and percent-decoded (repeatedly), and, for one of
  SECRET_ENCODED_MIN (12) characters or more, also with white space taken out, in base64
  or base64url at any alignment, and in hex. A
  draft holding one is not written (skipped, HELD_CREDENTIALS), a skip reason holding one
  is counted under HELD_CREDENTIALS, a cursor holding one fails the run, and a failed run's
  message holding one is kept as a sentence that says so.

The runner (cairn.sources.runner, re-exported here; importing this package does not import
the memory use cases)
- `run_source(view, name, *, http, env, gate=None, guard=None, clock=None, sleep=None,
  limit=RUN_LIMIT, plan=None, plans=None) -> SyncReport` (`saved`, `unchanged`, `refused`,
  `rejected`, `skipped`, `malformed`, `left`: drafts it had no time for, `capped`, `hold`,
  `reasons`:
  the skips by reason, `declined`, `busy`, `held`, `superseded`). It claims the source in the store
  (`Store.claim_source`, a compare-and-set recorded in the database, so the command line
  and the server's background sync see each other): when another run holds an unexpired
  claim it returns a report with `busy` set and does nothing else. It opens the source,
  pulls from the stored cursor, and writes each draft through `Memory.note` (the note
  guard, the compiler and `gate`, lint_gate by default, as `serve` uses) with the kind's
  `via` ("langfuse"), acting as "system", one draft at a time on a worker thread so
  the server's event loop keeps serving requests. A draft for a project the
  config does not allow, or that does not exist, is skipped ("project not allowed", "no such
  project"); a draft is written by the id its project had when the run claimed the source
  (SourceClaim.project_ids), so a project deleted during the run and made again under its name
  gets nothing ("no such project"). A draft refused for what it is (Memory.note raises
  Malformed: EmptyText, "empty text"; PastBound, a field past cairn.store.FIELD_MAX, "a
  field past its bound"; LongHeadings, a passage under headings past
  cairn.store.HEADINGS_MAX or more headings than HEADINGS_OVER_PASSAGES over its passages,
  "headings past their bound")
  is skipped by that reason and counted in `malformed` too, and the cursor moves past it,
  so no later run proposes it again; any other BadRequest fails the run. A draft whose page
  holds all the claims a page holds (Memory.note raises PageFull) is skipped by reason
  "page full" and the cursor moves past it too: a page full of other notes, never a draft
  too long for an empty page alone, since every kind cuts or splits what it writes to fit a
  page's bounds (K4, cairn.sources.fit). A refusal for the
  workspace's state is not a skip: the guard's (declined, below) and a cap's (`capped`).
  Then it stores the new cursor and the report (`Store.finish_source_run`), which
  records one "source.synced" audit event with the counts (`malformed` among them; never a
  reason, a link or any text) and the source's name, unless
  every count is zero (a background sync every minute would otherwise fill the audit log).
  A claim older than SOURCE_CLAIM_TTL may be taken by another run. Just before each
  draft it writes, a run checks in the store that the claim is still its own; a run that
  finds it gone, then or when it comes to store the cursor, writes nothing more, stores
  nothing (the claim, the cursor and `last_error` are the other run's) and reports
  `superseded`. A check-then-write: a note already being written when the claim is taken
  still lands, and the next run finds it "unchanged".
- A run has `limit` (RUN_LIMIT, 10 minutes, on `clock`), well inside SOURCE_CLAIM_TTL;
  its pull has the first half of it (PULL_SHARE).
  Once it has passed, the run writes no more drafts and stores, as a run that worked, the
  `resume` of the last draft it came to (or the cursor it started from, when it came to
  none or that draft has no `resume`); `left` counts the drafts it did not come to, and
  the next run pulls them again.
- Plans (H6): `run_source(..., plan=(name, Plan))` with a view limited by that plan
  (sync_all and `source-sync` with plans pass both). A draft past one of its caps is not
  declined: the run stops there and stores, as for `limit`, the cursor before it; `capped`
  counts it and every draft after it. When that cap was the month's notes, the report
  keeps `hold` ({"until": the instant the UTC month resets, "plan", "cap"}): until then,
  while the workspace is on the same plan with the same notes cap, a run of that source
  neither claims nor pulls nor writes anything and answers `held`. A plan-set, or a plans
  file that changes the cap, lets it go at the next run. A storage cap holds nothing.
  sync_all and `source-sync` limit the view with `plans.limit` (resolve=limits_of) and pass
  `plans=`: each draft's write reads the plan in force inside its own transaction, so a
  downgrade during a pass holds its next draft, and `hold` names that plan.
- Running again writes nothing new; a run that died halfway can simply be run again: what
  it had already written comes back "unchanged" (a note with the same text and link is
  the one already stored, and its page already says it).
- A draft the note guard refused, or whose page the gate rejected, is final for the
  item it came from: the cursor has moved past it, so no later run proposes it again. The
  run's report lists each one under `declined` (at most MAX_DECLINED) as its link (the
  draft's `source_ref`, a Langfuse trace link), its status and its codes (the guard's rule
  names, or the gate's violation codes) and its project's id, never any text of the note or
  of a violation's message; GET /sources shows that list with the last run, each link only
  to a viewer who reaches its project (Store.list_sources trims it). The audit event keeps the
  counts only.
- A run that fails raises, moves no cursor, releases its claim and leaves its message in
  the source's `last_error`: a SourceError's own message (never a credential), anything
  else as its class name only, since its message could quote a note. The next run that
  works clears it. Storing the result is part of the run: if that fails, the claim is
  released all the same. A run stopped while it was claiming lets go of the claim it got.
- The runner and `sync_all` make every store call on a worker thread, never on the event
  loop; a call under way when the run is cancelled finishes first.
- `sync_all(store, *, http, env, gate=None, guard=None, clock=None, sleep=None) ->
  list[SyncOutcome]` (`account_id`, `source`, `report` or None, `error` or None,
  `error_type`) runs every source of every workspace once, in workspace then name order;
  one that fails does not stop the others. Cancellation is not a failure: it stops the
  pass.
- `sync_forever(store, every, *, http, env, gate=None, guard=None, ...)` is what
  `serve --sync-every SECONDS` runs in the background: a pass, then a wait of what is left
  of `every` seconds since the pass started (none when the pass took longer: passes never
  overlap and missed ones are not made up). A failing source is logged on "cairn.sources"
  by workspace, source and error class, never its message; a pass that breaks as a whole
  is logged by class and the next pass still comes. It stops when cancelled (the server's
  shutdown); a note being written on a worker thread then finishes first, and the stopped
  run releases its source and moves no cursor.
- Preview (R1, tests/acceptance/test_r1_preview.py). `preview_source(view, name, *, http,
  env, days=None, clock=None) -> Preview` reads a source as a run would and stores nothing:
  no claim, cursor, item, audit event or note. An off source is pulled once from no cursor
  with `since` `days` ago (1 to 30, 7 unless given; Langfuse ignores `since` and looks back
  its own 7 days); an on source from its stored cursor and
  its own switch-on. Who may: who may switch it on with the projects it names
  (Store.source_to_preview). The answer counts the drafts a run would write and lists the
  first 20 titles (HELD_TITLE for one holding a credential), with `written_before` (the
  items the source wrote, Store.source_items_written: only GitHub, Linear and the connector
  name items) and `out_of_reach` (drafts for a project the viewer does not reach, never
  shown). A source that cannot be read answers `read` False and why; one whose credential
  variable is unset fails in `open`, before any request.
- `source_client(transport=None)` is the HTTP client sources read through: a 30 s timeout,
  redirects never followed. Making one holds the "httpx" and "httpcore" loggers at WARNING
  or above, so no caller's log gets httpx's request lines, which carry the query string, or
  httpcore's DEBUG trace of a response's headers, where a provider may echo a credential.
  `kept_error(error)` is what a failed run keeps and prints.
"""

import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import unquote

from cairn import SOFTWARE

if TYPE_CHECKING:
    from cairn.sources.runner import (
        SOURCE_LOGGER,
        SyncOutcome,
        SyncReport,
        kept_error,
        preview_source,
        run_source,
        source_client,
        sync_all,
        sync_forever,
    )


HELD_CREDENTIALS = "held the source's credentials"  # the reason such a draft is skipped


# A secret at least this long (characters) is also looked for with white space taken out and
# encoded (holds_secret); a shorter one only as it is: the base64 or hex of a few characters
# is short enough for innocent text to hold it. The same floor as the note guard's for a
# secret (cairn.guard.LITERAL_MIN), not imported so that this seam stays light.
SECRET_ENCODED_MIN = 12
UNQUOTE_ROUNDS = 8  # percent-decodings applied at most, one after the other


def holds_secret(value: object, secrets: "tuple[str, ...]") -> bool:
    """Whether `value` holds one of `secrets`. Anything not a string is looked at as JSON.

    Each secret is looked for as it is, in the text and in the text percent-decoded again
    and again until nothing changes (at most UNQUOTE_ROUNDS times: a link encodes the "+",
    "/" and "=" of a Basic header, and a link inside a link encodes it twice). A secret of
    SECRET_ENCODED_MIN characters or more is also looked for, in each of those texts and in
    each with all white space taken out (a key split across lines, or spaced out), with
    its own white space taken out, and encoded: in base64 and in base64url, with or
    without padding, at each of the three alignments a substring can have (the part of the
    encoding that does not depend on the bytes around it), and in hex, in either case. The
    work is linear in the text's length for a fixed number of secrets."""
    if value is None or not secrets:
        return False
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    texts = [text]
    for _ in range(UNQUOTE_ROUNDS):
        decoded = unquote(texts[-1])
        if decoded == texts[-1]:
            break
        texts.append(decoded)
    if any(secret in shown for shown in texts for secret in secrets if secret):
        return True
    long = [secret for secret in secrets if len(secret) >= SECRET_ENCODED_MIN]
    if not long:
        return False
    texts = list(dict.fromkeys([*texts, *("".join(shown.split()) for shown in texts)]))
    lowered = list(dict.fromkeys(shown.lower() for shown in texts))
    for secret in long:
        bare = "".join(secret.split())
        raw = bare.encode()
        if any(bare in shown for shown in texts) or any(raw.hex() in shown for shown in lowered):
            return True
        if any(needle in shown for needle in _base64_cores(raw) for shown in texts):
            return True
    return False


def _base64_cores(raw: bytes) -> "list[str]":
    """The base64 and base64url of `raw` as it appears inside the encoding of any longer
    text: for each of the three byte offsets it can start at, the characters that depend
    only on its own bytes (a character holds 6 bits, a byte 8)."""
    import base64

    cores = []
    for offset in range(3):
        encoded = base64.b64encode(bytes(offset) + raw).decode()
        start, end = -(-8 * offset // 6), 8 * (offset + len(raw)) // 6
        core = encoded[start:end]
        cores += [core, core.replace("+", "-").replace("/", "_")]
    return cores


class SourceError(Exception):
    """A source could not be read. The message says why and what to do; it never holds a
    credential or a response body."""


ITEM_MAX = 200  # characters of a draft's item, at most


@dataclass(frozen=True)
class NoteDraft:
    """A note a source proposes: the project it names, its title and Markdown text, the link
    back to where it came from, the label it carries and, when it says one, the topic of the
    page it belongs on (Memory.note's `topic`: drafts with one topic land on one page).

    `selectors` (R3): the selectors of the source's account this draft's item carries, as
    `marks` writes them (Gmail: "label:" and each of its own labels a message carries,
    case-folded); empty for a kind whose items carry one selector only. A run holds a draft
    that carries a selector of another source made from the same account that is on.

    `item` (I1): the source's own stable name for the thing this draft is, 1 to ITEM_MAX
    characters, or None. A kind that sets it promises that two drafts of one source with the
    same item are the same thing, and the runner writes an item once for as long as the
    source exists (see "Items" in this package's docstring). ValueError: an item that is
    not such a string, so a kind that breaks the promise fails its pull."""

    project: str
    title: str
    text: str
    source_ref: str
    label: str
    resume: str | None = None  # the cursor a run that stops right after this draft stores
    topic: str | None = None  # None: the page is named by the title
    item: str | None = None  # None: judged by its text alone, as every draft before I1
    selectors: tuple[str, ...] = ()  # R3: what of its account's selectors the item carries

    def __post_init__(self) -> None:
        if self.item is not None and not (
            isinstance(self.item, str) and 1 <= len(self.item) <= ITEM_MAX
        ):
            raise ValueError(f"a draft's item is 1 to {ITEM_MAX} characters")


@dataclass(frozen=True)
class Skip:
    """An item a source handled without proposing a note: its id at the source, and why."""

    ref: str
    reason: str


@dataclass(frozen=True)
class Pulled:
    """What one pull found, and the cursor the next pull starts from.

    `stopped` (None): one sentence, in the kind's words, saying why the pull stopped while
    the source still had more (a bound on its answers reached, the source asking to slow
    down) and what the next run does. Never a credential, an item's text or the software's
    name: the run's report keeps it (SyncReport.said) and the Sources page shows it."""

    drafts: list[NoteDraft] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    cursor: str | None = None
    stopped: str | None = None
    held: list["Held"] = field(default_factory=list)  # R3: items the kind kept unread
    held_expired: int = 0  # R3: held items that left the look-back window, unwritten


@dataclass(frozen=True)
class Held:
    """An item a kind held unread (R3), because it carries a selector of another source of
    the same account that is on (the source's `holding`): its title, its link (as a draft's
    `source_ref`), the project it would go to, and the selectors it carries."""

    title: str
    link: str
    project: str
    selectors: tuple[str, ...]


class Source(Protocol):
    name: str

    async def pull(self, cursor: str | None) -> Pulled: ...


Clock = Callable[[], datetime]


@dataclass(frozen=True)
class SourceKind:
    """One kind of source: how its config is checked, and how a source is opened from it."""

    check: Callable[[Any], dict[str, Any]]
    open: Callable[..., Source]
    via: str  # its notes are written via this
    starts_off: bool = False  # a source of this kind is added off, to be switched on
    # What a source of this kind reads, in one sentence made from its checked config (never
    # a credential): the "Reads" fact on GET /sources. None: the page shows no such fact.
    describe: Callable[[Mapping[str, Any]], str] | None = None
    # What switching a source of this kind on reads, in one sentence, said beside an off
    # source on GET /sources ("{product}" in it: the product's name). None: FROM_THEN_ON
    # for a kind that starts off (it obeys `since`), and nothing for any other.
    switch_on: str | None = None
    # The project GET /sources proposes for a source of this kind that names none yet (a
    # project's name): ticked when the viewer reaches a project of that name, else typed into
    # the new-project field. Only a proposal: a post still names its projects.
    default_project: str | None = None
    # Whether notes of this kind are kept from a model unless the operator allows it (M2):
    # a page that cites one is tidied without the model (cairn.arrange). True for the kinds
    # that read Google Workspace data, whose rules restrict passing it on.
    withheld: bool = False
    # Whether the project proposed for a source of this kind that names none is the source's
    # own name (C9: a connector of the operator's own reads a system only they know), in
    # place of `default_project`. See proposed_project.
    own_project: bool = False
    # The kind's settings for `source-add --help`, after the kind's name: a JSON object of
    # them and what each means (never a credential). None: the kind is in the command's own
    # text, as the kinds built in before C9 are.
    settings: str | None = None
    # What switching on a source with this checked config reads, when that depends on the
    # config (K2: Linear's and GitHub's backfill_days), in one sentence ("{product}" in it:
    # the product's name). None: `switch_on` says it for every config.
    switch_on_with: Callable[[Mapping[str, Any]], str] | None = None
    # The word before the first ":" of every `source_ref` this kind's notes carry ("gmail"
    # for "gmail:<id>"), lower case, or None for a kind with no word of its own (a link, or
    # a name the operator chooses). For a withheld kind, a note a person or an agent writes
    # with that word is withheld too (M3: withheld_forms); for any other it is not read.
    ref_form: str | None = None

    def proposed_project(self, source: str) -> str | None:
        """The project GET /sources and source-add propose for the source named `source` when
        it names none: the source's own name for a kind with `own_project` (None when that
        name is not one a project may have), else `default_project`."""
        if not self.own_project:
            return self.default_project
        from cairn.store.memory import is_project_name  # the store imports this package

        return source if is_project_name(source) else None

    def switching_on(
        self, product: str = SOFTWARE, config: Mapping[str, Any] | None = None
    ) -> str | None:
        """The sentence GET /sources says beside an off source of this kind, if any, naming
        the product as `product`. With the source's checked `config`, a kind that says it by
        config (`switch_on_with`) says it for that config."""
        if config is not None and self.switch_on_with is not None:
            return self.switch_on_with(config).format(product=product)
        if self.switch_on is not None:
            return self.switch_on.format(product=product)
        return FROM_THEN_ON if self.starts_off else None


FROM_THEN_ON = (
    "Switching it on reads only what happens from then on: nothing from before, and nothing"
    " from while it was off."
)


_RUNNER = {
    "SOURCE_LOGGER",
    "SyncOutcome",
    "SyncReport",
    "config_from",
    "kept_error",
    "near_miss",
    "preview_source",
    "run_source",
    "source_client",
    "sync_all",
    "sync_forever",
}


def __getattr__(name: str) -> Any:
    """The runner's names, imported when first asked for: the runner uses the memory use
    cases, which this package's seam must not pull in (the store imports KINDS)."""
    if name in _RUNNER:
        from cairn.sources import runner

        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# The kinds. Imported last: each kind's module builds on the seam above.
from cairn.sources.connector import KIND as _CONNECTOR  # noqa: E402
from cairn.sources.github import KIND as _GITHUB  # noqa: E402
from cairn.sources.gmail import KIND as _GMAIL  # noqa: E402
from cairn.sources.google_calendar import KIND as _GOOGLE_CALENDAR  # noqa: E402
from cairn.sources.google_drive import KIND as _GOOGLE_DRIVE  # noqa: E402
from cairn.sources.google_meet import KIND as _GOOGLE_MEET  # noqa: E402
from cairn.sources.granola import KIND as _GRANOLA  # noqa: E402
from cairn.sources.langfuse import KIND as _LANGFUSE  # noqa: E402
from cairn.sources.linear import KIND as _LINEAR  # noqa: E402

KINDS: dict[str, SourceKind] = {
    "langfuse": _LANGFUSE,
    "granola": _GRANOLA,
    "google_meet": _GOOGLE_MEET,
    "google_drive": _GOOGLE_DRIVE,
    "google_calendar": _GOOGLE_CALENDAR,
    "gmail": _GMAIL,
    "linear": _LINEAR,
    "github": _GITHUB,
    "connector": _CONNECTOR,
}


@dataclass(frozen=True)
class Choosing:
    """How a source is made from a credential of one kind (R2): which of the kind's settings
    the credential holds (names and addresses, never a secret's value), which a person
    chooses what it reads with (`selector`), and which reading options they may set. With
    `projects`, the three are the kind's settings, each once. `needs`: a source names at
    least one of these selector settings, or `nothing_selected` says what to name; empty:
    the project chosen is the choice (Langfuse) or the operator's feed is (the connector).
    `refused`: no source of this kind is made from a credential, in these words. `sample`:
    a selector the kind's check accepts, used only to check a credential's settings."""

    credential: tuple[str, ...]
    selector: tuple[str, ...]
    options: tuple[str, ...]
    needs: tuple[str, ...] = ()
    nothing_selected: str = ""
    refused: str | None = None
    no_selector: str | None = None  # the sentence for any selector given, when it has none
    sample: Mapping[str, Any] = field(default_factory=dict)


_GOOGLE_CREDENTIAL = ("credentials_env", "subject", "base_url")
_SHOWN = re.compile(r"[a-z][a-z0-9_]{0,39}")  # a name a refusal may repeat

# Each kind's table (section 3 of the R2 contract). The command line (source-add) keeps
# every default it had: these rules are asked by Store.add_source_from alone.
CHOOSING: dict[str, Choosing] = {
    "langfuse": Choosing(
        credential=("base_url", "public_key_env", "secret_key_env"),
        selector=("score_name",),
        options=(),
    ),
    "granola": Choosing(
        credential=("key_env", "base_url"),
        selector=("folder_id", "space_id"),
        options=("transcripts", "settle_minutes", "lookback_days"),
        needs=("folder_id", "space_id"),
        nothing_selected="name one folder or one space",
        sample={"folder_id": "fol_" + "0" * 14},
    ),
    "google_meet": Choosing(
        credential=_GOOGLE_CREDENTIAL,
        selector=("meeting_code",),
        options=("wait_hours",),
        needs=("meeting_code",),
        nothing_selected="name one meeting: the code from its address, such as abc-mnop-xyz",
        sample={"meeting_code": "abc-mnop-xyz"},
    ),
    "google_drive": Choosing(
        credential=_GOOGLE_CREDENTIAL,
        selector=("folder_id",),
        options=("settle_hours", "existing"),
        needs=("folder_id",),
        nothing_selected="name one folder: the last part of its address",
        sample={"folder_id": "0" * 10},
    ),
    "google_calendar": Choosing(
        credential=_GOOGLE_CREDENTIAL,
        selector=("calendar_id",),
        options=(),
        needs=("calendar_id",),
        nothing_selected=('name one calendar: "primary" is your main calendar, every event on it'),
        sample={"calendar_id": "primary"},
    ),
    "gmail": Choosing(
        credential=_GOOGLE_CREDENTIAL,
        selector=("label",),
        options=("lookback_days",),
        needs=("label",),
        nothing_selected="name the one label you put on the messages it reads",
        sample={"label": "sample"},
    ),
    "linear": Choosing(
        credential=("key_env", "base_url"),
        selector=("teams", "linear_project"),
        options=("comments", "settle_minutes", "lookback_days", "backfill_days"),
        needs=("teams", "linear_project"),
        nothing_selected="name teams or one Linear project",
        sample={"teams": ["TEAM"]},
    ),
    "github": Choosing(
        credential=("token_env", "base_url"),
        selector=("repo", "items"),
        options=("comments", "settle_minutes", "lookback_days", "backfill_days"),
        needs=("repo",),
        nothing_selected="name one repository, as owner/name",
        sample={"repo": "example/example"},
    ),
    "connector": Choosing(
        credential=("url", "secret_env"),
        selector=(),
        options=("limit",),
        no_selector="the connector protocol has no selector",
    ),
}


# Two sources made from one account that claim one item (R3, invariant 4).
OVERLAP = (
    "another source made from this account already reads what this one would ({}): two"
    " sources never read one item, so choose another selector or another account"
)
# A pair made before a kind's rule refused it, both on: neither run reads or writes (R3).
OVERLAP_ON = (
    "another source made from this account, {}, can read what this one reads, and both are"
    " on: neither reads or writes anything until one of them is switched off"
)


def marks(kind: str, config: Mapping[str, Any]) -> frozenset[str]:
    """The selectors a source of `kind` with `config` (checked) claims, as a draft's
    `selectors` names them: only for a kind whose items can carry the selectors of two of its
    account's sources and that says so per item (Gmail: its label, case-folded). Empty for
    every other kind, whose overlaps are refused when a source is made (overlaps)."""
    if kind == "gmail" and isinstance(config.get("label"), str):
        return frozenset({"label:" + config["label"].casefold()})
    return frozenset()


def _folded(values: object) -> set[str]:
    return (
        {v.casefold() for v in values if isinstance(v, str)} if isinstance(values, list) else set()
    )


def _same(a: Mapping[str, Any], b: Mapping[str, Any], name: str) -> bool:
    x, y = a.get(name), b.get(name)
    return isinstance(x, str) and isinstance(y, str) and x.casefold() == y.casefold()


PRIMARY_CALENDAR = "primary"  # Google's keyword for the calendar of the account it reads as


def _calendar(config: Mapping[str, Any]) -> str:
    """The calendar a Calendar config names, case-folded: `primary` when it names none, as
    the kind's check reads it."""
    named = config.get("calendar_id")
    return named.casefold() if isinstance(named, str) else PRIMARY_CALENDAR


def overlaps(kind: str, a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Whether two sources of `kind` made from one account, with the checked configs `a` and
    `b`, can select one item (refused when the second is made, OVERLAP). Where cairn cannot
    show that two selections are apart, they overlap. Linear: a team twice, teams beside a
    Linear project, or one project twice. GitHub: one repository with an item kind in
    common. Langfuse: one project twice. A connector: always (its feed is the selection).
    Drive: one folder twice. Gmail: one label twice (two labels are held per message
    instead). Google Meet: one meeting's code twice.

    Granola: always, whatever each selects. A source with no folder and no space reads every
    note the key reads; a folder's notes include those of the folders below it; a note can be
    in any number of folders, so in the spaces of each, and a folder may sit in the space
    another source reads (docs/granola-api.md: List notes' `folder_id`, Get note's
    `folder_membership` and `space_membership`, and the help center, paraphrased there: a
    note can be in any number of folders). Nothing in those notes shows two selections apart,
    so one account has at most one Granola source made from it.

    Google Calendar: one calendar id twice, in any letter case (no id is `primary`), and
    `primary` beside any other id, whether or not the config's `subject` is known: the
    primary calendar's id only "usually matches the user's primary email address", and that
    `primary` is the delegated person's calendar is not stated (docs/google-calendar-api.md,
    B and A), so cairn cannot tell `primary` from any address. Accepted: two ids, neither
    `primary`, that differ beyond letter case. A calendar "is identified by an ID", and the
    only second name for a calendar our notes record is the `primary` keyword. Not ruled
    out: a person's other address (an alias) naming their primary calendar; Google's pages
    do not say whether an alias is accepted as a calendar's id."""
    if kind == "linear":
        teams_a, teams_b = _folded(a.get("teams")), _folded(b.get("teams"))
        one_a, one_b = a.get("linear_project"), b.get("linear_project")
        return bool(
            (teams_a & teams_b)
            or (teams_a and one_b)
            or (one_a and teams_b)
            or _same(a, b, "linear_project")
        )
    if kind == "github":
        every = ["pulls", "issues", "releases"]
        shared = set(a.get("items", every)) & set(b.get("items", every))
        return _same(a, b, "repo") and bool(shared)
    if kind == "langfuse":
        return bool(_folded(a.get("projects")) & _folded(b.get("projects")))
    if kind == "connector":
        return True
    if kind == "granola":
        return True
    if kind == "google_calendar":
        one, other = _calendar(a), _calendar(b)
        return one == other or PRIMARY_CALENDAR in (one, other)
    selector = {
        "google_drive": "folder_id",
        "gmail": "label",
        "google_meet": "meeting_code",
    }
    return kind in selector and _same(a, b, selector[kind])


def config_from(
    kind: str,
    settings: Mapping[str, Any],
    select: object,
    options: object,
    projects: list[str],
) -> dict[str, Any]:
    """A source's config made from a credential's `settings` (as stored), a person's
    `select` and `options` (JSON objects) and its `projects`, before the kind's check: what
    Store.add_source_from checks with KINDS[kind].check. ValueError, in words that name a
    setting and never repeat a value: a kind no source is made from this way (`refused`), a
    setting the credential holds ("base_url is set by the account, not by a source"),
    `projects` in either object, a selector sent as an option or the other way round, a
    name the kind does not have, and nothing selected (the kind's own sentence)."""
    choosing = CHOOSING[kind]
    if choosing.refused is not None:
        raise ValueError(choosing.refused)
    chosen: dict[str, Any] = {}
    for given, own, other, what in (
        (select, choosing.selector, choosing.options, "selector"),
        (options, choosing.options, choosing.selector, "option"),
    ):
        if not isinstance(given, Mapping):
            raise ValueError(f"the {what}s are a JSON object of settings")
        for name, value in given.items():
            if name in choosing.credential:
                raise ValueError(f"{name} is set by the account, not by a source")
            if name == "projects":
                raise ValueError("projects is chosen on its own: name one project")
            if what == "selector" and not choosing.selector and choosing.no_selector:
                raise ValueError(choosing.no_selector)
            if name in other:
                where = "select" if what == "option" else "options"
                raise ValueError(f"{name} is not a {what} of a {kind} source: send it in {where}")
            if name not in own:
                takes = ", ".join(own) or "nothing"
                shown = f" {name!r}" if isinstance(name, str) and _SHOWN.fullmatch(name) else ""
                raise ValueError(f"a {kind} source has no {what}{shown}: it takes {takes}")
            if value is not None:
                chosen[name] = value
    if choosing.needs and not any(name in chosen for name in choosing.needs):
        raise ValueError(choosing.nothing_selected)
    return {**settings, **chosen, "projects": list(projects)}


def withheld_via() -> frozenset[str]:
    """The `via` of every kind in KINDS that is `withheld`, read now: a kind added to KINDS
    later counts."""
    return frozenset(kind.via for kind in KINDS.values() if kind.withheld)


# The `via` of notes held back from a model that no kind in KINDS writes: Slack's
# (cairn.slack.people.write, for a filed message, a note typed with the command and a direct
# message alike). Slack is not a kind: it cannot be added, listed or switched on as a source.
# Held by `via` alone, never by `source_ref`, and sent only when the operator names it
# (<PREFIX>_SLACK_NOTES=send, cairn.web.cli_arrange): <PREFIX>_SOURCE_NOTES=send does not.
HELD_VIA = frozenset({"slack"})


# M3: the forms of `source_ref` the sources guide's recipes give an agent that reads a meeting,
# a mail thread, a calendar, a document or a to-do list with a connector of its own: the word
# before the first ":". The guide's table is held against this set
# (tests/unit/test_guide_facts_m3_unit.py).
SOURCE_REF_FORMS = frozenset({"meeting", "mail", "calendar", "doc", "todo"})


def source_ref_word(source_ref: str | None) -> str | None:
    """What stands before the first ":" of `source_ref`, with the white space at its ends
    dropped and letter case ignored (str.casefold), so "Mail:", " mail:" and "mail :" are
    "mail"; None when there is no colon."""
    if not source_ref or ":" not in source_ref:
        return None
    return source_ref.split(":", 1)[0].strip().casefold()


def source_ref_form(source_ref: str | None) -> str | None:
    """The recipe form of `source_ref`, one of SOURCE_REF_FORMS, or None (source_ref_word's
    rule: no colon, or another word, is no form)."""
    word = source_ref_word(source_ref)
    return word if word in SOURCE_REF_FORMS else None


def withheld_forms() -> frozenset[str]:
    """The words a `source_ref` that keeps its note from a model starts with: the recipe
    forms (SOURCE_REF_FORMS) and the `ref_form` of every withheld kind in KINDS, read now."""
    own = {kind.ref_form for kind in KINDS.values() if kind.withheld and kind.ref_form}
    return SOURCE_REF_FORMS | own


def withheld_by_ref(via: str | None, source_ref: str | None) -> bool:
    """Whether a note written via `via` is kept from a model by its `source_ref` (M3): its
    word (source_ref_word) is one of withheld_forms(), and no kind in KINDS writes via `via`.
    A note one of the server's own kinds wrote is judged by that kind alone (M2, decision 90):
    Granola's notes carry "meeting:granola/<id>" and are sent, as they were. KINDS is read
    now."""
    if source_ref_word(source_ref) not in withheld_forms():
        return False
    return all(kind.via != via for kind in KINDS.values())


# A bare word, after an optional "e-": letters only ([^\W\d_] is a letter in any script),
# then a colon (blanks before it allowed, as source_ref_word drops them) or nothing more.
_BARE_WORD = re.compile(r"(e-)?([^\W\d_]+)\s*(?::.*)?", re.DOTALL)


def near_miss(source_ref: str | None) -> str | None:
    """The form `source_ref` comes close to without having it, or None. The web form asks
    about it before storing (R1.5); REST and MCP do not. The forms are withheld_forms():
    the recipe's (SOURCE_REF_FORMS) and the withheld kinds' own words, the same list the
    form's hint names.

    The rule: a source whose word (source_ref_word) is one of the forms as written is no
    near miss. Otherwise it is one when, with every Unicode format character (category Cf,
    such as a zero-width space) removed, the blanks at its ends dropped and the rest
    case-folded,
    - its word is one of the forms, which is then the form it is close to; or
    - it begins with a bare word followed by a colon, or is that word alone, and the word
      is a form, with or without an "e" or "e-" before it and an "s" after it ("email",
      "e-mail", "meetings", "docs", "todos"). A word followed by anything else is none:
      "meeting with Bo", "todo list" and "docs/x.md" are neither.
    So "ma<zero-width space>il:x", "email:x" and "mail" are near misses of "mail";
    "mail:x" and " MAIL :x" are withheld as written; "https://example.test/mail" and
    "session:abc" are neither."""
    forms = withheld_forms()
    if not source_ref or source_ref_word(source_ref) in forms:
        return None
    cleaned = "".join(c for c in source_ref if unicodedata.category(c) != "Cf")
    cleaned = cleaned.strip().casefold()
    word = source_ref_word(cleaned)
    if word in forms:
        return word
    bare = _BARE_WORD.fullmatch(cleaned)
    if bare is None:
        return None
    hyphened, letters = bare.groups()
    words = [letters] if hyphened else [letters, letters.removeprefix("e")]
    for stem in (stem for w in words for stem in (w, w.removesuffix("s"))):
        if stem in forms:
            return stem
    return None


__all__ = [
    "CHOOSING",
    "HELD_CREDENTIALS",
    "HELD_VIA",
    "ITEM_MAX",
    "KINDS",
    "OVERLAP",
    "OVERLAP_ON",
    "SECRET_ENCODED_MIN",
    "SOURCE_LOGGER",
    "SOURCE_REF_FORMS",
    "Choosing",
    "Clock",
    "Held",
    "NoteDraft",
    "Pulled",
    "Skip",
    "Source",
    "SourceError",
    "SourceKind",
    "SyncOutcome",
    "SyncReport",
    "config_from",
    "holds_secret",
    "kept_error",
    "marks",
    "near_miss",
    "overlaps",
    "preview_source",
    "run_source",
    "source_client",
    "source_ref_form",
    "source_ref_word",
    "sync_all",
    "sync_forever",
    "withheld_by_ref",
    "withheld_forms",
    "withheld_via",
]
