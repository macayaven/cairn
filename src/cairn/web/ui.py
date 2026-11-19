"""The web UI: its routes, the page view of a compiled page, keys, the onboarding pages, and
login with a key and logout. The forms that write (notes and projects) are in
cairn.web.compose, and the guided setup (/start) in cairn.web.start.

Sources (S2; frozen by tests/acceptance/test_s2_source_switch.py). GET /sources opens for
whoever reaches the whole workspace (cairn.web.workspace.may_see_sources; 403 for a narrowed
session) and shows each source the viewer handles (Store.list_sources: every one, whatever it
writes into, to owners and admins; to a member, those of their own account, R4, and with none
only NOTHING_READ and NO_ACCOUNT, no form) as a section: its state in words
("On since <when>" or "Off"), where it writes as a Scope (the projects the viewer reaches by
name, the others as one row, "a project you do not reach"), what it reads (in
the words of its kind, SourceKind.describe; no such fact for a kind that does not describe
itself), its last run, and the forms that apply. An off source has one form, which posts to
/integrations/<name>/on and says what switching on reads, as the kind says it
(SourceKind.switching_on: "only what happens from then on" for a kind that starts off).
For a source that names no project, of a kind with a default project (SourceKind.default_project),
the form ticks a project of that name the viewer reaches, else fills it in as the new project's
name; only the viewer's own projects are looked at, so it tells nothing of others. The forms
offer what Store.enable_source allows the viewer: an owner (or a session acting for no
person) every project they reach and a new project; an admin only the projects every member
reaches (the project's rule gives everyone "read" or "write"), and no new project. An
on source has a quiet button that posts to /integrations/<name>/off and, under "Change
where it writes", a form that posts to /integrations/<name>/on with other projects. A source
that writes into a project an admin does not reach offers that admin only the switch off
(Store.enable_source refuses the rest: OWNER_DECIDES). No
credential and no environment variable's value is shown. The switches live under
/integrations/, never under /sources, which only answers GET (decision 25: the web view
adds no source from a config; it makes one from an account the operator registered, R2).

The page for a person who has never seen a command line (R2, section 5 of the contract):
on top, NOTHING_READ; then "Your accounts" (the credentials the viewer may make sources
from: their own and the workspace's, each with its service, whose, the account's address
when known, and how many sources read through it); then each source with its first line in
words ("From your Gmail: messages labelled “to-team” → into decisions"). A source made from
an account names no variable, no address and no subject anywhere on the page
(cairn.web.rest._hidden); one added on the command line is said in its kind's words, as
before. A source made from another person's account offers the viewer only the switch off.
Last, "Choose something new": choosing the account is a GET of this page with
`?account=<name>` (no script: the kind's fields depend on the account), which shows a form
posting to /integrations/new with that kind's selector fields (CHOOSE_FIELDS), where it
writes, a Preview button (R4: what saving would bring, before saving) and two buttons, "Save,
off" and "Save and switch on". A member's own source offers the projects they manage and a
new private one, as their account's form does.
- POST /integrations/new: `credential`, `name`, `select_<setting>` for each of the kind's
  selector settings (blank: left out; Linear's teams separated by commas), `project` or
  `new_project`, `days`, and `then` ("on" switches it on once saved; "preview" saves
  nothing and answers 200 with the page, the form still filled and the result above its Save
  buttons, cairn.web.rest.preview_unsaved). Store.add_source_from, then
  Store.enable_source for "on". 303 to /sources?saved=<name>#source-<name>-name; 400 or 403
  with the page filled again with what was typed, the refusal on top (id "refused") naming
  its field, which points at it with aria-describedby; 404 for an account out of sight.
- The four endings are said beside their buttons before they are done (SWITCH_OFF_SAYS,
  REMOVE_SAYS, DISCONNECT_SAYS, FORGET_SAYS, in the sources guide's words). POST
  /integrations/<name>/remove and POST /integrations/accounts/<name>/disconnect ask first:
  without `confirm` they answer the question (source_end.html: what it does, and the name to
  type, as a project's delete asks); with the name, Store.remove_source or
  Store.remove_credential and 303 to /sources; another name, the question again, 400. 404
  for no such source or account in sight (for a member, anything not of their own
  account); 403 for a member with no account, and in the store's words for whoever may not
  (a member's own account, which an owner disconnects).
- POST /integrations/<name>/on, for owners, admins and a member's own source (a web
  session, same-origin, as every form): `project` (repeatable: names of existing projects;
  blanks are left out) and `new_project` (optional: the name of a project to make, private,
  of the general kind, made by the person who posted). The source is switched on with the
  ticked projects, plus the new one, or with the projects it names when neither is given
  (Store.enable_source, one write: the new project is made only when the switch works). 303
  to /sources; 400 with the reason on /sources when the kind or the store refuses (a taken
  or invalid new name, no project, a project that does not exist or that the person may
  only read, an admin's new project or private project: OWNER_DECIDES); 404 for no such
  source; 403 for a member with no account.
- POST /integrations/<name>/off switches it off (Store.disable_source): 303 to /sources; 404
  for no such source; 403 for a member with no account.
- POST /integrations/<name>/preview (R1): `days` (optional, for an off source). 200 with the
  Sources page, the preview (cairn.web.rest.previewed) in the source's section, in a region
  labelled by its heading and marked aria-live="polite"; the form posts to the heading's
  fragment, so the browser moves to it. 400, 403 or 409 with the refusal on top; 404 for no
  such source; 403 for a member with no account. Stores nothing."""

import functools
import re
from typing import Any
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from cairn import oauth
from cairn.compiler import render_body, section_headings
from cairn.kinds import GENERAL
from cairn.memory import (
    BadRequest,
    Conflict,
    Forbidden,
    Memory,
    NotFound,
    choose_reach,
    is_outside,
)
from cairn.plans import limit_sentences
from cairn.sources import CHOOSING, KINDS
from cairn.sources import connector as _connector
from cairn.sources import github as _github
from cairn.sources import gmail as _gmail
from cairn.sources import google_drive as _google_drive
from cairn.sources import google_meet as _google_meet
from cairn.sources import granola as _granola
from cairn.sources import linear as _linear
from cairn.sources.runner import PREVIEW_DAYS, PREVIEW_DAYS_MAX, PreviewBusy
from cairn.spans import Span
from cairn.store import AUDIT_KEEP, Page, Project
from cairn.store.audit import UNKNOWN_WRITER
from cairn.store.models import shown_metrics
from cairn.store.sources import (
    ACCOUNT_GONE,
    FORMER_MEMBER,
    HOSTED_LEFT,
    NAME_TAKEN,
    NO_PROJECT_CHOSEN,
    NOT_YOUR_ACCOUNT,
    ONE_PROJECT,
    OWNER_CANNOT_WRITE,
    NotASourceManager,
)
from cairn.store.usage import OverLimit
from cairn.web.auth import SESSION_COOKIES, SESSION_TTL, Accounts, _memory, may_manage_credentials
from cairn.web.brand import product_of
from cairn.web.components import UNREACHED_DESTINATION, destination_of, numbered_lines
from cairn.web.compose import compose_routes
from cairn.web.ratelimit import TOO_MANY_FAILURES, CredentialFailures
from cairn.web.rest import (
    _held_said,
    _hidden,
    _logged,
    _scrubbed,
    _whole_number,
    preview_unsaved,
    previewed,
)
from cairn.web.sharing import sharing_words, words_for
from cairn.web.start import start_routes
from cairn.web.templating import _page, brand_of
from cairn.web.workspace import part

UI_HITS = 30
# What ends with each ending a list offers, said on the page before it (end.html).
KEY_ENDS = (
    "Every script and agent that sends this key stops working at once, and every web session"
    " opened with it ends."
)
MEMBER_ENDS = (
    "Their sessions here end, and the agents they approved and the keys they made stop"
    " working, at once."
)
MEMBER_KEEPS = "What they wrote stays, and they leave every project's list of people."
INVITE_ENDS = "The link stops working: {email} cannot join with it."
AGENT_ENDS = "It stops working at once: its tokens are refused from then on."
SET_ENDS = "Every key and agent that reaches the set stops reaching its projects."
SET_KEEPS = "The projects stay as they are."
KEY_LABEL_MAX = 80  # characters, for a key made in the UI
_NOT_KEYS_MANAGER = (
    "Only an owner or an admin of this workspace can make or revoke its keys, or change its sets."
)
_NOT_AUDITOR = "Only an owner or an admin of this workspace can read its audit log."
_NOT_SOURCES = (  # R4: every person who reaches the whole workspace opens the page
    "A connection limited to some projects or to reading does not see this workspace's sources."
)


def _reads(source: Any) -> str | None:
    """What a source reads, as its kind describes it (SourceKind.describe), or None."""
    kind = KINDS.get(source.kind)
    return kind.describe(source.config) if kind is not None and kind.describe else None


