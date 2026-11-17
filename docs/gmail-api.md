# Gmail API v1: reading one person's labelled messages

Researched 2026-11-13. No Gmail API was called. Every page below was fetched that day with a
plain HTTPS GET (`?hl=en`, User-Agent `curl/8`), converted to text and read in full unless a note
says otherwise; Java, Node.js, Python and Apps Script samples were skipped, HTTP and JSON samples
read. Final URLs are given after redirects, with each page's "Last updated" line (Help Center
pages show none). The token side (service account with domain-wide delegation, one user's refresh
token, the general error envelope, when a refresh token stops working) is in google-auth.md and
is not repeated here.

Reference (base https://developers.google.com/workspace/gmail/api/reference/rest; every method URL
is relative to `https://gmail.googleapis.com`):

- **REST** = .../reference/rest (2026-04-15). Read the service section and the method tables for
  `users`, `users.drafts`, `users.history`, `users.labels`, `users.messages`, `.attachments`,
  `users.settings`; the later settings tables skimmed. **.../reference/rest/v1 returned 404.**
- **USERS** = .../v1/users (2026-04-15); **PROFILE** = .../v1/users/getProfile (2026-04-15)
- **LABELS** = .../v1/users.labels (2026-04-15); **LABELS-LIST** = .../users.labels/list
  (2026-04-15); **LABELS-GET** = .../users.labels/get (2026-04-15)
- **MSG** = .../v1/users.messages (2026-05-06); **MSG-LIST** = .../users.messages/list
  (2026-04-15); **MSG-GET** = .../users.messages/get (2026-04-15)
- **FORMAT** = .../v1/Format (2025-03-24; the target of MSG-GET's `Format` link)
- **ATT** = .../v1/users.messages.attachments (2025-03-24; it defines MessagePartBody);
  **ATT-GET** = .../users.messages.attachments/get (2026-04-15)
- **THR** = .../v1/users.threads (2025-03-24); **THR-LIST** = .../users.threads/list (2026-04-15);
  **THR-GET** = .../users.threads/get (2026-04-15)
- **HIST** = .../v1/users.history (2025-03-24); **HIST-LIST** = .../users.history/list (2026-04-15)
- **DISCOVERY** = https://gmail.googleapis.com/$discovery/rest?version=v1 (listed on REST;
  `revision` **20261106**). Used to check scopes, parameters, field names, types and formats.
  Tagged **[discovery]**. Its text was dumped to `txt/discovery.txt` for the quotation check.

Guides (base https://developers.google.com/workspace/gmail/api):

- **G-OVERVIEW** = .../guides (2026-09-18)
- **G-SCOPES** = .../auth/scopes (2026-09-10)
- **G-SYNC** = .../guides/sync (2026-09-15)
- **G-FILTER** = .../guides/filtering (2026-09-10)
- **G-LISTMSG** = .../guides/list-messages (2026-09-10)
- **G-LABELS** = .../guides/labels (2026-09-10)
- **G-THREADS** = .../guides/threads (2026-09-10)
- **G-BATCH** = .../guides/batch (2026-09-10)
- **G-ERRORS** = .../guides/handle-errors (2026-09-15)
- **G-PUSH** = .../guides/push (2026-09-15). Read for history ids and labels only; Pub/Sub set-up
  steps skimmed.
- **G-PERF** = .../guides/performance (2026-09-10). Its examples use a fictional "Demo" API.
- **QUOTA** = .../reference/quota (2026-10-16)
- **RELNOTES** = https://developers.google.com/workspace/gmail/release-notes (asked for as
  .../gmail/api/release-notes, which redirects here; the page says "Last updated 2026-09-03" but
  carries entries up to November 10, 2026).

Other pages:

- **WS-AUTH** = https://developers.google.com/workspace/guides/auth-overview (asked for as
  .../gmail/api/auth/about-auth; 2026-09-03).
- **OAUTH-SCOPES** = https://developers.google.com/identity/protocols/oauth2/scopes (2026-09-14).
  Read only the Gmail API v1 section and searched the rest for Gmail scopes.
- **RESTRICTED-VERIF** = https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification
  (2026-11-10).
- **SENSITIVE-VERIF** = https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification
  (2026-11-10). Read the summary, "Understand sensitive scopes" and "Exceptions"; the submission
  steps (the same as RESTRICTED-VERIF's) skimmed.
- **VERIF-HUB** = https://support.google.com/cloud/answer/13463073 (asked for as
  .../cloud/answer/9110914, WS-POLICY's "OAuth Application Verification FAQ" link; a hub page with
  links only). Of its links, 13461325 (titled Submitting your app for verification), 13463816 (Annual
  Recertification) and 13464018 (Changes to approved app) were fetched for their titles only and
  not read or saved.
- **VERIF-FAQ** = https://support.google.com/cloud/answer/13463817 ("Frequently Asked Questions").
- **VERIF-REQ** = https://support.google.com/cloud/answer/13464321 ("Verification requirements").
  Searched for internal, domain-wide, service account, delegation, model and security assessment;
  the brand-verification sections skimmed.
- **NOT-NEEDED** = https://support.google.com/cloud/answer/13464323 ("When is verification not
  needed").
- **RESTRICTED-LIST** = https://support.google.com/cloud/answer/13464325 ("Restricted Scopes").
  Read the Gmail section; the other APIs skimmed.
- **SEC-ASSESS** = https://support.google.com/cloud/answer/13465431 ("Security Assessment").
- **WS-POLICY** = https://developers.google.com/workspace/workspace-api-user-data-developer-policy
  (2026-09-03).
- **UD-POLICY** = https://developers.google.com/terms/api-services-user-data-policy ("Last updated
  February 15, 2024").
- **TOOLS-SAFETY** = https://developers.google.com/workspace/tools-safety (2026-09-03; linked from
  QUOTA).
- **BLOG** = https://workspaceupdates.googleblog.com/2026/05/agent-tools-and-security-updates-for-workspace-developers.html
  (May 1, 2026; linked from QUOTA). Read the article; the blog's sidebar not read.
- **HELP-SEARCH** = https://support.google.com/mail/answer/7190 ("Refine searches in Gmail").
- **HELP-LABELS** = https://support.google.com/mail/answer/118708 ("Create & manage labels in Gmail").
- **ADMIN-APPS** = https://knowledge.workspace.google.com/admin/apps/control-which-apps-access-google-workspace-data
  (asked for as https://support.google.com/a/answer/7281227; 2026-11-10). Read "Restrict or
  unrestrict Google services", "Restrict access to high-risk OAuth scopes", the access settings
  and "Unconfigured app settings"; the app-list and CSV sections skimmed.

From google-auth.md (not re-read, except a grep): its DWD and SA pages say nothing specific to
Gmail or mail; a grep of both for "gmail" and "mail" finds only the service account's own email
address and the `sub` claim.

Not fetched or not usable: .../reference/rest/v1 (404). No Gmail-specific admin page on
domain-wide delegation was found or fetched.

Tags: **[doc: X]** = stated on page X; **[example: X]** = visible only in an example on page X,
not stated as a rule; **[discovery]** = from the discovery document; **[not stated]** = nowhere in
the pages above. **QUESTION:** marks an open point that matters for the design; **CONFLICT**
quotes two Google statements that disagree.

---

## Headline points (read first)

1. **Nothing in a message says when a label was put on it.** The Message resource has
   `internalDate` (when Google accepted the mail) and `historyId`, and nothing else with a time
   [doc: MSG]. The only record that a label was added is a `labelsAdded` entry in
   `history.list` [doc: HIST-LIST], and history lasts "typically ... at least one week" and "might
   be significantly shorter" [doc: G-SYNC]. After a history gap, a fresh listing cannot tell a
   message labelled yesterday from one labelled a year ago. QUESTION 1.
2. **Labels live on messages, not threads.** "Labels only exist on messages." "If messages are
   added to a thread after you add a label, the new messages don't inherit the existing label
   associated with the thread." [doc: G-LABELS] The Gmail Help Center says the same for the UI
   (paraphrased): with conversation view on, a label put on a conversation goes on the messages it
   holds then, and replies that come later do not get it. [doc: HELP-LABELS] So labelling a
   thread in Gmail labels the messages it holds at that moment.
3. **There may be a narrower read scope than `gmail.readonly`, but only the discovery document
   names it.** DISCOVERY (revision 20261106) lists `https://www.googleapis.com/auth/gmail.messages.readonly`
   ("Read your emails") on `messages.list`, `messages.get`, `messages.attachments.get`,
   `threads.list`, `threads.get` and `history.list`, and `gmail.labels.readonly` on `labels.list`
   and `labels.get` [discovery]. None of the method pages, G-SCOPES, OAUTH-SCOPES or
   RESTRICTED-LIST mentions either scope **CONFLICT**, so their class is **[not stated]**.
   WS-POLICY's rule makes any scope that reads message bodies restricted (section B). QUESTION 3.
4. **`gmail.metadata` cannot read bodies and cannot use `q`.** `format=full` and `format=raw`
   "cannot be used when accessing the api using the gmail.metadata scope", and `q` "cannot be
   used when accessing the api using the gmail.metadata scope" [doc: FORMAT, MSG-LIST].
5. **`after:` with a date means midnight "PST"; with a number it is seconds.** "All
   dates used in the search query are interpreted as midnight on that date in the PST timezone.
   To specify accurate dates for other timezones pass the value in seconds instead" [doc:
   G-FILTER]. Whether `after:` is inclusive is **[not stated]**.
