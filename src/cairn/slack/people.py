"""What the three ways a person acts in Slack share (cairn.slack.commands, messages and
shortcuts): who the Slack user is, a search and a note as theirs, a message's author asked of
Slack, and the private reply that follows. Every message a person sees is built by
cairn.slack.blocks; nothing here builds one.

Who asks
- `asker(context, slack_user)`: the person the install's Slack user is linked to
  (Store.slack_person) with their Memory (Context.memory_for: their reach, the gate, the guard
  and the plan), or None when there is no link or the person is no longer a member: both are
  "not linked". `link_prompt` makes the one-time value (Store.offer_slack_link, 10 minutes,
  one use, replacing the Slack user's earlier offer and its code) and answers with its
  address, <public url>/integrations/slack/link?t=<value>, and the two steps of a link: open
  it signed in, it shows a code, type `<command> link` and the code (cairn.slack.commands).
- `writable(memory)`: the projects the person may write to, by name. `chosen(context, asker,
  name)`: one of those made the person's Slack project (Store.set_slack_project), or a
  refusal that says nothing of whether a project of that name exists (NOT_WRITABLE).

A search and a note, as the person's
- `search`: Memory.recall(query, limit=5), in their reach; a project out of it contributes
  nothing, not even a count (blocks.found shows only the hits).
- `write`: Memory.note into the person's Slack project, `via` "slack", whole: the note is
  stored, then compiled into its page (extractive, no model) before this returns, so when
  Slack is answered the note is in the store (D8); a page that kept moving leaves it stored
  and waiting for `serve` (N1), and the answer still links its page. Then
  Store.record_slack_note records it as having come through the install: filed, always, with
  its conversation and message time (D9: a message recorded already answers with the note it
  already is); typed, only when the note is new (a text the project held already came in
  some other way, and is not forgotten with the install, D10). The record is written when the
  note is stored and before it is compiled (Memory.note's `on_stored`): for a filed message
  it gives the claim cairn.slack.shortcuts made its note, so a compile that then fails leaves
  a note the install's removal still finds; the person is then told it is
  stored (STORED_NOT_FILED). The answer links the note's page. Each refusal is its own
  sentence: no project chosen (NO_PROJECT), the project out of
  reach or gone (OUT_OF_REACH), read-only (the store's sentence), the guard's (cairn's own,
  naming the rule), the plan's cap, a full page, a note filed under another topic, a text the
  project forgot, a text that is not a note (NOT_A_NOTE), the store unable to write.
- `written`: `write` inside one of the server's turns of compiles (Context.turn; G1,
  cairn.web.turns), as a REST note is: the turn is waited for on the event loop, holding no
  worker thread, and the note is stored and compiled while it is held. Slack wants its
  answer within three seconds, so the wait is at most TURN_WITHIN (1) seconds, which with
  the shortcut's AUTHOR_WITHIN leaves time for the compile; past it nothing is stored, no
  turn is held or waited for, and the person is told so (BUSY) and to try again. No note is
  kept to be written later: what is answered is what is stored (D8).
- `already(context, asker, note_id)`: the answer for a message filed already, linking its
  note, or, when that note is out of the person's reach, ELSEWHERE, naming nothing of it.
  BEING_FILED: the answer for a message someone else is filing right now.
- Two seams, each the one place its message is built: `filed_message` distinguishes a
  note filed from one stored and waiting for its page. `unlinked` confirms the link is undone
  and says how to link again.

The author of a message (D7)
- `author(context, message)`: the name of the message's author, once Slack shows the author
  is of the install's team: one users.info, AUTHOR_WITHIN (1.5) seconds in all, nothing
  retried. A positive answer is the user's `team_id` the install's, and neither `is_stranger`,
  `is_bot` nor `deleted` set. Otherwise NotFiled with the reason: no person as its author,
  another organisation, no answer in time, a rate limit, Slack's error by its code.
- AUTHORS keeps positive answers, KNOWN_MAX at most, each for KNOWN_FOR by the server's clock
  (Context.now), per install and author, so a second filing of the same author's message
  asks Slack nothing. A negative answer is not kept.

Replies after the answer
- `reply_later` and `post_later` send a private reply once Slack has its 200 (the response's
  background task): through the reply address a signed request gave (SlackClient.reply), or
  chat.postMessage to a direct conversation, once, within REPLY_WITHIN seconds, never again.
  A reply that fails is logged by its error's class; the note is there all the same.

Nothing of a message, a search or a note's text, a token or an address is in a log line: a
failure is logged by its class alone."""

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from cairn.compiler import page_for
from cairn.memory import WAITING, Malformed, Memory, NotFound, RequestError, unavailable
from cairn.slack import blocks
from cairn.slack.client import SlackError
from cairn.slack.dispatch import Context
from cairn.store import CannotWrite, Project, StoreUnusable, stored_key
from cairn.store.slack import SlackPerson