def _switching(source: Any, product: str) -> str | None:
    """What switching an off source on reads, as its kind says for its config
    (SourceKind.switching_on), naming the product as `product`."""
    kind = KINDS.get(source.kind)
    return kind.switching_on(product, source.config) if kind is not None else None


# R2, the page: each kind's service in plain words, as a person knows it.
SERVICES = {
    "gmail": "Gmail",
    "google_drive": "Google Drive",
    "google_calendar": "Google Calendar",
    "google_meet": "Google Meet",
    "granola": "Granola",
    "linear": "Linear",
    "github": "GitHub",
    "langfuse": "Langfuse",
    "connector": "connector",
}
NOTHING_READ = "Nothing is read until you choose what and where."
NO_ACCOUNT = "No account yet. Ask the person who runs this server to connect one."
HIDDEN_REASON = "a reason that names the account"  # what a skip reason says when hidden
HIDDEN_LINK = "its link, which names the account"  # a declined link, when hidden
DISCONNECTED_LINE = "Its account was disconnected: it reads nothing and cannot be switched on again"
# R3: said once under "Held", for a source whose last run held items another source selects too.
HELD_SAID = (
    "These carry what another of your sources reads too, so neither wrote them. Take one of"
    " the two labels off, or switch one source off, and the next run writes each once."
)
REMOVED_THERE = "The person who runs this server removes it there."  # a command-line source
# The four endings (contract 2.6), each said beside its button before it is done, in the
# sources guide's words.
SWITCH_OFF_SAYS = (
    "Switch off: it reads nothing until switched on; it keeps its place, its record of items"
    " and its notes; switched on again it reads nothing from while it was off."
)
REMOVE_SAYS = (
    "Remove the source: it reads nothing more; it forgets which items it wrote, so a source"
    " made again starts from its own switch-on; its notes stay where they are."
)
PREVIEW_SAYS = (
    "Preview: it reads once and shows how many items it would bring, and their titles. It"
    " stores nothing. Off, it reads the days you give; on, what its next run would bring."
)
DISCONNECT_SAYS = (
    "Disconnect the account: its sources are switched off and stay listed, off; their notes stay."
)
FORGET_SAYS = (
    "To take back what came in: one note at a time, from the page that cites it, or the whole"
    " project. Nothing takes back all of a source's notes at once."
)
CHANGE_SAYS = (
    "Change where it writes: its next notes go where you choose; what it wrote before stays"
    " where it is."
)
STOPPED = (OWNER_CANNOT_WRITE, ACCOUNT_GONE)  # why the store switches a source off by itself
_GITHUB_ITEMS = {"pulls": "pull requests", "issues": "issues", "releases": "releases"}
# What a person types to choose what a source of each kind reads (CHOOSING's selector): the
# setting, its label and a hint, in plain words. Nothing is pre-filled. A kind with none here
# has nothing to choose (the connector's feed is its choice).
CHOOSE_FIELDS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "gmail": (
        (
            "label",
            "The Gmail label on the messages it should read",
            "One label you made, such as to-team. Gmail's own labels, such as INBOX, are refused.",
        ),
    ),
    "google_drive": (
        ("folder_id", "The Google Drive folder", "The last part of the folder's address."),
    ),
    "google_calendar": (
        ("calendar_id", "The calendar", 'Its ID, or "primary" for your main calendar.'),
    ),
    "google_meet": (
        (
            "meeting_code",
            "The meeting's code, from its address: abc-mnop-xyz",
            "With or without the dashes. Each time it is held, its transcript is read.",
        ),
    ),
    "linear": (
        ("teams", "Linear teams", "Their keys, separated by commas, such as ENG, OPS."),
        ("linear_project", "Or one Linear project", "Its ID, in place of teams."),
    ),
    "github": (
        (
            "repo",
            "The GitHub repository",
            "As owner/name. It reads its merged pull requests, closed issues and releases.",
        ),
    ),
    "granola": (
        ("folder_id", "One Granola folder", "Its ID."),
        ("space_id", "Or one Granola space", "Its ID, in place of a folder."),
    ),
    "langfuse": (
        ("score_name", "The score that marks a trace", "Its name. Left empty: the usual one."),
    ),
}
_LIST_FIELDS = {("linear", "teams")}  # typed as words separated by commas
# The reading options a person may set (CHOOSING's options), under "More choices": a number
# with the kind's own range (RANGES: its default, least and most), or a checkbox whose tick
# sends CHECKED_MEANS. Left empty or unticked, nothing is sent and the kind's default holds.
OPTION_FIELDS: dict[str, tuple[str, str]] = {
    "settle_minutes": ("number", "Minutes an item stays unchanged before it is read"),
    "settle_hours": ("number", "Hours a document stays unchanged before it is read"),
    "wait_hours": ("number", "Hours after a meeting ends that its transcript is looked for"),
    "lookback_days": ("number", "Days it looks back"),
    "backfill_days": ("number", "Days before switching on that it also reads"),
    "limit": ("number", "Items in one answer of the feed, at most"),
    "existing": ("checkbox", "Also read the documents already in the folder"),
    "transcripts": ("checkbox", "With their transcripts"),
    "comments": ("checkbox", "Without their comments"),
}
CHECKED_MEANS = {"existing": True, "transcripts": True, "comments": False}
RANGES: dict[tuple[str, str], tuple[int, int, int]] = {
    ("granola", "settle_minutes"): _granola.SETTLE_MINUTES,
    ("granola", "lookback_days"): _granola.LOOKBACK_DAYS,
    ("google_drive", "settle_hours"): _google_drive.SETTLE_HOURS,
    ("google_meet", "wait_hours"): _google_meet.WAIT_HOURS,
    ("gmail", "lookback_days"): _gmail.LOOKBACK_DAYS,
    ("linear", "settle_minutes"): _linear.SETTLE_MINUTES,
    ("linear", "lookback_days"): _linear.LOOKBACK_DAYS,
    ("linear", "backfill_days"): _linear.BACKFILL_DAYS,
    ("github", "settle_minutes"): _github.SETTLE_MINUTES,
    ("github", "lookback_days"): _github.LOOKBACK_DAYS,
    ("github", "backfill_days"): _github.BACKFILL_DAYS,
    ("connector", "limit"): _connector.LIMIT,
}
GITHUB_ITEMS = (
    ("pulls", "Pull requests merged"),
    ("issues", "Issues closed"),
    ("releases", "Releases published"),
)  # GitHub's `items`, a selector shown under "More choices": none ticked reads all three
MORE_FIELD = "More choices"
WHAT_FIELD, WHERE_FIELD, NAME_FIELD = "What it reads", "Where it writes", "Its name"
ACCOUNT_FIELD = "Which account"


def _days_typed(form: Any) -> object:
    """A preview form's `days`: None when left empty, a whole number when it is one, else as
    typed, for cairn.sources.runner.preview_days to refuse in its words."""
    typed = form.get("days")
    days: object = typed.strip() if isinstance(typed, str) and typed.strip() else None
    if isinstance(days, str) and days.isdigit():
        days = int(days)
    return days