6. **A history id can go stale within hours.** "A `historyId` is typically valid for at least a
   week, but in some rare circumstances may be valid for only a few hours." A stale one
   "typically returns an `HTTP 404` error code"; then "your application should perform a full
   sync" [doc: HIST-LIST]. The 404's body is **[not stated]**.
7. **Rate-limit refusals come as 403 or 429, with different reasons.** `rateLimitExceeded`,
   `userRateLimitExceeded` and `dailyLimitExceeded` are 403s with `"domain": "usageLimits"`; the
   per-user concurrency, bandwidth and daily per-user limits are 429s [example and doc: G-ERRORS].
   Whether `Retry-After` is sent is **[not stated]**; for 429 the page says only "with a retry
   time".
8. **Reading a full message costs 60 of 6,000 quota units per user per minute,** so at most 100
   full `messages.get` a minute per mailbox; `messages.list` costs 5, `history.list` 2,
   `labels.list` 1 [doc: QUOTA]. Projects that used the API between November 2025 and April 2026
   keep their earlier quotas, which QUOTA does not state.
9. **Messages do not change; only their labels do.** "After you create a message, you can't
   change it." [doc: G-OVERVIEW]; on refetch "only the `labelIds` might change" [doc: G-SYNC].
10. **The documentation never shows how a text/plain plus text/html message is laid out.** It
    says only that `parts` "only applies to container MIME message parts, for example
    `multipart/*`" and that body data is base64url [doc: MSG, ATT]. The tree for
    `multipart/alternative` is **[not stated]**.

---

## A. Service, version, base URL, discovery

- Service `gmail.googleapis.com`; service endpoint `https://gmail.googleapis.com`; discovery
  document `https://gmail.googleapis.com/$discovery/rest?version=v1`. [doc: REST]
- Paths are `/gmail/v1/users/{userId}/...`, e.g. `GET https://gmail.googleapis.com/gmail/v1/users/{userId}/messages`
  [doc: MSG-LIST]. "The URL uses gRPC Transcoding syntax." [doc: every method page]
- `userId`: "The user's email address. The special value `me` can be used to indicate the
  authenticated user." [doc: every method page] With domain-wide delegation the token is for the
  impersonated user, so `me` names that user; that `me` works under impersonation is
  **[not stated]** on Gmail pages.
- Guide examples use another host: `GET https://www.googleapis.com/gmail/v1/users/me/messages?q=...`
  [example: G-FILTER] and `POST https://www.googleapis.com/gmail/v1/users/me/watch` [example:
  G-PUSH]. Whether `www.googleapis.com` still serves Gmail is **[not stated]**; use the REST
  endpoint.
- DISCOVERY: `rootUrl` `https://gmail.googleapis.com/`, `batchPath` `batch` [discovery].
- Every request in this connector is a GET with an empty body ("The request body must be
  empty.") [doc: MSG-LIST, MSG-GET, LABELS-LIST, LABELS-GET, HIST-LIST, THR-LIST, THR-GET, PROFILE].
- Partial responses: the `fields` parameter works "with any request that returns response data";
  an invalid one gives "an HTTP `400 Bad Request` status code" [doc: G-PERF]. Gzip needs
  `Accept-Encoding: gzip` and a User-Agent containing `gzip` [doc: G-PERF].

## B. Scopes and their class

### B.1 Scopes that could serve this connector

| Scope | Description (quoted) | Class | Methods it opens here |
|---|---|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | "View your email messages and settings." [doc: G-SCOPES] | Restricted [doc: G-SCOPES, RESTRICTED-LIST] | all: `labels.*`, `messages.list/get`, `attachments.get`, `threads.*`, `history.list`, `getProfile` [doc: method pages] |
| `https://www.googleapis.com/auth/gmail.metadata` | "View your email message metadata such as labels and headers, but not the email body." [doc: G-SCOPES] | Restricted [doc: G-SCOPES, RESTRICTED-LIST] | all of the above except `attachments.get` [doc: ATT-GET]; no `q`, no `full`/`raw` [doc: MSG-LIST, FORMAT] |
| `https://www.googleapis.com/auth/gmail.labels` | "See and edit your email labels." [doc: G-SCOPES] | Non-sensitive [doc: G-SCOPES]; G-LABELS: "the `https://www.googleapis.com/auth/gmail.labels` non-sensitive scope" | `labels.list`, `labels.get` (and label writes) [doc: LABELS-LIST, LABELS-GET; discovery] |
| `https://www.googleapis.com/auth/gmail.messages.readonly` | "Read your emails" [discovery] | **[not stated]** | `messages.list`, `messages.get`, `messages.attachments.get`, `threads.list`, `threads.get`, `history.list` [discovery only] |
| `https://www.googleapis.com/auth/gmail.labels.readonly` | "Read your email labels" [discovery] | **[not stated]** | `labels.list`, `labels.get` [discovery only] |
| `https://www.googleapis.com/auth/gmail.modify` | "Read, compose, and send emails from your Gmail account." [doc: G-SCOPES] | Restricted | reads and writes; too wide for a read-only source |
| `https://mail.google.com/` | "Read, compose, send, and permanently delete all your email from Gmail." [doc: G-SCOPES] | Restricted | everything; too wide |

Other scopes in DISCOVERY that read mail but do not fit: `gmail.messages` ("Read, update, and
permanently delete all your emails"), and the add-on scopes `gmail.addons.current.message.readonly`
/ `.metadata` / `.action`, which G-SCOPES classes as Sensitive or Non-sensitive and describes as
working "when the add-on is running" or "when you interact with the add-on" [doc: G-SCOPES;
discovery]. They appear on `messages.get` and `threads.get` in DISCOVERY but not on `messages.list`
or `history.list` [discovery].

### B.2 What the provider says about the classes

- G-SCOPES lists three tables. Non-sensitive: `gmail.addons.current.action.compose`,
  `gmail.addons.current.message.action`, `gmail.labels`. Sensitive:
  `gmail.addons.current.message.metadata`, `gmail.addons.current.message.readonly`, `gmail.send`.
  Restricted: `https://mail.google.com/`, `gmail.readonly`, `gmail.compose`, `gmail.insert`,
  `gmail.modify`, `gmail.metadata`, `gmail.settings.basic`, `gmail.settings.sharing`.
  [doc: G-SCOPES]
- RESTRICTED-LIST's Gmail list matches: `https://mail.google.com/` (noted there as covering any
  use of IMAP, SMTP and POP3), `gmail.readonly`, `gmail.metadata`, `gmail.modify`, `gmail.insert`,
  `gmail.compose`, `gmail.settings.basic`, `gmail.settings.sharing`. [doc: RESTRICTED-LIST]
- WS-POLICY's definition: Restricted scopes include "Any Gmail API scope that permits an
  application to:" "Read, create, or modify message bodies (including attachments), metadata, or
  headers; or" "Control mailbox access, email forwarding, or admin settings." [doc: WS-POLICY]
- Definitions on G-SCOPES: Non-sensitive scopes "only require basic OAuth App Verification";
  Sensitive "require additional OAuth App Verification"; Restricted "provide wide access to Google
  user data and require restricted scope OAuth App Verification". "If you store restricted scope
  data on servers (or transmit), then you must go through a security assessment." [doc: G-SCOPES]
- **CONFLICT: which scopes exist.** DISCOVERY revision 20261106 has 26 scopes, including
  `gmail.messages`, `gmail.messages.readonly`, `gmail.messages.modify`, `gmail.messages.delete`,
  `gmail.labels.readonly`, `gmail.labels.create`, `gmail.drafts`, `gmail.drafts.readonly`,
  `gmail.drafts.create`, `gmail.send.self`, `gmail.settings.filters`,
  `gmail.settings.filters.readonly` [discovery]. The method pages (last updated 2026-04-15) list
  for `messages.list` only `https://mail.google.com/`, `gmail.modify`, `gmail.readonly`,
  `gmail.metadata` [doc: MSG-LIST], and G-SCOPES, OAUTH-SCOPES and RELNOTES mention none of the
  new ones. QUESTION 3.
- Per DISCOVERY, `getProfile` accepts `https://mail.google.com/`, `gmail.compose`,
  `gmail.metadata`, `gmail.modify`, `gmail.readonly` only; **not** `gmail.messages.readonly`
  [discovery; PROFILE lists the same five]. A connector on `gmail.messages.readonly` cannot call
  `getProfile` (section G.4).
- Bodies: of the scopes the documentation classes, none narrower than `gmail.readonly` reads a
  body. `gmail.metadata` cannot (B.1); `gmail.labels` opens only label methods. The undocumented
  `gmail.messages.readonly` would, per DISCOVERY.
- **What `gmail.metadata` can do** [doc: RELNOTES, November 01, 2016]: "This scope gives developers
  access to a user's mailbox including history records, labels, and email headers, but not email
  body's or attachments." With it: `labels.list/get`, `messages.list` with `labelIds` (not `q`),
  `messages.get` with `format=minimal` or `metadata`, `threads.list/get` (not `q`; `full` refused),
  `history.list`, `getProfile` [doc: method pages, FORMAT, THR-GET]. Whether `snippet` is returned
  under `gmail.metadata` is **[not stated]**. The error returned when `q` or `format=full` is used
  with it is **[not stated]**.

### B.3 Verification and the security assessment, and when they do not apply

Not interpreted. Passages from developers.google.com pages (RESTRICTED-VERIF, SENSITIVE-VERIF,
WS-POLICY, UD-POLICY) are quoted; those from the Help Center (SEC-ASSESS, VERIF-FAQ, VERIF-REQ,
NOT-NEEDED) are paraphrased in our words and marked so:

- "If your app requests any restricted scopes and doesn't qualify for an exception, you need to
  satisfy the Additional requirements for specific API scopes". "If you store or transmit
  restricted scope data on servers, then you need to complete a security assessment."
  [doc: RESTRICTED-VERIF]
- "Every app that requests access to Google users' restricted data and has the ability to access
  data from or through a third-party server must go through a security assessment from
  Google-empanelled security assessors." [doc: RESTRICTED-VERIF] SEC-ASSESS (paraphrased): an app
  that asks for restricted scopes has to pass a security assessment every year, using CASA.
- Exceptions [doc: RESTRICTED-VERIF and SENSITIVE-VERIF, identical text]:
  - "Internal use only": "This means the app is used only by people in your Google Workspace or
    Cloud Identity organization. The project must be owned by the organization, and its OAuth
    consent screen needs to be configured for an Internal user type."
  - "Service-owned data only": "If your app uses a service account to access only its own data,
    and it doesn't access any user data (linked to a Google Account), then you don't need to
    submit for verification."
  - "Domain-wide installation": "If you plan for your app to only target users of a Google
    Workspace or Cloud Identity organization and always use domain-wide installation, then your app
    won't require brand verification. However, if your app utilizes restricted or sensitive scopes,
    app verification is required."
  - "Personal use" and "Projects used in Development, Testing, or Staging tiers" (user cap, tester
    warning, limited refresh-token lifetime).
- **CONFLICT on the same page.** RESTRICTED-VERIF's summary lists "domain-wide installation" among
  "Exceptions to verification requirements"; its body says that with restricted scopes "app
  verification is required". [doc: RESTRICTED-VERIF] The page explains it only as "a domain-wide
  installation lets a domain administrator grant third-party and internal applications access to
  your users' data"; whether that covers a service account with domain-wide delegation is
  **[not stated]**. VERIF-FAQ and VERIF-REQ do not mention domain-wide delegation or domain-wide
  installation at all; VERIF-FAQ's only service-account answer is about Cloud IAM data.
- VERIF-FAQ on internal-only (paraphrased): an app meant only for one organization or Google
  Workspace domain can be set as internal-only in the OAuth consent screen's configuration (User
  type "Internal"); when that choice is missing, the project may not belong to an organization.
  Expected times: 10 business days for sensitive scope verification, 6 weeks for restricted scope
  verification [doc: VERIF-FAQ].
