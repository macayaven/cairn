"""The runner: a source's pull written into a workspace through the `note` use case. The
contract is in cairn.sources's docstring."""

import asyncio
import contextlib
import functools
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from cairn.compiler import Gate
from cairn.gate import lint_gate
from cairn.guard import Guard
from cairn.memory import (
    FILED_ELSEWHERE,
    FORGOTTEN,
    PAGE_FULL,
    FiledOnce,
    Malformed,
    Memory,
    NoteForgotten,
    NotFound,
    OverQuota,
    PageFull,
    Refused,
    _codes,
)
from cairn.plans import Plan, Plans
from cairn.sources import (
    HELD_CREDENTIALS,
    KINDS,
    OVERLAP_ON,
    Clock,
    SourceError,
    holds_secret,
)
from cairn.sources.url import Checked, Resolve, system_resolve
from cairn.store import SourceOff, Store
from cairn.store.hosted import HOSTED_ENV, NOT_HERE, RECONNECT, TokenUnusable
from cairn.store.sources import OwnerCannotWrite

SOURCE_LOGGER = "cairn.sources"
MIN_SYNC_EVERY = 1.0  # seconds between the starts of two background passes, at least
SOURCE_TIMEOUT = 30.0  # seconds for each request to a source
RUN_LIMIT = timedelta(minutes=10)  # a run's time, well inside SOURCE_CLAIM_TTL (15 min)
PULL_SHARE = 0.5  # of a run's time its pull may take: the rest is for writing the notes
MAX_DECLINED = 50  # refused or rejected drafts a report lists (its counts count them all)
# The loggers an HTTP client writes to, as cairn.llm.CLIENT_LOGGERS (not imported: a server
# without a model loads no model code).
CLIENT_LOGGERS = ("httpx", "httpcore")
log = logging.getLogger(SOURCE_LOGGER)
Sleep = Callable[[float], Awaitable[None]]
# G1: a turn of the server's compiles (cairn.web.turns.Compiles.turn), held while a draft is
# stored and compiled and waited for on the event loop; none (nullcontext) outside a server.
Turn = Callable[[], AbstractAsyncContextManager[Any]]


def source_client(
    transport: httpx.AsyncBaseTransport | None = None, *, resolve: Resolve | None = None
) -> httpx.AsyncClient:
    """The HTTP client sources read through: a timeout, and redirects never followed. It
    also holds the "httpx" and "httpcore" loggers (CLIENT_LOGGERS) at WARNING or above for
    good: httpx logs every request at INFO with its full URL, and httpcore traces every
    response's headers at DEBUG, where a provider may echo a credential. So a source's
    query strings and whatever its provider sends back in a header stay out of every log,
    whoever made the client (the command line, `serve`).

    Before each request the client refuses an address inside this machine's network
    plumbing, and a name that resolves to one (cairn.sources.url.Checked), through
    `resolve`; without a transport, through the system's resolver. A client given a
    transport and no resolver (a test's) looks no name up."""
    for name in CLIENT_LOGGERS:
        client_log = logging.getLogger(name)
        client_log.setLevel(max(logging.WARNING, client_log.level))
    if resolve is None and transport is None:
        resolve = system_resolve
    hooks = [Checked(resolve)] if resolve is not None else []
    return httpx.AsyncClient(
        timeout=SOURCE_TIMEOUT,
        follow_redirects=False,
        transport=transport,
        event_hooks={"request": hooks},
    )


