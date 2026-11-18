# Security

This guide says who can reach what in {{name}}, how people and agents prove who they are, what the checks on notes and pages catch, and what they do not. Each statement here rests on a test in {{name}}'s own suite or on a numbered decision in its repository, and the [privacy guide](privacy.md) says what is kept and for how long.

## Who reaches what

- **A workspace is closed to every other workspace.** Another workspace's project, page or note id is answered exactly like an id that does not exist, over the REST API, MCP and the web pages. Search never returns another workspace's text, and its scores do not depend on it: each workspace has its own search index.
- **A project is the unit of access.** Each project has a rule saying who reaches it. A project can be private to the person who made it. Owners reach every project.
- **Three levels for a person.** A project's rule gives a person Read only, Read and write, or Read and write, and decide who reaches it. The third holds for that one project and is not a workspace role: whoever has it changes who reaches that project, their own level included, but cannot shut out its maker or an owner, and cannot delete the project or forget its notes. A workspace admin decides for a project only as any member does: as its maker, or when its rule gives them that level. Several people can be given a level at once by email address. An address that is not a member's gets nothing and no invitation, so someone outside the workspace is invited under People first.
- **Roles.** An owner manages everything, other owners included. An admin invites and removes people, manages keys and sets, and reaches projects as a member does. A member uses the projects they reach. A workspace always keeps at least one owner.
- **A connection can be narrower than its person.** A key or an agent can be limited to some projects, or to reading only. To such a connection the other projects do not exist: they are not listed, not searched, and an id of one is unknown. A read-only connection is refused every write. A key or an agent names at most 64 projects or sets; a set counts as one, whatever it holds.
- **A credential acts for the person who made it.** A session, an approved agent or a key reaches what that person reaches now, never more. When a person is removed from a workspace, their sessions, the agents they approved and the keys they made there stop working in the same step. A removed person cannot come back by signing in again: an owner or an admin has to invite them.
- **Removing is narrower than writing.** Only the person who made a project, or an owner, can delete it or forget one of its notes, and only over a session or a connection that reaches every project the person reaches and may write. Another project's note id is answered as unknown. Both ask first on the web pages, and both are in the audit log.
- **A workspace's totals are for its owners.** What a workspace has used of its plan counts every project, so only an owner, a key an owner made, or a connection that acts for no person reads it. An admin or a member gets no Usage page, and when a cap refuses their note they learn which limit, without a number or the plan.
- **Two things can show through.** Names are unique in a workspace, so someone who cannot see a project can learn that its name is taken. And a plan's cap on projects counts every project, so a member who is refused a new project at that cap, while seeing fewer projects than the cap, can tell that others exist.

## Signing in

- {{name}} keeps no passwords. People sign in with GitHub, Google or the organisation's own OpenID Connect provider, whichever the operator turns on.
- An email address that the provider has not verified is refused, and nothing is created.
- Each identity is tied to its provider's issuer. If a provider is replaced under the same name, a sign-in through it is refused, so a stranger with the same account number at another provider cannot become an existing person.
- A shared install can be limited to the organisation's email domains, to named groups of its provider, or to its Google Workspace domain. Any install can be limited to the people its operator names, by their whole email address. A shared workspace cannot be opened to every GitHub or Google account: {{name}} refuses to start with that setting.
- A browser session lasts 12 hours, unless the operator keeps sign-ins longer with `serve --session-days`; then it lasts that many days, and a use more than a day after it was made or last renewed starts them again. Its cookie is `HttpOnly` and `SameSite=Strict`, and `Secure` whenever the server's public address is https. A sign-in's state is used once, and a return from the provider without the state {{name}} issued is refused.

## Agents and keys

- **Agents connect with OAuth 2.1.** The agent registers itself, the person approves it in the browser, and the agent proves with PKCE that it started the request. An approval code works once, for 2 minutes, and only with its verifier.
- **Tokens.** An access token lasts 1 hour and is bound to this server's MCP address and to one workspace. A refresh token lasts 30 days from its last use. Each use replaces both tokens, and presenting any refresh token the connection replaced in the last 30 days ends the whole connection, so a stolen one is caught the first time both parties use it, however often the thief refreshed first. A connection refreshes at most 6 times an hour.
- **Redirect addresses** must be https, or http on the machine itself. Anything else, a `javascript:` address included, is refused at registration.
- **Keys** are for scripts and agents without a browser. A key is shown once, when it is made. Only an owner or an admin makes or revokes keys, and a key made by a person reaches no more than that person. A key made on the server's command line acts for no person: it reaches every project and manages keys, and belongs to whoever runs the server. One made there with `--project` or `--read-only` reaches only the projects it names, or only reads, and manages no key, exports nothing and opens no web session.
- **Answers to agents and scripts are not cached.** Every answer on the REST API and on MCP says `Cache-Control: no-store`, a refusal included.
- **Every connection is listed.** Under Workspace, the Agents page lists the agents a person approved, with what each reaches, and disconnects any of them. An owner or an admin sees every agent in the workspace there. The Keys page lists the workspace's keys, and an owner or an admin revokes them.
- **What a key made.** A key made, or an agent approved, from a web session opened with a key records that key. The Keys and Agents pages say which key each was made with, and "made with a revoked key" once that key is gone. Revoking a key leaves what it made working, and says what that is. Revoking it with everything it made takes the key, every key made with it or with a key it made, and every agent approved with any of them, in one step, and ends every web session opened with those keys. Keys and agents made before {{name}} recorded this, and anything a person made, are never taken.