- NOT-NEEDED adds (paraphrased) apps that a Google Workspace administrator has trusted or that were
  installed from the Marketplace: an administrator can put a third-party app on the trusted list in
  the Google Admin console. For internal apps it says that an app set as internal-only meets
  neither the unverified app screen nor the 100-user cap. [doc: NOT-NEEDED]
- WS-POLICY: "If an app is only used by users within your own organization, then these additional
  requirements do not apply." [doc: WS-POLICY] UD-POLICY: "Note: If your app is only used by users
  within your own domain, then these requirements do not apply." [doc: UD-POLICY]
- A service account with domain-wide delegation is not named in any exception. Whether a server
  that reads one organisation's mail with that organisation's own service account needs
  verification or a CASA assessment is **[not stated]** as such. QUESTION 4.

## C. Labels: `users.labels.list`, `users.labels.get`

`GET https://gmail.googleapis.com/gmail/v1/users/{userId}/labels` — "Lists all labels in the
user's mailbox." No query parameters, no paging. [doc: LABELS-LIST]

```
{ "labels": [ { object (Label) } ] }
```

"List of labels. Note that each label resource only contains an `id`, `name`,
`messageListVisibility`, `labelListVisibility`, and `type`. The `labels.get` method can fetch
additional label details." [doc: LABELS-LIST]

`GET https://gmail.googleapis.com/gmail/v1/users/{userId}/labels/{id}` — `id` "The ID of the label
to retrieve."; returns a Label. [doc: LABELS-GET]

**Label** [doc: LABELS; "The maximum number of labels supported for a user's mailbox is 10,000."]:

| Field | Type | Description (quoted) |
|---|---|---|
| `id` | string | "The immutable ID of the label." |
| `name` | string | "The display name of the label." |
| `messageListVisibility` | enum `show` / `hide` | "The visibility of messages with this label in the message list in the Gmail web interface." |
| `labelListVisibility` | enum `labelShow` / `labelShowIfUnread` / `labelHide` | "The visibility of the label in the label list in the Gmail web interface." |
| `type` | enum `system` / `user` | `system`: "Labels created by Gmail."; `user`: "Custom labels created by the user or application." |
| `messagesTotal`, `messagesUnread`, `threadsTotal`, `threadsUnread` | integer (int32 [discovery]) | counts |
| `color` | object `{textColor, backgroundColor}` | "Color is only available for labels that have their `type` set to `user`." |

- **Name to id:** no method looks a label up by name **[not stated]**; the connector lists all
  labels and matches `name`. Whether names are unique, and whether matching is case-sensitive, is
  **[not stated]**. System label names are reserved: "no `USER` label can be created with the same
  name as any `SYSTEM` label." [doc: G-LABELS]
- **System label ids**: G-LABELS's table gives names `INBOX`, `SPAM`, `TRASH`, `UNREAD`, `STARRED`,
  `IMPORTANT`, `SENT`, `DRAFT`, `CATEGORY_PERSONAL`, `CATEGORY_SOCIAL`, `CATEGORY_PROMOTIONS`,
  `CATEGORY_UPDATES`, `CATEGORY_FORUMS`; "The preceding list isn't exhaustive and other reserved
  label names exist." That a system label's `id` equals its name is shown only by usage
  (`labelIds=["INBOX"]`) [example: G-LISTMSG, G-PUSH]; it is **[not stated]**.
- **User label id format** is **[not stated]**; a fake should not assume one.
- **Nested labels:** the Gmail UI offers "Nest label under" [doc: HELP-LABELS]. How a nested
  label's `name` is spelled in the API (whether it includes the parent's name, and with what
  separator), and whether `label:` in `q`
  takes that form, are **[not stated]**. QUESTION 14.
  **[cairn]** To confirm against a real mailbox: that `labels.list` names a nested label with its
  parents in front, `parent/child`. The connector does not depend on the spelling: it matches the
  `label` setting against whatever `name` Gmail gives (exactly, else ignoring case when one label
  matches), and its guide says Google's pages do not state it.
- **[cairn]** The `label` setting refuses Gmail's own names whatever their case (`INBOX`, `SENT`,
  `DRAFT`, `SPAM`, `TRASH`, `UNREAD`, `STARRED`, `IMPORTANT`, `CHAT`, anything beginning
  `CATEGORY_`), and a pull refuses a name that resolves to a label whose `type` is not `user`,
  since other reserved names exist.
- **CONFLICT on the label limit.** "The maximum number of labels supported for a user's mailbox is
  10,000." [doc: LABELS] vs a limit of 5,000 labels a person can create (paraphrased)
  [doc: HELP-LABELS]
- Renaming: `labels.patch`/`update` exist [doc: REST]; `id` is "immutable" [doc: LABELS], so a
  stored id survives a rename. Deleting a label: "Immediately and permanently deletes the specified
  label and removes it from any messages and threads that it's applied to." [doc: REST] Whether
  removal by label deletion produces `labelsRemoved` history records is **[not stated]**.

**Label semantics on messages and threads** [doc: G-LABELS]:

- "Labels only exist on messages. If you retrieve a list of labels on a thread, you get a list of
  labels that exist on any message within the thread."
- "When you add or remove a label on a thread, you add or remove the specified label on all
  existing messages in the thread."
- "If messages are added to a thread after you add a label, the new messages don't inherit the
  existing label associated with the thread."
- "When you add a label to a message, the label is added to that message and becomes associated
  with the thread to which the message belongs. The label isn't added to other messages within the
  thread."
- HELP-SEARCH (paraphrased): a label goes on a message, never on a whole conversation.
- HELP-LABELS on the UI (paraphrased): with conversation view on, a label goes on the messages
  there are at that moment and not on replies that come later; with it off, a label shows only on
  the messages it was put on.
- Labels can also be applied by a Gmail filter at delivery: filters "automatically trigger actions
  such as adding or removing labels" [doc: G-OVERVIEW]. How that appears in history (section G)
  is **[not stated]**.

## D. `users.messages.list`

`GET https://gmail.googleapis.com/gmail/v1/users/{userId}/messages`; empty body. "Lists the
messages in the user's mailbox." [doc: MSG-LIST]

| Parameter | What the page says |
|---|---|
| `maxResults` | integer (uint32). "This field defaults to 100. The maximum allowed value for this field is 500." |
| `pageToken` | string. "Page token to retrieve a specific page of results in the list." |
| `q` | string. "Only return messages matching the specified query. Supports the same query format as the Gmail search box." "Parameter cannot be used when accessing the api using the gmail.metadata scope." |
| `labelIds[]` | string, repeated [discovery]. "Only return messages with labels that match all of the specified label IDs. Messages in a thread might have labels that other messages in the same thread don't have." |
| `includeSpamTrash` | boolean, default `false` [discovery]. "Include messages from `SPAM` and `TRASH` in the results." |

**Response** [doc: MSG-LIST]:

```
{
  "messages": [ { object (Message) } ],
  "nextPageToken": string,
  "resultSizeEstimate": integer
}
```

- `messages[]`: "Note that each message resource contains only an `id` and a `threadId`.
  Additional message details can be fetched using the messages.get method."
- `nextPageToken`: "Token to retrieve the next page of results in the list." That it is absent on
  the last page is **[not stated]** for messages (HIST-LIST says it for history).
- `resultSizeEstimate`: "Estimated total number of results." (uint32).
- Whether an empty result omits `messages` or returns `[]` is **[not stated]**; the guide's
  sample code does `results.get("messages", [])` [example: G-LISTMSG].
