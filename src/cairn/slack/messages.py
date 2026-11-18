"""A direct message to the bot (`on_event("message")`; Slack delivers it with im:history,
decision D3).

Only a person's own new message in a direct conversation with the bot is acted on: the
event's `channel_type` "im", no `subtype` (an edit, a deletion, a file shared, ...), no
`bot_id` (another app's), and an author who is not the install's bot user, so the bot never
answers itself. Anything else is acknowledged (200, empty) and nothing is done.

- Plain words are a search, as the person's (cairn.slack.people.search).
- `note:` and text (any case) writes a note into the person's Slack project, `via` "slack",
  recorded as typed; it is in the store before Slack is answered (D8).
- `help`, or nothing but spaces, lists what there is.
- A Slack user who is not linked is answered with the one-time address alone; `help` without
  it.

The answer to Slack is 200 with an empty body; the reply follows it, once the response has
left (people.post_later): chat.postMessage to that conversation, sent once."""

from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from cairn.slack import people
from cairn.slack.dispatch import Context, on_event
from cairn.slack.verify import Envelope

NOTE = "note:"


def _acted_on(event: dict, context: Context) -> bool:
    author = event.get("user")
    return (
        event.get("channel_type") == "im"
        and "subtype" not in event
        and "bot_id" not in event
        and isinstance(author, str)
        and bool(author)
        and author != context.install.bot_user_id
        and isinstance(event.get("channel"), str)
    )


async def _answer(event: dict, context: Context) -> dict:
    words = event.get("text") if isinstance(event.get("text"), str) else ""
    words = words.strip()
    if not words or words.casefold() == "help":
        return people.help_message(context)
    asker = await people.asker(context, event["user"])
    if asker is None:
        return await people.link_prompt(context, event["user"])
    if words[: len(NOTE)].casefold() == NOTE:
        return await run_in_threadpool(people.write, context, asker, words[len(NOTE) :].strip())
    return await run_in_threadpool(people.search, context, asker, " ".join(words.split()))


@on_event("message")
async def direct_message(envelope: Envelope, context: Context) -> Response | None:
    event = envelope.payload["event"]
    if not _acted_on(event, context):
        return None
    message = await _answer(event, context)
    return Response(
        status_code=200, background=people.post_later(context, event["channel"], message)
    )