@dataclass
class SyncReport:
    """What one run did. `skipped` counts the source's own skips and the drafts for a project
    the source may not write to or that does not exist, and `malformed` (counted in
    `skipped` too) the drafts refused for what they are (Memory.Malformed: empty text, a
    field past its bound); `reasons` counts them by reason, "forgotten" (cairn.memory.FORGOTTEN)
    among them: a draft whose text its project has forgotten, skipped (not malformed) and moved
    past like one, and "no such project" for a draft routed to a project deleted since.
    `left`: the drafts the run had no time for (see RUN_LIMIT). `capped`: the drafts left
    for the next run because a cap of the workspace's plan was reached (cairn.plans): the
    first one over it and every one after it, none of them declined. `hold`: set when
    that cap was the month's notes and the run knew the plan: {"until": the ISO instant the
    month resets, "plan": the plan's name, "cap": its notes_per_month}; until then, and while
    the workspace stays on that plan with that cap, the source is not pulled (`held`, a run
    that did nothing; see run_source). `declined`: each draft the
    guard refused or the gate rejected, as `link` (its `source_ref`), `status` ("refused"
    or "rejected"), `codes` (the guard's rules, or the gate's violation codes: never a
    message, which may quote the note) and `project` (the id of the project it was for: a
    viewer who does not reach it is not shown the link), at most MAX_DECLINED. `busy`:
    another run held the source, so this one did nothing. `superseded`: another run took
    the source after the claim's limit, so this one wrote nothing more and stored no
    cursor. `off`: the source is switched off, so the run read nothing and wrote nothing,
    and every other field is left as it is. `said` (C9): the pull's own sentence on why it
    stopped while the source had more (Pulled.stopped), followed by NOTHING_NEW when every
    draft it gave was one its source had written before; None, and not kept, otherwise, or
    when it holds a credential. `holding` (R3): each item held because it carries the
    selector of another source of the same account that is on, as {"title", "link",
    "project"} (the project's id, as `declined`), at most HELD_MAX, once per item;
    `holding_more` counts the others. Kept as "held_items" and "held_more" only when there
    is one. `held_expired` (R3): items held before that left the look-back window unwritten
    this run (Pulled.held_expired); kept only when there is one."""

    saved: int = 0
    unchanged: int = 0
    refused: int = 0
    rejected: int = 0
    skipped: int = 0
    malformed: int = 0
    left: int = 0
    capped: int = 0
    hold: dict[str, Any] | None = None
    reasons: dict[str, int] = field(default_factory=dict)
    declined: list[dict[str, Any]] = field(default_factory=list)
    busy: bool = False
    superseded: bool = False
    held: bool = False
    off: bool = False
    said: str | None = None
    holding: list[dict[str, Any]] = field(default_factory=list)
    holding_more: int = 0
    held_expired: int = 0
    held_seen: set[str] = field(default_factory=set, repr=False)  # every item held, listed or not

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def decline(self, link: str, status: str, codes: list[str], project_id: str) -> None:
        if len(self.declined) < MAX_DECLINED:
            item = {"link": link, "status": status, "codes": codes, "project": project_id}
            self.declined.append(item)

    def counts(self) -> dict[str, Any]:
        """The report as the store keeps it: the counts and the reasons."""
        kept = asdict(self)
        del kept["busy"], kept["superseded"], kept["held"], kept["off"]
        if kept["said"] is None:  # the reports of the kinds that never say one stay as they were
            del kept["said"]
        del kept["held_seen"]
        holding, more = kept.pop("holding"), kept.pop("holding_more")
        if holding:  # R3: kept only when an item was held, as `said`
            kept["held_items"], kept["held_more"] = holding, more
        if not kept["held_expired"]:
            del kept["held_expired"]
        return kept

    def hold_item(self, title: str, link: str, project_id: str | None) -> None:
        """An item held (R3), listed once whichever of its drafts (its parts) says it."""
        item = re.sub(r"/[0-9]+$", "", link)  # a later part of one item: the same item
        if item in self.held_seen:  # once, before it is counted, listed or not
            return
        self.held_seen.add(item)
        if len(self.holding) < HELD_MAX:
            self.holding.append({"title": title, "link": item, "project": project_id})
        else:
            self.holding_more += 1

    def stopped(self, sentence: str | None) -> None:
        """Keep the pull's sentence on why it stopped (Pulled.stopped), adding NOTHING_NEW
        when every draft of the run was one its source had written before."""
        if not sentence:
            return
        new = self.saved or self.refused or self.rejected or self.skipped or self.left
        if self.unchanged and not (new or self.capped):
            sentence = f"{sentence} {NOTHING_NEW}"
        self.said = sentence


@dataclass(frozen=True)
class SyncOutcome:
    """One source's run in a pass over every workspace: its report, or why it failed (the
    error's message, and its class, which is all the background sync logs)."""

    account_id: str
    source: str
    report: SyncReport | None = None
    error: str | None = None
    error_type: str | None = None


def kept_error(error: BaseException) -> str:
    """What a failed run leaves in `last_error`: a SourceError says why in words cairn wrote
    (never a credential); anything else may quote a note, so only its class is kept."""
    if isinstance(error, SourceError):
        return str(error) or "the source could not be read"
    if isinstance(error, asyncio.CancelledError):
        return STOPPED
    return f"the run failed: {type(error).__name__}"