log = logging.getLogger("cairn.slack")
FOUND_AT_ONCE = 5  # claims a search shows
AUTHOR_WITHIN = 1.5  # seconds Slack has, in all, to say who wrote a message
KNOWN_FOR = timedelta(minutes=10)  # how long a positive answer about an author is kept
KNOWN_MAX = 512  # answers kept at most, the oldest dropped first
REPLY_WITHIN = 10.0  # seconds a private reply may take; it is sent once
TURN_WITHIN = 1.0  # seconds a note waits for a turn among the server's compiles
BUSY = (
    "cairn is compiling as many notes as it can at once, and nothing was stored."
    " Try again in a moment."
)

STORED_NOT_FILED = (
    "The note is stored in your project, but cairn could not put it on a page."
    " An owner of the workspace can see it there."
)
NO_PROJECT = (
    "No project is chosen for your notes from Slack. Choose one with `{command} project <name>`."
)
OUT_OF_REACH = (
    "Your Slack project is out of your reach or no longer exists, and nothing was stored."
    " Choose another with `{command} project <name>`."
)
NOT_WRITABLE = (
    "You cannot write to a project of that name, or there is none."
    " `{command} project` lists the projects you can write to."
)
NOT_A_NOTE = "This is not a note cairn keeps ({reason}), and nothing was stored."
ELSEWHERE = "This message is filed already, in a project you do not reach."
BEING_FILED = (
    "This message is being filed right now, and nothing more was filed. Try again in a moment"
    " to see its note."
)
NO_TEXT = "This message has no text to file, and nothing was filed."
NOT_A_MESSAGE = "This is not a message cairn can file, and nothing was filed."
NO_PERSON = (
    "This message has no person as its author (an app, a bot or a deactivated account wrote"
    " it), and nothing was filed."
)
OTHER_ORGANISATION = (
    "This message's author is not of this Slack workspace, and cairn files only what was"
    " written here: nothing was filed."
)
NOT_IN_TIME = "Slack did not say in time who wrote this message, and nothing was filed. Try again."
RATE_LIMITED = (
    "Slack is limiting how often cairn asks who wrote a message, and nothing was filed."
    " Try again in a minute."
)
SLACK_SAID = "Slack did not say who wrote this message (it answered {code}), and nothing was filed."
NO_TOKEN = (
    "This install holds no bot token, so cairn cannot ask Slack who wrote this message:"
    " nothing was filed."
)


@dataclass(frozen=True)
class Asker:
    """A Slack user of the install, linked to a person who is a member: their link (with
    their Slack project) and their Memory."""

    slack_user: str
    person: SlackPerson
    memory: Memory = field(repr=False)

    @property
    def name(self) -> str:
        found = self.memory.person
        return found.name if found is not None else ""


class NotFiled(Exception):
    """A message that is not filed; `reason` (its message) says why, to the person."""


async def asker(context: Context, slack_user: str | None) -> Asker | None:
    """Who asks (the module); None: not linked."""
    if not slack_user:
        return None

    def look() -> Asker | None:
        person = context.view.slack_person(context.install.id, slack_user)
        if person is None:
            return None
        memory = context.memory_for(person.user_id)
        return None if memory is None else Asker(slack_user, person, memory)

    return await run_in_threadpool(look)


async def link_prompt(context: Context, slack_user: str | None) -> dict:
    """The first step of a link for `slack_user`: a one-time address, which replaces their
    earlier offer and its code, and the two steps said (blocks.link_prompt)."""
    if not slack_user:
        return blocks.refused("Slack did not say who asked.")
    value = await run_in_threadpool(context.view.offer_slack_link, context.install.id, slack_user)
    return blocks.link_prompt(
        f"{context.public_url}/integrations/slack/link?t={value}", context.install.command
    )


def linked(asker: Asker) -> dict:
    """Who the Slack user is linked to."""
    return blocks.linked(asker.name)


async def unlinked(context: Context, slack_user: str | None) -> dict:
    """What a person is told once `unlink` undid their link, and how to link again with this
    install's command."""
    return blocks.unlinked(context.install.command)


def help_message(context: Context) -> dict:
    return blocks.help_text(context.install.command)


def page_url(context: Context, project: str, slug: str) -> str:
    return f"{context.public_url}/b/{quote(project, safe='')}/p/{quote(slug, safe='')}"


