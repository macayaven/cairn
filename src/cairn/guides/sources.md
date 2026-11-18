# Sources: meetings, mail, calendar, documents and to-dos

{{name}} signs in to your mail only through a [Gmail source](#mail-from-gmail-read-by-the-server) the operator adds, which reads only the messages a person labels, and to your calendar only through a [Google Calendar source](#events-from-google-calendar-read-by-the-server) the operator adds. Otherwise, an agent you already use reads the source with the connector it already has, and writes what matters to {{name}} as notes. {{name}}'s part is the same as for any note: it stores the note as written, compiles it into a page, and every claim on the page cites the note's lines.

This guide gives one recipe per kind of source. Each is a project, a connection that reaches only that project, and an instruction you give the agent.

For a system with machine keys of its own, the operator can add a source that the server reads on a timer (`serve --sync-every`). It reads these: Langfuse, where the traces someone marked with a score become notes; [Granola](#meetings-from-granola-read-by-the-server), where meeting notes do; [Google Meet](#meeting-transcripts-from-google-meet-read-by-the-server), where meeting transcripts do; [Google Drive](#documents-from-google-drive-read-by-the-server), where the documents put into one folder do; [Google Calendar](#events-from-google-calendar-read-by-the-server), where events do; and [Gmail](#mail-from-gmail-read-by-the-server), where the messages a person labels do. `python -m cairn.server source-add --help` lists each kind's settings. [Sources the server reads](#sources-the-server-reads) says who switches them on.

It also reads [Linear](#issues-from-linear-read-by-the-server), where issues become notes once they are finished.

It also reads [GitHub](#finished-work-from-github-read-by-the-server), where the pull requests merged, the issues closed and the releases published in one repository do.

For any other system, the server can read [a connector of your own](#a-connector-of-your-own): a small web service you write that serves that system's items.

## What every recipe shares

**One project per kind of source.** Meetings go to a project of the kind *Meetings*, documents to *Business documents*, your own to-dos to *Personal notes*. The kind tells every agent that uses the project what belongs there. Make them under **Projects**, or several at once with the guided setup at `/start`.

**A connection that reaches only that project.** When the browser opens to approve the agent, choose **Selected projects** and tick the one project. For a job without a browser, make a key limited to it under **Workspace**, then **Keys**. The agent that reads your mail can then write to the mail project and to nothing else, and it cannot list or search the others. What the agent may read *at the source* is decided by the source's own connector; {{name}} never sees those credentials.

**One note per item, with the item's address.** Every note carries `source_ref`: a stable address of the item at the source. The recipes below use these forms:

| Source | `source_ref` |
|---|---|
| A meeting | `meeting:<the tool's meeting id>` |
| A mail thread | `mail:<thread id>` |
| A calendar event | `calendar:<event id>` |
| A calendar week | `calendar:week/<Monday's date>` |
| A document | `doc:<file id>` |
| A to-do | `todo:<list>/<item id>` |

A page that holds a note whose `source_ref` has one of these forms, or starts the way the server's own Gmail, Google Meet and Google Drive sources start theirs (`gmail:`, `meet:` and `drive:`), is not sent to a model the operator connects to arrange pages, unless the operator sets `<PREFIX>_SOURCE_NOTES=send` and the page's project is not kept from every model ([the privacy guide](privacy.md#where-it-is) says by whom); {{name}} tidies that page itself instead. That holds whoever wrote the note: {{name}} goes by the form, so write it as the recipe gives it. The form is the word before the first `:`, in any letter case and with any white space around it: `Mail:` counts, `mailbox:` and `email:` do not. A note that one of the [sources the server reads](#sources-the-server-reads) writes is judged by its source instead.

`topic` names the page the note builds. Notes with the same topic build one page, so a weekly meeting's notes collect on one page and a project's mail on another.

**When two notes are the same topic.** A note's topic is its `topic`, else its title. Two topics are one when they differ only in letter case, in accents on Latin or Greek letters, in how their words are separated (spaces, hyphens, underscores and slashes alike) or in sentence punctuation at the edges of words: "Retry policy", "retry-policy", "RETRY_POLICY" and "Retry policy!" build one page, and so do "Café menu" and "Cafe menu". Anything else makes another topic: "C++", "C#" and "C" are three, "Release 2.10" and "Release 2-10" are two, "e-mail" and "email" are two, and two titles that differ only in their last word are two, however long they are. A page keeps the address its first note gave it. A note of another topic whose title would make the same address gets a page of its own, at that address with a short tag added.

**A note is filed once.** The same text with the same `source_ref` is one note, filed on the page of the topic it was first sent with. Sent again with another topic, it is refused and nothing is stored: the answer names the page the note is on, and a source's run skips it with the reason `filed under another topic`. To move a note, forget it on the page that cites it, then send it with its new topic, reworded or to another project: a text forgotten in a project is not stored there again.

**Running it twice is safe.** The same text with the same `source_ref` is stored once. A second run that sends it with the same topic answers `unchanged`: no note is stored again and no page version is written, and the outcome is recorded in the workspace's audit log. A note stored before {{name}} filed notes by topic stays the same note whatever topic or title it is sent with: it is not stored again, and once it is on its page it answers `unchanged`. A note stored since, sent with another topic, is refused as above.

**{{name}} adds; it does not take back.** This is the rule for edits and removals at the source:

- An item that changed is a new note. The page gets a new version that holds what was said before *and* what is said now, each with its source. If Thursday became Friday, the page says both.
- So tell the agent to write the change as a statement ("The release moved from Thursday to Friday"), not to send the whole item again.
- An item removed at the source stays in {{name}} until someone forgets it there. The person who made the project, or an owner, can forget one note from the page that cites it: the note, its text and every claim that cites only it go, a page that cited it keeps one new version without it (its earlier versions go), a page only it held goes, and the same text is not stored in that project again (a source that sends it again is skipped, with the reason `forgotten`). They can also delete the whole project.

Decide what you bring in with that in mind.

**A note fits a page.** A page holds at most 2,000 claims and 400,000 characters of claims, and every source {{name}} reads writes notes that fit one. A note that would not fit is cut, and the note says where it was cut; a source that writes a long item in parts, such as a document, a message or a transcript, makes each part fit, and the parts hold all of the item between them. A connector's item is not cut: one whose text is longer than a page holds is skipped, and the run's report says so. A note with more than 30,000 facts is not cut either: it is skipped as `facts past their bound`.

**What not to bring.** By default the note guard refuses a note that holds a key, a token or a password. It matches patterns and nothing more: do not send what should not be kept. Ask the agent for decisions, facts and commitments in its own words, with the address of the item, not for a copy of the item.

## Meetings

*Project:* kind **Meetings**, for example `meetings`. *The agent needs:* a connector to your meeting notes (Granola, or the notes your call tool writes).

```text
After each meeting I attended, write one note to the {{name}} project "meetings".
- title: the meeting's name and its date
- topic: the meeting's series name (the project's name for a one-off)
- label: meeting
- source_ref: meeting:<the meeting's id in the notes tool>
- text, as Markdown: one line saying who met and when, then a list of what was decided,
  a list of who committed to what by when, and a list of open questions.
Write what was said in your own words. No transcript. Leave out small talk and anything
personal. If a meeting had no decision, commitment or open question, write no note.
Then call recall on the topic and tell me if a commitment from an earlier meeting has no update.
```

### Meetings from Granola, read by the server

When a team keeps its meeting notes in Granola, the operator can add a Granola source instead, and the notes arrive without anyone asking an agent. Use the recipe above when you want an agent to choose what matters and write it in its own words, or when your notes live in a tool {{name}} has no source for.

**What it reads and writes.** A Granola source reads the notes one Granola API key can read, or the notes of one folder or space, and writes each meeting as a note into one project. Its note has a heading with the title, when the meeting took place, who attended and who owns the note, by name, then the summary as Granola wrote it. Each meeting's page is named by its title and its date, so a weekly meeting gets a page a week; two meetings with the same title on the same day share one. {{name}} writes people's names, never the email addresses Granola sends beside them, and never the owner's private notes. The summary itself is written unchanged, so an address in it meets the note guard as in any note. A name that holds an address counts as no name: the attendee is counted, the owner left out. With `transcripts` on, the meeting's transcript is written too, a line a turn, on pages of its own; transcripts are off unless you ask for them.

**Once, after it settles.** A note is read once its summary has not changed for `settle_minutes` (30 minutes unless set), and only once: an edit at Granola afterwards, a regenerated summary or a deletion changes nothing in {{name}}. Only notes created after the source was switched on are read, nothing from before. Granola's API returns only notes that have a generated summary, so a meeting without one is not read.

**Setting it up.** Granola makes API keys on its Business and Enterprise plans. The operator puts the key in the server's environment, never in {{name}} (with Docker, a line in `deploy/cairn.env`), and adds the source, which starts off:

```text
python -m cairn.server source-add --db cairn.db --name meetings --kind granola --config '{"key_env": "GRANOLA_API_KEY"}'
```

Everything but `key_env` is optional: `folder_id` or `space_id` (one folder or one space), `transcripts` (false), `settle_minutes` (30), `lookback_days` (30: how far back the daily look for notes that turned up late goes) and `base_url`. Then an owner or an admin switches it on under **Workspace**, then **Sources**, and chooses its project ([Sources the server reads](#sources-the-server-reads)).

### Meeting transcripts from Google Meet, read by the server

When a team meets on Google Meet, the operator can add a Google Meet source, and each meeting's transcript arrives as a note without anyone asking an agent. Use it for what was said, a line a turn. Use the Granola source when the team keeps its notes in Granola and wants Granola's summaries, and the recipe above when you want an agent to choose what matters and write it in its own words.

**What it reads and writes.** A Google Meet source reads the meetings Google Meet lists for one person, or the one meeting whose code it names, and writes each transcript as a note into one project. Its note has a heading with the meeting code and when the meeting started, then when it started and ended, when it was transcribed and who took part, then a line a turn: the time since the meeting started, who spoke and what they said. The Meet API gives a meeting no title, so each meeting's page is named by its meeting code and its start in UTC, and a meeting gets a page of its own. Names are written as Meet shows them, never an email address: someone Meet gives no name, or a name that holds an address, is "Unknown speaker". Someone who did not sign in has "(anonymous)" or "(by phone)" after their name on every line, so a guest who types the name of someone who signed in is not written as that person. A transcript of more than 1,500 turns is written in parts. When it is switched on, the page proposes the project `meet-transcripts`.

**Only what Google transcribed, once.** A meeting has a transcript only if someone in it started transcription, or an admin made transcription automatic, and only on a Google Workspace edition that has transcripts. Google can take up to 24 hours to generate the file, so the source looks at each meeting at every run until `wait_hours` after it ended (48 unless set, from 1 to 168), and reads each transcript once, as soon as its file is generated: a change at Google afterwards changes nothing in {{name}}. A transcript still not generated by then is reported once and not read. Only meetings that ended after the source was switched on are read, nothing from before. Google deletes a meeting's record and its transcript entries 30 days after the meeting ends, so the server must sync within that time: a run picks up the meetings since the last run that finished, as long as Google still has them.

**Setting it up.** The source signs in to Google in one of two ways, and needs the scope `https://www.googleapis.com/auth/meetings.space.readonly` either way. Its credential is the text of a JSON document in a variable of the server's environment, never in {{name}} and never a path to a file: with Docker, a line in `deploy/cairn.env` with the JSON on one line between single quotes.

With a service account, for a Google Workspace domain whose super admin agrees:

1. In a Google Cloud project, create a service account and a JSON key for it. Google keeps no copy of the key, and an organization created on or after May 3, 2024 blocks key creation unless its admin allows it.
2. A super admin of the domain opens the Admin console, then Security, Access and data control, API Controls, **Manage Domain Wide Delegation**, and adds the service account's numeric **Client ID** (not its email address) with the scope above. It usually takes a few minutes and can take up to 24 hours.
3. The variable holds the key file's contents, and `subject` in the source's config is the address of the person whose meetings it reads.

With one person's own authorisation, when there is no super admin to ask:

1. In a Google Cloud project, create an OAuth client of type **Desktop app** and download its JSON file.
2. On the computer whose browser the person uses, run `google-authorize` with `--kind google_meet`, as [One person's own Google authorisation](#one-persons-own-google-authorisation) shows. The person grants the scope above in that browser, and the command writes the credential to a new file.
3. The variable holds the file's one line, `{"type": "authorized_user", "client_id": "...", "client_secret": "...", "refresh_token": "..."}`, and the config has no `subject`. Then delete the file. How long the refresh token lasts is under [One person's own Google authorisation](#one-persons-own-google-authorisation); when it stops working, the source says to authorise again.

Then the operator adds the source, which starts off:

```text
python -m cairn.server source-add --db cairn.db --name calls --kind google_meet --config '{"credentials_env": "GOOGLE_MEET_CREDENTIAL", "subject": "person@example.com"}'
```

`credentials_env` is the one setting it needs; `subject` (with a service account), `meeting_code` (one meeting, `abc-mnop-xyz`), `wait_hours` (48), `base_url` and `projects` are optional. Then an owner or an admin switches it on under **Workspace**, then **Sources**, and chooses its project ([Sources the server reads](#sources-the-server-reads)). The Sources page shows whose meetings it reads, the `subject`, and never the credential.

## Mail

*Project:* kind **Team decisions and runbooks** or **General**, for example `mail-decisions`. *The agent needs:* read access to your mail, narrowed at the source to a label if the connector allows it.

```text
Once a day, look at mail threads labelled "to-{{slug}}" that changed since yesterday.
For each thread that holds a decision, a commitment or a fact I will need again, write
one note to the {{name}} project "mail-decisions".
- title: the thread's subject
- topic: the project or customer the thread is about
- label: mail
- source_ref: mail:<thread id>
- text: what was decided or promised, by whom, with the date. Two to six lines.
Never copy a message's body, a signature, an address or a phone number.
Skip newsletters, receipts and anything automated.
```

Labelling by hand is the access level here: the agent sees your mailbox through the connector, and the label is what you agreed it brings in.

### Mail from Gmail, read by the server

When the operator can sign in to Google as a server, they can add a Gmail source instead, and each message a person labels arrives as a note of its own without anyone asking an agent. Use it to keep what a message said, in its sender's words. Use the recipe above when you want an agent to pick out what a thread decided and write it in its own words, without copying a body, a signature or an address, or when your mail is not in Gmail.

**A person chooses what is read.** A Gmail source reads only the messages of one mailbox that carry one Gmail label: a label a person made in Gmail, which the operator names in `label`. Gmail's own labels, such as `INBOX`, `SENT` or a category, are refused. Putting the label on a message is what chooses it. The source starts off: the operator adds it, and it reads nothing until an owner or an admin switches it on. A message that arrived before the source was switched on is never read, even when it is labelled later. A label is noticed for `lookback_days` after a message arrived (30 unless set, from 1 to 365): a message labelled later than that is not read. A message is the unit, not a conversation: labelling a conversation in Gmail labels the messages it holds at that moment, so a reply that arrives later is read only when it gets the label itself. Drafts are not read, nor messages in Spam or Trash.

**No setting reads a whole mailbox.** A mailbox holds other people's words, and most of it is not worth keeping. So no setting reads every message, a folder or a search: a message is read only because a person put the label on it.

**What it reads and writes.** A Gmail source writes each message it reads as a note into one project. Its note has a heading with the subject, then when the message arrived (in UTC), who sent it, who it was sent to and copied to, by name, and the names of its attachments, then its body as text: its plain text, or its HTML turned into text when it has no plain text. The body is written as it was sent, the earlier messages it quotes included. Attachments are never read. The messages of one conversation in one month share a page, named by the subject without its "Re:" or "Fwd:" and by the month, so a subject that comes back every week gets a page a month. A message longer than 1,500 passages is written in parts. A message with no text of its own, one whose answer from Gmail is over 8 MB, and one that would take more than 20 parts are not read, and each is reported once. When it is switched on, the page proposes the project `mail`.

**No email address among the facts.** People are named as their mail names them, never by their address: someone with no name, or whose name holds an address, is written as the domain of their address, and past ten people the rest are counted ("and 3 more"). The subject and the body are the sender's words, written unchanged, so an address in them meets the note guard as in any note. Under the workspace's default guard policy, `secrets`, an address in a body is kept. Under `secrets+personal`, a message whose subject or body holds an email address is refused by the note guard and not read again, and mail often holds one, in a signature or above a quoted reply.

**Read once.** Each message is read once. Taking the label off afterwards, labelling it again, or moving it to Trash changes nothing in {{name}}. Google says a message cannot be changed once it is made, only its labels, so there is nothing later to follow. Switched off and on again, the source reads only messages that arrived after it was switched on again.

**Setting it up.** The source signs in to Google in one of two ways, and needs the scope `https://www.googleapis.com/auth/gmail.readonly` in each, which Google describes as "View your email messages and settings." Google classes this scope as Restricted, and a Workspace administrator can block it. The Gmail API must be enabled in the Cloud project of the service account or the OAuth client. Its credential is the text of a JSON document in a variable of the server's environment, never in {{name}} and never a path to a file: with Docker, a line in `deploy/cairn.env` with the JSON on one line between single quotes.

With a service account acting for one person, for a Google Workspace domain whose super admin agrees:

1. In a Google Cloud project, create a service account and a JSON key for it. Google keeps no copy of the key, and an organization created on or after May 3, 2024 blocks key creation unless its admin allows it.
2. A super admin of the domain opens the Admin console, then Security, Access and data control, API Controls, **Manage Domain Wide Delegation**, and adds the service account's numeric **Client ID** (not its email address) with the scope above. It usually takes a few minutes and can take up to 24 hours.
3. The variable holds the key file's contents, and `subject` in the source's config is the address of the person whose mail it reads. A service account without `subject` is refused: it has no mailbox of its own.

With one person's own authorisation, when there is no super admin to ask:

1. In a Google Cloud project, create an OAuth client of type **Desktop app** and download its JSON file.
2. On the computer whose browser the person uses, run `google-authorize` with `--kind gmail`, as [One person's own Google authorisation](#one-persons-own-google-authorisation) shows. The person grants the scope above in that browser, and the command writes the credential to a new file.
3. The variable holds the file's one line, `{"type": "authorized_user", "client_id": "...", "client_secret": "...", "refresh_token": "..."}`, and the config has no `subject`: the source reads the mailbox of the person who authorised it. Then delete the file. How long the refresh token lasts is under [One person's own Google authorisation](#one-persons-own-google-authorisation); when it stops working, the source says to authorise again.

Then the person makes the label in Gmail, and the operator adds the source, which starts off:

```text
python -m cairn.server source-add --db cairn.db --name mail --kind gmail --config '{"credentials_env": "GMAIL_CREDENTIAL", "subject": "person@example.com", "label": "For {{slug}}"}'
```

`label` is the label's name as Gmail shows it. Google's pages do not say how the API names a label nested under another; {{name}} expects the parent's name in front, `parent/child`, and when no label has the name given, a run says so. `credentials_env` and `label` are the settings it needs; `subject` (with a service account), `lookback_days` (30), `base_url` and `projects` are optional. Then an owner or an admin switches it on under **Workspace**, then **Sources**, and chooses its project ([Sources the server reads](#sources-the-server-reads)). The Sources page shows whose mailbox it reads, the label, and how long a label is noticed, and never the credential.

## Calendar

*Project:* kind **Personal notes**, for example `calendar`. *The agent needs:* read access to your calendar.

```text
Every Friday, write one note to the {{name}} project "calendar" for the week that ended.
- title: Week of <Monday's date>
- topic: Weeks
- label: calendar
- source_ref: calendar:week/<Monday's date>
- text: a list of the meetings I attended, each as "<date>: <title>, with <who>", and
  the hours they took in total.
Leave out events marked private and events I declined.
```

A week is one note because a single event says little. If an event's own notes matter, they belong in the meetings recipe.

### Events from Google Calendar, read by the server

When the operator can sign in to Google as a server, they can add a Google Calendar source instead, and each event of one calendar arrives as a note of its own without anyone asking an agent. Use it for what each meeting was: when and where, who was invited and what its description says. Use the recipe above when a week in one note is enough, or when you want an agent to choose what matters and write it in its own words, and the [Google Meet source](#meeting-transcripts-from-google-meet-read-by-the-server) for what was said.

**What it reads and writes.** A Google Calendar source reads the events of one calendar, and writes each event that has ended as a note into one project. Its note has a heading with the event's title, then when it took place (in UTC, or its days for an all-day event), where, who organised it, who was invited and the Google Meet meeting code when it has one, then its description as text. Each event's page is named by its title and the day it started, so each occurrence of a weekly meeting gets a page of its own. It passes over events marked private, events the calendar's own person declined, and events that are not meetings: out of office, focus time, working location, birthdays and events from Gmail. When it is switched on, the page proposes the project `calendar`.

**No email address among the facts.** Names are written as Google gives them, never an email address: someone Google gives no name, or whose name holds an address, is counted ("and 2 more"), and an address in the title or the location is left out. The description is written with its words unchanged, so an address in it meets the note guard as in any note.

**Once, after it has ended.** An event is read once, as soon as it has ended, and later edits are not followed: a change at Google afterwards, or a deletion, changes nothing in {{name}}. An event put on the calendar up to two days after it ended is still read. An event moved more than two days later after it was read is read again once it has ended at its new time, as a new note. Only events that ended after the source was switched on are read, nothing from before.

**Which calendar.** The operator names the one calendar it reads by its id, `calendar_id`: `primary` unless told otherwise, the calendar of the account it reads as, or another calendar's id, which looks like an email address. The account it reads as must see the calendar's event details: on a calendar it sees as free/busy only, which hides each event's title, the source writes nothing and says to share the details.

**Setting it up.** The source signs in to Google in one of three ways, and needs the scope `https://www.googleapis.com/auth/calendar.events.readonly` whichever it is, which Google describes as "View events on all your calendars." The Google Calendar API must be enabled in the Cloud project of the service account or the OAuth client. Its credential is the text of a JSON document in a variable of the server's environment, never in {{name}} and never a path to a file: with Docker, a line in `deploy/cairn.env` with the JSON on one line between single quotes.

With a service account acting for one person, for a Google Workspace domain whose super admin agrees:

1. In a Google Cloud project, create a service account and a JSON key for it. Google keeps no copy of the key, and an organization created on or after May 3, 2024 blocks key creation unless its admin allows it.
2. A super admin of the domain opens the Admin console, then Security, Access and data control, API Controls, **Manage Domain Wide Delegation**, and adds the service account's numeric **Client ID** (not its email address) with the scope above. It usually takes a few minutes and can take up to 24 hours.
3. The variable holds the key file's contents, and `subject` in the source's config is the address of the person whose calendar it reads.

With a service account and a calendar shared with it, when no super admin delegates:

1. Create a service account and a JSON key for it, as in the first way.
2. The calendar's owner shares the calendar with the service account's address, `client_email` in the key file, so that it sees event details and not only free/busy. No domain-wide delegation is needed. Google's pages do not say what a service account sees of a calendar shared with it.
3. The variable holds the key file's contents, the config has no `subject`, and `calendar_id` is the shared calendar's id. A service account has no calendar of its own to read, so `primary` is refused.

With one person's own authorisation, when there is no super admin to ask:

1. In a Google Cloud project, create an OAuth client of type **Desktop app** and download its JSON file.
2. On the computer whose browser the person uses, run `google-authorize` with `--kind google_calendar`, as [One person's own Google authorisation](#one-persons-own-google-authorisation) shows. The person grants the scope above in that browser, and the command writes the credential to a new file.
3. The variable holds the file's one line, `{"type": "authorized_user", "client_id": "...", "client_secret": "...", "refresh_token": "..."}`, and the config has no `subject`. Then delete the file. How long the refresh token lasts is under [One person's own Google authorisation](#one-persons-own-google-authorisation); when it stops working, the source says to authorise again.

Then the operator adds the source, which starts off:

```text
python -m cairn.server source-add --db cairn.db --name diary --kind google_calendar --config '{"credentials_env": "GOOGLE_CALENDAR_CREDENTIAL", "subject": "person@example.com"}'
```

`credentials_env` is the one setting it needs; `subject` (with a service account acting for a person), `calendar_id` (`primary`), `base_url` and `projects` are optional. Then an owner or an admin switches it on under **Workspace**, then **Sources**, and chooses its project ([Sources the server reads](#sources-the-server-reads)). The Sources page shows which calendar it reads and as whom, the `calendar_id` and the `subject`, and never the credential.

## Documents

*Project:* kind **Business documents**, for example `specs`. *The agent needs:* read access to the documents, narrowed to a folder.

```text
When I say "file this document", write one note to the {{name}} project "specs".
- title: the document's title
- topic: the document's title
- label: document
- source_ref: doc:<file id>
- text: the document's decisions, numbers, names of things, interfaces and constraints, as a
  list under the document's own section headings. Keep versions, flags, paths and commands
  exactly as written, in code formatting.
When a document I filed before has changed, write one note that says what changed and
what it replaces, with the same topic and source_ref.
```

{{name}} checks that a version or a flag in a note reaches the page or is listed as left out, and that every line of code reaches it, which is why the recipe keeps them exact.

### Documents from Google Drive, read by the server

When a team keeps its documents in Google Drive, the operator can add a Google Drive source for one folder, and each document put into that folder arrives as a note without anyone asking an agent. Use it to keep the documents themselves, word for word. Use the recipe above when you want an agent to pick out the decisions and constraints and write them in its own words, when a document's later versions matter as much as its first, or when your documents live where {{name}} has no source.

**What it reads and writes.** A Google Drive source reads the Google Docs, Markdown files and text files directly in the one folder the operator names, and writes each document as a note into one project: a Doc as Google exports it to Markdown, a file as it is. It does not look into subfolders, follow shortcuts or read any other kind of file, such as a Sheet, a presentation or a PDF. Its note has a heading with the document's name, then when it was last modified, who last modified it, by name, and a link to open it in Google Drive, then the document's text, unchanged but for embedded images, which are left out with their alt text kept. No email address is written among these facts. Each document gets a page of its own, named by its name. A document longer than 1,500 passages is written in parts. A document that may not be downloaded, one over 10 MB, and one that would take more than 20 parts are not read, and each is reported once. When it is switched on, the page proposes the project `documents`.

**A person chooses what is read.** The source starts off: the operator adds it, and it reads nothing until an owner or an admin switches it on. From then on, putting a document into the folder is what chooses it. A document already in the folder when the source is switched on is not read, unless `existing` is set; one put in or changed after is. A document is read once it has rested, not modified for `settle_hours` (24 unless set, from 1 to 720), so one still being written waits.

**Read once, and later edits are not followed.** Each document is read once. An edit, a rename, a move or a deletion afterwards changes nothing in {{name}}. {{name}} keeps every note it is given, so reading a document again at each change would pile near-copies onto its page, and Google gives a reader no dependable sign that a Doc's text has changed. To have a newer version read, take the document out of the folder, leave it out until the server has synced once, and put it back: it is read again once it has rested, and if it changed, its page gets a second note. Switched off and on again, the source takes stock again: what was put in or changed while it was off is not read.

**Setting it up.** The source signs in to Google in one of three ways, and needs the scope `https://www.googleapis.com/auth/drive.readonly` in each. Google classes this scope as Restricted, and a Workspace administrator can block it. Its credential is the text of a JSON document in a variable of the server's environment, never in {{name}} and never a path to a file: with Docker, a line in `deploy/cairn.env` with the JSON on one line between single quotes. The Google Drive API is enabled in the Google Cloud project the credential comes from.

With a service account the folder is shared with, when no administrator needs to be asked:

1. In a Google Cloud project, create a service account and a JSON key for it. Google keeps no copy of the key, and an organization created on or after May 3, 2024 blocks key creation unless its admin allows it.
2. Someone who may share the folder shares it with the service account's email address as Viewer, and unticks Notify people: a service account has no inbox. Sharing with the whole domain does not reach a service account, which is not part of the domain.
3. The variable holds the key file's contents, and the config has no `subject`: the service account reads as itself, with no domain-wide delegation.

With a service account acting for a person, for a Google Workspace domain whose super admin agrees:

1. In a Google Cloud project, create a service account and a JSON key for it, as above.
2. A super admin of the domain opens the Admin console, then Security, Access and data control, API Controls, **Manage Domain Wide Delegation**, and adds the service account's numeric **Client ID** (not its email address) with the scope above. It usually takes a few minutes and can take up to 24 hours.
3. The variable holds the key file's contents, and `subject` in the source's config is the address of the person it reads as. The folder must be one that person can see.

With one person's own authorisation, when the source should read as that person:

1. In a Google Cloud project, create an OAuth client of type **Desktop app** and download its JSON file.
2. On the computer whose browser the person uses, run `google-authorize` with `--kind google_drive`, as [One person's own Google authorisation](#one-persons-own-google-authorisation) shows. The person grants the scope above in that browser, and the command writes the credential to a new file.
3. The variable holds the file's one line, `{"type": "authorized_user", "client_id": "...", "client_secret": "...", "refresh_token": "..."}`, and the config has no `subject`. Then delete the file. How long the refresh token lasts is under [One person's own Google authorisation](#one-persons-own-google-authorisation); when it stops working, the source says to authorise again.

Then the operator adds the source, which starts off:

```text
python -m cairn.server source-add --db cairn.db --name specs --kind google_drive --config '{"credentials_env": "GOOGLE_DRIVE_CREDENTIAL", "folder_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz"}'
```

`folder_id` is the last part of the folder's address, after `/folders/`. `credentials_env` and `folder_id` are the settings it needs; `subject` (with delegation), `settle_hours` (24), `existing` (false), `base_url` and `projects` are optional. Then an owner or an admin switches it on under **Workspace**, then **Sources**, and chooses its project ([Sources the server reads](#sources-the-server-reads)). The Sources page shows which folder it reads and as whom, and never the credential.

## To-dos

*Project:* kind **Personal notes**, for example `todo`. *The agent needs:* read access to your list.

```text
When a to-do is completed, write one note to the {{name}} project "todo".
- title: the to-do's text
- topic: the project or list it belongs to
- label: done
- source_ref: todo:<list>/<item id>
- text: what was done and the date, in one or two lines, with a link to the result if
  there is one.
Do not write open to-dos: the list is where they live. {{name}} keeps what got done and why.
```

Open items change too often for a memory that does not take back. What was finished is stable, and it is what an agent asks about later.

### Issues from Linear, read by the server

When a team tracks its work in Linear, the operator can add a Linear source, and each issue arrives as a note once it is finished, without anyone asking an agent. Use the recipe above for a list Linear does not hold, or when you want an agent to write what was done in its own words.

**What it reads and writes.** A Linear source reads the issues one Linear API key can read, or those of the teams or the one Linear project the operator names, and writes each issue into one project once it is completed or cancelled. Its note has a heading with the issue's identifier and title, then its team, its Linear project when it has one, its status, the day it was finished, who it is assigned to and who created it, by name, and its labels, then the description as Linear holds it, then its comments, oldest first, each reply after the comment it answers, with who wrote each one and when. Each issue gets a page of its own, named by its identifier and title, so a team that finishes a hundred issues a month adds about a hundred pages a month. {{name}} writes people's names, never their email addresses, and a name that holds an address counts as no name. A description or a comment too long for one page is cut, with a line saying so, and a note keeps an issue's first 200 comments and says how many more there are. When it is switched on, the page proposes the project `linear`.

**Once, when it is finished.** An issue is read once it has been completed or cancelled for `settle_minutes` (30 unless set), so that the comment that closes it is in, and only once: an edit, a new comment, reopening it, finishing it again or moving it to another team afterwards writes nothing more in {{name}}. An issue marked as a duplicate, one still open and one in Linear's trash are never read. Unless `backfill_days` is set, only issues finished after the source was switched on are read, nothing from before. Archived issues are read too, since Linear archives finished issues by itself.

**Starting with what finished lately.** By default a source reads nothing finished before it was switched on, so that switching it on never pours an unknown amount of history into a project. With `backfill_days` (0 unless set, at most 365) it also reads the issues finished in that many days before it is switched on, oldest first, each once. It reads them over several runs, at most 25 issues a run, so 600 issues take 24 runs, and an issue finished meanwhile takes its turn in the order they were finished. Switched off and on again, it reads from that many days before the new switch-on, and what it wrote before is not written again.

**Setting it up.** In Linear, a person makes a personal API key under Settings, Account, Security & Access. Give it only the Read permission and limit it to the teams the source should read: the key reads what its owner can see in those teams, private teams the owner belongs to included. The operator puts the key in the server's environment, never in {{name}} (with Docker, a line in `deploy/cairn.env`), and adds the source, which starts off:

```text
python -m cairn.server source-add --db cairn.db --name tracker --kind linear --config '{"key_env": "LINEAR_API_KEY", "teams": ["ENG"]}'
```

Everything but `key_env` is optional: `teams` (up to 20 team keys, the letters before the number in an identifier, `ENG` in `ENG-123`) or `linear_project` (one Linear project's id), not both, and without either every team the key can read; `comments` (true); `settle_minutes` (30); `lookback_days` (30: how far back the daily look for issues that turned up late goes); `backfill_days` (0: how many days before the switch-on it also reads) and `base_url`. A team the key cannot read is not an error: the source reads what Linear lists, so when a team's issues do not arrive, check the key's teams. Then an owner or an admin switches it on under **Workspace**, then **Sources**, and chooses its project ([Sources the server reads](#sources-the-server-reads)).

**When Linear says no.** When Linear refuses the key, the run fails, and the Sources page names the variable to check, never the key. When Linear asks the server to slow down, the run stops where it is, keeps what it finished and does not fail; the Sources page says so under the source's counts, and the next run goes on. All the keys of one Linear user share that user's hourly allowance of 2,500 requests, so another tool using the same person's keys can use it up.

### Finished work from GitHub, read by the server

A repository's lasting knowledge is in its finished work: what a merged pull request changed and why, how an issue was resolved, what a release shipped. The operator can add a GitHub source that reads that work from one repository, so it arrives without anyone asking an agent.

**What it reads and writes.** A GitHub source reads one repository and writes each finished item as a note into one project: a pull request once it is merged, an issue once it is closed as completed or as not planned, and a release once it is published, a prerelease among them. It never reads an open pull request or issue, a pull request closed without merging, an issue closed as a duplicate, a draft release, or review comments on lines of code. Its note has a heading with the repository, the item's number and its title (for a release, its tag and name), a list of facts (who opened it, who merged it, when and into which branch; when an issue was closed and how; a release's tag, date and who published it), then the description as GitHub gives it. With `comments` on, which it is unless you turn it off, the conversation follows under a **Comments** heading in time order: each comment with its author and date, and a pull request's reviews that carry text, each with its verdict in words. People are named by their GitHub login, a bot's followed by "(bot)" and an account since deleted as "a deleted account"; no email address is written. Each item has a page of its own, named by its title.

**Once, after it settles.** An item is read once it has been finished for `settle_minutes` (30 minutes unless set), and it is written once: an edit, a new comment, a reopening and a second close, or a release published again changes nothing in {{name}} afterwards. Unless `backfill_days` is set, only work finished after the source was switched on is read, nothing from before and nothing finished while it was off. An issue reopened before it was read is read once it is closed again and has settled.

**Starting with what finished lately.** By default a source reads nothing finished before it was switched on, so that switching it on never pours an unknown amount of history into a project. With `backfill_days` (0 unless set, at most 365) it also reads the pull requests merged, the issues closed and the releases published in that many days before it is switched on, oldest first, each once. It reads them over several runs, at most 25 items a run, so 600 items take 24 runs, and an item finished meanwhile takes its turn in the order they were finished. Both the list of what changed and the first daily check reach back that far; in a busy repository that first check can find more than the 1,000 results GitHub's search returns, and the report then says so, while the list still reads every item whose update time moved. Switched off and on again, it reads from that many days before the new switch-on, and what it wrote before is not written again.

**How it finds them.** Every run lists the repository's closed issues and pull requests whose update time moved, and its releases. Once a day it also asks GitHub's search for what was merged or closed since the last such check, because GitHub does not say that closing an issue moves its update time. A run writes at most 25 items; the rest wait for the next run, in the order they were finished.

**Long items.** A description is cut after 1,000 paragraphs, list items, tables, quotes or code blocks (or 250,000 characters), and a comment after 200 (or 65,536 characters), between two of them where it can (a code block cut in its middle is closed), with a line saying so. At most 300 comments and 300 reviews of an item are read, and the note says how many comments were left out. An image or a file that GitHub signs for a few minutes (an address on a GitHub host whose query holds `jwt`, `token` or `X-Amz-Signature`) is written without its query.

**Setting it up.** On GitHub, make a fine-grained personal access token limited to the one repository, with read-only access to Pull requests, Issues and Contents. An organisation can require that its owners approve such a token: until they do, GitHub answers 404 for the repository, as for one that does not exist, and the run fails with a reason that says the token may lack the permission or still wait for the organisation's approval. The operator puts the token in the server's environment, never in {{name}} (with Docker, a line in `deploy/cairn.env`), and adds the source, which starts off:

```text
python -m cairn.server source-add --db cairn.db --name code --kind github --config '{"token_env": "GITHUB_TOKEN", "repo": "quillworks/quill"}'
```

Everything but `token_env` and `repo` is optional: `items` (`["pulls", "issues", "releases"]`, or one or two of them to read less), `comments` (true), `settle_minutes` (30), `lookback_days` (30: how many days it keeps its own record of what it wrote, which spares GitHub a second read; an item is written once either way), `backfill_days` (0: how many days before the switch-on it also reads) and `base_url` (`https://api.github.com`; for GitHub Enterprise Server, `https://HOST/api/v3`). For a source that names no project yet, the page proposes `github`. Then an owner or an admin switches it on under **Workspace**, then **Sources**, and chooses its project ([Sources the server reads](#sources-the-server-reads)).

**When GitHub says no.** A refused token, or a 404 for the repository, fails the run, and the Sources page shows the reason with the variable and the repository named. A 403 fails it too and names what was asked for (the list of issues and pull requests, a pull request, its comments or reviews, or the releases), so you know which permission is missing. A message of GitHub's own is shown only when it holds nothing that looks like a credential. When GitHub asks the server to slow down (a rate limit), the run stops there without failing, its report says so, and the next run goes on from where it stopped. When the daily check's search is refused, the run does not fail: what the listing found is written, and the report says the search was refused.

## Check a recipe before you leave it running

1. Run it once by hand and open the page it built. Every claim should open its source lines.
2. Run it again on the same items. A note with the same text and the same `source_ref` answers `unchanged`. If a note is stored a second time, the agent worded it differently, and {{name}} took that for a change: make the instruction ask for the source's own words where it can, and run the recipe for a period that has ended.
3. An owner or an admin sees the notes under **Workspace**, then **Audit log**, with the agent's name. The connection shows the one project it reaches under **Agents**, or under **Keys** when the job uses a key.
4. Ask another agent connected to the same project a question the notes answer.

## A connector of your own

**What it is.** For a system {{name}} does not read itself, you can write a connector: a small web service of your own, in any language, that serves that system's items. Each run, the server asks it for what is new, and it answers with a list of items, each with an id and a text. The server does the rest: the schedule, the switch, the bounds, the guard against secrets, and writing each item once. What a connector must do is the connector protocol, version 1, written down in the repository's `docs/connector-protocol.md`.

**Three steps.**

1. Write the connector, or copy the reference one in `examples/connector/`: one file of Python, standard library only, that serves the Markdown files of a folder. Change the part that lists the items to read your own system.
2. Check it before a source reads it. `connector-check` asks the connector as a source would and prints one line per rule of the protocol, `ok` or `FAILED`; it exits 0 when every rule holds and 1 when one fails. It writes nothing, and never prints the secret or an item's text:

   ```text
   python -m cairn.server connector-check --url https://feed.example.test/items --secret-env FEED_SECRET
   ```

3. Add it as a source, which starts off, then switch it on:

   ```text
   python -m cairn.server source-add --db cairn.db --name feed --kind connector --config '{"url": "https://feed.example.test/items", "secret_env": "FEED_SECRET"}'
   ```

**Once per id.** Each item is written once, by its id: what the connector later sends under the same id is not written, so an edited item needs a new id. An item that was refused is judged again when it comes again.

**The secret.** The config names the environment variable that holds the secret (`secret_env`), never the secret itself. The server sends the secret only as the request's Bearer, and only to the connector's `url`, which is `https`, or `http` only to `127.0.0.1` or `localhost`.

**Off until switched on.** A connector source is added off, and reads only what happens from its switch-on on: an item dated before then is not written. For a connector source that names no project yet, the Sources page proposes a project named like the source itself.

**Its notes and the model.** {{name}} cannot know whose words a connector brings, so a page that holds its notes is not sent to the model the operator connects to arrange pages unless the operator allows it, as for the Google sources ([Sources the server reads](#sources-the-server-reads)).

**When it fails.** The Sources page shows why in words: for instance that the connector refused the secret in the variable named (HTTP 401 or 403), that it answered HTTP 500, or that its answer breaks protocol 1, and how. A failed run moves nothing on, so the next run asks the same request again. A run that worked but stopped early says why under the source's counts: the connector asked to slow down (HTTP 429), or it still had more after 20 answers, the most one run asks for; when nothing it gave was new, it says that too.

## Sources the server reads

Only the command line adds or removes a source from a config, or registers an account, because keys live in the server's environment ([one exception](#a-google-account-connected-in-the-browser)); a person makes sources from an account on the Sources page ([below](#sources-made-from-an-account)). No agent adds one.

**Which workspace.** A source belongs to one workspace. `source-add` and the other source commands act on the default workspace unless `--workspace ACCOUNT` names another. Where each person has a workspace of their own (`serve --join personal`, which is what `serve` does unless told otherwise), a source added without it goes to the default workspace, not to theirs: name the person's workspace, and `workspace-list` prints the ids.

**On or off.** A Langfuse source is on as soon as it is added. A kind of source may start off instead, as one that reads people's meetings, mail, calendar or documents should: a source of that kind is added off, and reads nothing until someone switches it on. A source that is off is not read at all: a sync passes over it and nothing is written. A run already under way at that moment may finish the request it is making, and writes nothing more.

**Who switches it.** An owner or an admin opens **Workspace**, then **Sources**, and switches a source on or off there. Every owner and admin sees every source on that page, and any of them can switch any source off; where a source writes is named only for the projects you reach, and the others show as one row, "a project you do not reach". A member sees only their own sources there. The operator can do the same with `source-enable --name NAME --project PROJECT` and `source-disable --name NAME`.

**Preview before you switch it on.** On the Sources page, **Preview** reads a source once, as a run would, and shows what it would bring: how many items and the first 20 titles. It stores nothing: no note, no place in the source, no record of an item. A source that is off is read from the days you give, 1 to 30 (7 unless you give one), except Langfuse, which looks back over the same week its first run does, whatever the number; a source that is on shows what its next run would bring. Who may switch a source on may preview it, and no agent can. A title that holds the source's credential is shown as held, an item for a project you do not reach is counted and not shown, and for GitHub, Linear and a connector of your own the preview also counts the items the source wrote before. When the service cannot be read, the preview says why; when the variable that holds the credential is not set, it sends nothing at all. The operator can do the same with `source-preview --name NAME [--days D]`.

**When a run fails.** The Sources page shows the reason as the run gave it, in words. That reason can name the environment variable a credential is read from, so the operator knows which one to check, except for a source made from an account. It never holds the credential.

**Where it writes.** Switching a source on is where you choose its projects: tick existing ones. An owner can also type the name of a new project: it is made private, so only they and the workspace's other owners reach it until they share it. For a source that names no project yet, the page proposes its kind's project, `meetings` for Granola: it ticks a project of that name when you reach one, and otherwise, for an owner, fills in the name as a new project. Tick another or type another name to write elsewhere. An admin chooses only among projects every member reaches and that the admin may write to: making a new project for a source, or sending it into a project private to some people, is for an owner, because the source may read someone's mail, calendar or meetings. On a source that is on, **Change where it writes** moves the next notes to other projects; what it wrote before stays where it is. Deleting a project takes it out of every source that writes into it, and a source left with no project is switched off: a new project of the same name gets nothing until someone switches the source on and chooses it. Making a project private, so that no member reaches it any more, does the same when anyone but an owner does it.

**{{name}}'s own model reads them only if the operator allows it.** When the operator connects a model to arrange pages, a page that holds a note from Google Meet, Google Calendar, Gmail or Google Drive, or from a connector of your own, or a note whose `source_ref` says it came from a meeting, a mail thread, a calendar, a document or a to-do list ([the forms](#what-every-recipe-shares)), is not sent to it unless the operator sets `<PREFIX>_SOURCE_NOTES=send`, which sends it except in a project its people keep from every model; {{name}} tidies that page itself instead. This is about the model the operator connects to arrange pages. An agent a person connects reads what that person reaches, these notes included, and hands what it reads to the model it runs on: that is the agent's doing, and this setting does not change it.

**From then on.** Switching a source on reads only what happens from then on: each run hands the source the moment it was last switched on, and a kind that starts off reads nothing that ended before it. Langfuse is the exception: a trace is read because a person marked it, so a mark made while the source was off is read once it is on again. A [Linear](#issues-from-linear-read-by-the-server) or [GitHub](#finished-work-from-github-read-by-the-server) source given `backfill_days` is the exception: it also reads what finished in that many days before it was switched on, the time it was off among them.

### Sources made from an account

**For the operator: register an account.** An account is a sign-in at a service, registered once, for the workspace or for one of its people:

```text
python -m cairn.server credential-add --db cairn.db --name ada-mail --kind gmail --config '{"credentials_env": "ADA_GOOGLE"}' --for ada@example.com
```

Its config holds only what the account is: the variables that hold its secrets, its address (`base_url`, or a connector's `url`) and, for Google, its `subject`; never what a source reads, `projects` or a secret itself. Alone it reads nothing. `credential-list` lists the accounts; `credential-remove --name NAME` disconnects one.

**Choose what and where.** Under **Workspace**, then **Sources**, choose your account (or, for an owner or an admin, the workspace's), and the page asks for the rest: a name, one per workspace, what it reads, such as a Gmail label or a Drive folder, nothing filled in reading everything, and one project, or a new private one. **More choices** holds its reading options: left empty, each keeps its usual value. You never name a variable, an address or a subject. One that would read everything is refused in its kind's words (a calendar must be named: `primary` is your main one; a Google Meet source names one meeting by the code in its address, `abc-mnop-xyz`). **Preview** reads once what saving would bring, checked as saving is but for the name, and stores nothing; **Save, off** adds it off, Langfuse's too. With a key: `POST /api/integrations/preview` (that body and `days`), `POST /api/integrations`, then `/api/integrations/NAME/on`; `GET /api/integrations` lists them.

**Whose account.** An account registered `--for` a person is theirs: only they make sources from it, switch them on and choose where they write, and only into a project they manage (one they made, one giving them `manage`, or a new private one). Another owner or admin may switch such a source off, never on or elsewhere. An account registered for no one is the workspace's, and its sources are routed as any other: an admin sends one only where every member reaches. When the person no longer writes where it writes, or leaves, its next run writes nothing and switches it off, the page saying beside Off "the person whose account it reads no longer writes into where it writes". A member does this with their own account only; an owner disconnects it. Two sources of one account never write one item: a message with both labels (matched case-blind) is held, listed for both under **Held** with what frees it, until it leaves the look-back window: counted, unwritten. Where two choices may meet, the form refuses a second: one Granola account makes one source (a note can be in any number of folders); a Calendar account never has `primary` beside another calendar, or one calendar twice in any case. Two made before this rule stay; while both are on, neither reads or writes anything, as the page says: switch one off.

**Four endings, each different.** The page asks for the name before removing or disconnecting.

- *Switch off* (`POST /api/integrations/NAME/off`): it reads nothing until switched on; it keeps its place, its record of items and its notes; switched on again it reads nothing from while it was off.
- *Remove the source* (`DELETE /api/integrations/NAME?confirm=NAME`): it reads nothing more; it forgets which items it wrote, so a source made again starts from its own switch-on; its notes stay where they are.
- *Disconnect the account* (`DELETE /api/credentials/NAME?confirm=NAME`, or `credential-remove`): its sources are switched off in the same write ("its account was disconnected") and stay listed, off, saying so, with Remove only; their notes stay. An owner does it, or its person when they own or administer the workspace; an owner sees other people's accounts listed too.
- *Forget what came in*: one note at a time, from the page that cites it, or the whole project. Nothing takes back all of a source's notes at once.

### One person's own Google authorisation

The Google Meet, Gmail, Google Calendar and Google Drive sources can each read as one person who authorised them, with no super admin. `google-authorize` obtains that person's credential, once for each credential:

1. In a Google Cloud project, enable the API of each source the credential is for, set up the consent screen, and create an OAuth client of type **Desktop app**. Download its JSON file when you create the client: Google's pages say its client secret can be downloaded only then.
2. On the computer whose browser the person uses, run the command with the client's file, the kind of each source the credential is for (`gmail`, `google_calendar`, `google_drive` or `google_meet`, one `--kind` each) and a file that does not exist yet:

   ```text
   python -m cairn.server google-authorize --client client_secret.json --kind gmail --out gmail-credential.json
   ```

   It prints an address and waits for the person, 300 seconds unless `--wait` gives from 30 to 900. The address is for this run and this person only: do not pass it on, since whoever signs in through it first chooses the Google account the source reads. The person opens the address in that browser, signs in and grants every scope it asks for. Google then sends the browser back to the command on 127.0.0.1, which is that same computer, so a browser on another computer cannot finish it. The command writes the credential on one line to the file `--out` names, readable by its owner only. It never overwrites a file, never opens a browser itself and opens no database. Give `--out` a file in a folder only you can write to: someone who can write to the folder could swap the file after the command has written it. Do not run it inside the server's container or on the server: there the browser could not come back to it, and the file would be written where the person cannot reach it. It needs {{name}} on that computer and nothing else: no database and no running server.
3. Put the file's one line in the server's environment under the name the source's config gives as `credentials_env`, then delete the file: it holds the client secret and the refresh token. Check first that the Google account the person signed in with is the one meant: the source reads as that account.

**How long it lasts.** A refresh token stops working if the person revokes it, after six months unused, and after 7 days while the client's consent screen is External and in "Testing"; the source then says to authorise again. While the consent screen is in "Testing", the person must be listed among its test users. Google's pages do not say whether a restricted scope, such as Gmail's or Google Drive's, can be granted to an unverified client that one person uses once its consent screen is "In production". Try it before you rely on it: {{name}} promises nothing about it.

### A Google account connected in the browser

Off unless the operator switches it on. Then a person on the operator's list, a member included, connects their own Google account on the Sources page, with no file to move; a member makes sources from it as from any account of theirs ([above](#sources-made-from-an-account)) and sees no one else's.

**For the operator.** Give this client a Google Cloud project of its own, and in it create an OAuth client of type **Web application**, with the redirect `<public url>/integrations/google/back`: revoking one of its tokens ends that Google account's permission for every client of the project, a sign-in client or a source's own client there included. Put the client's JSON, on one line, in `CAIRN_GOOGLE_CONNECT_CLIENT`, and 32 random bytes in base64 (`openssl rand -base64 32`) in `CAIRN_GOOGLE_CONNECT_KEY`, the key the tokens are sealed with. Start `serve --google-connect` with `--sign-in` and `--public-url`, and one `--google-connect-for ADDRESS` for each person it is offered to. A variable missing or malformed stops the server before it opens the database, naming the variable. While the consent screen is in "Testing", a connection lasts as [above](#one-persons-own-google-authorisation) says, and only its listed test users can connect. Gmail's and Google Drive's scopes are restricted: for anyone else, Google's pages ask for its verification of the app and, since the server holds what it reads, a yearly security assessment, unless an exception applies, such as a consent screen of type Internal used only by its own organisation's people. Keep the key apart from the database and its backups: lose it, and every account must be connected again.

**What stays as it is.** The ID token's claims are read from the token endpoint's own answer, over TLS, without checking its signature. Scopes Google reports beyond those asked for are recorded as granted. The authorisation code passes through the address of `/integrations/google/back` and `/integrations/google/finish`, so the browser's history holds it; Google accepts it once. Reconnecting does not revoke the token it replaces. With the switch off, or from the command line, removing an account, a person or a workspace ends nothing at Google.

**Before this is offered to anyone**, check four things against the real Google: one account connected for two services (whether each consent returns a refresh token, what `scope` comes back, what one revocation ends); the ID token a Web client's exchange returns (the exact `iss`, the type of `email_verified`, an address Google has not verified); the way back in Safari, Firefox and Chrome; and a revocation under way while the same Google account connects elsewhere (the new connection is marked to be reconnected: does Google end it?).

**For a person.** Under **Workspace**, then **Sources**, **Connect Google** for one service sends you to Google, which asks for that service's one read-only permission. Nothing is read until you make a source from the account and switch it on. **Reconnect** replaces the token and keeps the account's name, its sources and where they write; consented as another Google account, it changes nothing here. **Disconnect**, with the account's name typed as a check, deletes the token and switches the account's sources off; their notes stay. It ends Google's permission too, unless the same Google address is still connected elsewhere on this server, in any workspace: then the permission ends when the last one is disconnected. An owner may disconnect any account of the workspace; removing a person, or deleting the workspace, disconnects theirs the same way. When Google cannot be reached, the account is removed anyway; its token waits, sealed, and the server sends the revocation again a minute later and at its next start, and the page says where to end the permission yourself: in your Google account's settings, among the third-party apps and services with access to your account. A run whose token Google no longer accepts writes nothing and says to reconnect your Google account.