STOPPED = "the run was stopped before it finished (CancelledError)"
WITHHELD = "the source's error held its credentials, so it was not kept"
HELD_CURSOR = "the source's cursor held its credentials, so it was not stored"
HELD_MAX = 20  # held items a report lists (R3); `held_more` counts the others
NOTHING_NEW = (
    "In this run nothing it gave was new: each item had been written before, so it may be"
    " sending the same items again and again."
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _secrets(config: Mapping[str, Any], env: Mapping[str, str]) -> tuple[str, ...]:
    """The values of the environment variables a config names (its `*_env` settings):
    what nothing a run stores may hold. A source adds its own (`Source.secrets`)."""
    named = (v for k, v in config.items() if k.endswith("_env") and isinstance(v, str))
    return tuple(value for value in (env.get(n, "").strip() for n in named) if value)


# A source made from a Google account connected in the browser (cairn.store.hosted): its
# config names HOSTED_ENV, a variable the run never reads. The run unseals the account's
# token (Store.hosted_document) and hands the kind the authorized_user document under
# HOSTED_VARIABLE instead, a name no environment variable can have ("=" ends a name). What
# the kind says about it names the account in words, and a token Google no longer accepts is
# said as RECONNECT.
HOSTED_VARIABLE = "=the connected Google account"
HOSTED_WORDS = "the connected Google account"
_HOSTED_REVOKED = (
    f"Google refused the token request for {HOSTED_VARIABLE}: the refresh token was revoked"
)


async def _hosted(
    view: Store,
    name: str,
    config: Mapping[str, Any],
    env: Mapping[str, str],
    account: str | None = None,
) -> tuple[Mapping[str, Any], Mapping[str, str], tuple[str, ...]]:
    """The config and environment the kind opens the source `name` with, and the secrets
    they add: as they are, unless the config names HOSTED_ENV. Then SourceError NOT_HERE in a
    process without the key (nothing is read), or GONE or UNOPENED (cairn.store.hosted).
    `account`: an unsaved source's account (preview_new_source), read in place of its link."""
    if config.get("credentials_env") != HOSTED_ENV:
        return config, env, ()
    if getattr(view, "google_custody", None) is None:
        raise SourceError(NOT_HERE)
    try:
        document = await _off_loop(view.hosted_document, name, account=account)
    except TokenUnusable as unusable:
        raise SourceError(str(unusable)) from None
    held = json.loads(document)
    secrets = (document, held["refresh_token"], held["client_secret"])
    return (
        {**config, "credentials_env": HOSTED_VARIABLE},
        {**env, HOSTED_VARIABLE: document},
        secrets,
    )


def _hosted_error(error: BaseException) -> BaseException:
    """A kind's SourceError about HOSTED_VARIABLE, in words: RECONNECT for a token Google no
    longer accepts, else the same sentence naming the account instead of the variable."""
    if not isinstance(error, SourceError) or HOSTED_VARIABLE not in str(error):
        return error
    said = str(error)
    if said.startswith(_HOSTED_REVOKED):
        return SourceError(RECONNECT)
    return SourceError(said.replace(HOSTED_VARIABLE, HOSTED_WORDS))


def _own_secrets(source: object) -> tuple[str, ...]:
    """What a source says it authenticates with (`Source.secrets`), read when asked: a source
    that signs in during its pull holds its token only from then on."""
    return tuple(getattr(source, "secrets", ()))


async def _finished(writing: "asyncio.Future[Any]") -> Any:
    """The result of a note being written on a worker thread. Cancelled meanwhile, it waits
    for the write to end before letting the cancellation through: the thread cannot be
    stopped, and the server closes the store once the background sync has stopped."""
    try:
        return await asyncio.shield(writing)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await writing
        raise


def _started(call: Callable[..., Any], *args: Any, **kwargs: Any) -> "asyncio.Future[Any]":
    return asyncio.ensure_future(asyncio.to_thread(call, *args, **kwargs))


async def _off_loop(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """A store call on a worker thread, so the event loop keeps serving; cancelled, it
    still finishes first (see _finished)."""
    return await _finished(_started(call, *args, **kwargs))


def _since(stamp: str | None) -> datetime | None:
    """A stored `enabled_at` as an aware UTC datetime: what a kind's `open` gets as `since`."""
    return None if stamp is None else datetime.fromisoformat(stamp).astimezone(UTC)


def _resume_before(drafts: Sequence[Any], index: int, claimed: str | None) -> str | None:
    """The cursor from which a pull hands back `drafts[index]` and every draft after it:
    the resume point of the draft before it, else the cursor the run began from."""
    previous = drafts[index - 1].resume if index else None
    return previous if previous is not None else claimed


def _holding(report: Mapping[str, Any] | None, plan: tuple[str, Plan], at: datetime) -> bool:
    """Whether a source whose last report is `report` is still held at its plan's monthly
    cap at `at`: the month it hit the cap has not reset, and the workspace is on the same
    plan with the same cap (a plan-set, or a plans file that changed the cap, lets it go)."""
    hold = (report or {}).get("hold")
    if not isinstance(hold, dict):
        return False
    name, current = plan
    same = hold.get("plan") == name and hold.get("cap") == current.notes_per_month
    return same and at < datetime.fromisoformat(hold["until"])


async def run_source(
    view: Store,
    name: str,
    *,
    http: httpx.AsyncClient,
    env: Mapping[str, str],
    gate: Gate | None = None,
    guard: Guard | None = None,
    clock: Clock | None = None,
    sleep: Sleep | None = None,
    limit: timedelta = RUN_LIMIT,
    plan: tuple[str, Plan] | None = None,
    plans: Plans | None = None,
    saved: Callable[[str, str], None] | None = None,
    turn: Turn = contextlib.nullcontext,
) -> SyncReport:
    """One run of the workspace's source `name` (KeyError: no such source). Every store
    call runs on a worker thread; whatever happens after the claim, the run ends by storing
    its result or by releasing the claim, unless another run took the claim meanwhile.
    `limit`: once that long has passed on `clock` since the run began, it writes no more
    drafts and stores where it got to; the source is opened with a deadline at PULL_SHARE
    of it. `plan`: the workspace's plan, (name, Plan), when `view` is limited by it: a source
    its last run left held at the plan's monthly cap (SyncReport.hold) is then neither
    claimed nor pulled until `clock` passes the reset or the plan changes, and the run
    answers `held` and writes nothing. `plans`: the server's table, so that a draft refused
    at a cap names (and holds the source on) the plan its write read, which a change of
    plan during the pass can make another than `plan`. `saved(project id, slug)`: called
    after a note saves a page (Memory.saved). `turn()`: held around each draft's note, so
    the server's bound on compiles at once holds for sources too (G1; cairn.web.turns).
    A source that is off is not read: the run
    answers SyncReport(off=True) and writes nothing, no claim included (Store.claim_source
    raises SourceOff). The kind's `open` gets `since`, the source's last switch-on.
    A source made from a person's credential whose person no longer writes where it writes
    (Store.claim_source: OwnerCannotWrite, the source switched off) raises SourceError in
    those words, before anything is read; found so mid-run (Store.holds_source_claim), it
    writes nothing more and is `superseded`, the store keeping the same sentence as its last
    error.

    Two sources, one item (R3). Before the pull the run reads the selectors of the other
    sources made from the same account that are on (Store.held_selectors); a draft carrying
    one of them (NoteDraft.selectors) is held: not written, not recorded, listed in the
    report (`holding`), whichever source runs first. A kind with a `holding` attribute is
    told them, so its cursor does not move past a held item and the next run judges it again
    (Gmail). They are read again after the pull, before any draft is written: at a draft held
    only by a source switched on meanwhile the run stops, as at its deadline: nothing after
    it is written, and the cursor is the one before it. A source added with source-add has
    none. Before anything else after the claim, a source with another source of its account
    that is on and selects what it selects (Store.overlapping_source: a pair made before its
    kind's rule refused it) raises SourceError in OVERLAP_ON's words: nothing is read or
    written, the source stays on, and the sentence is its last error, until one of the two
    is switched off.

    Items (I1). When a pull's drafts name items (NoteDraft.item), the run reads once which
    of them the source wrote before (Store.written_source_items). A draft whose item is
    among them is not written and counts as `unchanged`; it is checked after the credential
    check and before anything else about the draft. Once Memory.note returns for a draft
    with an item, whatever the status ("saved", "unchanged", "rejected": the note is
    stored), the item is recorded (Store.record_source_item) while the run still holds the
    claim; a run that finds the claim gone then writes nothing more and is `superseded`. A
    draft for which nothing was stored (refused, malformed, capped, its project gone or not
    allowed, its text forgotten, its page full, a credential in it) records nothing, so it
    is judged again when it is sent again.
    The window that is left: recording is a write of its own, after the note's, not part of
    the note's transaction. A run that dies between the two leaves the item unrecorded; the
    next run proposes it again, the note path answers "unchanged" for the same text, and it
    is recorded then. If the source sends other text for that item in that window, a second
    note is written (tests/acceptance/test_i1_source_items.py pins both)."""
    now = clock or _utc_now
    began = now()
    deadline = began + limit
    view = view.acting_as("system", None)
    if guard is not None:
        view = view.with_guard(guard)
    if plan is not None:
        info = await _off_loop(view.get_source, name)
        if info is not None and not info.enabled:
            return SyncReport(off=True)
        if info is not None and _holding(info.last_report, plan, began):
            return SyncReport(held=True, hold=(info.last_report or {}).get("hold"))
    claiming = _started(view.claim_source, name)
    try:
        claim = await _finished(claiming)
    except SourceOff:  # nothing was claimed, nothing is read or written
        return SyncReport(off=True)
    except OwnerCannotWrite as gone:  # R2, invariant 5: recorded by the store, nothing read
        raise SourceError(str(gone)) from None
    except asyncio.CancelledError:  # stopped while claiming: a claim it got is let go
        if not claiming.cancelled() and claiming.exception() is None and claiming.result():
            with contextlib.suppress(Exception):
                await _off_loop(view.fail_source_run, claiming.result(), STOPPED)
        raise
    if claim is None:
        return SyncReport(busy=True)
    report = SyncReport()
    ended: BaseException | None = None
    settled = False  # the result stored, or the claim found to be another run's
    named = _secrets(claim.config, env)
    secrets = named
    source = None
    try:
        both_on = await _off_loop(view.overlapping_source, claim)  # R3: a pair made before
        if both_on is not None:
            raise SourceError(OVERLAP_ON.format(both_on))
        config, opened_env, hosted = await _hosted(view, claim.name, claim.config, env)
        named = secrets = named + hosted
        kind = KINDS[claim.kind]
        source = kind.open(
            claim.name, config, http=http, env=opened_env, clock=clock, sleep=sleep,
            deadline=began + limit * PULL_SHARE, since=_since(claim.enabled_at),
        )  # fmt: skip
        secrets = named + _own_secrets(source)
        others = await _off_loop(view.held_selectors, claim)  # R3: the account's other sources
        if others and hasattr(source, "holding"):  # a kind that keeps a held item unread
            source.holding = others
        pulled = await source.pull(claim.cursor)
        secrets = named + _own_secrets(source)  # what it signed in with during the pull too
        before = others
        # R3: read again after the pull, before any draft is written: a source of the account
        # switched on meanwhile holds what it selects too, by what is true now
        others = await _off_loop(view.held_selectors, claim)
        if holds_secret(pulled.cursor, secrets):
            raise SourceError(HELD_CURSOR)
        for skip in pulled.skipped:
            report.skip(HELD_CREDENTIALS if holds_secret(skip.reason, secrets) else skip.reason)
        report.held_expired = pulled.held_expired
        for held in pulled.held:  # R3: what the kind kept unread, another source's too
            if others.intersection(held.selectors):
                project_id = claim.project_ids.get(held.project.casefold())
                title = HELD_TITLE if holds_secret(held.title, secrets) else held.title
                report.hold_item(title, held.link, project_id)
        allowed = {project.casefold() for project in claim.config["projects"]}
        # A1: what the run stores is the source's, by the name its claim holds.
        writes = view.writing_as("source", claim.name)
        memory = Memory(writes, lint_gate if gate is None else gate, plan=plan, plans=plans)
        memory.saved = saved  # T4b: a page a note saved is queued for arranging
        cursor = pulled.cursor
        items = {draft.item for draft in pulled.drafts if draft.item is not None}
        written = await _off_loop(view.written_source_items, claim, items) if items else set()
        for index, draft in enumerate(pulled.drafts):
            if now() >= deadline:  # out of time: what is left waits for the next run
                report.left = len(pulled.drafts) - index
                cursor = _resume_before(pulled.drafts, index, claim.cursor)
                break
            if any(holds_secret(value, secrets) for value in vars(draft).values()):
                report.skip(HELD_CREDENTIALS)
                continue
            if draft.item is not None and draft.item in written:  # in memory already (I1)
                report.unchanged += 1
                continue
            if others and others.intersection(draft.selectors):  # R3: another source's too
                project_id = claim.project_ids.get(draft.project.casefold())
                report.hold_item(draft.title, draft.source_ref, project_id)
                if not before.intersection(draft.selectors):  # held by the second read only:
                    report.left = len(pulled.drafts) - index - 1  # stop, as at the deadline
                    cursor = _resume_before(pulled.drafts, index, claim.cursor)
                    break
                continue
            if draft.project.casefold() not in allowed:
                report.skip("project not allowed")
                continue
            if not await _off_loop(view.holds_source_claim, claim):
                settled = report.superseded = True  # another run took it: write nothing
                return report
            resolved = claim.project_ids.get(draft.project.casefold())  # by id, never by name
            target = await _off_loop(view.get_project, resolved) if resolved else None
            if target is None or target.id != resolved:
                report.skip("no such project")
                continue
            try:
                async with turn():  # G1: one of the server's compiles, waited for on the loop
                    result = await _off_loop(
                        memory.note,
                        target.id,
                        draft.text,
                        title=draft.title,
                        topic=draft.topic,
                        label=draft.label,
                        source_ref=draft.source_ref,
                        via=kind.via,
                    )
            except Refused as refused:  # the guard refused it: note.refused, nothing stored
                report.refused += 1
                rules = dict.fromkeys(finding.rule for _, finding in refused.findings)
                report.decline(draft.source_ref, "refused", list(rules), target.id)
                continue
            except OverQuota as over:  # past a cap of the workspace's plan: nothing
                # stored, and not declined for good: it and every draft after it wait
                report.capped = len(pulled.drafts) - index
                if over.limit == "notes_per_month" and plan and over.resets_at is not None:
                    until = over.resets_at.isoformat()
                    named = over.plan if over.plan is not None else plan[0]  # read in the write
                    report.hold = {"until": until, "plan": named, "cap": over.cap}
                cursor = _resume_before(pulled.drafts, index, claim.cursor)
                break
            except NotFound:  # the project was deleted meanwhile
                report.skip("no such project")
                continue
            except Malformed as bad:  # refused for what it is: nothing stored, moved past
                report.skip(bad.reason)
                report.malformed += 1
                continue
            except NoteForgotten:  # the project forgot this text: nothing stored, moved past
                report.skip(FORGOTTEN)
                continue
            except PageFull:  # its page holds all the claims a page holds: moved past
                report.skip(PAGE_FULL)
                continue
            except FiledOnce:  # N1: stored already, under another topic: moved past
                report.skip(FILED_ELSEWHERE)
                continue
            status = result["status"]
            if status == "saved":
                report.saved += 1
            elif status == "unchanged":
                report.unchanged += 1
            else:
                report.rejected += 1
                report.decline(
                    draft.source_ref, "rejected", _codes(result["violations"]), target.id
                )
            if draft.item is not None:  # the note is stored, whatever its status (I1)
                if not await _off_loop(view.record_source_item, claim, draft.item):
                    settled = report.superseded = True  # another run took it: no more
                    return report
                written.add(draft.item)
        if holds_secret(cursor, secrets):  # a draft's `resume`, say
            raise SourceError(HELD_CURSOR)
        if not holds_secret(pulled.stopped, secrets):
            report.stopped(pulled.stopped)
        kept = await _off_loop(view.finish_source_run, claim, cursor, report.counts())
        settled = True
        report.superseded = not kept
    except BaseException as error:
        ended = _hosted_error(error)
        if ended is not error:
            raise ended from None
        raise
    finally:
        if not settled:  # the run failed or was stopped, storing its result included
            with contextlib.suppress(Exception):
                if source is not None:  # a pull that failed may have signed in first
                    secrets = named + _own_secrets(source)
                said = kept_error(ended) if ended else STOPPED
                if holds_secret(said, secrets):
                    said = WITHHELD
                await _off_loop(view.fail_source_run, claim, said)
    return report


PREVIEW_DAYS = 7  # how far back a preview of an off source reads, unless told
PREVIEW_DAYS_MAX = 30
PREVIEW_LIMIT = timedelta(seconds=20)  # a preview's pull, at most
PREVIEW_TITLES = 20  # titles a preview lists, at most
PREVIEW_TITLE_MAX = 200  # characters of each title shown
HELD_TITLE = "held: it holds a credential"  # what a preview shows for such a draft
PREVIEW_DAYS_SAID = f"days is a whole number from 1 to {PREVIEW_DAYS_MAX}"
PREVIEW_BUSY = "a preview of this source is under way"
PREVIEW_LATE = (
    f"the service did not answer within {PREVIEW_LIMIT.seconds} seconds; nothing was read"
)
# (workspace, what a preview holds) under way here: "account:" and the account's name, for a
# preview of a source made from an account, saved or not (one at a time per account), else
# "source:" and the source's name (a source added on the command line), case-folded.
_previewing: set[tuple[str, str]] = set()


class PreviewBusy(Exception):
    """A preview of the same source is under way in this process (PREVIEW_BUSY)."""


@dataclass(frozen=True)
class Preview:
    """What a source would bring (preview_source). `read`: the service answered. `count`: the
    drafts a run would write, none of them written before and each for a project the viewer
    reaches (or for none yet); `titles`: the first PREVIEW_TITLES of them, each cut to
    PREVIEW_TITLE_MAX characters, HELD_TITLE for one that holds a credential; `more`: the
    count past the titles shown. `written_before`: drafts whose item the source wrote already
    (only kinds that name items: GitHub, Linear and the connector). `out_of_reach`: drafts for
    a project the viewer does not reach, never shown. `said`: why the service could not be
    read, when `read` is False."""

    source: str
    days: int
    read: bool
    count: int = 0
    titles: list[str] = field(default_factory=list)
    more: int = 0
    written_before: int = 0
    out_of_reach: int = 0
    said: str | None = None

    def answer(self) -> dict[str, Any]:
        """The preview as POST /api/integrations/<name>/preview answers it."""
        if not self.read:
            return {"source": self.source, "days": self.days, "read": False, "said": self.said}
        shown = asdict(self)
        del shown["said"]
        return shown


def preview_days(days: object) -> int:
    """`days` as a preview takes it: PREVIEW_DAYS for None, else a whole number from 1 to
    PREVIEW_DAYS_MAX (ValueError PREVIEW_DAYS_SAID)."""
    if days is None:
        return PREVIEW_DAYS
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= PREVIEW_DAYS_MAX:
        raise ValueError(PREVIEW_DAYS_SAID)
    return days


async def preview_source(
    view: Store,
    name: str,
    *,
    http: httpx.AsyncClient,
    env: Mapping[str, str],
    days: object = None,
    clock: Clock | None = None,
) -> Preview:
    """What one run of the source `name` would bring now, as `view` may see it, storing
    nothing (R1). The source is read as a run reads it (its kind's `open`, `http`, `env`)
    and pulled once, with a deadline PREVIEW_LIMIT away: an off source from no cursor with
    `since` `days` ago (preview_days); an on source from its stored cursor and its own
    switch-on, so it shows the next run. No claim, no cursor, no item, no audit event and no
    note: the store is only read (Store.source_to_preview, Store.source_items_written), on
    a worker thread. Who may: who may switch the source on with the projects it names
    (KeyError, PermissionError as Store.source_to_preview raises them). ValueError: `days`.
    PreviewBusy: a preview of this source, or of another of its account, saved or not, is
    under way in this process; it is asked only once the store's checks pass. A source that
    cannot be read (its credential variable unset, which sends nothing; a refusal; no answer
    in time) answers `read` False and why, never a credential."""
    days = preview_days(days)
    info = await _off_loop(view.source_to_preview, name)  # the store's checks, first
    account = (await _off_loop(view.source_credentials)).get(info.name.casefold())
    held = f"account:{account.casefold()}" if account else f"source:{info.name.casefold()}"
    return await _held(
        (view.account_id, held),
        lambda: _preview(view, lambda: info, http=http, env=env, days=days, clock=clock),
    )


async def preview_new_source(
    view: Store,
    name: str,
    credential: str,
    select: object,
    options: object,
    project: str | None,
    *,
    new_project: str | None = None,
    http: httpx.AsyncClient,
    env: Mapping[str, str],
    days: object = None,
    clock: Clock | None = None,
) -> Preview:
    """What the source Store.add_source_from would make of these arguments would bring, before
    it is saved (R4, part 2): checked as saving checks it but for the name, which it does
    not check, in the same words (Store.source_from_to_preview: KeyError, PermissionError,
    ValueError, OverLimit), with the config saving would store, then opened and pulled once
    as preview_source pulls an off source (the same deadline, the same hiding of titles, the
    same counts). Nothing is stored, not even the new project, whose drafts count as in
    reach. ValueError: `days`.
    PreviewBusy: a preview of a source of this account, saved or not, is under way in this
    process; it is asked only once the store's checks pass."""
    days = preview_days(days)
    info = await _off_loop(  # the store's checks, first
        view.source_from_to_preview, name, credential, select, options, project,
        new_project=new_project,
    )  # fmt: skip
    made = (new_project,) if isinstance(new_project, str) else ()

    def previewed() -> Awaitable[Preview]:
        return _preview(
            view, lambda: info, http=http, env=env, days=days, clock=clock, made=made,
            saved=False, account=credential,
        )  # fmt: skip

    return await _held((view.account_id, f"account:{credential.casefold()}"), previewed)


async def _held(mark: tuple[str, str], previewed: Callable[[], Awaitable[Preview]]) -> Preview:
    """`previewed()`, awaited while `mark` is held (_previewing); PreviewBusy when it is held
    already. Nothing is awaited between the look and the taking."""
    if mark in _previewing:
        raise PreviewBusy(PREVIEW_BUSY)
    _previewing.add(mark)
    try:
        return await previewed()
    finally:
        _previewing.discard(mark)


async def _preview(
    view: Store,
    found: Callable[[], Any],
    *,
    http: httpx.AsyncClient,
    env: Mapping[str, str],
    days: int,
    clock: Clock | None,
    made: tuple[str, ...] = (),
    saved: bool = True,
    account: str | None = None,
) -> Preview:
    """A preview of the source `found` answers (a stored one, or one not saved: `saved`
    False, so no item was written before), `made` naming a new project it would make."""
    now = clock or _utc_now
    info = await _off_loop(found)
    reached = {project.name.casefold() for project in await _off_loop(view.list_projects)}
    reached |= {project.casefold() for project in made}
    began = now()
    since = _since(info.enabled_at) if info.enabled else began - timedelta(days=days)
    cursor = info.cursor if info.enabled else None
    named = _secrets(info.config, env)
    source = None
    try:
        config, opened_env, hosted = await _hosted(view, info.name, info.config, env, account)
        named = named + hosted
        kind = KINDS[info.kind]
        source = kind.open(
            info.name, config, http=http, env=opened_env, clock=clock, sleep=None,
            deadline=began + PREVIEW_LIMIT, since=since,
        )  # fmt: skip
        async with asyncio.timeout(PREVIEW_LIMIT.total_seconds()):
            pulled = await source.pull(cursor)
    except TimeoutError:
        return Preview(info.name, days, read=False, said=PREVIEW_LATE)
    except Exception as error:  # the service could not be read: why, in words
        secrets = named + (_own_secrets(source) if source is not None else ())
        said = kept_error(_hosted_error(error))
        return Preview(info.name, days, read=False, said=WITHHELD if holds_secret(said, secrets)
                       else said)  # fmt: skip
    secrets = named + _own_secrets(source)
    allowed = {project.casefold() for project in info.config.get("projects", [])}
    items = {draft.item for draft in pulled.drafts if draft.item is not None}
    written = (
        await _off_loop(view.source_items_written, info.name, items) if items and saved else set()
    )
    titles: list[str] = []
    count = before = unreached = 0
    for draft in pulled.drafts:
        project = draft.project.casefold()
        if project and project not in reached:
            unreached += 1
            continue
        if project and allowed and project not in allowed:  # a run skips it
            continue
        if draft.item is not None and draft.item in written:
            before += 1
            continue
        count += 1
        if len(titles) < PREVIEW_TITLES:
            held = any(holds_secret(value, secrets) for value in vars(draft).values())
            title = draft.title or draft.source_ref
            titles.append(HELD_TITLE if held else title[:PREVIEW_TITLE_MAX])
    return Preview(
        info.name, days, read=True, count=count, titles=titles, more=count - len(titles),
        written_before=before, out_of_reach=unreached,
    )  # fmt: skip


async def sync_all(
    store: Store,
    *,
    http: httpx.AsyncClient,
    env: Mapping[str, str],
    gate: Gate | None = None,
    guard: Guard | None = None,
    clock: Clock | None = None,
    sleep: Sleep | None = None,
    plans: Plans | None = None,
    saved: Callable[[str, str, str], None] | None = None,
    turn: Turn = contextlib.nullcontext,
) -> list[SyncOutcome]:
    """Every source of every workspace, once. A source that fails does not stop the others;
    cancellation does stop the pass. With `plans`, each workspace writes through a view
    limited by the plan in force (Plans.in_force: the operator's, else the default), as its
    requests do. `saved(account id, project id, slug)` is called
    after a note saves a page, as a request's note does (the app's arranger queues it)."""
    outcomes = []
    for account in await _off_loop(store.list_accounts):
        try:
            view = await _off_loop(store.for_account, account.id)
            plan = await _off_loop(plans.in_force, store, account.id) if plans is not None else None
        except KeyError:  # deleted meanwhile
            continue
        if plan is not None:
            view = plans.limit(view, plan[1])  # type: ignore[union-attr]
        for info in await _off_loop(view.list_sources):
            if not info.enabled:  # passed over: no claim, nothing read
                outcomes.append(SyncOutcome(account.id, info.name, report=SyncReport(off=True)))
                continue
            try:
                report = await run_source(
                    view, info.name, http=http, env=env, gate=gate, guard=guard, clock=clock,
                    sleep=sleep, plan=plan, plans=plans,
                    saved=functools.partial(saved, account.id) if saved is not None else None,
                    turn=turn,
                )  # fmt: skip
            except Exception as error:
                outcomes.append(
                    SyncOutcome(
                        account.id, info.name, error=kept_error(error),
                        error_type=type(error).__name__,
                    )
                )  # fmt: skip
            else:
                outcomes.append(SyncOutcome(account.id, info.name, report=report))
    return outcomes


async def sync_forever(
    store: Store,
    every: float,
    *,
    http: httpx.AsyncClient,
    env: Mapping[str, str],
    gate: Gate | None = None,
    guard: Guard | None = None,
    wait: Sleep = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    plans: Plans | None = None,
    saved: Callable[[str, str, str], None] | None = None,
    turn: Turn = contextlib.nullcontext,
) -> None:
    """`sync_all`, then a wait of what is left of `every` seconds since the pass started,
    for ever: passes never overlap, and a pass longer than `every` is followed by the next
    at once (missed ones are not made up). A failed source is logged by workspace, source
    and error class only; so is a pass that breaks as a whole (the database, say), and the
    next pass still comes. Returns only by cancellation. ValueError: `every` under
    MIN_SYNC_EVERY."""
    if not every >= MIN_SYNC_EVERY:
        raise ValueError(f"sync at most every {MIN_SYNC_EVERY:g} s")
    while True:
        started = monotonic()
        try:
            outcomes = await sync_all(
                store,
                http=http,
                env=env,
                gate=gate,
                guard=guard,
                plans=plans,
                saved=saved,
                turn=turn,
            )
        except Exception as error:
            log.warning("a pass over the sources failed: %s", type(error).__name__)
            outcomes = []
        for outcome in outcomes:
            if outcome.error_type is not None:
                log.warning(
                    "source %r of workspace %s failed: %s",
                    outcome.source,
                    outcome.account_id,
                    outcome.error_type,
                )
        await wait(max(0, every - (monotonic() - started)))