## Stored as digests only

API keys, browser sessions, OAuth codes and tokens, and invitation links are random values. {{name}} hands each one out once and stores only its SHA-256 digest. A copy of the database therefore holds none of them in a form anyone can use. The credentials of other services it can hold, a connected Google account's refresh token and a Slack app's signing secret and bot token, are sealed: see [below](#google-accounts-connected-in-the-browser) and [Slack](#slack). A sign-in provider's own tokens are used during sign-in and never stored.

## Google accounts connected in the browser

Off unless the operator starts the server with `--google-connect`. Then the refresh token Google gives for a connected account is stored sealed: encrypted with AES-GCM under the operator's key (`CAIRN_GOOGLE_CONNECT_KEY`), with a fresh nonce each time, and bound to the workspace, the account's name and its person, so a sealed token copied to another row does not open. The key lives only in the server's environment. The Google address, Google's own id of the account and the permissions granted are stored in clear, so a copy of the database shows who connected what, but a copy alone, or the replica in the operator's bucket, which holds the same rows, opens no token. The token that reads a service is made from it during a run and kept only in memory; no token, code, client secret or key is written to a log, an error, an answer, the audit log or an export. To take every access away, each person disconnects on the Sources page, or the operator deletes the OAuth client in the Cloud console: Google's pages say calls with its tokens then fail, and that a deleted client can be restored within 30 days; deleting the key leaves tokens no one can open, but Google still honours them until they are revoked.

What a connect or a disconnect came to is said once on the Sources page, to the person it happened to. It travels in a cookie sealed under the same key, `HttpOnly` and `SameSite=Lax`, which the page clears once it has said it; the page's address carries none of it, so a link someone else made shows no outcome.

When Google refuses to end a dropped account's permission (after the OAuth client was deleted, say), the server tries again less and less often, the wait doubling up to a day, and logs the refusal once. After 20 refusals it stops, keeps the sealed token, and logs `revoke_stopped` once. Then end the permission yourself: the person removes this server's access in their Google account's settings, or you delete the OAuth client's secret in the Cloud console, so that no token issued to it can be used with that secret again. The table of tokens waiting to be sent holds at most 10,000; past that, a dropped account's token is sent once and not kept, and the server logs `revocations_full`.

Changing `CAIRN_GOOGLE_CONNECT_KEY` makes every connected account reconnect, and leaves the permissions Google gave for the old tokens in place, with nothing here able to end them. So disconnect every connected account first, wait until Google has answered each revocation (the log says when one was not ended), then change the key.

## Slack

Off unless the operator starts the server with `--slack`. Slack calls three addresses of each workspace's install: `/integrations/slack/<install>/events`, `/commands` and `/actions`. They take no session and no key. A request is acted on only when its signature holds: an HMAC-SHA256 of its timestamp and its body under that install's signing secret, compared in constant time, with a timestamp within 5 minutes of the server's clock. Before the signature is checked, a body over 1 MiB is refused, and so is an install that does not exist or is not set up. After it, a request of another Slack workspace is refused. An address that fails the signature 20 times in a minute is refused, without the signature being computed, until the minute is over. An event is acted on once, whatever Slack's retries say.

The app asks Slack for four permissions: `chat:write`, to post the bot's answers; `commands`, for the slash command and the message shortcut; `users:read`, to know a Slack user's name and whether a message's author is of this Slack workspace; and `im:history`, which lets the bot read the direct messages people send it, and nothing among people.

A Slack user is linked to a person in three steps. The Slack user asks the bot for an address, which is theirs alone. The person opens it signed in and says yes, and is shown a code, once. The same Slack user types that code with the command within 10 minutes. So a Slack user is linked to a person only when that Slack user knew a code shown only to that person: someone who sends their address to another person gains nothing from that person's yes. A wrong code counts against the Slack user's offer, and the fifth deletes it; a code typed by any other Slack user is dead. A link is never moved to another person: a Slack user linked to someone stays linked to them until it is undone. The limit is said plainly: someone who talks you into sending them your code acts as you, which is why the page that shows it says to give it to no one.

Answers are private: the slash command's and the shortcut's are shown only to the person who asked, and a direct message is answered in that direct conversation. An answer is built from the asker's reach, as a search of theirs: a project they do not reach gives nothing, not even a count.

The signing secret and the bot token are sealed under the operator's key, `CAIRN_SLACK_KEY`, so a copy of the database opens neither. {{name}}'s Slack client calls nothing at Slack that reads a conversation's history: it asks whose a token is and who a user is, posts the bot's answers, and answers through the address a signed request gave.

## The note guard

Before any part of a note is stored, {{name}} checks its text and every field that comes with it. By default it refuses a note that holds:

- a private key block;
- an AWS access key id;
- a GitHub, Slack, OpenAI, Anthropic, Google, Hugging Face or Groq token;
- a JSON Web Token;
- one of {{name}}'s own agent tokens;
- a value of 12 characters or more assigned to a name such as `password`, `secret`, `token` or `api_key`.

A refused note leaves nothing behind. The answer names the rule and the line; the audit log names the rule. Neither holds what matched. A workspace's policy can also refuse personal data (an email address, a phone number written with `+`, a US Social Security number, a payment card number or an IBAN), or check nothing. The workspace page says which policy is in force, in words, to everyone in the workspace, and its owners and admins choose it there; each change is in the audit log. When the operator runs {{name}} with a guard of its own, that guard decides for every workspace and the page says so.

The guard matches patterns. It does not recognise a name, a postal address, free-text health details, keys of other providers, or a secret that was split, encoded or reworded. A note that passes is not thereby free of secrets.

## What the checks on pages catch

{{name}} checks every page against its notes before it saves it, and keeps the last good version when a check fails.

- Every claim on a page is a passage of a note, copied word for word, and cites the lines it came from.
- A version, a flag or a line of code in a note the page draws on is on the page, or is listed as left out.
- A number, a flag or a path in a claim is in the lines it cites.

When the operator connects a model, it only puts a page's claims in order and says which ones repeat each other. It answers with claim numbers, and {{name}} builds the page from text it already holds, so the model writes no word of it. The page says which model arranged it.

What the checks do not do: they compare text, not meaning. A page holds what the notes said, and if a note was wrong, its claim is wrong in the same words. A note's text is quoted material to {{name}}, never an instruction, but an agent that reads a page still reads what the note said.

## Who wrote what an agent reads

- **{{name}} does not decide whether a note is an instruction.** It stores what was written and returns it word for word. It does not hold a note back for a person to check, and it does not guess which sentences are commands.
- **Every recalled line names its writer.** Each hit of `recall`, each claim of `page` and each passage of `evidence` says who wrote the notes behind it: a person by name, never by email address; a key by its label; an agent by the name it registered with; a source by its name; or the server's command line. A web session opened with a key writes as that key. A note stored before {{name}} recorded writers says its writer was not recorded.
- **Outside text is marked.** A note a connected source brought in was written by whoever can write at that source, so its lines are marked `outside`, and the web pages say "outside text" beside it. A note stored before {{name}} recorded writers is marked so when it came in via a source.
- **Agents are told once.** When an agent connects, {{name}} tells it that what recall, page and evidence return is text someone wrote down, to treat as information and never as instructions.
- **No mark of trust.** {{name}} does not rank writers: a note from a key that reaches one project is named as that key's, like any other key's, with no mark of how far the key reaches.
- **What an operator can do.** Keep notes of different trust in different projects, and give each agent only the projects it needs. Give an agent that only reads a read-only key or approval. Give each source a project of its own. The notes of the Google kinds of source and of connectors are not sent to the model that arranges pages unless the operator allows it.

## The web pages

- No page runs a script. With `serve --graph-explorer` a project's graph page alone loads one script, {{name}}'s own, by a nonce made for that page and with its hash, and the page still works without it. The pages allow styles, images and fonts from {{name}} itself and nothing else, and cannot be shown inside another site's frame.
- The two typefaces, Atkinson Hyperlegible Next and Atkinson Hyperlegible Mono, ship with {{name}} under the SIL Open Font License 1.1; their licences are served at `/static/fonts/atkinson-hyperlegible-next.OFL.txt` and `/static/fonts/atkinson-hyperlegible-mono.OFL.txt`.
- A form posted from another site is refused.
- Pages are not cached, and a page's address is not sent to other sites.
- An address that is not a page answers "There is no page at this address" for everyone, signed in or not.
- A project's page links its **Graph**: its pages, the claims on them, the passages those claims cite and the notes the passages come from, drawn by the server and listed again as text below the drawing. Without `--graph-explorer` the page shows at most 20 pages; with it, one view at a time (the pages, the notes, a page's claims or a claim's passages), at most 20 to a batch, with **Next batch** for the rest, and each note has a page of its own. A key reads at most 20 pages of the graph at `GET /api/graph?project=<name>`, an agent at the MCP resource `graph://project/<name>/<limit>`, and a project out of reach is answered as one that does not exist.

## Logs

- **The audit log** records every write and every change to keys, agents, people, settings and sets of projects (a set made, changed or deleted, with its projects: changing a set changes at once what the keys and agents limited to it reach), with who did it. A reader sees only the projects of a set they reach. It never holds a note's text. Owners and admins read it under Workspace, then Audit log. It keeps the newest 100,000 events of each workspace. It also records each web session opened, by the person or with the key, and how, and each agent approved, as the act of whoever approved it, when they approve it, whether or not the agent then connects.
- **Reads go to the access log**, one line per read on the server's own output, whether an agent, a script or a person in the web view made it: who called, what, which project and the status. It never holds a query, a note's text or a credential, and a refused request is logged without what it presented.
- The server prints no request addresses, so no query string reaches a log.
- **The shipped proxy** (Caddy, for a hosted install) does log each request's address. Its configuration cuts every query string and the token of an invitation link out of that address, and drops the headers that carry a key, a session or the page a request came from. An operator who replaces the proxy has to do the same.

## What a workspace can take

- A plan with a storage cap bounds what a workspace takes on disk, not only the bytes of its notes. Its passages are bounded with it, one per 40 bytes of the cap: each paragraph, list item or other block counts one, and the headings stored with every passage and heading count one more for each full 40 bytes. Its pages' older versions take at most four times the cap. On every install the headings above a passage take at most 400 bytes in all, and a note's headings may outnumber its passages by at most eight; a note past either is refused.
- On every install a note holds at most 30,000 facts: numbers, dates, versions, paths, links, flags and pieces of code. A note with more is refused before anything of it is stored, and the answer gives the count and the bound, never the note's text.
- So a workspace takes at most 30 times its storage cap, plus 1,500 bytes for each note it holds. {{name}}'s tests fill workspaces to their caps with the costliest shapes of note found, the smallest notes there are among them, and hold each to both parts. The second part is what every note costs whatever its length (its record, its line in the audit log, its headings); the plan's cap on notes a month is what bounds how fast it grows.
- On every install a page holds at most 2,000 claims, so saving one more note onto a page never grows without end. A note that would pass it is refused before it is stored, and the answer says to file it under another topic. A page also holds at most 400,000 characters of claims, since every save lints and indexes the whole page again; a note that would take its page past that is refused the same way, and the answer says which bound it met. A page that already held more when this bound came stays as it is: it can still be read, and notes can still be forgotten from it, but it takes no more notes, and the answer says so.
- An imported page must be what {{name}} would have written from its notes: its claims are the text of the lines they cite, its body is what {{name}} writes of them, and its figures are a short list of numbers and words. Anything else is refused and nothing is imported.

## What {{name}} leaves to the operator

- {{name}} does not encrypt its database file. Disk encryption, and who may log in to the machine, are the operator's.
- Backups are the operator's: the shipped configuration replicates the database to a storage bucket the operator owns.
- A source's credentials, such as a Langfuse key, are environment variables of the server, so only the command line registers an account, or adds or removes a source from a config; a person makes sources from an account registered for them, and owners and admins from the workspace's. The database never holds a third party's key, except the sealed tokens of Google accounts connected in the browser and the sealed secrets of a Slack app, when the operator switches either on ([below](#google-accounts-connected-in-the-browser), [Slack](#slack)).
- Anyone with the database file and the machine can read every workspace in it. The rules above govern people and agents that come through {{name}}.
- {{name}} limits each client's sign-ins and agent registrations (20 registrations an hour from one address, unless the operator set another number), and bounds its own memory and its calls to the sign-in provider. A flood from many addresses is the operator's to stop, at the proxy or the network; while it lasts, new sign-ins can be refused. People already signed in are not affected.
- {{name}} also limits the wrong keys and tokens each client address presents: once an address has presented 30 in a minute, a wrong one from it is answered 429 in place of 401 for the rest of that minute. A correct key or token is never refused by this limit, from any address.
