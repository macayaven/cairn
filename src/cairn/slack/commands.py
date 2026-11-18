"""The slash command (`/cairn`, or the name the install's owner gave it), word by word, each
registered with cairn.slack.dispatch.on_command. The answer is in the HTTP response, as JSON
with `response_type` "ephemeral": for the person who asked alone, never the conversation.

- `find <words>`: the person's search (cairn.slack.people.search), at most 5 claims, each
  linked to its page; nothing found is said. `find` with no words is `help`.
- `note <text>`: a note in the person's Slack project, `via` "slack", recorded as typed; it is
  written before the answer leaves (D8) and the answer links its page.
- `project` says which project that is and which the person can write to; `project <name>`
  changes it to one of those, and refuses any other with one sentence whatever it is.
- `link`: who the Slack user is linked to, and nothing changes, whatever follows. For a Slack
  user not linked, `link` alone is the first step of a link (people.link_prompt: a new
  one-time address, replacing their earlier offer and its code), and `link <code>` the last
  (Store.take_slack_offer): the code the person who said yes to their address was shown
  links them to that person, and the answer says to whom. Anything else, a wrong code, one
  run out, one not given yet, another Slack user's, or a person no longer a member, links
  nothing and is answered with one sentence (NO_LINK), whichever it was.
- `unlink` undoes the link and offers the one-time address again, as to anyone not linked.
- `help`, and no word: what there is.
- A Slack user who is not linked (people.asker) is answered with the one-time address alone,
  whatever they ask but `link <code>`; `help` and no word are answered without it, and
  write nothing."""

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from cairn.slack import blocks, people
from cairn.slack.dispatch import Context, on_command
from cairn.slack.verify import Envelope


NO_LINK = "That code links nothing. To link your Slack user, type `{command} link` to start again."


def _answer(message: dict) -> JSONResponse:
    return JSONResponse({**message, "response_type": "ephemeral"})


def _rest(envelope: Envelope) -> str:
    """What follows the command's first word."""
    words = envelope.payload.get("text", "").strip().split(maxsplit=1)
    return words[1] if len(words) > 1 else ""


@on_command("")
@on_command("help")
async def help_(envelope: Envelope, context: Context) -> JSONResponse:
    """What there is; asked of no one, written nowhere."""
    return _answer(people.help_message(context))


@on_command("find")
async def find(envelope: Envelope, context: Context) -> JSONResponse:
    query = " ".join(_rest(envelope).split())
    if not query:
        return _answer(people.help_message(context))
    asker = await people.asker(context, envelope.user_id)
    if asker is None:
        return _answer(await people.link_prompt(context, envelope.user_id))
    return _answer(await run_in_threadpool(people.search, context, asker, query))


@on_command("note")
async def note(envelope: Envelope, context: Context) -> JSONResponse:
    asker = await people.asker(context, envelope.user_id)
    if asker is None:
        return _answer(await people.link_prompt(context, envelope.user_id))
    return _answer(await people.written(context, asker, _rest(envelope)))


@on_command("project")
async def project(envelope: Envelope, context: Context) -> JSONResponse:
    asker = await people.asker(context, envelope.user_id)
    if asker is None:
        return _answer(await people.link_prompt(context, envelope.user_id))
    name = _rest(envelope).strip()
    if not name:
        return _answer(await run_in_threadpool(people.project_said, context, asker))
    return _answer(await run_in_threadpool(people.chosen, context, asker, name))


@on_command("link")
async def link(envelope: Envelope, context: Context) -> JSONResponse:
    asker = await people.asker(context, envelope.user_id)
    if asker is not None:  # linked: said, and nothing changes, a code or not
        return _answer(people.linked(asker))
    code = _rest(envelope).strip()
    if not code or not envelope.user_id:
        return _answer(await people.link_prompt(context, envelope.user_id))
    no_link = blocks.refused(NO_LINK.format(command=context.install.command))
    try:
        await run_in_threadpool(
            context.view.take_slack_offer, context.install.id, envelope.user_id, code
        )
    except (KeyError, ValueError):
        return _answer(no_link)
    asker = await people.asker(context, envelope.user_id)
    return _answer(people.linked(asker) if asker is not None else no_link)


@on_command("unlink")
async def unlink(envelope: Envelope, context: Context) -> JSONResponse:
    if envelope.user_id:
        await run_in_threadpool(
            context.view.unlink_slack_user, context.install.id, envelope.user_id
        )
    return _answer(await people.unlinked(context, envelope.user_id))