def _options(kind: str, form: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """The reading options a person set on the form, as add_source_from takes them (blanks
    and unticked boxes left out; a whole number as a number, anything else as typed, for the
    kind to refuse in its words), and what they set, to show again."""
    options: dict[str, Any] = {}
    typed: dict[str, Any] = {}
    for setting in CHOOSING[kind].options if kind in CHOOSING else ():
        shape = OPTION_FIELDS.get(setting, ("number", ""))[0]
        value = form.get(f"option_{setting}")
        value = value.strip() if isinstance(value, str) else ""
        if shape == "checkbox":
            typed[setting] = bool(value)
            if value:
                options[setting] = CHECKED_MEANS.get(setting, True)
        elif value:
            typed[setting] = value
            options[setting] = int(value) if value.lstrip("-").isdigit() else value
    return options, typed


def _chosen(kind: str, form: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """The selector a person typed on the form, as add_source_from takes it (blanks left
    out, a list where the kind takes one), and what they typed, to show again."""
    select: dict[str, Any] = {}
    typed: dict[str, Any] = {}
    if kind == "github":
        ticked = [item for item in form.getlist("select_items") if isinstance(item, str)]
        typed["items"] = ticked
        if ticked:
            select["items"] = ticked
    for setting, _, _ in CHOOSE_FIELDS.get(kind, ()):
        value = form.get(f"select_{setting}")
        value = value if isinstance(value, str) else ""
        typed[setting] = value
        if not value.strip():
            continue
        if (kind, setting) in _LIST_FIELDS:
            select[setting] = [word.strip() for word in value.split(",") if word.strip()]
        else:
            select[setting] = value.strip()
    return select, typed


def _refused_setting(refused: Exception, kind: str) -> str | None:
    """The reading option (or GitHub's `items`) a refusal names, if any: its field is under
    "More choices"."""
    said = str(refused)
    named = (*(CHOOSING[kind].options if kind in CHOOSING else ()), "items")
    return next((setting for setting in named if re.search(rf"\b{setting}\b", said)), None)


def _refused_field(refused: Exception, kind: str = "") -> str:
    """Which part of the new-source form a refusal is about: its name, where it writes, one
    of "More choices", or what it reads (everything else the kind refuses)."""
    said = str(refused)
    if _refused_setting(refused, kind):
        return MORE_FIELD
    if said == NAME_TAKEN or said.startswith(("a source name", "source name")):
        return NAME_FIELD
    if said == NOT_YOUR_ACCOUNT:
        return ACCOUNT_FIELD
    where = (
        NO_PROJECT_CHOSEN,
        ONE_PROJECT,
        "this workspace has no project named",
        "invalid project name",
        "project name",
        "project names",
    )
    if isinstance(refused, PermissionError) or said.startswith(where):
        return WHERE_FIELD
    return WHAT_FIELD


def _listed(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _selected(kind: str, config: dict[str, Any]) -> str | None:
    """What a source reads, from its selector alone (never an account's own settings), in
    words: the part after "From ...:" in its first line. None for a kind it cannot say."""
    if kind == "gmail" and config.get("label"):
        return f"messages labelled “{config['label']}”"
    if kind == "google_drive" and config.get("folder_id"):
        return f"documents put in the folder {config['folder_id']}"
    if kind == "google_calendar" and config.get("calendar_id"):
        if config["calendar_id"] == "primary":
            return "every event on the main calendar"
        return f"events on the calendar {config['calendar_id']}"
    if kind == "google_meet" and config.get("meeting_code"):
        return f"transcripts of the meeting {config['meeting_code']}"
    if kind == "google_meet":
        return "meeting transcripts"
    if kind == "linear" and config.get("teams"):
        return f"issues finished in the teams {', '.join(config['teams'])}"
    if kind == "linear" and config.get("linear_project"):
        return f"issues finished in the Linear project {config['linear_project']}"
    if kind == "github" and config.get("repo"):
        items = [_GITHUB_ITEMS.get(item, item) for item in config.get("items", _GITHUB_ITEMS)]
        return f"{_listed(items)} of {config['repo']}"
    if kind == "granola" and config.get("folder_id"):
        return f"notes in the folder {config['folder_id']}"
    if kind == "granola" and config.get("space_id"):
        return f"notes in the space {config['space_id']}"
    if kind == "langfuse":
        score = config.get("score_name")
        return f"traces marked with the score “{score}”" if score else "marked traces"
    if kind == "connector":
        return "what its feed hands over"
    return None


def _whose(owner: str | None, owner_name: str | None, me: str | None) -> str:
    """Whose an account is, as the first line of its source says it ("From your Gmail")."""
    if owner is None:
        return "the workspace's"
    if owner == me:
        return "your"
    return f"{owner_name}'s" if owner_name else f"{FORMER_MEMBER}'s"


def _account_rows(store: Any) -> list[dict[str, Any]]:
    """The accounts the viewer may make sources from (R2): their own, and the workspace's
    (every one, for a view acting for no person), each in words, with whether the viewer
    may disconnect it (Store.remove_credential's rule)."""
    me = store.user_id
    owner_of_workspace = me is None or store.role() == "owner"
    manages = store.manages()  # R4: a member's own account is an owner's to disconnect
    rows = []
    for held in store.list_credentials():
        if not (me is None or held.owner is None or held.owner == me):
            continue
        whose = "Yours" if held.owner is not None and held.owner == me else "The workspace's"
        if held.owner is not None and held.owner != me:
            whose = held.owner_name or FORMER_MEMBER
        rows.append(
            {
                "name": held.name,
                "kind": held.kind,
                "mine": held.owner is not None and held.owner == me,
                "of": _whose(held.owner, held.owner_name, me),
                "service": SERVICES.get(held.kind, held.kind),
                "whose": whose,
                "account": held.account,
                "sources": held.sources,
                "disconnects": owner_of_workspace or (manages and held.owner == me),
                "refused": CHOOSING[held.kind].refused if held.kind in CHOOSING else None,
            }
        )
    return rows


def _other_accounts(store: Any) -> list[dict[str, Any]]:
    """Other people's accounts in the workspace, for an owner only: each person's own account
    the viewer may disconnect (Store.remove_credential) and never makes a source from. Its
    service, whose it is and how many sources read through it; never its address."""
    if store.user_id is None or store.role() != "owner":
        return []
    return [
        {
            "name": held.name,
            "service": SERVICES.get(held.kind, held.kind),
            "whose": held.owner_name or FORMER_MEMBER,
            "sources": held.sources,
        }
        for held in store.list_credentials()
        if held.owner is not None and held.owner != store.user_id
    ]


def _new_source_form(
    memory: Memory,
    accounts: list[dict[str, Any]],
    chosen: str | None,
    typed: dict[str, Any],
    invalid: str | None,
    product: str,
) -> dict[str, Any] | None:
    """The form that makes a source from the account `chosen` (one of `accounts` a source
    can be made from), or None while none is chosen: its kind's fields, the projects the
    viewer may send it into (Store.add_source_from's rule: into a project they manage from
    their own account; else as Store.enable_source offers it), whether they may make a new
    one, and what switching it on reads. `typed` fills it again after a refusal."""
    if not chosen:
        return None
    account = next(
        (
            row
            for row in accounts
            if row["name"].casefold() == chosen.casefold() and not row["refused"]
        ),
        None,
    )
    if account is None:
        return None
    store = memory.store
    decides = store.user_id is None or store.role() == "owner"
    reached = store.list_projects()
    if account["mine"]:
        offered = [b for b in reached if decides or _sees_rule(memory, b)]
    elif decides:
        offered = reached
    else:
        offered = [b for b in reached if store.project_sharing(b.id).everyone != "none"]
    select = typed.get("select", {})
    fields = [
        {
            "setting": setting,
            "id": f"new-select-{setting}",
            "label": label,
            "hint": hint,
            "value": select.get(setting, ""),
        }
        for setting, label, hint in CHOOSE_FIELDS.get(account["kind"], ())
    ]
    kind = KINDS.get(account["kind"])
    given = typed.get("options", {})
    setting = typed.get("refused_setting")
    options = []
    for name in CHOOSING[account["kind"]].options if account["kind"] in CHOOSING else ():
        shape, label = OPTION_FIELDS.get(name, ("number", name))
        usual, least, most = RANGES.get((account["kind"], name), (None, None, None))
        options.append(
            {
                "setting": name,
                "id": f"new-option-{name}",
                "type": shape,
                "label": label,
                "min": least,
                "max": most,
                "placeholder": usual,
                "value": given.get(name, "" if shape == "number" else False),
                "invalid": setting == name,
            }
        )
    items = None
    if account["kind"] == "github":
        ticked = select.get("items", [])
        items = [
            {"value": value, "label": label, "checked": value in ticked}
            for value, label in GITHUB_ITEMS
        ]
    return {
        "options": options,
        "work": items,
        "work_invalid": setting == "items",
        "more_open": invalid == MORE_FIELD,
        "account": account,
        "legend": f"{WHAT_FIELD} from {account['of']} {account['service']}",
        "fields": fields,
        "projects": offered,
        "makes": decides or account["mine"],
        "switching": kind.switching_on(product) if kind is not None else None,
        "name": typed.get("name", ""),
        "days": typed.get("days", ""),  # a preview before saving (R4): the days typed
        "project": (typed.get("project") or "").casefold(),
        "new_project": typed.get("new_project", ""),
        "invalid": invalid,
    }


def _source_rows(store: Any, found: list[Any], product: str) -> dict[str, dict[str, Any]]:
    """For each source, by name: its first line in words, what it reads and what switching it
    on reads, its last error, and which of its buttons the viewer gets. A source made from an
    account names no variable, no address and no subject (cairn.web.rest._hidden); one added
    on the command line is said as today, in its kind's words. A source made from another
    person's account offers the viewer only the switch off; one whose account was
    disconnected stays off, and can only be removed."""
    me = store.user_id
    made_from = store.source_credentials()
    held = {info.name.casefold(): info for info in store.list_credentials()}
    rows: dict[str, dict[str, Any]] = {}
    for source in found:
        key = source.name.casefold()
        linked = key in made_from
        account = held.get((made_from.get(key) or "").casefold()) if linked else None
        hidden = _hidden(source.kind, source.config) if linked else []
        projects = list(source.config.get("projects", []))
        if source.unreached:
            projects.append(UNREACHED_DESTINATION)
        whose = _whose(account.owner, account.owner_name, me) if account else "the workspace's"
        service = SERVICES.get(source.kind, source.kind)
        what = _selected(source.kind, source.config)
        gone = linked and account is None
        first = f"From {service}" if gone else f"From {whose} {service}"
        others = account is not None and account.owner is not None and me not in (
            None, account.owner
        )  # fmt: skip
        line = f"{first}{f': {what}' if what else ''} → into " + (
            ", ".join(projects) if projects else "no project yet"
        )
        error = _scrubbed(source.last_error, hidden) if source.last_error else None
        report = source.last_report or {}
        rows[source.name] = {
            # R3: what its last run held, as far as the viewer reaches (list_sources trims it):
            # a title links only to an address the web opens, as a declined draft's does.
            "held": [
                {"title": d["title"], "href": d["link"] if _web_link(d["link"]) else None}
                for d in report.get("held_items", [])
            ],
            "held_more": report.get("held_more", 0),
            "held_expired": _held_said(report.get("held_expired", 0)),
            "first": f"{line}. {DISCONNECTED_LINE}." if gone else line,
            # Switched off by the store, with its reason (a run it refused): said next to "Off".
            "stopped": error if not source.enabled and source.last_error in STOPPED else None,
            "reads": _scrubbed(_reads(source), hidden),
            "switching": _scrubbed(_switching(source, product), hidden),
            "error": error,
            "said": _scrubbed((source.last_report or {}).get("said"), hidden),
            # What it skipped and declined, hidden as its error is: no address of its account.
            "reasons": _reasons(report.get("reasons") or {}, hidden),
            "declined": [_declined(d, hidden) for d in report.get("declined", [])],
            "hidden": hidden,
            "routes": not others and not (linked and account is None),
            "disconnected": linked and account is None,
            "linked": linked,  # made from an account: a person may remove it (Store.remove_source)
            # The viewer's own account (R4: a member's too): routed into what they manage.
            "mine": account is not None and me is not None and account.owner == me,
        }
    return rows


def _reasons(reasons: dict[str, int], hidden: list[tuple[str, str]]) -> dict[str, int]:
    """A last report's skip reasons with the account's settings said in words (_scrubbed);
    two that read the same once hidden are counted together."""
    said: dict[str, int] = {}
    for reason, count in reasons.items():
        shown = _scrubbed(reason, hidden) or HIDDEN_REASON
        said[shown] = said.get(shown, 0) + count
    return said


def _declined(item: dict[str, Any], hidden: list[tuple[str, str]]) -> dict[str, Any]:
    """A declined draft as the page shows it: its link in words when it names the account's
    address, and a link to follow only when it names none."""
    link = item.get("link", "")
    shown = _scrubbed(link, hidden) or HIDDEN_LINK
    href = link if shown == link and _web_link(link) else None
    codes = item.get("codes", [])
    return {"text": shown, "href": href, "status": item.get("status"), "codes": codes}


def _web_link(link: object) -> bool:
    """Whether a held item's link is an address a browser opens (a kind's own reference, such
    as "gmail:<id>", is not)."""
    return isinstance(link, str) and link.startswith(("https://", "http://"))


def _proposed(source: Any, reached: list[Project], decides: bool = True) -> dict[str, Any]:
    """What the switch-on form proposes for a source: the projects ticked (casefolded) and
    the new project's name. A source that names projects ticks them; one that names none, of a
    kind that proposes a project (SourceKind.proposed_project: its default project, or the
    source's own name), ticks a project of that name the viewer reaches, else proposes the
    name as a new project to a viewer who may make one (`decides`: an owner). Only the
    viewer's own projects are looked at, so the page never says whether a project out of their
    reach exists."""
    named = [project.casefold() for project in source.config.get("projects", [])]
    kind = KINDS.get(source.kind)
    default = kind.proposed_project(source.name) if kind is not None else None
    if named or not default:
        return {"ticked": named, "new": ""}
    if any(project.name.casefold() == default.casefold() for project in reached):
        return {"ticked": [default.casefold()], "new": ""}
    return {"ticked": [], "new": default if decides else ""}


# The counts of a source's last run, in the order /sources shows them.
SOURCE_COUNTS = ("saved", "unchanged", "refused", "rejected", "skipped")
AUDIT_SHOWN = 200  # events on /audit, newest first
WEB_PAGES_AT_ONCE = 200  # Q1: the pages a project's page lists at a time, by slug
# The limits /usage shows, in order, and what a person reads for each.
USAGE_NAMES = (
    ("notes_per_month", "Notes written this month"),
    ("storage_bytes", "Notes stored"),
    ("passages", "Passages in notes"),
    ("projects", "Projects"),
    ("model_budget_micros", "Model use this month"),
)


def _lines(span: Span) -> str:
    if span.start_line == span.end_line:
        return f"line {span.start_line}"
    return f"lines {span.start_line}\N{EN DASH}{span.end_line}"


# What the gates (cairn.gate) and the arranger record about a version, in words, by the name
# callers meet (cairn.store.models.SHOWN_METRICS); a name without one here shows as it is.
CHECKS = {
    "claims": "Claims",
    "passages_cited": "Source passages cited",
    "retention": "Facts of the sources kept",
    "code_line_retention": "Code lines of the sources kept",
    "passage_coverage": "Source passages accounted for",
    "omission_rate": "Facts marked as left out",
    "arranged_by": "Arranged by",
}


def _metric(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    return str(value)


def writer_words(kind: str, name: str | None) -> str:
    """Who wrote a note, in words, with the kind as a word (A1: the page's sentence under
    each source). A person by name (never an address: the store's writer is already
    audit.person_name's), a key and an agent by label, a source by name ("from a source"
    when the name did not travel, as after an import); the operator's command line in words
    of its own; a note stored before writers were recorded: "writer not recorded"."""
    if kind == "person":
        return f"written by {name}" if name else "written by a person"
    if kind == "key":
        return f"written by the key {name}"
    if kind == "agent":
        return f"written by the agent {name}"
    if kind == "source":
        return f"from the source {name}" if name else "from a source"
    if kind == "operator":
        return "added on the server's command line"
    return "writer not recorded"


def page_view(memory: Memory, project: Project, page: Page) -> dict[str, Any]:
    """The claims in the body's order, each under the section headings the body gives it
    (compiler.section_headings) and linked to numbered sources; then every cited source,
    shown only when it is a span of `project`, with the numbers of the claims that cite it,
    who wrote its note (writer_words) and whether that note is outside text (A1,
    memory.is_outside); then the checks, in words (CHECKS)."""
    span_ids = list(dict.fromkeys(i for claim in page.claims for i in claim.span_ids))
    found = memory.sources(project, span_ids)
    writers = memory.store.note_writers(note.id for _, note in found.values())
    number = {span_id: n for n, span_id in enumerate(span_ids, 1)}
    cited_by: dict[str, list[tuple[str, int]]] = {span_id: [] for span_id in span_ids}
    top: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    previous: tuple[str, ...] = ()
    for n, claim in enumerate(page.claims, 1):
        cites = list(dict.fromkeys(claim.span_ids))
        for span_id in cites:
            cited_by[span_id].append((claim.id, n))
        first = next((found[i][0] for i in cites if i in found), None)
        # compiler.render_body's rule (section_headings): the claims with no section first,
        # each other under every heading of its path; `n` stays the claim's stored place.
        path = first.heading_path if first else ()
        section, headings = section_headings(page.title, path, previous)
        if section:
            previous = section
        (rest if section else top).append(
            {
                "claim": claim,
                "kind": first.kind if first else "paragraph",
                "headings": headings,
                "cites": [(span_id, number[span_id]) for span_id in cites],
            }
        )
    items = [*top, *rest]
    sources = []
    for span_id in span_ids:
        span, note = found.get(span_id, (None, None))
        kind, name, via = (
            writers.get(note.id, (UNKNOWN_WRITER, None, note.via))
            if note
            else (UNKNOWN_WRITER, None, None)
        )
        sources.append(
            {
                "writer": writer_words(kind, name),
                "outside": note is not None and is_outside(kind, via),
                "id": span_id,
                "n": number[span_id],
                "span": span,
                "note": note,
                "lines": _lines(span) if span else "",
                "numbered": numbered_lines(span, note) if span else [],
                "cited": span.end_line - span.start_line + 1 if span else 0,
                "cited_by": cited_by[span_id],
            }
        )
    by_id = {source["id"]: source for source in sources}
    for item in items:  # each claim's proof: its own sources, in the order it cites them
        item["proof"] = [by_id[span_id] for span_id, _ in item["cites"]]
        item["proof_lines"] = sum(source["cited"] for source in item["proof"])
    metrics = [
        (name, CHECKS.get(name, name), _metric(value))
        for name, value in shown_metrics(page.metrics).items()
    ]
    return {
        "items": items,
        # The Markdown body as REST and MCP give it: rendered when read, never the stored text.
        "body": render_body(page.title, page.claims, {i: span for i, (span, _) in found.items()}),
        "sources": sources,
        "metrics": metrics,
        "lines_cited": sum(source["cited"] for source in sources),
        "notes": len({note.id for _, note in found.values()}),
    }


def _sees_rule(memory: Memory, project: Project) -> bool:
    """Whether /b/<project>/access opens for the caller: the question that page asks the store
    (Store.project_access, PermissionError for whoever is neither the maker, nor an owner, nor
    at "manage" on the project)."""
    try:
        memory.store.project_access(project.id)
    except (PermissionError, KeyError):
        return False
    return True


def _ui(request: Request, name: str, status_code: int = 200, **context: Any) -> Response:
    """A page for the signed-in caller: the app's navigation, naming the person when one
    signed in."""
    person = _memory(request).person
    return _page(
        name, status_code, brand=brand_of(request), person=person, signed_in=True, **context
    )


def _ended(
    request: Request,
    *,
    title: str,
    trail: tuple[tuple[str, str], ...],
    said: tuple[str, ...],
    action: str,
    button: str,
    back: str,
    keep: str = "No, keep it",
) -> Response:
    """The page before an ending a list offers (end.html): `title`, what ends (`said`), the
    one form, posting to `action`, and the way back to the list at `back`, said as `keep`."""
    return _ui(
        request, "end.html", title=title, trail=trail, said=said, action=action,
        button=button, back=back, keep=keep,
    )  # fmt: skip


def ui_routes(public_url: str | None, *, sign_in: bool = False) -> list[Route]:
    """`sign_in`: people sign in through a provider, so the pages speak to them, not to the
    operator of a homelab."""
    app_url = public_url.rstrip("/") if public_url else "https://<your-host>"
    mcp_url = f"{app_url}/mcp"
    by_url = oauth.issuer(public_url) is not None

    def home(request: Request) -> Response:
        """The projects, each with its page count and how far it is shared: a fixed number of
        reads whatever the number of projects (Q1)."""
        memory = _memory(request)
        projects = memory.projects()["projects"]
        sharing = memory.store.projects_sharing(project["id"] for project in projects)
        me = memory.store.user_id
        shared = {project["id"]: words_for(sharing.get(project["id"]), me) for project in projects}
        blank = {"name": "", "description": "", "kind": GENERAL}
        return _ui(
            request,
            "home.html",
            projects=projects,
            shared=shared,
            project_form=blank,
            project_error=None,
        )

    def project(request: Request) -> Response:
        """A project's pages, WEB_PAGES_AT_ONCE at a time by slug (Q1): the part after the slug
        `after`, how many pages there are in all, and a link to the next part."""
        memory = _memory(request)
        target = memory.project(request.path_params["project"])
        after = request.query_params.get("after") or None
        part = memory.pages_part(target, None, after, WEB_PAGES_AT_ONCE)
        return _ui(
            request,
            "project.html",
            project=target,
            notes=memory.store.count_notes(target.id),
            pages=part["pages"],
            pages_total=part.get("pages_total", len(part["pages"])),
            pages_next=part.get("pages_next"),
            after=after,
            rule=_sees_rule(memory, target),
            erase=memory.may_erase(target),
            sharing=sharing_words(memory, target.id),
            # M4: whether a model may be sent its pages; whoever sees `rule` may change it
            kept=bool(memory.store.kept_from_models([target.id])),
        )

    def page(request: Request) -> Response:
        """A page, with the latest VERSIONS_AT_ONCE of its version numbers, or those below
        `before` (Q1), how many it keeps in all, and a link to the earlier ones. When older
        versions were removed (its kept versions are fewer than its latest number), it says
        it shows the last N and why."""
        memory = _memory(request)
        target = memory.project(request.path_params["project"])
        query = request.query_params  # a web page: what it does not know it ignores (X1)
        version = _whole_number(query, "version")
        before = _whole_number(query, "before")
        shown = memory.find_page(target, request.path_params["slug"], version)
        versions, total, latest, following = memory.versions_of(target, shown.slug, None, before)
        latest = latest or shown.version
        return _ui(
            request,
            "page.html",
            project=target,
            page=shown,
            versions=versions,
            versions_total=total,
            versions_next=following,
            before=before,
            trimmed=total < latest,
            latest=latest,
            erase=memory.may_erase(target),  # each source's note links to its forget page
            **page_view(memory, target, shown),
        )

    def search(request: Request) -> Response:
        memory = _memory(request)
        query = request.query_params.get("q", "").strip()
        ref = request.query_params.get("project", "")
        project = memory.project(ref) if ref else None  # resolve once; search by id, not by name
        scope = project.name if project else ""
        found = memory.recall(query, project.id if project else None, UI_HITS) if query else {}
        hits = found.get("hits", [])
        projects = memory.store.list_projects()
        return _ui(
            request,
            "search.html",
            query=query,
            scope=scope,
            projects=projects,
            hits=hits,
            searched=found.get("searched"),  # Q2: what the search left out, as recall says it
            most=UI_HITS,  # at most this many are asked for, so the page cannot count past it
        )

    def setup(request: Request) -> Response:
        return _ui(
            request,
            "setup.html",
            app_url=app_url,  # this server's own address, or a placeholder without one
            mcp_url=mcp_url,
            public_url=public_url,
            oauth=by_url,
            sign_in=sign_in,
        )

    def welcome(request: Request) -> Response:
        """Where a new person lands: how to connect an agent, and the way back to where they
        were going (`next`), when there was one."""
        target = _local_path(request.query_params.get("next"))
        return _ui(
            request,
            "welcome.html",
            mcp_url=mcp_url,
            oauth=by_url,
            next=None if target == "/" else target,
            start=not _memory(request).store.list_projects(),  # nothing yet: lead to /start
        )

    def agents(request: Request) -> Response:
        """The connected agents, each with what it reaches, by name, and what it does not
        (the projects and sets the reader sees: components.scope_of)."""
        memory = _memory(request)
        grants, names = memory.store.list_grants(), memory.reach_names()
        return _ui(
            request,
            "agents.html",
            grants=grants,
            names=names,
            sets=memory.store.list_sets(),
            projects=memory.store.list_projects(),
        )

    def key_page(memory: Memory, created: dict[str, str] | None = None) -> dict[str, Any]:
        """What keys.html shows: the keys with their reach by name, what a new key's reach
        can be chosen from, and the address an app is given with a new key."""
        return {
            "app_url": app_url,
            "keys": memory.store.list_keys(),
            "projects": memory.store.list_projects(),
            "sets": memory.store.list_sets(),
            "names": memory.reach_names(),
            "created": created,
            "manage": may_manage_credentials(memory),
            "makers": {member.user_id: member.name for member in memory.store.list_members()},
        }

    def keys(request: Request) -> Response:
        """The workspace's key labels and reach; the form and the revoke buttons only for a
        caller who may manage them."""
        return _ui(request, "keys.html", **key_page(_memory(request)))

    async def managed_store(request: Request) -> Any:
        """The caller's store, when the caller may manage the workspace's keys and sets; 403
        else."""
        memory = _memory(request)
        if not await run_in_threadpool(may_manage_credentials, memory):
            raise Forbidden(_NOT_KEYS_MANAGER)
        return memory.store

    async def create_key(request: Request) -> Response:
        """A new key for this workspace, with the reach chosen on the form (every project, to
        write, when nothing is chosen), shown on this response only (never stored as is)."""
        store = await managed_store(request)
        form = await request.form()
        label = form.get("label")
        label = label.strip() if isinstance(label, str) else ""
        if not label or len(label) > KEY_LABEL_MAX or not label.isprintable():
            raise BadRequest(f"A key's label has 1 to {KEY_LABEL_MAX} printable characters.")
        projects, access = choose_reach(
            form.get("scope"), form.getlist("projects"), form.get("access")
        )
        try:
            key = await run_in_threadpool(store.create_key, label, projects, access)
        except ValueError as error:
            raise BadRequest(f"That key cannot be made: {error}.") from None
        created = {"label": label, "key": key}
        page = await run_in_threadpool(key_page, _memory(request), created)
        return _ui(request, "keys.html", **page)

    def ask_revoke_key(request: Request) -> Response:
        """GET /keys/<label>/revoke: the page before a key is revoked (key_revoke.html), its
        one form, or both when the key made anything still live (A1). For whoever may manage
        keys (403 else); 404 for no such key. Writes nothing."""
        memory = _memory(request)
        if not may_manage_credentials(memory):
            raise Forbidden(_NOT_KEYS_MANAGER)
        label = request.path_params["label"]
        if not any(key.label == label for key in memory.store.list_keys()):
            raise NotFound(f"no key labelled {label!r}")
        made = memory.store.made_by(label)
        return _ui(request, "key_revoke.html", label=label, made=made, key_ends=KEY_ENDS)

    async def revoke_key(request: Request) -> Response:
        """Revoke the keys of a label. A1: when they made anything still live
        (Store.made_by), and the form chose neither "keep" nor "take" in `made`, a page that
        names what they made and offers both, each its own form; "take" revokes them with
        it, in one transaction (Store.revoke_key's with_made). A key that made nothing is
        revoked at once. 303 to /keys; 404 for no such key."""
        store = await managed_store(request)
        label = request.path_params["label"]
        choice = (await request.form()).get("made")
        if choice not in ("keep", "take"):
            made = await run_in_threadpool(store.made_by, label)
            if made:
                return _ui(request, "key_revoke.html", label=label, made=made, key_ends=KEY_ENDS)
        revoke = functools.partial(store.revoke_key, label, with_made=choice == "take")
        if not await run_in_threadpool(revoke):
            raise NotFound(f"no key labelled {label!r}")
        return RedirectResponse("/keys", status_code=303)

    def sets(request: Request) -> Response:
        """The workspace's sets with their projects by name; the forms only for a caller who
        may manage them (the same rule as keys)."""
        memory = _memory(request)
        return _ui(
            request,
            "sets.html",
            sets=memory.store.list_sets(),
            projects=memory.store.list_projects(),
            names=memory.reach_names(),
            manage=may_manage_credentials(memory),
        )

    async def create_set(request: Request) -> Response:
        store = await managed_store(request)
        form = await request.form()
        name = form.get("name")
        name = name.strip() if isinstance(name, str) else ""
        try:
            await run_in_threadpool(store.create_set, name, form.getlist("projects"))
        except ValueError as error:
            taken = await run_in_threadpool(store.get_set, name)  # by id or by name
            if taken is not None and taken.name.casefold() == name.casefold():
                raise Conflict(f"A set named {name!r} already exists.") from None
            raise BadRequest(f"That set cannot be made: {error}.") from None
        return RedirectResponse("/sets", status_code=303)

    async def update_set(request: Request) -> Response:
        """Replace a set's projects with the ticked ones (none ticked: an empty set)."""
        store = await managed_store(request)
        form = await request.form()
        set_id = request.path_params["set"]
        try:
            await run_in_threadpool(store.update_set, set_id, form.getlist("projects"))
        except KeyError:
            raise NotFound(f"no set {set_id!r}") from None
        except ValueError as error:
            raise BadRequest(f"That set cannot be changed: {error}.") from None
        return RedirectResponse("/sets", status_code=303)

    def ask_delete_set(request: Request) -> Response:
        """GET /sets/<id>/delete: the page before a set is deleted. For whoever may manage
        sets (403 else); 404 for no such set. Writes nothing."""
        memory = _memory(request)
        if not may_manage_credentials(memory):
            raise Forbidden(_NOT_KEYS_MANAGER)
        found = memory.store.get_set(request.path_params["set"])
        if found is None or found.id != request.path_params["set"]:
            raise NotFound(f"no set {request.path_params['set']!r}")
        return _ended(
            request,
            title=f"Delete the set {found.name}",
            trail=(("/workspace", "Workspace"), ("/sets", "Sets")),
            said=(SET_ENDS, SET_KEEPS),
            action=f"/sets/{quote(found.id, safe='')}/delete",
            button=f"Delete the set {found.name}",
            back="/sets",
        )

    async def delete_set(request: Request) -> Response:
        store = await managed_store(request)
        set_id = request.path_params["set"]
        if not await run_in_threadpool(store.delete_set, set_id):
            raise NotFound(f"no set {set_id!r}")
        return RedirectResponse("/sets", status_code=303)

    def audit(request: Request) -> Response:
        """The workspace's audit log, newest first: who wrote what, and the security events.
        For whoever may manage the workspace's keys; a web session always reaches the whole
        workspace, and a narrowed view's log would be empty anyway."""
        memory = _memory(request)
        if not part("/audit").opens(memory):
            raise Forbidden(_NOT_AUDITOR)
        events = memory.store.list_audit(AUDIT_SHOWN)
        return _ui(request, "audit.html", events=events, kept=AUDIT_KEEP)

    def sources_page(
        request: Request,
        status_code: int = 200,
        error: str | None = None,
        preview: dict[str, Any] | None = None,
        *,
        account: str | None = None,
        typed: dict[str, Any] | None = None,
        invalid: str | None = None,
        saved: str | None = None,
        unsaved: dict[str, Any] | None = None,
    ) -> Response:
        """/sources: each source with its state, where it writes and the switches (see the
        module), with `error` said on top when a switch was refused, and `preview` (R1: a
        preview's answer) in its source's section. `account`: the account chosen for a new
        source, whose kind's fields the form shows, filled with `typed` and with `invalid`
        (WHAT_FIELD, WHERE_FIELD or NAME_FIELD) pointing at the refusal. `saved`: the source
        just saved, off. `unsaved`: a preview before saving (R4), shown in the new-source form
        above its Save buttons."""
        memory = _memory(request)
        found = memory.store.list_sources()
        reached = memory.store.list_projects()  # only what the viewer reaches
        store = memory.store
        decides = store.user_id is None or store.role() == "owner"  # Store.enable_source's rule
        offered = (
            reached
            if decides
            else [b for b in reached if store.project_sharing(b.id).everyone != "none"]
        )
        product = product_of(brand_of(request)).name
        rows = _source_rows(store, found, product)
        managed = None  # the projects the viewer manages, read once when a source is theirs
        for row in rows.values():  # where each source's forms may send it (Store.enable_source)
            if row["mine"]:
                if managed is None:
                    managed = [b for b in reached if decides or _sees_rule(memory, b)]
                row["projects"], row["decides"] = managed, True
            else:
                row["projects"], row["decides"] = offered, decides
        manages = store.manages()
        if preview is not None and preview.get("said") and preview.get("source") in rows:
            hidden = rows[preview["source"]]["hidden"]
            preview = {**preview, "said": _scrubbed(preview["said"], hidden) or "it was refused"}
        accounts = _account_rows(store)
        new = _new_source_form(memory, accounts, account, typed or {}, invalid, product)
        return _page(
            "sources.html",
            status_code,
            brand=brand_of(request),
            person=memory.person,
            signed_in=True,
            sources=found,
            manages=manages,
            # R4: a member with no account and no source sees the one sentence, and no form.
            bare=not manages and not accounts and not found,
            counts=SOURCE_COUNTS,
            projects=offered,
            decides=decides,
            destinations={
                s.name: destination_of(s.config.get("projects", []), s.unreached) for s in found
            },
            rows=rows,
            accounts=accounts,
            others=_other_accounts(store),
            choosable=[row for row in accounts if not row["refused"]],
            new=new,
            saved=saved,
            nothing_read=NOTHING_READ,
            no_account=NO_ACCOUNT,
            removed_there=REMOVED_THERE,
            held_said=HELD_SAID,
            says={
                "preview": PREVIEW_SAYS,
                "off": SWITCH_OFF_SAYS,
                "remove": REMOVE_SAYS,
                "disconnect": DISCONNECT_SAYS,
                "forget": FORGET_SAYS,
                "change": CHANGE_SAYS,
            },
            proposed={s.name: _proposed(s, reached, decides) for s in found},
            error=error,
            preview=preview,
            unsaved=unsaved,
            preview_days=PREVIEW_DAYS,
            preview_days_max=PREVIEW_DAYS_MAX,
        )

    def sources(request: Request) -> Response:
        """The Sources page (see the module), for whoever may manage the workspace's keys.
        `account`: the account chosen for a new source; `saved`: the source just saved."""
        memory = _memory(request)
        if not part("/sources").opens(memory):
            raise Forbidden(_NOT_SOURCES)
        query = request.query_params  # a web page: what it does not know it ignores (X1)
        return sources_page(
            request, account=query.get("account") or None, saved=query.get("saved") or None
        )

    async def new_source(request: Request) -> Response:
        """POST /integrations/new (R2): a source made from an account (Store.add_source_from)
        with what the person typed, saved off, then switched on when `then` is "on". 303 to
        its section, with "saved"; the page again, filled with what was typed and the
        refusal on top naming its field, when the store or the kind refuses."""
        memory = _memory(request)
        if not await run_in_threadpool(part("/sources").opens, memory):
            raise Forbidden(_NOT_SOURCES)
        form = await request.form()
        credential = form.get("credential")
        credential = credential if isinstance(credential, str) else ""
        held = {
            info.name.casefold(): info
            for info in await run_in_threadpool(memory.store.list_credentials)
        }
        if credential.casefold() not in held:
            raise NotFound(f"no account named {credential!r}")
        kind = held[credential.casefold()].kind
        select, shown = _chosen(kind, form)
        options, set_options = _options(kind, form)
        name, project, new = (
            value.strip() if isinstance(value, str) else ""
            for value in (form.get("name"), form.get("project"), form.get("new_project"))
        )
        typed = {
            "name": name, "select": shown, "options": set_options, "project": project,
            "new_project": new,
        }  # fmt: skip
        make = functools.partial(
            memory.store.add_source_from, name, credential, select, options, project or None,
            new_project=new or None,
        )  # fmt: skip
        then = form.get("then")
        days = _days_typed(form)
        if then == "preview":  # R4: what saving would bring, saving nothing
            typed["days"] = days if days is not None else ""
        try:
            if then == "preview":
                shown = await preview_unsaved(
                    request, name, credential, select, options, project or None, new or None,
                    days,
                )  # fmt: skip
                page = functools.partial(
                    sources_page, request, 200, None, None, account=credential, typed=typed,
                    unsaved=shown,
                )  # fmt: skip
                return await run_in_threadpool(page)
            await run_in_threadpool(make)
        except KeyError:  # gone since the account was looked up
            raise NotFound(f"no account named {credential!r}") from None
        except PreviewBusy as busy:
            said = f"{ACCOUNT_FIELD}: {busy}."  # a preview holds the account it reads
            page = functools.partial(
                sources_page, request, 409, said, account=credential, typed=typed
            )
            return await run_in_threadpool(page)
        except OverLimit as over:
            raise memory.quota_error(over) from None
        except (ValueError, PermissionError) as refused:
            field = _refused_field(refused, kind)
            typed["refused_setting"] = _refused_setting(refused, kind)
            said = f"{field}: {refused}."
            status = 403 if isinstance(refused, PermissionError) else 400
            page = functools.partial(
                sources_page, request, status, said,
                account=credential, typed=typed, invalid=field,
            )  # fmt: skip
            return await run_in_threadpool(page)
        if then == "on":
            try:
                await run_in_threadpool(memory.store.enable_source, name)
            except (ValueError, PermissionError) as refused:
                said = f"Source {name!r} was saved, off, and not switched on: {refused}."
                return await run_in_threadpool(sources_page, request, 400, said)
            return RedirectResponse(f"/sources#source-{quote(name)}-name", status_code=303)
        return RedirectResponse(
            f"/sources?saved={quote(name)}#source-{quote(name)}-name", status_code=303
        )

    async def switch_on(request: Request) -> Response:
        memory = _memory(request)
        if not await run_in_threadpool(part("/sources").opens, memory):
            raise Forbidden(_NOT_SOURCES)
        form = await request.form()
        name = request.path_params["name"]
        ticked = [b.strip() for b in form.getlist("project") if isinstance(b, str) and b.strip()]
        typed = form.get("new_project")
        new = typed.strip() if isinstance(typed, str) and typed.strip() else None
        projects = ticked if ticked or new else None  # neither: the projects it names
        try:
            await run_in_threadpool(
                functools.partial(memory.store.enable_source, name, projects, new_project=new)
            )
        except KeyError:
            raise NotFound(f"no source named {name!r}") from None
        except OverLimit as over:  # the plan's projects, all made
            raise memory.quota_error(over) from None
        except NotASourceManager as refused:  # no source is theirs: none to switch (R4)
            raise Forbidden(str(refused)) from None
        except (ValueError, PermissionError) as refused:
            said = f"Source {name!r} was not switched on: {refused}."
            return await run_in_threadpool(sources_page, request, 400, said)
        return RedirectResponse("/sources", status_code=303)

    def asked(
        request: Request,
        ending: str,
        name: str,
        status_code: int = 200,
        error: str | None = None,
        *,
        hosted: bool = False,
    ) -> Response:
        """The question before an ending that cannot be undone from the page: what it does,
        and the name typed as a check, as a project's delete asks (cairn.web.erasure)."""
        what = {
            "remove": ("Remove the source", REMOVE_SAYS, f"/integrations/{quote(name)}/remove"),
            "disconnect": (
                "Disconnect the account",
                DISCONNECT_SAYS,
                f"/integrations/accounts/{quote(name)}/disconnect",
            ),
        }[ending]
        title, said, action = what
        if hosted:  # a Google account connected in the browser, removed with the switch off
            said = f"{said} {HOSTED_LEFT}"
        return _ui(
            request,
            "source_end.html",
            status_code,
            title=title,
            ended=name,
            said=said,
            action=action,
            error=error,
        )

    async def remove(request: Request) -> Response:
        """POST /integrations/<name>/remove: without `confirm`, the question; with the source's
        name, Store.remove_source (its notes stay) and 303 to /sources; another name, the
        question again, 400. 404 for no such source, or one the store removes nothing of
        for this view (it writes where the view does not reach), 403 for whoever may not."""
        memory = _memory(request)
        if not await run_in_threadpool(part("/sources").opens, memory):
            raise Forbidden(_NOT_SOURCES)
        name = request.path_params["name"]
        found = await run_in_threadpool(memory.store.get_source, name)
        if found is None:
            raise NotFound(f"no source named {name!r}")
        confirm = (await request.form()).get("confirm")
        if confirm is None:
            return asked(request, "remove", found.name)
        if not isinstance(confirm, str) or confirm.strip().casefold() != found.name.casefold():
            told = f"Type {found.name} to remove it: its name, as a check."
            return asked(request, "remove", found.name, 400, told)
        try:
            removed = await run_in_threadpool(memory.store.remove_source, found.name)
        except PermissionError as refused:
            raise Forbidden(str(refused)) from None
        if not removed:  # one it writes where the view does not reach: nothing changed
            raise NotFound(f"no source named {name!r}")
        return RedirectResponse("/sources", status_code=303)

    async def disconnect_account(request: Request) -> Response:
        """POST /integrations/accounts/<name>/disconnect: as remove, for an account
        (Store.remove_credential: its sources are switched off and stay listed)."""
        memory = _memory(request)
        if not await run_in_threadpool(part("/sources").opens, memory):
            raise Forbidden(_NOT_SOURCES)
        name = request.path_params["name"]
        held = {
            info.name.casefold(): info.name
            for info in await run_in_threadpool(memory.store.list_credentials)
        }
        if name.casefold() not in held:
            raise NotFound(f"no account named {name!r}")
        name = held[name.casefold()]
        confirm = (await request.form()).get("confirm")
        hosted = getattr(memory.store, "google_custody", None) is None and (
            await run_in_threadpool(memory.store.credential_hosted, name)
        )  # with the switch on, Store.remove_credential refuses it (HOSTED_DISCONNECT)
        if confirm is None:
            return asked(request, "disconnect", name, hosted=hosted)
        if not isinstance(confirm, str) or confirm.strip().casefold() != name.casefold():
            told = f"Type {name} to disconnect it: its name, as a check."
            return asked(request, "disconnect", name, 400, told, hosted=hosted)
        try:
            await run_in_threadpool(memory.store.remove_credential, name)
        except KeyError:
            raise NotFound(f"no account named {name!r}") from None
        except PermissionError as refused:
            raise Forbidden(str(refused)) from None
        if hosted:  # the switch is off (it refuses otherwise): say what is left at Google
            return _ui(request, "source_ended.html", title="Account removed", ended=name,
                       said=HOSTED_LEFT)  # fmt: skip
        return RedirectResponse("/sources", status_code=303)

    async def switch_off(request: Request) -> Response:
        memory = _memory(request)
        if not await run_in_threadpool(part("/sources").opens, memory):
            raise Forbidden(_NOT_SOURCES)
        name = request.path_params["name"]
        try:
            await run_in_threadpool(memory.store.disable_source, name)
        except KeyError:
            raise NotFound(f"no source named {name!r}") from None
        except PermissionError as refused:  # NotASourceManager: no source is theirs (R4)
            raise Forbidden(str(refused)) from None
        return RedirectResponse("/sources", status_code=303)

    async def preview(request: Request) -> Response:
        """POST /integrations/<name>/preview (R1): the Sources page with what the source would
        bring in its section, or the refusal on top."""
        memory = _memory(request)
        if not await run_in_threadpool(part("/sources").opens, memory):
            raise Forbidden(_NOT_SOURCES)
        form = await request.form()
        name = request.path_params["name"]
        days = _days_typed(form)
        try:
            said = await previewed(request, name, days)
        except (BadRequest, Forbidden, Conflict) as refused:
            told = f"Source {name!r} was not previewed: {refused}."
            return await run_in_threadpool(sources_page, request, refused.status, told)
        return await run_in_threadpool(sources_page, request, 200, None, said)

    def usage(request: Request) -> Response:
        """What the workspace has used of its plan, for whoever may see its totals
        (Memory.sees_totals: an owner, or a session opened with a key that acts for no
        person); 403 otherwise, from Memory.usage. What the plan allows is said in its own
        words (cairn.plans.limit_sentences), as a 402 says it."""
        memory = _memory(request)
        allows = list(limit_sentences(memory.plan[1]).values()) if memory.plan else []
        return _ui(request, "usage.html", usage=memory.usage(), names=USAGE_NAMES, allows=allows)

    def ask_disconnect(request: Request) -> Response:
        """GET /agents/<grant>/revoke: the page before an agent is disconnected; 404 for an
        agent the caller does not see on /agents (Store.list_grants). Writes nothing."""
        grant_id = request.path_params["grant"]
        found = next((g for g in _memory(request).store.list_grants() if g.id == grant_id), None)
        if found is None:
            raise NotFound(f"no connected agent {grant_id!r}")
        return _ended(
            request,
            title=f"Disconnect {found.client_name}",
            trail=(("/workspace", "Workspace"), ("/agents", "Connected agents")),
            said=(AGENT_ENDS,),
            action=f"/agents/{quote(found.id, safe='')}/revoke",
            button=f"Disconnect {found.client_name}",
            back="/agents",
        )

    def ask_remove_member(request: Request) -> Response:
        """GET /members/<user>/remove (its POST is cairn.web.members'): the page before a
        person is removed. For whoever manages people (403 else); 404 for no such member.
        Writes nothing."""
        from cairn.web.members import NOT_A_MANAGER  # it imports this module

        store = _memory(request).store
        if not store.manages():
            raise Forbidden(NOT_A_MANAGER)
        user_id = request.path_params["user"]
        found = next((m for m in store.list_members() if m.user_id == user_id), None)
        if found is None:
            raise NotFound(f"no member {user_id!r}")
        return _ended(
            request,
            title=f"Remove {found.name}",
            trail=(("/workspace", "Workspace"), ("/members", "Members")),
            said=(MEMBER_ENDS, MEMBER_KEEPS),
            action=f"/members/{quote(found.user_id, safe='')}/remove",
            button=f"Remove {found.name}",
            back="/members",
            keep=f"No, keep {found.name}",
        )

    def ask_revoke_invite(request: Request) -> Response:
        """GET /members/invites/<id>/revoke (its POST is cairn.web.members'): the page before
        an invitation is revoked. For whoever manages people (403 else); 404 for no open
        invitation. Writes nothing."""
        from cairn.web.members import NOT_A_MANAGER  # it imports this module

        store = _memory(request).store
        if not store.manages():
            raise Forbidden(NOT_A_MANAGER)
        invite_id = request.path_params["invite"]
        found = next((i for i in store.list_invites() if i.id == invite_id), None)
        if found is None:
            raise NotFound(f"no open invitation {invite_id!r}")
        return _ended(
            request,
            title=f"Revoke the invitation for {found.email}",
            trail=(("/workspace", "Workspace"), ("/members", "Members")),
            said=(INVITE_ENDS.format(email=found.email),),
            action=f"/members/invites/{quote(found.id, safe='')}/revoke",
            button="Revoke the invitation",
            back="/members",
        )

    async def disconnect(request: Request) -> Response:
        grant_id = request.path_params["grant"]
        if not await run_in_threadpool(_memory(request).store.revoke_grant, grant_id):
            raise NotFound(f"no connected agent {grant_id!r}")
        return RedirectResponse("/agents", status_code=303)

    # The pages that show memory are each one record on the access log, named as the MCP
    # tool that reads the same (cairn.web.rest._logged); the others show the workspace's
    # settings, people and credentials, whose changes are in its audit log.
    return [
        Route("/", _logged("projects", home)),
        Route("/b/{project}", _logged("overview", project)),
        Route("/b/{project}/p/{slug}", _logged("page", page)),
        Route("/search", _logged("recall", search)),
        Route("/setup", setup),
        Route("/welcome", welcome),
        Route("/agents", agents),
        Route("/agents/{grant}/revoke", ask_disconnect, methods=["GET"]),
        Route("/agents/{grant}/revoke", disconnect, methods=["POST"]),
        Route("/keys", keys, methods=["GET"]),
        Route("/keys", create_key, methods=["POST"]),
        Route("/keys/{label:path}/revoke", ask_revoke_key, methods=["GET"]),
        Route("/keys/{label:path}/revoke", revoke_key, methods=["POST"]),
        Route("/sets", sets, methods=["GET"]),
        Route("/sets", create_set, methods=["POST"]),
        Route("/sets/{set}", update_set, methods=["POST"]),
        Route("/sets/{set}/delete", ask_delete_set, methods=["GET"]),
        Route("/sets/{set}/delete", delete_set, methods=["POST"]),
        # The pages before removing a person and revoking an invitation; the POSTs that do
        # them are cairn.web.members'.
        Route("/members/{user}/remove", ask_remove_member, methods=["GET"]),
        Route("/members/invites/{invite}/revoke", ask_revoke_invite, methods=["GET"]),
        Route("/audit", audit, methods=["GET"]),
        *compose_routes(),
        *start_routes(mcp_url, oauth=by_url),
        Route("/sources", sources, methods=["GET"]),
        Route("/integrations/new", new_source, methods=["POST"]),
        Route("/integrations/{name}/remove", remove, methods=["POST"]),
        Route("/integrations/accounts/{name}/disconnect", disconnect_account, methods=["POST"]),
        Route("/integrations/{name}/on", switch_on, methods=["POST"]),
        Route("/integrations/{name}/off", switch_off, methods=["POST"]),
        Route("/integrations/{name}/preview", _logged("source_preview", preview), methods=["POST"]),
        Route("/usage", usage, methods=["GET"]),
    ]


def _local_path(value: object) -> str:
    """`value` when it is a path on this server, else "/": the only places a sign-in sends
    the browser on to. A path starts with one "/"; "//host" and "/\\host" (which browsers
    read the same way) name another server, and so can anything holding a character a browser
    drops or rewrites, so only printable ASCII without a backslash passes."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return "/"
    if any(not "!" <= char <= "~" or char == "\\" for char in value):
        return "/"
    return value


def welcome_path(target: str) -> str:
    """/welcome, carrying the way on to `target` (a path from _local_path) unless it is /."""
    return "/welcome" if target == "/" else f"/welcome?next={quote(target, safe='')}"


def session_routes(
    accounts: Accounts, *, key_login: bool = True, failures: CredentialFailures | None = None
) -> list[Route]:
    """Login and logout. Login is the one handler that takes a credential itself (the form's
    key), so it asks `accounts` for the session, and counts a refused key against the client's
    address in `failures` as Auth does on /api (H1): the key is looked up first, and a correct
    one opens a session from any address; from an address over the limit, a refused key, or
    none, is answered 429 on the login form, with Retry-After and TOO_MANY_FAILURES, in
    place of 401. Logout ends the session Auth
    admitted, and
    ends and clears every other session cookie the browser sends under a name the server has
    used for it (auth.SESSION_COOKIES: cairn_session, session, __Host-session), so a session
    left under another name does not come back when a brand is switched on or off.
    Without `key_login` only logout is here: people sign in through a provider instead.
    The cookie is `accounts.sessions`' (auth.Sessions); a key's session lasts SESSION_TTL."""
    sessions = accounts.sessions

    def login_form(request: Request) -> Response:
        target = _local_path(request.query_params.get("next"))
        return _page("login.html", brand=brand_of(request), error=None, next=target)

    async def login(request: Request) -> Response:
        form = await request.form()
        key, target = form.get("key"), _local_path(form.get("next"))
        key = key.strip() if isinstance(key, str) else ""
        token = await run_in_threadpool(accounts.open_session, key)
        if token is None:
            brand = brand_of(request)
            address = request.client.host if request.client else None
            if failures is not None and (wait := failures.blocked(address)):
                busy = _page("login.html", 429, brand=brand, error=TOO_MANY_FAILURES, next=target)
                busy.headers.update({"retry-after": str(wait), "cache-control": "no-store"})
                return busy
            if key and failures is not None:
                failures.failed(address)
            refused = "That key was not accepted."
            return _page("login.html", 401, brand=brand, error=refused, next=target)
        response = RedirectResponse(target, status_code=303)
        sessions.set(response, token, SESSION_TTL)
        return response

    async def logout(request: Request) -> Response:
        if token := request.cookies.get(sessions.cookie):
            await run_in_threadpool(_memory(request).store.end_session, token)
        response = RedirectResponse("/login", status_code=303)
        sessions.clear(response)
        for name in SESSION_COOKIES:  # B1: a name the cookie had before a brand was switched
            if name != sessions.cookie and (other := request.cookies.get(name)):
                await run_in_threadpool(accounts.end_session, other)
                sessions.clear(response, name)
        return response

    logins = [
        Route("/login", login_form, methods=["GET"]),
        Route("/login", login, methods=["POST"]),
    ]
    return [*(logins if key_login else []), Route("/logout", logout, methods=["POST"])]