- **Order. CONFLICT by omission.** MSG-LIST states no order. G-LISTMSG: "The returned `messages`
  objects are listed in reverse chronological order (newest first)." G-SYNC: store "the
  `historyId` of the most recent message (the first message in the `list` response)". Whether
  "chronological" means `internalDate`, and whether the order is stable across pages while the
  mailbox changes, are **[not stated]**. MSG-LIST has no order parameter.
- Whether page tokens expire is **[not stated]**.
- **Duplicates:** whether a message can appear on two pages if labels change during paging is
  **[not stated]**.

**`labelIds` versus `q` with `label:`**: both select by label. `labelIds` takes ids and works under
`gmail.metadata`; `q` takes Gmail search syntax and does not. G-FILTER: "you can also filter
messages and threads by label with the `labelIds[]` parameter." Whether `labelIds` and `q` can be
combined is **[not stated]** (nothing forbids it).
**[cairn]** To confirm against a real mailbox: that `q` narrows a listing made with `labelIds`, and
that `after:` goes by `internalDate`. The connector sends both but depends on neither: it draws
the switch-on and look-back lines itself from each message's `internalDate`, and keeps the digest
of a message it passed over while the listing still returns it, so a listing that ignores `q`
gives the same notes over more pulls and never fetches a message twice.

**Search operators that matter** [doc: HELP-SEARCH, G-FILTER]:

| Operator | What the page says |
|---|---|
| `label:` | Finds mail that has one of your labels (paraphrased). Examples `label:friends`, `label:important`. How a label name with spaces or a nested name is written is **[not stated]**. |
| `after:` / `before:` (also `older:` / `newer:`) | Finds mail received within a period (paraphrased). Examples `after:2004/04/16`, `after:04/16/2004`, `before:2004/04/18`. |
| `after:` with seconds | "Caution: All dates used in the search query are interpreted as midnight on that date in the PST timezone. To specify accurate dates for other timezones pass the value in seconds instead:" example `?q=in:sent after:1388552400 before:1391230800` [doc: G-FILTER]. The unit is seconds since the epoch by the example (1388552400 is 2014-01-01 05:00 UTC); "epoch" is not written **[not stated]**. |
| `older_than:` / `newer_than:` | Finds mail older or newer than a period given in `d` (day), `m` (month) or `y` (year) (paraphrased). Examples `older_than:1y`, `newer_than:2d`. Hours are not listed **[not stated]**. |
| `in:anywhere` | Finds mail anywhere in Gmail, Spam and Trash included (paraphrased). |
| `has:userlabels` / `has:nouserlabels` | Finds mail with, or without, a label (paraphrased). |
| `rfc822msgid` | Finds mail by the value of its message-id header (paraphrased). |

- `after:`/`before:` are about when mail was received; whether that is `internalDate` is
  **[not stated]**. Whether `after:` and `before:` are inclusive or exclusive is **[not stated]**.
  "PST" is written as such; whether it follows daylight saving (PDT) is **[not stated]**.
- Neither `after:` nor any other operator selects by when a label was added **[not stated]**.
- Differences from the UI [doc: G-FILTER]: the UI does alias expansion and the API does not; "The
  Gmail UI allows users to perform thread-wide searches, but the API doesn't."
- HELP-SEARCH on negation (paraphrased): a search with a negative operator may still show
  conversations that hold what was excluded (said of the UI).
- What a malformed `q` returns is **[not stated]**. G-ERRORS's `badRequest` sample is about
  `orderBy`, a parameter Gmail does not have [example: G-ERRORS].

## E. `users.messages.get` and the Message

`GET https://gmail.googleapis.com/gmail/v1/users/{userId}/messages/{id}`; `id` "The ID of the message
to retrieve. This ID is usually retrieved using `messages.list`." Returns a Message. [doc: MSG-GET]

| Parameter | What the page says |
|---|---|
| `format` | enum, default `full` [discovery]. "The format to return the message in." |
| `metadataHeaders[]` | string, repeated [discovery]. "When given and format is `METADATA`, only include headers specified." |

The enum values are lowercase (`minimal`, `full`, `raw`, `metadata`) [doc: FORMAT; discovery], but
the descriptions write `METADATA` and `format=RAW` [doc: MSG-GET, MSG]. Whether the server accepts
either case is **[not stated]**; send lowercase.

`format` values [doc: FORMAT]:

| Value | Meaning (quoted) |
|---|---|
| `minimal` | "Returns only email message ID and labels; does not return the email headers, body, or payload." |
| `full` | "Returns the full email message data with body content parsed in the `payload` field; the `raw` field is not used. Format cannot be used when accessing the api using the gmail.metadata scope." |
| `raw` | "Returns the full email message data with body content in the `raw` field as a base64url encoded string; the `payload` field is not used. Format cannot be used when accessing the api using the gmail.metadata scope." |
| `metadata` | "Returns only email message ID, labels, and email headers." |

- Which other fields (`snippet`, `historyId`, `internalDate`, `sizeEstimate`, `threadId`) come back
  with `minimal` or `metadata` is **[not stated]**.
- Whether `metadataHeaders` matches header names case-insensitively, and how it is repeated on the
  URL (`metadataHeaders=From&metadataHeaders=To`), is **[not stated]**; DISCOVERY marks it
  `repeated` [discovery].
- QUOTA prices `messages.get` at "20 (60 when `format` is `full`, `raw`, or `html`)"; `html` is not
  a `format` value in FORMAT or DISCOVERY **CONFLICT** [doc: QUOTA, FORMAT].

**Message** [doc: MSG; "An email message."]:

| Field | Type | Description (quoted) |
|---|---|---|
| `id` | string | "The immutable ID of the message." |
| `threadId` | string | "The ID of the thread the message belongs to." |
| `labelIds[]` | string | "List of IDs of labels applied to this message." |
| `snippet` | string | "A short part of the message text." |
| `historyId` | string (uint64 [discovery]) | "The ID of the last history record that modified this message." |
| `internalDate` | string (int64 format) | "The internal message creation timestamp (epoch ms), which determines ordering in the inbox. For normal SMTP-received email, this represents the time the message was originally accepted by Google, which is more reliable than the `Date` header. However, for API-migrated mail, it can be configured by client to be based on the `Date` header." |
| `payload` | object MessagePart | "The parsed email structure in the message parts." |
| `sizeEstimate` | integer (int32 [discovery]) | "Estimated size in bytes of the message." |
| `raw` | string (bytes format) | "The entire email message in an RFC 2822 formatted and base64url encoded string. Returned in `messages.get` and `drafts.get` responses when the `format=RAW` parameter is supplied." |
| `classificationLabelValues[]` | object | Workspace classification labels; "Classification Labels are different from Gmail inbox labels." |

- `historyId` and `internalDate` are JSON strings holding integers [doc: MSG; discovery: `uint64`,
  `int64`].
- `raw` is "A base64-encoded string." on MSG while its description says base64url. Whether padding
  `=` is present is **[not stated]**.

**MessagePart** [doc: MSG; "A single MIME message part."]:

| Field | Type | Description (quoted) |
|---|---|---|
| `partId` | string | "The immutable ID of the message part." |
| `mimeType` | string | "The MIME type of the message part." |
| `filename` | string | "The filename of the attachment. Only present if this message part represents an attachment." |
| `headers[]` | `{name, value}` | "List of headers on this message part. For the top-level message part, representing the entire message payload, it will contain the standard RFC 2822 email headers such as `To`, `From`, and `Subject`." |
| `body` | MessagePartBody | "The message part body for this part, which may be empty for container MIME message parts." |
| `parts[]` | MessagePart | "The child MIME message parts of this part. This only applies to container MIME message parts, for example `multipart/*`. For non- container MIME message part types, such as `text/plain`, this field is empty." |

Header: `name` "The name of the header before the `:` separator. For example, `To`."; `value` "The
value of the header after the `:` separator. For example, `someuser@example.com`." [doc: MSG]

**MessagePartBody** [doc: ATT]:

| Field | Type | Description (quoted) |
|---|---|---|
| `attachmentId` | string | "When present, contains the ID of an external attachment that can be retrieved in a separate `messages.attachments.get` request. When not present, the entire content of the message part body is contained in the data field." |
| `size` | integer | "Number of bytes for the message part data (encoding notwithstanding)." |
| `data` | string (bytes format) | "The body data of a MIME message part as a base64url encoded string. May be empty for MIME container types that have no message body or when the body data is sent as a separate attachment." |

`messages.attachments.get`: `GET https://gmail.googleapis.com/gmail/v1/users/{userId}/messages/{messageId}/attachments/{id}`,
returns a MessagePartBody; scopes `https://mail.google.com/`, `gmail.modify`, `gmail.readonly`
(not `gmail.metadata`) [doc: ATT-GET]; DISCOVERY adds `gmail.messages.readonly`. 20 quota units
[doc: QUOTA].

**What the documentation does not say about bodies** (all **[not stated]**):

- How a `multipart/alternative` message with `text/plain` and `text/html` is laid out: whether the
  top-level `payload.mimeType` is `multipart/alternative` with two `parts`, how nesting inside
  `multipart/mixed` looks, and what `partId` values look like.
