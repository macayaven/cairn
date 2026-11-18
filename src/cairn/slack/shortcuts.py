"""The message shortcut "File in cairn" (`on_action("file_in_cairn")`): the message it was
used on becomes a note in the person's Slack project.

What is filed: the message's text as Markdown, a heading naming who wrote it and when (UTC),
and the message's own link as `source_ref` (cairn.slack.text.note_from_message), `via`
"slack", recorded as filed with its conversation and message time (Store.record_slack_note).

In this order, each refusal said privately with its reason and nothing filed:
1. A Slack user who is not linked is answered with the one-time address alone.
2. A message without a conversation or a time as Slack writes them is not one cairn can file
   (people.NOT_A_MESSAGE); one with no text is refused (people.NO_TEXT).
3. A message filed already (D9) is answered with the note it already is, before anything is
   asked of Slack; one in a project out of the person's reach names nothing of it.
4. No project chosen is refused before anything is asked of Slack.
5. The author (D7): a message with no person as its author is refused without asking; else
   Slack is asked who the author is, once, within 1.5 seconds in all, unless the answer is
   kept already (people.author): another organisation, a stranger, a bot or a deactivated
   account, no answer in time, a rate limit and an error from Slack are each refused.
6. The note is written (people.write) before Slack is answered (D8).

The answer to Slack is 200 with an empty body; the reply follows once the response has left,
through the reply address the signed request gave (SlackClient.reply: Slack's hosts only,
private to the person), sent once. A payload without a reply address is acknowledged and
nothing is done: there is no way to tell the person anything."""

from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from cairn.slack import blocks, people
from cairn.slack.dispatch import Context, on_action
from cairn.slack.text import NotText, note_from_message
from cairn.slack.verify import Envelope
from cairn.store.slack import MESSAGE_TIME, SLACK_ID


def _place(payload: dict) -> tuple[dict, str, str] | None:
    """The message, its conversation and its time, or None when the payload lacks them."""
    message, conversation = payload.get("message"), payload.get("channel")
    if not isinstance(message, dict) or not isinstance(conversation, dict):
        return None
    conversation_id, time = conversation.get("id"), message.get("ts")
    if not isinstance(conversation_id, str) or not SLACK_ID.fullmatch(conversation_id):
        return None
    if not isinstance(time, str) or not MESSAGE_TIME.fullmatch(time):
        return None
    return message, conversation_id, time


def _has_text(message: dict) -> bool:
    text = message.get("text")
    return isinstance(text, str) and bool(text.strip())


async def _filed(envelope: Envelope, context: Context) -> dict:
    asker = await people.asker(context, envelope.user_id)
    if asker is None:
        return await people.link_prompt(context, envelope.user_id)
    place = _place(envelope.payload)
    if place is None:
        return blocks.refused(people.NOT_A_MESSAGE)
    message, conversation_id, time = place
    if not _has_text(message):
        return blocks.refused(people.NO_TEXT)
    held = await run_in_threadpool(
        context.view.slack_note_for, context.install.id, conversation_id, time
    )
    if held is not None:
        return await run_in_threadpool(people.already, context, asker, held)
    if asker.person.project_id is None:
        return blocks.refused(people.NO_PROJECT.format(command=context.install.command))
    try:
        name = await people.author(context, message)
    except people.NotFiled as refused:
        return blocks.refused(str(refused))
    install = context.install
    try:
        draft = note_from_message(
            message,
            team_id=install.team_id or "",
            team_domain=install.team_domain or "",
            conversation_id=conversation_id,
            author=name,
            names={message["user"]: name} if name else {},
        )
    except NotText:
        return blocks.refused(people.NO_TEXT)
    except ValueError:  # a conversation, a time or a domain not as Slack writes them
        return blocks.refused(people.NOT_A_MESSAGE)
    return await run_in_threadpool(
        lambda: people.write(
            context,
            asker,
            draft.text,
            title=draft.title,
            source_ref=draft.source_ref,
            message=(conversation_id, time),
        )
    )


@on_action("file_in_cairn")
async def file_in_cairn(envelope: Envelope, context: Context) -> Response | None:
    address = envelope.payload.get("response_url")
    if not isinstance(address, str) or not address:
        return None
    answer = await _filed(envelope, context)
    return Response(status_code=200, background=people.reply_later(context, address, answer))
