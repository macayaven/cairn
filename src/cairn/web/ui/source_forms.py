"""The Sources page's rows and forms: what each source and account is said as, the fields
of a new source by kind, and what a refusal points at (cairn.web.ui.source_pages serves
them)."""

import re
from typing import Any

from cairn.memory import Memory
from cairn.sources import CHOOSING, KINDS
from cairn.sources import connector as _connector
from cairn.sources import github as _github
from cairn.sources import gmail as _gmail
from cairn.sources import google_drive as _google_drive
from cairn.sources import google_meet as _google_meet
from cairn.sources import granola as _granola
from cairn.sources import linear as _linear
from cairn.store import Project
from cairn.store.sources import (
    ACCOUNT_GONE,
    FORMER_MEMBER,
    NAME_TAKEN,
    NO_PROJECT_CHOSEN,
    NOT_YOUR_ACCOUNT,
    ONE_PROJECT,
    OWNER_CANNOT_WRITE,
)
from cairn.web.components import UNREACHED_DESTINATION
from cairn.web.rest import _held_said, _hidden, _scrubbed
from cairn.web.ui.memory_pages import _sees_rule

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