def search(context: Context, asker: Asker, query: str) -> dict:
    """The person's search (the module). Blocking: run it on a worker thread."""
    try:
        hits = asker.memory.recall(query, limit=FOUND_AT_ONCE)["hits"]
    except RequestError as refused:
        return blocks.refused(str(refused))
    if not hits:
        return blocks.nothing_found(query)
    return blocks.found(hits, query=query, base_url=context.public_url)


def writable(memory: Memory) -> list[Project]:
    """The projects in the person's reach they may write to, by name
    (Store.writable_projects: one read, whatever the workspace holds)."""
    return memory.store.writable_projects()


def project_said(context: Context, asker: Asker) -> dict:
    """Which project is the person's Slack project, and which they can write to."""
    names = [project.name for project in writable(asker.memory)]
    current = asker.person.project_id
    found = asker.memory.store.get_project(current) if current else None
    return blocks.project_is(found.name if found else None, writable=names)


def chosen(context: Context, asker: Asker, name: str) -> dict:
    """The project named `name` made the person's Slack project (the module)."""
    projects = writable(asker.memory)
    wanted = name.strip().casefold()
    match = next((p for p in projects if p.name.casefold() == wanted or p.id == name), None)
    if match is None:
        return blocks.refused(NOT_WRITABLE.format(command=context.install.command))
    context.view.set_slack_project(context.install.id, asker.slack_user, match.id)
    return blocks.project_is(match.name, writable=[project.name for project in projects])


def write(
    context: Context,
    asker: Asker,
    text: str,
    *,
    title: str | None = None,
    source_ref: str | None = None,
    message: tuple[str, str] | None = None,
) -> dict:
    """A note of the person's, written and recorded (the module); `message`: the filed
    message's (conversation, time). Blocking: run it on a worker thread."""
    command = context.install.command
    project_id = asker.person.project_id
    if project_id is None:
        return blocks.refused(NO_PROJECT.format(command=command))
    kept: list[str] = []  # the note this became, once it is stored and recorded

    def stored(note: Any, created: bool) -> None:
        if message is not None:  # a filed message is recorded always: it is its key (D9)
            recorded = context.view.record_slack_note(
                context.install.id,
                note.id,
                how="filed",
                brought_by=asker.person.user_id,
                conversation_id=message[0],
                message_time=message[1],
            )
            if recorded != note.id and created:
                # the claim was taken over and the message filed meanwhile: this note is
                # one Slack created all the same, so it is forgotten with the install's
                context.view.record_slack_note(
                    context.install.id, note.id, how="typed", brought_by=asker.person.user_id
                )
            kept.append(recorded)
            return
        if created:  # a typed text the project held already did not come through here
            context.view.record_slack_note(
                context.install.id, note.id, how="typed", brought_by=asker.person.user_id
            )
        kept.append(note.id)

    try:
        answer = asker.memory.note(
            project_id, text, title=title, source_ref=source_ref, via="slack", on_stored=stored
        )
    except NotFound:
        return blocks.refused(OUT_OF_REACH.format(command=command))
    except Malformed as malformed:
        return blocks.refused(NOT_A_NOTE.format(reason=str(malformed)))
    except RequestError as refused:  # read-only, the guard's, the plan's cap, a full page...
        return blocks.refused(str(refused))
    except (CannotWrite, StoreUnusable) as trouble:
        return blocks.refused(str(unavailable(trouble)))
    except Exception as failed:  # the compile, after the note was stored and recorded
        if not kept:
            raise
        log.warning("slack: a note was stored and not filed: %s", type(failed).__name__)
        return blocks.refused(STORED_NOT_FILED)
    if kept[0] != answer["note_id"]:  # the same message, filed meanwhile by someone else
        return already(context, asker, kept[0])
    project = asker.memory.store.get_project(project_id)
    note = asker.memory.store.get_note(answer["note_id"])
    if project is None or note is None:  # gone since: what is said of a project gone
        return blocks.refused(OUT_OF_REACH.format(command=command))
    return filed_message(
        title=note.title,
        page_url=page_url(context, project.name, answer["page"]),
        project=project.name,
        waiting=answer.get("status") == WAITING,
    )


def filed_message(*, title: str, page_url: str, project: str, waiting: bool) -> dict:
    """What a person is told of a note written: the one seam for it. `waiting`: the note is
    stored and waits for its page (Memory.note's status WAITING, N1)."""
    return blocks.filed(title=title, page_url=page_url, project=project, waiting=waiting)