- Whether a large text body can be moved out to `attachmentId` (the field description allows "when
  the body data is sent as a separate attachment"), and at what size.
- The character set of decoded `data`: whether Gmail converts to UTF-8 or leaves the part's own
  charset (the `Content-Type` header of the part would then be needed).
- Whether `Content-Transfer-Encoding` (quoted-printable, base64) is already undone in `data`.
- Whether header values in `payload.headers` are RFC 2047-decoded, whether repeated headers
  appear twice, and the case of header names.
- For the note: `Subject`, `Date`, `From`, `To`, `Cc` are standard headers ("such as `To`, `From`,
  and `Subject`" [doc: MSG]); `Bcc` presence on received mail and the format of `Date` are
  **[not stated]**. The parsing of display names from `From`/`To` is left to the client.

The only worked example of reading headers is the thread sample, which loops over
`payload["headers"]` for `name == "Subject"` [example: G-THREADS].

## F. Threads: `users.threads.list`, `users.threads.get`

`GET https://gmail.googleapis.com/gmail/v1/users/{userId}/threads` — the same parameters as
`messages.list`: `maxResults` ("This field defaults to 100. The maximum allowed value for this
field is 500."), `pageToken`, `q` (not under `gmail.metadata`), `labelIds[]` ("Only return threads
with labels that match all of the specified label IDs."), `includeSpamTrash`. [doc: THR-LIST]

```
{ "threads": [ { object (Thread) } ], "nextPageToken": string, "resultSizeEstimate": integer }
```

"Note that each thread resource does not contain a list of `messages`." [doc: THR-LIST] Which
fields a listed thread does carry (`id`, `snippet`, `historyId`) is **[not stated]**.

- "If any message in a thread matches the query, the thread is returned in the result."
  [doc: G-THREADS]
- Order of `threads.list` is **[not stated]**.

`GET https://gmail.googleapis.com/gmail/v1/users/{userId}/threads/{id}`; `format` `full` (default),
`metadata`, `minimal` (no `raw`: "The raw format isn't allowed for the `threads.get` to avoid
unexpectedly large amounts of data being returned." [doc: RELNOTES]); `metadataHeaders[]`. Returns
a Thread. [doc: THR-GET]

**Thread** [doc: THR; "A collection of messages representing a conversation."]: `id` ("The unique
ID of the thread."), `snippet`, `historyId` ("The ID of the last history record that modified this
thread."), `messages[]` ("The list of messages in the thread."). The order of `messages[]` is
**[not stated]** on THR; G-THREADS says threads let you retrieve "all messages in a conversation,
in order".

Cost: `threads.get` 40 units, 120 with `format=full`; `threads.list` 10 [doc: QUOTA]. A thread
returned because one message carries the label still holds unlabelled messages; each message's
`labelIds` tells them apart; this follows from G-LABELS and MSG but is **[not stated]** as such.

## G. Incremental reading: `users.history.list`, `getProfile`, full sync

### G.1 `history.list`

`GET https://gmail.googleapis.com/gmail/v1/users/{userId}/history`. "Lists the history of all
changes to the given mailbox. History results are returned in chronological order (increasing
`historyId`)." [doc: HIST-LIST]

| Parameter | What the page says |
|---|---|
| `startHistoryId` | string (uint64 [discovery]). "Required. Returns history records after the specified `startHistoryId`. The supplied `startHistoryId` should be obtained from the `historyId` of a message, thread, or previous `list` response. History IDs increase chronologically but are not contiguous with random gaps in between valid IDs. Supplying an invalid or out of date `startHistoryId` typically returns an `HTTP 404` error code. A `historyId` is typically valid for at least a week, but in some rare circumstances may be valid for only a few hours. If you receive an `HTTP 404` error response, your application should perform a full sync. If you receive no `nextPageToken` in the response, there are no updates to retrieve and you can store the returned `historyId` for a future request." |
| `labelId` | string. "Only return messages with a label matching the ID." |
| `historyTypes[]` | enum, repeated [discovery]: `messageAdded`, `messageDeleted`, `labelAdded`, `labelRemoved`. "History types to be returned by the function" |
| `maxResults` | integer (uint32). "This field defaults to 100. The maximum allowed value for this field is 500." |
| `pageToken` | string. "Page token to retrieve a specific page of results in the list." |

- "after the specified `startHistoryId`": exclusive by the wording; that a record whose `id`
  equals `startHistoryId` is excluded is not otherwise stated.
- `maxResults` counts "history records", not messages [doc: HIST-LIST].
- `labelId` semantics: "Only return messages with a label matching the ID." Whether it filters
  records to those touching a message that **now** has the label, that **had** it at the time, or
  whose `labelsAdded.labelIds` contains it, is **[not stated]**. Whether it can be combined with
  `historyTypes=labelAdded` to get exactly the records where that label was added is **[not stated]**. QUESTION 7.
- The wording "If you receive no `nextPageToken` in the response, there are no updates to retrieve"
  reads as describing the last page; the doc does not say whether the response `historyId` may be
  stored after reading all pages, only "you can store the returned `historyId`".

**Response** [doc: HIST-LIST]:

```
{
  "history": [ { object (History) } ],
  "nextPageToken": string,
  "historyId": string
}
```

- `history[]`: "List of history records. Any `messages` contained in the response will typically
  only have `id` and `threadId` fields populated."
- `historyId`: "The ID of the mailbox's current history record."
- Whether `history` is absent or `[]` when nothing changed is **[not stated]**.

**History** [doc: HIST-LIST; "A record of a change to the user's mailbox. Each history change may
affect multiple messages in multiple ways."]:

| Field | Type | Description (quoted) |
|---|---|---|
| `id` | string (uint64 [discovery]) | "The mailbox sequence ID." |
| `messages[]` | Message | "List of messages changed in this history record. The fields for specific change types, such as `messagesAdded` may duplicate messages in this field. We recommend using the specific change-type fields instead of this." |
| `messagesAdded[]` | `{message}` | "Messages added to the mailbox in this history record." |
| `messagesDeleted[]` | `{message}` | "Messages deleted (not Trashed) from the mailbox in this history record." |
| `labelsAdded[]` | `{message, labelIds[]}` | "Labels added to messages in this history record."; `labelIds`: "Label IDs added to the message." |
| `labelsRemoved[]` | `{message, labelIds[]}` | "Labels removed from messages in this history record."; `labelIds`: "Label IDs removed from the message." |

- **CONFLICT on what the embedded message holds.** HIST-LIST: messages "will typically only have
  `id` and `threadId` fields populated." RELNOTES (June 22, 2015): "the `history.list` method
  returns all current `Message.labelIds` as part of the response in `messagesAdded`,
  `messagesDeleted`, `labelsAdded`, and `labelsRemoved`." Whether `message.labelIds` is present,
  and whether it is the state now or at the time of the record, is unsettled.
- History records carry no timestamp of their own (only `id`) [doc: HIST-LIST]. So the time a
  label was added is not given even in history **[not stated]**.
- How a message that arrives already labelled (by a Gmail filter) appears: one `messagesAdded`
  record whose message carries the label, or also a `labelsAdded` record, is **[not stated]**.
  QUESTION 8.
- Whether `labelsAdded` appears when a person labels a whole thread (one record per message, or
  one record with several messages) is **[not stated]**.

### G.2 How long history lasts and the error

- HIST-LIST: "A `historyId` is typically valid for at least a week, but in some rare
  circumstances may be valid for only a few hours."
- G-SYNC: "History records are typically available for at least one week and often longer.
  However, the time period for which records are available might be significantly shorter, and
  records might be unavailable in rare cases."
- G-SYNC: "If the `startHistoryId` supplied by your client is outside the available range of
  history records, the Gmail API returns an `HTTP 404` error response. In this case, your client
  must perform a full sync."
- **The 404 body is not shown anywhere** (no `reason`, no `message`) **[not stated]**. HIST-LIST
  says "typically returns an `HTTP 404`"; what else it may return is **[not stated]**. A 404 for a
  bad user or a stale id cannot be told apart from the documentation. QUESTION 9.
- Whether a `startHistoryId` that is in the future, or not a number, gives 404 or 400 is
  **[not stated]** ("invalid or out of date" share the 404 sentence).

### G.3 The full-sync fallback the guide describes [doc: G-SYNC]

1. "Call the `messages.list` method to retrieve the first page of message IDs."
2. "Create a batch request of `messages.get` method requests for each of the messages returned by
   the list request." Use `format=full` or `format=raw` the first time, and `format=minimal` for
   cached messages "as only the `labelIds` might change".
3. "Merge the updates into your cached results. Your app should store the `historyId` of the most
   recent message (the first message in the `list` response) for future partial
   synchronizations."

- **CONFLICT on where the first `historyId` comes from.** G-SYNC says to take it from "the first
  message in the `list` response", but a listed message "contains only an `id` and a `threadId`"
  [doc: MSG-LIST]; it needs a `messages.get` first. G-SYNC also says: "The `startHistoryId` query
  parameter must be set to the `historyId` of a recent message. To retrieve the `historyId` of a
  recent message, use the `messages.get` or `messages.list` methods."
- G-SYNC: "In a full sync operation, your app should retrieve and store as many of the most
  recent messages or threads as are necessary for your purpose." It does not bound a full sync.
- A message's `historyId` is "the last history record that modified this message" [doc: MSG], not
  the mailbox's current one; starting from it may replay records older than the sync. That is
  harmless if records are applied idempotently; the docs do not discuss it **[not stated]**.

### G.4 `users.getProfile`

`GET https://gmail.googleapis.com/gmail/v1/users/{userId}/profile` — "Gets the current user's Gmail
profile." [doc: PROFILE]

```
{ "emailAddress": string, "messagesTotal": integer, "threadsTotal": integer, "historyId": string }
```

`historyId`: "The ID of the mailbox's current history record." `emailAddress`: "The user's email
address." [doc: PROFILE] 1 quota unit [doc: QUOTA].

- Whether `getProfile.historyId` is a valid `startHistoryId` is **[not stated]**; HIST-LIST names
  only "a message, thread, or previous `list` response". G-PUSH says the `watch` response
  "contains the current mailbox `historyId`" [doc: G-PUSH], and its worked example passes the
  `watch` response's id as `startHistoryId` [example: G-PUSH]. That a mailbox-current id is
  accepted is shown only by that example.
- Scopes: not `gmail.messages.readonly` [discovery] (B.2).

### G.5 Push (not needed for polling; recorded for completeness)

- `watch` takes `labelIds` and `labelFilterBehavior` (`include`/`exclude`) [example: G-PUSH;
  discovery]; the notification is `{"emailAddress": ..., "historyId": ...}` and the client then
  calls `history.list` [doc: G-PUSH]. "You must call the `watch` method at least once every 7 days".
  It needs a Cloud Pub/Sub topic and grants `publish` to `gmail-api-push@system.gserviceaccount.com`
  [doc: G-PUSH]. "in rare situations, notifications might be delayed or dropped" [doc: G-PUSH].
  `watch` costs 100 units [doc: QUOTA].

## H. Time

- `internalDate`: string holding epoch **milliseconds** [doc: MSG; discovery `int64`]. For SMTP
  mail, "the time the message was originally accepted by Google"; for API-migrated mail it "can be
  configured by client to be based on the `Date` header" [doc: MSG]. It "determines ordering in
  the inbox" [doc: MSG].
- `Date` header: the sender's own claim, a string in `payload.headers` [doc: MSG names headers;
  format **[not stated]**].
- `q` `after:`/`before:`: a date is "midnight on that date in the PST timezone"; a number is
  seconds [doc: G-FILTER] (D).
- `history.list`: no timestamps at all; `historyId` is an ordering only: "History IDs increase
  chronologically but are not contiguous with random gaps in between valid IDs." [doc: HIST-LIST]
- No field gives when a label was applied, when a message was read, or when it was trashed
  **[not stated]** (headline 1).
- Pub/Sub's `publishTime` is RFC 3339 [example: G-PUSH]; not a Gmail field.

## I. Immutability, labelling, trashing, deleting

- **Content does not change.** "An email message containing the sender, recipients, subject, and
  body. After you create a message, you can't change it." [doc: G-OVERVIEW] "only the `labelIds`
  might change" [doc: G-SYNC]. `id` is "The immutable ID of the message." [doc: MSG]
- **Labelling and unlabelling** change `labelIds` and produce `labelsAdded` / `labelsRemoved`
  history records [doc: HIST-LIST]. A message whose label is removed simply stops matching
  `labelIds`/`label:`; this is **[not stated]** as such.
- **Trash** is a label: `TRASH` is a system label [doc: G-LABELS]; `messages.trash` "Moves the
  specified message to the trash." [doc: MSG]. `messages.list` excludes `SPAM` and `TRASH` unless
  `includeSpamTrash=true` (default `false`) [doc: MSG-LIST; discovery]. Trashing a thread moves
  "Any messages that belong to the thread" to the trash [discovery]. In history, trashing is not a
  `messagesDeleted` ("Messages deleted (not Trashed)") [doc: HIST-LIST]; that it shows as
  `labelsAdded` with `TRASH` is **[not stated]**. How long Trash keeps mail before deleting it is
  **[not stated]** in these pages.
- **Delete**: `messages.delete` "Immediately and permanently deletes the specified message."
  [doc: MSG]; DISCOVERY adds "This operation cannot be undone." It appears in history as
  `messagesDeleted` [doc: HIST-LIST]. `messages.get` on a deleted id: status and body
  **[not stated]** (presumably 404). HELP-LABELS (paraphrased): deleting a message takes it out of
  the inbox and off every label.
- **Spam**: `SPAM` is a system label and is excluded like `TRASH` [doc: G-LABELS, MSG-LIST].
- A connector that writes each message once never needs to re-read content, since content does
  not change; but a message can lose and regain the label, which history reports again as
  `labelsAdded`; this is **[not stated]**.

## J. Batch requests

[doc: G-BATCH unless marked]

- "You're limited to 100 calls in a single batch request." "Note: Larger batch sizes are likely to
  trigger rate limiting. We recommend sending batches of no more than 50 requests." G-ERRORS:
  "Don't send batches larger than 50 requests."
- Endpoint: "which can be sent to the `batchPath` specified in the API discovery document. The
  default path is `/batch/api_name/api_version`." DISCOVERY's `batchPath` is `batch`, i.e.
  `POST https://gmail.googleapis.com/batch` [discovery]. **CONFLICT** with the guide's default form
  (`/batch/gmail/v1`); which one Gmail serves is not settled by the pages.
- Format: `multipart/mixed`; each part has `Content-Type: application/http`, optional `Content-ID`,
  and a complete HTTP request; "The HTTP request must only contain the path portion of the URL;
  full URLs are not allowed in batch requests." The outer request's headers (e.g.
  `Authorization`) apply to every part unless a part overrides them.
- Response: `multipart/mixed`, one part per request "in the same order as the requests"; each part
  a full HTTP response with its own status; `Content-ID` echoed with the prefix `response-`.
  "The server might perform your calls in any order."
- **Quota:** "A set of n requests batched together counts toward your usage limit as n requests,
  not as one request." Batching saves connections, not quota.
- The examples use a fictional "Farm API" on `www.googleapis.com` [example: G-BATCH]; no Gmail
  batch example is given.

## K. Quotas and limits

[doc: QUOTA unless marked]

- "As of May 1, 2026, the usage limits for this API were updated. Google Cloud projects that made
  any use of this API between November 2025 and April 2026 will continue with their previously
  set usage quotas. Cloud projects created on or after May 1, 2026 are subject to the new API
  quotas." The earlier quotas are not given on the page **[not stated]**.
- RELNOTES, May 01, 2026: "Quota units for `drafts.get`, `messages.attachments.get`,
  `messages.get`, `messages.trash`, `threads.get`, and `threads.trash` have changed." The earlier
  values are not given. TOOLS-SAFETY, for existing projects: "API usage quotas already in place
  will remain unchanged for at least 60 days." (from May 1, 2026).

| Limit | Value |
|---|---|
| Per minute per project | 1,200,000 quota units |
| Per minute per user per project | 6,000 quota units |
| Daily billing threshold, per day per project | 80,000,000 quota units ("before charges apply"; "You can't request an increase on this daily threshold limit.") |

Methods this connector uses:

| Method | Quota units |
|---|---|
| `getProfile` | 1 |
| `labels.list`, `labels.get` | 1 each |
| `messages.list` | 5 |
| `messages.get` | 20 (60 when `format` is `full`, `raw`, or `html`) |
| `messages.attachments.get` | 20 |
| `threads.list` | 10 |
| `threads.get` | 40 (120 when `format` is `full`) |
| `history.list` | 2 |

- "API calls by a service account are considered to be using a single account." Whether calls by
  one service account impersonating several users count per impersonated user is
  **[not stated]**. QUESTION 10.
- Charges: "All standard use of the Gmail API is available at no additional cost. Exceeding the
  quota request limits is planned to incur charges to your Google Cloud billing account later in
  2026." "Google will share full billing details later in 2026 with at least 90 days' notice
  before any changes take effect." TOOLS-SAFETY: "Later in 2026, following 90 days of notice:"
  quota increases need billing and use over standard daily thresholds "will generate charges".
- Other limits: "there's also a limit of 500 recipients per email message" (sending only). Per-user
  bandwidth limits "equal to, but independent of, IMAP" and a "per-user concurrent request limit"
  [doc: G-ERRORS]; numbers **[not stated]**.
- **Backoff** [doc: QUOTA]: "wait 1 + `random_number_milliseconds` and retry", then 2, then 4;
  wait time `min(((2^n)+random_number_milliseconds), maximum_backoff)`, with
  `random_number_milliseconds` "less than or equal to 1,000" and `maximum_backoff` "typically 32
  or 64 seconds"; "At some point, clients should be prevented from retrying indefinitely."
  G-ERRORS: "Start retry periods at least one second after the error."
- `Retry-After`: never named **[not stated]**. G-ERRORS says the 429s for sending and bandwidth come
  "with a retry time", form **[not stated]**.
- Push: "maximum notification rate of one event per second" per watched user [doc: G-PUSH].

## L. Errors

G-ERRORS: "The Gmail API returns two levels of error information:" "HTTP error codes and messages in
the header." and "A JSON object in the response body". Status summary: 400, 401, 403, 404, 429,
"500, 502, 503, 504 - Server Errors". [doc: G-ERRORS]

The samples use the older `error.errors[]` shape (google-auth.md C.2). Several samples have
trailing commas and are not valid JSON as printed. Copied as shown [example: G-ERRORS]:

```
{
  "error": {
    "errors": [
      {
        "domain": "global",
        "reason": "authError",
        "message": "Invalid Credentials",
        "locationType": "header",
        "location": "Authorization",
      }
    ],
    "code": 401,
    "message": "Invalid Credentials"
  }
}
```

```
{
  "error": {
    "errors": [
      {
        "domain": "usageLimits",
        "reason": "dailyLimitExceeded",
        "message": "Daily Limit Exceeded"
      }
    ],
    "code": 403,
    "message": "Daily Limit Exceeded"
  }
}
```

```
{
  "error": {
    "errors": [
      {
        "domain": "global",
        "reason": "domainPolicy",
        "message": "The domain administrators have disabled Gmail apps."
      }
    ],
    "code": 403,
    "message": "The domain administrators have disabled Gmail apps."
  }
}
```

```
{
  "error": {
  "errors": [
    {
    "domain": "usageLimits",
    "message": "Rate Limit Exceeded",
    "reason": "rateLimitExceeded",
    }
  ],
  "code": 403,
  "message": "Rate Limit Exceeded"
  }
}
```

```
{
  "error": {
  "errors": [
    {
    "domain": "usageLimits",
    "reason": "userRateLimitExceeded",
    "message": "User Rate Limit Exceeded"
    }
  ],
  "code": 403,
  "message": "User Rate Limit Exceeded"
  }
}
```

```
{
  "error": {
  "errors": [
    {
    "domain": "global",
    "reason": "backendError",
    "message": "Backend Error",
    }
  ],
  "code": 500,
  "message": "Backend Error"
  }
}
```

What each means [doc: G-ERRORS]:

| Status / reason | What the page says |
|---|---|
| 400 `badRequest` | "A required field or parameter is missing." / "A supplied value or combination of fields is invalid." Sample is about `orderBy` [example]. |
| 401 `authError` | "the access token you're using is either expired or invalid. Missing authorization for the requested scopes can also cause this error." Fix: refresh the token. |
| 403 `dailyLimitExceeded` | "This error occurs when your project reaches its API limit." and "when the application owner sets a quota limit to cap usage of a particular resource." |
| 403 `domainPolicy` | "This error occurs when the policy for the user's domain doesn't allow your app to access Gmail." |
| 403 `rateLimitExceeded` | "This error indicates that the user has reached the maximum request rate for the Gmail API. This limit varies depending on the request type." Fix: quota increase, exponential backoff. |
| 403 `userRateLimitExceeded` | "This error occurs when a request reaches the per-user limit." Fix: fewer requests or exponential backoff. |
| 404 | "The requested resource couldn't be found." No sample, no `reason` **[not stated]**. |
| 429 | Too many requests: it "can occur due to daily per-user limits (including mail sending limits), bandwidth limits, or a per-user concurrent request limit." "You can't increase per-user limits." No JSON sample **[not stated]**. |
| 500 `backendError`, 502, 503, 504 | "use exponential backoff to retry the request." |

- The 429 messages are given inline only. Bandwidth: "Too many requests: User-rate limit exceeded"
  "with a retry time". Concurrency: "Too many requests: Too many concurrent requests for user".
  Sending: "Too many requests: User-rate limit exceeded (Mail sending)". [doc: G-ERRORS] The body
  shape and `reason` of a 429 are **[not stated]**.
- **CONFLICT on what `rateLimitExceeded` is.** G-ERRORS lists "The user exceeded the rate limit."
  and "The project exceeded the rate limit." as separate 403 cases, but describes
  `rateLimitExceeded` as "the user has reached the maximum request rate" and
  `userRateLimitExceeded` as "the per-user limit". Which reason the project-level limit returns is
  unclear.
- Not documented for Gmail **[not stated]**: a token lacking the needed scope (401 per the
  `authError` text, or 403?), `q` or `format=full` under `gmail.metadata`, a message or label id
  that does not exist, a user who does not exist or has no Gmail, a user whose mailbox the admin
  has blocked from API access besides `domainPolicy`. Delegation failures surface at the token
  endpoint (`unauthorized_client`, `access_denied`; google-auth.md A.6).

## M. On the customer's side

- **Enable the API** in the Cloud project: G-LISTMSG's prerequisite is "A Google Cloud project with
  the Gmail API enabled." [doc: G-LISTMSG]
- **Domain-wide delegation**: a super admin grants the service account's client ID the scopes
  (google-auth.md A.2). WS-AUTH: "Because of the power of this feature, only super admins can
  enable domain-wide delegation of authority." The scope string the admin enters must match what
  the token requests; for this connector `https://www.googleapis.com/auth/gmail.readonly` (or
  `gmail.metadata` / `gmail.messages.readonly` per QUESTION 3) and, if used,
  `https://www.googleapis.com/auth/gmail.labels`. No Gmail page says anything further about
  impersonating a user for mail **[not stated]**; G-SCOPES's only delegation note is about
  `gmail.settings.sharing`: "They are only available to Google Workspace customers using a service
  account with domain-wide delegation."
- Gmail "delegates" (G-OVERVIEW: "A delegator is a Gmail user who grants mailbox access to another
  user") are a mailbox-sharing feature, not domain-wide delegation; not used here.
- **What an admin can switch off** [doc: ADMIN-APPS]:
  - In Admin console > Security > Access and data control > API controls, a service such as Gmail
    can be Restricted (paraphrased): then only internal and third-party apps set to Trusted or to
    Specific Google data can reach its data, and apps installed before that are not trusted stop
    working and their tokens are revoked.
  - For Gmail, Google Drive, Docs and Chat, access to a fixed list of high-risk OAuth scopes can
    be restricted as well (paraphrased). Gmail's list: `https://mail.google.com/`, `gmail.compose`,
    `gmail.insert`, `gmail.metadata`, `gmail.modify`, `gmail.readonly`, `gmail.send`,
    `gmail.settings.basic`, `gmail.settings.sharing`. (`gmail.labels` and the discovery-only scopes
    are not on it.)
  - Apps can be Trusted, Limited, Specific Google data, or Blocked; a Limited app reaches only
    Google services that are not restricted (paraphrased).
  - Internal apps: a setting that lets apps the organization built itself reach restricted Google
    Workspace APIs (paraphrased) ("Trust internal apps").
  - Whether API controls apply to a service account using domain-wide delegation is
    **[not stated]** on ADMIN-APPS.
- The error the connector sees when the admin has blocked Gmail apps is `domainPolicy`
  (section L); whether blocking via API controls produces that error, a token-endpoint error, or a
  revoked refresh token is **[not stated]**.
- **The person's side:** nothing to set up beyond creating the label and applying it. A person who
  labels a conversation labels only its current messages (C).

## N. Policy (quoted, not interpreted)

From WS-POLICY ("Google Workspace user data and developer policy"):

- Scope of the page: it governs "Gmail, Google Chat, Google Drive, Google Sheets, and other Google
  Workspace APIs, MCPs, and other developer tools requesting access to user data."
- "Note: Certain Google OAuth API Scopes (the "Restricted Scopes") are subject to additional
  requirements as described in this guideline." ... "If an app is only used by users within your
  own organization, then these additional requirements do not apply."
- Approved Gmail use cases include: "Applications that automatically backup email" and
  "Applications that enhance the email experience for productivity purposes (such as applications
  for customer relationship management, delayed sending of email or mail merge, or providing
  generative AI summaries)". Not allowed include "Applications that export email on a one-time or
  manual basis."
- Limited use: "These requirements apply to data derived from both Sensitive and Restricted
  scopes." "Limit your use of data to providing or improving your appropriate use case or features
  that are visible and prominent in the requesting application's user interface."
- "Transfers of data are not allowed, except:" "To provide or improve your appropriate use case or
  user-facing features that are visible and prominent in the requesting application's user
  interface and only with the user's consent;" "For security purposes (for example, investigating
  abuse);" "To comply with applicable laws and/or regulations; or," and a merger with prior consent.
- "Do not allow humans to read user data, unless:" "You have obtained and documented the user's
  explicit consent or affirmative agreement to view specific messages, files, or other data", or
  the data is "aggregated and anonymized", or for security, or to comply with law.
- Prohibited: "Transferring, selling, or using user data to create, train, or improve a machine
  learning or artificial intelligence model beyond that specific user's personalized model for the
  appropriate use case or user-facing feature."
- "Note: Remember, our Terms of Service prohibits the scraping, building databases (including
  databases for model training purposes), or otherwise creating permanent copies of Google User
  data. This includes keeping cached copies longer than permitted by the cache header."
- Disclosure wording: "The use of information received from Google Workspace scopes will adhere to
  the Google User Data Policy, including the Limited Use requirements."
- Consent and notice: "Your disclosure must accompany and immediately precede a request for user
  consent. You must not begin collection prior to obtaining affirmative consent." "You must
  provide user help documentation that explains how users can manage and delete their data from
  your app or service." Also: "granularly require users to confirm the MCP, tool, skill, or other
  agentic behavior invocation."
- Security for restricted scopes: "Applications accessing Restricted Scopes must demonstrate that
  they adhere to certain security practices." Required: encrypting user data "At rest on your
  systems"; "Keeping user data and credentials, specifically tokens such as OAuth access and
  refresh tokens, encrypted at rest."; "Ensuring keys and key material are managed appropriately,
  such as stored in a hardware security module or equivalent-strength key management system.";
  "Protecting against prompt injection techniques by either using Google Cloud Platform's Model
  Armor or other prompt injection protection." "Required security measures for Restricted Scopes
  also include following the Cloud Application Security Assessment (CASA)."
- Restricted Gmail scopes defined as any that permits an app to "Read, create, or modify message
  bodies (including attachments), metadata, or headers" (B.2).
- Incidents: notify "security@google.com of any known or suspected unauthorized access".

From UD-POLICY ("Google API Services User Data Policy"):

- "Note: If your app is only used by users within your own domain, then these requirements do not
  apply." (said of "Additional Requirements for Specific API Scopes").
- Limited Use "requirements apply to the raw data obtained from the scopes and data aggregated,
  anonymized, or derived from them."
- "Don't allow humans to read the data, unless:" "You first obtained the user's affirmative
  agreement to view specific messages, files, or other data".
- UD-POLICY's Limited Use list has no machine-learning clause; WS-POLICY's does (above).
- "Depending on the API being accessed and number of user grants or users, applications must pass
  an annual security assessment and obtain a Letter of Assessment from a Google-designated third
  party."

From VERIF-FAQ ("Limited Use" section), paraphrased:

- The Workspace API User Data and Developer policy forbids using Google User Data to create, train
  or improve a machine learning or AI model, foundational models included; the data may neither
  train or improve a foundational model nor be stored together with one.
- For AI and machine learning, Google User Data may be used only for a personalized model that
  carries out a feature the user sees or the use case the user directed.
- Under the Workspace API Policy a personalized model is either one that runs only on the device
  or one built for that one end user or that one organization alone; it does not mix Google User
  Data across end users or organizations. A fine-tuned or domain-specific model can in some cases
  count as a foundation model.
- **CONFLICT on whose model.** WS-POLICY allows a model only up to "that specific user's
  personalized model"; VERIF-FAQ (paraphrased) counts as personalized a model built for that one
  end user or that one organization alone. Neither page says whether sending mail text to a third-party model for inference
  (no training) is a "transfer" or "use" covered by the clause **[not stated]**.
- VERIF-REQ (paraphrased): an application that transfers data for AI model training, within the
  approved use cases, must show in its demonstration video the step where the user explicitly
  consents to their data being used for training a personalized AI model.

From TOOLS-SAFETY: "working with Google Workspace APIs is still subject to the Google Workspace
API developer policy and the Google APIs Terms of Service. As such, Google may require
verification, such as in the form of an audit, of adherence to our user data policies."

From G-OVERVIEW, the use cases the API is suited to include "Read-only mail extraction, indexing,
and backup".

---

## Open questions for the connector

1. QUESTION: How does the connector know a message was labelled **after** the source was switched
   on? No field records when a label was applied (headline 1, H). Only `labelsAdded` history
   records show it, a history id "may be valid for only a few hours" in rare cases (G.2), and after
   a 404 a full listing by label cannot tell old labellings from new ones. Options: take everything
   labelled at switch-on time as a baseline and skip it; accept that a history gap may admit older
   labelled messages; or add `internalDate` > switch-on as a second rule (which drops old mail
   labelled later). The design must choose.
   **[cairn]** The third: history is not read. A message is written when it arrived
   (`internalDate`) at or after the switch-on and at or after the pull's clock less
   `lookback_days` (30 unless set, 1 to 365), and passed over for good otherwise. So a message
   that arrived before the switch-on is never written, whenever it was labelled, and a label put
   on more than `lookback_days` after a message arrived is not noticed. Each pull lists the
   label's messages with `after:` a day before the later of those two lines.
2. QUESTION: Where does the switch-on `startHistoryId` come from? HIST-LIST names only "a message,
   thread, or previous `list` response"; `getProfile.historyId` ("the mailbox's current history
   record") is not named, and `getProfile` is closed under `gmail.messages.readonly` (G.4). A
   message's own `historyId` is that message's last change, which may be older than switch-on or
   already outside the history window (G.3). Only G-PUSH's example uses a mailbox-current id.
   **[cairn]** Moot: no history id is used.
3. QUESTION: Which read scope? `gmail.readonly` is documented and restricted. DISCOVERY (rev
   20261106) offers `gmail.messages.readonly` and `gmail.labels.readonly`, absent from every
   human-written page; their class, whether an admin can grant them through domain-wide delegation
   today, and whether they are on ADMIN-APPS's high-risk list are not stated (B).
   **[cairn]** `gmail.readonly` only, the documented scope that reads bodies; it can read but
   not send, change or delete. The discovery-only scopes are not asked for.
4. QUESTION: Do verification and the CASA assessment apply when one Workspace organisation's own
   service account (with domain-wide delegation) or its own internal OAuth client reads its own
   users' mail on a server? The pages exempt "Internal use only" apps and apps "only used by users
   within your own organization", say "domain-wide installation" with restricted scopes needs
   verification, and never mention domain-wide delegation (B.3).
   **[cairn]** Not answered: the guide says only that Google classes the scope as Restricted and
   that a Workspace administrator can restrict it.
5. QUESTION: Should the unit be a message or a thread? Labels live on messages and a labelled
   thread does not label later replies (C). `threads.get` returns unlabelled messages too and
   costs twice `messages.get` (F, K).
   **[cairn]** A message. A reply that arrives later in a labelled conversation is read only when
   it gets the label itself. The labelled messages of one conversation in one month share a page
   by their topic (the subject without its reply and forward prefixes, and the month); `threadId`
   is not read.
6. QUESTION: How is a `text/plain` + `text/html` message laid out in `payload`, in what charset is
   decoded `body.data`, and can a text body arrive as an `attachmentId`? None is stated (E). This
   decides whether the connector uses `format=full` or `format=raw` and parses MIME itself.
   **[cairn]** `format=full`, and no MIME parsing of its own. The part tree is walked depth
   first, to any depth: the body is the first `text/plain` part without a `filename` that holds
   words, else the first such `text/html` part turned into text by `cairn.sources.markup`. `data`
   is decoded as base64url, with or without padding, then as UTF-8 with replacement; a part's
   own `Content-Type` charset and `Content-Transfer-Encoding` are not read, so a body Gmail
   leaves in another charset shows replacement characters. A part whose `body` has an
   `attachmentId` and no `data` is not read (`attachments.get` is never called): a text body
   moved out that way would leave the message with no text to read, reported once. Header
   names are matched ignoring case; RFC 2047 encoded words in `Subject` and in display names are
   decoded whether or not Gmail decoded them first.
7. QUESTION: What does `history.list?labelId=X` filter on, and does
   `historyTypes=labelAdded&labelId=X` return exactly the records where X was added? The parameter
   says only "Only return messages with a label matching the ID." (G.1)
8. QUESTION: When a Gmail filter labels a message on arrival, does history show `messageAdded`
   (with the label in `labelIds`), `labelAdded`, or both? Does `history.list` return
   `message.labelIds` at all (CONFLICT HIST-LIST vs RELNOTES)? (G.1)
   **[cairn]** Questions 7 and 8 are moot: history is not read.
9. QUESTION: What are the status and body for a stale `startHistoryId`, a message id that no longer
   exists, a label id that was deleted, a user with no mailbox, and a scope the token lacks? Only
   "typically ... `HTTP 404`" is given for history, with no body (G.2, L).
   **[cairn]** A 404 on `messages.get` for a message listed a moment ago skips it once ("gone").
   A 404 on `labels.list` or `messages.list` fails the pull and says to check `subject`. A
   deleted label is not met by id: the name is resolved again on every pull. A 401, or a 403
   that is not a rate limit or `dailyLimitExceeded`, is "refused access" and names the scope to
   check. An error's body is read for its reason only, and one that is not JSON by its status.
10. QUESTION: Under domain-wide delegation, is the 6,000 units per minute per user counted per
    impersonated user, or for the service account as "a single account"? (K)
    **[cairn]** Not answered. One pull makes at most 50 `messages.get` with `format=full`
    (3,000 units) besides one `labels.list` (1 unit) and its listing's pages (5 units each),
    about half of one person's minute; what it does not reach waits for the next pull.
11. QUESTION: Which quotas does an existing project get? QUOTA says projects that used the API
    between November 2025 and April 2026 keep "previously set usage quotas" without stating them,
    and charging is "planned ... later in 2026" (K).
    **[cairn]** Not answered: cairn reads no quota. A quota met answers as a rate limit, waited
    out, or as `dailyLimitExceeded`, which is not retried and says to raise or remove the cap.
12. QUESTION: Do API controls (ADMIN-APPS) apply to a service account with domain-wide delegation,
    and what error does the connector see when an admin restricts Gmail or its high-risk scopes:
    `domainPolicy` 403, a token-endpoint error, or a revoked token? (M)
    **[cairn]** Not answered. A 403 that is not a rate limit says to check, among the rest,
    that no administrator's policy keeps apps from Gmail; a refusal at the token endpoint is
    reported as `cairn.sources.google_auth` reports it.
13. QUESTION: May the connector pass mail text to an AI model at all (for compiling pages), given
    WS-POLICY's machine-learning clause and VERIF-FAQ's "personalized model" reading, and does the
    "only used by users within your own organization" exemption lift Limited Use for this case (N)?
14. QUESTION: How is a nested label's `name` spelled in `labels.list`, and how is a label whose name
    has spaces or a slash written after `label:` in `q`? If the connector uses `labelIds` instead of
    `q`, only the first part matters (C, D).
    **[cairn]** `labelIds`, so `label:` is never written. The nested name is to be confirmed
    against a real mailbox (C).
15. QUESTION: Is `after:<seconds>` inclusive, and is "PST" fixed UTC-8 or Pacific time with
    daylight saving? Only matters if `q` is used instead of history (D).
    **[cairn]** Seconds, never a date, so PST does not arise; `after:` is a day before the line,
    so either reading of it lists every message at or after the line (D).
16. QUESTION: Is `messages.list` order (newest first, per G-LISTMSG) guaranteed and stable across
    pages while labels change? MSG-LIST states no order (D).
    **[cairn]** Not relied on. Every page is read before any message, an id listed twice is read
    once, and a pull's notes are written by `internalDate`, then id. The ids are fetched in the
    reverse of the order listed, so the oldest come first when the guide's order holds.
17. QUESTION: Batch endpoint: `POST https://gmail.googleapis.com/batch` (discovery `batchPath`) or
    `/batch/gmail/v1` (the guide's default form)? (J)
    **[cairn]** Moot: no batch; each message is one GET.

**[cairn]** The mailbox in every path (`userId`) is the subject's address for a service account,
the documented form, since the pages do not state that `me` names the impersonated person (A);
`me` for a refresh token. Question 13 is not answered by cairn: whether a person's mail is read at
all is decided by the operator who adds the source and the person who labels a message.
