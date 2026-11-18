"""Slack, both ways (K1): one Slack app for each workspace, made by its owner, through which a
person files a Slack message as a note or writes one, and asks the bot to find something.

The parts
- cairn.slack.verify: what a request Slack sends must be before anything of it is read (the
  body's cap, the timestamp, the signature) and the Envelope a handler is given.
- cairn.slack.dispatch: the registries a handler joins (`on_event`, `on_command`,
  `on_action`) and what it is handed (Context).
- cairn.web.slack: the switch (`serve --slack`), the three addresses Slack calls and the
  background loop; cairn.store.slack: what is kept.

Every module of this package is imported when the addresses are made (cairn.web.slack), so a
module that registers handlers needs no other change to be heard."""