async def written(context: Context, asker: Asker, text: str, **fields: Any) -> dict:
    """`write` (the same arguments), holding one of the server's turns of compiles (the
    module); BUSY when none came within TURN_WITHIN seconds, and nothing stored."""
    turn = context.turn()
    try:  # a turn that is free is taken at once, before the time is up, whatever it is
        async with asyncio.timeout(TURN_WITHIN):
            await turn.__aenter__()
    except TimeoutError:  # the wait was taken back: no turn is held or waited for
        return blocks.refused(BUSY)
    try:
        return await run_in_threadpool(lambda: write(context, asker, text, **fields))
    finally:
        await turn.__aexit__(None, None, None)


def already(context: Context, asker: Asker, note_id: str) -> dict:
    """The answer for a message that is a note already (D9). Blocking."""
    store = asker.memory.store
    note = store.get_note(note_id)
    project = store.get_project(note.project_id) if note is not None else None
    if note is None or project is None:
        return blocks.refused(ELSEWHERE)
    slug = page_for(store, note.project_id, stored_key(note), note.topic or note.title)
    url = page_url(context, project.name, slug)
    return blocks.filed(title=note.title, page_url=url, project=project.name, already=True)


class Authors:
    """Positive answers about authors (the module): at most `most`, each for `keep`."""

    def __init__(self, most: int = KNOWN_MAX, keep: timedelta = KNOWN_FOR) -> None:
        self.most, self.keep = most, keep
        self._known: OrderedDict[tuple[str, str], tuple[str | None, Any]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._known)

    def get(self, install: str, author: str, now: Any) -> tuple[str | None] | None:
        """(name,) when the author is known to be of the install's team, else None."""
        found = self._known.get((install, author))
        if found is None:
            return None
        name, until = found
        if now >= until:
            del self._known[(install, author)]
            return None
        return (name,)

    def remember(self, install: str, author: str, name: str | None, now: Any) -> None:
        self._known.pop((install, author), None)
        self._known[(install, author)] = (name, now + self.keep)
        while len(self._known) > self.most:
            self._known.popitem(last=False)

    def clear(self) -> None:
        self._known.clear()


AUTHORS = Authors()


def _name(user: dict) -> str | None:
    profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
    for value in (profile.get("real_name"), user.get("real_name"), profile.get("display_name")):
        if isinstance(value, str) and value.strip():
            return value
    return None


async def author(context: Context, message: dict) -> str | None:
    """The author's name, once Slack shows the author is of the install's team (the module);
    NotFiled otherwise."""
    author_id = message.get("user")
    if not isinstance(author_id, str) or not author_id or message.get("bot_id"):
        raise NotFiled(NO_PERSON)
    install = context.install
    known = AUTHORS.get(install.id, author_id, context.now())
    if known is not None:
        return known[0]
    token = await run_in_threadpool(context.bot_token)
    if not token:
        raise NotFiled(NO_TOKEN)
    try:
        found = await context.slack().user(token, author_id, within=AUTHOR_WITHIN)
    except SlackError as failed:
        if failed.code == "timeout":
            raise NotFiled(NOT_IN_TIME) from None
        if failed.code == "rate_limited":
            raise NotFiled(RATE_LIMITED) from None
        raise NotFiled(SLACK_SAID.format(code=failed.code)) from None
    if found.get("team_id") != install.team_id or found.get("is_stranger"):
        raise NotFiled(OTHER_ORGANISATION)
    if found.get("is_bot") or found.get("deleted"):
        raise NotFiled(NO_PERSON)
    name = _name(found)
    AUTHORS.remember(install.id, author_id, name, context.now())
    return name


async def _reply(context: Context, address: str, message: dict) -> None:
    try:
        await context.slack().reply(address, message)
    except Exception as failed:  # its class only: a message can quote what was written
        log.warning("slack: a private reply was not sent: %s", type(failed).__name__)


async def _post(context: Context, conversation: str, message: dict) -> None:
    try:
        token = await run_in_threadpool(context.bot_token)
        if not token:
            log.warning("slack: a reply was not sent: the install holds no bot token")
            return
        fields = {"channel": conversation, **message}
        await context.slack().call("chat.postMessage", token, fields, within=REPLY_WITHIN)
    except Exception as failed:  # its class only
        log.warning("slack: a reply was not sent: %s", type(failed).__name__)


def reply_later(context: Context, address: str, message: dict) -> BackgroundTask:
    """The private reply through `address`, sent after the response (the module)."""
    return BackgroundTask(_reply, context, address, message)


def post_later(context: Context, conversation: str, message: dict) -> BackgroundTask:
    """chat.postMessage to `conversation`, sent after the response (the module)."""
    return BackgroundTask(_post, context, conversation, message)
